"""Run the release test suite and enforce stable IDs plus a zero-skip policy."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

import pytest


class RegressionContractPlugin:
    def __init__(
        self,
        manifest_path: Path,
        global_manifest_path: Path,
        minimum_count_path: Path | None,
    ) -> None:
        self.expected_ids = {
            line.strip()
            for line in manifest_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.allowed_ids = {
            line.strip()
            for line in global_manifest_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if not self.expected_ids.issubset(self.allowed_ids):
            unknown = sorted(self.expected_ids.difference(self.allowed_ids))
            raise ValueError(f"Selected test IDs are absent from the global manifest: {unknown}")
        self.collected_ids: Counter[str] = Counter()
        self.disallowed_outcomes: list[str] = []
        self.minimum_count = (
            int(minimum_count_path.read_text(encoding="utf-8-sig").strip())
            if minimum_count_path is not None
            else None
        )

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        if self.minimum_count is not None and len(items) < self.minimum_count:
            raise pytest.UsageError(
                f"Release test count fell below the approved baseline: "
                f"minimum={self.minimum_count}, collected={len(items)}"
            )
        for item in items:
            for marker in item.iter_markers(name="required_test_id"):
                if len(marker.args) != 1 or not isinstance(marker.args[0], str):
                    raise pytest.UsageError(
                        f"{item.nodeid}: required_test_id must contain one string"
                    )
                test_id = marker.args[0]
                if test_id not in self.allowed_ids:
                    raise pytest.UsageError(
                        f"{item.nodeid}: {test_id} is not in required_regression_test_ids.txt"
                    )
                self.collected_ids[test_id] += 1

        missing = sorted(self.expected_ids.difference(self.collected_ids))
        duplicates = sorted(test_id for test_id, count in self.collected_ids.items() if count != 1)
        if missing or duplicates:
            raise pytest.UsageError(
                f"Regression ID contract failed; missing={missing}, duplicates={duplicates}"
            )

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.disallowed_outcomes.append(f"collection skip: {report.nodeid}")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        was_xfail = getattr(report, "wasxfail", None)
        if report.skipped:
            self.disallowed_outcomes.append(f"runtime skip: {report.nodeid} ({report.when})")
        elif was_xfail is not None:
            self.disallowed_outcomes.append(f"xfail/xpass: {report.nodeid} ({was_xfail})")

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        if self.disallowed_outcomes:
            reporter: Any = session.config.pluginmanager.get_plugin("terminalreporter")
            if reporter is not None:
                reporter.write_sep(
                    "=",
                    "Release suite forbids skip/xfail outcomes: "
                    + "; ".join(self.disallowed_outcomes),
                    red=True,
                )
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default="backend/tests/required_regression_test_ids.txt",
        help="Repository-relative manifest of IDs required for this release gate",
    )
    parser.add_argument(
        "--minimum-count-file",
        default="backend/tests/required_current_test_count.txt",
        help="Repository-relative file containing the approved minimum test count",
    )
    arguments = parser.parse_args()
    plugin = RegressionContractPlugin(
        root / arguments.manifest,
        root / "backend" / "tests" / "required_regression_test_ids.txt",
        root / arguments.minimum_count_file,
    )
    return int(pytest.main([str(root / "backend" / "tests"), "-q"], plugins=[plugin]))


if __name__ == "__main__":
    raise SystemExit(main())
