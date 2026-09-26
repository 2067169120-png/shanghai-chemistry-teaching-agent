from copy import deepcopy
import math
import random
import pytest
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_studio import (
    TEMPLATES, Countdown, NoRepeatPicker, equilibrium_step, get_template, make_groups,
    parse_roster, search_templates, template_brief,
)


def test_template_catalog_search_and_invalid_identity():
    assert len(TEMPLATES) == len({t.key for t in TEMPLATES}) == 6
    assert search_templates("错因")[0].key == "feedback"
    assert search_templates("", "实验")[0].key == "experiment"
    assert not search_templates("不存在的模板")
    with pytest.raises(ValueError):
        get_template("missing")


def test_template_preserves_materials_images_and_user_requirements():
    payload = {"topic": "已有课题", "materials": "完整公共材料", "audience": "已有班级",
               "objective": "教师目标", "lesson_route": "复习", "image_assets": [{"id": "figure"}],
               "image_input_mode": "vision", "advanced": {"template_and_delivery": "教师原结构"}}
    before = deepcopy(payload)
    result = template_brief(payload, "unit", topic="新课题", audience="新班级")
    assert payload == before
    for field in ("topic", "materials", "audience", "objective", "image_assets", "image_input_mode", "lesson_route"):
        assert result[field] == before[field]
    assert result["advanced"]["template_and_delivery"].startswith("教师原结构")
    assert template_brief(result, "unit") == result
    assert "timing" not in result


def test_empty_brief_gets_unit_timing_and_requested_title():
    result = template_brief({"advanced": {}}, "unit", topic="化学平衡", audience="高二")
    assert result["topic"] == "化学平衡" and result["audience"] == "高二"
    assert result["timing"] == {"periods": 2, "minutes_per_period": 40}
    assert result["objective"]


def test_favorites_persist_without_changing_question_bank_or_drafts(tmp_path):
    store = DesktopStateStore(tmp_path)
    store.save_draft("draft", {"title": "原草稿"})
    store.add_to_basket({"key": "theme1"})
    before = store.snapshot()
    store.save_studio_favorites(["unit", "experiment", "unit"])
    after = DesktopStateStore(tmp_path).snapshot()
    assert after["studio"]["favorites"] == ["experiment", "unit"]
    assert after["basket"] == before["basket"] and after["drafts"] == before["drafts"]


@pytest.mark.parametrize("text", ["", "01\n01", "x" * 61, "\n".join(map(str, range(301)))])
def test_invalid_rosters_are_rejected(text):
    with pytest.raises(ValueError):
        parse_roster(text)


def test_roster_delimiters_and_balanced_groups():
    assert parse_roster("01，02;03\n04") == ["01", "02", "03", "04"]
    entries = [str(n) for n in range(41)]
    groups = make_groups(entries, 6, random.Random(42))
    assert max(map(len, groups)) - min(map(len, groups)) <= 1
    assert sorted(sum(groups, [])) == sorted(entries)
    assert entries == [str(n) for n in range(41)]
    with pytest.raises(ValueError):
        make_groups(entries, 42)


def test_picker_never_repeats_until_new_round():
    picker = NoRepeatPicker(["01", "02", "03"], random.Random(1))
    assert {picker.draw(), picker.draw(), picker.draw()} == {"01", "02", "03"}
    assert picker.draw() is None


def test_countdown_uses_deadline_not_callback_counts():
    now = [0.]
    clock = Countdown(300, lambda: now[0])
    clock.start()
    now[0] = 10.2
    assert clock.label() == "04:50"
    clock.pause()
    now[0] += 60
    assert clock.label() == "04:50"
    clock.start()
    now[0] += 500
    assert clock.seconds == 0 and clock.label() == "00:00"
    clock.reset(60)
    assert clock.label() == "01:00" and clock.deadline is None
    with pytest.raises(ValueError):
        Countdown(-1)


def test_equilibrium_preserves_total_and_equal_rates_not_equal_amounts():
    a, b = equilibrium_step(100, 100, .3, .1, 100)
    assert a + b == pytest.approx(100)
    assert a == pytest.approx(25) and b == pytest.approx(75)
    assert .3 * a == pytest.approx(.1 * b)
    assert equilibrium_step(20, 100, 0, 0, 100) == (20, 80)
    a1, _ = equilibrium_step(70, 100, .2, .4, 3)
    a2, _ = equilibrium_step(a1, 100, .2, .4, 4)
    assert a2 == pytest.approx(equilibrium_step(70, 100, .2, .4, 7)[0])


@pytest.mark.parametrize("values", [(101, 100, 1, 1, 1), (100, 100, -1, 1, 1), (100, 100, 1, 1, -1), (math.nan, 100, 1, 1, 1)])
def test_bad_model_parameters_are_rejected(values):
    with pytest.raises(ValueError):
        equilibrium_step(*values)
