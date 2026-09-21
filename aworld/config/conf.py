# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import math
import os
import traceback
import uuid
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Union, Iterable, Literal, Type

import yaml
from pydantic import BaseModel, Field, model_validator


def load_config(file_name: str, dir_name: str = None) -> Dict[str, Any]:
    from aworld.logs.util import logger

    """Dynamically load config file form current path.

    Args:
        file_name: Config file name.
        dir_name: Config file directory.

    Returns:
        Config dict.
    """

    if dir_name:
        file_path = os.path.join(dir_name, file_name)
    else:
        # load conf form current path
        current_dir = Path(__file__).parent.absolute()
        file_path = os.path.join(current_dir, file_name)
    if not os.path.exists(file_path):
        logger.debug(f"{file_path} not exists, please check it.")

    configs = dict()
    try:
        with open(file_path, "r") as file:
            yaml_data = yaml.safe_load(file)
        configs.update(yaml_data)
    except FileNotFoundError:
        logger.debug(f"Can not find the file: {file_path}")
    except Exception:
        logger.warning(f"{file_name} read fail.\n", traceback.format_exc())
    return configs


def wipe_secret_info(config: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """Return a deep copy of this config as a plain Dict as well ass wipe up secret info, used to log."""

    def _wipe_secret(conf):
        def _wipe_secret_plain_value(v):
            if isinstance(v, List):
                return [_wipe_secret_plain_value(e) for e in v]
            elif isinstance(v, Dict):
                return _wipe_secret(v)
            else:
                return v

        key_list = []
        for key in conf.keys():
            key_list.append(key)
        for key in key_list:
            if key.strip('"') in keys:
                conf[key] = "-^_^-"
            else:
                _wipe_secret_plain_value(conf[key])
        return conf

    if not config:
        return config
    return _wipe_secret(config)


class ClientType(Enum):
    SDK = "sdk"
    HTTP = "http"


class HistoryWriteStrategy(Enum):
    """History write strategy for memory operations."""

    EVENT_DRIVEN = "event_driven"  # Write through message system (default)
    DIRECT = "direct"  # Direct call to memory handler


class ConfigDict(dict):
    """Object mode operates dict, can read non-existent attributes through `get` method."""

    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__

    def __init__(self, seq: dict = None, **kwargs):
        if seq is None:
            seq = OrderedDict()
        super(ConfigDict, self).__init__(seq, **kwargs)
        self.nested(self)

    def nested(self, seq: dict):
        """Nested recursive processing dict.

        Args:
            seq: Python original format dict
        """
        for k, v in seq.items():
            if isinstance(v, dict):
                seq[k] = ConfigDict(v)
                self.nested(v)


class BaseConfig(BaseModel):
    def to_dict(self) -> ConfigDict:
        return ConfigDict(self.model_dump())


class ContextCacheConfig(BaseConfig):
    enabled: bool = True
    # Keep provider-specific cache controls opt-in. Stable-prefix construction
    # and cache-usage accounting remain enabled without sending optional wire
    # fields such as OpenAI ``prompt_cache_key`` or Anthropic ``cache_control``.
    # ``None`` means no declaration, ``True`` is an explicit opt-in, and
    # ``False`` is an explicit veto when model and per-Agent policy are merged.
    allow_provider_native_cache: Optional[bool] = None
    # Optional provider routing hint. Adapters may lower it only when their
    # reviewed native API supports an explicit cache namespace/key.
    provider_cache_namespace: Optional[str] = None


def resolve_provider_native_cache_intent(configs: Iterable[Any]) -> bool:
    """Resolve an explicit provider-native cache opt-in across policy layers.

    Missing/``None`` declarations are neutral, at least one explicit ``True``
    is required, and any explicit ``False`` vetoes the feature. This keeps
    stable-prefix Context behavior default-on without inventing Provider wire
    controls for deployments that do not expose them.
    """
    declarations: List[bool] = []
    for config in configs:
        if config is None:
            continue
        value = (
            config.get("allow_provider_native_cache")
            if isinstance(config, dict)
            else getattr(config, "allow_provider_native_cache", None)
        )
        if value is None:
            continue
        if not isinstance(value, bool):
            raise TypeError("allow_provider_native_cache must be a boolean or None")
        declarations.append(value)
    return bool(declarations) and all(declarations)


class ContextCompilerRuntimeConfig(BaseConfig):
    # Adaptive Context is the shipped default for the capability-gated runtime.
    # Callers retain an explicit ``off``/``shadow`` rollback path.
    mode: Literal["off", "observe", "shadow", "enforce"] = "enforce"
    compiler_version: str = "v1"
    policy_version: str = "v1"
    universal_final: bool = True
    context_limit: Optional[int] = None
    reserved_output_tokens: int = 4096
    provider_protocol_reserve: int = 256
    safety_margin_tokens: int = 512
    # Offload controls how much Tool output stays inline; the final compiler
    # controls the total request budget. Do not impose a second implicit cap
    # on an indivisible assistant/tool exchange that fits that total budget.
    # Callers can still opt into a hard per-item contract explicitly.
    max_item_tokens: Optional[int] = Field(default=None, gt=0)
    require_proven_semantics_for_enforce: bool = True
    scoped_instructions: Literal["workspace_only", "nested"] = "nested"
    progressive_skills: bool = True
    progressive_tools: bool = True
    # ``None`` preserves the complete permission-filtered catalog.  An
    # explicitly configured list (including ``[]``) opts into progressive
    # selection and is combined only with activated Skill Tool requests.
    progressive_tool_base_tools: Optional[List[str]] = None
    progressive_tool_unmanaged_policy: Literal["preserve", "drop"] = "preserve"
    task_catalog_policy: Literal["per_call", "sticky"] = "sticky"
    checkpoint_policy: Literal["explicit", "budget_pressure", "adaptive"] = "adaptive"
    destructive_sandbox_checkpoint: bool = True
    elastic_step_budget: bool = True
    step_budget_extension_steps: int = Field(default=40, gt=0)
    step_budget_hard_limit: int = Field(default=240, gt=0)
    step_budget_recent_progress_window: int = Field(default=20, gt=0)
    default_tool_output_inline_tokens: int = Field(default=4096, gt=0)
    artifact_offload: bool = True
    context_inspector: bool = True
    trace_level: Literal["none", "summary", "decisions", "full_redacted"] = "decisions"
    completion_contract: Literal["off", "observe", "enforce"] = "off"
    # One model turn shares a hard wall deadline. Streaming additionally has
    # an idle deadline and an active Tool-free action deadline, leaving a
    # bounded continuation window instead of spending the whole turn on
    # provider-visible reasoning. ``None`` disables an individual optional
    # deadline; for the total config field it inherits the stable public
    # 360-second default. Direct ``GenerationBudgetPolicy`` construction can
    # explicitly disable the total deadline as well.
    generation_total_timeout_seconds: Optional[float] = Field(default=None, gt=0)
    generation_stream_idle_timeout_seconds: Optional[float] = Field(
        default=None, gt=0
    )
    generation_active_tool_free_timeout_seconds: Optional[float] = Field(
        default=None, gt=0
    )
    generation_action_repair_timeout_seconds: Optional[float] = Field(
        default=None, gt=0
    )
    generation_action_repair_max_output_tokens: int = Field(default=1024, gt=0)
    generation_partial_response_context_chars: int = Field(default=8192, gt=0)
    generation_action_repair_enabled: bool = False


class ModelConfig(BaseConfig):
    model_config = ConfigDict(extra="allow")
    llm_provider: Optional[str] = (
        None  # Set to None to allow automatic provider detection
    )
    llm_model_name: Optional[str] = None
    llm_temperature: float = 1.0
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_client_type: ClientType = ClientType.SDK
    llm_sync_enabled: bool = True
    llm_async_enabled: bool = True
    llm_stream_call: bool = False
    max_retries: int = 3
    max_model_len: Optional[int] = None  # Maximum model context length
    max_tokens: Optional[int] = Field(default=None, gt=0)
    provider_native_cache_capability: Literal[
        "auto", "supported", "unsupported"
    ] = "auto"
    model_type: Optional[str] = (
        "qwen"  # Model type determines tokenizer and maximum length
    )
    params: Optional[Dict[str, Any]] = {}
    ext_config: Optional[Dict[str, Any]] = {}
    llm_response_parser: Optional[Any] = None
    context_cache: ContextCacheConfig = Field(default_factory=ContextCacheConfig)
    context_compiler: ContextCompilerRuntimeConfig = Field(
        default_factory=ContextCompilerRuntimeConfig
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        declared_fields = type(self).model_fields
        for key, value in kwargs.items():
            if key in declared_fields:
                continue
            if hasattr(self, key):
                setattr(self, key, value)

        # init max_model_len
        if self.max_model_len is None:
            # qwen or other default model_type
            self.max_model_len = 128000 if self.model_type != "claude" else 200000


class LlmCompressionConfig(BaseConfig):
    enabled: bool = False
    compress_type: str = "llm"  # llm, llmlingua
    trigger_compress_token_length: int = (
        10000  # Trigger compression when exceeding this length
    )
    compress_model: Optional[ModelConfig] = Field(
        default=None, description="Compression model configuration"
    )


class OptimizationConfig(BaseConfig):
    enabled: bool = False
    max_token_budget_ratio: float = 0.5  # Maximum context length ratio


class MetaLearningConfig(BaseConfig):
    """Enhanced configuration for meta-learning functionality.

    Meta-learning enables intelligent agents to learn from task execution trajectories,
    analyze performance patterns, extract knowledge, and continuously optimize their
    behavior based on observed outcomes. This comprehensive configuration supports
    multiple learning modes and specialized learning components.
    """

    # Core enablement
    enabled: bool = Field(
        default=False, description="Whether to enable meta-learning capabilities"
    )

    # Storage configuration
    learning_knowledge_storage_base_path: Optional[str] = Field(
        default=None,
        description="Base path for storing trajectory data. Defaults to './' or TRAJ_STORAGE_BASE_PATH env var",
    )


class SelfEvolveJudgeConfig(BaseConfig):
    """Judge selection for framework-owned self-evolve evaluation."""

    mode: Literal["trajectory", "agent_md", "custom_agent", "backend_ref", "disabled"] = "trajectory"
    agent_path: Optional[str] = None
    agent_id: Optional[str] = None
    backend_ref: Optional[str] = None
    model_profile: Optional[str] = None


class SelfEvolveConfig(BaseConfig):
    """Disabled-by-default self-evolve configuration for harness optimization."""

    mode: Literal["off", "offline", "shadow", "online"] = "off"
    measurement_mode: Literal["off", "shadow", "advisory", "required"] = "off"
    measurement_primary_metric: str = "task_success"
    measurement_minimum_effect: float = 0.0
    measurement_confidence_level: float = 0.95
    measurement_min_independent_cases: int = 2
    measurement_bootstrap_samples: int = 2_000
    measurement_zero_yield_patience: int = 2
    measurement_invalid_control_patience: int = 2
    measurement_maximum_interval_width: Optional[float] = None
    replay_total_timeout_seconds: Optional[int] = None
    apply_policy: Literal["proposal", "auto_verified", "verified_only"] = "proposal"
    inferred_new_skill_policy: Literal[
        "disabled", "draft_only", "auto_verified"
    ] = "auto_verified"
    # ``max_run_tokens`` remains readable for existing configs.  New callers
    # should use the explicit total-run ceiling below.
    max_run_tokens: Optional[int] = None
    total_run_token_budget: Optional[int] = None
    per_attempt_replay_token_limit: Optional[int] = None
    max_run_cost_usd: Optional[float] = None
    max_run_wall_seconds: Optional[float] = None
    candidate_generation_tokens_per_unit: Optional[int] = None
    candidate_generation_cost_usd_per_unit: Optional[float] = None
    candidate_generation_wall_seconds_per_unit: Optional[float] = None
    candidate_screening_tokens_per_unit: Optional[int] = None
    candidate_screening_cost_usd_per_unit: Optional[float] = None
    candidate_screening_wall_seconds_per_unit: Optional[float] = None
    replay_tokens_per_unit: Optional[int] = None
    replay_cost_usd_per_unit: Optional[float] = None
    replay_wall_seconds_per_unit: Optional[float] = None
    evaluation_tokens_per_unit: Optional[int] = None
    evaluation_cost_usd_per_unit: Optional[float] = None
    evaluation_wall_seconds_per_unit: Optional[float] = None
    deprecated_config_mappings: tuple[str, ...] = ()
    min_eval_cases: int = 30
    judge_repetitions: int = 3
    judge_timeout_seconds: int = 300
    cooldown_seconds: int = 0
    max_iterations: int = 1
    max_improvement_cycles: int = 6
    min_improvement: float = 0.0
    max_background_jobs: int = 1
    auto_apply_target_types: tuple[str, ...] = ("skill",)
    target_types: tuple[str, ...] = (
        "skill",
        "prompt-section",
        "tool-description",
        "config",
        "workspace-artifact",
    )
    eval_sources: tuple[str, ...] = (
        "current_trajectory",
        "trajectory_log",
        "session",
        "jsonl",
        "batch_config",
    )
    regression_benchmarks: tuple[str, ...] = ()
    challenger_enabled: bool = True
    challenger_max_cases: int = 2
    require_deterministic_signal_for_verified: bool = True
    requires_post_apply_reevaluation: bool = True
    judge_config: SelfEvolveJudgeConfig = Field(default_factory=SelfEvolveJudgeConfig)
    replay_enabled: bool = True
    replay_timeout_seconds: int = 600
    # Match the direct ``aworld-cli run`` multi-step default.  A single model
    # turn can issue a tool call but cannot observe its result and synthesize a
    # terminal answer, which deterministically censors browser/tool replays.
    replay_max_steps: Optional[int] = None
    replay_candidate_limit: int = 2
    candidate_screening_max_cases: int = 3
    # Leave enough implicit search width for multiple evidence-quality repairs
    # after a near-pass.  Work is still metered in bounded 2M-token cycles and
    # operators can explicitly lower any frontier; verified apply policies
    # retain every release gate.
    max_generated_candidates: int = 24
    max_full_evaluation_candidates: int = 12
    max_score_tiebreak_candidates: int = 1
    baseline_replay_repetitions: int = 1
    candidate_replay_repetitions: int = 1
    replay_stability_margin: float = 0.0

    @model_validator(mode="after")
    def validate_apply_policy(self) -> "SelfEvolveConfig":
        if self.mode == "online" and self.apply_policy != "auto_verified":
            raise ValueError("online self-evolve requires apply_policy='auto_verified'")
        if (
            self.apply_policy in {"auto_verified", "verified_only"}
            and not self.requires_post_apply_reevaluation
        ):
            raise ValueError(
                "verified self-evolve policies require post-apply re-evaluation"
            )
        if self.replay_candidate_limit <= 0:
            raise ValueError("replay_candidate_limit must be positive")
        if self.candidate_screening_max_cases <= 0:
            raise ValueError("candidate_screening_max_cases must be positive")
        if self.max_generated_candidates <= 0:
            raise ValueError("max_generated_candidates must be positive")
        if self.max_full_evaluation_candidates <= 0:
            raise ValueError("max_full_evaluation_candidates must be positive")
        if self.max_score_tiebreak_candidates < 0:
            raise ValueError("max_score_tiebreak_candidates must be non-negative")
        if self.baseline_replay_repetitions <= 0:
            raise ValueError("baseline_replay_repetitions must be positive")
        if self.candidate_replay_repetitions <= 0:
            raise ValueError("candidate_replay_repetitions must be positive")
        if self.judge_timeout_seconds <= 0:
            raise ValueError("judge_timeout_seconds must be positive")
        if self.replay_timeout_seconds <= 0:
            raise ValueError("replay_timeout_seconds must be positive")
        if (
            self.replay_total_timeout_seconds is not None
            and self.replay_total_timeout_seconds <= 0
        ):
            raise ValueError("replay_total_timeout_seconds must be positive")
        if self.replay_stability_margin < 0:
            raise ValueError("replay_stability_margin must be non-negative")
        if not self.measurement_primary_metric.strip():
            raise ValueError("measurement_primary_metric must be non-empty")
        if not 0 < self.measurement_confidence_level < 1:
            raise ValueError(
                "measurement_confidence_level must be between 0 and 1"
            )
        if self.measurement_min_independent_cases <= 0:
            raise ValueError(
                "measurement_min_independent_cases must be positive"
            )
        if not 200 <= self.measurement_bootstrap_samples <= 100_000:
            raise ValueError(
                "measurement_bootstrap_samples must be between 200 and 100000"
            )
        if not math.isfinite(self.measurement_minimum_effect):
            raise ValueError("measurement_minimum_effect must be finite")
        if self.measurement_zero_yield_patience <= 0:
            raise ValueError("measurement_zero_yield_patience must be positive")
        if self.measurement_invalid_control_patience <= 0:
            raise ValueError(
                "measurement_invalid_control_patience must be positive"
            )
        if (
            self.measurement_maximum_interval_width is not None
            and (
                not math.isfinite(self.measurement_maximum_interval_width)
                or self.measurement_maximum_interval_width < 0
            )
        ):
            raise ValueError(
                "measurement_maximum_interval_width must be non-negative and finite"
            )
        if not 0 < self.challenger_max_cases <= 8:
            raise ValueError("challenger_max_cases must be between 1 and 8")
        if self.max_improvement_cycles <= 0:
            raise ValueError("max_improvement_cycles must be positive")
        for field_name in (
            "max_run_tokens",
            "total_run_token_budget",
            "per_attempt_replay_token_limit",
            "candidate_generation_tokens_per_unit",
            "candidate_screening_tokens_per_unit",
            "replay_tokens_per_unit",
            "evaluation_tokens_per_unit",
        ):
            value = getattr(self, field_name)
            if value is not None and value <= 0:
                raise ValueError(f"{field_name} must be positive")
        for field_name in ("max_run_cost_usd", "max_run_wall_seconds"):
            value = getattr(self, field_name)
            if value is not None and value <= 0:
                raise ValueError(f"{field_name} must be positive")
        for field_name in (
            "candidate_generation_cost_usd_per_unit",
            "candidate_generation_wall_seconds_per_unit",
            "candidate_screening_cost_usd_per_unit",
            "candidate_screening_wall_seconds_per_unit",
            "replay_cost_usd_per_unit",
            "replay_wall_seconds_per_unit",
            "evaluation_cost_usd_per_unit",
            "evaluation_wall_seconds_per_unit",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        deprecated_mappings = list(self.deprecated_config_mappings)
        if self.total_run_token_budget is None and self.max_run_tokens is not None:
            self.total_run_token_budget = self.max_run_tokens
            deprecated_mappings.append(
                "max_run_tokens_to_total_run_token_budget"
            )
        if (
            self.per_attempt_replay_token_limit is None
            and self.max_run_tokens is not None
        ):
            self.per_attempt_replay_token_limit = self.max_run_tokens
            deprecated_mappings.append(
                "max_run_tokens_to_per_attempt_replay_token_limit"
            )
        self.deprecated_config_mappings = tuple(
            dict.fromkeys(deprecated_mappings)
        )
        return self

class SummaryPromptConfig(BaseConfig):
    """Configuration for summary prompt templates."""

    template: str = Field(
        description="Base template, such as AWORLD_MEMORY_EXTRACT_NEW_SUMMARY"
    )
    summary_rule: str = Field(
        description="Summary rule, used to guide how to generate summaries"
    )
    summary_schema: str = Field(
        description="Summary schema, defines output format and structure"
    )
    memory_type: str = Field(
        default="summary",
        description="Memory type, used to distinguish different types of summaries",
    )


class ContextRuleConfig(BaseConfig):
    """Context interference rule configuration"""

    # ===== Performance optimization configuration =====
    optimization_config: OptimizationConfig = OptimizationConfig()

    # ===== LLM conversation compression configuration =====
    llm_compression_config: LlmCompressionConfig = LlmCompressionConfig()


class AgentMemoryConfig(BaseConfig):
    """Configuration for procedural memory."""

    model_config = ConfigDict(
        from_attributes=True,
        validate_default=True,
        revalidate_instances="always",
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )
    # short-term config
    history_rounds: int = Field(
        default=100,
        description="rounds of message msg; when the number of messages is greater than the history_rounds, the memory will be trimmed",
    )
    history_write_strategy: HistoryWriteStrategy = Field(
        default=HistoryWriteStrategy.EVENT_DRIVEN,
        description="History write strategy: event_driven (through message system) or direct (direct call to handler)",
    )
    history_scope: Optional[str] = Field(
        default="task",
        description="History initialization scope: user, session, or task",
    )

    enable_summary: bool = Field(
        default=False,
        description="enable_summary use llm to create summary short-term memory",
    )
    summary_model: Optional[str] = Field(
        default=None, description="short-term summary model"
    )
    summary_rounds: Optional[int] = Field(
        default=5,
        description="rounds of message msg; when the number of messages is greater than the summary_rounds, the summary will be created",
    )
    summary_context_length: Optional[int] = Field(
        default=40960,
        description=" when the content length is greater than the summary_context_length, the summary will be created",
    )
    summary_prompts: Optional[List[SummaryPromptConfig]] = Field(default=[])
    summary_summaried: Optional[bool] = Field(
        default=True,
        description="whether to summarize historical summary messages when summary is triggered",
    )
    summary_role: Optional[str] = Field(
        default="assistant", description="role for summary memory items"
    )
    tool_result_offload: bool = Field(
        default=True,
        description="compact oversized tool results before storing them in prompt-facing short-term memory",
    )
    tool_action_white_list: Optional[list[str]] = Field(
        default_factory=list,
        description="tool actions that should always use tool result compaction, formatted as tool:action",
    )
    tool_result_length_threshold: Optional[int] = Field(
        default=30000,
        description="compact tool results whose serialized size exceeds this token threshold",
    )
    tool_result_preview_chars: Optional[int] = Field(
        default=2000,
        description="maximum preview characters to keep in prompt-facing compacted tool results",
    )

    # Long-term memory config
    enable_long_term: bool = Field(
        default=False, description="enable_long_term use to store long-term memory"
    )
    long_term_model: Optional[str] = Field(
        default=None, description="long-term extract model"
    )
    # LongTermConfig
    long_term_config: Optional[BaseModel] = Field(
        default=None, description="long_term_config"
    )

    def __deepcopy__(self, memo=None):
        """Support copy.deepcopy for AgentMemoryConfig."""
        if memo is None:
            memo = {}

        # Check if already copied (avoid circular references)
        if id(self) in memo:
            return memo[id(self)]

        # Create a new instance using model_dump and model_validate to avoid recursion
        # Use mode='python' to get plain Python objects
        data = self.model_dump(mode="python")
        # Deep copy the data dict to handle nested objects
        copied_data = copy.deepcopy(data, memo)
        # Create new instance from copied data
        new_instance = self.__class__.model_validate(copied_data)
        memo[id(self)] = new_instance
        return new_instance


class AgentConfig(BaseConfig):
    llm_config: ModelConfig = ModelConfig()
    memory_config: AgentMemoryConfig = AgentMemoryConfig()

    # default reset init in first
    need_reset: bool = True
    # use vision model
    use_vision: bool = True
    max_steps: int = 10
    max_input_tokens: int = 128000
    max_actions_per_step: int = 10
    infrastructure_error_circuit_breaker_threshold: int = 3
    system_prompt: Optional[str] = None
    system_prompt_template: Optional[str] = None
    working_dir: Optional[str] = None
    enable_recording: bool = False
    use_tools_in_prompt: bool = False
    exit_on_failure: bool = False
    human_tools: List[str] = []
    skill_configs: Dict[str, Any] = None
    ptc_tools: List[str] = []
    # Concurrent batch size when this agent is called as tool in parallel
    # None means no limit (all parallel), positive integer limits batch size
    concurrent_batch_size: Optional[int] = None
    meta_learning_config: MetaLearningConfig = MetaLearningConfig()
    self_evolve_config: SelfEvolveConfig = Field(default_factory=SelfEvolveConfig)
    ext: dict = {}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialize llm_config with relevant kwargs
        llm_config_kwargs = {}
        llm_config_ext = {}
        for k, v in kwargs.items():
            if k in ModelConfig.model_fields:
                llm_config_kwargs[k] = v
            elif k not in self.__class__.model_fields:
                llm_config_ext[k] = v

        # Reassignment if it has llm config args
        if llm_config_kwargs or not self.llm_config:
            self.llm_config = ModelConfig(**llm_config_kwargs)

        self.llm_config.ext_config.update(llm_config_ext)

    @property
    def llm_model_name(self) -> str:
        return self.llm_config.llm_model_name

    @property
    def llm_provider(self) -> str:
        return self.llm_config.llm_provider


class TaskRunMode(Enum):
    INTERACTIVE = "INTERACTIVE"
    ONE_WAY = "ONE_WAY"


class TaskConfig(BaseConfig):
    model_config = {"arbitrary_types_allowed": True}
    max_steps: int = 100
    # Runtime trajectory implementations live in ``aworld.dataset`` and are
    # intentionally not imported while core configuration classes are being
    # defined. Dataset construction validates the concrete strategy/storage;
    # keeping these as class-valued extension points removes a fragile
    # package-import-order dependency for TaskConfig subclasses.
    trajectory_strategy: Optional[Type[Any]] = None
    trajectory_storage: Optional[Type[Any]] = None
    stream: bool = False
    resp_carry_context: bool = True
    resp_carry_raw_llm_resp: bool = False
    exit_on_failure: bool = False
    ext: dict = {}
    run_mode: TaskRunMode = TaskRunMode.ONE_WAY


class ToolConfig(BaseConfig):
    name: str = None
    custom_executor: bool = False
    enable_recording: bool = False
    working_dir: str = ""
    max_retry: int = 3
    llm_config: ModelConfig = None
    reuse: bool = False
    use_async: bool = False
    exit_on_failure: bool = False
    ext: dict = {}


class EngineName:
    # Use asyncio or MultiProcess run in local
    LOCAL = "local"
    # Stateless(task) run in ray. Ray actor will use a new name
    RAY = "ray"
    SPARK = "spark"


class RunConfig(BaseConfig):
    job_name: str = "aworld_job"
    engine_name: str = EngineName.LOCAL
    worker_num: int = 1
    # engine whether to run in local
    in_local: bool = True
    # run in local whether to use the same process
    reuse_process: bool = True
    # Is the task sequence dependent
    sequence_dependent: bool = False
    # The custom implement of RuntimeEngine
    cls: Optional[str] = None
    event_bus: Optional[Dict[str, Any]] = None
    tracer: Optional[Dict[str, Any]] = None


class StorageConfig(BaseConfig):
    name: str = "inmemory"


class DataLoaderConfig(BaseConfig):
    batch_size: Optional[int] = 1
    sampler: Any = None
    shuffle: bool = False
    drop_last: bool = False
    seed: Optional[int] = None
    batch_sampler: Optional[Iterable[List[int]]] = None
    collate_fn: Optional[Callable[..., Any]] = None


class DatasetConfig(BaseConfig):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    transforms: List[Callable[..., Any]] = Field(default_factory=list)

    # Config for loading dataset from source
    format: Optional[str] = None
    split: Optional[str] = None
    subset: Optional[str] = None
    json_field: Optional[str] = None
    parquet_columns: Optional[List[str]] = None
    encoding: str = "utf-8"
    limit: Optional[int] = None
    preload_transform: Optional[Callable[..., Any]] = None

    # Config for dataloader
    dataloader_config: DataLoaderConfig = DataLoaderConfig()


class EvaluationConfig(BaseConfig):
    """
    Evaluation run config.
    """

    # full class name of eval target, e.g. aworld.evaluations.base.EvalTarget
    eval_target: Any = None
    eval_target_full_class_name: str = None
    eval_target_config: dict = None
    eval_criterias: List[Union[dict]] = None
    eval_suite_id: str = None
    eval_dataset: Any = None
    # eval dataset id or file path, file path should be a jsonl file
    eval_dataset_id_or_file_path: str = None
    eval_dataset_load_config: Optional[DataLoaderConfig] = DataLoaderConfig()
    # preload transform function or function name, e.g. aworld.evaluations.base.preload_transform
    eval_dataset_preload_transform: Optional[Union[Callable[[any], Any], str]] = None
    eval_dataset_query_column: Optional[str] = "query"
    eval_dataset_answer_column: Optional[str] = "answer"
    eval_output_answer_column: Optional[str] = "answer"
    repeat_times: int = 1
    parallel_num: int = 1
    skip_passed_cases: bool = False
    skip_passed_on_metrics: List[str] = []
