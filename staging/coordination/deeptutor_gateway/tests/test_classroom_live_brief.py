import importlib.util
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "classroom_live_brief",
    ROOT
    / "staging/coordination/deeptutor_gateway/scripts/prepare_classroom_live_brief.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_scope_is_explicit_and_references_remain_unchanged():
    source = json.loads(MODULE.SOURCE.read_text("utf-8"))
    original = deepcopy(source)
    brief = MODULE.build_classroom_live_brief(source)
    assert source == original
    assert brief["output_kind"] == "joint"
    assert brief["image_assets"] == source["image_assets"]
    assert "约40人" in brief["audience"]
    assert "第一课时21页" not in brief["advanced"]["template_and_delivery"]
    assert "K1—K6均要" not in brief["materials"]
    assert "第二课时40分钟：回顾3分钟" not in brief["materials"]
    assert "K6只保留在输入参考材料中" in brief["materials"]
    for field in ["K1", "K2", "K3", "K4", "K5", "K6", *[f"Q{i}" for i in range(1, 10)]]:
        marker = "## " + field if field.startswith("K") else "### " + field
        assert brief["materials"].count(marker + " ") == 1
    # No source equations, questions or definitions are cut from the input.
    for text in (
        "图2.12先看KNO₃固体与水溶液",
        "NaHSO₄＝Na⁺＋HSO₄⁻",
        "H₂O₂也有极微弱的自偶电离",
        "2H₂O ⇌ H₃O⁺＋OH⁻",
        "讲义参考答案AC",
    ):
        assert text in brief["materials"]


def test_changed_scope_input_fails_instead_of_guessing():
    source = json.loads(MODULE.SOURCE.read_text("utf-8"))
    source["materials"] = source["materials"].replace("K1—K6均要", "K1—K5均要")
    import pytest

    with pytest.raises(ValueError, match="scope instruction changed"):
        MODULE.build_classroom_live_brief(source)
