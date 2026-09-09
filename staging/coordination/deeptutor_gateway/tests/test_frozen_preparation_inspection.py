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
                "MAX_IMAGES = 12", "MAX_IMAGES = 6"
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
