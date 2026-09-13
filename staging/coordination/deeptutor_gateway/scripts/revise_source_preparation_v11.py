"""Offline, explicitly authored revision of the frozen v11 real-model sample.

No provider access or source overwrite. This is NOT another model result and
NOT teacher approval. The production renderer handles the reviewed candidate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from verify_real_blueprint_preparation import ROOT, BundledArtifactRenderer
from verify_real_source_preparation import EXPECTED, FIGURE, digest

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    _canonical_candidate_digest,
)

INPUT = (
    ROOT
    / "runtime/deeptutor_shchem/qa/real-source-preparation-v11-20260909-r2-budget64k/candidate.json"
)
INPUT_SHA = "4792335db297e1363962c4a8b02b227153addbc23e86aa1d1d7ec05ae0241911"
SOURCE_CONCEPT = "依据：PKG-032解析版Word区块42—45、49；TB-M1-C2-S22-C05；教材印刷56—57页。区块不是页码。"
SOURCE_MICRO = "依据：TB-M1-C2-S22-C06；教材印刷57页图2.14及外接电源、闭合通路说明。未恢复Word缺失对象。"
SOURCE_STRONG = "依据：Word区块50—57与TB-M1-C2-S22-C07；教材印刷58页。删除讲义按键极性概括强弱的维度，依据电离程度。"
SOURCE_EQUATION = "依据：Word区块58—63仅作范围参考，缺失公式未恢复；教材印刷57页三个书写任务及58页醋酸例式。答案为本次依据教材规则书写的讲解，不冒称官方答案。"


def lines(heading, prompt, count):
    return {
        "heading": heading,
        "prompt": prompt,
        "response_kind": "lines",
        "response_lines": count,
        "columns": [],
        "row_labels": [],
    }


def table_section(heading, prompt, columns, rows):
    return {
        "heading": heading,
        "prompt": prompt,
        "response_kind": "table",
        "response_lines": 0,
        "columns": columns,
        "row_labels": rows,
    }


def comparison(columns, rows, label="比较维度"):
    return {
        "kind": "comparison",
        "comparison": {
            "dimension_label": label,
            "columns": columns,
            "rows": [{"label": key, "values": values} for key, values in rows],
        },
        "steps": [],
    }


def revised_candidate(original):
    c = copy.deepcopy(original)
    c["title"] = "电解质与电离方程式｜课堂修订候选"
    c["objectives"] = [
        {
            "id": "O01",
            "statement": "依据化合物、规定状态和自身电离三个判断关注点辨析电解质；用微观模型区分电离与导电。",
        },
        {
            "id": "O02",
            "statement": "以水溶液中的电离程度区分强弱电解质，规范书写教材基础电离方程式并检查原子、电荷守恒。",
        },
        {
            "id": "O03",
            "statement": "分段记录概念关系，最后独立解释NaCl晶体与水溶液导电能力的差异，并说明CO₂辨析依据。",
        },
    ]
    a1, a2, a3 = c["activities"]
    a1.update(
        title="一、概念边界与微观解释",
        teacher_action="按第1—7页推进。先收集猜想，再圈定义条件；CO₂先判断后反馈。观察图2.14不先讲答案，随后区分产生自由移动离子与在闭合电路中定向移动。第7页停顿记录学习单第1页。",
        student_action="口头给出猜想，观察教材图，分段完成学习单第1页；以离子能否自由移动解释NaCl晶体与水溶液差别。",
        materials=["教材印刷56—57页及图2.14", "配套学习单第1页", "PPT第1—7页"],
    )
    a1["worksheet"] = {
        "title": "一、概念边界与微观解释",
        "instructions": [
            "姓名：________  班级：________  日期：________",
            "随第2、4、7页分段记录；先听解释，再补关键词。",
        ],
        "sections": [
            lines(
                "1. 判断对象与条件",
                "补全：电解质和非电解质都是________。电解质在________或________状态下，因自身电离而能导电；非电解质在这两种状态下均不能因自身电离而导电。",
                2,
            ),
            lines(
                "2. 辨析CO₂（二氧化碳）",
                "CO₂的水溶液能导电，为什么仍不能据此把CO₂判为电解质？请写出导电离子的来源。",
                2,
            ),
            table_section(
                "3. 电离与导电",
                "先用关键词比较，再补全离子状态。",
                ["比较对象", "发生的过程或离子状态", "所需条件或判断依据"],
                ["电离", "电解质溶液导电", "NaCl晶体与水溶液"],
            ),
            lines(
                "4. 回答开场问题",
                "不用照抄：为什么NaCl晶体不导电，而其水溶液能导电？用‘离子—自由移动—闭合电路’组织回答。",
                2,
            ),
        ],
    }
    a2.update(
        title="二、强弱比较与符号表达",
        teacher_action="第8页先填电离程度比较表，第9页核对；第10页示范NaCl与醋酸。第11页完成教材三题并遮屏重写醋酸；第12页逐项核对，第13页检查守恒。",
        student_action="学习单第2页填写比较表，写三个教材任务并回忆一个已讲例式；用不同颜色订正并标记错在符号、电荷或系数。",
        materials=["教材印刷57—58页", "配套学习单第2页", "PPT第8—13页"],
    )
    a2["worksheet"] = {
        "title": "二、强弱比较与符号表达",
        "instructions": [
            "第8页先填表，第9页核对；第11页独立写，第12—13页订正。",
            "先完成教材三题，再回忆重写已讲的醋酸例式。",
        ],
        "sections": [
            table_section(
                "1. 按电离程度比较",
                "只讨论水溶液中的电离程度；不以键极性或溶解度作强弱判据。",
                ["比较维度", "强电解质", "弱电解质"],
                ["电离程度", "书写符号", "本课代表物质"],
            ),
            lines(
                "2. 教材任务与例式回忆",
                "依次写氢氧化钡Ba(OH)₂、硫酸钠Na₂SO₄、氯化钡BaCl₂和已讲的醋酸CH₃COOH例式。每式一行、下一行订正并说明依据；全对则写守恒检查。",
                8,
            ),
        ],
    }
    a3.update(
        title="三、独立解释与离堂检验",
        teacher_action="第14页回看两页笔记并只补缺项；第15页暂时遮住笔记独立回答，第16页对照关键条件自评。若未掌握，登记为待补，不宣称全班达标。",
        student_action="补齐两页笔记，用自己的话回答开场问题和CO₂辨析，指出一个方程式的守恒检查方法。",
        materials=["已完成的两页学习单", "PPT第14—16页"],
    )
    a3.pop("worksheet", None)

    c["slides"] = []

    def slide(
        title,
        minutes,
        content,
        notes,
        source,
        activity="A01",
        objectives=None,
        assessment=None,
        visual=None,
        image=None,
    ):
        index = len(c["slides"]) + 1
        row = {
            "id": f"S{index:02}",
            "order": index,
            "title": title,
            "minutes": minutes,
            "content": content,
            "purpose": title,
            "teacher_notes": notes + "\n" + source,
            "activity_ids": [activity],
            "objective_ids": objectives
            or (
                ["O01"]
                if activity == "A01"
                else ["O02"]
                if activity == "A02"
                else ["O01", "O02", "O03"]
            ),
            "assessment_ids": assessment or [],
        }
        if visual is not None:
            row["visual"] = visual
        if image is not None:
            row["image"] = image
        c["slides"].append(row)

    slide(
        "NaCl不导电，还是能导电？",
        2,
        [
            "同一种物质，为什么氯化钠（NaCl）晶体不导电，而其水溶液能导电？",
            "今天沿着‘判类别—看微粒—写符号’寻找解释。",
        ],
        "等待20秒，听两种猜想而不公布答案。记录‘固体没有离子’等可能的误解，留到第5—7页用图核对。此处不让学生抄长段。",
        SOURCE_CONCEPT,
    )
    slide(
        "一、先确定判断对象和状态",
        3,
        [
            "电解质：在水溶液中或熔融状态下能导电的化合物。",
            "非电解质：在水溶液中和熔融状态下都不能导电的化合物。",
            "圈关键词：化合物；‘或’与‘和’。单质、混合物均不属于这两类。",
            "判断关注：导电离子是否来自该化合物自身电离？",
        ],
        "先读教材定义，再以NaCl作口头例证。最后留40秒补学习单第1页第1栏；强调不能拿‘固体此时不导电’否定物质类别。过渡：如果物质先与水反应，怎样判断？",
        SOURCE_CONCEPT,
    )
    slide(
        "先判断：CO₂属于电解质吗？",
        1,
        [
            "二氧化碳（CO₂）的水溶液能导电。",
            "这能否直接证明CO₂是电解质？",
            "先说判断，再说你还需要追问的条件。",
        ],
        "等待30秒。若学生答‘能导电就是电解质’，只追问：离子来自CO₂自身，还是与水反应的生成物？此页不提前给答案。",
        SOURCE_CONCEPT,
    )
    slide(
        "辨析：追问离子从哪里来",
        2,
        [
            "CO₂不是电解质，而是非电解质。",
            "CO₂与水反应生成碳酸；导电离子主要来自生成的碳酸电离，不是CO₂自身直接电离。",
            "笔记关键词：溶液能导电，不等于溶质一定是电解质。",
        ],
        "这是讲义区块49的辨析，不展开多元电离。最后40秒记录第1页第2栏；若学生仍只看导电现象，回到第2页三个判断关注点。过渡：NaCl自身电离时，微粒状态怎样变？",
        SOURCE_CONCEPT,
    )
    slide(
        "观察图2.14：离子状态怎样变？",
        2,
        ["先辨认图例，再比较晶体、溶于水、熔融三种情况。"],
        "给学生40秒读图，不同时念解释。追问晶体中有没有离子、哪条路径出现水分子、离子能否自由移动。该图是教材示意，不是本班实验现象。此页不抄笔记。",
        SOURCE_MICRO,
        image={
            "asset_id": c["image_assets"][0]["asset_id"],
            "observation_prompt": "氯化钠晶体中有没有离子？溶于水或熔融后，离子的运动条件有什么改变？",
        },
    )
    slide(
        "从图像解释：电离不等于导电",
        4,
        ["有自由移动的离子，并不等于此刻已经形成电流。"],
        "先让学生依据图解释晶体与溶液，再补外接电源、闭合通路。这里仅比较电解质的电离及其溶液导电，不泛化为金属导电机制。前2分钟问答，后2分钟讲清条件和过渡。",
        SOURCE_MICRO,
        visual=comparison(
            ["电离", "电解质溶液导电"],
            [
                (
                    "核心过程",
                    ["电解质形成能自由移动的离子", "自由移动离子定向移动形成电流"],
                ),
                (
                    "本课条件",
                    ["水分子作用下；NaCl也可在熔融时电离", "外接电源，形成闭合通路"],
                ),
                (
                    "NaCl实例",
                    [
                        "晶体含离子；溶于水后离子能自由移动",
                        "溶液接入闭合电路，才形成持续电流",
                    ],
                ),
            ],
        ),
    )
    slide(
        "停一停：记下概念之间的关系",
        2,
        [
            "判断类别：化合物 → 规定状态 → 自身电离。",
            "解释NaCl：晶体含离子但不能自由移动；水溶液中离子能自由移动。",
            "区别过程：电离产生自由移动离子；导电还需外接电源和闭合通路。",
            "补学习单第1页第3—4栏；只记关键词，不重抄定义。",
        ],
        "留90秒安静记录，抽查是否写出‘晶体含离子’和闭合通路。过渡：都是电解质，在水中是否都全部电离？",
        SOURCE_CONCEPT + SOURCE_MICRO,
    )
    slide(
        "二、都在水中，电离程度相同吗？",
        2,
        [
            "阅读教材第58页：NaCl与醋酸（CH₃COOH）的电离有什么差别？",
            "在学习单第2页先填‘电离程度’，其余两行暂留空。",
        ],
        "先回忆再读教材核对，不新添未知实验或数据。等待1分钟并请一人回答。比较对象限定为水溶液中的电离程度，不依据灯亮暗直接判强弱。",
        SOURCE_STRONG,
        activity="A02",
    )
    slide(
        "核对：强弱的依据是电离程度",
        2,
        ["水溶液中，全部电离与部分电离是本课区分依据。"],
        "用1分钟核对程度、符号与代表物质，1分钟补表。不采用讲义按键极性分类的那一行；强调溶解度也不是强弱判据。后两行引向符号表达。",
        SOURCE_STRONG,
        activity="A02",
        visual=comparison(
            ["强电解质", "弱电解质"],
            [
                ("电离程度", ["在水溶液中全部电离", "在水溶液中部分电离"]),
                ("书写符号", ["=", "⇌"]),
                ("本课代表物质", ["氯化钠NaCl", "醋酸CH₃COOH"]),
            ],
        ),
    )
    slide(
        "示范：把电离程度写进方程式",
        2,
        [
            "氯化钠：NaCl = Na⁺ + Cl⁻",
            "醋酸：CH₃COOH ⇌ H⁺ + CH₃COO⁻",
            "先写微粒，再选符号，最后检查原子与电荷守恒。",
        ],
        "逐个指认下标与右上角电荷：下标不是离子电荷；醋酸例式是示范，不能当作未见过的新题成绩。让学生解释可逆符号为何不同。",
        SOURCE_EQUATION,
        activity="A02",
    )
    slide(
        "独立写：教材三题＋例式回忆",
        4,
        [
            "教材任务：氢氧化钡Ba(OH)₂、硫酸钠Na₂SO₄、氯化钡BaCl₂。",
            "回忆任务：遮住示范，重写醋酸CH₃COOH的电离方程式。",
            "完成学习单第2页第2栏；先独立写，再圈出不确定的电荷或系数。",
        ],
        "前3分钟不展示答案，最后1分钟自查。巡视只提示‘分别数原子和电荷’，不口头泄露全班答案。若大面积不理解括号，先保留错例再反馈。",
        SOURCE_EQUATION,
        activity="A02",
        assessment=["E02"],
    )
    slide(
        "逐项核对：四个方程式",
        3,
        [
            "氢氧化钡：Ba(OH)₂ = Ba²⁺ + 2OH⁻",
            "硫酸钠：Na₂SO₄ = 2Na⁺ + SO₄²⁻",
            "氯化钡：BaCl₂ = Ba²⁺ + 2Cl⁻",
            "醋酸：CH₃COOH ⇌ H⁺ + CH₃COO⁻",
        ],
        "这四式为按教材规则书写的候选讲解，不称官方答案。留1分钟逐项比对，再针对实见错误讲系数2、原子团与电荷位置。没有看到学生作答不能说‘全班已经正确’。",
        SOURCE_EQUATION,
        activity="A02",
        assessment=["E02"],
    )
    slide(
        "检查依据：不能只看像不像答案",
        3,
        [
            "以Ba(OH)₂为例：左右各有1个Ba、2个O、2个H。",
            "电荷总和：左侧0；右侧（+2）＋2×（−1）= 0。",
            "Na₂SO₄的SO₄²⁻要整体写出；系数2表示两个Na⁺。",
            "在学习单第2页的订正行写一条理由；全对则写一条守恒检查。",
        ],
        "1分钟示范守恒，2分钟让学生订正并说明依据。弱电解质另核对可逆符号。板书保留‘微粒—符号—原子—电荷’四步，过渡到课末独立解释。",
        SOURCE_EQUATION,
        activity="A02",
        assessment=["E02"],
    )
    slide(
        "三、合上教材，补齐两页笔记",
        3,
        [
            "概念边界：化合物＋规定状态＋自身电离。",
            "微观解释：离子能自由移动，不等于已形成电流。",
            "程度与符号：全部电离用=；部分电离用⇌。",
            "检查表达：微粒正确，原子守恒，电荷守恒。",
        ],
        "先遮屏给1分钟独立回忆，再显示本页给1分钟只补缺项，最后1分钟互查。不要重新抄两页笔记。若缺项，保留为课后任务。",
        SOURCE_CONCEPT + SOURCE_MICRO + SOURCE_STRONG + SOURCE_EQUATION,
        activity="A03",
        assessment=["E01"],
    )
    slide(
        "离堂检验：用依据回答",
        3,
        [
            "为什么NaCl晶体不导电，而其水溶液能导电？",
            "为什么CO₂水溶液能导电，却不能据此把CO₂判为电解质？",
            "选一个已写方程式，说出你怎样检查原子和电荷守恒。",
        ],
        "先遮住笔记独立想1分钟，再听两位学生回答，另由全班在笔记旁标记未能解释的项目。只依据实际产出判断，不把同桌代答计为个人达成。",
        SOURCE_CONCEPT + SOURCE_EQUATION,
        activity="A03",
        assessment=["E01"],
    )
    slide(
        "回到开场：一条完整的解释链",
        2,
        [
            "NaCl晶体有离子，但离子不能自由移动；溶于水后离子能自由移动，接入闭合电路便能导电。",
            "CO₂溶于水后形成碳酸，导电不能归因于CO₂自身直接电离。",
            "强弱比较回到电离程度；方程式用符号、原子和电荷守恒共同检查。",
        ],
        "这是离堂回答后的核对页，不提前展示。学生用不同颜色补一个遗漏条件。课后只补缺项并练习口头解释；如遇学情不足，下一课先用同一问题再诊断。",
        SOURCE_CONCEPT + SOURCE_MICRO + SOURCE_STRONG,
        activity="A03",
        assessment=["E01"],
    )

    stage_specs = [
        (
            "开场与概念边界",
            8,
            "A01",
            ["O01"],
            "第1—4页：2分钟提出问题，3分钟圈定义并记关键词，1分钟独立辨析CO₂，2分钟反馈并记录离子来源。",
            "学习单第1页第1—2栏与口头理由。",
            "检查是否以化合物、状态和自身电离判断；CO₂辨析若只说‘不导电’须回问离子来源。",
        ),
        (
            "观察、解释与第一段笔记",
            8,
            "A01",
            ["O01"],
            "第5—7页：2分钟看图不先给解释，4分钟微观比较，2分钟停顿写笔记。追问：晶体是否无离子？离子能自由移动是否自动等于电流？",
            "观察并完成学习单第1页第3—4栏。",
            "需含晶体有离子、自由移动的差别、外接电源和闭合通路。",
        ),
        (
            "强弱比较、独立书写与反馈",
            16,
            "A02",
            ["O02"],
            "第8—13页：2分钟读教材填表、2分钟核对、2分钟示范、4分钟独立写、3分钟逐题核对、3分钟守恒检查与订正。保留学生实际错误，不预设为真实表现。",
            "完成学习单第2页比较、方程式及订正理由。",
            "逐式检查微粒、=或⇌、原子数和电荷；用理由解释订正。",
        ),
        (
            "重建与离堂检验",
            8,
            "A03",
            ["O01", "O02", "O03"],
            "第14—16页：3分钟只补两页笔记缺项、3分钟独立回答并抽样交流、2分钟核对关键条件。未达成者记录待补，课后不重复抄写全课。",
            "补缺记录、个人口头解释与自查标记。",
            "以E01标准逐项检查，课堂效果仍须教师实测。",
        ),
    ]
    c["lesson_stages"] = [
        {
            "id": f"L{i:02}",
            "order": i,
            "title": t,
            "minutes": m,
            "activity_ids": [a],
            "objective_ids": o,
            "assessment_ids": ["E02"] if a == "A02" else ["E01"],
            "teacher_action": ta,
            "student_action": sa,
            "assessment": ev,
            "materials": ["对应PPT与两页学习单", "教材印刷56—58页"],
        }
        for i, (t, m, a, o, ta, sa, ev) in enumerate(stage_specs, 1)
    ]
    c["assessments"][0].update(
        objective_ids=["O01", "O02", "O03"],
        title="分段笔记与独立解释",
        evidence_of_learning="学习单两页补缺及离堂口头解释；须区分独立作答与提示后订正。",
        success_criteria=[
            "按化合物、规定状态、自身电离辨析电解质，能说明CO₂例外表象的原因。",
            "说清NaCl晶体有离子但不能自由移动，并补足溶液导电的外接电源与闭合通路。",
            "以电离程度区分强弱，不用溶解度或键极性直接判断。",
            "能以一个已写方程式解释原子、电荷守恒；笔记缺项有订正记录。",
        ],
    )
    c["assessments"][1]["success_criteria"] = [
        "三道教材任务的微粒、电荷、系数正确。",
        "强电解质用=；醋酸例式用⇌，例式回忆不冒充陌生题迁移。",
        "逐项检查原子数和电荷总和。",
        "能说出一处订正依据；全对时提供一个守恒检查。",
    ]
    c["homework"].update(
        title="只补缺项，不重抄全课",
        estimated_minutes=10,
        tasks=[
            {
                "id": "H01",
                "instruction": "按离堂检验补齐两页笔记的缺项；若方程式写错，重新写出并注明原子或电荷检查。",
                "objective_ids": ["O01", "O02", "O03"],
            },
            {
                "id": "H02",
                "instruction": "不用看笔记，口头解释NaCl晶体与水溶液的导电差异，以及CO₂的辨析理由。说不清的条件标为下次课待问。",
                "objective_ids": ["O01", "O03"],
            },
        ],
    )
    c["uncertainties"] = [
        {
            "field": "Word缺失对象",
            "description": "区块46、55和部分公式含缺失对象或域，未声称恢复。样课只使用已读文字及独立核对的教材页面。",
            "teacher_action": "若需原讲义口诀或复杂电离方程式，先查看原Word；本课不扩展该部分。",
        },
        {
            "field": "来源冲突处理",
            "description": "真实模型原表仍含按键极性概括强弱的行，现修订删除；中央原始讲义与知识点记录未改动。",
            "teacher_action": "核对新表只以电离程度比较，确认班级教材口径。",
        },
        {
            "field": "图像与制品复核",
            "description": "初始服务只接收图片说明；本次离线修订由主代理查看教材原图并重新排版，不是服务视觉识别或教师签核。",
            "teacher_action": "课前检查实际教室投影清晰度及教材来源使用范围。",
        },
        {
            "field": "实际学情",
            "description": "尚无真实班级诊断或试讲反馈，40分钟节奏为设计值。",
            "teacher_action": "根据开场和离堂实际产出调整，保留必要独立书写与订正时间。",
        },
    ]
    for view in (c["activities"], c["lesson_stages"], c["slides"]):
        assert sum(row["minutes"] for row in view) == 40
    c["candidate_id"] = "PREPCAND-" + _canonical_candidate_digest(c)[:32]
    return c


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-python", required=True, type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists():
        raise RuntimeError("Use a new output directory; no overwrite")
    if digest(INPUT) != INPUT_SHA:
        raise RuntimeError("Frozen candidate changed")
    for path, expected in EXPECTED.items():
        if digest(path) != expected:
            raise RuntimeError("Source changed; review required")
    candidate = revised_candidate(json.loads(INPUT.read_text(encoding="utf-8")))
    renderer = BundledArtifactRenderer(args.artifact_python)
    result = renderer.render(
        candidate,
        output_kind="linked_bundle",
        output_dir=target,
        report_progress=lambda *a: None,
        is_cancelled=lambda: False,
        image_data={candidate["image_assets"][0]["asset_id"]: FIGURE.read_bytes()},
    )
    provenance = {
        "revision": "20260909-source-classroom-offline-r4",
        "method": "assistant_authored_offline_revision_of_frozen_real_model_candidate",
        "new_provider_calls": 0,
        "original_candidate_sha256": INPUT_SHA,
        "original_candidate_unchanged": digest(INPUT) == INPUT_SHA,
        "source_files_unchanged": all(digest(p) == h for p, h in EXPECTED.items()),
        "slides": len(candidate["slides"]),
        "minutes": 40,
        "teacher_approval": False,
        "classroom_validation": "not_performed",
        "visual_review": "pending",
        "artifacts": result,
        "hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in target.iterdir()
            if p.is_file()
        },
    }
    (target / "revision-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in provenance.items() if k not in {"artifacts", "hashes"}},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
