"""Source-bound local revision of one frozen v15 lesson; no provider calls.

The returned model candidate and its rendered artifacts remain unchanged.
This is an assistant-authored teaching candidate, not teacher approval.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from verify_real_source_preparation import (
    EXPECTED,
    FIGURE,
    ROOT,
    BundledArtifactRenderer,
    digest,
)

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_candidate,
)

SOURCE = ROOT / "runtime/deeptutor_shchem/qa/real-source-preparation-v15-20260909-r1"
RETURNED_SHA = "682cd7ac714253f0e67d141ae607d32b66da984f924050796de8d619ea36cb18"
QUOTE = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
BOOK_REF = "沪科技化学必修第一册，印刷56—58页（PDF61—63页）"


def comparison(columns, rows):
    return {
        "kind": "comparison",
        "comparison": {
            "dimension_label": "记录项目",
            "columns": columns,
            "rows": [{"label": label, "values": values} for label, values in rows],
        },
        "steps": [],
    }


def added_slide(title, content, visual, activity, objectives, minutes, notes):
    return {
        "title": title,
        "purpose": "先作答后核对，以完整结论支持课堂记录。",
        "objective_numbers": objectives,
        "activity_numbers": [activity],
        "assessment_numbers": [activity],
        "minutes": minutes,
        "content": content,
        "visual": visual,
        "image": None,
        "teacher_notes": notes,
    }


def revised_raw(raw):
    revised = deepcopy(raw)
    slides = revised["slides"]
    revised["objectives"][1]["statement"] = (
        "能区分电离与电解质导电，说明自由移动离子及外加电场的作用"
    )
    revised["objectives"][2]["statement"] = "能按水溶液中的电离程度比较强、弱电解质"
    slides[0].update(activity_numbers=[1], objective_numbers=[2], minutes=1)
    slides[0]["content"] = [
        "第2章 海洋中的卤素资源",
        "2.2 氧化还原反应和离子反应",
        "高二复习 · 1课时",
    ]
    slides[1]["minutes"] = 2
    slides[1]["content"][2] = "先在学习单活动1写下初步解释，观察后再修正。"
    slides[1]["teacher_notes"] = (
        "学生先写一句初步解释，再听取两种回答。若答“必须有水”，追问熔融状态没有水为什么也能导电。暂不揭示结论。"
    )
    slides[2]["minutes"] = 3
    observation = added_slide(
        "观察核对：有离子不等于能导电",
        [
            "依据教材图2.14整理；记录三种状态的差别。",
            "表中“能导电”指具备导电能力，不表示此时已经形成电流。",
        ],
        comparison(
            ["离子状态", "导电能力"],
            [
                ("NaCl晶体", ["存在Na⁺、Cl⁻，但不能自由移动", "不能导电"]),
                ("NaCl水溶液", ["存在可自由移动的水合离子", "能导电"]),
                ("熔融NaCl", ["存在可自由移动的Na⁺、Cl⁻", "能导电"]),
            ],
        ),
        1,
        [2],
        2,
        "先请学生解释表中一行，再投影完整表供纠错。NaCl晶体不导电的原因是缺少可自由移动的离子，不把没有接电源当成固体特有的原因。仅用教材图讨论，不安排熔融实验。",
    )
    slides[3]["minutes"] = 3
    slides[3]["content"] = [
        "依据教材及对应Word讲义整理：分类对象是化合物。",
        "电解质条件用“或”，非电解质条件用“和”；导电须由该化合物自身电离产生的离子引起。",
        "先判断学习单活动2的六种物质，下一页再核对。",
    ]
    # Keep the definition table complete but do not reveal the six judgments
    # before the separate classification feedback page.
    slides[3]["visual"]["comparison"]["rows"].pop()
    slides[3]["teacher_notes"] = (
        "先讲定义条件，不给六种待判物质的分类答案。学生在活动2独立判断并说明依据；"
        "收集两种回答，再切换第6页核对。"
    )
    classification = added_slide(
        "分类核对：判断必须说明依据",
        ["对应Word讲义考点一；以下为整理与解析，不是教材逐字原句。"],
        comparison(
            ["判断", "判断依据"],
            [
                (
                    "NaCl、NaOH、CH₃COOH",
                    ["电解质", "在水溶液中能自身电离；强、弱电解质都属于电解质"],
                ),
                ("蔗糖", ["非电解质", "不能自身电离产生自由移动离子"]),
                (
                    "SO₂、CO₂",
                    ["非电解质", "溶于水发生反应，导电离子并非由原化合物自身电离产生"],
                ),
            ],
        ),
        2,
        [1],
        3,
        "先请学生说明各组依据，再显示完整表。纠错关键：溶液能导电不能直接推出溶质是电解质。单质、混合物不属于这两类化合物。将判断写入学习单，不必抄整段解析。",
    )
    slides[4]["title"] = "知识点2：电离与电解质导电"
    slides[4]["content"] = [
        f"教材原文（印刷57页／PDF62页）：{QUOTE}",
        "以下仅比较电解质：电离形成自由移动离子；离子定向移动才形成电流。",
    ]
    slides[4]["visual"] = comparison(
        ["电离", "电解质导电"],
        [
            ("微观变化", ["形成可以自由移动的离子", "离子在电场作用下定向移动"]),
            (
                "条件",
                [
                    "水溶液中或熔融状态下；不需要通电",
                    "有自由移动离子；本课装置外接电源并构成闭合回路",
                ],
            ),
            (
                "联系与区别",
                ["为电解质导电提供自由移动离子", "电离本身不等于已经产生电流"],
            ),
        ],
    )
    slides[4]["teacher_notes"] = (
        "留1分钟摘录教材原句，圈出“或”“自由移动”。不能把自由离子说成所有导体的共同载流粒子；金属导电依靠自由电子。NaCl晶体即使接入电路也不能导电，关键是离子不能自由移动。"
    )
    slides[5]["content"] = [
        "依据教材印刷58页整理：强、弱电解质按在水溶液中的电离程度区分。",
        "不能用溶解度、溶液导电强弱或键极性大小直接代替电离程度。",
    ]
    slides[5]["visual"] = comparison(
        ["强电解质", "弱电解质"],
        [
            ("水溶液中", ["全部电离", "部分电离"]),
            ("方程式符号", ["=", "⇌（可逆符号）"]),
            (
                "所举溶质的存在形式",
                ["以NaCl为例：Na⁺、Cl⁻", "以CH₃COOH为例：分子与电离出的离子共存"],
            ),
            ("本课实例", ["NaCl、HCl、NaOH", "CH₃COOH"]),
        ],
    )
    slides[5]["teacher_notes"] = (
        "表中仅讨论所举溶质的电离，不是完整溶液微粒清单；不展开盐类水解等后续内容。学生在活动2记录“全部/部分”“=/⇌”，并口述醋酸分子和离子共存的原因。留1分钟记录。"
    )
    slides[6]["minutes"] = 3
    slides[6]["content"] = [
        "以下均为水溶液中的电离。强电解质用“=”：",
        "氯化钠：NaCl = Na⁺ + Cl⁻；氯化氢：HCl = H⁺ + Cl⁻。",
        "氢氧化钠：NaOH = Na⁺ + OH⁻。",
        "弱电解质用“⇌”。醋酸：CH₃COOH ⇌ H⁺ + CH₃COO⁻。",
        "书写后检查：原子守恒、电荷守恒。",
    ]
    slides[7]["minutes"] = 4
    slides[7]["content"][1] = "Ba(OH)₂（氢氧化钡）、Na₂SO₄（硫酸钠）、BaCl₂（氯化钡）"
    slides[7]["content"][2] = (
        "再写出CH₃COOH（醋酸）的电离方程式；逐式检验原子与电荷守恒。"
    )
    slides[8]["content"] = [
        "水溶液中：氢氧化钡 Ba(OH)₂ = Ba²⁺ + 2OH⁻",
        "硫酸钠 Na₂SO₄ = 2Na⁺ + SO₄²⁻",
        "氯化钡 BaCl₂ = Ba²⁺ + 2Cl⁻",
        "醋酸 CH₃COOH ⇌ H⁺ + CH₃COO⁻",
        "以Na₂SO₄为例：Na、S、O原子数守恒；右侧电荷2×(+1)+(−2)=0。",
    ]
    slides[9]["minutes"] = 2
    slides[9]["content"] = [
        "为什么NaCl固体不导电，而它的水溶液或熔融状态能导电？",
        "在学习单活动4先独立写1—2句话，区分“有离子”“离子自由移动”和“形成电流”。",
        "回答后再看下一页，补全自己的笔记。",
    ]
    slides[9]["teacher_notes"] = (
        "先独立回答再反馈。预设：晶体中离子不能自由移动；溶于水或熔融后形成自由移动离子，具备导电能力；在外加电场下离子定向移动可形成电流。追问只答“有离子”的学生。"
    )
    summary = added_slide(
        "课堂笔记：概念、条件与书写规则",
        ["依据本课教材与讲义整理。先核对自己的回答，再记录下表关键词。"],
        comparison(
            ["应记录的结论", "易错提醒"],
            [
                (
                    "电解质分类",
                    [
                        "化合物；水溶液或熔融状态能导电",
                        "由自身电离产生离子；单质、混合物不在分类内",
                    ],
                ),
                (
                    "电离与导电",
                    [
                        "电离形成自由移动离子；离子定向移动形成电流",
                        "NaCl晶体已有离子，但不能自由移动",
                    ],
                ),
                (
                    "强、弱电解质",
                    ["水溶液中全部／部分电离", "判断看电离程度，不直接看导电强弱"],
                ),
                (
                    "电离方程式",
                    [
                        "NaCl = Na⁺ + Cl⁻；CH₃COOH ⇌ H⁺ + CH₃COO⁻",
                        "强用=、弱用⇌；原子与电荷均守恒",
                    ],
                ),
            ],
        ),
        4,
        [1, 2, 3, 4, 5],
        5,
        "前1分钟核对开场问题，随后3分钟让学生在活动4补全关键词和一个典型方程式，最后1分钟同桌互查并布置课后补全。不要要求全班在5分钟重抄所有知识表；已有记录只补缺项。",
    )
    revised["slides"] = (
        slides[:3] + [observation, slides[3], classification] + slides[4:] + [summary]
    )
    for slide in revised["slides"]:
        slide["teacher_notes"] += (
            f" 来源：{BOOK_REF}；对应Word讲义考点一（区块42—63，缺失对象未补写）。"
        )

    activities = revised["activities"]
    activities[0].update(
        minutes=8,
        teacher_action="第1—4页：交代章节，先写初步解释，再观察图2.14、填表并核对。不开设熔融实验。",
        student_action="在活动1写初步解释、填三种状态比较表，并根据第4页修正。",
    )
    activities[0]["worksheet"]["instructions"] = [
        "先写初步解释，再观察教材图2.14并填表。",
        "表中判断导电能力，不把离子示意图当成实测电流记录。",
    ]
    activities[0]["worksheet"]["sections"][0]["columns"][-1] = "是否具备导电能力"
    activities[1].update(
        minutes=15,
        teacher_action="第5—8页：先判别六种物质，再核对分类；摘录电离原句，比较电离与电解质导电、强与弱电解质。每个知识表给出记录时间。",
        student_action="在活动2分类并说明依据；摘录原句、记录强弱判据及符号；不能只抄物质类别。",
    )
    activities[1]["worksheet"] = {
        "title": "概念判断与关键笔记",
        "instructions": [
            "先判断，再看PPT核对；每组中的物质分别判断。",
            "记录时抓住定义条件、微观区别和方程式符号。",
        ],
        "sections": [
            {
                "heading": "分类判断",
                "prompt": "判断下列物质是否为电解质，写出自身能否电离的依据。",
                "response_kind": "table",
                "response_lines": 0,
                "columns": ["物质", "判断", "依据"],
                "row_labels": ["NaCl、NaOH、CH₃COOH", "蔗糖", "SO₂、CO₂"],
            },
            {
                "heading": "电离定义与导电区别",
                "prompt": "摘录第7页教材电离原句，圈出“或”“自由移动”；再用一句话区分电离与电解质导电。",
                "response_kind": "lines",
                "response_lines": 3,
                "columns": [],
                "row_labels": [],
            },
            {
                "heading": "强弱电解质的记录",
                "prompt": "据第8页记录：比较条件（水溶液中）、强/弱的电离程度、方程式符号，以及醋酸溶液中所举溶质的存在形式。",
                "response_kind": "lines",
                "response_lines": 3,
                "columns": [],
                "row_labels": [],
            },
        ],
    }
    activities[2].update(
        minutes=10,
        teacher_action="第9页示范3分钟；第10页独立书写4分钟；第11页核对3分钟。核对系数、离子符号和守恒，不提前投影答案。",
    )
    activities[3].update(
        minutes=7,
        teacher_action="第12页先独立回扣2分钟；第13页核对、补笔记与互查5分钟。按已有记录补缺，不重新抄全表。",
        student_action="先写回扣问题的回答，再在活动4整理四类结论与一个典型方程式，用不同符号标记改正处。",
    )
    activities[3]["worksheet"]["instructions"] = [
        "先独立回答开场问题，再看PPT第13页核对。",
        "已有笔记只补缺项；写清概念之间的联系，不画无说明的箭头。",
    ]
    activities[3]["worksheet"]["sections"][0]["heading"] = "一页笔记与课末回扣"
    activities[3]["worksheet"]["sections"][0]["prompt"] = (
        "先用1—2句话解释NaCl不同状态的导电能力，再记录：分类条件、电离与电解质导电、强弱电解质判据、一个典型方程式。最后标出你本节课改正的一处认识。"
    )

    stages = revised["lesson_stages"]
    stages[0].update(
        minutes=3,
        teacher_action="第1页用1分钟交代章节范围，第2页用2分钟提出NaCl导电问题；让学生在学习单活动1写一句初步解释，听取两种回答，暂不纠错。",
        student_action="在活动1写初步解释，比较晶体、水溶液与熔融状态。",
    )
    stages[1].update(
        minutes=5,
        teacher_action="第3页观察图2.14并填写三种状态表，3分钟；第4页逐行核对，2分钟。提示图示提供微观解释，并非实测电流。",
        student_action="独立填表，同桌解释，再根据完整表修正。",
    )
    stages[2].update(
        teacher_action="第5页用3分钟讲分类条件并让学生判断六种物质；第6页用3分钟核对。SO₂、CO₂溶于水发生反应，不能因所得溶液能导电就判原化合物为电解质。",
        student_action="填写活动2分类表中的三组物质及依据，核对后修改。",
    )
    stages[3].update(
        teacher_action="第7页先摘录教材原句并圈关键词，约1分钟；再比较电离与电解质导电。强调电离不需要通电，自由移动离子在电场作用下定向移动形成电流；金属导电不以自由离子为载流粒子。",
        student_action="在活动2摘录电离原句，写出电离与电解质导电的区别。",
    )
    stages[4].update(
        teacher_action="第8页按水溶液中的电离程度比较强弱电解质。说明表中微粒是所举溶质的存在形式，不是完整微粒清单。留1分钟记录判据与符号；请学生解释醋酸为何分子和离子共存。",
        student_action="在活动2记录全部/部分电离、=/⇌及醋酸的存在形式。",
    )
    stages[5].update(
        minutes=10,
        teacher_action=activities[2]["teacher_action"]
        + " 示例与练习均在水溶液条件下；以Na₂SO₄检验2×(+1)+(−2)=0，并检查Na、S、O原子数。",
    )
    stages[6].update(
        minutes=7,
        teacher_action=activities[3]["teacher_action"],
        student_action=activities[3]["student_action"],
        assessment="检查回答区分已有离子、自由移动离子、定向移动电流；笔记覆盖四类结论且保留条件。收集未解决的问题，不声称已达成目标。",
    )
    revised["assessments"][1]["success_criteria"] = [
        "能按自身电离判断六种化合物，并说明SO₂、CO₂例外的原因",
        "能区分电离与电解质导电，知道电离不需要通电",
        "能在水溶液条件下说出强弱判据及=/⇌符号",
    ]
    revised["assessments"][3]["success_criteria"][1] = (
        "笔记区分电离形成自由移动离子与离子定向移动形成电流"
    )
    revised["homework"]["tasks"][0]["instruction"] = (
        "补全活动4的一页笔记，保留分类条件、教材电离原句、强弱判据和典型方程式；已有记录不重复抄写。"
    )
    revised["uncertainties"] = [
        {
            "field": "来源与修订身份",
            "description": "本稿由助手根据冻结的v15模型稿另存修订，教材PDF61—63页及图2.14已经助手逐页对照，不代表真人教师审核。只有电离定义标作逐字原句，其余知识表是教材/讲义整理。",
            "teacher_action": "对照所用教材版本核对术语与适用范围，批准后再授课。",
        },
        {
            "field": "Word提取缺口",
            "description": "Word区块42—63有缺失公式/图片/域代码；本稿没有猜补。方程式采用已对照的教材印刷57—58页；未采用键极性直接判强弱。",
            "teacher_action": "如增加弱碱、多元酸、酸式盐等例子，先另核教材和原始Word对象。",
        },
        {
            "field": "课堂节奏",
            "description": "13页、40分钟是设计预算；采用4分钟独立书写，并在概念页和结尾留记录时间。实际学情、投影后排可读性和课堂效果未验证。",
            "teacher_action": "试讲时检查四个方程式的书写时间；必要时减少题量或延长课时，不压缩必要的思考和记录。",
        },
    ]
    return revised


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    if output.exists():
        raise RuntimeError("Use a new output directory")
    source = SOURCE / "returned-candidate.json"
    if digest(source) != RETURNED_SHA or any(
        digest(p) != h for p, h in EXPECTED.items()
    ):
        raise RuntimeError("Frozen source changed")
    files = [p for p in SOURCE.iterdir() if p.is_file()]
    before = {str(p): digest(p) for p in files}
    raw = json.loads(source.read_text("utf-8"))
    brief = json.loads((SOURCE / "teacher-brief.json").read_text("utf-8"))
    revised = revised_raw(raw)
    candidate = normalize_preparation_candidate(revised, brief)
    assert len(candidate["slides"]) == 13
    assert candidate["slides"][0]["title"] == "电解质的电离"
    assert QUOTE in candidate["slides"][6]["content"][0]
    assert not any(u["field"] == "timing_alignment" for u in candidate["uncertainties"])
    assert all(
        sum(x["minutes"] for x in candidate[k]) == 40
        for k in ("slides", "activities", "lesson_stages")
    )
    BundledArtifactRenderer(args.artifact_python).render(
        candidate,
        output_kind="linked_bundle",
        output_dir=output,
        report_progress=lambda *_args: None,
        is_cancelled=lambda: False,
        image_data={candidate["image_assets"][0]["asset_id"]: FIGURE.read_bytes()},
    )
    (output / "revised-raw-candidate.json").write_text(
        json.dumps(revised, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    receipt = {
        "method": "assistant_source_bound_revision_not_original_model_output",
        "source_returned_candidate_sha256": RETURNED_SHA,
        "new_model_calls": 0,
        "original_artifacts_unchanged": all(
            digest(Path(p)) == h for p, h in before.items()
        ),
        "source_files_unchanged": all(digest(p) == h for p, h in EXPECTED.items()),
        "slide_count": 13,
        "designed_minutes": 40,
        "teacher_approval": False,
        "classroom_validation": False,
        "visual_review": "pending",
    }
    (output / "revision-verification.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
