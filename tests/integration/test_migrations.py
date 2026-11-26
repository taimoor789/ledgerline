import psycopg
import pytest

from tests.conftest import Db, migrate

pytestmark = pytest.mark.db


def test_up_down_up(db: Db) -> None:
    with psycopg.connect(db.admin, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS migration_check")
        conn.execute("CREATE DATABASE migration_check")
    url = db.url(db="migration_check")

    def has_schema() -> bool:
        with psycopg.connect(url) as conn:
            row = conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'ledger'")
            return row.fetchone() == (1,)

    migrate(url)
    assert has_schema()
    migrate(url, "base", down=True)
    assert not has_schema()
    migrate(url)
    assert has_schema()
