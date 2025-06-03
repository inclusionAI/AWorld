import json
import logging

from fastapi import HTTPException
from mcp import McpError
from pydantic import create_model
from pydantic.fields import FieldInfo, Field
from typing_extensions import Dict, Type, Any, Optional, Union, List, ForwardRef
from mcp import ClientSession, types
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp import StdioServerParameters

logger = logging.getLogger("mcp_openapi")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

from mcp.types import (
    CallToolResult,
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)

MCP_ERROR_TO_HTTP_STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    METHOD_NOT_FOUND: 404,
    INVALID_PARAMS: 422,
    INTERNAL_ERROR: 500,
}


def process_tool_response(result: CallToolResult) -> list:
    """Universal response processor for all tool endpoints"""
    response = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            text = content.text
            if isinstance(text, str):
                try:
                    text = json.loads(text)
                except json.JSONDecodeError:
                    pass
            response.append(text)
        elif isinstance(content, types.ImageContent):
            image_data = f"data:{content.mimeType};base64,{content.data}"
            response.append(image_data)
        elif isinstance(content, types.EmbeddedResource):
            # TODO: Handle embedded resources
            response.append("Embedded resource not supported yet.")
    return response


def _process_schema_property(
        _model_cache: Dict[str, Type],
        prop_schema: Dict[str, Any],
        model_name_prefix: str,
        prop_name: str,
        is_required: bool,
        schema_defs: Optional[Dict] = None,
) -> tuple[Union[Type, List, ForwardRef, Any], FieldInfo]:
    try:
        if "$ref" in prop_schema:
            ref = prop_schema["$ref"]
            ref = ref.split("/")[-1]
            assert ref in schema_defs, "Custom field not found"
            prop_schema = schema_defs[ref]

        prop_type = prop_schema.get("type")
        prop_desc = prop_schema.get("description", "")

        default_value = ... if is_required else prop_schema.get("default", None)
        pydantic_field = Field(default=default_value, description=prop_desc)

        if "anyOf" in prop_schema:
            type_hints = []
            for i, schema_option in enumerate(prop_schema["anyOf"]):
                type_hint, _ = _process_schema_property(
                    _model_cache,
                    schema_option,
                    f"{model_name_prefix}_{prop_name}",
                    f"choice_{i}",
                    False,
                )
                type_hints.append(type_hint)
            return Union[tuple(type_hints)], pydantic_field

        if isinstance(prop_type, list):
            # Create a Union of all the types
            type_hints = []
            for type_option in prop_type:
                # Create a temporary schema with the single type and process it
                temp_schema = dict(prop_schema)
                temp_schema["type"] = type_option
                type_hint, _ = _process_schema_property(
                    _model_cache, temp_schema, model_name_prefix, prop_name, False
                )
                type_hints.append(type_hint)

            # Return a Union of all possible types
            return Union[tuple(type_hints)], pydantic_field

        if prop_type == "object":
            nested_properties = prop_schema.get("properties", {})
            nested_required = prop_schema.get("required", [])
            nested_fields = {}

            nested_model_name = f"{model_name_prefix}_{prop_name}_model".replace(
                "__", "_"
            ).rstrip("_")

            if nested_model_name in _model_cache:
                return _model_cache[nested_model_name], pydantic_field

            for name, schema in nested_properties.items():
                is_nested_required = name in nested_required
                nested_type_hint, nested_pydantic_field = _process_schema_property(
                    _model_cache,
                    schema,
                    nested_model_name,
                    name,
                    is_nested_required,
                    schema_defs,
                )

                nested_fields[name] = (nested_type_hint, nested_pydantic_field)

            if not nested_fields:
                return Dict[str, Any], pydantic_field

            NestedModel = create_model(nested_model_name, **nested_fields)
            _model_cache[nested_model_name] = NestedModel

            return NestedModel, pydantic_field

        elif prop_type == "array":
            items_schema = prop_schema.get("items")
            if not items_schema:
                # Default to list of anything if items schema is missing
                return List[Any], pydantic_field

            # Recursively determine the type of items in the array
            item_type_hint, _ = _process_schema_property(
                _model_cache,
                items_schema,
                f"{model_name_prefix}_{prop_name}",
                "item",
                False,  # Items aren't required at this level,
                schema_defs,
            )
            list_type_hint = List[item_type_hint]
            return list_type_hint, pydantic_field

        elif prop_type == "string":
            return str, pydantic_field
        elif prop_type == "integer":
            return int, pydantic_field
        elif prop_type == "boolean":
            return bool, pydantic_field
        elif prop_type == "number":
            return float, pydantic_field
        elif prop_type == "null":
            return None, pydantic_field
        else:
            return Any, pydantic_field



    except Exception as e:
        logging.warning(f"_process_schema_property error: {e}")


def get_model_fields(form_model_name, properties, required_fields, schema_defs=None):
    model_fields = {}
    _model_cache: Dict[str, Type] = {}

    for param_name, param_schema in properties.items():
        is_required = param_name in required_fields
        python_type_hint, pydantic_field_info = _process_schema_property(
            _model_cache,
            param_schema,
            form_model_name,
            param_name,
            is_required,
            schema_defs,
        )
        # Use the generated type hint and Field info
        model_fields[param_name] = (python_type_hint, pydantic_field_info)
    return model_fields


def get_tool_handler(
        app,  # modified to receive app instead of session
        endpoint_name,
        form_model_fields,
        response_model_fields=None,
):
    if form_model_fields:
        FormModel = create_model(f"{endpoint_name}_form_model", **form_model_fields)
        ResponseModel = (
            create_model(f"{endpoint_name}_response_model", **response_model_fields)
            if response_model_fields
            else Any
        )

        async def tool(form_data: FormModel) -> ResponseModel:
            args = form_data.model_dump(exclude_none=True)
            print(f"Calling endpoint: {endpoint_name}, with args: {args}")

            # Use session in app.state
            session = app.state.session
            if not session:
                raise HTTPException(
                    status_code=503,
                    detail={"message": "Service unavailable", "error": "Session not initialized"}
                )

            try:
                result = await session.call_tool(endpoint_name, arguments=args)

                if result.isError:
                    error_message = "Unknown tool execution error"
                    error_data = None
                    if result.content:
                        if isinstance(result.content[0], types.TextContent):
                            error_message = result.content[0].text
                    detail = {"message": error_message}
                    if error_data is not None:
                        detail["data"] = error_data
                    raise HTTPException(
                        status_code=500,
                        detail=detail,
                    )

                response_data = process_tool_response(result)
                final_response = (
                    response_data[0] if len(response_data) == 1 else response_data
                )
                return final_response

            except McpError as e:
                print(f"MCP Error calling {endpoint_name}: {e.error}")
                status_code = MCP_ERROR_TO_HTTP_STATUS.get(e.error.code, 500)
                raise HTTPException(
                    status_code=status_code,
                    detail=(
                        {"message": e.error.message, "data": e.error.data}
                        if e.error.data is not None
                        else {"message": e.error.message}
                    ),
                )
            except Exception as e:
                logger.error(f"Unexpected error calling {endpoint_name}: {str(e)}")

                # Try to rebuild session and retry
                try:
                    logger.info(f"Attempting to recreate session and retry for {endpoint_name}")
                    session = await recreate_session(app)
                    result = await session.call_tool(endpoint_name, arguments=args)

                    # Process successful retry response
                    response_data = process_tool_response(result)
                    final_response = (
                        response_data[0] if len(response_data) == 1 else response_data
                    )
                    logger.info(f"Retry successful for {endpoint_name}")
                    return final_response
                except Exception as retry_e:
                    logger.error(f"Retry failed for {endpoint_name}: {str(retry_e)}")
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Unexpected error", "error": str(e)}
                    )

        return tool
    else:
        async def tool():  # No parameters
            print(f"Calling endpoint: {endpoint_name}, with no args")

            # Use session in app.state
            session = app.state.session
            if not session:
                raise HTTPException(
                    status_code=503,
                    detail={"message": "Service unavailable", "error": "Session not initialized"}
                )

            try:
                result = await session.call_tool(endpoint_name, arguments={})

                if result.isError:
                    error_message = "Unknown tool execution error"
                    if result.content:
                        if isinstance(result.content[0], types.TextContent):
                            error_message = result.content[0].text
                    detail = {"message": error_message}
                    raise HTTPException(
                        status_code=500,
                        detail=detail,
                    )

                response_data = process_tool_response(result)
                final_response = (
                    response_data[0] if len(response_data) == 1 else response_data
                )
                return final_response

            except McpError as e:
                print(f"MCP Error calling {endpoint_name}: {e.error}")
                status_code = MCP_ERROR_TO_HTTP_STATUS.get(e.error.code, 500)
                raise HTTPException(
                    status_code=status_code,
                    detail=(
                        {"message": e.error.message, "data": e.error.data}
                        if e.error.data is not None
                        else {"message": e.error.message}
                    ),
                )
            except Exception as e:
                logger.error(f"Unexpected error calling {endpoint_name}: {str(e)}")

                # Try to rebuild session and retry
                try:
                    logger.info(f"Attempting to recreate session and retry for {endpoint_name}")
                    session = await recreate_session(app)
                    result = await session.call_tool(endpoint_name, arguments={})

                    # Process successful retry response
                    response_data = process_tool_response(result)
                    final_response = (
                        response_data[0] if len(response_data) == 1 else response_data
                    )
                    logger.info(f"Retry successful for {endpoint_name}")
                    return final_response
                except Exception as retry_e:
                    logger.error(f"Retry failed for {endpoint_name}: {str(retry_e)}")
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Unexpected error", "error": str(e)}
                    )

        return tool


async def recreate_session(app):
    """
    Recreate session and update app.state.session

    Parameters:
        app: FastAPI application instance

    Returns:
        Newly created ClientSession instance
    """
    logger.info(f"Recreating session for {app.title}")

    # Clean up possible existing session resources
    if hasattr(app.state, "session"):
        try:
            # Close the session if it exists
            if app.state.session:
                # Let's just set it to None to allow garbage collection
                app.state.session = None
        except Exception as e:
            logger.warning(f"Error cleaning up existing session: {str(e)}")

    # Get connection parameters
    server_type = getattr(app.state, "server_type", "stdio")

    # Create session based on server type
    if server_type == "stdio":
        command = getattr(app.state, "command", None)
        args = getattr(app.state, "args", [])
        env = getattr(app.state, "env", {})

        if not command:
            raise ValueError(f"Command not available for stdio server: {app.title}")

        logger.info(f"Recreating stdio client for {app.title}: {command} {args}")
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env={**env},
        )

        # Create new connection and session
        async with stdio_client(server_params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                # Initialize session
                await session.initialize()

                # Update only the session in app.state
                app.state.session = session

                # Return the new session
                logger.info(f"Stdio client reconnected for {app.title}")
                return session

    elif server_type == "sse":
        args = getattr(app.state, "args", [])
        url = args[0] if isinstance(args, list) and args else args
        sse_read_timeout = getattr(app.state, "sse_read_timeout", None)

        if not url:
            raise ValueError(f"URL not available for SSE server: {app.title}")

        logger.info(f"Recreating SSE client for {app.title} with URL: {url}")
        # Create new connection and session
        async with sse_client(url=url, sse_read_timeout=sse_read_timeout) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                # Initialize session
                await session.initialize()

                # Update only the session in app.state
                app.state.session = session

                # Return the new session
                logger.info(f"SSE client reconnected for {app.title}")
                return session

    else:
        raise ValueError(f"Unsupported server type: {server_type} for {app.title}")