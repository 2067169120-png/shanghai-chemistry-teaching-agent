"""Replay one frozen live model result locally; never access provider settings.

Preserves authored teaching content, reruns current deterministic normalization
and rendering, and records changes separately from the original response.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from verify_real_source_preparation import (
    EXPECTED,
    FIGURE,
    ROOT,
    BundledArtifactRenderer,
    digest,
)

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_candidate,
)

SOURCE = ROOT / "runtime/deeptutor_shchem/qa/real-source-preparation-v14-20260909-r2"
RETURNED_SHA = "d6408995c5ece41aaaaf08677b5bffbfdbf180db1c54b48d8e296d051d77ab0f"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    if output.exists():
        raise RuntimeError("Use a new output directory")
    returned = SOURCE / "returned-candidate.json"
    if digest(returned) != RETURNED_SHA or any(
        digest(p) != h for p, h in EXPECTED.items()
    ):
        raise RuntimeError("Frozen source changed")
    source_files = (
        list(SOURCE.glob("*.json"))
        + list(SOURCE.glob("*.docx"))
        + list(SOURCE.glob("*.pptx"))
    )
    before = {str(p): digest(p) for p in source_files}
    raw = json.loads(returned.read_text("utf-8"))
    original_raw = deepcopy(raw)
    brief = json.loads((SOURCE / "teacher-brief.json").read_text("utf-8"))
    candidate = normalize_preparation_candidate(raw, brief)
    if raw != original_raw:
        raise RuntimeError("Normalization mutated the model response")
    previous = json.loads((SOURCE / "candidate.json").read_text("utf-8"))
    for key in (
        "activities",
        "slides",
        "lesson_stages",
        "objectives",
        "assessments",
        "homework",
    ):
        if candidate[key] != previous[key]:
            raise RuntimeError(
                "Authored content or timing changed during layout replay"
            )
    BundledArtifactRenderer(args.artifact_python).render(
        candidate,
        output_kind="linked_bundle",
        output_dir=output,
        report_progress=lambda *_args: None,
        is_cancelled=lambda: False,
        image_data={candidate["image_assets"][0]["asset_id"]: FIGURE.read_bytes()},
    )
    receipt = {
        "method": "frozen_model_response_deterministic_replay",
        "source_returned_candidate_sha256": RETURNED_SHA,
        "new_model_calls": 0,
        "authored_content_and_timing_unchanged": True,
        "original_artifacts_unchanged": all(
            digest(Path(p)) == h for p, h in before.items()
        ),
        "uncertainties_before": previous["uncertainties"],
        "uncertainties_after": candidate["uncertainties"],
        "teacher_approval": False,
        "visual_review": "pending",
    }
    (output / "replay-verification.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in receipt.items() if not k.startswith("uncertainties")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
