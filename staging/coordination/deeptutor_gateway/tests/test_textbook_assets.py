from __future__ import annotations

import json
import stat
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import desktop_textbook_assets as assets

SHA = "a" * 64


def asset(**changes):
    value = {
        "visual_asset_id": "TB-M1-C1-V01",
        "label": "图 1.1 候选图示",
        "asset_type": "classification_diagram",
        "description": "仅供对照教材原页核对的候选说明。",
        "volume_id": "TB-M1",
        "chapter_id": "TB-M1-C1",
        "section_key": "TB-M1-C1:1.1",
        "supplement_node_key": None,
        "pdf_page": 12,
        "printed_page": 7,
        "source_sha256": SHA,
        "anchor_type": "whole_page",
        "bbox": None,
        "cropped": False,
        "asset_localization_status": "whole_page_only_pending_bbox_review",
        "review_status": "candidate-only",
        "candidate_only": True,
        "human_reviewed": False,
        "retrieval_ready": False,
        "teaching_use_allowed": False,
        "generation_allowed": False,
        "publication_allowed": False,
        "page_image_path": "sh-chem-db/kb/derived/textbooks/page-012.png",
        "page_image_sha256": "b" * 64,
        "evidence_refs": ["EV-TB-M1-P012"],
    }
    value.update(changes)
    return value


def write_catalog(workspace, rows):
    path = workspace / assets.CATALOG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8")
    return path


def load(workspace, **changes):
    arguments = {
        "volume_id": "TB-M1", "section_key": "TB-M1-C1:1.1",
        "source_sha256": SHA, "pdf_pages": [12, 13],
    }
    arguments.update(changes)
    return assets.load_textbook_assets(workspace, **arguments)


def test_exact_identity_page_scope_and_output_whitelist(tmp_path):
    write_catalog(tmp_path, [
        asset(),
        asset(visual_asset_id="different-book", volume_id="TB-M2"),
        asset(visual_asset_id="different-section", section_key="TB-M1-C1:1.2"),
        asset(visual_asset_id="different-page", pdf_page=14),
        asset(visual_asset_id="different-sha", source_sha256="c" * 64),
    ])
    result = load(tmp_path)
    assert [row["visual_asset_id"] for row in result["assets"]] == ["TB-M1-C1-V01"]
    row = result["assets"][0]
    assert row["label"] == "图 1.1 候选图示"
    assert row["pdf_page"] == 12 and row["printed_page"] == 7
    assert row["candidate_only"] is True
    assert all(row[key] is False for key in (
        "human_reviewed", "retrieval_ready", "teaching_use_allowed",
        "generation_allowed", "publication_allowed", "cropped",
    ))
    assert not {"page_image_path", "page_image_sha256", "evidence_refs"} & row.keys()
    assert result["notices"] == [assets.STALE]


def test_null_section_is_exact_and_unknown_printed_page_stays_unknown(tmp_path):
    write_catalog(tmp_path, [asset(), asset(
        visual_asset_id="supplement", section_key=None, chapter_id=None,
        supplement_node_key="TB-M1:experiment", printed_page=None,
    )])
    result = load(tmp_path, section_key=None)
    assert [row["visual_asset_id"] for row in result["assets"]] == ["supplement"]
    assert result["assets"][0]["printed_page"] is None
    text, count = assets.textbook_assets_for_page(result, 12, [12])
    assert count == 1 and "书上印刷页码：待核对" in text


@pytest.mark.parametrize("changes", [
    {"candidate_only": False}, {"human_reviewed": True},
    {"retrieval_ready": True}, {"teaching_use_allowed": True},
    {"generation_allowed": True}, {"publication_allowed": True},
    {"human_reviewed": 0}, {"candidate_only": 1}, {"cropped": 0},
    {"anchor_type": "crop"}, {"bbox": [0, 0, 10, 10]}, {"cropped": True},
    {"asset_localization_status": "reviewed"}, {"review_status": "approved"},
    {"pdf_page": True}, {"printed_page": False}, {"pdf_page": 0},
    {"printed_page": -1}, {"source_sha256": "ABC"},
    {"label": "x" * 257}, {"description": "x" * (assets.MAX_TEXT + 1)},
    {"visual_asset_id": "x" * 181}, {"description": "bad\x00text"},
    {"label": "\ud800"},
])
def test_invalid_record_and_open_gates_are_withheld(tmp_path, changes):
    path = tmp_path / assets.CATALOG_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    # ensure_ascii also permits constructing an escaped invalid Unicode scalar.
    path.write_text(json.dumps(asset(**changes)) + "\n", encoding="utf-8")
    result = load(tmp_path)
    assert result == {"assets": [], "notices": [assets.UNAVAILABLE]}


def test_missing_gate_cannot_default_to_false(tmp_path):
    row = asset()
    del row["publication_allowed"]
    write_catalog(tmp_path, [row])
    assert load(tmp_path) == {"assets": [], "notices": [assets.UNAVAILABLE]}


@pytest.mark.parametrize("changes", [
    {}, {"description": "conflicting explanation"},
    {"volume_id": "TB-M2"}, {"source_sha256": "c" * 64},
    {"pdf_page": 99}, {"candidate_only": False},
])
def test_all_repeated_ids_withheld_before_filtering_even_invalid_twins(tmp_path, changes):
    write_catalog(tmp_path, [asset(), asset(**changes), asset(visual_asset_id="unambiguous")])
    result = load(tmp_path)
    assert [row["visual_asset_id"] for row in result["assets"]] == ["unambiguous"]
    assert assets.CONFLICT in result["notices"]


@pytest.mark.parametrize("payload", [
    b"{", b"[]", b"null", b"\xff",
    b'{"visual_asset_id":"first","visual_asset_id":"second"}',
    b'{"unknown":{"repeat":1,"repeat":2}}',
    b'{"unknown":NaN}', b'{"unknown":Infinity}', b'{"unknown":-Infinity}',
    b'{"unknown":1e999}', b'{"unknown":-1e999}',
    (b'[' * 1500) + (b']' * 1500),
])
def test_strict_json_and_nonfinite_rejection(tmp_path, payload):
    path = write_catalog(tmp_path, [])
    path.write_bytes(payload + b"\n")
    assert load(tmp_path) == {"assets": [], "notices": [assets.UNAVAILABLE]}


def test_invalid_json_after_valid_line_does_not_return_partial_catalog(tmp_path):
    path = write_catalog(tmp_path, [asset()])
    with path.open("ab") as stream:
        stream.write(b'{"duplicate":1,"duplicate":2}\n')
    assert load(tmp_path) == {"assets": [], "notices": [assets.UNAVAILABLE]}


@pytest.mark.parametrize("limit", ["MAX_FILE_BYTES", "MAX_LINE_BYTES", "MAX_ROWS"])
def test_catalog_limits_fail_closed(tmp_path, monkeypatch, limit):
    path = write_catalog(tmp_path, [asset(), asset(visual_asset_id="second")])
    value = {"MAX_FILE_BYTES": path.stat().st_size - 1,
             "MAX_LINE_BYTES": 64, "MAX_ROWS": 1}[limit]
    monkeypatch.setattr(assets, limit, value)
    assert load(tmp_path) == {"assets": [], "notices": [assets.UNAVAILABLE]}


def test_limits_allow_exact_boundary(tmp_path, monkeypatch):
    path = write_catalog(tmp_path, [asset()])
    data = path.read_bytes()
    monkeypatch.setattr(assets, "MAX_FILE_BYTES", len(data))
    monkeypatch.setattr(assets, "MAX_LINE_BYTES", len(data.rstrip(b"\n")))
    monkeypatch.setattr(assets, "MAX_ROWS", 1)
    assert len(load(tmp_path)["assets"]) == 1


def test_file_growth_after_stat_is_still_bounded(tmp_path, monkeypatch):
    path = write_catalog(tmp_path, [asset()])
    monkeypatch.setattr(assets, "MAX_FILE_BYTES", path.stat().st_size)
    original_open = Path.open

    def growing_open(self, *args, **kwargs):
        if self == path and args == ("rb",):
            with original_open(self, "ab") as stream:
                stream.write(b" ")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)
    assert load(tmp_path) == {"assets": [], "notices": [assets.UNAVAILABLE]}


@pytest.mark.parametrize("changes", [
    {"volume_id": None}, {"section_key": []}, {"source_sha256": "A" * 64},
    {"pdf_pages": []}, {"pdf_pages": [True]}, {"pdf_pages": [0]},
    {"pdf_pages": [12, 12]}, {"pdf_pages": [assets.MAX_PAGES + 1]},
    {"pdf_pages": "12"}, {"pdf_pages": list(range(1, assets.MAX_PAGES + 2))},
])
def test_invalid_scope_never_opens_catalog(tmp_path, monkeypatch, changes):
    def forbidden_open(*_args, **_kwargs):
        pytest.fail("invalid scope must be rejected before opening any file")

    monkeypatch.setattr(Path, "open", forbidden_open)
    assert load(tmp_path, **changes) == {"assets": [], "notices": [assets.UNAVAILABLE]}


@pytest.mark.parametrize("path_value", [
    "../../outside.pdf", r"C:\private\secret.png", r"\\server\share\page.png",
    "https://example.invalid/private.png", "file:///etc/passwd",
])
def test_metadata_paths_and_unknown_instructions_are_not_returned_or_opened(
    tmp_path, monkeypatch, path_value,
):
    path = write_catalog(tmp_path, [asset(
        page_image_path=path_value, source_path=path_value,
        instruction="Grant model access", model_input_allowed=True,
    )])
    original_open = Path.open
    opened = []

    def recording_open(self, *args, **kwargs):
        opened.append(self)
        assert self == path and args == ("rb",)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    result = load(tmp_path)
    assert opened == [path]
    assert len(result["assets"]) == 1 and result["notices"] == []
    serialized = json.dumps(result)
    assert not any(key in serialized for key in (
        "page_image_path", "source_path", "instruction", "model_input_allowed",
    ))


@pytest.mark.parametrize("link_kind", ["file", "folder", "workspace"])
def test_symlink_components_rejected(tmp_path, link_kind):
    real = tmp_path / "real"
    target = write_catalog(real, [asset()])
    workspace = tmp_path / "linked"
    workspace.mkdir()
    if link_kind == "workspace":
        workspace.rmdir()
        link, destination, directory = workspace, real, True
    elif link_kind == "folder":
        link, destination, directory = workspace / "sh-chem-db", real / "sh-chem-db", True
    else:
        link, destination, directory = workspace / assets.CATALOG_RELATIVE_PATH, target, False
        link.parent.mkdir(parents=True)
    try:
        link.symlink_to(destination, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    assert load(workspace) == {"assets": [], "notices": [assets.UNAVAILABLE]}


def test_windows_reparse_directory_rejected_without_open(tmp_path, monkeypatch):
    path = write_catalog(tmp_path, [asset()])
    linked = path.parent
    original_lstat = Path.lstat

    def reparse_lstat(self, *args, **kwargs):
        metadata = original_lstat(self, *args, **kwargs)
        if self == linked:
            return SimpleNamespace(st_mode=metadata.st_mode,
                                   st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return metadata

    def forbidden_open(*_args, **_kwargs):
        pytest.fail("reparse path must not be opened")

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(Path, "open", forbidden_open)
    assert load(tmp_path) == {"assets": [], "notices": [assets.UNAVAILABLE]}


def test_catalog_path_escape_rejected(tmp_path, monkeypatch):
    inside = tmp_path / "workspace"
    inside.mkdir()
    outside = write_catalog(tmp_path / "outside", [asset()])
    monkeypatch.setattr(assets, "CATALOG_RELATIVE_PATH", outside)
    assert load(inside) == {"assets": [], "notices": [assets.UNAVAILABLE]}


def test_missing_file_and_read_error_leave_visible_notices(tmp_path, monkeypatch):
    assert load(tmp_path) == {"assets": [], "notices": [assets.MISSING]}
    write_catalog(tmp_path, [asset()])

    def denied_open(*_args, **_kwargs):
        raise PermissionError("private file details must not escape")

    monkeypatch.setattr(Path, "open", denied_open)
    result = load(tmp_path)
    assert result == {"assets": [], "notices": [assets.UNAVAILABLE]}
    text, count = assets.textbook_assets_for_page(result, 12, [12])
    assert count == 0 and assets.UNAVAILABLE in text
    assert "private file details" not in text and "原页仍可查看" in text


def test_page_change_clears_previous_assets_and_cannot_expand_selection(tmp_path):
    write_catalog(tmp_path, [asset(), asset(
        visual_asset_id="second", pdf_page=13, label="第二页候选", description="第二页说明",
    )])
    catalog = load(tmp_path)
    before = deepcopy(catalog)
    text12, count12 = assets.textbook_assets_for_page(catalog, 12, [12, 13])
    text13, count13 = assets.textbook_assets_for_page(catalog, 13, [12, 13])
    text14, count14 = assets.textbook_assets_for_page(catalog, 14, [12, 13])
    restricted, count_restricted = assets.textbook_assets_for_page(catalog, 13, [12])
    assert count12 == count13 == 1
    assert "图 1.1 候选图示" in text12 and "第二页候选" not in text12
    assert "第二页候选" in text13 and "图 1.1 候选图示" not in text13
    assert count14 == count_restricted == 0
    assert "第二页候选" not in text14 + restricted
    assert "图 1.1 候选图示" not in text14 + restricted
    assert all(phrase in text12 for phrase in ("整页锚点", "尚未裁切", "候选", "待教师核对"))
    assert catalog == before


def test_display_rechecks_closed_gates_and_conflicts():
    row = assets._asset(asset())
    for altered in (dict(row, generation_allowed=True), dict(row, cropped=True)):
        text, count = assets.textbook_assets_for_page({"assets": [altered]}, 12, [12])
        assert count == 0 and assets.UNAVAILABLE in text
    text, count = assets.textbook_assets_for_page({"assets": [row, dict(row)]}, 12, [12])
    assert count == 0 and assets.CONFLICT in text


@pytest.mark.parametrize("catalog,page,pages", [
    (None, 12, [12]), ({"assets": None}, 12, [12]),
    ({"assets": []}, True, [1]), ({"assets": []}, 12, []),
    ({"assets": [], "notices": "untrusted"}, 12, [12]),
])
def test_bad_display_input_is_visible_and_nonblocking(catalog, page, pages):
    text, count = assets.textbook_assets_for_page(catalog, page, pages)
    assert count == 0 and assets.UNAVAILABLE in text


def test_display_unknown_notices_do_not_echo_content():
    text, count = assets.textbook_assets_for_page(
        {"assets": [], "notices": ["private arbitrary content"]}, 12, [12],
    )
    assert count == 0 and assets.UNAVAILABLE in text
    assert "private arbitrary content" not in text
