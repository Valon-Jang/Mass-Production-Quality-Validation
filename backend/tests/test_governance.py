from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.domain.scope import EXCLUDED_SCOPE_REQUIREMENTS, ExcludedCapability

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PACKAGE_SHA256 = "17750504c999f8c7ec331646ce00456d71bc9a33018f9cefe23be8ce41ebba03"


@pytest.mark.required_test_id("DQ-P0-BASELINE-001")
def test_baseline_zip_and_inner_manifest_are_immutable() -> None:
    package = ROOT / "MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_HANDOFF_PACKAGE.zip"
    assert hashlib.sha256(package.read_bytes()).hexdigest() == EXPECTED_PACKAGE_SHA256

    with zipfile.ZipFile(package) as archive:
        files = [name for name in archive.namelist() if not name.endswith("/")]
        manifest_name = next(name for name in files if name.endswith("/MANIFEST_SHA256.txt"))
        prefix = manifest_name.removesuffix("MANIFEST_SHA256.txt")
        manifest_lines = archive.read(manifest_name).decode("utf-8-sig").splitlines()

        assert len(files) == 15
        assert len(manifest_lines) == 14
        for line in manifest_lines:
            match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", line)
            assert match is not None
            expected_hash, relative_name = match.groups()
            content = archive.read(f"{prefix}{relative_name}")
            assert hashlib.sha256(content).hexdigest() == expected_hash


@pytest.mark.required_test_id("DQ-P0-REQ-001")
def test_baseline_and_living_requirement_trackers_are_separate_and_valid() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_requirements.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "baseline=333" in result.stdout
    assert "living_amendments=" in result.stdout


@pytest.mark.required_test_id("DQ-P0-BOOT-001")
def test_windows_bootstrap_uses_the_explicit_python312_environment() -> None:
    required_scripts = {
        "Bootstrap.ps1",
        "Dev.ps1",
        "Gate.ps1",
        "Lint.ps1",
        "Test.ps1",
        "Typecheck.ps1",
        "Update-Locks.ps1",
    }
    assert required_scripts.issubset({path.name for path in (ROOT / "scripts").glob("*.ps1")})

    bootstrap = (ROOT / "scripts" / "Bootstrap.ps1").read_text(encoding="utf-8-sig")
    assert "py -3.12 -m venv" in bootstrap
    assert ".venv" in bootstrap
    assert "--require-hashes" in bootstrap
    assert "requirements\\dev.lock" in bootstrap
    assert "Length -lt 1024" in bootstrap
    assert 'SimpleMatch "--hash=sha256:"' in bootstrap
    assert "alembic upgrade head" in bootstrap

    lock_updater = (ROOT / "scripts" / "Update-Locks.ps1").read_text(encoding="utf-8-sig")
    assert ".lock.tmp" in lock_updater
    assert "Assert-LockFile" in lock_updater
    assert "[System.IO.File]::Replace" in lock_updater
    assert "--allow-unsafe" in lock_updater

    dev_lock = (ROOT / "requirements" / "dev.lock").read_text(encoding="utf-8-sig")
    assert "pip==25.0.1 \\" in dev_lock
    assert "setuptools==82.0.1 \\" in dev_lock
    assert "# WARNING: The following packages were not pinned" not in dev_lock
    for package in ("pip==25.0.1", "setuptools==82.0.1"):
        package_block = dev_lock.split(package, maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
        assert "--hash=sha256:" in package_block

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8-sig")
    assert 'requires-python = ">=3.12,<3.13"' in pyproject
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8-sig"))
    assert {"bootstrap", "lint", "typecheck", "test", "gate", "dev"}.issubset(package["scripts"])


@pytest.mark.required_test_id("DQ-P0-SCOPE-001")
def test_phase0_contains_no_forbidden_automatic_decision_or_external_adapter() -> None:
    assert EXCLUDED_SCOPE_REQUIREMENTS == {
        "EXC-001": ExcludedCapability.PHOTO_AI_ANALYSIS,
        "EXC-002": ExcludedCapability.MEASUREMENT_DEVICE_CALIBRATION,
        "EXC-003": ExcludedCapability.SUPPLIER_RESPONSE_SPEED_SCORING,
        "EXC-004": ExcludedCapability.SUPPLIER_EMAIL_AUTO_SEND,
        "EXC-005": ExcludedCapability.AUTOMATIC_SHIPMENT_HOLD,
        "EXC-006": ExcludedCapability.AUTOMATIC_MASTER_SPEC_CHANGE,
        "EXC-007": ExcludedCapability.AUTOMATIC_SUPPLY_RATIO_DECISION,
        "EXC-008": ExcludedCapability.CLAIM_BASED_MARKET_NO_ISSUE_SCORING,
    }

    application_root = ROOT / "backend" / "app"
    source = "\n".join(
        path.read_text(encoding="utf-8-sig").lower() for path in application_root.rglob("*.py")
    )
    forbidden_live_tokens = {
        "win32com.client",
        "microsoft.graph",
        "send_mail(",
        "analyze_photo(",
        "photo_ai_client",
        "calibration_certificate",
        "supplier_response_speed_score",
        "auto_shipment_hold",
        "auto_spec_change",
        "auto_supply_ratio",
        "claim_market_no_issue_score",
    }
    assert all(token not in source for token in forbidden_live_tokens)
