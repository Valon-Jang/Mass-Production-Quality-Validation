from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import AppSettings
from app.infrastructure.database import Database
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


class ReadyDatabase:
    def __init__(self) -> None:
        self.checked = False
        self.disposed = False

    def check(self) -> None:
        self.checked = True

    def dispose(self) -> None:
        self.disposed = True


class FailedDatabase(ReadyDatabase):
    def check(self) -> None:
        raise RuntimeError("C:/private/secret-database.sqlite3")


def _settings() -> AppSettings:
    return AppSettings(database_url="sqlite+pysqlite:///:memory:")


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "backend"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.required_test_id("DQ-P0-HEALTH-001")
def test_live_and_ready_endpoints() -> None:
    database = ReadyDatabase()
    application = create_app(settings=_settings(), database=database)

    with TestClient(application) as client:
        live = client.get("/api/v1/health/live")
        ready = client.get("/api/v1/health/ready")

    assert live.status_code == 200
    assert live.json() == {
        "status": "ok",
        "service": "Mass Production Quality Validation",
        "version": "0.1.0",
    }
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "service": "Mass Production Quality Validation",
        "version": "0.1.0",
        "database": "ready",
    }
    assert database.checked is True
    assert database.disposed is True


def test_readiness_failure_is_sanitized() -> None:
    application = create_app(settings=_settings(), database=FailedDatabase())

    with TestClient(application) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"status": "unavailable", "component": "database"}}
    assert "private" not in response.text
    assert "sqlite3" not in response.text


def test_migrated_database_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    database_path = tmp_path / "readiness.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    command.upgrade(_alembic_config(database_url), "head")
    database = Database(database_url)
    application = create_app(settings=_settings(), database=database)

    with TestClient(application) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "ready"
    assert database_path.is_file()


def test_empty_database_is_not_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.sqlite3"
    database = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    application = create_app(settings=_settings(), database=database)

    with TestClient(application) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"status": "unavailable", "component": "database"}}
    assert not database_path.exists()


def test_stale_database_is_not_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "stale.sqlite3"
    database = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "0000"},
        )
    application = create_app(settings=_settings(), database=database)

    with TestClient(application) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"status": "unavailable", "component": "database"}}
