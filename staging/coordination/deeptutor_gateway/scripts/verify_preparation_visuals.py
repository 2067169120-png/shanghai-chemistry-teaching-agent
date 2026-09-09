"""Offline layout demonstration, not a generated lesson or chemistry approval.

Run with bundled Python. No question bank, user state, provider or credentials.
The four slides cover both types and their maximum column/node counts.
"""

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


def demonstration() -> dict:
    slides = [
        {
            "title": "先分清：看到了什么，怎样解释？",
            "content": ["观察与解释分栏，理由才能被核对。"],
            "visual": {
                "kind": "comparison",
                "comparison": {
                    "dimension_label": "比较维度",
                    "columns": ["观察记录", "解释与推断"],
                    "rows": [
                        {
                            "label": "回答的问题",
                            "values": [
                                "在什么条件下看到了什么？",
                                "哪些理由支持这一解释？",
                            ],
                        },
                        {
                            "label": "记录的重点",
                            "values": ["对象、条件与实际变化", "证据、推理和适用边界"],
                        },
                        {
                            "label": "表达的要求",
                            "values": [
                                "先描述，不把猜测写成现象",
                                "说明依据，保留待验证的假设",
                            ],
                        },
                    ],
                },
                "steps": [],
            },
        },
        {
            "title": "让结论沿着证据形成",
            "content": ["每前进一步，都能指出所依据的信息。"],
            "visual": {
                "kind": "process",
                "comparison": None,
                "steps": [
                    {
                        "label": "记录观察",
                        "detail": "保留对象与条件，将观察记录和猜测分开。",
                    },
                    {
                        "label": "组织解释",
                        "detail": "说明哪些观察支持解释，哪些信息仍然缺少。",
                    },
                    {
                        "label": "核对边界",
                        "detail": "检查推理能否被证据支持，不把可能性说成确定结论。",
                    },
                ],
            },
        },
        {
            "title": "把学习目标、活动和评价对齐",
            "content": ["版式边界示范：三列对象、四个共同维度。"],
            "visual": {
                "kind": "comparison",
                "comparison": {
                    "dimension_label": "对齐维度",
                    "columns": ["目标", "活动", "评价"],
                    "rows": [
                        {
                            "label": "关注点",
                            "values": [
                                "学生应会做什么",
                                "学生实际做什么",
                                "怎样判断是否会做",
                            ],
                        },
                        {
                            "label": "表达",
                            "values": [
                                "使用可观察的动作",
                                "写明操作与产出",
                                "给出检查标准",
                            ],
                        },
                        {
                            "label": "依据",
                            "values": [
                                "当前课题与学习起点",
                                "可用材料与课堂时间",
                                "实际留下的学习证据",
                            ],
                        },
                        {
                            "label": "调整",
                            "values": [
                                "检查范围是否合适",
                                "补充必要的学习支架",
                                "根据表现反馈与追问",
                            ],
                        },
                    ],
                },
                "steps": [],
            },
        },
        {
            "title": "从备课到课后修订",
            "content": ["版式边界示范：四个节点表示工作顺序，不表示化学反应。"],
            "visual": {
                "kind": "process",
                "comparison": None,
                "steps": [
                    {"label": "明确目标", "detail": "先确定希望学生能够完成的动作。"},
                    {"label": "安排活动", "detail": "选择可用材料，安排学生实际参与。"},
                    {
                        "label": "检查产出",
                        "detail": "依据具体标准，核对学生留下的证据。",
                    },
                    {
                        "label": "课后修订",
                        "detail": "根据真实课堂记录调整，不预填实施效果。",
                    },
                ],
            },
        },
    ]
    for index, slide in enumerate(slides, 1):
        slide.update(
            {
                "id": f"S{index:02d}",
                "order": index,
                "minutes": 10,
                "purpose": "验证显式页面结构，不是完整化学教案。",
                "teacher_notes": "离线版式示范。课堂使用前仍需补入来源明确的教学材料。",
                "objective_ids": [],
                "activity_ids": [],
                "assessment_ids": [],
            }
        )
    return {
        "schema_version": "shchem.desktop-preparation-canonical.v1",
        "candidate_id": "layout-demonstration-not-a-manager-task",
        "title": "观察、解释与学习证据：可编辑页面示范",
        "topic": "教师备课的图形组织",
        "audience": "教师版式审阅",
        "artifact_mode": "ppt",
        "slides": slides,
        "candidate_only": True,
        "teacher_review_required": True,
        "publication_allowed": False,
        "official_claim_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = NativePreparationRenderer().render(
        demonstration(),
        output_kind="ppt",
        output_dir=args.output,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
