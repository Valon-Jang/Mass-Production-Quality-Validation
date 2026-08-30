from pathlib import Path

import pytest

from app.config import AppSettings

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.required_test_id("DQ-P0-NAME-001")
def test_official_product_name_is_used_by_new_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_APP_NAME", raising=False)
    assert AppSettings().app_name == "Mass Production Quality Validation"

    checked_files = [
        *list((ROOT / "backend").rglob("*.py")),
        *list((ROOT / "scripts").glob("*.py")),
        *list((ROOT / "scripts").glob("*.ps1")),
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        ROOT / "package.json",
    ]
    checked_files.extend(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if "source" not in path.relative_to(ROOT / "docs").parts
    )
    legacy_name = "Valonark " + "OQC AI"
    for path in checked_files:
        assert legacy_name not in path.read_text(encoding="utf-8-sig"), path


def test_settings_read_prefixed_environment_without_writing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MASS_PRODUCTION_QUALITY_VALIDATION_ENVIRONMENT", "test")
    monkeypatch.setenv(
        "MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", "sqlite+pysqlite:///:memory:"
    )
    before = set(tmp_path.iterdir())

    settings = AppSettings()

    assert settings.environment == "test"
    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert set(tmp_path.iterdir()) == before
