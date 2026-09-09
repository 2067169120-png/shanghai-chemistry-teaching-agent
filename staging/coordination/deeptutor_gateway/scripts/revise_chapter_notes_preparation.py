"""Chapter-first, textbook-note revision of the existing classroom example.

Uses the production renderer and bundled artifact runtime, with no model call.
The r4 source generator and all previous artifacts remain unchanged.
"""

import argparse
import json
from pathlib import Path

from revise_source_preparation_v11 import (
    EXPECTED,
    FIGURE,
    INPUT,
    INPUT_SHA,
    BundledArtifactRenderer,
    _canonical_candidate_digest,
    comparison,
    digest,
    revised_candidate,
)

ELECTROLYSIS_QUOTE = (
    "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
)


def chapter_notes_candidate(original):
    c = revised_candidate(original)
    c["title"] = c["topic"] = "电解质的电离"
    cover = c["slides"][0]
    cover.update(
        title=c["topic"],
        purpose="明确本课教材小节和学习范围",
        minutes=1,
        content=[
            "第2章 海洋中的卤素资源",
            "2.2 氧化还原反应和离子反应",
            "电解质　电离　强弱电解质　电离方程式",
        ],
        teacher_notes="用1分钟说明本课范围。导入问题在第2页，不用问题替代章节名。\n依据：教材印刷56—58页；Word区块42—63。",
    )
    definitions = c["slides"][1]
    definitions.update(
        title="一、电解质与非电解质",
        minutes=4,
        content=[
            "同为NaCl，为什么晶体不导电，而水溶液能导电？先明确物质类别。",
            "知识表依据教材第56页整理；记录时保留对象和状态条件。",
        ],
        visual=comparison(
            ["电解质", "非电解质"],
            [
                ("判断对象", ["化合物", "化合物"]),
                (
                    "定义条件",
                    [
                        "在水溶液中或熔融状态下能导电",
                        "在水溶液中和熔融状态下均不能导电",
                    ],
                ),
                ("本课例子", ["氯化钠NaCl", "蔗糖、酒精"]),
                (
                    "判断提醒",
                    [
                        "导电离子来自自身电离；不要求固体导电",
                        "不能把单质或混合物归入非电解质",
                    ],
                ),
            ],
        ),
        teacher_notes="先用1分钟提出NaCl晶体与水溶液导电差异的问题，听取猜想。接着2分钟对照表格解释定义对象及‘或’与‘和’，最后1分钟填写学习单第1页第1栏。表中定义和例子依据教材整理，判断提醒结合讲义辨析，不把归纳表冒称教材逐字原句。\n依据：教材印刷56页；Word区块42—45、49；TB-M1-C2-S22-C05。",
    )
    c["slides"][3]["title"] = "辨析反馈：CO₂的导电离子来源"
    c["slides"][5]["title"] = "二、电离与导电的区别"
    c["slides"][5]["content"] = ["知识表依据教材第57页整理；区别过程与条件。"]
    c["slides"][5]["visual"]["comparison"]["rows"][1]["values"][0] = (
        "溶于水；NaCl也可熔融电离"
    )
    c["slides"][6].update(
        title="电离的定义与笔记要点",
        content=[
            "教材原文（第57页）：" + ELECTROLYSIS_QUOTE,
            "NaCl晶体含离子，但离子不能自由移动；水溶液中离子能自由移动。",
            "电解质溶液导电还需外接电源和闭合通路。",
            "笔记：保留定义中的状态、微粒和运动条件，再记录电离与导电的区别。",
        ],
        teacher_notes="先指认教材定义中的状态、自由移动的离子、过程三个要点；再留90秒记录学习单第1页第3—4栏。原句与后两条知识归纳分开，不把归纳冒称原文。\n依据：教材印刷57页电离定义及图2.14；TB-M1-C2-S22-C06。",
    )
    c["slides"][7]["title"] = "比较NaCl与醋酸的电离程度"
    c["slides"][8]["title"] = "三、强电解质与弱电解质"
    c["slides"][8]["visual"]["comparison"]["rows"][2]["label"] = "代表物质"
    c["slides"][8]["purpose"] = "核对电离程度并形成完整知识表格"
    c["slides"][8]["content"] = ["知识表依据教材第58页整理；适用范围为水溶液。"]
    c["slides"][9]["title"] = "四、电离方程式的书写"
    c["slides"][13]["title"] = "本课知识小结与笔记核对"
    c["slides"][15]["title"] = "回顾反馈：用所学知识解释"
    c["activities"][0]["teacher_action"] = (
        "第1页说明教材小节；第2页提出NaCl问题后讲定义表并留时间记录。"
        "第3—4页先判断CO₂再反馈。第5—7页观察教材图、比较过程，记录教材电离原句和导电条件。"
    )
    c["lesson_stages"][0]["teacher_action"] = (
        "第1—4页：1分钟说明教材小节，4分钟用NaCl问题导入并讲解定义表、记录条件，"
        "1分钟独立辨析CO₂，2分钟反馈并记录离子来源。"
    )
    for rows in (c["slides"], c["activities"], c["lesson_stages"]):
        assert sum(row["minutes"] for row in rows) == 40
    c["candidate_id"] = "PREPCAND-" + _canonical_candidate_digest(c)[:32]
    return c


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("Use a new output directory")
    if digest(INPUT) != INPUT_SHA or any(digest(p) != h for p, h in EXPECTED.items()):
        raise RuntimeError("Source changed; review required")
    candidate = chapter_notes_candidate(json.loads(INPUT.read_text("utf-8")))
    BundledArtifactRenderer(args.artifact_python).render(
        candidate,
        output_kind="linked_bundle",
        output_dir=output,
        report_progress=lambda *a: None,
        is_cancelled=lambda: False,
        image_data={candidate["image_assets"][0]["asset_id"]: FIGURE.read_bytes()},
    )
    receipt = {
        "revision": "20260909-chapter-textbook-notes-r6",
        "method": "assistant_authored_offline_revision",
        "new_provider_calls": 0,
        "source_sha256": {p.name: h for p, h in EXPECTED.items()},
        "original_sources_unchanged": all(digest(p) == h for p, h in EXPECTED.items()),
        "original_candidate_unchanged": digest(INPUT) == INPUT_SHA,
        "quote_source": {"printed_page": 57, "text": ELECTROLYSIS_QUOTE},
        "slides": 16,
        "minutes": 40,
        "teacher_approval": False,
        "visual_review": "pending",
    }
    (output / "revision-provenance.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
