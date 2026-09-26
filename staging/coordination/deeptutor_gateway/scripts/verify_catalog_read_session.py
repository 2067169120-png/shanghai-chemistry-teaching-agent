"""Compare full live catalogs using direct readers and the desktop read session."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths

    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=output / "isolated-state"),
        provider_store=object(),
    )
    rows = []
    try:
        for scope in ("master", "wave1", "supplemental"):
            start = time.perf_counter()
            direct = (
                facade._supplemental.theme_groups()
                if scope == "supplemental"
                else facade._themes.groups(scope)
            )
            direct_seconds = time.perf_counter() - start
            start = time.perf_counter()
            scoped = facade._load_theme_scope(scope)
            scoped_seconds = time.perf_counter() - start
            direct_hash, scoped_hash = digest(direct), digest(scoped)
            assert direct == scoped, scope
            assert direct_hash == scoped_hash
            row = {
                "scope": scope,
                "direct_seconds": direct_seconds,
                "scoped_seconds": scoped_seconds,
                "complete_catalog_equal": True,
                "canonical_sha256": scoped_hash,
                "counts": scoped.get("counts"),
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        report = {
            "comparisons": rows,
            "model_calls": 0,
            "measurement": "one sequential direct/scoped pair per scope, not a statistical benchmark",
            "facade_sha256": hashlib.sha256(
                (
                    ROOT / "integrations/deeptutor_shchem_v1/desktop_facade.py"
                ).read_bytes()
            ).hexdigest(),
        }
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        facade.shutdown()


if __name__ == "__main__":
    main()
