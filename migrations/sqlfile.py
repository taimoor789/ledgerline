from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).parent / "sql"


def run_sql(name: str) -> None:
    # Raw driver call with no params, so '%' and ':=' in plpgsql pass through untouched.
    driver_conn = op.get_bind().connection.driver_connection
    assert driver_conn is not None
    driver_conn.execute((SQL_DIR / name).read_text())
