import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from aworld.logs.util import logger


class AWorldConfig:
    """Global configuration manager for aworld-cli."""
    
    def __init__(self):
        self.home_dir = Path.home()
        self.config_dir = self.home_dir / ".aworld"
        self.config_file = self.config_dir / "aworld.json"
        self._config: Optional[Dict[str, Any]] = None
    
    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_config_file(self) -> Dict[str, Any]:
        """Load configuration from JSON file."""
        if not self.config_file.exists():
            return {}
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ Warning: Failed to load config from {self.config_file}: {e}")
            return {}
    
    def _save_config_file(self, config: Dict[str, Any]):
        """Save configuration to JSON file."""
        self._ensure_config_dir()
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"❌ Error: Failed to save config to {self.config_file}: {e}")
            raise
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file."""
        if self._config is None:
            self._config = self._load_config_file()
        return self._config.copy()
    
    def save_config(self, config: Dict[str, Any]):
        """Save configuration to file."""
        self._config = config.copy()
        self._save_config_file(config)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value using dot notation (e.g., 'models.provider')."""
        config = self.load_config()
        keys = key.split('.')
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def set(self, key: str, value: Any):
        """Set a configuration value using dot notation (e.g., 'models.provider')."""
        config = self.load_config()
        keys = key.split('.')
        current = config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
        self.save_config(config)
    
    def unset(self, key: str) -> bool:
        """Remove a configuration value using dot notation."""
        config = self.load_config()
        keys = key.split('.')
        current = config
        for k in keys[:-1]:
            if not isinstance(current, dict) or k not in current:
                return False
            current = current[k]
        if keys[-1] in current:
            del current[keys[-1]]
            self.save_config(config)
            return True
        return False
    
    def get_config_path(self) -> str:
        """Get the path to the config file."""
        return str(self.config_file)
    
    def get_config_source(self, env_file: str = ".env") -> tuple[str, str]:
        """
        Determine the configuration source.
        Returns (source_type, source_path) tuple.
        Priority: local .env > global config
        """
        env_path = Path(env_file).resolve()
        if env_path.exists():
            return ("local", str(env_path))
        return ("global", str(self.config_file))


# Global config instance
_global_config: Optional[AWorldConfig] = None


def get_config() -> AWorldConfig:
    """Get the global config instance."""
    global _global_config
    if _global_config is None:
        _global_config = AWorldConfig()
    return _global_config


def resolve_stream_value(global_config: Dict[str, Any]) -> str:
    """
    Resolve stream setting from config to STREAM env value ('1' or '0').
    Priority: output.stream > stream > models.stream. Default: '1'.
    """
    stream_val = (global_config.get('output') or {}).get('stream')
    if stream_val is None:
        stream_val = global_config.get('stream')
    if stream_val is None:
        stream_val = (global_config.get('models') or {}).get('stream')
    if stream_val in (True, 'true', '1', 'yes'):
        return '1'
    if stream_val in (False, 'false', '0', 'no'):
        return '0'
    return '1'


def resolve_no_truncate_value(global_config: Dict[str, Any]) -> Optional[str]:
    """
    Resolve output no_truncate (NO_TRUNCATE) from config.
    Returns '1', '0', or None (not set; caller should not overwrite env).
    """
    val = (global_config.get('output') or {}).get('no_truncate')
    if val is None:
        return None
    if val in (True, 'true', '1', 'yes'):
        return '1'
    return '0'


def resolve_limit_tokens_value(global_config: Dict[str, Any]) -> Optional[str]:
    """
    Resolve output limit_tokens (LIMIT_TOKENS) from config.
    Returns string representation of integer, or None (not set).
    Priority: output.limit_tokens > top-level limit_tokens.
    """
    val = (global_config.get('output') or {}).get('limit_tokens')
    if val is None:
        val = global_config.get('limit_tokens')
    if val is None:
        return None
    try:
        n = int(val)
        return str(n) if n > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_limit_strategy_value(global_config: Dict[str, Any]) -> str:
    """
    Resolve output limit_strategy from config.
    Returns 'compress' or 'terminate'. Default: 'compress'.
    When LIMIT_TOKENS exceeded: compress = run context compression and continue;
    terminate = sys.exit(0).
    Priority: output.limit_strategy > top-level limit_strategy.
    """
    val = (global_config.get('output') or {}).get('limit_strategy')
    if val is None:
        val = global_config.get('limit_strategy')
    if val is None:
        return 'compress'
    s = str(val).strip().lower()
    if s in ('compress', 'terminate'):
        return s
    return 'compress'


def apply_stream_env(global_config: Dict[str, Any]) -> None:
    """Apply output/stream config to os.environ: STREAM, NO_TRUNCATE, LIMIT_TOKENS, LIMIT_STRATEGY."""
    os.environ['STREAM'] = resolve_stream_value(global_config)
    no_truncate = resolve_no_truncate_value(global_config)
    if no_truncate is not None:
        os.environ['NO_TRUNCATE'] = no_truncate
    limit_tokens = resolve_limit_tokens_value(global_config)
    if limit_tokens is not None:
        os.environ['LIMIT_TOKENS'] = limit_tokens
    os.environ['LIMIT_STRATEGY'] = resolve_limit_strategy_value(global_config)


def _env_to_config() -> Dict[str, Any]:
    """Build config dict from current os.environ."""
    env_config: Dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith((
            'OPENAI_', 'ANTHROPIC_', 'GEMINI_', 'MODEL_', 'LLM_',
            'TEXT_TO_IMAGE_', 'IMAGE_TO_IMAGE_', 'IMAGE_',
        )):
            continue
        if key == 'OPENAI_API_KEY':
            env_config.setdefault('models', {}).setdefault('openai', {})['api_key'] = value
        elif key == 'ANTHROPIC_API_KEY':
            env_config.setdefault('models', {}).setdefault('anthropic', {})['api_key'] = value
        elif key == 'GEMINI_API_KEY':
            env_config.setdefault('models', {}).setdefault('gemini', {})['api_key'] = value
        elif key == 'LLM_API_KEY':
            env_config.setdefault('models', {}).setdefault('default', {})['api_key'] = value
        elif key == 'LLM_MODEL_NAME':
            env_config.setdefault('models', {}).setdefault('default', {})['model'] = value
        elif key == 'LLM_BASE_URL':
            env_config.setdefault('models', {}).setdefault('default', {})['base_url'] = value
        elif key == 'LLM_PROVIDER':
            env_config.setdefault('models', {}).setdefault('default', {})['provider'] = value
        elif key.startswith('TEXT_TO_IMAGE_'):
            text_to_image = env_config.setdefault('models', {}).setdefault('text_to_image', {})
            if key == 'TEXT_TO_IMAGE_API_KEY':
                text_to_image['api_key'] = value
            elif key == 'TEXT_TO_IMAGE_MODEL_NAME':
                text_to_image['model'] = value
            elif key == 'TEXT_TO_IMAGE_BASE_URL':
                text_to_image['base_url'] = value
            elif key == 'TEXT_TO_IMAGE_PROVIDER':
                text_to_image['provider'] = value
            elif key == 'TEXT_TO_IMAGE_TEMPERATURE':
                text_to_image['temperature'] = value
        elif key.startswith('IMAGE_TO_IMAGE_'):
            image_to_image = env_config.setdefault('models', {}).setdefault('image_to_image', {})
            if key == 'IMAGE_TO_IMAGE_API_KEY':
                image_to_image['api_key'] = value
            elif key == 'IMAGE_TO_IMAGE_MODEL_NAME':
                image_to_image['model'] = value
            elif key == 'IMAGE_TO_IMAGE_BASE_URL':
                image_to_image['base_url'] = value
            elif key == 'IMAGE_TO_IMAGE_PROVIDER':
                image_to_image['provider'] = value
            elif key == 'IMAGE_TO_IMAGE_TEMPERATURE':
                image_to_image['temperature'] = value
        elif key.startswith('IMAGE_'):
            text_to_image = env_config.setdefault('models', {}).setdefault('text_to_image', {})
            if key == 'IMAGE_API_KEY' and 'api_key' not in text_to_image:
                text_to_image['api_key'] = value
            elif key == 'IMAGE_MODEL_NAME' and 'model' not in text_to_image:
                text_to_image['model'] = value
            elif key == 'IMAGE_BASE_URL' and 'base_url' not in text_to_image:
                text_to_image['base_url'] = value
            elif key == 'IMAGE_PROVIDER' and 'provider' not in text_to_image:
                text_to_image['provider'] = value
            elif key == 'IMAGE_TEMPERATURE' and 'temperature' not in text_to_image:
                text_to_image['temperature'] = value
    return env_config


def _get_aworld_skills_path() -> str:
    """Resolve aworld-skills path (AWorld/aworld-skills). Uses AWORLD_SKILLS env if set."""
    env_val = (os.environ.get('AWORLD_SKILLS') or '').strip()
    if env_val:
        return env_val
    # From aworld-cli/src/aworld_cli/core/config.py -> AWorld/aworld-skills
    _aworld_skills = Path(__file__).resolve().parents[4] / "aworld-skills"
    # print(f"_get_aworld_skills_path: {_aworld_skills}")
    return str(_aworld_skills) if _aworld_skills.exists() else ""


def _apply_skills_path_env(skills_cfg: Optional[Dict[str, Any]] = None) -> None:
    """
    Set xxx_SKILLS_PATH in os.environ.
    SKILLS_PATH fallback: default_skills_base. Others fallback: SKILLS_PATH when unset.
    """
    skills_cfg = skills_cfg or {}
    default_skills_base = str(Path.home() / ".aworld" / "skills")
    # print(f'default_skills_base {Path.home()}')
    aworld_skills = _get_aworld_skills_path()
    if aworld_skills:
        default_skills_base = default_skills_base + ";" + aworld_skills
        os.environ['AWORLD_SKILLS'] = aworld_skills
    skills_path_val = (skills_cfg.get('default_skills_path') or '').strip()
    if not skills_path_val:
        skills_path_val = (os.environ.get('SKILLS_PATH') or '').strip()
    if not skills_path_val:
        skills_path_val = default_skills_base
    os.environ['SKILLS_PATH'] = skills_path_val
    _other_skill_keys = [
        ('evaluator_skills_path', 'EVALUATOR_SKILLS_PATH'),
        ('media_skills_path', 'MEDIA_SKILLS_PATH'),
        ('aworld_skills_path', 'AWORLD_SKILLS_PATH'),
        ('developer_skills_path', 'DEVELOPER_SKILLS_PATH'),
    ]
    for cfg_key, env_key in _other_skill_keys:
        val = (skills_cfg.get(cfg_key) or '').strip()
        if not val:
            val = (os.environ.get(env_key) or '').strip()
        if not val:
            val = skills_path_val
        os.environ[env_key] = val


def _apply_filesystem_config(filesystem_cfg: Optional[Dict[str, Any]] = None) -> None:
    """
    Apply filesystem config to ARTIFACT_DIRECTORY env var.
    When filesystem.artifact_directory is set, it overrides ARTIFACT_DIRECTORY.
    """
    filesystem_cfg = filesystem_cfg or {}
    working_dir = (
        (filesystem_cfg.get("artifact_directory") or filesystem_cfg.get("working_directory")) or ""
    ).strip()
    if working_dir:
        os.environ["ARTIFACT_DIRECTORY"] = working_dir


def _apply_diffusion_models_config(models_config: Dict[str, Any]) -> None:
    """
    Apply models.diffusion config to DIFFUSION_* env vars for diffusion agent.
    Priority: models.diffusion config > existing DIFFUSION_* env vars > LLM_*.
    Supports models.diffusion for backwards compatibility.
    """
    diff_cfg = models_config.get('diffusion')
    diff_cfg = diff_cfg if isinstance(diff_cfg, dict) else {}
    api_key = (diff_cfg.get('api_key') or '').strip()
    model_name = (diff_cfg.get('model') or '').strip()
    base_url = (diff_cfg.get('base_url') or '').strip()
    provider = (diff_cfg.get('provider') or '').strip()
    temperature = diff_cfg.get('temperature')

    if not api_key:
        api_key = (os.environ.get('DIFFUSION_API_KEY') or '').strip()
    if not api_key:
        api_key = (os.environ.get('LLM_API_KEY') or '').strip()
    if not api_key:
        api_key = (os.environ.get('LLM_API_KEY') or '').strip()
    if not api_key:
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY'):
            v = (os.environ.get(key) or '').strip()
            if v:
                api_key = v
                if not provider and 'OPENAI' in key:
                    provider = 'openai'
                elif not provider and 'ANTHROPIC' in key:
                    provider = 'anthropic'
                elif not provider and 'GEMINI' in key:
                    provider = 'gemini'
                break
    if not model_name:
        model_name = (os.environ.get('DIFFUSION_MODEL_NAME') or '').strip()
    if not model_name:
        model_name = (os.environ.get('LLM_MODEL_NAME') or '').strip()
    if not base_url:
        base_url = (os.environ.get('DIFFUSION_BASE_URL') or '').strip()
    if not base_url:
        base_url = (os.environ.get('LLM_BASE_URL') or '').strip()
    if not base_url:
        for key in ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'GEMINI_BASE_URL'):
            v = (os.environ.get(key) or '').strip()
            if v:
                base_url = v
                break
    if not provider:
        provider = (os.environ.get('DIFFUSION_PROVIDER') or '').strip()
    if not provider:
        provider = 'video'
    if temperature is None:
        env_temp = (os.environ.get('DIFFUSION_TEMPERATURE') or '').strip()
        if env_temp:
            temperature = float(env_temp)

    if api_key:
        os.environ['DIFFUSION_API_KEY'] = api_key
    if model_name:
        os.environ['DIFFUSION_MODEL_NAME'] = model_name
    if base_url:
        os.environ['DIFFUSION_BASE_URL'] = base_url
    os.environ['DIFFUSION_PROVIDER'] = provider
    if temperature is not None:
        os.environ['DIFFUSION_TEMPERATURE'] = str(float(temperature))


def _apply_audio_models_config(models_config: Dict[str, Any]) -> None:
    """
    Apply models.audio config to AUDIO_* env vars for audio agent.
    Priority: models.audio config > existing AUDIO_* env vars > LLM_*.
    """
    audio_cfg = models_config.get('audio')
    audio_cfg = audio_cfg if isinstance(audio_cfg, dict) else {}
    api_key = (audio_cfg.get('api_key') or '').strip()
    model_name = (audio_cfg.get('model') or '').strip()
    base_url = (audio_cfg.get('base_url') or '').strip()
    provider = (audio_cfg.get('provider') or '').strip()
    temperature = audio_cfg.get('temperature')

    if not api_key:
        api_key = (os.environ.get('AUDIO_API_KEY') or '').strip()
    if not api_key:
        api_key = (os.environ.get('LLM_API_KEY') or '').strip()
    if not api_key:
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY'):
            v = (os.environ.get(key) or '').strip()
            if v:
                api_key = v
                if not provider and 'OPENAI' in key:
                    provider = 'openai'
                elif not provider and 'ANTHROPIC' in key:
                    provider = 'anthropic'
                elif not provider and 'GEMINI' in key:
                    provider = 'gemini'
                break
    if not model_name:
        model_name = (os.environ.get('AUDIO_MODEL_NAME') or '').strip()
    if not model_name:
        model_name = (os.environ.get('LLM_MODEL_NAME') or '').strip()
    if not base_url:
        base_url = (os.environ.get('AUDIO_BASE_URL') or '').strip()
    if not base_url:
        base_url = (os.environ.get('LLM_BASE_URL') or '').strip()
    if not base_url:
        for key in ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'GEMINI_BASE_URL'):
            v = (os.environ.get(key) or '').strip()
            if v:
                base_url = v
                break
    if not provider:
        provider = (os.environ.get('AUDIO_PROVIDER') or '').strip()
    if not provider:
        provider = 'openai'
    if temperature is None:
        env_temp = (os.environ.get('AUDIO_TEMPERATURE') or '').strip()
        if env_temp:
            temperature = float(env_temp)

    if api_key:
        os.environ['AUDIO_API_KEY'] = api_key
    if model_name:
        os.environ['AUDIO_MODEL_NAME'] = model_name
    if base_url:
        os.environ['AUDIO_BASE_URL'] = base_url
    os.environ['AUDIO_PROVIDER'] = provider
    if temperature is not None:
        os.environ['AUDIO_TEMPERATURE'] = str(float(temperature))


def _resolve_image_model_cfg(models_config: Dict[str, Any], key: str, legacy_key: Optional[str] = None) -> Dict[str, Any]:
    cfg = models_config.get(key)
    if isinstance(cfg, dict):
        return cfg
    if legacy_key:
        legacy_cfg = models_config.get(legacy_key)
        if isinstance(legacy_cfg, dict):
            return legacy_cfg
    return {}


def _apply_named_image_model_config(
    models_config: Dict[str, Any],
    *,
    config_key: str,
    env_prefix: str,
    legacy_env_prefix: Optional[str] = None,
) -> None:
    cfg = _resolve_image_model_cfg(
        models_config,
        config_key,
        legacy_key='image' if config_key == 'text_to_image' else None,
    )
    api_key = (cfg.get('api_key') or '').strip()
    model_name = (cfg.get('model') or '').strip()
    base_url = (cfg.get('base_url') or '').strip()
    provider = (cfg.get('provider') or '').strip()
    temperature = cfg.get('temperature')

    env_api_key = f'{env_prefix}_API_KEY'
    env_model_name = f'{env_prefix}_MODEL_NAME'
    env_base_url = f'{env_prefix}_BASE_URL'
    env_provider = f'{env_prefix}_PROVIDER'
    env_temperature = f'{env_prefix}_TEMPERATURE'

    legacy_env_api_key = f'{legacy_env_prefix}_API_KEY' if legacy_env_prefix else None
    legacy_env_model_name = f'{legacy_env_prefix}_MODEL_NAME' if legacy_env_prefix else None
    legacy_env_base_url = f'{legacy_env_prefix}_BASE_URL' if legacy_env_prefix else None
    legacy_env_provider = f'{legacy_env_prefix}_PROVIDER' if legacy_env_prefix else None
    legacy_env_temperature = f'{legacy_env_prefix}_TEMPERATURE' if legacy_env_prefix else None

    if not api_key:
        api_key = (os.environ.get(env_api_key) or '').strip()
    if not api_key and legacy_env_api_key:
        api_key = (os.environ.get(legacy_env_api_key) or '').strip()
    if not api_key:
        api_key = (os.environ.get('LLM_API_KEY') or '').strip()
    if not api_key:
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY'):
            v = (os.environ.get(key) or '').strip()
            if v:
                api_key = v
                if not provider and 'OPENAI' in key:
                    provider = 'openai'
                elif not provider and 'ANTHROPIC' in key:
                    provider = 'anthropic'
                elif not provider and 'GEMINI' in key:
                    provider = 'gemini'
                break
    if not model_name:
        model_name = (os.environ.get(env_model_name) or '').strip()
    if not model_name and legacy_env_model_name:
        model_name = (os.environ.get(legacy_env_model_name) or '').strip()
    if not model_name:
        model_name = (os.environ.get('LLM_MODEL_NAME') or '').strip()
    if not base_url:
        base_url = (os.environ.get(env_base_url) or '').strip()
    if not base_url and legacy_env_base_url:
        base_url = (os.environ.get(legacy_env_base_url) or '').strip()
    if not base_url:
        base_url = (os.environ.get('LLM_BASE_URL') or '').strip()
    if not base_url:
        for key in ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'GEMINI_BASE_URL'):
            v = (os.environ.get(key) or '').strip()
            if v:
                base_url = v
                break
    if not provider:
        provider = (os.environ.get(env_provider) or '').strip()
    if not provider and legacy_env_provider:
        provider = (os.environ.get(legacy_env_provider) or '').strip()
    if not provider:
        provider = 'image'
    if temperature is None:
        env_temp = (os.environ.get(env_temperature) or '').strip()
        if not env_temp and legacy_env_temperature:
            env_temp = (os.environ.get(legacy_env_temperature) or '').strip()
        if env_temp:
            temperature = float(env_temp)

    if api_key:
        os.environ[env_api_key] = api_key
    if model_name:
        os.environ[env_model_name] = model_name
    if base_url:
        os.environ[env_base_url] = base_url
    os.environ[env_provider] = provider
    if temperature is not None:
        os.environ[env_temperature] = str(float(temperature))


def _apply_image_models_config(models_config: Dict[str, Any]) -> None:
    """
    Apply models.text_to_image and models.image_to_image config to env vars.
    Legacy models.image is treated as models.text_to_image.
    """
    _apply_named_image_model_config(
        models_config,
        config_key='text_to_image',
        env_prefix='TEXT_TO_IMAGE',
        legacy_env_prefix='IMAGE',
    )
    _apply_named_image_model_config(
        models_config,
        config_key='image_to_image',
        env_prefix='IMAGE_TO_IMAGE',
    )


def _apply_models_config_to_env(models_config: Dict[str, Any]) -> None:
    """
    Apply models config (api_key, model, base_url) to os.environ.
    Supports: models.default (flat) and legacy models.default.{openai|anthropic|gemini}.
    Also applies models.diffusion to DIFFUSION_*.
    """
    if not models_config:
        return
    default_cfg = models_config.get('default') or {}
    if not isinstance(default_cfg, dict):
        default_cfg = {}
    # New format: default has api_key, model, base_url, provider
    if (default_cfg.get('api_key') or '').strip():
        api_key = (default_cfg.get('api_key') or '').strip()
        model_name = (default_cfg.get('model') or '').strip()
        base_url = (default_cfg.get('base_url') or '').strip()
        provider = (default_cfg.get('provider') or 'openai').strip().lower() or 'openai'
        if provider == 'anthropic':
            os.environ['ANTHROPIC_API_KEY'] = api_key
            if base_url:
                os.environ['ANTHROPIC_BASE_URL'] = base_url
        elif provider == 'gemini':
            os.environ['GEMINI_API_KEY'] = api_key
            if base_url:
                os.environ['GEMINI_BASE_URL'] = base_url
        else:
            os.environ['OPENAI_API_KEY'] = api_key
            if base_url:
                os.environ['OPENAI_BASE_URL'] = base_url
        os.environ['LLM_PROVIDER'] = provider
        os.environ['LLM_API_KEY'] = api_key
        if model_name:
            os.environ['LLM_MODEL_NAME'] = model_name
        if base_url:
            os.environ['LLM_BASE_URL'] = base_url
        _apply_diffusion_models_config(models_config)
        _apply_audio_models_config(models_config)
        _apply_image_models_config(models_config)
        return
    # Legacy: nested models.default.{provider} or models.{provider}
    default_providers = {k: v for k, v in default_cfg.items()
                        if k in ('openai', 'anthropic', 'gemini') and isinstance(v, dict)}
    if not default_providers:
        for p in ('openai', 'anthropic', 'gemini'):
            if p in models_config and isinstance(models_config[p], dict):
                default_providers[p] = models_config[p]
    llm_primary_set = False
    for provider, provider_config in default_providers.items():
        if not isinstance(provider_config, dict):
            continue
        api_key = (provider_config.get('api_key') or '').strip()
        model_name = (provider_config.get('model') or '').strip()
        base_url = (provider_config.get('base_url') or '').strip()
        if api_key:
            if provider.lower() == 'openai':
                os.environ['OPENAI_API_KEY'] = api_key
            elif provider.lower() == 'anthropic':
                os.environ['ANTHROPIC_API_KEY'] = api_key
            elif provider.lower() == 'gemini':
                os.environ['GEMINI_API_KEY'] = api_key
            if not llm_primary_set:
                os.environ['LLM_API_KEY'] = api_key
                os.environ['LLM_PROVIDER'] = provider.lower()
                if model_name:
                    os.environ['LLM_MODEL_NAME'] = model_name
                if base_url:
                    os.environ['LLM_BASE_URL'] = base_url
                llm_primary_set = True
        if base_url:
            if provider.lower() == 'openai':
                os.environ['OPENAI_BASE_URL'] = base_url
            elif provider.lower() == 'anthropic':
                os.environ['ANTHROPIC_BASE_URL'] = base_url
            elif provider.lower() == 'gemini':
                os.environ['GEMINI_BASE_URL'] = base_url
            if not os.environ.get('LLM_BASE_URL'):
                os.environ['LLM_BASE_URL'] = base_url

    _apply_diffusion_models_config(models_config)
    _apply_audio_models_config(models_config)
    _apply_image_models_config(models_config)


def _load_from_local_env(source_path: str) -> tuple[Dict[str, Any], str, str]:
    """Load config from local .env. Loads dotenv (override), applies skills path and output (STREAM, NO_TRUNCATE, LIMIT_TOKENS). ARTIFACT_DIRECTORY can be set in .env."""
    load_dotenv(dotenv_path=source_path, override=True)
    _apply_skills_path_env(skills_cfg={})
    apply_stream_env({
        'stream': os.environ.get('STREAM'),
        'output': {
            'stream': os.environ.get('STREAM'),
            'no_truncate': os.environ.get('NO_TRUNCATE'),
            'limit_tokens': os.environ.get('LIMIT_TOKENS'),
            'limit_strategy': os.environ.get('LIMIT_STRATEGY', 'compress'),
        },
    })
    # Apply DIFFUSION_* and image model envs from LLM_* when not set in .env
    _apply_diffusion_models_config({})
    _apply_audio_models_config({})
    _apply_image_models_config({})
    # Removed debug print statement that was leaking to stdout
    logger.info(f"[config] load_dotenv loaded from: {source_path} {os.environ.get('LLM_MODEL_NAME')} {os.environ.get('LLM_BASE_URL')}")
    return _env_to_config(), "local", source_path


def _load_from_global_config(config: AWorldConfig) -> tuple[Dict[str, Any], str, str]:
    """Load config from global aworld.json. Applies skills path, models, filesystem (artifact_directory), output (stream, no_truncate, limit_tokens)."""
    global_config = config.load_config()
    skills_cfg = global_config.get('skills') if isinstance(global_config.get('skills'), dict) else {}
    _apply_skills_path_env(skills_cfg)
    _apply_models_config_to_env(global_config.get('models') or {})
    _apply_filesystem_config(global_config.get('filesystem') if isinstance(global_config.get('filesystem'), dict) else {})
    apply_stream_env(global_config)
    return global_config, "global", str(config.config_file)


def load_config_with_env(env_file: str = ".env") -> tuple[Dict[str, Any], str, str]:
    """
    Load configuration with environment variable support.
    When .env exists: use .env only, do not load aworld.json.
    Otherwise: load from global aworld.json.
    """
    config = get_config()
    source_type, source_path = config.get_config_source(env_file)
    cwd = Path.cwd()
    expected = (cwd / env_file).resolve() if env_file else None
    resolved_path = Path(source_path) if source_path else None
    exists = resolved_path.exists() if resolved_path else False
    logger.info(f"[config] env_file={env_file!r} cwd={cwd} expected={expected} source_path={source_path!r} exists={exists}")

    if source_type == "local" and source_path:
        return _load_from_local_env(source_path)

    logger.info(f"[config] no local .env, falling back to global aworld.json")
    return _load_from_global_config(config)


def has_model_config(config_dict: Dict[str, Any]) -> bool:
    """
    Return True if any model configuration is available (env or config file).
    Used to prompt user to run --global-config when missing.
    """
    # Env vars used by the agent (LLM_*) or provider-specific keys applied by load_config_with_env
    env_keys = (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "TEXT_TO_IMAGE_API_KEY",
        "IMAGE_TO_IMAGE_API_KEY",
        "IMAGE_API_KEY",
    )
    for key in env_keys:
        if os.environ.get(key, "").strip():
            return True
    # Global config: models.default (flat or nested)
    models = config_dict.get("models") or {}
    default_cfg = models.get("default") or {}
    # New format: default has api_key
    if isinstance(default_cfg, dict) and (default_cfg.get("api_key") or "").strip():
        return True
    # Legacy: default.openai etc
    if isinstance(default_cfg, dict):
        for p in ('openai', 'anthropic', 'gemini'):
            if isinstance(default_cfg.get(p), dict) and (default_cfg[p].get("api_key") or "").strip():
                return True
    # Legacy: models.openai etc
    for p in ('openai', 'anthropic', 'gemini'):
        if isinstance(models.get(p), dict) and (models[p].get("api_key") or "").strip():
            return True
    for p in ('text_to_image', 'image_to_image', 'image'):
        if isinstance(models.get(p), dict) and (models[p].get("api_key") or "").strip():
            return True
    return False
