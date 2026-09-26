"""Offline, source-preserving course composition exemplar; no provider calls."""

import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    normalize_preparation_candidate,
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_pedagogy import (
    teacher_design_starter,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    _draw_layout,
    _FontBook,
    _make_layouts,
    _save_png,
    _write_pptx,
)

SOURCE = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读讲练版-r9"
OUT = ROOT / "outputs/备课/2026-09-09-电解质的电离-课程重组版-r10/投影核对版"
QA = ROOT / "runtime/deeptutor_shchem/qa/course-composition-r10-20260909/refined"


def table(columns, rows):
    return {
        "kind": "comparison",
        "steps": [],
        "comparison": {
            "dimension_label": "对象或条件",
            "columns": columns,
            "rows": [{"label": label, "values": values} for label, values in rows],
        },
    }


def compose(raw):
    old = {i: deepcopy(s) for i, s in enumerate(raw["slides"], 1)}

    def page(title, content, *, base=2, visual=None):
        s = deepcopy(old[base])
        s.update(title=title, content=content, visual=visual, image=None)
        return s

    # The original textbook's figure 2.13 is KNO3, not sugar or NaCl.
    old[4]["content"] = [
        "教材图2.13用硝酸钾 KNO₃。",
        "不加水，只改变物质状态，能否导电？",
    ]
    old[4]["image"]["observation_prompt"] = (
        "读装置和教材说明：改变了什么条件？不要把加热当作通电的作用。"
    )
    observation = page(
        "两组证据：水并不是唯一条件",
        ["依据教材56页整理：现象先说明条件，原因还需用微观模型解释。"],
        visual=table(
            ["教材结论", "本课追问"],
            [
                ("硝酸钾固体", ["通常条件下不导电", "固体里没有离子吗？"]),
                ("硝酸钾水溶液", ["能够导电", "水使粒子发生什么变化？"]),
                ("硝酸钾熔融状态", ["能够导电", "没有水，为什么也能导电？"]),
            ],
        ),
    )
    old[12]["content"] = [
        "转看NaCl：晶体中原来就有离子吗？",
        "比较溶于水与熔融两条路径，找出共同变化。",
    ]
    old[11]["content"] = [
        "教材摘录：电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
    ]
    old[5]["content"] = [
        "读教材原句，圈出‘化合物’及‘或／和’。",
        "根据电离解释，再判断物质类别。",
    ]
    old[6]["content"] = ["依据教材与讲义整理：先判对象，再判规定条件下能否自身电离。"]
    old[13]["content"] = [
        "依据教材57页整理：有离子不等于离子能自由移动。",
        "电离产生自由移动的离子；导电还需要外接电源与闭合通路。",
    ]
    old[10]["content"] = [
        "讲义参考答案：B，冰醋酸（纯净的醋酸）属于电解质。",
        "先判对象：黄酒、漂白粉是混合物，不纳入这两类。",
        "再判自身电离：醋酸在水溶液中电离；乙醇是非电解质。",
        "方法：不能仅凭当前样品的导电性给物质分类。",
    ]
    opening = page(
        "先判断，再用两条证据修正",
        [
            "NaCl晶体不导电，能否据此判定它不是电解质？",
            "先写下判断和理由，课末用教材图和定义重新回答。",
            "本课任务：解释为什么导电，并区分‘物质类别’与‘样品状态’。",
        ],
    )
    exit1 = page(
        "回到开场：证据是否改变了你的判断？",
        [
            "独立回答：NaCl晶体不导电，能否据此否定其电解质身份？",
            "理由须同时包含：定义中的条件、晶体中离子的移动能力。",
            "对照开场作答，圈出需要改正或补充的一处理由。",
        ],
    )
    first = [
        old[1],
        opening,
        old[3],
        old[4],
        observation,
        old[12],
        old[11],
        old[13],
        old[5],
        old[6],
        old[7],
        old[8],
        old[9],
        old[10],
        old[14],
        old[15],
        old[37],
        old[38],
        exit1,
        old[42],
    ]
    first_minutes = [1, 2, 2, 1, 2, 3, 1, 3, 1, 3, 1, 2, 2, 3, 2, 2, 3, 1, 3, 2]

    second_start = page(
        "第二课时：电离为什么用不同符号？",
        [
            "回顾：电离解决‘有没有自由移动的离子’。",
            "继续追问：进入水中的电解质是否都全部电离？",
            "今天把电离程度转化为判断依据和规范的电离方程式。",
        ],
    )
    strong_text = page("教材原句：区别在水溶液中的电离程度", old[16]["content"][:2])
    old[16]["content"] = [
        "读教材58页原句：找出‘全部’与‘部分’。",
        "本页只比较电离程度，不比较灯泡亮暗。",
    ]
    old[17]["content"] = ["据教材整理：先确定电离程度，才能选择完全电离或可逆表达。"]
    old[18]["content"] = [
        "据教材与讲义归纳；本表描述目标溶质，不是整个水溶液的全部微粒。"
    ]
    old[25]["content"] = ["把强弱判据用于书写：离子怎么写，符号怎么选，最后怎样检查？"]
    old[26]["title"] = "主讲示范：硫酸铵怎样检查电荷与原子？"
    old[26]["content"] = [
        "水溶液中的硫酸铵：(NH₄)₂SO₄＝2NH₄⁺＋SO₄²⁻。",
        "先判类别：盐；NH₄⁺、SO₄²⁻保留为整体。",
        "再核对：右侧总电荷为2×(+1)+(−2)=0；N、H、S、O原子数分别相等。",
        "迁移到教材书写题：系数与电荷不能混为一谈。",
    ]
    old[27]["content"][-1] = "来源：教材印刷57页‘书写表达’三题。独立作答，下一页核对。"
    old[28]["content"] = old[28]["content"][:4]
    old[31]["content"] = [
        "观察教材图2.15：反应前后微粒怎样变化？",
        "先读图，再比较下一页的两种表达。",
    ]
    water_feedback = page(
        "水的电离：模型与简写如何对应？",
        [
            "图示表达：2H₂O ⇌ H₃O⁺＋OH⁻。",
            "教材书写任务的简写：H₂O ⇌ H⁺＋OH⁻。",
            "H⁺是水溶液中氢离子的简写，不表示裸质子独立存在。",
        ],
    )
    old[39]["content"][-1] = (
        "据教材58页两例式及水的书写任务整理；这次不看前页独立复写。"
    )
    old[40]["content"] = old[40]["content"][:4]
    old[30]["title"] = "讲义补充：多元弱酸为什么分步写？"
    old[30]["visual"]["comparison"]["rows"] = old[30]["visual"]["comparison"]["rows"][
        :2
    ]
    old[30]["content"] = ["依据讲义5页整理：逐步写出失去一个H⁺后的酸根，保留可逆符号。"]
    exit2 = page(
        "用同一套方法完成整课回扣",
        [
            "不看笔记复述：先判研究对象，再看状态条件，再判电离程度，最后规范表达。",
            "选本课一道错题，说出错误发生在哪一步、依据哪条条件改正。",
            "接下来只对照总结补缺，不重新抄完整张表。",
        ],
    )
    old[44]["visual"]["comparison"]["rows"] = old[44]["visual"]["comparison"]["rows"][
        :2
    ]
    old[44]["content"] = [
        "据讲义考向整理；酸式盐和溶解平衡辨析列为可选拓展，不占本次80分钟。"
    ]
    second = [
        second_start,
        old[16],
        strong_text,
        old[17],
        old[19],
        old[20],
        old[21],
        old[22],
        old[18],
        old[25],
        old[26],
        old[27],
        old[28],
        old[29],
        old[31],
        water_feedback,
        old[39],
        old[40],
        old[30],
        old[33],
        old[34],
        exit2,
        old[43],
        old[44],
    ]
    second_minutes = [
        1,
        1,
        2,
        1,
        2,
        3,
        1,
        1,
        2,
        1,
        2,
        3,
        2,
        2,
        1,
        1,
        2,
        2,
        2,
        2,
        3,
        1,
        1,
        1,
    ]
    assert sum(first_minutes) == sum(second_minutes) == 40
    c = deepcopy(raw)
    c["slides"] = first + second
    for slide, minute in zip(c["slides"], first_minutes + second_minutes, strict=True):
        slide["minutes"] = minute
    c["title"] = "电解质的电离｜两课时课程重组"
    c["objectives"][3]["statement"] = (
        "能依电离程度写基本电离式，检查离子整体、符号、原子与电荷；辨析多元弱酸分步。"
    )
    groups = [
        (
            0,
            8,
            [2],
            "第一课时｜从导电现象到微观解释",
            "教材56—57页图2.12—2.14及电离定义",
            "比较三种状态，说明自由移动离子如何形成。",
            "能区分已有离子与自由移动离子；将电离与通电导电分开。",
            "若把不导电解释成没有离子，回到晶体图中找离子；若认为通电才电离，比较两条电离路径。",
        ),
        (
            8,
            16,
            [1, 2],
            "第一课时｜把解释转为分类方法",
            "教材56页定义；讲义3—4页总结、6—7页分类及状态题",
            "圈定义条件；跟随Q3主讲过程，独立完成Q4并写理由。",
            "先区分化合物与混合物、单质，再判断规定条件下的自身电离。",
            "Q3按对象—条件—自身电离示范；Q4从物质分类转向状态条件，不提示选项答案。",
        ),
        (
            16,
            20,
            [1, 2],
            "第一课时｜读图迁移与出口检查",
            "讲义8页三状态图；教材56—57页",
            "Q7独立从图找证据，再修正开场判断，对照总表补记。",
            "不看答案解释NaCl晶体不导电但仍是电解质，并指出图中依据。",
            "若只报AC，追问每个判断的图中证据；出口回答需要类别与状态两条理由。",
        ),
        (
            20,
            29,
            [3],
            "第二课时｜电离程度与分类边界",
            "教材58页强弱原句；讲义4—6页比较、例1及氯化铵变式",
            "圈全部/部分，跟随Q1逐项排除，独立完成Q2再整理类型表。",
            "用电离程度判断，不以难溶、导电亮暗或弱碱对应盐代替。",
            "Q1主讲展示各选项为何排除；Q2减少支架，检验能否区分盐与相关弱碱。",
        ),
        (
            29,
            38,
            [4],
            "第二课时｜把概念转为符号",
            "教材57—58页示例、书写任务与图2.15；讲义5页书写方法",
            "跟随硫酸铵示范，独立写Q8前3式；读水图后独立复写后三式。",
            "强弱符号、离子整体、系数、原子和电荷均正确，能说明水两种表达的联系。",
            "前三式迁移盐/强碱的完整表达；后三式检验弱电解质的符号与整体性，不在作答时提示答案。",
        ),
        (
            38,
            44,
            [1, 2, 3, 4],
            "第二课时｜纠错与关系收束",
            "讲义5页多元弱酸、7页例2考查点；教材56—58页",
            "用H₂S两步式补全规则，独立纠错Q5后讲评，最后复述判断链并查漏。",
            "不只改符号，要说出错因；能把分类、条件、电离程度与书写步骤联系起来。",
            "Q5为据讲义整理，不是缺字原题原样复制；未答对分步时回到H₂S逐次失去H⁺。",
        ),
    ]
    c["activities"], c["assessments"], c["lesson_stages"] = [], [], []
    for gi, (
        start,
        end,
        objectives,
        title,
        source,
        action,
        criteria,
        feedback,
    ) in enumerate(groups, 1):
        duration = sum(s["minutes"] for s in c["slides"][start:end])
        c["activities"].append(
            {
                "title": title,
                "objective_numbers": objectives,
                "minutes": duration,
                "teacher_action": feedback,
                "student_action": action,
                "materials": [source],
                "worksheet": None,
            }
        )
        c["assessments"].append(
            {
                "title": title + "检查",
                "objective_numbers": objectives,
                "activity_numbers": [gi],
                "evidence_of_learning": action,
                "success_criteria": [criteria],
            }
        )
        c["lesson_stages"].append(
            {
                "title": title,
                "objective_numbers": objectives,
                "activity_numbers": [gi],
                "assessment_numbers": [gi],
                "minutes": duration,
                "teacher_action": feedback,
                "student_action": action,
                "materials": [source],
                "assessment": criteria,
            }
        )
        for index in range(start, end):
            s = c["slides"][index]
            s["objective_numbers"], s["activity_numbers"], s["assessment_numbers"] = (
                objectives,
                [gi],
                [gi],
            )
            s["purpose"] = (
                title
                + "；"
                + (
                    "学生独立作答，后续反馈"
                    if "Q" in s["title"] and "核对" not in s["title"]
                    else "依据本页图文推进解释、方法或笔记"
                )
            )
            s["teacher_notes"] = (
                f"{title}，本页{index + 1}，预算{s['minutes']}分钟。来源：{source}。\n{feedback}\n学生产出：{action}\n检查：{criteria}\n投影与板书：保留本段核心关系；本页正文或表格作为笔记依据，讲解后停顿整理。预设反应不是实际课堂结果。"
            )
        c["slides"][start]["teacher_notes"] += (
            f"\n课程段：{title}。承接上一段结论并解决本段问题；采用{source}；学生产出：{action}；进入下一段标准：{criteria}。"
        )
    c["lesson_stages"][0]["materials"].append(
        "课程取舍：第一课时现象—解释—分类，第二课时程度—书写—纠错；酸式盐、溶解平衡细辨及H₂O₂列课后可选，不把Word全章挤进80分钟。"
    )
    c["slides"][3]["teacher_notes"] += (
        "\n图2.13仅作教材图文分析，KNO₃熔融导电结论来自教材56页正文；不开展熔盐操作。旧版糖/食盐对象说明已纠正。"
    )
    c["slides"][16]["teacher_notes"] += (
        "\n独立读图3分钟，再看下一页反馈；指出为什么原选项B、D不成立。"
    )
    c["slides"][18]["teacher_notes"] += (
        "\n出口参考：不能。NaCl满足规定状态下能导电的化合物条件；晶体中的离子不能自由移动，不否定其电解质类别。"
    )
    c["slides"][19]["content"] = [
        "第一课时笔记核对：分类看规定条件，导电看当前状态。用已有笔记补缺。"
    ]
    c["homework"] = {
        "title": "课后订正与可选拓展",
        "estimated_minutes": 10,
        "tasks": [
            {
                "instruction": "订正课堂Q3、Q4、Q7、Q1、Q2、Q5及Q8，每处错题写出判断依据。",
                "objective_numbers": [1, 2, 3, 4],
            },
            {
                "instruction": "可选：阅读原讲义5页酸式盐的条件对照、8页难溶盐辨析；先注明电离表达或溶解平衡语境，再与教师核对。未学相关前置知识者不做。",
                "objective_numbers": [3, 4],
            },
        ],
    }
    c["uncertainties"].append(
        {
            "field": "course_composition_r10",
            "description": "本版是离线课程重组，不是v20模型输出；未制作同步学习单，不应混用R9学习单。酸式盐等原讲义内容转为可选。",
            "teacher_action": "依据班级前置知识审阅80分钟分配，试教后调整；有明确必讲酸式盐要求时重新分配而非直接追加。",
        }
    )
    # Final projection review: retain source identity in notes, keep tasks legible.
    c["slides"][5]["image"]["observation_prompt"] = "图2.14：找离子，比较两条路径。"
    c["slides"][8]["image"]["observation_prompt"] = "教材定义：对象与条件。"
    c["slides"][21]["image"]["observation_prompt"] = "教材定义：全部与部分。"
    original_question = deepcopy(c["slides"][16])
    c["slides"][16]["minutes"] = 1
    c["slides"][16]["content"] = [
        "观察三种状态，下一页据图判断。",
        "X接电源正极，Y接电源负极。",
    ]
    c["slides"][16]["image"]["observation_prompt"] = "先辨认图a、b、c中的粒子与状态。"
    original_question.update(
        image=None,
        minutes=2,
        title="Q7 据图独立判断（承接前页）",
        content=[
            "选择正确说法，并用前页图中证据说明理由。",
            "A．图a中所含微粒为离子。",
            "B．氯化钠只有在通电条件下才能电离。",
            "C．图b表示熔融状态下氯化钠的导电过程。",
            "D．氯化钠在图示三种不同状态下均能导电。",
        ],
    )
    c["slides"].insert(17, original_question)
    for i, s in enumerate(c["slides"], 1):
        s["title"] = re.sub(r"^K[1-6] ", "", s["title"])
        visible = []
        for line in s["content"]:
            if line.startswith("来源："):
                s["teacher_notes"] += "\n" + line
                continue
            line = re.sub(r"^[1-6]．", "", line)
            visible.append(line)
        s["content"] = visible
        s["teacher_notes"] = re.sub(
            r"本页\d+，预算\d+分钟",
            f"本页{i}，预算{s['minutes']}分钟",
            s["teacher_notes"],
        )
    c["slides"][16]["teacher_notes"] += (
        "\n图页观察1分钟，题干页2分钟，需要时返回本图；下一题干页结束后才讲评。"
    )
    c["slides"][17]["teacher_notes"] += "\n题图见前页，必要时返回图页供学生取证。"
    # Numbered answer labels should not receive a second automatic number.
    for n in (33, 38):
        for j, line in enumerate(c["slides"][n]["content"][:3]):
            c["slides"][n]["content"][j] = re.sub(r"^[1-6]．", "", line)
    return c


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    QA.mkdir(parents=True, exist_ok=False)
    source_data = (SOURCE / "edited-raw-candidate.json").read_bytes()
    raw = compose(json.loads(source_data))
    brief = json.loads((SOURCE / "teacher-brief.json").read_text("utf-8"))
    brief["output_kind"] = "ppt"
    brief["advanced"]["template_and_delivery"] = (
        teacher_design_starter("复习")
        + "\n本次两课时：先现象—解释—分类，再程度—符号—纠错；第一课时21页、第二课时24页仅为本次编辑结果，不作为通用页数要求。酸式盐及溶解平衡细辨移到课后选学。"
    )
    brief["objective"] = (
        "两课时完整讲练：用教材图文解释导电和电离，依据讲义分类例题巩固判据，规范基本电离方程式并纠错；先保证独立作答和笔记，不展开酸式盐专题。"
    )
    canonical = normalize_preparation_candidate(
        raw, normalize_preparation_payload(brief)
    )
    assert [s["minutes"] for s in canonical["slides"]] == [
        s["minutes"] for s in raw["slides"]
    ]
    for name, data in [
        ("candidate.json", canonical),
        ("edited-raw-candidate.json", raw),
        ("teacher-brief.json", brief),
    ]:
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    assets = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读生成材料/assets"
    images = {
        "IMG-" + hashlib.sha256(p.read_bytes()).hexdigest(): p.read_bytes()
        for p in assets.glob("*.image")
    }
    layouts = _make_layouts(canonical, image_data=images)
    _write_pptx(OUT / "lesson_presentation.pptx", layouts, canonical, root=OUT)
    fonts = _FontBook()
    for i, layout in enumerate(layouts, 1):
        _save_png(_draw_layout(layout, fonts), QA / f"slide-{i:02d}.png", root=QA)
    (QA / "layout-report.json").write_text(
        json.dumps([l.public() for l in layouts], ensure_ascii=False, indent=2), "utf-8"
    )
    (OUT / "本次完整生成提示词.txt").write_text(_prompt(brief), "utf-8")
    guide = [
        "# 两课时课程重组说明",
        "",
        "这是离线编辑样课，不是v20提示词的真实模型生成结果；须教师复核和试教。",
        "",
        "第一课时40分钟，第二课时40分钟。PPT第22页开始第二课时。旧R9、教材及Word母文件均未修改。",
        "",
        "主要调整：教材现象与微观图提前形成解释，再做分类；Q3/Q1为主讲示范，Q4/Q2/Q7及书写任务承担独立迁移；定义图与可记录文字分工；纠正图2.13实验对象为KNO₃。",
        "",
        "酸式盐、难溶盐溶解平衡细辨、H₂O₂转课后可选；不等于删除原讲义。已保留讲义多元弱酸分步及Q5纠错。只交付PPT及本说明，不制作同步Word学习单，请勿混用R9学习单。",
        "",
        "常考总结指讲义列出的考向，不代表已核验考频；所有题目与答案按讲义/教材来源，不冒称官方。",
        "",
        "## 逐段授课安排",
        "",
    ]
    for stage in raw["lesson_stages"]:
        guide += [
            f"### {stage['title']}（{stage['minutes']}分钟）",
            "",
            f"学生：{stage['student_action']}",
            "",
            f"教师：{stage['teacher_action']}",
            "",
            f"检查：{stage['assessment']}",
            "",
        ]
    guide += ["## 逐页讲解与笔记", ""]
    for i, s in enumerate(raw["slides"], 1):
        guide += [
            f"### {i}. {s['title']}（{s['minutes']}分钟）",
            "",
            s["teacher_notes"],
            "",
        ]
    (OUT / "课程结构与授课说明.md").write_text("\n".join(guide), "utf-8")
    assert (SOURCE / "edited-raw-candidate.json").read_bytes() == source_data
    print(
        json.dumps(
            {
                "output": str(OUT),
                "slides": len(layouts),
                "period_minutes": [40, 40],
                "model_calls": 0,
                "office_visual_review": "pending",
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
