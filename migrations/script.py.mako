"""${message}"""

from migrations.sqlfile import run_sql

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}


def upgrade() -> None:
    run_sql("${up_revision}.up.sql")


def downgrade() -> None:
    run_sql("${up_revision}.down.sql")
