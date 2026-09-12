"""Keep public reading counts distinct from raw downloads and source approval."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CATALOG_ROOT = ROOT / "knowledge/courseware/smartedu"


def _catalog():
    return json.loads(
        (CATALOG_ROOT / "first-batch-preview-catalog.json").read_text(encoding="utf-8")
    )


def test_reading_counts_come_from_unique_courses_and_pages():
    catalog = _catalog()
    courses = catalog["courseware"]
    counts = catalog["counts"]
    assert len({item["id"] for item in courses}) == len(courses)
    assert counts["course_packages_reviewed"] == len(
        {item["course_package_id"] for item in courses}
    )
    assert counts["courseware_previews_reviewed"] == len(courses)
    assert counts["presentation_pages_visually_reviewed"] == sum(
        len(set(item["visually_reviewed_pages"])) for item in courses
    )
    for item in courses:
        assert item["visually_reviewed_pages"] == list(
            range(1, item["preview_page_count"] + 1)
        )
        assert (CATALOG_ROOT / item["design_card"]).is_file()
    assert counts["derived_design_cards"] == len(
        {item["design_card"] for item in courses}
    )


def test_supplementary_text_counts_do_not_increase_presentation_counts():
    catalog = _catalog()
    documents = list(
        catalog["restored_access_package_review"]["supplementary_documents"]
    )
    for course in catalog["courseware"]:
        documents.extend(course.get("supplementary_documents", []))
    assert catalog["counts"]["supplementary_documents_text_reviewed"] == len(documents)
    assert catalog["counts"]["supplementary_document_pages_text_reviewed"] == sum(
        len(item["text_reviewed_pages"]) for item in documents
    )
    assert all(not item["downloaded"] for item in documents)


def test_new_course_is_not_mislabeled_as_a_download_or_textbook_verification():
    catalog = _catalog()
    course = next(
        item
        for item in catalog["courseware"]
        if item["id"] == "smartedu-electron-configuration-representations"
    )
    assert catalog["counts"]["source_courseware_files_downloaded"] == sum(
        bool(item["downloaded"]) for item in catalog["courseware"]
    )
    assert not course["downloaded"] and not course["raw_format_verified"]
    assert course["raw_sha256"] is None
    assert course["declared_format_from_title"] == "unknown"
    assert course["use_status"] == "design_reference_only"
    assert course["evidence_pages"]["textbook_locator_on_slide"] == [24]
    assert "第24页教材引文未独立核对原页" in course["limitations"]
    assert catalog["latest_new_course_review"]["full_screen_login_prompt_seen"]
