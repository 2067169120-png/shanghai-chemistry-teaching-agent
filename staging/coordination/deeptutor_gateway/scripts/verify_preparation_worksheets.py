"""Offline joint-bundle QA with two activity-owned worksheets; no API calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    NativePreparationRenderer,
)
from staging.coordination.deeptutor_gateway.scripts.verify_preparation_visuals import (
    demonstration,
)


def worksheet_sample() -> dict:
    return {
        "title": "观察记录与解释整理",
        "instructions": ["围绕课堂已经讨论的材料填写，先整理记录，再写出自己的解释。"],
        "sections": [
            {
                "heading": "整理观察依据",
                "prompt": "将材料中的信息填入对应位置。没有提供的条件请标明未说明，不补写观察现象。",
                "response_kind": "table",
                "response_lines": 0,
                "columns": ["记录项目", "材料中的信息", "仍需核对的内容"],
                "row_labels": ["观察对象", "观察条件", "实际变化"],
            },
            {
                "heading": "形成自己的解释",
                "prompt": "写出你的解释，并指出它依赖表中哪条信息。暂时没有证据的部分请保留为问题。",
                "response_kind": "lines",
                "response_lines": 4,
                "columns": [],
                "row_labels": [],
            },
        ],
    }


def joint_demonstration() -> dict:
    candidate = demonstration()
    candidate["title"] = "课堂观察与证据表达教学设计"
    candidate["artifact_mode"] = "linked_bundle"
    candidate["lesson_route"] = "review"
    candidate["timing"] = {"periods": 1, "minutes_per_period": 40, "total_minutes": 40}
    candidate["source_basis"] = {"statement_zh": "离线软件交付测试，不是完整化学课"}
    candidate["objectives"] = [
        {"id": "O01", "statement": "区分记录与解释，并说明结论依据。"}
    ]
    candidate["activities"] = [
        {
            "id": "A01",
            "title": "观察信息整理",
            "objective_ids": ["O01"],
            "minutes": 20,
            "teacher_action": "提示学生区分材料中的记录和解释，核对条件是否有出处。",
            "student_action": "在观察记录与解释整理学习单中记录材料信息，并写出有依据的解释。",
            "materials": ["课堂已讨论材料，实际授课时需由教师配套提供"],
            "worksheet": worksheet_sample(),
        },
        {
            "id": "A02",
            "title": "证据链复核",
            "objective_ids": ["O01"],
            "minutes": 20,
            "teacher_action": "要求学生先引用同伴的原意，再核对推理是否使用了额外假设。",
            "student_action": "根据证据链复核学习单记录同伴解释、检查依据并修订自己的表述。",
            "materials": ["上一活动已经完成的观察记录与解释整理学习单"],
            "worksheet": {
                "title": "证据链复核",
                "instructions": [
                    "与同伴交换上一活动的学习单，按原意记录，再给出有依据的反馈。"
                ],
                "sections": [
                    {
                        "heading": "记录同伴的解释",
                        "prompt": "用自己的话复述同伴的结论和依据，请同伴确认含义没有改变。",
                        "response_kind": "lines",
                        "response_lines": 3,
                        "columns": [],
                        "row_labels": [],
                    },
                    {
                        "heading": "核对证据链",
                        "prompt": "填写你核对到的内容，不能确认的部分写出还需要什么信息。",
                        "response_kind": "table",
                        "response_lines": 0,
                        "columns": ["核对环节", "我的核对记录"],
                        "row_labels": ["信息出处", "推理依据", "结论边界"],
                    },
                    {
                        "heading": "修订自己的表述",
                        "prompt": "写出修订后的表述，并简要说明修改的原因。",
                        "response_kind": "lines",
                        "response_lines": 3,
                        "columns": [],
                        "row_labels": [],
                    },
                ],
            },
        },
    ]
    candidate["assessments"] = [
        {
            "id": "E01",
            "title": "记录与推理核对",
            "objective_ids": ["O01"],
            "activity_ids": ["A01", "A02"],
            "evidence_of_learning": "两张学习单上的记录和修订表述。",
            "success_criteria": [
                "记录没有补造现象",
                "解释指出依据",
                "保留缺失条件和待验证问题",
            ],
        }
    ]
    candidate["lesson_stages"] = [
        {
            "title": activity["title"],
            "activity_ids": [activity["id"]],
            "minutes": activity["minutes"],
            "teacher_action": activity["teacher_action"],
            "student_action": activity["student_action"],
            "materials": activity["materials"],
            "assessment": "检查学习单中的记录和表述是否符合评价标准。",
        }
        for activity in candidate["activities"]
    ]
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = NativePreparationRenderer().render(
        joint_demonstration(), output_kind="joint", output_dir=args.output
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
