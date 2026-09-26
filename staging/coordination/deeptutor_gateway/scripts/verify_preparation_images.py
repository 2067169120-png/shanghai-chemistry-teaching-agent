"""Offline real-image pipeline check; not a model run or a complete lesson."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    DesktopPreparationManager,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    NativePreparationRenderer,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    image = (
        root
        / "outputs/备课/2026-09-09-电解质与电离方程式-修订版/textbook-figure-2-14.png"
    )
    original = image.read_bytes()
    expected = "f375638dde10576147a56d6c71c890187ca9a3e6179236612b3077733554f3bf"
    if hashlib.sha256(original).hexdigest() != expected:
        raise ValueError("Frozen exemplar figure changed; stop for source review.")
    manager = DesktopPreparationManager(
        output / "isolated-manager", NativePreparationRenderer()
    )
    asset = manager.image_store.import_image(
        image,
        "图2.14 氯化钠电离过程示意图",
        "沪科技化学必修第一册 · 第57页（PDF第62页）",
        "比较晶体、溶于水和熔融状态，说明离子存在与自由移动的区别。",
    )
    payload = {
        "output_kind": "ppt",
        "topic": "电离概念片段（离线链路验收）",
        "audience": "教师检查用，非完整课堂成品",
        "lesson_route": "复习",
        "lesson_timing": "1课时×10分钟",
        "objective": "依据教材图说明离子存在与自由移动的区别",
        "materials": "冻结样课教材图2.14及教师已整理文字。此候选为确定性软件验收，不调用模型。",
        "image_assets": [asset],
    }
    raw = {
        "title": "从教材图到概念笔记（离线验收片段）",
        "objectives": [{"statement": "能区分离子存在与离子能够自由移动。"}],
        "activities": [
            {
                "title": "观察、解释与记录",
                "objective_numbers": [1],
                "minutes": 10,
                "teacher_action": "先给观察时间，再听取解释，最后留出笔记时间。",
                "student_action": "比较图中两条路径，口头解释并完成记录。",
                "materials": ["教材图2.14"],
                "worksheet": None,
            }
        ],
        "assessments": [
            {
                "title": "一句话解释",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "evidence_of_learning": "学生能说明固体中有离子却不能自由移动。",
                "success_criteria": ["区分存在与自由移动", "指出溶解或熔融条件"],
            }
        ],
        "slides": [],
        "lesson_stages": [
            {
                "title": "观察到概念",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "assessment_numbers": [1],
                "minutes": 10,
                "teacher_action": "先观察教材原图，追问粒子状态，再核对笔记。",
                "student_action": "观察、口述理由、记录并回看问题。",
                "materials": ["教材图2.14"],
                "assessment": "检查存在与自由移动是否混淆。",
            }
        ],
        "homework": {
            "title": "口头回顾",
            "tasks": [
                {
                    "instruction": "用一句话说明氯化钠晶体中离子存在与自由移动的区别。",
                    "objective_numbers": [1],
                }
            ],
            "estimated_minutes": 2,
        },
        "uncertainties": [
            {
                "field": "classroom_fit",
                "description": "此文件仅验证真实教材图片的本地导出链路。",
                "teacher_action": "整课请使用来源对齐样课并另行复核；本片段不代表模型或试教结果。",
            }
        ],
    }
    rows = [
        (
            "看图解释氯化钠的电离",
            4,
            ["先观察两条路径", "区分存在与自由移动"],
            "先留45秒观察，暂不要求抄写。再请学生说明两种条件。",
            None,
            {
                "asset_id": asset["asset_id"],
                "observation_prompt": "晶体中已有离子，为什么仍不能导电？",
            },
        ),
        (
            "比较三种状态中的离子",
            3,
            ["比较离子存在与自由移动两个维度"],
            "解释时保留溶液中水合离子的表述，不将教材图当作实测录像。",
            {
                "kind": "comparison",
                "comparison": {
                    "dimension_label": "比较维度",
                    "columns": ["晶体", "溶于水", "熔融"],
                    "rows": [
                        {"label": "有无离子", "values": ["有", "有", "有"]},
                        {"label": "能否自由移动", "values": ["不能", "能够", "能够"]},
                    ],
                },
                "steps": [],
            },
            None,
        ),
        (
            "整理笔记：存在不等于自由移动",
            3,
            [
                "氯化钠晶体中已有离子，但离子不能自由移动。",
                "溶于水或熔融后形成能够自由移动的离子。",
            ],
            "留60秒整理，再不看屏幕回答第一页的问题。核对学生是否漏写条件。",
            None,
            None,
        ),
    ]
    for title, minutes, content, notes, visual, binding in rows:
        raw["slides"].append(
            {
                "title": title,
                "purpose": "观察、解释与笔记闭环的局部验收",
                "objective_numbers": [1],
                "activity_numbers": [1],
                "assessment_numbers": [1],
                "minutes": minutes,
                "content": content,
                "teacher_notes": notes,
                "visual": visual,
                "image": binding,
            }
        )
    fixture_calls = []

    def fixture(
        payload, candidate_schema, profile_binding, report_progress, is_cancelled
    ):
        fixture_calls.append(True)
        return deepcopy(raw)

    task = manager.prepare(payload, "offline-fixture", "local-images-v10")
    result = manager.run(task["task_id"], fixture)
    if result["status"] != "completed":
        raise RuntimeError(result.get("error"))
    pptx, _ = manager.artifact_path(task["task_id"], "pptx")
    receipt = {
        "network_provider_calls": 0,
        "fixture_calls": len(fixture_calls),
        "candidate_only": True,
        "complete_lesson": False,
        "image_source_sha256": expected,
        "source_unchanged": image.read_bytes() == original,
        "pptx": str(pptx),
        "task": result,
    }
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"pptx": str(pptx), "network_provider_calls": 0}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
