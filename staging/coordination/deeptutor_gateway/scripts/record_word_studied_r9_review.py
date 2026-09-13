"""Freeze assistant visual-review evidence without changing source artifacts."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读讲练版-r9"
QA = ROOT / "runtime/deeptutor_shchem/qa/word-studied-r9-office-20260909"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    c = json.loads((OUT / "candidate.json").read_text("utf-8"))
    mothers = {
        "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx": "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d",
        "课本/沪科技化学必修第一册【高清教材】.pdf": "a565f0a15ffd10c704f4be42bfe7200c125b68959d11ef582acdc45dde2ccf22",
    }
    assert all(digest(ROOT / p) == h for p, h in mothers.items())
    assert c["topic"] == "电解质的电离"
    assert len(c["slides"]) == 44
    assert not any(
        "不能唯一核对活动时间" in u["description"] for u in c["uncertainties"]
    )
    budgets = []
    for a in c["activities"]:
        slide_minutes = sum(
            s["minutes"] for s in c["slides"] if s["activity_ids"] == [a["id"]]
        )
        stage_minutes = sum(
            s["minutes"] for s in c["lesson_stages"] if s["activity_ids"] == [a["id"]]
        )
        assert a["minutes"] == slide_minutes == stage_minutes
        budgets.append(
            {
                "activity": a["id"],
                "activity_minutes": a["minutes"],
                "slide_minutes": slide_minutes,
                "stage_minutes": stage_minutes,
            }
        )
    previous = QA.parent / "word-studied-r8-office-20260909"
    assert all(
        digest(p) == digest(previous / "ppt" / p.name)
        for p in (QA / "ppt").glob("slide-*.png")
    )
    assert all(
        digest(p) == digest(previous / "word" / p.name)
        for p in (QA / "word").glob("student_worksheet-*.png")
    )
    machine = json.loads((OUT / "qa_report.json").read_text("utf-8"))
    receipt = {
        "reviewer": "primary_assistant_not_human_teacher",
        "scope": "artifact_visual_and_content_alignment_review_not_teaching_approval",
        "artifacts": {
            p.name: digest(p) for p in OUT.iterdir() if p.suffix in {".pptx", ".docx"}
        },
        "source_hashes_unchanged": mothers,
        "office_rendered_pages": {"ppt": 44, "lesson_plan": 11, "student_worksheet": 8},
        "visually_reviewed_pages": {
            "ppt": list(range(1, 45)),
            "lesson_plan": list(range(1, 12)),
            "student_worksheet": list(range(1, 9)),
        },
        "review_reuse": "R9 PPT and worksheet PNGs are byte-identical to fully reviewed R8 pages; R8 PPT pages 2-24 are byte-identical to reviewed R7 pages. R9 lesson plan pages 1-11 reviewed directly.",
        "observations": [
            "No observed clipping or overlapping text in actual Office exports.",
            "Eight embedded source images retained; source definitions compared on page with editable transcription.",
            "Worksheet Q2 shares the K3 page; each Q8 three-equation writing table is on one page; Q7 uses the same unedited source image.",
            "Worksheet response areas remain blank; Q9 answer appears only in teacher notes.",
            "Some automatic numbered bullets coexist with original Q8 numbering; content remains identifiable but may be cosmetically simplified later.",
        ],
        "remaining_machine_failures": [x for x in machine["checks"] if not x["passed"]],
        "machine_warning_disposition": "Pages 5,16,37 retain conservative Pillow text-box overflow estimates. Actual PowerPoint exports reviewed with no clipping observed. Estimates were not suppressed and classroom rear-row readability is not certified.",
        "source_locator_warning_disposition": "Word reading-page citations are explicit but not recognized by the current locator regex; no original source claims inferred from that check.",
        "activity_time_alignment": budgets,
        "two_period_minutes": [40, 40],
        "api_calls_for_initial_generation": 1,
        "api_calls_for_all_editorial_revisions": 0,
        "regression_tests": {
            "passed": 14,
            "scope": "source-studied input and explicit returned-candidate revision",
        },
        "docx_render_engine": "Read-only Microsoft Word BodyRange ExportAsFixedFormat and Poppler; bundled LibreOffice unavailable.",
        "teacher_review_required": True,
        "human_review_completed": False,
        "real_classroom_validation": False,
        "publication_allowed": False,
        "native_app_artifact_registration": "Not registered as a completed native generation task. Original failed task and response retained; files are explicitly edited offline artifacts.",
        "page_images": {str(p.relative_to(QA)): digest(p) for p in QA.rglob("*.png")},
    }
    path = QA / "assistant-review.json"
    if path.exists():
        raise SystemExit("Refusing replacement of frozen review")
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), "utf-8")
    print(
        json.dumps(
            {
                "pages": receipt["office_rendered_pages"],
                "budgets": budgets,
                "remaining_machine_failed_checks": len(
                    receipt["remaining_machine_failures"]
                ),
                "source_hashes_unchanged": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
