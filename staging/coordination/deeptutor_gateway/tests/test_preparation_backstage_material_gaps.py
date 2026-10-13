"""Prompt and transport contracts only; synthetic output proves no model quality."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy

import pytest

from test_preparation_visual_provider import _asset, _content, _context, _Transport

from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    StructuredPreparationProvider,
    _prompt,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_pedagogy import teacher_design_starter


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
@pytest.mark.parametrize("mode", ["local_only", "vision"])
def test_incomplete_materials_and_images_route_gaps_backstage_in_actual_request(style, mode):
    asset, pixels = _asset(1)
    payload = {
        "topic": "图表观察与概念辨析",
        "lesson_route": "review",
        "materials": "仅有目录；题面图缺少连接条件，未提供可转写教材原句。",
        "image_input_mode": mode,
        "image_assets": [asset],
    }
    original = deepcopy(payload)
    transport = _Transport()
    provider = StructuredPreparationProvider(_context(style), transport=transport)
    provider.generate(payload, image_data={asset["asset_id"]: pixels} if mode == "vision" else None)
    assert len(transport.requests) == 1
    _, prompt, image_urls = _content(transport.requests[0])

    # The same product boundary applies to student content and teacher delivery.
    boundary = prompt.split("无法支持的事实和数字", 1)[0]
    assert all(name in boundary for name in ("生成正文", "表格", "图注", "教师备注", "板书", "Word教案", "PPT正文", "讲者备注"))
    assert "不混入教学正文或teacher_notes" in boundary
    assert "不写‘仅供参考’‘请教师自行检查’‘AI可能有误’‘不保证准确性’" in boundary

    # Check the complete operative clauses, rather than only the early prohibition.
    material_clause = re.search(r"资料缺口(?:仅)?具体记在[^。]+", prompt)
    image_clause = re.search(r"需要补图时[^。]+", prompt)
    assert material_clause is not None and image_clause is not None
    for clause in (material_clause.group(), image_clause.group()):
        assert "uncertainties" in clause
        assert not re.search(r"(?:在|记在|写入)\s*(?:教师备注|teacher_notes|备注)", clause), clause

    # Source facts stay in the unchanged brief; missing content is never licensed.
    assert "无法支持的事实和数字从正文省略" in prompt
    assert "不凭图片元数据转写或虚构原句图" in prompt
    assert "不能用“见图”替代缺失证据" in prompt
    assert "答案与解析图只用于相应题目的点击显示分析或后续讲评" in prompt
    sent_brief, _ = json.JSONDecoder().raw_decode(prompt.split("教师备课简报 JSON：\n", 1)[1])
    assert sent_brief == original == payload
    assert sent_brief["image_assets"][0]["sha256"] == asset["sha256"]
    assert "fixture-not-a-real-credential" not in prompt
    assert len(image_urls) == (1 if mode == "vision" else 0)
    if mode == "vision":
        _, encoded = image_urls[0].split(",", 1)
        actual_pixels = base64.b64decode(encoded, validate=True)
        assert actual_pixels == pixels
        assert hashlib.sha256(actual_pixels).hexdigest() == asset["sha256"]
        assert "图中模糊、被遮挡或缺失" in prompt
        assert "转写状态仅记录在uncertainties" in prompt
    else:
        assert "没有看到图片像素" in prompt
        assert "不得声称已看图" in prompt


@pytest.mark.parametrize("topic", ["核外电子排布的表示方法", "电离平衡", "系统的内能"])
def test_composed_source_and_course_contracts_have_no_positive_delivery_metadata_routes(topic):
    starter = teacher_design_starter("review", topic)
    payload = {
        "topic": topic,
        "lesson_route": "review",
        "materials": "实际摘录原文与图像身份仅供内部追溯。",
        "advanced": {"template_and_delivery": starter},
    }
    original = deepcopy(payload)
    prompt = _prompt(payload)
    # Inspect operative instructions, apart from the unchanged source snapshot.
    instructions, brief_section = prompt.split("教师备课简报 JSON：\n", 1)
    instructions += prompt.split("参考资料到此结束。", 1)[1]
    positive_delivery_routes = (
        r"(?:资料缺口|出处与采用说明)[^。；\n]{0,36}(?:记在|放在|写入)[^。；\n]{0,16}(?:教师备注|teacher_notes|教案)",
        r"(?:在|并在)[^。；\n]{0,36}(?:教师备注|teacher_notes)[^。；\n]{0,26}(?:注明参考编号|说明缺口|待核原因)",
        r"需要补图时在备注",
        r"teacher_notes[^。；\n]*使用哪些来源",
        r"lesson_stage\.materials[^。；\n]*主要来源",
        r"来源简注可小",
    )
    for pattern in positive_delivery_routes:
        assert not re.search(pattern, instructions), pattern
    assert not re.search(positive_delivery_routes[0], starter)

    # The differing deliveries and real-source constraints remain explicit.
    assert "学生打印版题目与答案分开" in instructions
    assert "课堂PPT可在学生作答后同页点击显示答案与依据" in instructions
    assert "教师完整版保留对应答案与分析" in instructions
    assert "静态学生版本不能依赖点击隐藏答案" in instructions
    assert "不涂改来源标识" in instructions
    assert "不能加白边、AI补字或补画图线来冒充恢复" in instructions
    assert "必要公共材料、图表、符号定义、条件" in instructions
    assert "基于现有资料的替代活动" in instructions
    assert "实际采用的Word区块编号、教材知识点编号保留在内部source_basis" in instructions
    sent_brief, _ = json.JSONDecoder().raw_decode(brief_section)
    assert payload == original == sent_brief
    assert "来源：国家中小学智慧教育平台" in starter


def test_scientific_uncertainty_and_teacher_instruction_are_preserved():
    prompt = _prompt({"topic": "检验方案的证据局限", "lesson_route": "experiment"})
    assert "分清现象、解释及证据局限" in prompt
    assert "未进行实验只写预期与待检验，不能声称学生观察到了结果" in prompt
    assert "详细讲解、板书递进、提问后的等待与追问、预设回答分支写入 teacher_notes" in prompt
    assert "真实实验须提示预实验、安全与废弃物处理" in prompt
