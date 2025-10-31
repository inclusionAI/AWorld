
from collections import defaultdict
from copy import copy, deepcopy
import dataclasses
import json
from typing import List, Any, Dict, Union, Tuple
import logging
import torch
from transformers import PreTrainedTokenizer
from .preprocess import open_image_from_any
from .vision_processor import is_vision_template
from .tool_policy import (
    ToolFormatter,
    JsonMinifiedFormatter,
    JsonCompactFormatter,
    JsonIndentedFormatter,
    ToolMainContentProcessor,
    JsonQwenFormatter,
)
from .system_policy import Llama32DateProcessor, SystemPolicy
from .tool_policy import ToolPolicy
from .constants import ToolPlacement, Role
from .global_policy import GlobalPolicy

Logger = logging.getLogger(__name__)

# Add console handler if no handlers exist
if not Logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    Logger.addHandler(console_handler)


@dataclasses.dataclass
class Template:
    """Class that holds all the components of a chat template. Convert messages to string prompts, tokenize messages to token ids, and generate jinja-based chat templates.

    Args:
        name: The name of this template
        system_template: The system template component
        system_template_with_tools: The system template with tool usage component
        system_message: The default system message
        stop_words: The stop words where the model stops generating (usually EOS token)
        tool_template: The tool response template component
        user_template: The user template component
        user_template_with_tools: The user template with tool usage component
        assistant_template: The assistant template component
        global_policy: The global policy, controls the behavior of the template
        system_policy: The system message policy, controls the behavior of forming the system message
        tool_policy: The tool policy for the template, controls the behavior of forming tools.
    """
    # The name of this template
    name: str
    # The template of the system prompt
    system_template: str = "{system_message}"
    # The template of the system prompt with tool usage
    system_template_with_tools: str = None
    # The system message
    system_message: str = ""
    # Behaviors
    # The tool template
    tool_template: str = None
    # The user template
    user_template: str = None
    user_template_with_tools: str = None
    # The assistant template
    assistant_template: str = None

    # Stop criteria (the default one is EOS token)
    stop_words: Union[str, List[str]] = None
    # Generation prompt
    generation_prompt: str = None
    # Global policy
    global_policy: "GlobalPolicy" = None
    # System message policy
    system_policy: "SystemPolicy" = None
    # Tool policy for this template
    tool_policy: "ToolPolicy" = None

    # vision part
    vision_start: str = None
    vision_end: str = None
    image_token: str = None
    video_token: str = None

    chat_template: str = None

    def __post_init__(self):
        """Post-initialization to automatically register vision processor if vision tokens are defined"""
        if self.image_token or self.video_token:
            self._register_vision_processor()
        # Initialise default tool policy if none was provided
        if self.tool_policy is None:
            self.tool_policy = ToolPolicy()
        if self.system_policy is None:
            self.system_policy = SystemPolicy()

    def _register_vision_processor(self):
        """Automatically register a vision processor for this template"""
        from .vision_processor import VisionProcessorConfig, register_processor

        # Determine model type based on template name
        model_type = self._infer_model_type()

        # Create vision config
        config = VisionProcessorConfig(
            model_type=model_type,
            image_token=self.image_token or "",
            video_token=self.video_token or "",
            vision_start=self.vision_start or "",
            vision_end=self.vision_end or "",
            processor_class="AutoProcessor",
            expansion_strategy="patch_based"
        )

        # Register the processor
        register_processor(self.name, config)

    def _infer_model_type(self) -> str:
        """Infer model type from template name"""
        name_lower = self.name.lower()

        if "qwen" in name_lower:
            return "qwen_vl"
        elif "llava" in name_lower:
            return "llava"
        elif "gemma" in name_lower:
            return "gemma3"
        elif "paligemma" in name_lower:
            return "paligemma"
        elif "internvl" in name_lower:
            return "internvl"
        elif "minicpm" in name_lower:
            return "minicpm"
        elif "mllama" in name_lower:
            return "mllama"
        elif "pixtral" in name_lower:
            return "pixtral"
        elif "video" in name_lower:
            return "video_llava"
        else:
            # Default to patch-based for unknown models
            return "patch_based"

    def _supports_tool_call(self) -> bool:
        if (self.system_template_with_tools or self.user_template_with_tools) and self.tool_template:
            return True
        else:
            return False

    def render(self, messages: List[Dict], tools=None, add_generation_prompt: bool = False) -> str:
        """Render the template.

        The heavy lifting is delegated to small, single-purpose helpers so the
        high-level flow is immediately apparent:

            1. _insert_tools              – decide where the tool catalogue lives
            2. _encode_turns              – encode every conversation turn
            3. _maybe_add_generation_prompt – append the generation prefix if requested

        Args:
            messages: The list of messages
            tools: The list of tools
            add_generation_prompt: Whether to add the generation prefix

        Returns:
            prompt: The final prompt string
            elements: The list of string *elements* that compose the prompt
            roles: The corresponding list of *roles* (used by downstream post-processing)
        """

        # Step 1 – decide tool placement & clone messages
        work_messages, tools_str, insert_tools_idx = self._insert_tools(messages, tools)

        # Step 2 – encode each conversation turn to text tokens
        elements, roles = self._encode_turns(work_messages, tools_str, insert_tools_idx)

        # Step 3 – append generation prefix if needed
        if add_generation_prompt:
            self._maybe_add_generation_prompt(elements, roles)

        # Concatenate the prompt
        prompt = "".join(elements)
        return prompt, elements, roles

    def _insert_tools(self, messages: List[Dict], tools):
        """Clone *messages* and compute where (and how) the tool catalogue
        should be injected.

        Returns:
            work_messages : List[Dict]
                A deepcopy of the original *messages* so we never mutate caller data.
            tools_str : Optional[str]
                The formatted tool catalogue or *None* if `tools` is falsy.
            insert_tools_idx : int
                Index of the *user* message that receives the catalogue, or -1 when
                no injection is required.
        """

        work_messages = deepcopy(messages)
        if tools:
            tools_str = self.tool_policy.format_tools(tools)
            placement = self.tool_policy.placement
            insert_tools_idx = self._find_insert_tools_index(work_messages, placement)
        else:
            tools_str = None
            insert_tools_idx = -1
        return work_messages, tools_str, insert_tools_idx

    def _encode_turns(
        self,
        work_messages: List[Dict],
        tools_str: str,
        insert_tools_idx: int,
    ) -> Tuple[List[str], List[Role]]:
        """Convert every message dict into its textual representation while
        tracking roles for later masking logic."""

        elements: List[str] = []
        roles: List[Role] = []

        # Global prefix comes first (rarely used but must respect ordering)
        if self.global_policy and self.global_policy.prefix:
            elements.append(self.global_policy.prefix)
            roles.append(Role.SYSTEM)

        for i, message in enumerate(work_messages):
            current_role = self._detect_role(message["role"])

            # --------------------------------------------------------------
            # Handle system message insertion on the very first turn
            # --------------------------------------------------------------
            if i == 0 and current_role == Role.SYSTEM:
                if self.system_policy.use_system:
                    system_message = self._encode_system_message(
                        message["content"], tools=tools_str
                    )
                    elements.append(system_message)
                    roles.append(Role.SYSTEM)
                # Whether inserted or not, we skip further handling of this
                # message because it's the (optional) system turn itself.
                continue
            elif i == 0 and current_role != Role.SYSTEM:
                if self.system_policy.use_system:
                    system_message = self._encode_system_message_default(tools=tools_str)
                    elements.append(system_message)
                    roles.append(Role.SYSTEM)
                # Do *not* `continue` – we still need to encode this first message.

            # --------------------------------------------------------------
            # Encode regular conversation turns
            # --------------------------------------------------------------
            if current_role == Role.USER:
                if i == insert_tools_idx:
                    user_message = self._encode_user_message_with_tools(
                        message["content"], tools=tools_str
                    )
                else:
                    user_message = self._encode_user_message(message["content"])
                elements.append(user_message)
                roles.append(Role.USER)

            elif current_role == Role.ASSISTANT:
                assistant_message = self._encode_assistant_message(message["content"])
                elements.append(assistant_message)
                roles.append(Role.ASSISTANT)

            elif current_role == Role.TOOL:
                tool_message = self._encode_tool_message(message["content"])
                elements.append(tool_message)
                roles.append(Role.TOOL)

            else:
                raise ValueError(f"Invalid role: {message['role']}")

        return elements, roles

    def _maybe_add_generation_prompt(self, elements: List[str], roles: List[Role]):
        """Append the generation prefix so the model knows to continue
        generating an assistant response."""

        generation_prefix, prefix = self._encode_generation_prompt()
        elements.append(generation_prefix)
        roles.append(Role.ASSISTANT_PREFIX)

    def _detect_role(self, role: str) -> Role:
        if role == "system":
            return Role.SYSTEM
        elif role == "user":
            return Role.USER
        elif role == "assistant":
            return Role.ASSISTANT
        elif role == "tool":
            return Role.TOOL
        else:
            raise ValueError(f"Invalid role: {role}")

    def _find_insert_tools_index(self, work_messages: List[Dict], placement: ToolPlacement) -> int:
        insert_tools_idx = 0  # Default to insert tools at system message
        for i, message in enumerate(work_messages):
            if placement == ToolPlacement.SYSTEM:
                insert_tools_idx = 0
            elif placement == ToolPlacement.FIRST_USER:
                if message.get("role") == "user":
                    insert_tools_idx = i
                    break
            elif placement == ToolPlacement.LAST_USER:
                if message.get("role") == "user":
                    insert_tools_idx = i
            else:
                raise ValueError(f"Unhandled ToolPlacement: {placement}")
        return insert_tools_idx

    def _encode_system_tools(self, tools: List[Dict]) -> str:
        return "\n".join([json.dumps(tool) for tool in tools])

    def _encode_system_message_default(self, tools=None) -> str:
        Logger.debug(f"[Template] Encoding system message default for template: {self.name}")
        if not self.system_policy.use_system_without_system_message:
            if tools is None:
                return ""
            else:
                # If tools are provided, use the system message with tools
                pass

        if self.system_policy.content_processor is not None:
            system_message = self.system_policy.content_processor(self.system_message, tools=tools)
        else:
            system_message = self.system_message

        if tools is None:
            return self.system_template.format(system_message=system_message)
        else:
            if self.system_template_with_tools:
                return self.system_template_with_tools.format(system_message=system_message, tools=tools)
            else:
                return self.system_template.format(system_message=system_message)

    def _encode_system_message(self, content, tools=None) -> str:
        # Handle both string content and list content formats
        Logger.debug(f"[Template] Encoding system message for template: {self.name}")
        if isinstance(content, str):
            system_message = content
        else:
            system_message = content[0]['text']

        if self.system_policy.content_processor is not None:
            system_message = self.system_policy.content_processor(system_message, tools=tools)

        if tools is None:
            return self.system_template.format(system_message=system_message)
        else:
            if self.system_template_with_tools is None:
                return self.system_template.format(system_message=system_message)
            else:
                return self.system_template_with_tools.format(system_message=system_message, tools=tools)

    def _encode_user_message_with_tools(self, content, tools: str) -> str:
        # Handle both string content and list content formats
        if isinstance(content, str):
            text = content
        else:
            text = ""
            for item in content:
                if item["type"] == "text":
                    text += item["text"]
                elif item["type"] in ["image", "image_url"]:
                    text += self.vision_start + self.image_token + self.vision_end
                elif item["type"] == "video":
                    text += self.vision_start + self.video_token + self.vision_end
                else:
                    raise ValueError(f"Invalid message type: {item['type']}")

        if self.user_template_with_tools:
            user_message = self.user_template_with_tools.format(content=text, tools=tools)
        else:
            user_message = self.user_template.format(content=text)
        return user_message

    def _encode_user_message(self, content) -> str:
        # Handle both string content and list content formats
        if isinstance(content, str):
            text = content
        else:
            text = ""
            for item in content:
                if item["type"] == "text":
                    text += item["text"]
                elif item["type"] in ["image", "image_url"]:
                    text += self.vision_start + self.image_token + self.vision_end
                elif item["type"] == "video":
                    text += self.vision_start + self.video_token + self.vision_end
                else:
                    raise ValueError(f"Invalid message type: {item['type']}")
        user_message = self.user_template.format(content=text)
        return user_message

    def _encode_assistant_message(self, content) -> str:
        # Handle both string content and list content formats
        if isinstance(content, str):
            text = content
        else:
            assert len(content) == 1, "Assistant message must be a single message"
            text = content[0]["text"]
        assistant_message = self.assistant_template.format(content=text)
        return assistant_message

    def _encode_tool_message(self, content) -> str:
        # Handle both string content and list content formats
        if isinstance(content, str):
            text = content
        else:
            assert len(content) == 1, "Tool message must be a single message"
            text = content[0]["text"]
        tool_message = self.tool_template.format(observation=text)
        return tool_message

    def _encode_generation_prompt(self) -> str:
        # Use generation prompt if it is set
        if "{content}" in self.assistant_template:
            prefix = self.assistant_template.split("{content}")[0]
            if self.generation_prompt:
                generation_prompt = self.generation_prompt
            else:
                generation_prompt = prefix
        else:
            raise ValueError(f"Assistant template {self.assistant_template} does not contain {{content}}")

        return generation_prompt, prefix

    def _split_assistant_message(self, assistant_message: str) -> List[str]:
        # Split the assistant message into generation prefix, content, and generation suffix
        generation_prefix, prefix = self._encode_generation_prompt()
        assert assistant_message.startswith(prefix), f"Assistant message {assistant_message} does not start with {prefix}"
        content_suffix = assistant_message[len(prefix):]
        content = content_suffix
        suffix = ""
        for stop_word in self.stop_words:
            if stop_word in content_suffix:
                stop_word_index = content_suffix.index(stop_word)
                content = content_suffix[:stop_word_index + len(stop_word)]
                suffix = content_suffix[stop_word_index + len(stop_word):]
                break
        return prefix, content, suffix

    def encode(self, messages: List[Dict], tokenizer: PreTrainedTokenizer, return_tensors: str = None, tools=None, add_generation_prompt=False, processor=None, **kwargs) -> str:
        """Encode the messages to token ids.

        Args:
            messages: The list of messages
            tokenizer: The tokenizer
            return_tensors: The return tensors
            tools: The list of tools
            add_generation_prompt: Whether to add the generation prefix
            processor: The processor for vision templates

        Returns:
            inputs: The dictionary of input ids, attention mask, labels, and action mask
        """
        if processor is None and self.supports_vision():
            raise ValueError(f"Processor is required for vision templates: {self.name}")

        if self.supports_vision():
            # Use vision-aware encoding with proper alignment
            return self._encode_with_vision_processor(messages, tokenizer, return_tensors, tools, add_generation_prompt=add_generation_prompt, processor=processor, **kwargs)
        else:
            # Use standard encoding
            return self._encode_standard(messages, tokenizer, return_tensors, tools, add_generation_prompt=add_generation_prompt, **kwargs)

    def _encode_standard(self, messages: List[Dict], tokenizer: PreTrainedTokenizer, return_tensors: str = None, tools=None, add_generation_prompt=False, **kwargs) -> str:
        Logger.debug(f"[Template] Encoding standard for template: {self.name}")
        """Standard encoding without vision support"""
        prompt, elements, roles = self.render(messages, tools=tools, add_generation_prompt=add_generation_prompt, **kwargs)
        elements, mask_flags = self._postprocess_elements(elements, roles)
        input_ids = []
        attention_mask = []
        labels = []
        action_mask = []

        if tokenizer.bos_token:
            # If add_bos_token is not set, we assume to add bos token
            # There is potential issue if the tokenizer has bos_token but do not add it by default
            if getattr(tokenizer, "add_bos_token", True):
                input_ids.append(tokenizer.bos_token_id)
                attention_mask.append(1)
                labels.append(-100)
                action_mask.append(0)

        for element, mask_flag in zip(elements, mask_flags):
            cur_input_ids = tokenizer.encode(element, add_special_tokens=False)
            input_ids.extend(cur_input_ids)
            attention_mask.extend([1] * len(cur_input_ids))
            if mask_flag:
                labels.extend([-100] * len(cur_input_ids))
                action_mask.extend([0] * len(cur_input_ids))
            else:
                labels.extend(cur_input_ids)
                action_mask.extend([1] * len(cur_input_ids))
        inputs = dict(
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            action_mask=action_mask
        )
        if return_tensors == "pt":
            inputs = {k: torch.tensor([v]) for k, v in inputs.items()}
        return inputs

    def _encode_with_vision_processor(self, messages: List[Dict], tokenizer: PreTrainedTokenizer, return_tensors: str = None, tools=None, add_generation_prompt=False, processor=None, **kwargs) -> str:
        Logger.debug(f"[Template] Encoding with vision processor for template: {self.name}")
        """Encode with vision processor handling proper alignment"""
        from .vision_processor import get_processor
        from .utils import extract_vision_inputs_from_messages

        # Get vision processor
        vision_processor = get_processor(self.name)
        if vision_processor is None:
            raise ValueError(f"No vision processor registered for template: {self.name}")

        # Get base prompt and mask information
        prompt, elements, roles = self.render(messages, tools=tools, add_generation_prompt=add_generation_prompt, **kwargs)
        elements, mask_flags = self._postprocess_elements(elements, roles)

        # Extract vision inputs
        images, videos = extract_vision_inputs_from_messages(messages)

        Logger.debug(f"[Template] images: {len(images)}")
        Logger.debug(f"[Template] videos: {len(videos)}")

        Logger.debug(f"[Template] messages: {messages}")

        # Use vision processor with alignment support
        return vision_processor.process_for_llm(
            prompt=prompt,
            elements=elements,
            mask_flags=mask_flags,
            images=images,
            videos=videos,
            processor=processor,
            tokenizer=tokenizer,
            return_tensors=return_tensors
        )

    def _postprocess_elements(self, elements: List[str], roles) -> List[str]:
        # Flag non-assistant messages
        new_elements = []
        mask_flags = []
        for i, element in enumerate(elements):
            if roles[i] == Role.ASSISTANT:
                new_elements.append(element)
                mask_flags.append(False)
            else:
                new_elements.append(element)
                mask_flags.append(True)

        # return new_elements, mask_flags

        # merge non-assistant messages and handle the generation prefix and suffixes
        merged_elements = []
        merged_mask_flags = []

        for i, (element, mask_flag) in enumerate(zip(new_elements, mask_flags)):
            if i == 0:
                prev_element = element
                prev_mask_flag = mask_flag
                continue
            else:
                if prev_mask_flag == mask_flag:
                    # Both previous and current elements are assistant messages
                    if not mask_flag:
                        prefix, content, suffix = self._split_assistant_message(element)
                        merged_elements.append(prefix)
                        merged_mask_flags.append(True)
                        merged_elements.append(content)
                        merged_mask_flags.append(False)
                        prev_element = suffix
                        prev_mask_flag = True  # We need to mask the suffix
                    # Both previous and current elements are non-assistant messages
                    else:
                        prev_element += element
                        prev_mask_flag = True
                else:
                    # Previous element is not assistant message, but the current one is
                    if not mask_flag:
                        prefix, content, suffix = self._split_assistant_message(element)
                        prev_element += prefix
                        prev_mask_flag = True
                        merged_elements.append(prev_element)
                        merged_mask_flags.append(prev_mask_flag)
                        merged_elements.append(content)
                        merged_mask_flags.append(False)
                        prev_element = suffix
                        prev_mask_flag = True
                    # Previous element is assistant message, but the current one is not
                    else:
                        prev_element += element
                        prev_mask_flag = True
        if prev_element != "":
            merged_elements.append(prev_element)
            merged_mask_flags.append(prev_mask_flag)
        return merged_elements, merged_mask_flags

    def supports_vision(self) -> bool:
        """Check if this template supports vision processing"""
        return is_vision_template(self.name)

    def get_vision_inputs(self, messages: List[Dict]):
        vision_inputs = defaultdict(list)
        Logger.debug(f"[Template] get_vision_inputs: messages: {messages}")
        for message in messages:
            content = message["content"]
            if isinstance(content, list):
                for item in content:
                    if item['type'] == 'text':
                        continue
                    elif item['type'] in ['image', 'image_url', 'image_base64']:
                        vision_inputs["image"].append(open_image_from_any(item[item['type']]))
                    elif item['type'] == 'video':
                        raise NotImplementedError("Video is not supported for chat template.")
                    else:
                        raise ValueError(f"Invalid message type: {item['type']}")
            else:
                raise ValueError(f"Invalid message content: {content}, the content should be a list of dicts")
        return vision_inputs

    def jinja_template(self) -> str:
        """Interface for getting the Jinja template.

        Returns:
            The Jinja template string
        """
        if self.chat_template:
            return self.chat_template
        else:
            return self.render_jinja_template()

    def render_jinja_template(self) -> str:
        """Return a Hugging-Face style chat-template (Jinja-mini dialect).

        The implementation now mirrors the three-step structure of
        `render()` for easier maintenance:

            1.  _jinja_header_constants      – immutable `set` statements
            2.  _jinja_system_block          – first turn / system handling
            3.  _jinja_loop_messages         – remaining turns & per-role logic
            4.  _jinja_generation_block      – optional generation prefix
        """

        parts: List[str] = []

        # 1.  Constant header (always first)
        parts.extend(self._jinja_header_constants())

        # 2.  System-message handling (depends on presence of tools etc.)
        parts.extend(self._jinja_system_block())

        # 2.5 Pre-compute insert index for user placement
        parts.extend(self._jinja_compute_insert_idx())

        # 3.  Loop over remaining messages
        parts.extend(self._jinja_loop_messages())

        # 4.  Generation prefix block
        parts.extend(self._jinja_generation_block())

        template_str = "".join(parts)

        # Post-process: Replace __CURRENT_DATE__ placeholder with actual date
        if "__CURRENT_DATE__" in template_str:
            from datetime import datetime
            current_date = datetime.now().strftime('%d %b %Y')
            template_str = template_str.replace("__CURRENT_DATE__", current_date)

        return template_str

    # ------------------------------------------------------------------
    # Private helpers – keep them together for readability
    # ------------------------------------------------------------------

    def _jinja_header_constants(self) -> List[str]:
        """Return Jinja `set` statements for all constant strings."""

        # Compute default system message considering content processor
        if self.system_policy.content_processor is not None:
            # Apply content processor to system message
            # TODO: tools is not used here, but we need to pass it for consistency
            processed_system_message = self.system_policy.content_processor(self.system_message, tools=None)
            default_system = self.system_template.format(system_message=processed_system_message)
        else:
            default_system = self.system_template.format(system_message=self.system_message)

        system_template_with_tools_raw = (
            self.system_template_with_tools if self.system_template_with_tools else None
        )

        # Split templates
        try:
            u_pref, u_suff = self.user_template.split("{content}")
            a_pref, a_suff = self.assistant_template.split("{content}")
        except ValueError as exc:
            raise ValueError(
                "`user_template` / `assistant_template` must contain `{content}` placeholder"
            ) from exc

        if self.tool_template:
            t_pref, t_suff = self.tool_template.split("{observation}")
        else:
            t_pref, t_suff = "", ""

        # Tokens for images / videos
        img_tok = (self.vision_start or "") + (self.image_token or "") + (self.vision_end or "")
        vid_tok = (self.vision_start or "") + (self.video_token or "") + (self.vision_end or "")

        header = [
            f"{{% set _u_pref  = {u_pref!r} %}}",
            f"{{% set _u_suff  = {u_suff!r} %}}",
            f"{{% set _a_pref  = {a_pref!r} %}}",
            f"{{% set _a_suff  = {a_suff!r} %}}",
            f"{{% set _t_pref  = {t_pref!r} %}}",
            f"{{% set _t_suff  = {t_suff!r} %}}",
            f"{{% set _img_tok = {img_tok!r} %}}",
            f"{{% set _vid_tok = {vid_tok!r} %}}",
            f"{{% set _default_system = {default_system!r} %}}",
            f"{{% set _system_message = {self.system_message!r} %}}",
            f"{{% set _system_template = {self.system_template!r} %}}",
            f"{{% set _tool_placement = {self.tool_policy.placement.name!r} %}}",
        ]

        if system_template_with_tools_raw:
            header.append(
                f"{{% set _system_template_with_tools = {system_template_with_tools_raw!r} %}}"
            )

        # Add user template with tools if it exists
        if self.user_template_with_tools:
            # Convert double braces to single braces for Jinja compatibility
            processed_template = self.user_template_with_tools.replace('{{', '{').replace('}}', '}')
            header.append(
                f"{{% set _u_template_with_tools = {processed_template!r} %}}"
            )

        # ------------------------------------------------------------------
        #  Formatter macro for tools (only if the template supports tool calls)
        # ------------------------------------------------------------------

        if self._supports_tool_call():
            # Build a Jinja macro that reproduces ToolPolicy.format_tools behaviour
            formatter_snippet = self.tool_policy.formatter.jinja()

            # The snippet usually comes wrapped in "{{ ... }}".  We drop the
            # outer braces because macro bodies are already an output context.
            formatter_body = formatter_snippet.strip()

            header.extend(
                [
                    "{% macro _fmt_tools(tools) %}",
                    f"{formatter_body}",
                    "{% endmacro %}",
                ]
            )

        # ------------------------------------------------------------------
        #  System processor macro (if system policy has a content processor)
        # ------------------------------------------------------------------

        if self.system_policy.content_processor is not None:
            # Build a Jinja macro that reproduces the system content processor behaviour
            processor_snippet = self.system_policy.content_processor.jinja()

            # The snippet should be a template that expects 'system_message' variable
            # We create a macro that can be called with the system message
            header.extend(
                [
                    "{% macro _process_system_message(system_message) %}",
                    f"{processor_snippet}",
                    "{% endmacro %}",
                ]
            )

        return header

    def _jinja_compute_insert_idx(self) -> List[str]:
        """Return Jinja code that pre-computes the index where tools should
        be injected for FIRST_USER and LAST_USER placements."""

        return [
            "{% set _insert_ns = namespace(idx=-1) %}",
            "{% if _tool_placement in ['FIRST_USER', 'LAST_USER'] %}",
            "{%- for _m in messages -%}",
            "{%- if _m['role'] == 'user' -%}",
            "{%- if _tool_placement == 'FIRST_USER' and _insert_ns.idx == -1 -%}",
            "{% set _insert_ns.idx = loop.index0 %}",
            "{%- elif _tool_placement == 'LAST_USER' -%}",
            "{% set _insert_ns.idx = loop.index0 %}",
            "{%- endif -%}",
            "{%- endif -%}",
            "{%- endfor -%}",
            "{% endif %}",
        ]

    def _jinja_system_block(self) -> List[str]:
        """Return Jinja code that handles the system message logic."""

        return [
            # Handle system message first (matching render logic)
            "{% if messages and messages[0]['role'] == 'system' %}",
            "{% if tools and _system_template_with_tools %}",
            "{% if messages[0]['content'] is string %}",
            "{% if _process_system_message is defined %}",
            "{{ _system_template_with_tools.format(system_message=_process_system_message(messages[0]['content']), tools=_fmt_tools(tools)) }}",
            "{% else %}",
            "{{ _system_template_with_tools.format(system_message=messages[0]['content'], tools=_fmt_tools(tools)) }}",
            "{% endif %}",
            "{% else %}",
            "{% if _process_system_message is defined %}",
            "{{ _system_template_with_tools.format(system_message=_process_system_message(messages[0]['content'][0]['text']), tools=_fmt_tools(tools)) }}",
            "{% else %}",
            "{{ _system_template_with_tools.format(system_message=messages[0]['content'][0]['text'], tools=_fmt_tools(tools)) }}",
            "{% endif %}",
            "{% endif %}",
            "{% else %}",
            "{% if messages[0]['content'] is string %}",
            "{% if _process_system_message is defined %}",
            "{% set processed_message = _process_system_message(messages[0]['content']) %}",
            "{% set formatted_system = _system_template | replace('{system_message}', processed_message) %}{{ formatted_system }}",
            "{% else %}",
            "{% set formatted_system = _system_template | replace('{system_message}', messages[0]['content']) %}{{ formatted_system }}",
            "{% endif %}",
            "{% else %}",
            "{% if _process_system_message is defined %}",
            "{% set processed_message = _process_system_message(messages[0]['content'][0]['text']) %}",
            "{% set formatted_system = _system_template | replace('{system_message}', processed_message) %}{{ formatted_system }}",
            "{% else %}",
            "{% set formatted_system = _system_template | replace('{system_message}', messages[0]['content'][0]['text']) %}{{ formatted_system }}",
            "{% endif %}",
            "{% endif %}",
            "{% endif %}",
            "{% else %}",
            "{% if tools and _system_template_with_tools %}",
            "{% if _process_system_message is defined %}",
            "{{ _system_template_with_tools.format(system_message=_process_system_message(_system_message), tools=_fmt_tools(tools)) }}",
            "{% else %}",
            "{{ _system_template_with_tools.format(system_message=_system_message, tools=_fmt_tools(tools)) }}",
            "{% endif %}",
            "{% else %}",
            "{% if _process_system_message is defined %}",
            "{% set processed_message = _process_system_message(_system_message) %}",
            "{% set formatted_system = _system_template | replace('{system_message}', processed_message) %}{{ formatted_system }}",
            "{% else %}",
            "{{ _default_system }}",
            "{% endif %}",
            "{% endif %}",
            "{% endif %}",
        ]

    def _jinja_loop_messages(self) -> List[str]:
        """Return Jinja loop that encodes all messages except the first system."""

        return [
            "{% set _tool_ns = namespace(inserted=False, user_count=0) %}",
            # Process remaining messages (skip first if it was system)
            "{% for m in messages %}",
            "{% if not (loop.first and m['role'] == 'system') %}",
            "{% if m['role'] == 'user' %}",
            "{% set _tool_ns.user_count = _tool_ns.user_count + 1 %}",
            "{% set ns = namespace(txt='') %}",
            "{% if m['content'] is string %}",
            "{% set ns.txt = m['content'] %}",
            "{% else %}",
            "{% for item in m['content'] %}",
            "{% if item['type'] == 'text'  %}",
            "{% set ns.txt = ns.txt + item['text'] %}",
            "{% elif item['type'] == 'image' %}",
            "{% set ns.txt = ns.txt + _img_tok %}",
            "{% elif item['type'] == 'video' %}",
            "{% set ns.txt = ns.txt + _vid_tok %}",
            "{% endif %}",
            "{% endfor %}",
            "{% endif %}",
            "{% if tools and ((_tool_placement == 'FIRST_USER' and _tool_ns.user_count == 1) or (_tool_placement == 'LAST_USER' and loop.index0 == _insert_ns.idx)) and not _tool_ns.inserted %}",
            "{% if _u_template_with_tools is defined %}",
            "{% set formatted_tools = _fmt_tools(tools) %}",
            "{{ _u_template_with_tools | replace('{content}', ns.txt) | replace('{tools}', formatted_tools) }}",
            "{% else %}",
            "{{ _u_pref }}{{ ns.txt }}{{ _u_suff }}\\n{{ _fmt_tools(tools) }}",
            "{% endif %}",
            "{% set _tool_ns.inserted = True %}",
            "{% else %}",
            "{{ _u_pref }}{{ ns.txt }}{{ _u_suff }}",
            "{% endif %}",
            "{% elif m['role'] == 'assistant' %}",
            "{% if m['content'] is string %}",
            "{{ _a_pref }}{{ m['content'] }}{{ _a_suff }}",
            "{% else %}",
            "{{ _a_pref }}{{ m['content'][0]['text'] }}{{ _a_suff }}",
            "{% endif %}",
            "{% elif m['role'] == 'tool' %}",
            "{% if m['content'] is string %}",
            "{{ _t_pref }}{{ m['content'] }}{{ _t_suff }}",
            "{% else %}",
            "{{ _t_pref }}{{ m['content'][0]['text'] }}{{ _t_suff }}",
            "{% endif %}",
            "{% endif %}",
            "{% endif %}",
            "{% endfor %}",
        ]

    def _jinja_generation_block(self) -> List[str]:
        """Return Jinja code that appends the generation prefix when requested."""

        return [
            "{% if add_generation_prompt %}",
            "{{ _a_pref }}",
            "{% endif %}",
        ]

    def render_with_mask(self, messages: List[Dict], add_generation_prompt: bool = False, tools=None, **kwargs):
        from termcolor import colored
        prompt, elements, roles = self.render(messages, add_generation_prompt=add_generation_prompt, tools=tools, **kwargs)
        elements, mask_flags = self._postprocess_elements(elements, roles)

        prompt = ""
        for element, mask_flag in zip(elements, mask_flags):
            if mask_flag:
                prompt += colored(element, "red")
            else:
                prompt += colored(element, "green")
        return prompt, elements, mask_flags

    def set_system_message(self, system_message: str):
        """Set the system message."""
        self.system_message = system_message

    def copy(self):
        return self.__class__(
            name=self.name,
            system_template=self.system_template,
            system_template_with_tools=self.system_template_with_tools,
            system_message=self.system_message,
            user_template=self.user_template,
            user_template_with_tools=self.user_template_with_tools,
            assistant_template=self.assistant_template,
            tool_template=self.tool_template,
            stop_words=self.stop_words,
            generation_prompt=self.generation_prompt,
            vision_start=self.vision_start,
            vision_end=self.vision_end,
            image_token=self.image_token,
            video_token=self.video_token,
            global_policy=deepcopy(self.global_policy),
            system_policy=deepcopy(self.system_policy),
            tool_policy=deepcopy(self.tool_policy),
            chat_template=self.chat_template,
        )

    def dict(self):
        return {
            "template_name": self.name,
            "system_message": self.system_message,
            "system_template_with_tools": self.system_template_with_tools,
            "stop_words": self.stop_words,
            "vision_start": self.vision_start,
            "vision_end": self.vision_end,
            "image_token": self.image_token,
            "video_token": self.video_token,
        }


class Qwen3Template(Template):
    def render(self, messages: List[Dict], tools=None, add_generation_prompt: bool = False, enable_thinking: bool = False) -> str:
        """Render the Qwen3 template with special thinking logic.

        Args:
            messages: The list of messages
            tools: The list of tools
            add_generation_prompt: Whether to add the generation prefix
            enable_thinking: Whether to enable thinking mode

        Returns:
            prompt: The final prompt string
            elements: The list of string *elements* that compose the prompt
            roles: The corresponding list of *roles* (used by downstream post-processing)
        """

        # Step 1 – decide tool placement & clone messages
        work_messages, tools_str, insert_tools_idx = self._insert_tools(messages, tools)

        # Step 2 – clean think content from all assistant messages except the last one
        work_messages = self._clean_think_content(work_messages)

        # Step 2.5 – reformat think content in the last assistant message if it exists
        if work_messages and work_messages[-1].get("role") == "assistant":
            work_messages = self._reformat_last_assistant_think_content(work_messages)

        # Step 3 – encode each conversation turn to text tokens
        elements, roles = self._encode_turns(work_messages, tools_str, insert_tools_idx)

        # Step 4 – handle special generation prompt logic for Qwen3
        if add_generation_prompt:
            self._maybe_add_generation_prompt_qwen3(elements, roles, enable_thinking, work_messages)
        elif work_messages and work_messages[-1].get("role") == "assistant":
            # Add empty think tokens to the last assistant message if it doesn't already have think tags
            self._add_empty_think_to_last_assistant(elements, roles, work_messages)

        # Concatenate the prompt
        prompt = "".join(elements)
        return prompt, elements, roles

    def _clean_think_content(self, messages: List[Dict]) -> List[Dict]:
        """Remove all think content (<think>...</think>) from assistant messages and reformat existing think content."""
        cleaned_messages = []
        for i, message in enumerate(messages):
            if message.get("role") == "assistant" and i != len(messages) - 1:
                cleaned_message = message.copy()
                content = message["content"]

                if isinstance(content, str):
                    # Remove think content from string
                    cleaned_content = self._remove_think_tags(content)
                else:
                    # Handle list content format
                    cleaned_content = []
                    for item in content:
                        if item["type"] == "text":
                            cleaned_text = self._remove_think_tags(item["text"])
                            cleaned_content.append({"type": "text", "text": cleaned_text})
                        else:
                            cleaned_content.append(item)

                cleaned_message["content"] = cleaned_content
                cleaned_messages.append(cleaned_message)
            else:
                cleaned_messages.append(message)

        return cleaned_messages

    def _remove_think_tags(self, text: str) -> str:
        """Remove <think>...</think> tags from text."""
        import re
        # Remove <think>...</think> tags and their content
        pattern = r'<think>.*?</think>'
        return re.sub(pattern, '', text, flags=re.DOTALL)

    def _has_think_tags(self, text: str) -> bool:
        """Check if text contains <think> and </think> tags."""
        return '<think>' in text and '</think>' in text

    def _reformat_think_content(self, text: str) -> str:
        """Reformat think content to ensure each think token ends with two newlines."""
        import re

        def replace_think_content(match):
            think_content = match.group(1)
            # Ensure the think content ends with exactly two newlines
            think_content = think_content.rstrip('\n')
            return f'<think>\n{think_content}\n</think>\n\n'

        # Find and replace think tags, ensuring proper formatting
        pattern = r'<think>(.*?)</think>'
        return re.sub(pattern, replace_think_content, text, flags=re.DOTALL)

    def _reformat_last_assistant_think_content(self, messages: List[Dict]) -> List[Dict]:
        """Reformat think content in the last assistant message."""
        if not messages or messages[-1].get("role") != "assistant":
            return messages

        messages = messages.copy()
        last_message = messages[-1].copy()
        content = last_message["content"]

        if isinstance(content, str):
            # Reformat think content in string
            last_message["content"] = self._reformat_think_content(content)
        else:
            # Handle list content format
            reformed_content = []
            for item in content:
                if item["type"] == "text":
                    reformed_text = self._reformat_think_content(item["text"])
                    reformed_content.append({"type": "text", "text": reformed_text})
                else:
                    reformed_content.append(item)
            last_message["content"] = reformed_content

        messages[-1] = last_message
        return messages

    def _maybe_add_generation_prompt_qwen3(self, elements: List[str], roles: List[Role], enable_thinking: bool, work_messages: List[Dict]):
        """Append the generation prefix with special Qwen3 thinking logic."""
        if enable_thinking:
            # Use standard generation prompt
            generation_prefix, prefix = self._encode_generation_prompt()
            elements.append(generation_prefix)
            roles.append(Role.ASSISTANT_PREFIX)
        else:
            # Check if the last message has think tags
            has_existing_think = False
            if work_messages and work_messages[-1].get("role") == "assistant":
                content = work_messages[-1]["content"]
                if isinstance(content, str):
                    has_existing_think = self._has_think_tags(content)
                elif isinstance(content, list):
                    for item in content:
                        if item.get("type") == "text" and self._has_think_tags(item["text"]):
                            has_existing_think = True
                            break

            generation_prefix, prefix = self._encode_generation_prompt()
            if has_existing_think:
                # Don't add empty think tokens if think tags already exist
                elements.append(generation_prefix)
            else:
                # Add empty think tokens after the generation prefix
                elements.append(generation_prefix + "<think>\n\n</think>\n\n")
            roles.append(Role.ASSISTANT_PREFIX)

    def _add_empty_think_to_last_assistant(self, elements: List[str], roles: List[Role], work_messages: List[Dict]):
        """Add empty think tokens to the last assistant message if it doesn't already have think tags."""
        if not elements or not roles or not work_messages:
            return

        # Check if the last message has think tags
        has_existing_think = False
        if work_messages[-1].get("role") == "assistant":
            content = work_messages[-1]["content"]
            if isinstance(content, str):
                has_existing_think = self._has_think_tags(content)
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text" and self._has_think_tags(item["text"]):
                        has_existing_think = True
                        break

        # Only add empty think tokens if no existing think tags
        if not has_existing_think:
            generation_prefix, prefix = self._encode_generation_prompt()

            # Find the last assistant element
            for i in range(len(elements) - 1, -1, -1):
                if roles[i] == Role.ASSISTANT:
                    # Add empty think tokens at the start of the assistant message
                    elements[i] = prefix + "<think>\n\n</think>\n\n" + elements[i][len(prefix):]
                    break

    def _split_assistant_message(self, assistant_message: str) -> List[str]:
        # Split the assistant message into generation prefix, content, and generation suffix
        generation_prefix, prefix = self._encode_generation_prompt()
        assert assistant_message.startswith(prefix), f"Assistant message {assistant_message} does not start with {prefix}"

        # We need to detect whether the assistant message starts with empty think tokens
        # If so, we need to set empty think tokens as non-assistant message
        if assistant_message.startswith(prefix + "<think>\n\n</think>\n\n"):
            prefix = prefix + "<think>\n\n</think>\n\n"

        content_suffix = assistant_message[len(prefix):]
        content = content_suffix
        suffix = ""
        for stop_word in self.stop_words:
            if stop_word in content_suffix:
                stop_word_index = content_suffix.index(stop_word)
                content = content_suffix[:stop_word_index + len(stop_word)]
                suffix = content_suffix[stop_word_index + len(stop_word):]
                break
        return prefix, content, suffix


class Chat:
    def __init__(self, template: str, messages: List[List[str]] = None, tools=None, tokenizer: PreTrainedTokenizer = None):
        """
        Args:
            template: The name of the template to use.
            messages: The messages to use for the chat.
            tools: The tools to use for the chat.
            tokenizer: The tokenizer to use for the chat.
        """
        self.template = get_template(template)
        self.messages = self.convert_to_hf_format_messages(messages)
        self.tokenizer = tokenizer
        self.tools = tools
        self.flags = {}

    def _detect_labels(self, messages):
        message = messages[0]
        if 'role' in message and "content" in message:
            return 'role', 'content'
        elif 'from' in message and "value" in message:
            return 'from', 'value'
        else:
            raise ValueError(f"Cannot find role label and content label in the data.")

    def _convert_single_message_to_hf_format(self, message: Dict) -> Dict:
        if isinstance(message['content'], str):
            message['content'] = [{"type": "text", "text": message['content']}]
        elif isinstance(message['content'], list):
            for item in message['content']:
                if item['type'] == 'text':
                    continue
                else:
                    # Not sure what to do with other types of content
                    pass

    def convert_to_hf_format_messages(self, messages: Union[List[Dict], Dict[str, List[Dict]]]) -> List[Dict]:
        hf_messages = []
        if messages is None:
            return None
        role_label, content_label = self._detect_labels(messages)
        for message in messages:
            hf_messages.append({"role": message[role_label], "content": message[content_label]})

        for message in hf_messages:
            self._convert_single_message_to_hf_format(message)

        return hf_messages

    def set_messages(self, messages: List[Dict]):
        """Set the messages for the chat."""
        self.messages = self.convert_to_hf_format_messages(messages)

    def prompt(self, add_generation_prompt=False, tools=None, **kwargs) -> str:
        """Get the prompt for the chat.

        Args:
            add_generation_prompt: Whether to add the generation prompt.
            tools: The tools to use for the chat.
            **kwargs: Additional keyword arguments to pass to the template render method.

        Returns:
            The prompt for the chat.
        """
        self.flags['add_generation_prompt'] = add_generation_prompt
        tools = tools or self.tools
        prompt, _, _ = self.template.render(messages=self.messages, tools=tools, add_generation_prompt=add_generation_prompt, **kwargs)
        return prompt

    def prompt_with_mask(self, add_generation_prompt=False, tools=None, **kwargs) -> str:
        prompt_with_mask, _, _ = self.template.render_with_mask(
            messages=self.messages, add_generation_prompt=add_generation_prompt, tools=tools, **kwargs)
        return prompt_with_mask

    def vision_inputs(self) -> List[Any]:
        return self.template.get_vision_inputs(self.messages)

    def tokenize(self, tokenizer: PreTrainedTokenizer = None, add_generation_prompt=False, tools=None, processor=None, **kwargs) -> List[int]:
        """Tokenize the messages.

        Args:
            tokenizer: The tokenizer to use for the chat.
            add_generation_prompt: Whether to add the generation prompt.
            tools: The tools to use for the chat.
            processor: The processor to use for the chat.

        Returns:
            inputs (dict): Inputs for helping training.
                - input_ids
                - attention_mask
                - labels
                - action_mask
                - multi_modal_inputs
        """
        if tokenizer is None:
            if self.tokenizer is None:
                raise ValueError("Tokenizer is not set. Set it when initializing the chat or pass it as an argument.")
            tokenizer = self.tokenizer

        if tools is None:
            tools = self.tools
        return self.template.encode(messages=self.messages, tokenizer=tokenizer, return_tensors="pt", tools=tools, add_generation_prompt=add_generation_prompt, processor=processor, **kwargs)

    def append(self, message: Union[Dict]):
        self._convert_single_message_to_hf_format(message)
        self.messages.append(message)


# A global registry for all conversation templates
TEMPLATES: Dict[str, Template] = {}


def register_template(template: Template, override: bool = False):
    """Register a new conversation template."""
    if not override:
        assert (
            template.name not in TEMPLATES
        ), f"{template.name} has been registered."

    TEMPLATES[template.name] = template


def get_template(name: str) -> Template:
    """Get a conversation template."""
    return TEMPLATES[name].copy()


register_template(
    Template(
        name="qwen2.5-no-system-tool",
        system_template="<|im_start|>system\n{system_message}<|im_end|>\n",
        system_message="You are Qwen, created by Alibaba Cloud. You are a helpful assistant.",
        user_template="<|im_start|>user\n{content}<|im_end|>\n",
        assistant_template="<|im_start|>assistant\n{content}<|im_end|>\n",
        tool_template="<|im_start|>user\n<tool_response>\n{observation}\n</tool_response><|im_end|>\n",
        stop_words=["<|im_end|>"],
    )
)

register_template(
    Template(
        name="qwen2.5-vl",
        system_template="<|im_start|>system\n{system_message}<|im_end|>\n",
        system_message="You are a helpful assistant.",
        user_template="<|im_start|>user\n{content}<|im_end|>\n",
        assistant_template="<|im_start|>assistant\n{content}<|im_end|>\n",
        tool_template="<|im_start|>tool\n{observation}<|im_end|>\n",
        vision_start="<|vision_start|>",
        vision_end="<|vision_end|>",
        image_token="<|image_pad|>",
        video_token="<|video_pad|>",
        stop_words=["<|im_end|>"],
    )
)


register_template(
    Template(
        name="qwen2.5",
        system_template="<|im_start|>system\n{system_message}<|im_end|>\n",
        system_message="You are Qwen, created by Alibaba Cloud. You are a helpful assistant.",
        system_template_with_tools="""<|im_start|>system\n{system_message}\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{tools}\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n{{"name": <function-name>, "arguments": <args-json-object>}}\n</tool_call><|im_end|>\n""",
        user_template="<|im_start|>user\n{content}<|im_end|>\n",
        assistant_template="<|im_start|>assistant\n{content}<|im_end|>\n",
        tool_template="<|im_start|>user\n<tool_response>\n{observation}\n</tool_response><|im_end|>\n",
        stop_words=["<|im_end|>"],
    )
)


register_template(
    Template(
        name="qwen2.5-think",
        system_template="<|im_start|>system\n{system_message}<|im_end|>\n",
        system_message="You are a helpful assistant. To answer the user's question, you first think about the reasoning process and then provide the user with the answer. The reasoning process and answer are enclosed within <think> </think> and <answer> </answer> tags, respectively, i.e., <think> reasoning process here </think> <answer> answer here </answer>.",
        # system_template_with_tools="""<|im_start|>You are a helpful assistant. To answer the user's question, you first think about the reasoning process and then provide the user with the answer. The reasoning process and answer are enclosed within <think> </think> and <answer> </answer> tags, respectively, i.e., <think> reasoning process here </think> <answer> answer here </answer>.# Tools\n\nYou may call one or more functions to assist with the user query.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{tools}\n</tools>\n\nFor each function call, return a json object inside <answer> and </answer> tags with function name and arguments within <tool_call></tool_call> XML tags:\n<answer>\n<tool_call>\n{{"name": <function-name>, "arguments": <args-json-object>}}\n</tool_call>\n</answer><|im_end|>\n""",
        system_template_with_tools="""<|im_start|>You are a helpful assistant. To answer the user's question, you first think about the reasoning process and then call tools or provide the answer. The thinking process is enclosed within <think> </think> tags, i.e., <think> [reasoning process here] </think> [response here].\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{tools}\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<think> [reasoning process here] </think>\n<tool_call>\n{{"name": <function-name>, "arguments": <args-json-object>}}\n</tool_call>\nYou must think first before calling any tool.<|im_end|>\n""",
        user_template="<|im_start|>user\n{content}<|im_end|>\n",
        assistant_template="<|im_start|>assistant\n<think>{content}<|im_end|>\n",
        tool_template="<|im_start|>user\n<tool_response>\n{observation}\n</tool_response><|im_end|>\n",
        stop_words=["<|im_end|>"],
        vision_start="<|vision_start|>",
        vision_end="<|vision_end|>",
        image_token="<|image_pad|>",
        video_token="<|video_pad|>",
    )
)

register_template(
    Qwen3Template(
        name="qwen3",
        system_template="<|im_start|>system\n{system_message}<|im_end|>\n",
        system_template_with_tools="""<|im_start|>system\n{system_message}# Tools\n\nYou may call one or more functions to assist with the user query.\n\nYou are provided with function signatures within <tools></tools> XML tags:\n<tools>\n{tools}\n</tools>\n\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n{{"name": <function-name>, "arguments": <args-json-object>}}\n</tool_call><|im_end|>\n""",
        user_template="<|im_start|>user\n{content}<|im_end|>\n",
        assistant_template="<|im_start|>assistant\n{content}<|im_end|>\n",
        tool_template="<|im_start|>user\n<tool_response>\n{observation}\n</tool_response><|im_end|>\n",
        stop_words=["<|im_end|>"],
        system_policy=SystemPolicy(
            use_system_without_system_message=False,
            content_processor=lambda system, tools: f"{system}\n\n" if (system != "" and tools) else system,
        ),
        chat_template="{%- if tools %}\n    {{- '<|im_start|>system\\n' }}\n    {%- if messages[0].role == 'system' %}\n        {{- messages[0].content + '\\n\\n' }}\n    {%- endif %}\n    {{- \"# Tools\\n\\nYou may call one or more functions to assist with the user query.\\n\\nYou are provided with function signatures within <tools></tools> XML tags:\\n<tools>\" }}\n    {%- for tool in tools %}\n        {{- \"\\n\" }}\n        {{- tool | tojson }}\n    {%- endfor %}\n    {{- \"\\n</tools>\\n\\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\\n<tool_call>\\n{\\\"name\\\": <function-name>, \\\"arguments\\\": <args-json-object>}\\n</tool_call><|im_end|>\\n\" }}\n{%- else %}\n    {%- if messages[0].role == 'system' %}\n        {{- '<|im_start|>system\\n' + messages[0].content + '<|im_end|>\\n' }}\n    {%- endif %}\n{%- endif %}\n{%- set ns = namespace(multi_step_tool=true, last_query_index=messages|length - 1) %}\n{%- for message in messages[::-1] %}\n    {%- set index = (messages|length - 1) - loop.index0 %}\n    {%- if ns.multi_step_tool and message.role == \"user\" and message.content is string and not(message.content.startswith('<tool_response>') and message.content.endswith('</tool_response>')) %}\n        {%- set ns.multi_step_tool = false %}\n        {%- set ns.last_query_index = index %}\n    {%- endif %}\n{%- endfor %}\n{%- for message in messages %}\n    {%- if message.content is string %}\n        {%- set content = message.content %}\n    {%- else %}\n        {%- set content = '' %}\n    {%- endif %}\n    {%- if (message.role == \"user\") or (message.role == \"system\" and not loop.first) %}\n        {{- '<|im_start|>' + message.role + '\\n' + content + '<|im_end|>' + '\\n' }}\n    {%- elif message.role == \"assistant\" %}\n        {%- set reasoning_content = '' %}\n        {%- if message.reasoning_content is string %}\n            {%- set reasoning_content = message.reasoning_content %}\n        {%- else %}\n            {%- if '</think>' in content %}\n                {%- set reasoning_content = content.split('</think>')[0].rstrip('\\n').split('<think>')[-1].lstrip('\\n') %}\n                {%- set content = content.split('</think>')[-1].lstrip('\\n') %}\n            {%- endif %}\n        {%- endif %}\n        {%- if loop.index0 > ns.last_query_index %}\n            {%- if loop.last or (not loop.last and reasoning_content) %}\n                {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content.strip('\\n') + '\\n</think>\\n\\n' + content.lstrip('\\n') }}\n            {%- else %}\n                {{- '<|im_start|>' + message.role + '\\n' + content }}\n            {%- endif %}\n        {%- else %}\n            {{- '<|im_start|>' + message.role + '\\n' + content }}\n        {%- endif %}\n        {%- if message.tool_calls %}\n            {%- for tool_call in message.tool_calls %}\n                {%- if (loop.first and content) or (not loop.first) %}\n                    {{- '\\n' }}\n                {%- endif %}\n                {%- if tool_call.function %}\n                    {%- set tool_call = tool_call.function %}\n                {%- endif %}\n                {{- '<tool_call>\\n{\"name\": \"' }}\n                {{- tool_call.name }}\n                {{- '\", \"arguments\": ' }}\n                {%- if tool_call.arguments is string %}\n                    {{- tool_call.arguments }}\n                {%- else %}\n                    {{- tool_call.arguments | tojson }}\n                {%- endif %}\n                {{- '}\\n</tool_call>' }}\n            {%- endfor %}\n        {%- endif %}\n        {{- '<|im_end|>\\n' }}\n    {%- elif message.role == \"tool\" %}\n        {%- if loop.first or (messages[loop.index0 - 1].role != \"tool\") %}\n            {{- '<|im_start|>user' }}\n        {%- endif %}\n        {{- '\\n<tool_response>\\n' }}\n        {{- content }}\n        {{- '\\n</tool_response>' }}\n        {%- if loop.last or (messages[loop.index0 + 1].role != \"tool\") %}\n            {{- '<|im_end|>\\n' }}\n        {%- endif %}\n    {%- endif %}\n{%- endfor %}\n{%- if add_generation_prompt %}\n    {{- '<|im_start|>assistant\\n' }}\n    {%- if enable_thinking is defined and enable_thinking is false %}\n        {{- '<think>\\n\\n</think>\\n\\n' }}\n    {%- endif %}\n{%- endif %}",
    )
)

register_template(
    Template(
        name="deepseek-prover",
        system_template="{system_message}\n",
        system_message="You are an AI programming assistant, utilizing the Deepseek Coder model, developed by Deepseek Company, and you only answer questions related to computer science. For politically sensitive questions, security and privacy issues, and other non-computer science questions, you will refuse to answer.",
        user_template="### Instruction:\n{content}\n",
        assistant_template="### Response:\n{content}\n<|EOT|>\n",
        stop_words=["<|EOT|>"],
    )
)

register_template(
    Template(
        name="llama-3.2",
        system_template="<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system_message}<|eot_id|>",
        system_template_with_tools="<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\nEnvironment: ipython\n{system_message}<|eot_id|>",
        user_template="<|start_header_id|>user<|end_header_id|>\n\n{content}<|eot_id|>",
        user_template_with_tools="""<|start_header_id|>user<|end_header_id|>\n\nGiven the following functions, please respond with a JSON for a function call with its proper arguments that best answers the given prompt.\n\nRespond in the format {{"name": function name, "parameters": dictionary of argument name and its value}}.Do not use variables.\n\n{tools}\n\n{content}<|eot_id|>""",
        assistant_template="<|start_header_id|>assistant<|end_header_id|>\n\n{content}<|eot_id|>",
        tool_template="""<|start_header_id|>ipython<|end_header_id|>\n\n"{observation}"<|eot_id|>""",
        stop_words=["<|eot_id|>"],
        system_policy=SystemPolicy(
            use_system=True,
            content_processor=Llama32DateProcessor(),
        ),
        tool_policy=ToolPolicy(
            placement=ToolPlacement.FIRST_USER,
            formatter=JsonIndentedFormatter()
        )
    )
)

register_template(
    Template(
        name="glm-4",
        system_template="<|system|>\n{system_message}",
        user_template="<|user|>\n{content}",
        assistant_template="<|assistant|>\n{content}",
        stop_words=[""],
        global_policy=GlobalPolicy(
            prefix="[gMASK]<sop>"
        ),
        system_policy=SystemPolicy(
            use_system=True,
            use_system_without_system_message=False,
        ),
    )
)

register_template(
    Template(
        name="phi-4",
        system_template="<|im_start|>system<|im_sep|>{system_message}<|im_end|>",
        user_template="<|im_start|>user<|im_sep|>{content}<|im_end|>",
        assistant_template="<|im_start|>assistant<|im_sep|>{content}<|im_end|>",
        stop_words=["<|im_end|>"],
    )
)

# Note: Partial align, some minor new-line problems.
register_template(
    Template(
        name="nemotron",
        system_template="<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n{system_message}<|eot_id|>",
        system_template_with_tools="""<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n{system_message}<AVAILABLE_TOOLS>{tools}</AVAILABLE_TOOLS><|eot_id|>""",
        user_template="<|start_header_id|>user<|end_header_id|>\n\n{content}<|eot_id|>",
        assistant_template="<|start_header_id|>assistant<|end_header_id|>\n\n{content}<|eot_id|>",
        tool_template="<|start_header_id|>user<|end_header_id|>\n\n<TOOL_RESPONSE>[{observation}]</TOOL_RESPONSE><|eot_id|>",
        stop_words=["<|eot_id|>"],
        system_policy=SystemPolicy(
            use_system=True,
            content_processor=lambda system_message, tools: f"\n{system_message}",
        ),
        tool_policy=ToolPolicy(
            placement=ToolPlacement.SYSTEM,
            content_processor=ToolMainContentProcessor(),
            formatter=JsonCompactFormatter(),
        )
    )
)

register_template(
    Template(
        name="deepseek-r1-distill-qwen",
        system_template="{system_message}",
        user_template="<｜User｜>{content}",
        assistant_template="<｜Assistant｜>{content}<｜end▁of▁sentence｜>",
        stop_words=["<｜end▁of▁sentence｜>"],
        generation_prompt="<｜Assistant｜><think>\n",
        global_policy=GlobalPolicy(
            prefix="<｜begin▁of▁sentence｜>"
        ),
        system_policy=SystemPolicy(
            use_system=True,
            use_system_without_system_message=False,
        ),
        chat_template="{% if not add_generation_prompt is defined %}{% set add_generation_prompt = false %}{% endif %}{% set ns = namespace(is_first=false, is_tool=false, is_output_first=true, system_prompt='') %}{%- for message in messages %}{%- if message['role'] == 'system' %}{% set ns.system_prompt = message['content'] %}{%- endif %}{%- endfor %}{{bos_token}}{{ns.system_prompt}}{%- for message in messages %}{%- if message['role'] == 'user' %}{%- set ns.is_tool = false -%}{{'<｜User｜>' + message['content']}}{%- endif %}{%- if message['role'] == 'assistant' and message['content'] is none %}{%- set ns.is_tool = false -%}{%- for tool in message['tool_calls']%}{%- if not ns.is_first %}{{'<｜Assistant｜><｜tool▁calls▁begin｜><｜tool▁call▁begin｜>' + tool['type'] + '<｜tool▁sep｜>' + tool['function']['name'] + '\\n' + '```json' + '\\n' + tool['function']['arguments'] + '\\n' + '```' + '<｜tool▁call▁end｜>'}}{%- set ns.is_first = true -%}{%- else %}{{'\\n' + '<｜tool▁call▁begin｜>' + tool['type'] + '<｜tool▁sep｜>' + tool['function']['name'] + '\\n' + '```json' + '\\n' + tool['function']['arguments'] + '\\n' + '```' + '<｜tool▁call▁end｜>'}}{{'<｜tool▁calls▁end｜><｜end▁of▁sentence｜>'}}{%- endif %}{%- endfor %}{%- endif %}{%- if message['role'] == 'assistant' and message['content'] is not none %}{%- if ns.is_tool %}{{'<｜tool▁outputs▁end｜>' + message['content'] + '<｜end▁of▁sentence｜>'}}{%- set ns.is_tool = false -%}{%- else %}{% set content = message['content'] %}{% if '</think>' in content %}{% set content = content.split('</think>')[-1] %}{% endif %}{{'<｜Assistant｜>' + content + '<｜end▁of▁sentence｜>'}}{%- endif %}{%- endif %}{%- if message['role'] == 'tool' %}{%- set ns.is_tool = true -%}{%- if ns.is_output_first %}{{'<｜tool▁outputs▁begin｜><｜tool▁output▁begin｜>' + message['content'] + '<｜tool▁output▁end｜>'}}{%- set ns.is_output_first = false %}{%- else %}{{'\\n<｜tool▁output▁begin｜>' + message['content'] + '<｜tool▁output▁end｜>'}}{%- endif %}{%- endif %}{%- endfor -%}{% if ns.is_tool %}{{'<｜tool▁outputs▁end｜>'}}{% endif %}{% if add_generation_prompt and not ns.is_tool %}{{'<｜Assistant｜><think>\\n'}}{% endif %}"
    )
)

register_template(
    Template(
        name="llemma",
        system_template="{system_message}",
        user_template="Input:{content}\n\n",
        assistant_template="Response:{content}</s>",
        stop_words=["</s>"]
    )
)
