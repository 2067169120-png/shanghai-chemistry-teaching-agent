"""Preview then explicitly complete missing local Word labels, without providers.

Only one named saved batch is eligible. No originals, source ranges, existing
nonempty labels, teacher edits or pinned corrections are replaced. An apply
requires the exact preview digest and the desktop application's exclusive lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from apply_word_boundary_repairs import desktop_lock

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_word_question_attributes import (
    complete_missing_attributes,
    suggest_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_word_questions import WordQuestionService


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def read_side_service(state_root):
    # Deliberately avoid facade initialization and its provider/export managers.
    facade = DesktopWorkbenchFacade.__new__(DesktopWorkbenchFacade)
    facade.paths = DesktopPaths.from_workspace(ROOT, state_root=state_root)
    facade._state = DesktopStateStore(state_root)
    return WordQuestionService(facade)


def coverage(rows):
    return {
        "question_count": len(rows),
        "primary_known": sum(r["primary_knowledge"]["id"] != "unknown" for r in rows),
        "curriculum_mapped": sum(bool(r["curriculum_candidates"]) for r in rows),
        "original_exam_known": sum(
            r["original_source"]["exam_type"]["value"] != "unknown" for r in rows
        ),
        "applicable_grade_known": sum(
            bool(r["applicable_grades"]["values"]) for r in rows
        ),
    }


def plan(service, batch_id):
    rows, source_count, snapshot_revision, warnings = service._annotation_snapshot(
        batch_id, {}
    )
    catalog = service._read_attribute_catalog()
    stored = service.attribute_store.get_many([row["key"] for row in rows])
    if len(stored) != len(rows):
        raise RuntimeError(
            "Completion requires an existing attribute row for every question"
        )
    planned = []
    changes = []
    samples = []
    for row in rows:
        old = stored[row["key"]]
        new = complete_missing_attributes(
            old, suggest_attributes(row, old["source"], catalog)
        )
        planned.append(new)
        if old != new:
            changed = {
                "key": row["key"],
                "source_sha256": row["source_sha256"],
                "question_revision": row["revision"],
                "old_attribute_revision": old["revision"],
                "new_attribute_revision": new["revision"],
                "old_primary": old["primary_knowledge"]["id"],
                "new_primary": new["primary_knowledge"]["id"],
                "old_sections": [
                    r["section_key"] for r in old["curriculum_candidates"]
                ],
                "new_sections": [
                    r["section_key"] for r in new["curriculum_candidates"]
                ],
            }
            changes.append(changed)
            # Only metadata in the shareable plan; source text stays private.
            if len(samples) < 24:
                samples.append(
                    {
                        **changed,
                        "source_name": row["source_name"],
                        "origin_block_start": row["origin_block_start"],
                    }
                )
    payload = {
        "batch_id": batch_id,
        "source_count": source_count,
        "snapshot_revision": snapshot_revision,
        "catalog_digest": digest(catalog),
        "stored_revisions": {k: v["revision"] for k, v in stored.items()},
        "changes": changes,
    }
    return {
        "plan_sha256": digest(payload),
        "source_count": source_count,
        "changed_questions": len(changes),
        "before": coverage(list(stored.values())),
        "after_proposed": coverage(planned),
        "warning_count": len(warnings),
        "distinct_warning_count": len(set(warnings)),
        "sample_metadata": samples,
        "changes": changes,
    }


def run(args):
    if args.report.exists():
        raise RuntimeError("Report exists; choose a new path")
    with desktop_lock(args.state_root, apply=args.apply):
        service = read_side_service(args.state_root)
        before_state = digest(service.state.snapshot())
        preview = plan(service, args.batch_id)
        report = {
            "mode": "apply" if args.apply else "preview",
            **preview,
            "model_invoked": False,
            "teacher_confirmed": False,
            "original_exam_facts_changed": False,
        }
        if args.apply:
            if (
                not args.expected_plan_sha256
                or args.expected_plan_sha256 != preview["plan_sha256"]
            ):
                raise RuntimeError(
                    "Current data differ from the reviewed plan; no apply"
                )
            report["receipt"] = service.annotate_imported_batch(
                args.batch_id, only_missing=True
            )
            # A fresh snapshot binds all source bytes and ranges once more.
            after = plan(service, args.batch_id)
            if (
                after["changed_questions"] != 0
                or after["before"] != preview["after_proposed"]
            ):
                raise RuntimeError("Applied result needs review; do not silently retry")
            report["after_actual"] = after["before"]
            report["idempotence_verified"] = True
        report["other_desktop_state_unchanged"] = before_state == digest(
            service.state.snapshot()
        )
        if not report["other_desktop_state_unchanged"]:
            raise RuntimeError("Unexpected desktop state change")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in {"changes", "sample_metadata", "receipt"}
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
