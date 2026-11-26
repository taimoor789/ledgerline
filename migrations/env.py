from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, pool


class _Migration(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEDGERLINE_", env_file=".env", extra="ignore")
    migration_url: str = ""


def _url() -> str:
    url = context.config.get_main_option("sqlalchemy.url") or _Migration().migration_url
    if not url:
        raise RuntimeError("Set LEDGERLINE_MIGRATION_URL")
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline mode is not supported")
run()
