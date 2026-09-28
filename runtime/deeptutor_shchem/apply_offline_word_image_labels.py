"""Preview source-closed local image label candidates; apply only an exact plan.

The root model reading declaration remains an AI candidate, never human review.
No provider is called. Apply uses the same desktop lock, CAS transaction and
post-commit recovery receipt as the independent text-only route.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.offline_word_image_label_review import OfflineWordImageLabelReviewService
from runtime.deeptutor_shchem import apply_offline_word_labels as common


def run(args, *, service_factory=None):
    return common.run(args, service_factory=service_factory, review_service_factory=OfflineWordImageLabelReviewService)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--label-mode", choices=common.LABEL_MODES, default="missing_only")
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except Exception as exc:
        print(json.dumps(common._failure(exc, common._operation_metadata(args)).operation, ensure_ascii=False))
        return 1
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"entries", "saved_attribute_revisions"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
