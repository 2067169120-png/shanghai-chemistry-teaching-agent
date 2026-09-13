from copy import deepcopy

from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_teaching_design_contract import (
    CLASSROOM_DESIGN_CONTRACT, CLASSROOM_DESIGN_REVISION,
)


def test_contract_reaches_real_preparation_prompt_without_rewriting_materials():
    payload = {"topic": "化学平衡", "lesson_route": "review", "materials": "合成资料：保留原题公共材料。"}
    before = deepcopy(payload)
    prompt = _prompt(payload)
    assert payload == before
    assert CLASSROOM_DESIGN_CONTRACT in prompt
    assert CLASSROOM_DESIGN_REVISION in prompt
    assert payload["materials"] in prompt


def test_contract_keeps_teaching_and_recommendation_grounded():
    for text in ("区分说课与授课", "不按导入文件或题库检索结果的顺序逐题粘贴",
                 "保留题目身份及完整公共材料", "不编造命中的题号", "未有复测结果不宣称进步",
                 "不构成新增审核闸门", "已有答题区域不重复加横线"):
        assert text in CLASSROOM_DESIGN_CONTRACT
