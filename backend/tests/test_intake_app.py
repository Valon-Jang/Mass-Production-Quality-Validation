from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook  # type: ignore[import-untyped]

import app.main as main_module
from app.application.intake_jobs import IntakeJobManager
from app.config import AppSettings
from app.infrastructure.file_store import XLSX_MIME
from app.main import create_app


class ReadyDatabase:
    def __init__(self) -> None:
        self.disposed = False

    def check(self) -> None:
        return None

    def dispose(self) -> None:
        self.disposed = True


class UnreadyDatabase(ReadyDatabase):
    def check(self) -> None:
        raise RuntimeError("schema is not ready")


class LifecycleManager:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def shutdown(self) -> None:
        self.stopped += 1


def _settings(tmp_path: Path, *, frontend_dist: Path | None = None) -> AppSettings:
    return AppSettings(
        database_url="sqlite+pysqlite:///:memory:",
        original_file_store_root=tmp_path / "originals",
        intake_staging_root=tmp_path / "staging",
        max_upload_bytes=4 * 1024 * 1024,
        intake_queue_capacity=2,
        intake_registry_capacity=8,
        frontend_dist_path=frontend_dist or (tmp_path / "missing-frontend"),
    )


def _workbook_bytes() -> bytes:
    stream = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OQC"
    sheet["A1"] = "MODEL-LOCAL-1"
    sheet["A2"] = "LOT-LOCAL-1"
    sheet["B3"] = 10.25
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _await_terminal(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(
            f"/api/v1/intake/jobs/{job_id}",
            params={"project_key": "local-project"},
        )
        assert response.status_code == 200
        snapshot: dict[str, Any] = response.json()
        if snapshot["terminal"] is True:
            return snapshot
        sleep(0.01)
    raise AssertionError("local intake did not become terminal")


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-005")
def test_built_korean_frontend_is_served_without_shadowing_api(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html lang="ko"><body>'
        "Mass Production Quality Validation 한글 접수"
        "</body></html>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.MASSPRODUCTIONQUALITYVALIDATION=true;", encoding="utf-8")
    database = ReadyDatabase()
    application = create_app(
        settings=_settings(tmp_path, frontend_dist=dist),
        database=database,
    )

    with TestClient(application) as client:
        root = client.get("/")
        asset = client.get("/assets/app.js")
        health = client.get("/api/v1/health/live")

    assert root.status_code == 200
    assert "Mass Production Quality Validation 한글 접수" in root.text
    assert asset.status_code == 200
    assert asset.text == "window.MASSPRODUCTIONQUALITYVALIDATION=true;"
    assert health.status_code == 200
    assert health.json()["service"] == "Mass Production Quality Validation"
    assert database.disposed is True
    assert not (tmp_path / ".localdata").exists()


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-006")
def test_application_lifespan_starts_and_stops_intake_on_each_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LifecycleBulkManager(LifecycleManager):
        limits = SimpleNamespace(
            max_files=2,
            max_file_bytes=1024,
            max_batch_bytes=2048,
        )

    class LifecycleFinalizationManager(LifecycleManager):
        pass

    monkeypatch.setattr(main_module, "BulkImportManager", LifecycleBulkManager)
    monkeypatch.setattr(
        main_module,
        "BulkFinalizationManager",
        LifecycleFinalizationManager,
    )
    database = ReadyDatabase()
    manager = LifecycleManager()
    bulk = LifecycleBulkManager()
    finalization = LifecycleFinalizationManager()
    application = create_app(
        settings=_settings(tmp_path),
        database=database,
        intake_manager=cast(IntakeJobManager, manager),
        bulk_manager=cast(Any, bulk),
        bulk_finalization=cast(Any, finalization),
    )

    with TestClient(application) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert manager.started == 1
        assert manager.stopped == 0
        assert bulk.started == finalization.started == 1
        assert bulk.stopped == finalization.stopped == 0
    with TestClient(application) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert manager.started == 2
        assert manager.stopped == 1
        assert bulk.started == finalization.started == 2
        assert bulk.stopped == finalization.stopped == 1

    assert manager.stopped == 2
    assert bulk.stopped == finalization.stopped == 2
    assert database.disposed is True
    assert not (tmp_path / ".localdata").exists()

    unready = UnreadyDatabase()
    unavailable_app = create_app(
        settings=_settings(tmp_path / "unready"),
        database=unready,
        intake_manager=cast(IntakeJobManager, LifecycleManager()),
    )
    with TestClient(unavailable_app) as client:
        finalization_response = client.get(
            "/api/v1/bulk/batches/unknown/finalization-candidate",
            params={"project_key": "local-project"},
        )
        history_response = client.post(
            "/api/v1/history/comparisons",
            json={
                "project_key": "local-project",
                "left": {"date_from": "2026-01-01", "date_to": "2026-01-31"},
                "right": {"date_from": "2026-02-01", "date_to": "2026-02-28"},
                "data_statuses": ["PENDING"],
                "filters": {},
                "limit_per_side": 10,
            },
        )
    assert finalization_response.status_code == 503
    assert history_response.status_code == 503
    assert unready.disposed is True


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-007")
def test_real_http_upload_preserves_and_scans_before_mapping_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    payload = _workbook_bytes()
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    database = ReadyDatabase()
    # Keep the content-addressed File Store below the legacy Windows path limit.
    settings = _settings(tmp_path.parent / "ui7")
    application = create_app(settings=settings, database=database)

    with TestClient(application) as client:
        accepted = client.post(
            "/api/v1/intake/jobs",
            data={
                "project_key": "local-project",
                "model_hint": "MODEL-LOCAL-1",
                "lot_hint": "LOT-LOCAL-1",
            },
            files={"workbook": ("local_oqc.xlsx", payload, XLSX_MIME)},
        )
        assert accepted.status_code == 202
        terminal = _await_terminal(client, accepted.json()["job_id"])

    assert terminal["status"] == "MAPPING_REQUIRED"
    assert terminal["receipt"]["original_filename"] == "local_oqc.xlsx"
    assert terminal["receipt"]["content_sha256"] == expected_sha256
    assert terminal["receipt"]["model_candidates"] == ["MODEL-LOCAL-1"]
    assert terminal["receipt"]["lot_candidates"] == ["LOT-LOCAL-1"]
    assert terminal["scan"]["sha256_before"] == expected_sha256
    assert terminal["scan"]["sha256_after"] == expected_sha256
    assert terminal["scan"]["sheet_count"] == 1
    assert terminal["scan"]["sheets"][0]["name"] == "OQC"
    assert terminal["poll_after_ms"] is None
    assert tuple(settings.intake_staging_root.iterdir()) == ()
    assert settings.original_file_store_root.is_dir()
    assert not (tmp_path / ".localdata").exists()
    assert database.disposed is True
