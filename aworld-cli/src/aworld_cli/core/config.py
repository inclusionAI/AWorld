import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv


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


def load_config_with_env(env_file: str = ".env") -> tuple[Dict[str, Any], str, str]:
    """
    Load configuration with environment variable support.
    Priority: local .env > global config
    
    Returns:
        (config_dict, source_type, source_path) tuple
    """
    config = get_config()
    source_type, source_path = config.get_config_source(env_file)
    
    # Load from .env if exists (highest priority for models)
    env_path = Path(env_file).resolve()
    if env_path.exists():
        load_dotenv(env_path)
        # Apply skills from global config even when using .env (skills not in .env by default)
        global_config = config.load_config()
        if 'skills' in global_config:
            skills_cfg = global_config['skills']
            if isinstance(skills_cfg, dict):
                if skills_cfg.get('skills_path'):
                    os.environ['SKILLS_PATH'] = str(skills_cfg['skills_path']).strip()
                if skills_cfg.get('evaluator_skills_path'):
                    os.environ['EVALUATOR_SKILLS_PATH'] = str(skills_cfg['evaluator_skills_path']).strip()
                if skills_cfg.get('explorer_skills_path'):
                    os.environ['EXPLORER_SKILLS_PATH'] = str(skills_cfg['explorer_skills_path']).strip()
                if skills_cfg.get('aworld_skills_path'):
                    os.environ['AWORLD_SKILLS_PATH'] = str(skills_cfg['aworld_skills_path']).strip()
                if skills_cfg.get('developer_skills_path'):
                    os.environ['DEVELOPER_SKILLS_PATH'] = str(skills_cfg['developer_skills_path']).strip()
        # Convert .env to config dict format for consistency
        env_config = {}
        for key, value in os.environ.items():
            if key.startswith(('OPENAI_', 'ANTHROPIC_', 'GEMINI_', 'MODEL_', 'LLM_')):
                # Map common env vars to config structure
                if key.startswith('OPENAI_'):
                    if 'models' not in env_config:
                        env_config['models'] = {}
                    if 'openai' not in env_config['models']:
                        env_config['models']['openai'] = {}
                    if key == 'OPENAI_API_KEY':
                        env_config['models']['openai']['api_key'] = value
                elif key.startswith('ANTHROPIC_'):
                    if 'models' not in env_config:
                        env_config['models'] = {}
                    if 'anthropic' not in env_config['models']:
                        env_config['models']['anthropic'] = {}
                    if key == 'ANTHROPIC_API_KEY':
                        env_config['models']['anthropic']['api_key'] = value
                elif key.startswith('GEMINI_'):
                    if 'models' not in env_config:
                        env_config['models'] = {}
                    if 'gemini' not in env_config['models']:
                        env_config['models']['gemini'] = {}
                    if key == 'GEMINI_API_KEY':
                        env_config['models']['gemini']['api_key'] = value
        
        return env_config, source_type, source_path
    
    # Otherwise load from global config
    global_config = config.load_config()
    # Apply skills config to environment
    if 'skills' in global_config:
        skills_cfg = global_config['skills']
        if isinstance(skills_cfg, dict):
            if skills_cfg.get('skills_path'):
                os.environ['SKILLS_PATH'] = str(skills_cfg['skills_path']).strip()
            if skills_cfg.get('evaluator_skills_path'):
                os.environ['EVALUATOR_SKILLS_PATH'] = str(skills_cfg['evaluator_skills_path']).strip()
            if skills_cfg.get('explorer_skills_path'):
                os.environ['EXPLORER_SKILLS_PATH'] = str(skills_cfg['explorer_skills_path']).strip()
            if skills_cfg.get('aworld_skills_path'):
                os.environ['AWORLD_SKILLS_PATH'] = str(skills_cfg['aworld_skills_path']).strip()
            if skills_cfg.get('developer_skills_path'):
                os.environ['DEVELOPER_SKILLS_PATH'] = str(skills_cfg['developer_skills_path']).strip()
    # Apply global config to environment (provider-specific + LLM_API_KEY, LLM_MODEL_NAME, LLM_BASE_URL)
    if 'models' in global_config:
        models_config = global_config['models']
        llm_primary_set = False
        for provider, provider_config in models_config.items():
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

    return global_config, source_type, source_path


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
    )
    for key in env_keys:
        if os.environ.get(key, "").strip():
            return True
    # Global config: at least one provider with api_key
    models = config_dict.get("models") or {}
    for provider_cfg in models.values():
        if isinstance(provider_cfg, dict) and (provider_cfg.get("api_key") or "").strip():
            return True
    return False
