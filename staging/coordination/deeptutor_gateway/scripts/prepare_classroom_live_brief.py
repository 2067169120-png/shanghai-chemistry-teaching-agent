"""Freeze the source-studied classroom brief, not a hand-composed slide answer.

Offline only. Retains the complete reference material, but reconciles its old
scope/timing instructions with the teacher's newer two-period classroom brief.
Does not open credentials, contact a provider, or alter the daily-app draft.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    _strict_json_load,
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    PREPARATION_PROMPT_REVISION,
    _prompt,
)

SOURCE = (
    ROOT
    / "outputs/备课/2026-09-09-电解质的电离-课堂投影版-r11/课堂版/teacher-brief.json"
)


def build_classroom_live_brief(source):
    brief = deepcopy(source)
    brief["output_kind"] = "joint"
    brief["audience"] = "约40人的高二化学复习课堂；具体班级基础未知，需教师核对"
    text = brief["materials"]
    old_timing = text[text.index("第一课时40分钟：") : text.index("\n\n第一页主标题为")]
    new_timing = (
        "第一课时40分钟：起点判断和教材实验读图5分钟；微观解释与电离定义8分钟；"
        "电解质分类及完整知识表8分钟；Q3例题和Q4独立练习及反馈10分钟；"
        "Q7读图应用及反馈6分钟；回到起点判断、补全笔记3分钟。"
        "这不是页数分配，思考、作答、反馈、笔记均计入上述预算。\n"
        "第二课时40分钟：回顾与电离程度问题3分钟；强弱定义和比较表6分钟；"
        "Q1例题及Q2独立练习与反馈8分钟；书写方法与硫酸铵示范5分钟；"
        "Q8六式分组作答与反馈9分钟；H₂S分步及Q5纠错6分钟；"
        "独立回扣与完整笔记查漏3分钟。Q8可拆为强电解质和弱电解质两组，"
        "完整保留六项作答与六项答案，不按页平均分摊分钟数。\n"
        "本次范围选择：K1—K5为必讲；K6酸式盐、Q6溶解平衡细辨、"
        "Q9过氧化氢为教师可选拓展，保留原材料供以后调整，"
        "不进入80分钟必讲PPT、必做作业或必填学习单。难溶不等于弱电解质"
        "仍在K3/Q1作定性辨析，不展开溶解平衡。"
    )
    text = text.replace(old_timing, new_timing, 1)
    replacements = {
        "以Q1、Q2、Q6检验。": "以Q1、Q2检验，Q6仅作为可选拓展。",
        "## K6 酸式盐与状态条件": "## K6 酸式盐与状态条件 教师可选拓展",
        "### Q6 配套整理题 难溶与弱电离": "### Q6 可选整理题 难溶与弱电离",
        "K1—K6均要在学生可见正文保留完整结论和例证": (
            "本次必讲K1—K5均要在学生可见正文保留完整结论和例证；"
            "K6只保留在输入参考材料中，未选择时不进入学生页面"
        ),
        "课后必做为订正Q1—Q8中错误项并写依据": (
            "课后必做为订正Q1—Q5、Q7、Q8中错误项并写依据"
        ),
        "教案与PPT共享O1—O4和Q1—Q9标识": (
            "教案与PPT共享O1—O4及本次选用的Q1—Q5、Q7、Q8标识"
        ),
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise ValueError("Expected scope instruction changed; inspect source")
        text = text.replace(old, new, 1)
    brief["materials"] = text
    advanced = brief["advanced"]
    old_page_rule = (
        "本次两课时：先现象—解释—分类，再程度—符号—纠错；第一课时21页、"
        "第二课时24页仅为本次编辑结果，不作为通用页数要求。"
        "酸式盐及溶解平衡细辨移到课后选学。"
    )
    if old_page_rule not in advanced["template_and_delivery"]:
        raise ValueError("Expected R11 instruction changed; inspect source")
    advanced["template_and_delivery"] = advanced["template_and_delivery"].replace(
        old_page_rule,
        "本次两课时按材料中已统一的40+40分钟编排；先现象—解释—分类，"
        "再程度—符号—纠错。不提供预写页纲，不固定页数；"
        "K6、Q6、Q9保留在输入中供教师以后扩展，本次不进入必讲课件。",
    )
    advanced["homework_and_strategy"] = (
        "订正Q1—Q5、Q7、Q8中错误项并写依据；K6、Q6、Q9为教师另行选择的拓展，"
        "不设为本次必做。各课时40分钟已含作答和记笔记时间。"
        "原题、据讲义整理题、教材原练习与例式复写分别标注，"
        "不使用未经核验的官方题源或采分点声明。"
    )
    # Binding and byte-level source images stay identical to the reviewed input.
    normalized = normalize_preparation_payload(brief)
    if normalized["timing"]["total_minutes"] != 80:
        raise ValueError("The classroom brief must remain two 40-minute periods")
    if brief["image_assets"] != source["image_assets"]:
        raise ValueError("Image references must be preserved")
    return brief


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    brief = build_classroom_live_brief(_strict_json_load(SOURCE))
    output.mkdir(parents=True, exist_ok=False)
    (output / "teacher-brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2), "utf-8"
    )
    (output / "generation-prompt.txt").write_text(_prompt(brief), "utf-8")
    receipt = {
        "source_brief": str(SOURCE.relative_to(ROOT)),
        "source_brief_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "prompt_revision": PREPARATION_PROMPT_REVISION,
        "required_knowledge": ["K1", "K2", "K3", "K4", "K5"],
        "required_questions": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q7", "Q8"],
        "reference_only": ["K6", "Q6", "Q9"],
        "reference_content_removed": False,
        "provided_slide_outline": False,
        "model_invoked": False,
        "daily_app_draft_changed": False,
        "candidate_only": True,
        "publication_allowed": False,
    }
    (output / "scope-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        json.dumps(
            {"output": str(output), "materials_characters": len(brief["materials"])},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
