"""Exercise the inspector's predicates; actual EXE inspection is a separate run."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[4]


def _inspector():
    path = Path(__file__).parents[1] / "scripts/verify_frozen_preparation_package.py"
    spec = importlib.util.spec_from_file_location("frozen_inspector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _CodeArchive:
    def __init__(self, *, wrong_budget=False, wrong_paper=False):
        self.wrong_budget = wrong_budget
        self.wrong_paper = wrong_paper

    def extract(self, name):
        path = ROOT / (name.replace(".", "/") + ".py")
        source = path.read_text(encoding="utf-8")
        if self.wrong_budget and name.endswith("visual_provider_runtime"):
            source = source.replace("return 65536", "return 32000")
        if self.wrong_paper and name.endswith("desktop_preparation_worksheet"):
            source = source.replace("Mm(210)", "Mm(216)")
        return compile(source, str(path), "exec")


class _MutatedArchive(_CodeArchive):
    def __init__(self, suffix, before, after):
        super().__init__()
        self.suffix, self.before, self.after = suffix, before, after

    def extract(self, name):
        if not name.endswith(self.suffix):
            return super().extract(name)
        path = ROOT / (name.replace(".", "/") + ".py")
        source = path.read_text(encoding="utf-8")
        assert self.before in source, "mutation must change current code"
        return compile(source.replace(self.before, self.after), str(path), "exec")


def test_inspector_checks_pure_code_without_loading_app_or_state():
    result = _inspector().verify_frozen_behavior(_CodeArchive())
    assert result["known_model_budget_cases"] == 5
    assert result["worksheet_a4"]
    assert result["worksheet_title_page_break"]
    assert result["worksheet_table_widths_match"]


@pytest.mark.parametrize("fault", ["wrong_budget", "wrong_paper"])
def test_inspector_rejects_previous_budget_or_non_a4(fault):
    with pytest.raises(RuntimeError, match="Frozen"):
        _inspector().verify_frozen_behavior(_CodeArchive(**{fault: True}))


def test_inspector_checks_catalog_method_not_just_module_presence():
    code = _CodeArchive().extract("integrations.deeptutor_shchem_v1.desktop_facade")
    assert _inspector().verify_catalog_session(code)
    old = compile(
        "def _load_theme_scope(self, scope):\n    return self._themes.groups(scope)\n",
        "old_catalog.py",
        "exec",
    )
    with pytest.raises(RuntimeError, match="Frozen catalog"):
        _inspector().verify_catalog_session(old)


def test_inspector_runs_frozen_note_prompt_without_importing_provider():
    assert _inspector().verify_frozen_note_prompt(_CodeArchive())


def test_inspector_runs_frozen_classroom_contract_and_editable_starter():
    namespace = _inspector().frozen_course_namespace(_CodeArchive())
    assert "复习" in namespace["course_composition_contract"]("review")
    assert _inspector().verify_frozen_classroom_layout(_CodeArchive())


def test_inspector_rejects_the_fixed_height_image_text_strip():
    class OldLayout(_CodeArchive):
        def extract(self, name):
            if not name.endswith("desktop_preparation_classroom_layout"):
                return super().extract(name)
            path = ROOT / (name.replace(".", "/") + ".py")
            source = path.read_text("utf-8").replace(
                "(460, 400, 340, 280, 220)", "(460,)"
            )
            return compile(source, str(path), "exec")

    with pytest.raises(
        RuntimeError, match="Frozen classroom adaptive image sizing missing"
    ):
        _inspector().verify_frozen_classroom_layout(OldLayout())


def test_inspector_rejects_missing_classroom_projection_rule():
    class OldProjection(_CodeArchive):
        def extract(self, name):
            if not name.endswith("desktop_preparation_pedagogy"):
                return super().extract(name)
            path = ROOT / (name.replace(".", "/") + ".py")
            source = path.read_text("utf-8").replace(
                "G 课堂投影：默认服务约40人班级", "G 一般呈现"
            )
            return compile(source, str(path), "exec")

    with pytest.raises(
        RuntimeError, match="Frozen classroom course contract incomplete"
    ):
        _inspector().verify_frozen_note_prompt(OldProjection())


@pytest.mark.parametrize(
    "missing",
    [
        "来源支持的完整例式或例证",
        "同页或紧邻页的可编辑原句或知识归纳",
        "不凭图片元数据转写或虚构原句图",
    ],
)
def test_inspector_rejects_missing_self_contained_notebook_rules(missing):
    class IncompleteNotes(_CodeArchive):
        def extract(self, name):
            path = ROOT / (name.replace(".", "/") + ".py")
            source = path.read_text("utf-8")
            if name.endswith("desktop_preparation_provider"):
                source = source.replace(missing, "未完整要求")
            return compile(source, str(path), "exec")

    with pytest.raises(RuntimeError, match="Frozen classroom note contract"):
        _inspector().verify_frozen_note_prompt(IncompleteNotes())


def test_inspector_runs_frozen_image_capacity_without_store_io():
    assert _inspector().verify_frozen_image_limit(_CodeArchive())


def test_inspector_rejects_old_six_image_capacity():
    class OldImages(_CodeArchive):
        def extract(self, name):
            if not name.endswith("desktop_preparation_images"):
                return super().extract(name)
            path = ROOT / (name.replace(".", "/") + ".py")
            source = path.read_text("utf-8").replace(
                "MAX_IMAGES = 48", "MAX_IMAGES = 6"
            )
            return compile(source, str(path), "exec")

    with pytest.raises(RuntimeError, match="Frozen image capacity"):
        _inspector().verify_frozen_image_limit(OldImages())


def test_inspector_rejects_note_contract_with_missing_feedback_rule():
    class OldContract(_CodeArchive):
        def extract(self, name):
            if not name.endswith("desktop_preparation_provider"):
                return super().extract(name)
            path = ROOT / (name.replace(".", "/") + ".py")
            source = path.read_text("utf-8").replace(
                "不要把待判断物质的分类结果当成例子提前列出", "课堂说明"
            )
            return compile(source, str(path), "exec")

    with pytest.raises(RuntimeError, match="Frozen classroom note contract"):
        _inspector().verify_frozen_note_prompt(OldContract())


def test_inspector_executes_frozen_image_modes_bindings_and_manager_adapter():
    result = _inspector().verify_frozen_preparation_image_input(_CodeArchive())
    assert result["pure_mode_cases"] == 7
    assert result["pure_vision_policy_cases"] == 6
    assert result["pure_pixel_binding_cases"] == 7
    assert result["pure_manager_adapter_cases"] == 3
    assert result["provider_keyword_and_builder_route_static_checked"] is True
    assert result["provider_instantiated"] is False
    assert result["transport_executed"] is False
    assert result["real_model_called"] is False


@pytest.mark.parametrize("old_revision", ["20260910-word-image-roles-v23", "20260912-source-studied-guidance-v25"])
def test_inspector_rejects_old_note_prompt(old_revision):
    archive = _MutatedArchive(
        "desktop_preparation_provider",
        "20260913-lecture-practice-notes-v26",
        old_revision,
    )
    with pytest.raises(RuntimeError, match="Frozen classroom note contract missing"):
        _inspector().verify_frozen_note_prompt(archive)


@pytest.mark.parametrize(
    "suffix,before,after,error",
    [
        (
            "desktop_preparation_image_input",
            "return mode",
            'return "local_only"',
            "Frozen image input mode differs",
        ),
        (
            "desktop_preparation_image_input",
            'if "vision" not in declared or "vision" not in effective:',
            "if False:",
            "Frozen visual policy accepted",
        ),
        (
            "desktop_preparation_provider",
            "image_data: Mapping[str, bytes] | None = None,",
            "pixel_data: Mapping[str, bytes] | None = None,",
            "Frozen provider image_data keyword route missing",
        ),
        (
            "desktop_preparation_provider",
            "build_structured_visual_request(",
            "build_structured_text_request(",
            "Frozen provider text/visual builder route missing",
        ),
        (
            "desktop_preparation_provider",
            "return pages",
            "return list(reversed(pages))",
            "Frozen pixel helper changed bytes or attachment order",
        ),
        (
            "desktop_preparation",
            '"image_data": dict(image_data or {})',
            '"image_data": {}',
            "Frozen manager adapter dropped image_data",
        ),
    ],
)
def test_inspector_rejects_broken_frozen_image_mode_or_pixel_route(
    suffix, before, after, error
):
    with pytest.raises(RuntimeError, match=error):
        _inspector().verify_frozen_preparation_image_input(
            _MutatedArchive(suffix, before, after)
        )


def test_inspector_image_input_namespace_rejects_application_imports():
    archive = _MutatedArchive(
        "desktop_preparation_image_input",
        "from typing import Any",
        "from typing import Any\nfrom . import desktop_facade",
    )
    with pytest.raises(
        RuntimeError, match="Unexpected dependency in frozen pure image module"
    ):
        _inspector().frozen_image_namespaces(archive)


def test_new_image_input_module_has_real_source_match_check():
    module_name = "integrations.deeptutor_shchem_v1.desktop_preparation_image_input"
    inspector = _inspector()
    assert inspector.verify_source_matches_frozen(_CodeArchive(), module_name, ROOT)[
        "code_matches"
    ]
    with pytest.raises(RuntimeError, match="Frozen module differs"):
        inspector.verify_source_matches_frozen(
            _MutatedArchive(
                "desktop_preparation_image_input", "return mode", 'return "local_only"'
            ),
            module_name,
            ROOT,
        )


def test_inspector_runs_word_split_and_explicit_review_contract_without_state():
    result = _inspector().verify_frozen_word_theme_index(_CodeArchive())
    assert result["synthetic_themes"] == 2
    assert result["shared_material_preserved"] and result["source_unchanged"]
    assert result["editorial_prefix_separate_questions"] == 2
    assert result["editorial_prefix_image_ownership_preserved"]
    assert result["answer_quotation_not_split"]
    assert result["explicit_review_cases"] == 3
    assert result["unconfirmed_ranges_still_held"] == 3
    assert result["wrong_issue_and_stale_source_cases_rejected"] == 6
    assert result["explicit_review_cannot_clear_answer_leakage"]
    assert result["boundary_review_revision"] == "20260912-explicit-source-boundary-review-v1"
    assert result["application_or_provider_loaded"] is False


@pytest.mark.parametrize(
    "before,after,error",
    [
        (
            "20260912-explicit-source-boundary-review-v1", "old-review-revision",
            "Frozen Word boundary review revision missing",
        ),
        (
            "tag = _LEADING_TAG.match(text)", "tag = None",
            "Frozen Word editorial prefix swallowed",
        ),
        (
            "if inferred_answer_at is not None:", "if False:",
            "Frozen Word review hold automatically cleared: unmarked_answer",
        ),
        (
            'if _BROKEN_VARIANT.match(_GAP.sub("", question_blocks[0].get("text", "")).strip()):',
            "if False:",
            "Frozen Word review hold automatically cleared: nonstandard_label",
        ),
        (
            "if not context_blocks and (", "if False and (",
            "Frozen Word review hold automatically cleared: self_contained_reference",
        ),
        (
            'and "unmarked_answer" in reviewed_issues', "and False",
            "Frozen Word explicit review changed source or failed: unmarked_answer",
        ),
        (
            '"export_ready": not needs_review,',
            '"export_ready": not needs_review or bool(reviewed_issues),',
            "Frozen Word narrow review cleared answer leakage",
        ),
    ],
)
def test_inspector_rejects_word_boundary_or_review_policy_regressions(before, after, error):
    with pytest.raises(RuntimeError, match=error):
        _inspector().verify_frozen_word_theme_index(
            _MutatedArchive("desktop_word_question_index", before, after)
        )


def test_frozen_word_index_rejects_application_imports_before_execution():
    archive = _MutatedArchive(
        "desktop_word_question_index", "from typing import Any",
        "from typing import Any\nfrom . import desktop_facade",
    )
    with pytest.raises(RuntimeError, match="Unexpected dependency in frozen pure Word index"):
        _inspector().verify_frozen_word_theme_index(archive)


def test_inspector_checks_word_range_preview_statically_without_loading_qt():
    result = _inspector().verify_frozen_word_range_preview_ui(_CodeArchive())
    assert result["constants_checked"] == 9
    assert result["preview_state_routes_static_checked"]
    assert result["ui_instantiated"] is False
    assert result["ui_interaction_checked"] is False


@pytest.mark.parametrize("missing", [
    "查看本次范围预览",
    "已核对无答案标签的原文分界",
    "已核对非标准题目标记的边界",
    "题面本身已包含所引用的全部材料",
    "范围已变化；请重新预览，之前的勾选确认已清除。",
])
def test_inspector_rejects_missing_word_range_preview_ui_contract(missing):
    with pytest.raises(RuntimeError, match="Frozen Word range preview UI constants missing"):
        _inspector().verify_frozen_word_range_preview_ui(
            _MutatedArchive("desktop_workbench.word_question_dialog", missing, "合成旧界面文案")
        )


def test_inspector_rejects_word_range_confirmation_without_review_forwarding():
    with pytest.raises(RuntimeError, match="Frozen Word range preview state route missing"):
        _inspector().verify_frozen_word_range_preview_ui(
            _MutatedArchive("desktop_workbench.word_question_dialog",
                            'ranges["reviewed_issues"] = reviewed',
                            'ranges["ignored_reviews"] = reviewed')
        )
