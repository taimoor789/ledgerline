"""Ledger core schema."""

from migrations.sqlfile import run_sql

revision = "0001_ledger_core"
down_revision = None


def upgrade() -> None:
    run_sql("0001_ledger_core.up.sql")


def downgrade() -> None:
    run_sql("0001_ledger_core.down.sql")
