"""D09 source-replacement contracts, using source-bound synthetic readers only.

Run with an E: ``--basetemp``. No provider, Office automation, production
question bank, or personal source document is opened by these tests.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.desktop_facade import ThemeCard
from integrations.deeptutor_shchem_v1.desktop_independent_paper import IndependentPaperLibrary
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_drafts import DRAFT_ID
from integrations.deeptutor_shchem_v1.desktop_mixed_paper_service import MixedPaperError, MixedPaperService, _digest, _sha, _word_key
from integrations.deeptutor_shchem_v1.desktop_paper_history import PAPER_HISTORY_ID
from integrations.deeptutor_shchem_v1.desktop_question_replacements import QuestionReplacementService
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateError, DesktopStateStore, DraftConflictError
from integrations.deeptutor_shchem_v1.reader_cancellation import ReadCancelled, TaskCancellationEvent, read_cancel_scope
from mixed_pagination_test_support import paginate_and_read
from test_desktop_mixed_paper_service import setup as mixed_setup, _add_word, _bundle, _catalog  # noqa: F401
from test_desktop_visual_import_facade import desktop_paths as desktop_paths, _png  # noqa: F401
from test_independent_paper import use_builders
from test_mixed_paper_draft_undo import request
from test_personal_visual_questions import BATCH_ID, imported_visual_batch  # noqa: F401
from test_visual_mixed_paper_integration import _change_source


REJECTED = (DesktopStateError, MixedPaperError, ValueError)
GOAL = {"text": "保留共同材料，练习氧化还原判断", "query": "氧化还原", "knowledge": []}
BODY = "依据氧化还原共同材料说明电子转移。"
ANSWER_ONLY = "ONLY_ANSWER_OMEGA"
TITLE_ONLY = "ONLY_SCHOOL_TITLE_SIGMA"


def _source(scope, theme_id="T1"):
    identity = _digest({"scope": scope, "paper": "P1", "theme": theme_id})
    return {"key": identity, "scope": scope, "title_zh": "合成完整主题",
            "paper_title_zh": TITLE_ONLY, "source_zh": "合成来源",
            "source_identity_sha256": identity, "data_snapshot_id": _digest(scope)}


def _group(theme_id, *, text=BODY, knowledge="K01"):
    return {"theme": {"id": theme_id, "title": "合成完整主题"},
            "shared_context": {"materials": [{"text_zh": "氧化还原共同材料"}]},
            "shared_materials": [{"text_zh": "氧化还原共同材料", "content_blocks": [
                {"block_type": "paragraph", "text_zh": "氧化还原共同材料"}]}],
            "atomic_chain": [{"atomic_part_id": label, "K": [knowledge],
                              "primary_knowledge": knowledge,
                              "label_summary": {"status": "complete", "primary_K": knowledge},
                              "visible_summary_zh": text,
                              "question_blocks": [{"block_type": "paragraph", "text_zh": text}],
                              "question": {"text_zh": text},
                              "reference_answer": {"text_zh": ANSWER_ONLY}}
                             for label in ("A1", "A2")]}


def _formal_entry(scope, theme_id="T2"):
    source = _source(scope, theme_id)
    card = ThemeCard(key=source["key"], scope=scope, title_zh=source["title_zh"],
                     paper_title_zh=source["paper_title_zh"], source_zh=source["source_zh"],
                     atomic_total=2, atomic_matched=2, shared_context_zh=BODY,
                     page_zh="合成第1页", source_identity_sha256=source["source_identity_sha256"],
                     data_snapshot_id=source["data_snapshot_id"], preview_zh=BODY)
    return {"entry_id": scope + ":" + card.key, "key": card.key, "lane": scope,
            "payload": card, "source_locator": {name: source[name] for name in
                ("scope", "key", "source_identity_sha256", "data_snapshot_id")},
            "unit_kind": "theme", "title": card.title_zh, "excerpt": BODY}


def _word_entry(row):
    # D08 keeps a source locator and label metadata; original readers supply
    # the body, shared material and answer range during D09 preparation.
    payload = {key: deepcopy(value) for key, value in row.items() if key not in
               {"question_blocks", "context_blocks", "answer_blocks"}}
    return {"entry_id": "word_native:" + row["key"], "key": row["key"],
            "lane": "word_native", "payload": payload, "unit_kind": "word_question_segment",
            "source_locator": {key: row[key] for key in
                ("key", "revision", "batch_id", "source_id", "archive_source_id", "source_sha256")},
            "title": row["title"], "excerpt": "候选目录摘要不能代替原题核验"}


def _opened(context):
    session = use_builders(context.library.open(context.session.paper_id), context.mixed)
    editor = session.open_mixed_paper_draft_session()
    editor.read(session.paper_basket_projection())
    return session, editor


def _paper_records(context):
    value = context.store.snapshot()["drafts"]
    prefix = context.session.paper_id + ":"
    return {"meta": deepcopy(value[context.session.paper_id]),
            "draft": deepcopy(value[prefix + DRAFT_ID]),
            "history": deepcopy(value.get(prefix + PAPER_HISTORY_ID))}


def _private_files(context):
    return {path.relative_to(context.store.root).as_posix(): path.read_bytes()
            for path in context.store.root.rglob("*") if path.is_file()}


def _prepare(context, *, target=None, entry=None, goal=None):
    return context.replacements.prepare(context.session.paper_id, target or context.target,
        goal=deepcopy(GOAL if goal is None else goal),
        candidate_entry=deepcopy(context.candidate if entry is None else entry))


def _commit(context, plan):
    return context.replacements.commit(plan["plan_id"], plan["plan_sha256"])


@pytest.fixture
def replacement(mixed_setup):
    mixed, store, words, _, _ = mixed_setup
    catalogs = {scope: _catalog(scope) for scope in ("master", "wave1", "supplemental")}
    for catalog in catalogs.values():
        catalog["papers"][0]["paper"]["title"] = TITLE_ONLY
        catalog["papers"][0]["theme_groups"] = [_group("T1"), _group("T2"), _group("T3")]
    mixed.facade.paper_theme_catalog = lambda scope: deepcopy(catalogs[scope])
    mixed._core_context = lambda: {"theme_catalog_loader": mixed.facade.paper_theme_catalog,
                                  "detail_loader": None, "crop_loader": None}
    mixed.facade._reader_stop_event = Event()
    words.row["title"] = TITLE_ONLY
    words.row["question_blocks"][0]["text"] = BODY
    words.row["context_blocks"][0]["text"] = "氧化还原共同材料"
    words.row["answer_blocks"][0]["text"] = ANSWER_ONLY
    words.row["attributes"] = {"revision": "word-tags-r1", "primary_knowledge": "K01"}
    alternative = deepcopy(words.row)
    alternative.update(key="word-question-replacement", revision="word-replacement-r1",
                       origin_block_start=8, points=3.5, title="合成替换Word题")
    alternative["question_blocks"][0]["index"] = 8
    alternative["question_blocks"][0]["assets"] = []
    alternative["answer_blocks"][0]["index"] = 9
    rows = {words.row["key"]: words.row, alternative["key"]: alternative}
    attribute_catalog = {"knowledge_points": [{"id": "K01", "name": "合成氧化还原"}]}
    words._read_attribute_catalog = lambda: deepcopy(attribute_catalog)

    def resolve(selections):
        words.calls.append(deepcopy(selections))
        resolved = []
        for selection in selections:
            row = rows.get(selection["key"])
            if row is None or selection["revision"] != row["revision"]:
                raise MixedPaperError("合成Word来源版本已变化")
            resolved.append({**deepcopy(row), "points": selection.get("points", row["points"])})
        return resolved, {words.row["source_id"]: (SimpleNamespace(content=words.data),)}

    words._resolve = resolve
    store.add_many_to_basket([_source("master"), _source("supplemental")])
    word_key = _add_word(mixed, words)
    library = IndependentPaperLibrary(mixed.facade)
    session = use_builders(library.create(), mixed)
    editor = session.open_mixed_paper_draft_session()
    editor.read(session.paper_basket_projection())
    replacements = QuestionReplacementService(mixed.facade, mixed_service=mixed)
    return SimpleNamespace(mixed=mixed, store=store, words=words, rows=rows,
        catalogs=catalogs, attribute_catalog=attribute_catalog, library=library,
        session=session, editor=editor, replacements=replacements,
        target=_source("master")["key"], word_key=word_key,
        candidate=_formal_entry("wave1"), word_candidate=_word_entry(alternative))


def test_prepare_is_read_only_and_plan_contains_verified_complete_source(replacement):
    context = replacement
    before = context.store.path.read_bytes()
    before_files = _private_files(context)
    plan = _prepare(context)
    assert context.store.path.read_bytes() == before
    assert _private_files(context) == before_files
    assert {"plan_id", "plan_sha256", "paper_id", "target_key", "new_key", "paper_revision",
            "before_projection", "after_projection", "before_item", "after_item", "goal",
            "match_evidence"}.issubset(plan)
    assert plan["paper_id"] == context.session.paper_id
    assert plan["target_key"] == context.target and plan["new_key"] == context.candidate["key"]
    assert plan["before_item"]["kind"] == plan["after_item"]["kind"] == "core_theme"
    assert plan["after_item"]["source_ref"]["scope"] == "wave1"
    assert len(plan["after_item"]["content"]["atomic_chain"]) == 2
    assert plan["goal"] == GOAL and plan["match_evidence"]
    assert len(plan["plan_sha256"]) == 64


def test_complete_source_commit_preserves_order_settings_other_papers_and_public_basket(replacement):
    context = replacement
    old = deepcopy(context.editor.current.record["payload"])
    old["order"].reverse()
    old["settings_ui"].update(title="教师确认标题", subtitle="旧稿保留", duration_minutes=75)
    old["settings"][context.target].update(score_per_atomic=8, answer_space_lines=6,
        atomic_settings={"A1": {"score": 11, "answer_space_lines": 3}})
    old["settings"][context.word_key]["points"] = 7.5
    context.editor.save(old, action="换源前普通配分", selected=context.target)
    second = context.library.create()
    other_before = context.store.draft_snapshot(second.paper_id)
    other_draft = context.store.draft_snapshot(second.paper_id + ":" + DRAFT_ID)
    basket = context.store.basket_snapshot()
    plan = _prepare(context)
    result = _commit(context, plan)
    assert result["status"] == "saved" and result["preview_invalidated"] is True
    assert result["version_id"] and result["revision"] != plan["paper_revision"]
    reopened, editor = _opened(context)
    payload = editor.current.record["payload"]
    expected_order = [plan["new_key"] if key == context.target else key for key in old["order"]]
    assert payload["order"] == expected_order and payload["excluded"] == old["excluded"]
    assert payload["settings_ui"] == old["settings_ui"]
    assert payload["settings"][context.word_key] == old["settings"][context.word_key]
    assert payload["settings"][plan["new_key"]] == plan["after_item"]["settings"]
    assert payload["settings"][plan["new_key"]].get("atomic_settings", {}) == {}
    assert context.target not in payload["settings"]
    assert [row["key"] for row in reopened.basket()] == [plan["new_key"] if row["key"] == context.target
        else row["key"] for row in context.session.state_store.record["items"]]
    assert context.store.basket_snapshot() == basket
    assert context.store.draft_snapshot(second.paper_id) == other_before
    assert context.store.draft_snapshot(second.paper_id + ":" + DRAFT_ID) == other_draft


def test_word_replacement_is_complete_segment_with_original_shared_material_and_fresh_points(replacement):
    context = replacement
    plan = _prepare(context, entry=context.word_candidate)
    assert plan["after_item"]["kind"] == "word_question"
    assert plan["new_key"] == _word_key(context.word_candidate["key"])
    assert plan["after_item"]["content"]["context_blocks"][0]["text"] == "氧化还原共同材料"
    assert plan["after_item"]["content"]["question_blocks"][0]["text"] == BODY
    assert plan["after_item"]["content"]["answer_blocks"][0]["text"] == ANSWER_ONLY
    _commit(context, plan)
    _, editor = _opened(context)
    assert editor.current.record["payload"]["settings"][plan["new_key"]] == {"points": 3.5}


@pytest.mark.parametrize("source", ["self", "active", "excluded"])
def test_self_and_current_active_or_excluded_source_cannot_be_replaced_again(replacement, source):
    context = replacement
    entry = _formal_entry("master", "T1") if source == "self" else _formal_entry("supplemental", "T1")
    if source == "excluded":
        payload = deepcopy(context.editor.current.record["payload"])
        key = entry["key"]
        payload["order"].remove(key)
        payload["excluded"].append(key)
        context.editor.save(payload)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context, entry=entry)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("excluded", [False, True])
def test_word_source_already_active_or_excluded_is_a_duplicate(replacement, excluded):
    context = replacement
    if excluded:
        payload = deepcopy(context.editor.current.record["payload"])
        payload["order"].remove(context.word_key)
        payload["excluded"].append(context.word_key)
        context.editor.save(payload)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context, entry=_word_entry(context.words.row))
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("goal", [
    {"text": "", "query": "氧化还原", "knowledge": []},
    {"text": "仅有教师目标", "query": "", "knowledge": []},
    {"text": "错误的知识依据", "query": "", "knowledge": ["K99"]},
    {"text": "答案不能充当题面", "query": ANSWER_ONLY, "knowledge": []},
    {"text": "校名不能充当题面", "query": TITLE_ONLY, "knowledge": []},
    {"text": "未命中真实题面", "query": "NEVER_IN_ORIGINAL", "knowledge": []},
])
def test_goal_must_match_real_question_shared_material_or_knowledge(replacement, goal):
    before = replacement.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(replacement, goal=goal)
    assert replacement.store.path.read_bytes() == before


def test_knowledge_goal_can_match_without_query_and_title_similarity_does_not_merge_sources(replacement):
    goal = {"text": "练习已经标注的知识", "query": "", "knowledge": ["K01"]}
    plan = _prepare(replacement, goal=goal)
    assert plan["new_key"] != plan["target_key"] and plan["match_evidence"]
    assert plan["before_item"]["title_zh"] == plan["after_item"]["title_zh"]


def test_candidate_excerpt_is_not_a_substitute_for_question_body_evidence(replacement):
    context = replacement
    marker = "ONLY_PICKER_EXCERPT_DELTA"
    entry = deepcopy(context.candidate)
    entry["excerpt"] = marker
    entry["payload"] = replace(entry["payload"], preview_zh=marker, shared_context_zh=marker)
    goal = {"text": "只采信重新读取的原题", "query": marker, "knowledge": []}
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context, entry=entry, goal=goal)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("field", ["key", "unit_kind", "source_locator"])
def test_picker_identity_or_unit_cannot_disagree_with_verified_source(replacement, field):
    entry = deepcopy(replacement.candidate)
    if field == "source_locator":
        entry[field]["source_identity_sha256"] = "0" * 64
    else:
        entry[field] = "foreign-source" if field == "key" else "atomic_part"
    before = replacement.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(replacement, entry=entry)
    assert replacement.store.path.read_bytes() == before


def test_bad_plan_hash_and_dismissed_plan_do_not_write(replacement):
    plan = _prepare(replacement)
    before = replacement.store.path.read_bytes()
    with pytest.raises(REJECTED):
        replacement.replacements.commit(plan["plan_id"], "0" * 64)
    assert replacement.store.path.read_bytes() == before
    # Dismissal consists of never committing the proposal. Reopening the
    # service must not create a durable version or alter the saved sources.
    fresh = QuestionReplacementService(replacement.mixed.facade, mixed_service=replacement.mixed)
    assert fresh.versions(replacement.session.paper_id)["versions"] == []
    assert replacement.store.path.read_bytes() == before


@pytest.mark.parametrize("change", ["draft", "draft_aba", "meta", "meta_aba"])
def test_commit_cas_checks_draft_and_saved_source_revision_without_overwrite(replacement, change):
    context = replacement
    plan = _prepare(context)
    key = context.session.paper_id + ":" + DRAFT_ID if change.startswith("draft") else context.session.paper_id
    other = DesktopStateStore(context.store.root)
    original = other.draft_snapshot(key).record
    changed = deepcopy(original)
    if change.startswith("draft"):
        changed["payload"]["settings_ui"]["title"] = "另一窗口编排"
    else:
        changed["created_at"] = "另一窗口来源版本"
    other.save_draft(key, changed)
    if change.endswith("aba"):
        other.save_draft(key, original)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert context.store.path.read_bytes() == before


def test_unrelated_public_basket_and_other_draft_changes_do_not_block_or_get_overwritten(replacement):
    context = replacement
    plan = _prepare(context)
    context.store.clear_basket()
    context.store.add_to_basket({"key": "public-after-prepare", "title_zh": "无关公共选题"})
    context.store.save_draft("another-lesson", {"teacher": "无关课时"})
    basket = context.store.basket_snapshot()
    _commit(context, plan)
    assert context.store.basket_snapshot() == basket
    assert context.store.draft_snapshot("another-lesson").record == {"teacher": "无关课时"}


@pytest.mark.parametrize("change", ["draft", "meta"])
def test_commit_final_transaction_checks_fresh_versions_and_keeps_interleaved_write(replacement, monkeypatch, change):
    context = replacement
    plan = _prepare(context)
    original_update = context.store._update
    other = DesktopStateStore(context.store.root)
    interleaved_bytes = []

    def interleave(operation, **kwargs):
        key = context.session.paper_id + ":" + DRAFT_ID if change == "draft" else context.session.paper_id
        changed = other.draft_snapshot(key).record
        if change == "draft":
            changed["payload"]["settings_ui"]["title"] = "在最终写入前变更"
        else:
            changed["created_at"] = "在最终来源写入前变更"
        other.save_draft(key, changed)
        interleaved_bytes.append(other.path.read_bytes())
        return original_update(operation, **kwargs)

    monkeypatch.setattr(context.store, "_update", interleave)
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert len(interleaved_bytes) == 1
    assert context.store.path.read_bytes() == interleaved_bytes[0]


@pytest.mark.parametrize("origin", ["current", "candidate"])
@pytest.mark.parametrize("kind", ["formal", "word"])
def test_source_only_change_at_final_transaction_is_rechecked_before_write(replacement, monkeypatch, origin, kind):
    context = replacement
    plan = _prepare(context, entry=context.word_candidate if kind == "word" else None)
    original_update, calls = context.store._update, []
    before = context.store.path.read_bytes()

    def change_source(operation, **kwargs):
        calls.append(True)
        if kind == "word":
            row = context.rows[context.words.row["key"] if origin == "current" else context.word_candidate["key"]]
            row["question_blocks"].append({"index": 16, "text": "最终提交前的范围变更", "assets": []})
        else:
            scope = "master" if origin == "current" else "wave1"
            group = context.catalogs[scope]["papers"][0]["theme_groups"][0 if origin == "current" else 1]
            group["atomic_chain"][0]["question_blocks"][0]["text_zh"] = "最终提交前真实题面变更"
        return original_update(operation, **kwargs)

    monkeypatch.setattr(context.store, "_update", change_source)
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert calls == [True] and context.store.path.read_bytes() == before


@pytest.mark.parametrize("origin", ["current", "candidate"])
@pytest.mark.parametrize("change", ["source", "range", "tags", "taxonomy"])
def test_changed_word_source_range_or_label_basis_blocks_commit_without_writing(replacement, origin, change):
    context = replacement
    plan = _prepare(context, entry=context.word_candidate)
    row = context.rows[context.words.row["key"] if origin == "current" else context.word_candidate["key"]]
    if change == "source":
        row["source_sha256"] = "d" * 64
    elif change == "range":
        row["question_blocks"].append({"index": 15, "text": "合成新增题目范围", "assets": []})
    elif change == "tags":
        row["attributes"] = {"revision": "changed-labels", "primary_knowledge": "K99"}
    else:
        context.attribute_catalog["knowledge_points"].append({"id": "K99", "name": "变更词表"})
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("phase", ["prepare", "commit"])
def test_cancelled_work_does_not_publish_sources_or_versions(replacement, phase):
    context = replacement
    plan = _prepare(context) if phase == "commit" else None
    event = Event()
    event.set()
    before = context.store.path.read_bytes()
    with read_cancel_scope(event, check_boundaries=False):
        with pytest.raises(ReadCancelled):
            _commit(context, plan) if plan is not None else _prepare(context)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("when", ["before", "after"])
def test_uncertain_commit_is_not_retried_and_reopen_reads_actual_disk(replacement, monkeypatch, when):
    context = replacement
    plan = _prepare(context)
    original, calls = context.store._write_unlocked, []

    def uncertain(value, **kwargs):
        calls.append(True)
        if when == "after":
            original(value, **kwargs)
        raise OSError("synthetic uncertain replacement write")

    monkeypatch.setattr(context.store, "_write_unlocked", uncertain)
    with pytest.raises(REJECTED):
        _commit(context, plan)
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert len(calls) == 1
    monkeypatch.setattr(context.store, "_write_unlocked", original)
    reopened, editor = _opened(context)
    keys = editor.current.record["payload"]["order"]
    assert (plan["new_key"] in keys) == (when == "after")
    assert (context.target in keys) == (when == "before")
    fresh = QuestionReplacementService(context.mixed.facade, mixed_service=context.mixed)
    assert len(fresh.versions(reopened.paper_id)["versions"]) == (1 if when == "after" else 0)


def test_source_version_restore_survives_reopen_and_recovers_complete_draft_and_ordinary_undo(replacement):
    context = replacement
    initial = deepcopy(context.editor.current.record)
    payload = deepcopy(initial["payload"])
    payload["order"].reverse()
    removed = context.word_key
    payload["order"].remove(removed)
    payload["excluded"].append(removed)
    payload["settings_ui"].update(title="换源前完整旧稿", duration_minutes=85, show_question_scores=True)
    payload["settings"][context.target]["atomic_settings"] = {"A1": {"score": 9, "answer_space_lines": 5}}
    context.editor.save(payload, action="需要保留的普通撤销", selected=context.target)
    old_records = _paper_records(context)
    plan = _prepare(context)
    committed = _commit(context, plan)
    fresh = QuestionReplacementService(context.mixed.facade, mixed_service=context.mixed)
    history = fresh.versions(context.session.paper_id)
    assert committed["version_id"] in [row["version_id"] for row in history["versions"]]
    restored = fresh.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"])
    assert restored["status"] == "saved" and restored["preview_invalidated"] is True
    actual_records = _paper_records(context)
    assert actual_records["meta"] == old_records["meta"]
    assert actual_records["draft"] == old_records["draft"]
    # The authenticated envelope must be rebound to the new revision. Its
    # actual reversible states remain exact, rather than copying a stale MAC.
    assert actual_records["history"]["undo"] == old_records["history"]["undo"]
    assert actual_records["history"]["redo"] == old_records["history"]["redo"]
    _, editor = _opened(context)
    assert editor.undo_count == 1
    editor.undo()
    assert editor.current.record == initial


@pytest.mark.parametrize("origin", ["current", "candidate"])
@pytest.mark.parametrize("change", ["body", "tags", "snapshot"])
def test_changed_formal_theme_content_labels_or_snapshot_blocks_commit(replacement, origin, change):
    context = replacement
    plan = _prepare(context)
    scope = "master" if origin == "current" else "wave1"
    catalog = context.catalogs[scope]
    theme = catalog["papers"][0]["theme_groups"][0 if origin == "current" else 1]
    if change == "body":
        theme["atomic_chain"][0]["question_blocks"][0]["text_zh"] = "已经变更的原题"
    elif change == "tags":
        theme["atomic_chain"][0]["K"] = ["K99"]
    else:
        catalog["data_snapshot_id"] = "e" * 64
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("flag", ["selection_ready", "export_ready"])
def test_unready_word_range_cannot_create_replacement_plan(replacement, flag):
    context = replacement
    context.rows[context.word_candidate["key"]][flag] = False
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context, entry=context.word_candidate)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("alias_kind", ["complete", "partial"])
def test_explicit_alias_duplicate_requires_the_complete_same_theme(replacement, alias_kind):
    context = replacement
    master = context.catalogs["master"]["papers"][0]["theme_groups"][0]
    master["atomic_chain"] = [{"atomic_part_id": "MASTER-PARENT", "question_blocks": [
        {"text_zh": BODY}], "alias_units": [
            {"atomic_part_id": "A1", "source_scope": "wave1"},
            {"atomic_part_id": "A2", "source_scope": "wave1"}]}]
    if alias_kind == "partial":
        candidate = context.catalogs["wave1"]["papers"][0]["theme_groups"][1]
        candidate["atomic_chain"][1]["atomic_part_id"] = "NEW-NONALIAS-ATOM"
    # Add explicit source evidence before creating the source-bound paper;
    # changing evidence on an already saved paper correctly makes it stale.
    context.session = use_builders(context.library.create(), context.mixed)
    context.editor = context.session.open_mixed_paper_draft_session()
    context.editor.read(context.session.paper_basket_projection())
    before = context.store.path.read_bytes()
    if alias_kind == "complete":
        with pytest.raises(REJECTED):
            _prepare(context, target=context.word_key)
    else:
        plan = _prepare(context, target=context.word_key)
        assert plan["new_key"] == context.candidate["key"]
    assert context.store.path.read_bytes() == before


def test_old_approved_preview_is_invalid_after_replacement_and_restore(replacement):
    context = replacement
    preview = paginate_and_read(context.session.service,
        context.session.create_paper_preview(request(context.session.service, context.editor)))
    context.session.approve_paper_preview(preview.preview_id, preview.preview_hash)
    plan = _prepare(context)
    committed = _commit(context, plan)
    with pytest.raises(REJECTED):
        context.session.export_paper_preview(preview.preview_id, preview.preview_hash)
    fresh = QuestionReplacementService(context.mixed.facade, mixed_service=context.mixed)
    history = fresh.versions(context.session.paper_id)
    fresh.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"])
    restored, _ = _opened(context)
    with pytest.raises(REJECTED):
        restored.export_paper_preview(preview.preview_id, preview.preview_hash)


def test_restore_uses_current_paper_revision_and_preserves_other_state(replacement):
    context = replacement
    committed = _commit(context, _prepare(context))
    history = context.replacements.versions(context.session.paper_id)
    context.store.save_draft("unrelated-restore", {"teacher": "keep"})
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision="stale")
    assert context.store.path.read_bytes() == before
    context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"])
    assert context.store.draft_snapshot("unrelated-restore").record == {"teacher": "keep"}


def test_version_restore_round_trip_recovers_the_new_source_and_its_ordinary_undo(replacement):
    context = replacement
    plan = _prepare(context)
    committed = _commit(context, plan)
    _, editor = _opened(context)
    replacement_base = deepcopy(editor.current.record)
    payload = deepcopy(editor.current.record["payload"])
    payload["settings_ui"]["title"] = "换源后的普通编排"
    editor.save(payload, action="换源后普通编辑", selected=plan["new_key"])
    replaced_records = _paper_records(context)
    first = context.replacements.versions(context.session.paper_id)
    context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=first["revision"])
    fresh = QuestionReplacementService(context.mixed.facade, mixed_service=context.mixed)
    second = fresh.versions(context.session.paper_id)
    new_versions = [row["version_id"] for row in second["versions"]
                    if row["version_id"] != committed["version_id"]]
    assert len(new_versions) == 1
    fresh.restore(context.session.paper_id, new_versions[0], expected_revision=second["revision"])
    actual = _paper_records(context)
    assert actual["meta"] == replaced_records["meta"] and actual["draft"] == replaced_records["draft"]
    assert actual["history"]["undo"] == replaced_records["history"]["undo"]
    _, reopened_editor = _opened(context)
    assert reopened_editor.undo_count == 1
    reopened_editor.undo()
    assert reopened_editor.current.record == replacement_base


def test_foreign_paper_cannot_restore_another_papers_source_version(replacement):
    context = replacement
    foreign = context.library.create()
    committed = _commit(context, _prepare(context))
    history = context.replacements.versions(foreign.paper_id)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        context.replacements.restore(foreign.paper_id, committed["version_id"], expected_revision=history["revision"])
    assert context.store.path.read_bytes() == before


def test_restore_reverifies_the_archived_source_instead_of_reusing_saved_content(replacement):
    context = replacement
    committed = _commit(context, _prepare(context))
    history = context.replacements.versions(context.session.paper_id)
    old_theme = context.catalogs["master"]["papers"][0]["theme_groups"][0]
    old_theme["atomic_chain"][0]["question_blocks"][0]["text_zh"] = "被替换掉的旧来源已变化"
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"])
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("when", ["before", "after"])
def test_uncertain_restore_is_not_retried(replacement, monkeypatch, when):
    context = replacement
    committed = _commit(context, _prepare(context))
    history = context.replacements.versions(context.session.paper_id)
    original, calls = context.store._write_unlocked, []

    def uncertain(value, **kwargs):
        calls.append(True)
        if when == "after":
            original(value, **kwargs)
        raise OSError("synthetic uncertain version restore")

    monkeypatch.setattr(context.store, "_write_unlocked", uncertain)
    for _ in range(2):
        with pytest.raises(REJECTED):
            context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"])
    assert len(calls) == 1


def _visual_entry(row):
    payload = {key: deepcopy(value) for key, value in row.items() if key not in
               {"question_text", "shared_text", "answer_text", "images"}}
    return {"entry_id": "visual_native:" + row["key"], "key": row["key"],
            "lane": "visual_native", "payload": payload, "unit_kind": "visual_printed_question",
            "source_locator": {key: row[key] for key in
                ("key", "revision", "batch_id", "candidate_sha256", "theme_key")},
            "title": row["title"], "excerpt": "目录摘要不能替代完整图片主题"}


@pytest.fixture
def visual_replacement(replacement, imported_visual_batch, monkeypatch):
    visual = imported_visual_batch
    facade = visual["facade"]
    monkeypatch.setattr(facade, "_word_questions", lambda: replacement.words)
    monkeypatch.setattr(facade, "paper_theme_catalog", replacement.mixed.facade.paper_theme_catalog)
    monkeypatch.setattr(facade, "_paper_catalog_snapshot_id", lambda catalog: catalog["data_snapshot_id"])
    mixed = MixedPaperService(facade)
    for name in ("_core_bundle_builder", "_docx_builder", "_word_validator", "_core_context"):
        setattr(mixed, name, getattr(replacement.mixed, name))
    store = facade.state_store
    store.clear_basket()
    store.add_many_to_basket([_source("master"), _source("supplemental")])
    _add_word(mixed, replacement.words)
    library = IndependentPaperLibrary(facade)
    session = use_builders(library.create(), mixed)
    editor = session.open_mixed_paper_draft_session()
    editor.read(session.paper_basket_projection())
    context = SimpleNamespace(**vars(replacement))
    context.mixed, context.store, context.library = mixed, store, library
    context.session, context.editor = session, editor
    context.replacements = QuestionReplacementService(facade, mixed_service=mixed)
    context.visual = visual
    context.visual_rows = visual["service"].catalog(BATCH_ID)["items"]
    # Keep the actual local CAS/crop/attribute readers; only synthetic formal
    # and Word ports are injected into this imported-source facade.
    context.candidate = _visual_entry(context.visual_rows[0])
    return context


def test_visual_printed_candidate_expands_complete_theme_and_second_printed_row_is_same_source(visual_replacement):
    context = visual_replacement
    goal = {"text": "保留完整电极主题及公共材料", "query": "铜电极", "knowledge": []}
    plan = _prepare(context, goal=goal)
    after = plan["after_item"]
    assert after["kind"] == "personal_visual_theme"
    assert len(after["content"]["theme"]["printed_questions"]) == 2
    assert after["content"]["theme"]["shared_materials"]
    assert {row["key"] for row in after["source_ref"]["selections"]} == {
        row["key"] for row in context.visual_rows}
    assert after["settings"] == {"use_source_scores": True}
    basket = context.store.basket_snapshot()
    _commit(context, plan)
    reopened, _ = _opened(context)
    visual_ref = next(row for row in reopened.basket() if row["key"] == plan["new_key"])
    assert len(visual_ref["visual_selections"]) == 2
    assert context.store.basket_snapshot() == basket
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context, target=context.word_key, entry=_visual_entry(context.visual_rows[1]), goal=goal)
    assert context.store.path.read_bytes() == before


@pytest.mark.parametrize("change", ["crop", "source", "cas", "attributes"])
def test_actual_visual_source_dependencies_change_blocks_commit(visual_replacement, change):
    context = visual_replacement
    goal = {"text": "保留完整电极主题", "query": "铜电极", "knowledge": []}
    plan = _prepare(context, goal=goal)
    if change == "attributes":
        from test_personal_visual_attributes import _updates
        row = context.visual_rows[0]
        service = context.visual["service"]
        options = service.attribute_options(BATCH_ID, row["key"], row["revision"])
        service.save_attributes(BATCH_ID, row["key"], row["revision"],
            _updates(options, teacher_note="合成标签依据已变化"),
            expected_attribute_revision=options["attributes"]["revision"])
    else:
        _change_source(context.visual, change)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert context.store.path.read_bytes() == before


def _signed_source_version(context, version_id):
    matches = []
    for identity, record in context.store.snapshot()["drafts"].items():
        if not isinstance(record, dict) or "source-version" not in str(record.get("scope", "")):
            continue
        for step in record.get("undo", []) + record.get("redo", []):
            state = step.get("state")
            if isinstance(state, dict) and state.get("version_id") == version_id:
                matches.append((identity, deepcopy(record), deepcopy(state)))
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("tamper", ["signature", "captured_draft"])
def test_extra_source_version_tamper_is_rejected_without_repair_or_overwrite(replacement, tamper):
    context = replacement
    committed = _commit(context, _prepare(context))
    history = context.replacements.versions(context.session.paper_id)
    identity, record, _ = _signed_source_version(context, committed["version_id"])
    if tamper == "signature":
        record["signature"] = "0" * 64
    else:
        record["undo"][0]["state"]["draft"]["payload"]["settings_ui"]["title"] = "篡改的来源版本旧稿"
    context.store.save_draft(identity, record)
    before = context.store.path.read_bytes()
    for operation in (
        lambda: context.replacements.versions(context.session.paper_id),
        lambda: _prepare(context, target=context.word_key, entry=_formal_entry("master", "T3")),
        lambda: context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"]),
    ):
        with pytest.raises(REJECTED):
            operation()
        assert context.store.path.read_bytes() == before
    assert context.store.draft_snapshot(identity).record == record


@pytest.mark.parametrize("change_profile_label", [False, True])
def test_extra_copied_source_version_cannot_authorize_restore_in_foreign_profile(replacement, tmp_path, change_profile_label):
    context = replacement
    committed = _commit(context, _prepare(context))
    identity, _, _ = _signed_source_version(context, committed["version_id"])
    foreign = DesktopStateStore(tmp_path / "foreign-profile")
    foreign.root.mkdir(parents=True)
    copied = context.store.snapshot()
    if change_profile_label:
        # Changing the visible profile label cannot create a valid signature.
        copied["drafts"][identity]["profile"] = os.path.normcase(str(foreign.root.resolve()))
    foreign.path.write_text(json.dumps(copied, ensure_ascii=False), encoding="utf-8")
    facade = SimpleNamespace(**vars(context.mixed.facade))
    facade.state_store = foreign
    facade.paths = SimpleNamespace(state_root=foreign.root)
    fresh = QuestionReplacementService(facade, mixed_service=MixedPaperService(facade))
    before = foreign.path.read_bytes()
    files_before = _private_files(SimpleNamespace(store=foreign))
    with pytest.raises(REJECTED):
        fresh.versions(context.session.paper_id)
    assert foreign.path.read_bytes() == before
    assert _private_files(SimpleNamespace(store=foreign)) == files_before
    assert foreign.draft_snapshot(identity).record == copied["drafts"][identity]


@pytest.mark.parametrize("aba", [False, True])
def test_extra_version_list_confirmation_expires_after_ordinary_edit_or_aba(replacement, aba):
    context = replacement
    committed = _commit(context, _prepare(context))
    history = context.replacements.versions(context.session.paper_id)
    _, editor = _opened(context)
    original = deepcopy(editor.current.record["payload"])
    changed = deepcopy(original)
    changed["settings_ui"]["title"] = "查看版本列表之后又编排"
    editor.save(changed)
    if aba:
        editor.save(original)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=history["revision"])
    assert context.store.path.read_bytes() == before
    assert context.replacements.versions(context.session.paper_id)["revision"] != history["revision"]


def test_extra_late_task_stop_waits_for_publication_and_cannot_relabel_success(replacement, monkeypatch):
    context = replacement
    plan = _prepare(context)
    signal, durable, attempted = TaskCancellationEvent(), Event(), Event()
    late_cancel, writes = [], []
    original = context.store._write_unlocked

    def cancel_after_durable_write():
        assert durable.wait(10)
        attempted.set()
        late_cancel.append(signal.request_cancel())

    def write(value, **kwargs):
        original(value, **kwargs)
        writes.append(True)
        durable.set()
        assert attempted.wait(10)

    monkeypatch.setattr(context.store, "_write_unlocked", write)
    worker = Thread(target=cancel_after_durable_write, daemon=True)
    worker.start()
    with read_cancel_scope(signal):
        result = _commit(context, plan)
        worker.join(10)
        assert not worker.is_alive()
        assert late_cancel == [False] and not signal.is_set()
    assert result["status"] == "saved" and writes == [True]
    _, editor = _opened(context)
    assert plan["new_key"] in editor.current.record["payload"]["order"]


def test_extra_damaged_editing_history_remains_signed_evidence_but_never_authorizes_undo(replacement):
    context = replacement
    payload = deepcopy(context.editor.current.record["payload"])
    payload["settings_ui"]["title"] = "包含损坏撤销证据的旧稿"
    context.editor.save(payload)
    history_id = context.session.paper_id + ":" + PAPER_HISTORY_ID
    damaged = context.store.draft_snapshot(history_id).record
    damaged["signature"] = "0" * 64
    context.store.save_draft(history_id, damaged)
    old = _paper_records(context)
    committed = _commit(context, _prepare(context))
    _, _, archived = _signed_source_version(context, committed["version_id"])
    assert archived["editing_history_evidence"] == damaged
    assert archived["editing_history_verified"] is False
    assert archived["editing_history"] == {"undo": [], "redo": []}
    versions = context.replacements.versions(context.session.paper_id)
    context.replacements.restore(context.session.paper_id, committed["version_id"], expected_revision=versions["revision"])
    actual = _paper_records(context)
    assert actual["meta"] == old["meta"] and actual["draft"] == old["draft"]
    _, editor = _opened(context)
    assert editor.undo_count == 0 and editor.redo_count == 0
    with pytest.raises(DesktopStateError):
        editor.undo()
    _, _, retained = _signed_source_version(context, committed["version_id"])
    assert retained["editing_history_evidence"] == damaged


@pytest.fixture
def real_word_replacement(replacement, desktop_paths, tmp_path, monkeypatch):
    from test_word_question_images import _fixture
    facade, choices, provider = _fixture(desktop_paths, tmp_path, count=2)
    monkeypatch.setattr(facade, "paper_theme_catalog", replacement.mixed.facade.paper_theme_catalog)
    monkeypatch.setattr(facade, "_paper_catalog_snapshot_id", lambda catalog: catalog["data_snapshot_id"])
    mixed = MixedPaperService(facade, core_bundle_builder=replacement.mixed._core_bundle_builder)
    mixed._core_context = replacement.mixed._core_context
    facade.state_store.add_to_basket(_source("master"))
    mixed.add_word_questions([choices[0]])
    library = IndependentPaperLibrary(facade)
    session = library.create()
    editor = session.open_mixed_paper_draft_session()
    editor.read(session.paper_basket_projection())
    words = facade._word_questions()
    raw, _ = words._resolve(choices)
    context = SimpleNamespace(mixed=mixed, store=facade.state_store, words=words,
        library=library, session=session, editor=editor, provider=provider,
        target=_source("master")["key"], word_key=_word_key(choices[0]["key"]),
        candidate=_word_entry(raw[1]), choices=choices, raw=raw,
        replacements=QuestionReplacementService(facade, mixed_service=mixed))
    yield context
    facade.shutdown()


@pytest.mark.parametrize("problem", ["cross_question_answer_pixels", "missing_shape_marker"])
def test_extra_native_word_material_roles_or_missing_shape_block_before_plan(real_word_replacement, monkeypatch, problem):
    context = real_word_replacement
    original = context.words._resolve
    first_picture = deepcopy(context.raw[0]["question_blocks"][1]["assets"])

    def malformed(values, **kwargs):
        rows, inventory = original(values, **kwargs)
        for row in rows:
            if row["key"] == context.choices[1]["key"]:
                if problem == "cross_question_answer_pixels":
                    row["answer_blocks"][0]["assets"] = deepcopy(first_picture)
                else:
                    block = row["question_blocks"][1]
                    block["assets"] = []
                    block["text"] = "【待查看原文：嵌入对象或旧公式】"
        return rows, inventory

    monkeypatch.setattr(context.words, "_resolve", malformed)
    before = context.store.path.read_bytes()
    with pytest.raises((MixedPaperError, ValueError)):
        _prepare(context, goal={"text": "保留原图与题答边界", "query": "观察图示", "knowledge": []})
    assert context.store.path.read_bytes() == before
    assert context.provider.borrow_calls == 0


@pytest.mark.parametrize("problem", ["same_pixels", "quality_lock"])
def test_extra_actual_visual_material_roles_and_quality_lock_block_before_plan(visual_replacement, monkeypatch, problem):
    from integrations.deeptutor_shchem_v1.desktop_personal_visual_theme_export import PersonalVisualThemeExportService
    context = visual_replacement
    personal = context.visual["service"]
    exporter = PersonalVisualThemeExportService(personal)
    monkeypatch.setattr(context.mixed, "_visual_themes", lambda: exporter)
    if problem == "same_pixels":
        original, first = personal._image_for_row, []

        def same(*args, **kwargs):
            payload = original(*args, **kwargs)
            if not first:
                first.append(payload)
            return first[0]

        monkeypatch.setattr(personal, "_image_for_row", same)
    else:
        original_rows = personal._rows

        def locked(*args, **kwargs):
            rows = original_rows(*args, **kwargs)
            rows[0]["selection_ready"] = False
            return rows

        monkeypatch.setattr(personal, "_rows", locked)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context, goal={"text": "保留完整真实题答图片", "query": "铜电极", "knowledge": []})
    assert context.store.path.read_bytes() == before


def test_extra_formal_builder_blocked_quality_report_prevents_replacement_plan(replacement, monkeypatch):
    context = replacement

    def blocked(payload, **kwargs):
        bundle = _bundle(payload, **kwargs)
        bundle["preflight_report"] = {"status": "blocked", "blocker_count": 1,
            "checks": [{"status": "blocked", "code": "question_crop_quality_blocked"}]}
        return bundle

    monkeypatch.setattr(context.mixed, "_core_bundle_builder", blocked)
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _prepare(context)
    assert context.store.path.read_bytes() == before


def test_extra_formal_original_image_bytes_change_invalidates_material_fingerprint(replacement, monkeypatch):
    context = replacement
    source_image = [_png("blue")]

    def image_bundle(payload, **kwargs):
        bundle = _bundle(payload, **kwargs)
        root = kwargs["asset_root"]
        root.mkdir(parents=True, exist_ok=True)
        reference = _sha(source_image[0]) + ".png"
        (root / reference).write_bytes(source_image[0])
        for audience in ("student", "teacher"):
            block = bundle[audience + "_plan"]["visible"]["theme_sections"][0]["printed_questions"][0]["atomic_parts"][0]["question_blocks"][0]
            block["asset_ref"] = reference
        return bundle

    monkeypatch.setattr(context.mixed, "_core_bundle_builder", image_bundle)
    plan = _prepare(context)
    source_image[0] = _png("red")
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert context.store.path.read_bytes() == before


def test_extra_formal_dependency_blueprint_change_invalidates_material_fingerprint(replacement, monkeypatch):
    context = replacement
    dependency = [{"kind": "independent", "prior_atomic_part_ids": []}]

    def dependency_bundle(payload, **kwargs):
        bundle = _bundle(payload, **kwargs)
        part = bundle["blueprint"]["theme_bundles"][0]["printed_questions"][0]["atomic_parts"][1]
        part["dependency"] = deepcopy(dependency[0])
        return bundle

    monkeypatch.setattr(context.mixed, "_core_bundle_builder", dependency_bundle)
    plan = _prepare(context)
    dependency[0] = {"kind": "prior_part_only", "prior_atomic_part_ids": ["A1"]}
    before = context.store.path.read_bytes()
    with pytest.raises(REJECTED):
        _commit(context, plan)
    assert context.store.path.read_bytes() == before
