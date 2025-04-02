from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    diagnostic_log_file: str = './diagnostic.log'
    diagnostic_log_file_max_bytes: int = 100 * 1024 * 1024  # logSize 100MB
    diagnostic_log_file_backup_count: int = 5  # logNum

    default_max_arg_length: int = 1000
    default_max_result_length: int = 1000

    model_config = SettingsConfigDict(case_sensitive=False, extra='ignore')


settings = Settings()
