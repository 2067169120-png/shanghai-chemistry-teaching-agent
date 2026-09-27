"""Content-bound import decisions using disposable sources/state and no provider."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_desktop_import_preview import _stored, _word_bytes
from test_desktop_visual_import_facade import FakeProviderStore, _facade, desktop_paths as desktop_paths

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_import_identity import identity_guard


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_word_questions.load_attribute_catalog",
                        lambda *args, **kwargs: {"knowledge_points": [], "nodes": []})


def setup(desktop_paths, tmp_path):
    source = tmp_path / "教师讲义.docx"
    source.write_bytes(_word_bytes())
    provider = FakeProviderStore(configured=False)
    facade = _facade(desktop_paths, provider)
    old = facade.save_visual_import_batch(handout_files=(source,), source_type="讲义")
    return facade, source, old, provider


def preview(facade, *paths):
    return facade.preview_import_files(handout_files=paths, source_type="讲义")


def commit(facade, value, **kwargs):
    return facade.commit_import_preview(value["preview_id"], value["revision"],
        [row["source_id"] for row in value["sources"]], **kwargs)


def teacher_label(facade, item, note="教师已核对的标签"):
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    return facade.word_question_save_attributes(item["key"], item["revision"], {"teacher_note": note},
        expected_attribute_revision=options["attributes"]["revision"],
        expected_stored_revision=options["stored_revision"])


def test_renamed_bytes_continue_exact_old_range_and_teacher_history_without_writes(desktop_paths, tmp_path):
    facade, source, old, provider = setup(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    item = facade.word_question_update_range(item["key"], item["revision"],
        block_start=1, question_end=1, answer_start=2, block_end=2)
    saved = teacher_label(facade, item)
    selected = [{"key": item["key"], "revision": item["revision"], "points": 3}]
    facade.word_question_save_selection(selected)
    renamed = tmp_path / "换名的讲义.docx"
    renamed.write_bytes(source.read_bytes())
    before = _stored(desktop_paths.state_root)
    plan = preview(facade, renamed)
    identity = plan["identities"]["items"][plan["sources"][0]["source_id"]]
    assert identity["status"] == "existing"
    assert identity["existing"]["source_name"] == source.name
    assert _stored(desktop_paths.state_root) == before
    result = commit(facade, plan)
    assert result.batch_id == old.batch_id and result.import_review["new_count"] == 0
    assert result.import_review["reused"][0]["source_sha256"] == item["source_sha256"]
    assert result.import_review["reused"][0]["batch_id"] == old.batch_id
    assert _stored(desktop_paths.state_root) == before
    current = facade.word_question_catalog()["items"][0]
    assert current["key"] == item["key"] and current["revision"] == item["revision"]
    assert current["attributes"] == saved and not current.get("attribute_stale")
    assert facade.word_question_saved_selection() == selected
    assert len(facade.list_imported_word_batches()) == 1 and provider.borrow_calls == 0


def test_same_name_different_bytes_adds_separate_source_without_migrating_teacher_label(desktop_paths, tmp_path):
    facade, source, old, provider = setup(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    saved = teacher_label(facade, item)
    archive_before = (facade._visual_import_root / "sources" / f"{item['source_sha256']}.docx").read_bytes()
    source.write_bytes(_word_bytes(text="合成新版内容不同"))
    plan = preview(facade, source)
    identity = next(iter(plan["identities"]["items"].values()))
    assert identity["status"] == "new_version" and identity["existing"] is None
    assert identity["same_name_versions"][0]["batch_id"] == old.batch_id
    result = commit(facade, plan)
    assert result.batch_id != old.batch_id and result.import_review["version_count"] == 1
    rows = facade.word_question_catalog()["items"]
    assert len(rows) == 2 and len({row["key"] for row in rows}) == 2
    assert next(row for row in rows if row["key"] == item["key"])["attributes"] == saved
    assert "attributes" not in next(row for row in rows if row["key"] != item["key"])
    assert (facade._visual_import_root / "sources" / f"{item['source_sha256']}.docx").read_bytes() == archive_before
    assert provider.borrow_calls == 0


def test_mixed_handouts_save_only_new_and_keep_reused_actual_batch(desktop_paths, tmp_path):
    facade, source, old, provider = setup(desktop_paths, tmp_path)
    other = tmp_path / "另一份.docx"
    other.write_bytes(_word_bytes(text="独立的新讲义"))
    result = commit(facade, preview(facade, source, other))
    assert result.source_count == 1 and result.sources[0].filename == other.name
    assert result.import_review["new_count"] == 1
    assert result.import_review["reused"][0]["batch_id"] == old.batch_id
    assert len(facade.list_imported_word_batches()) == 2 and provider.borrow_calls == 0


@pytest.mark.parametrize("existing", [False, True])
def test_same_bytes_twice_only_one_source_is_saved_or_continued(desktop_paths, tmp_path, existing):
    provider = FakeProviderStore(configured=False)
    facade = _facade(desktop_paths, provider)
    a, b = tmp_path / "甲.docx", tmp_path / "乙.docx"
    a.write_bytes(_word_bytes()); b.write_bytes(a.read_bytes())
    if existing:
        facade.save_visual_import_batch(handout_files=(a,), source_type="讲义")
    result = commit(facade, preview(facade, a, b))
    assert result.source_count == 1 and result.import_review["repeated_count"] == 1
    assert len(result.import_review["reused"]) == int(existing)
    assert result.import_review["new_count"] == int(not existing)
    assert len(facade.list_imported_word_batches()) == 1 and provider.borrow_calls == 0


@pytest.mark.parametrize("change", ["input", "archive", "descriptor"])
def test_changed_confirmation_binding_fails_without_new_write(desktop_paths, tmp_path, change):
    facade, source, old, provider = setup(desktop_paths, tmp_path)
    alias = tmp_path / "别名.docx"; alias.write_bytes(source.read_bytes())
    plan = preview(facade, alias)
    if change == "input":
        alias.write_bytes(_word_bytes(text="后来修改的本次输入"))
    elif change == "archive":
        target = next(iter(plan["identities"]["items"].values()))["existing"]
        (facade._visual_import_root / "sources" / f"{target['source_sha256']}.docx").write_bytes(b"changed")
    else:
        descriptor = facade._saved_visual_import_batch(old.batch_id)
        descriptor["test_intervening_change"] = True
        facade._state.save_draft(facade._visual_import_draft_id(old.batch_id), descriptor)
    before = _stored(desktop_paths.state_root)
    with pytest.raises(DesktopFacadeError, match="变化"):
        commit(facade, plan)
    assert _stored(desktop_paths.state_root) == before and provider.borrow_calls == 0


def test_teacher_edit_after_preview_is_preserved_as_current_not_rebound(desktop_paths, tmp_path):
    facade, source, _, _ = setup(desktop_paths, tmp_path)
    item = facade.word_question_catalog()["items"][0]
    plan = preview(facade, source)
    latest = teacher_label(facade, item, "预览之后教师补写")
    before = _stored(desktop_paths.state_root)
    commit(facade, plan)
    assert _stored(desktop_paths.state_root) == before
    assert facade.word_question_catalog()["items"][0]["attributes"] == latest


def test_another_facade_import_between_preview_and_commit_requires_new_preview(desktop_paths, tmp_path):
    provider = FakeProviderStore(configured=False)
    first, second = _facade(desktop_paths, provider), _facade(desktop_paths, provider)
    a, b = tmp_path / "甲.docx", tmp_path / "乙.docx"
    a.write_bytes(_word_bytes()); b.write_bytes(a.read_bytes())
    stale = preview(second, b)
    commit(first, preview(first, a))
    before = _stored(desktop_paths.state_root)
    with pytest.raises(DesktopFacadeError, match="变化"):
        commit(second, stale)
    assert _stored(desktop_paths.state_root) == before
    result = commit(second, preview(second, b))
    assert result.import_review["new_count"] == 0


def test_concurrent_confirmation_and_cancel_do_not_create_personal_records(desktop_paths, tmp_path):
    facade, source, _, _ = setup(desktop_paths, tmp_path)
    plan = preview(facade, source)
    before = _stored(desktop_paths.state_root)
    with identity_guard(desktop_paths.state_root), ThreadPoolExecutor() as pool:
        with pytest.raises(DesktopFacadeError, match="导入正在保存"):
            pool.submit(commit, facade, plan).result(timeout=5)
    with pytest.raises(DesktopFacadeError, match="取消"):
        commit(facade, plan, should_cancel=lambda: True)
    assert _stored(desktop_paths.state_root) == before
    assert commit(facade, plan).import_review["new_count"] == 0


def test_reusing_word_does_not_detach_new_answer_context(desktop_paths, tmp_path):
    facade, source, _, _ = setup(desktop_paths, tmp_path)
    answer = tmp_path / "新答案.docx"; answer.write_bytes(_word_bytes(text="另一个答案"))
    plan = facade.preview_import_files(question_files=(source,), answer_files=(answer,), source_type="题答")
    before = _stored(desktop_paths.state_root)
    with pytest.raises(DesktopFacadeError, match="关联"):
        commit(facade, plan)
    assert _stored(desktop_paths.state_root) == before


def test_duplicate_preview_does_not_parse_legacy_word_or_rebuild_cache(desktop_paths, tmp_path, monkeypatch):
    facade, source, _, _ = setup(desktop_paths, tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("identity inventory must not parse Word or rebuild previews")
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_preparation_sources.PreparationSourcesService.word_preview_bytes", forbidden)
    monkeypatch.setattr("integrations.deeptutor_shchem_v1.desktop_word_preview_cache.WordPreviewCache.save", forbidden)
    before = _stored(desktop_paths.state_root)
    result = commit(facade, preview(facade, source))
    assert result.import_review["new_count"] == 0 and _stored(desktop_paths.state_root) == before


def test_all_existing_question_answer_pair_continues_actual_original_batch(desktop_paths, tmp_path):
    provider = FakeProviderStore(configured=False)
    facade = _facade(desktop_paths, provider)
    question, answer = tmp_path / "原题.docx", tmp_path / "原答案.docx"
    question.write_bytes(_word_bytes(text="合成题面"))
    answer.write_bytes(_word_bytes(text="合成原答案"))
    old = facade.save_visual_import_batch(question_files=(question,), answer_files=(answer,), source_type="题答")
    q_alias, a_alias = tmp_path / "题目换名.docx", tmp_path / "答案换名.docx"
    q_alias.write_bytes(question.read_bytes()); a_alias.write_bytes(answer.read_bytes())
    before = _stored(desktop_paths.state_root)
    plan = facade.preview_import_files(question_files=(q_alias,), answer_files=(a_alias,), source_type="题答")
    result = commit(facade, plan)
    assert result.batch_id == old.batch_id and result.import_review["new_count"] == 0
    assert [target["role"] for target in result.import_review["reused"]] == ["question", "answer"]
    assert all(target["batch_id"] == old.batch_id for target in result.import_review["reused"])
    assert _stored(desktop_paths.state_root) == before and provider.borrow_calls == 0


@pytest.mark.parametrize("original_role", ["question", "answer"])
def test_existing_source_selected_as_handout_keeps_its_original_role(desktop_paths, tmp_path, original_role):
    facade = _facade(desktop_paths, FakeProviderStore(configured=False))
    question, answer = tmp_path / "题目.docx", tmp_path / "答案.docx"
    question.write_bytes(_word_bytes(text="合成题")); answer.write_bytes(_word_bytes(text="合成答案"))
    old = facade.save_visual_import_batch(question_files=(question,), answer_files=(answer,), source_type="题答")
    selected = question if original_role == "question" else answer
    alias = tmp_path / "本次当讲义选择.docx"; alias.write_bytes(selected.read_bytes())
    before = _stored(desktop_paths.state_root)
    result = commit(facade, preview(facade, alias))
    assert result.batch_id == old.batch_id
    assert result.import_review["reused"][0]["role"] == original_role
    assert [row.role for row in result.sources] == ["question", "answer"]
    assert _stored(desktop_paths.state_root) == before
