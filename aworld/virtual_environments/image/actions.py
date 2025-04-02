# coding: utf-8

from typing import Any, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

from aworld.core.common import ActionModel, ActionResult, Tools
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.envs.tool_action import ImageAnalysisAction
from aworld.logs.util import logger
from aworld.virtual_environments.action import ExecutableAction
from aworld.virtual_environments.image.prompts import IMAGE_OCR, IMAGE_REASONING
from aworld.virtual_environments.image.utils import (
    create_image_content,
    encode_image,
    handle_llm_response,
)


@ActionFactory.register(
    name=ImageAnalysisAction.OCR.value.name,
    desc=ImageAnalysisAction.OCR.value.desc,
    tool_name=Tools.IMAGE_ANALYSIS.value,
)
class ImageOcrAction(ExecutableAction):
    """Image OCR Action class for performing optical character recognition tasks.

    Inherits from ExecutableAction base class and implements specific OCR functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info(f"exec {ImageAnalysisAction.OCR.value.name} action")

        params = action.params
        image_url = params.get("image_url", "")
        image_with_header = params.get("with_header", True)
        image_base64 = encode_image(image_url, with_header=image_with_header)

        inputs = []
        try:
            content = create_image_content(IMAGE_OCR, image_base64)
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            image_text = handle_llm_response(
                response.choices[0].message.content, "image_text"
            )
        except (ValueError, IOError, RuntimeError) as e:
            image_text = ""
            logger.error(f"Execute error: {str(e)}")

        return ActionResult(content=image_text, keep=True), image_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for OCR action"""
        return await self.act(action, llm, **kwargs)


@ActionFactory.register(
    name=ImageAnalysisAction.REASONING.value.name,
    desc=ImageAnalysisAction.REASONING.value.desc,
    tool_name=Tools.IMAGE_ANALYSIS.value,
)
class ImageReasoningAction(ExecutableAction):
    """Image Reasoning Action class for answering questions about images.

    Inherits from ExecutableAction base class and implements specific reasoning functionality.
    """

    def act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        logger.info(f"exec {ImageAnalysisAction.REASONING.value.name} action")

        params = action.params
        question = params.get("question", "")
        image_url = params.get("image_url", "")
        image_with_header = params.get("with_header", True)
        image_base64 = encode_image(image_url, with_header=image_with_header)

        inputs = []
        try:
            content = create_image_content(
                IMAGE_REASONING.format(task=question), image_base64
            )
            inputs.append({"role": "user", "content": content})

            response = llm.chat.completions.create(
                messages=inputs,
                model=kwargs.get("model", "gpt-4o"),
                **{"temperature": kwargs.get("temperature", 0.0)},
            )
            image_reasoning_result = handle_llm_response(
                response.choices[0].message.content, "image_reasoning_result"
            )
        except (ValueError, IOError, RuntimeError) as e:
            image_reasoning_result = ""
            logger.error(f"Execute error: {str(e)}")

        return ActionResult(content=image_reasoning_result, keep=True), image_base64

    async def async_act(
        self, action: ActionModel, llm: BaseChatModel, **kwargs
    ) -> Tuple[ActionResult, Any]:
        """Asynchronous execution method for reasoning action"""
        return await self.act(action, llm, **kwargs)
