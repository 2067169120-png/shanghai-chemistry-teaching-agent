"""Create a local metadata-only continuation queue; never edit imported sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.deeptutor_shchem_v1.local_import_continuation import (
    ContinuationError,
    write_continuation,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sources", type=int, default=98)
    args = parser.parse_args(argv)
    try:
        summary = write_continuation(
            args.workspace,
            args.state,
            args.output,
            expected_sources=args.expected_sources,
        )
    except Exception as exc:  # noqa: BLE001 -- validators may include private excerpts
        # Foreign exceptions may contain a source excerpt; do not print them.
        error = str(exc) if isinstance(exc, ContinuationError) else type(exc).__name__
        print(
            json.dumps({"status": "failed", "error": error}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
