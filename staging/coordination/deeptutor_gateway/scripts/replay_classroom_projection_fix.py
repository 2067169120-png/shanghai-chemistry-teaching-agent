"""Offline regression replay after a live candidate exposed dense image pages.

Preserves every source text and the frozen candidate. This verifies layout
behavior only; it does not repair teaching content or certify class readiness.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    NativePreparationRenderer,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    if output.exists():
        raise RuntimeError("Replay output already exists")
    candidate = json.loads(args.candidate.read_text("utf-8"))
    images = PreparationImageStore(args.assets)
    image_data = {
        asset["asset_id"]: images.load(asset) for asset in candidate["image_assets"]
    }
    result = NativePreparationRenderer().render(
        candidate,
        output_kind=candidate["artifact_mode"],
        output_dir=output,
        image_data=image_data,
    )
    report = json.loads((output / "qa_report.json").read_text("utf-8"))
    overflow = next(
        row for row in report["checks"] if row["check"] == "text_box_overflow_estimate"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "slide_count": result["slide_count"],
                "remaining_overflow": overflow["items"],
                "model_invoked": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
