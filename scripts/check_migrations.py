"""Perform a read-only structural check of the Alembic migration graph."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(root / "backend"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    bases = script.get_bases()
    revisions = list(script.walk_revisions())
    if len(heads) != 1:
        print(f"MIGRATION CHECK FAILED: expected one head, found {heads}", file=sys.stderr)
        return 1
    if len(bases) != 1:
        print(f"MIGRATION CHECK FAILED: expected one base, found {bases}", file=sys.stderr)
        return 1
    if not revisions:
        print("MIGRATION CHECK FAILED: no migrations found", file=sys.stderr)
        return 1

    print(f"Migration graph passed: revisions={len(revisions)}, base={bases[0]}, head={heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
