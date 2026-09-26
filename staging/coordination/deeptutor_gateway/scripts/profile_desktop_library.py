"""Measure the local desktop browse path without providers or real user state."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import pstats
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", default="master")
    parser.add_argument("--no-profile", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_version import DESKTOP_VERSION

    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=output / "isolated-state"),
        provider_store=object(),
    )
    measured = []

    def measure(label, operation):
        profile = cProfile.Profile()
        start = time.perf_counter()
        value = operation() if args.no_profile else profile.runcall(operation)
        elapsed = time.perf_counter() - start
        stream = io.StringIO()
        if not args.no_profile:
            stats = pstats.Stats(profile, stream=stream).sort_stats("cumulative")
            stats.print_stats(32)
            (output / (label + ".txt")).write_text(stream.getvalue(), encoding="utf-8")
            profile.dump_stats(str(output / (label + ".prof")))
        measured.append(
            {
                "operation": label,
                "seconds": elapsed,
                "profile_enabled": not args.no_profile,
            }
        )
        print(f"{label}: {elapsed:.3f}s", flush=True)
        return value

    try:
        first = measure(
            "search-first", lambda: facade.search_themes(scope=args.scope, limit=1)
        )
        assert first.cards
        detail = measure(
            "detail-first", lambda: facade.library_theme_detail(first.cards[0])
        )
        second = measure(
            "search-repeat", lambda: facade.search_themes(scope=args.scope, limit=1)
        )
        assert first == second
        # Source identity/content digest supports a later baseline comparison;
        # per-view random image session IDs are deliberately excluded.
        snapshot = asdict(detail)
        for image in snapshot["shared_images"]:
            image.pop("view_id", None)
        for part in snapshot["parts"]:
            for image in part["question_images"]:
                image.pop("view_id", None)
        content_hash = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        report = {
            "version": DESKTOP_VERSION,
            "scope": args.scope,
            "operations": measured,
            "same_search_result": True,
            "detail_units": len(detail.parts),
            "detail_content_sha256": content_hash,
            "theme_identity": detail.key,
            "model_calls": 0,
            "source_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in (
                    "integrations/deeptutor_shchem_v1/desktop_facade.py",
                    "integrations/deeptutor_shchem_v1/desktop_library_session.py",
                )
            },
        }
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        facade.shutdown()


if __name__ == "__main__":
    main()
