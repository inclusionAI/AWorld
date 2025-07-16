import os
from typing import AsyncGenerator, Generator, Callable, Any, Optional

from aworld.output.workspace import WorkSpace
from aworld.output.base import OutputPart, MessageOutput, Output


async def consume_output(__output__, callback):
    if isinstance(__output__, Output):
        ## parts
        if __output__.parts:
            for part in __output__.parts:
                await consume_part(part, callback)
        ## MessageOutput
        if isinstance(__output__, MessageOutput):
            if __output__.reason_generator or __output__.response_generator:
                if __output__.reason_generator:
                    await consume_content(__output__.reason_generator, callback)
                if __output__.reason_generator:
                    await consume_content(__output__.response_generator, callback)
            else:
                await consume_content(__output__.reasoning, callback)
                await consume_content(__output__.response, callback)
            if __output__.tool_calls:
                await consume_content(__output__.tool_calls, callback)
        else:
            await consume_content(__output__.data, callback)




async def consume_part(part, callback):
    if isinstance(part.content, Output):
        await consume_output(__output__=part.content, callback=callback)
    else:
        await consume_content(__content__=part.content, callback=callback)



async def consume_content(__content__, callback: Callable[..., Any]):
    if not __content__:
        return
    if isinstance(__content__, AsyncGenerator):
        async for sub_content in __content__:
            if isinstance(sub_content, OutputPart):
                await consume_part(sub_content, callback)
            elif isinstance(sub_content, Output):
                await consume_output(sub_content, callback)
            else:
                await callback(sub_content)
    elif isinstance(__content__, Generator) or isinstance(__content__, list):
        for sub_content in __content__:
            if isinstance(sub_content, OutputPart):
                await consume_part(sub_content, callback)
            elif isinstance(sub_content, Output):
                await consume_output(sub_content, callback)
            else:
                await callback(sub_content)
    elif isinstance(__content__, str):
        await callback(__content__)
    else:
        await callback(__content__)


async def load_workspace(workspace_id: str, workspace_type: str, workspace_parent_path: str) -> Optional[WorkSpace]:
    """
    This function is used to get the workspace by its id.
    It first checks the workspace type and then creates the workspace accordingly.
    If the workspace type is not valid, it raises an HTTPException.
    """
    if workspace_id is None:
        raise RuntimeError("workspace_id is None")

    if workspace_type == "local":
        workspace = WorkSpace.from_local_storages(workspace_id, storage_path=os.path.join(workspace_parent_path, workspace_id))
    elif workspace_type == "oss":
        workspace = WorkSpace.from_oss_storages(workspace_id, storage_path=os.path.join(workspace_parent_path, workspace_id))
    else:
        raise RuntimeError("Invalid workspace type")
    return workspace