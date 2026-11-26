from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEDGERLINE_", env_file=".env", extra="ignore")

    database_url: str
    pool_min_size: int = 2
    pool_max_size: int = 10
    client_id: str = "default"
