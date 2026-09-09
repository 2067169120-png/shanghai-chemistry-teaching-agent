"""Local source-bound 2 x 40 minute lesson; no provider or settings access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from verify_real_blueprint_preparation import BundledArtifactRenderer

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    _canonical_candidate_digest,
    normalize_preparation_candidate,
)

BASE = ROOT / "runtime/deeptutor_shchem/qa/real-source-preparation-v15-20260909-r1"
WORD = (
    ROOT
    / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx"
)
BOOK = ROOT / "课本/沪科技化学必修第一册【高清教材】.pdf"
FIGURE = (
    ROOT / "outputs/备课/2026-09-09-电解质与电离方程式-修订版/textbook-figure-2-14.png"
)
EXPECTED = {
    WORD: "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d",
    BOOK: "a565f0a15ffd10c704f4be42bfe7200c125b68959d11ef582acdc45dde2ccf22",
    FIGURE: "f375638dde10576147a56d6c71c890187ca9a3e6179236612b3077733554f3bf",
}
QUOTE = "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
B56 = "教材印刷56页（PDF61页）"
B57 = "教材印刷57页（PDF62页）"
B58 = "教材印刷58页（PDF63页）"
W = "对应Word讲义考点一"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def comparison(columns, rows):
    return {
        "kind": "comparison",
        "comparison": {
            "dimension_label": "项目",
            "columns": columns,
            "rows": [{"label": label, "values": values} for label, values in rows],
        },
        "steps": [],
    }


def lines(heading, prompt, count=3):
    return {
        "heading": heading,
        "prompt": prompt,
        "response_kind": "lines",
        "response_lines": count,
        "columns": [],
        "row_labels": [],
    }


def table(heading, prompt, columns, rows):
    return {
        "heading": heading,
        "prompt": prompt,
        "response_kind": "table",
        "response_lines": 0,
        "columns": columns,
        "row_labels": rows,
    }


def worksheet(title, sections):
    return {
        "title": title,
        "instructions": [
            "班级________ 姓名________ 日期________",
            "先独立作答，讲评后用另一种颜色订正。完整知识表在PPT中，记录时保留条件和理由。",
        ],
        "sections": sections,
    }


def lesson():
    raw = {
        "title": "电解质的电离 两课时完整讲练",
        "objectives": [
            {
                "statement": "根据教材实验与微观图说明电离和导电的区别，保留自由移动及外接电源条件"
            },
            {
                "statement": "按化合物、自身电离和或与和的条件区分电解质与非电解质，并解释具体反例"
            },
            {
                "statement": "按水溶液中的电离程度判断强弱电解质，解释醋酸、一水合氨与水的电离"
            },
            {
                "statement": "规范书写教材电离方程式，用原子与电荷守恒检查，并能纠正典型错误"
            },
        ],
        "activities": [],
        "assessments": [],
        "slides": [],
        "lesson_stages": [],
        "homework": {},
        "uncertainties": [],
    }
    provenance = []

    def slide(
        a, title, minutes, content, ref, notes, visual=None, image=None, role="知识讲解"
    ):
        ref = ref.replace(B56 + "—58", "教材印刷56—58页（PDF61—63页）")
        ref = ref.replace(B56 + "—57", "教材印刷56—57页（PDF61—62页）")
        ref = ref.replace(B57 + "—58", "教材印刷57—58页（PDF62—63页）")
        objectives = (
            [1]
            if a in (1, 2)
            else [2]
            if a == 3
            else [1, 2]
            if a == 4
            else [3]
            if a == 5
            else [4]
            if a == 6
            else [1, 2, 3, 4]
        )
        # The native template numbers separate content items. Keep authored
        # question numbers in one paragraph group so it cannot double-number.
        if (
            visual is None
            and image is None
            and any(re.match(r"^(?:[1-9]\.\s|[①②③④⑤⑥])", item) for item in content)
        ):
            content = ["\n\n".join(content)]
        raw["slides"].append(
            {
                "title": title,
                "purpose": role,
                "objective_numbers": objectives,
                "activity_numbers": [a],
                "assessment_numbers": [a],
                "minutes": minutes,
                "content": content,
                "visual": visual,
                "image": image,
                "teacher_notes": notes + "\n来源：" + ref,
            }
        )
        provenance.append(
            {"slide": len(raw["slides"]), "title": title, "source": ref, "role": role}
        )

    # First period: 12 + 8 + 13 + 7 = 40 minutes.
    slide(
        1,
        "电解质的电离",
        1,
        [
            "第2章 海洋中的卤素资源",
            "2.2 氧化还原反应和离子反应",
            "两课时完整讲练 · 第1课时 实验与概念",
        ],
        B56,
        "说明本次只学习2.2中的电解质电离部分，后续离子反应另课处理。发放学习单1—6，第一课时用1—3。",
    )
    slide(
        1,
        "两课时要解决的问题",
        1,
        [
            "第一课时：哪些物质属于电解质？它们为什么能导电？",
            "第二课时：为什么还要分强弱？怎样写对电离方程式？",
            "学习方法：观察证据 → 形成概念 → 例题讲解 → 独立练习 → 订正笔记。",
        ],
        B56 + "—58；" + W,
        "明确每节40分钟。题目出现时暂停，先写后看答案。目标不是记住导电物质清单，而是会按条件解释。",
    )
    slide(
        1,
        "教材实验 物质的导电性",
        3,
        [
            "教材第56页：比较NaCl、NaOH、KNO₃的固体和水溶液，再比较蔗糖、酒精及盐酸。",
            "同一装置、同类接触条件下观察灯泡；避免把电极污染带入下一种样品。",
            "另有KNO₃熔融实验。课堂读教材现象，不进行加热熔盐操作。",
            "先预测：NaCl固体不导电，能否说明NaCl不是电解质？",
        ],
        B56,
        "用教材实验引出问题，学习单1先写预测。教材装置是导电性定性比较，不给虚构电流值或灯泡亮度排名。电极清洗和相同接触条件为课堂方法补充。本课采用图文分析；不安排学生接市电、熔盐或制取气体。",
    )
    slide(
        1,
        "实验现象与记录",
        3,
        ["依据教材内容整理的参考现象，并非本班实测记录。学习单1按物质与状态记录。"],
        B56,
        "固体行指教材列出的这些固体，不能推广到金属。教材只安排KNO₃熔融实验，不把其他样品的熔融实验写成已观察。一般装置中酒精和蔗糖水溶液无明显导电现象，不声称电导严格为零。",
        comparison(
            ["样品或状态", "参考现象"],
            [
                ("无明显导电", ["NaCl、NaOH、KNO₃、蔗糖固体", "灯泡不亮"]),
                ("无明显导电", ["无水酒精、酒精及蔗糖水溶液", "灯泡不亮"]),
                ("有导电能力", ["NaCl、NaOH、KNO₃水溶液及盐酸", "灯泡发光"]),
                ("熔融实验", ["熔融KNO₃（硝酸钾）", "灯泡发光"]),
            ],
        ),
    )
    slide(
        1,
        "由现象提出微观问题",
        1,
        [
            "同样是NaCl，为什么固体与水溶液的导电性不同？",
            "同样加入水，为什么NaCl溶液与蔗糖溶液的导电性不同？",
            "请在学习单1保留初步解释，接下来用教材图检验。",
        ],
        B56 + "想一想；课堂改编",
        "收集有离子、离子能运动、必须有水三种可能解释，不立即评价；熔融路径将反驳必须有水。",
    )
    slide(
        1,
        "教材图 氯化钠的电离",
        3,
        [
            "教材图2.14：比较晶体、溶于水与熔融两条路径。",
            "找出离子，并说明什么发生了改变。",
        ],
        B57 + "图2.14",
        "晶体中已有钠离子和氯离子，但不能自由移动；水分子作用使离子脱离晶体表面并形成水合离子；熔融也可形成自由移动离子。注意图例颜色，不把水分子当作离子。",
        image={
            "asset_id": "IMG-" + EXPECTED[FIGURE],
            "observation_prompt": "为什么有离子仍可能不导电？没有水时能否形成自由移动离子？",
        },
    )

    slide(
        2,
        "知识点一 电离",
        3,
        [
            "教材原文（印刷57页）：“" + QUOTE + "”",
            "NaCl溶于水和熔融都可发生电离；电离本身不需要通电。",
            "为书写简便，电离方程式用离子符号表示水合离子。",
            "笔记：摘录原句，圈出“或”和“自由移动”。",
        ],
        B57,
        "留1分钟摘录原句。点明电离描述自由移动离子的形成过程，不等同于电流。教材方程式把水合钠离子简写作Na⁺，不要求学生把水合数写入方程式。",
    )
    slide(
        2,
        "知识表 电离与电解质导电",
        3,
        ["范围：以下比较电解质；金属的导电载流粒子另有不同。"],
        B57 + "及课堂整理",
        "先让学生用图解释，再展示表。离子能自由移动只是具备导电能力，实验中还需外接电源并闭合回路形成电流。对比应记录在学习单1的解释中。",
        comparison(
            ["电离", "电解质导电"],
            [
                ("微观过程", ["形成可自由移动的离子", "离子在电场作用下定向移动"]),
                (
                    "条件",
                    [
                        "水溶液中或熔融状态下；不需通电",
                        "有自由移动离子；本课装置接电源并闭合",
                    ],
                ),
                ("联系", ["提供自由移动离子", "电离不等于已经形成电流"]),
            ],
        ),
    )
    slide(
        2,
        "例1 NaCl固体不导电的原因",
        2,
        [
            "问题：NaCl固体中有Na⁺、Cl⁻，为什么仍不能导电？",
            "分析：先判断有无离子，再判断离子能否自由移动。",
            "答案：固体中离子受约束，不能自由移动；溶于水或熔融后，才有可自由移动的离子。",
            "反例提醒：不能写成“NaCl固体中没有离子”。",
        ],
        B56 + "想一想、" + B57 + "；课堂解析",
        "先请一名学生解释，再归纳答案。相同材料的不同状态不改变NaCl属于电解质的分类。",
    )

    slide(
        3,
        "知识点二 电解质与非电解质",
        3,
        ["依据教材与讲义整理：先确定研究对象，再核对导电条件。"],
        B56 + "；" + W + "区块42—49",
        "给学生1分钟记录完整定义条件。自身电离用于排除CO₂与水反应后产物电离的混淆。不能只凭某一状态不导电就定为非电解质。",
        comparison(
            ["电解质", "非电解质"],
            [
                ("对象", ["化合物", "化合物"]),
                (
                    "定义条件",
                    ["水溶液中或熔融状态下能够导电", "水溶液中和熔融状态下都不能导电"],
                ),
                (
                    "本质",
                    ["自身能电离形成自由移动离子", "自身不能电离形成自由移动离子"],
                ),
                ("注意", ["单质、混合物不属于两类中的任一类", "不能把“和”漏成“或”"]),
            ],
        ),
    )
    slide(
        3,
        "例2 CO₂溶于水后能导电",
        2,
        [
            "问题：CO₂水溶液能导电，能否据此把CO₂归为电解质？",
            "分析：CO₂先与水反应生成碳酸；产生导电离子的是生成的碳酸，不是CO₂自身电离。",
            "结论：CO₂是非电解质；碳酸是电解质。",
            "判断时先问：离子究竟由谁电离产生？",
        ],
        W + "区块49、56；讲义知识点改编例题",
        "本页只讲分类因果，不补写讲义缺失的多元电离式。不把CO₂的溶解反应与电离混写成一个电离方程式。",
    )
    slide(
        3,
        "练习1 六种物质怎样分类",
        3,
        [
            "A NaCl固体　B NaCl水溶液　C 铁",
            "D 蔗糖　E CO₂　F 气态HCl",
            "分别归为电解质、非电解质或两者都不是，并各选一例说明理由。",
            "学习单2：独立判断2分钟，写理由1分钟。",
        ],
        W + "区块42—49；课堂变式（非原试卷题）",
        "不要提前展示答案。提醒题目对象有纯净物与混合物、不同状态，学生应按定义而非记忆导电清单判断。",
        role="独立练习",
    )
    slide(
        3,
        "练习1 解析与依据",
        3,
        ["先看研究对象，再看自身电离能力，不按当前状态是否导电直接分类。"],
        W + "区块42—49；课堂变式解析",
        "答案A、F为电解质；D、E非电解质；B、C两者都不是。F是氯化氢化合物，盐酸则是其水溶液混合物。要求至少写一个条件完整的依据，不只写字母。",
        comparison(
            ["分类", "依据"],
            [
                (
                    "A NaCl；F HCl",
                    ["电解质", "在水溶液中自身电离；当前不导电不妨碍分类"],
                ),
                ("D 蔗糖；E CO₂", ["非电解质", "不由自身电离产生自由移动离子"]),
                ("B NaCl水溶液", ["两者都不是", "是混合物，不是化合物"]),
                ("C 铁", ["两者都不是", "是单质；金属导电依靠自由电子"]),
            ],
        ),
    )
    slide(
        3,
        "练习2 讲义中的分类题",
        2,
        [
            "讲义收录题：以下物质，属于电解质的是（　）。",
            "A 黄酒　B 冰醋酸　C 漂白粉　D 乙醇",
            "词义提示：冰醋酸指纯醋酸；本题不以溶液是否导电直接分类。",
            "学习单2：选出答案，并说明其余三项为什么不选。",
        ],
        W
        + "变式训练2·变载体；Word正文子节点94—97；题源年份学校仅为讲义自带标签，未独立核验",
        "给90秒独立作答，30秒交流。题干选项及答案B已核对Word文字层，不宣称原始校考试卷已核验。词义提示为本课新增支架。黄酒和漂白粉按混合物判断，不展开漂白粉反应。",
        role="独立练习",
    )

    slide(
        4,
        "练习2 答案与分类理由",
        2,
        [
            "答案B。冰醋酸是纯醋酸，属于化合物，溶于水后能自身电离，是电解质。",
            "A黄酒、C漂白粉均为混合物，不属于这两类化合物。",
            "D乙醇是化合物，但不能自身电离，是非电解质。",
            "分类路线：先看是否化合物，再看能否自身电离。醋酸的强弱留待第二课时讨论。",
        ],
        W + "变式训练2，答案B；解析据讲义和教材整理",
        "强调冰醋酸与醋酸水溶液不是同一分类对象。学生订正时必须写明混合物、化合物、自身电离三个判断依据。",
    )
    slide(
        4,
        "第一课时离堂检测",
        2,
        [
            "1. 不接电源的NaCl水溶液中是否发生电离？此时有无由外接电源驱动的电流？",
            "2. 说明蔗糖溶于水和NaCl溶于水在微观上的关键不同。",
            "学习单3：不翻笔记，独立写两句解释。",
        ],
        B56 + "—57；原创课堂检测",
        "用两分钟收集解释，判断学生能否同时使用自由移动离子和电流条件。",
        role="独立检测",
    )
    slide(
        4,
        "离堂检测 参考答案",
        1,
        [
            "1. 有电离；无外接电源驱动的电流。形成自由移动离子不等于已形成电流。",
            "2. NaCl溶于水形成自由移动离子；蔗糖溶于水仍以分子形式存在，不由自身电离形成离子。",
            "订正重点：溶解不一定发生电离；电离不需要通电。",
        ],
        B56 + "—57；课堂解析",
        "允许学生用自己语言表达，但必须保留分子与自由移动离子的差别。",
    )
    slide(
        4,
        "第一课时笔记整理",
        2,
        ["学习单3补全知识表；已有记录不必重复抄写。"],
        B56 + "—57；" + W,
        "留足2分钟整理。第一课时到此结束，第19页为第二课时开始，不占用课间计时。",
        comparison(
            ["核心结论", "例子或条件"],
            [
                (
                    "分类",
                    [
                        "电解质和非电解质均为化合物",
                        "NaCl是；铁和NaCl水溶液均不是两类对象",
                    ],
                ),
                (
                    "电离",
                    ["形成可以自由移动离子的过程", "水溶液中或熔融状态下；不需通电"],
                ),
                (
                    "导电",
                    ["电解质导电依靠自由移动离子", "闭合回路中的离子定向移动形成电流"],
                ),
            ],
        ),
    )

    # Second period: 10 + 14 + 11 + 5 = 40 minutes.
    slide(
        5,
        "第二课时 强弱与符号表达",
        1,
        [
            "先回忆：电离需要通电吗？NaCl固体不导电，还是电解质吗？",
            "本课任务：比较强弱，写对方程式，用守恒检查。",
            "今天使用学习单4—6。",
        ],
        B57 + "—58；课堂复习",
        "口答：不需要；是电解质。若前一课存在概念错误，在本页即时纠正，不让错误进入强弱分类。",
    )
    slide(
        5,
        "知识点三 强弱电解质",
        2,
        [
            "教材原文节选（印刷58页）：",
            "“像氯化钠、氯化氢、氢氧化钠等在水溶液中能够全部电离为自由移动离子的电解质称为强电解质。”",
            "“像醋酸、一水合氨（NH₃·H₂O）等在水溶液中仅有部分分子能电离出自由移动离子的电解质称为弱电解质。”",
        ],
        B58,
        "先读原句，再圈出水溶液、全部、部分三个关键限定。原书化学式用上下标，此处保留同一化学式。留记录时间，不将溶解度与电离程度混为一谈。",
    )
    slide(
        5,
        "知识表 强弱的判据",
        2,
        ["本表按教材整理，只比较所举溶质，不是完整溶液微粒清单。"],
        B58 + "；" + W + "区块51—57",
        "学习单4填表。溶液中还存在水及其极微弱电离产生的离子，表中所举溶质不是穷尽一切粒子。酸碱盐举例不用于推断所有物质均可不加条件一概而论。",
        comparison(
            ["强电解质", "弱电解质"],
            [
                ("电离程度", ["水溶液中全部电离", "水溶液中部分电离"]),
                ("表示符号", ["=", "⇌（可逆符号）"]),
                ("溶质举例", ["NaCl以Na⁺、Cl⁻存在", "醋酸分子与其电离出的离子共存"]),
                ("教材实例", ["NaCl、HCl、NaOH", "CH₃COOH、NH₃·H₂O；水极弱"]),
            ],
        ),
    )
    slide(
        5,
        "例3 醋酸为什么是弱电解质",
        2,
        [
            "问题：醋酸在水中能电离，为什么不归为强电解质？",
            "分析：能否自身电离用于区分电解质与非电解质；电离程度用于区分强与弱。",
            "答案：醋酸在水中只有部分分子电离，存在CH₃COOH及其电离产生的H⁺、CH₃COO⁻。",
            "电离式：CH₃COOH ⇌ H⁺ + CH₃COO⁻。",
        ],
        B58 + "；课堂解析",
        "明确醋酸属于弱电解质，不是非电解质。此处离子清单只讲醋酸自身电离，不是溶液中全部微粒。",
    )
    slide(
        5,
        "练习3 强弱判断是否成立",
        2,
        [
            "①蔗糖水溶液几乎不导电，所以蔗糖是弱电解质。",
            "②只知道某溶液导电较强，就能判断其溶质是强电解质。",
            "③水电离极微弱，所以水不是电解质。",
            "学习单4：判断并修正依据。",
        ],
        B56 + "、58；" + W + "区块57；原创课堂辨析",
        "90秒独立判断，30秒交流。第二项不提供浓度与其他条件，因此不能由导电性作结论。不要编造两种溶液实测亮度。",
        role="独立练习",
    )
    slide(
        5,
        "练习3 解析",
        1,
        [
            "①错。蔗糖不能自身电离，是非电解质；弱电解质能部分电离。",
            "②错。强弱判据是电离程度；导电性还与离子浓度等因素有关。",
            "③错。教材指出水是极弱的电解质，电离微弱不等于不电离。",
        ],
        B58 + "；" + W + "区块51—57；课堂解析",
        "如进度紧只让学生订正关键判据，勿扩展电导定量计算。",
    )

    slide(
        6,
        "知识点四 电离方程式",
        2,
        [
            "教材第57页（水溶液中）：NaCl = Na⁺ + Cl⁻；HCl = H⁺ + Cl⁻；NaOH = Na⁺ + OH⁻。",
            "书写顺序：判强弱 → 写离子 → 配系数 → 查守恒。",
            "强电解质用“=”；弱电解质用“⇌”。离子团按整体书写。",
            "系数表示离子个数关系；右上角电荷表示一个离子所带电荷。",
        ],
        B57 + "—58；课堂方法整理",
        "先读物质名称：氯化钠、氯化氢、氢氧化钠。不能把电离式写成通电分解反应式，也不能把二价离子误写成两个一价离子。",
    )
    slide(
        6,
        "例4 氢氧化钡怎样书写",
        2,
        [
            "题目：写出Ba(OH)₂在水溶液中的电离方程式。",
            "分析：Ba(OH)₂是强电解质，用“=”；离子是Ba²⁺与OH⁻。",
            "答案：Ba(OH)₂ = Ba²⁺ + 2OH⁻。",
            "检查：Ba、O、H各自守恒；右侧电荷(+2) + 2×(−1) = 0。",
        ],
        B57 + "书写表达第1项；课堂解析",
        "OH作为氢氧根整体保留。括号外2成为OH⁻前的系数，不改为O²⁻或OH²⁻。此题先作为讲解例题，再让学生遮住答案在学习单5复写。",
    )
    slide(
        6,
        "例5 硫酸钠怎样书写",
        2,
        [
            "题目：写出Na₂SO₄在水溶液中的电离方程式。",
            "分析：Na⁺与SO₄²⁻是两个离子种类；一个SO₄²⁻对应两个Na⁺。",
            "答案：Na₂SO₄ = 2Na⁺ + SO₄²⁻。",
            "检查：Na、S、O各自守恒；2×(+1) + (−2) = 0。",
        ],
        B57 + "书写表达第2项；课堂解析",
        "硫酸根保持整体；不能写为Na²⁺。让学生说出离子种类数与个数比的区别，不把2Na⁺理解为一个二价钠离子。",
    )
    slide(
        6,
        "练习4 教材书写任务",
        4,
        [
            "合上例题答案，写出水溶液中的电离方程式。",
            "教材第57页：①Ba(OH)₂ 氢氧化钡　②Na₂SO₄ 硫酸钠　③BaCl₂ 氯化钡。",
            "教材第58页：④CH₃COOH 醋酸、⑤NH₃·H₂O 一水合氨为例式复写；⑥H₂O 水为书写任务。",
            "学习单5：每式检查符号、离子电荷、原子守恒与电荷守恒。",
        ],
        B57 + "书写表达三项；" + B58 + "例式复写",
        "本页4分钟用于学生书写。①②③及⑥是教材原书写任务；④⑤是依据教材例式新增的复写练习，不能合称六道教材原题。水的极弱电离已在第21、24页学过，本页先尝试书写，第29—30页反馈。",
        role="独立练习",
    )
    slide(
        6,
        "练习4 答案与检查",
        2,
        [
            "①Ba(OH)₂ = Ba²⁺ + 2OH⁻",
            "②Na₂SO₄ = 2Na⁺ + SO₄²⁻　③BaCl₂ = Ba²⁺ + 2Cl⁻",
            "④CH₃COOH ⇌ H⁺ + CH₃COO⁻",
            "⑤NH₃·H₂O ⇌ NH₄⁺ + OH⁻",
            "⑥H₂O ⇌ H⁺ + OH⁻",
            "逐式检查：左右原子相同；左右总电荷均为0；弱电解质保留可逆符号。",
        ],
        B57 + "—58；课堂解析",
        "点名读离子名称：钡离子、氢氧根、钠离子、硫酸根、氯离子、氢离子、醋酸根、铵根。第四项仅羧基氢电离，不能产生4H⁺；第五项是NH₄⁺而不是NH₃⁺。",
    )
    slide(
        6,
        "教材讲解 水的微弱电离",
        2,
        [
            "教材第58页：写出水的电离方程式，用简单离子符号表示。",
            "书写结果：H₂O ⇌ H⁺ + OH⁻。",
            "教材图2.15画出水分子之间的质子转移；上式使用教材要求的简写。",
            "水是极弱电解质；不以“难测到明显导电”否定其电离。",
        ],
        B58 + "书写表达及图2.15",
        "学习单5第六行已在练习4作答，本页讲解和订正，不再要求学生面对已显示的答案独立作答。逐项说出可逆符号与守恒。图示实际形成水合氢离子；本课按教材任务使用H⁺简写，不开展平衡常数计算。",
    )

    slide(
        7,
        "例6 氨与一水合氨的区别",
        2,
        [
            "问题：NH₃、NH₃·H₂O、氨水分别怎样分类？",
            "分析：NH₃与水反应生成NH₃·H₂O，再由NH₃·H₂O电离出离子。",
            "答案：NH₃是非电解质；NH₃·H₂O是弱电解质；氨水是混合物，两类都不是。",
            "三个名称不能互相替代，分类对象必须写清。",
        ],
        W + "区块47、49；" + B58 + "；讲义知识点改编例题",
        "本课采用所用教材的一水合氨表示法。板书名称与化学式对应，不把溶液氨水写成一种化合物。",
    )
    slide(
        7,
        "练习5 查错并改写",
        3,
        [
            "①Na₂SO₄ = Na²⁺ + SO₄²⁻",
            "②BaCl₂ = Ba²⁺ + Cl₂⁻",
            "③CH₃COOH = H⁺ + CH₃COO⁻",
            "④NH₃·H₂O ⇌ NH₃⁺ + OH⁻",
            "学习单6：每式圈出错误、写出正确式，并标出检查依据。",
        ],
        B57 + "—58；原创课堂纠错练习",
        "给3分钟独立查错。错误式仅用于纠错，不可摘为正确笔记；先看离子符号和种类，再用原子与电荷验证。",
        role="独立练习",
    )
    slide(
        7,
        "练习5 解析",
        2,
        [
            "①Na₂SO₄ = 2Na⁺ + SO₄²⁻。系数2不能替代为钠离子的二价电荷。",
            "②BaCl₂ = Ba²⁺ + 2Cl⁻。应为两个氯离子，不是Cl₂⁻。",
            "③CH₃COOH ⇌ H⁺ + CH₃COO⁻。醋酸部分电离，要用可逆符号。",
            "④NH₃·H₂O ⇌ NH₄⁺ + OH⁻。产物是铵根离子；原错误式也不满足H原子守恒。",
        ],
        B57 + "—58；原创课堂纠错解析",
        "第三式即使原子和电荷守恒仍是错式，说明守恒是必要检查但不是唯一检查；还要检查强弱及物种。学习单6用另一色笔订正。",
    )
    slide(
        7,
        "练习6 综合解释",
        3,
        [
            "有NaCl固体、NaCl水溶液和蔗糖水溶液三种样品。",
            "1. 哪个样品属于电解质？哪些样品是混合物？",
            "2. 在本课导电装置中，哪个样品能使灯泡明显发光？说明微观原因。",
            "3. 写出NaCl在水中的电离方程式，并解释为什么不能只按“是否导电”给样品分类。",
            "学习单6：把分类、微观解释、符号表达连起来。",
        ],
        B56 + "—57；原创课堂综合题",
        "给学生3分钟，先按三个问号分别写。必须针对样品而非把溶液中的溶质偷换成样品整体。",
        role="独立练习",
    )
    slide(
        7,
        "练习6 解析",
        1,
        [
            "1. NaCl固体是电解质；两种水溶液都是混合物，不属于电解质或非电解质这两类化合物。",
            "2. NaCl水溶液能明显导电，因为有自由移动的离子；固体中离子不能自由移动，蔗糖不自身电离。",
            "3. NaCl = Na⁺ + Cl⁻。分类须看化合物自身电离能力，不是只看当前样品能否导电。",
        ],
        B56 + "—57；课堂解析",
        "按研究对象、自由移动离子、方程式三项反馈。若班级多数不会解释，需延长讲评，将末尾回顾安排为课后整理，不宣称固定时长适合所有班级。",
    )

    slide(
        8,
        "第二课时离堂检测",
        2,
        [
            "1. 默写一水合氨的电离方程式，并说明为什么用可逆符号。",
            "2. “只要电荷守恒，电离方程式就一定正确。”对吗？用本课一例说明。",
            "学习单6：不翻笔记独立完成，答完再看下一页。",
        ],
        B58 + "；原创课堂检测",
        "收集两题：第一题同时检查物种、符号与判据，第二题用醋酸等号错式说明守恒不能替代化学判断。",
        role="独立检测",
    )
    slide(
        8,
        "离堂检测 参考答案",
        1,
        [
            "1. NH₃·H₂O ⇌ NH₄⁺ + OH⁻；一水合氨在水中部分电离，过程可逆。",
            "2. 不对。将醋酸电离写成“=”虽可满足电荷守恒，却没有正确表示弱电解质的部分电离。",
            "检查顺序：物质及离子种类 → 强弱与符号 → 原子与电荷守恒。",
        ],
        B58 + "；课堂解析",
        "允许用其他本课错误式举例，但反例必须确实满足题述的电荷守恒且有另一错误。",
    )
    slide(
        8,
        "两课时核心笔记",
        2,
        ["最后核对四条主线，保留定义的条件与方程式的符号。"],
        B56 + "—58；" + W,
        "留2分钟补齐笔记。课后只重做本节错题，并给每个错误写出一个检查动作。不把本节内容扩充成未经核验的上海原题卷。",
        comparison(
            ["必须记住", "具体例证"],
            [
                (
                    "分类",
                    [
                        "对象是化合物；或与和；自身电离",
                        "NaCl是电解质；蔗糖不是；盐水是混合物",
                    ],
                ),
                (
                    "微观",
                    [
                        "自由移动离子与电流形成条件不同",
                        "固体NaCl有离子，但离子不能自由移动",
                    ],
                ),
                (
                    "强弱",
                    [
                        "按水溶液中的电离程度判断",
                        "NaCl全部电离；醋酸、一水合氨部分电离",
                    ],
                ),
                ("书写", ["判强弱、写离子、配系数、查守恒", "Na₂SO₄ = 2Na⁺ + SO₄²⁻"]),
            ],
        ),
    )

    sheets = {
        1: worksheet(
            "学习单1 导电现象与电离",
            [
                table(
                    "实验记录",
                    "依据教材或课堂讲解填参考现象，不作为本班实测数据。",
                    ["样品组", "现象", "解释或疑问"],
                    [
                        "NaCl NaOH KNO₃ 蔗糖固体",
                        "上述三种盐碱的水溶液及盐酸",
                        "蔗糖与酒精水溶液 无水酒精",
                        "熔融KNO₃",
                    ],
                ),
                lines(
                    "图2.14与例1",
                    "为什么NaCl固体有离子却不导电？溶于水或熔融后发生什么变化？",
                    3,
                ),
                lines(
                    "教材原句",
                    "摘录电离定义，圈出关键条件；另注明电离是否需要通电。",
                    3,
                ),
            ],
        ),
        3: worksheet(
            "学习单2 分类与依据",
            [
                table(
                    "练习1",
                    "分类填电解质 非电解质 两者都不是，并写依据。",
                    ["样品", "分类", "依据"],
                    [
                        "A NaCl固体",
                        "B NaCl水溶液",
                        "C 铁",
                        "D 蔗糖",
                        "E CO₂",
                        "F 气态HCl",
                    ],
                ),
                lines(
                    "练习2",
                    "讲义收录题：以下物质属于电解质的是____。A黄酒 B冰醋酸 C漂白粉 D乙醇。冰醋酸指纯醋酸。写出选择并说明各项分类依据。",
                    4,
                ),
            ],
        ),
        4: worksheet(
            "学习单3 第一课时整理",
            [
                lines(
                    "离堂检测",
                    "1 不接电源的NaCl水溶液中是否电离？有无外接电源驱动的电流？2 蔗糖和NaCl溶于水有什么微观区别？",
                    4,
                ),
                table(
                    "概念笔记",
                    "按投影表补全，每条保留必要条件。",
                    ["概念", "结论", "例证"],
                    ["电解质与非电解质", "电离", "电解质导电"],
                ),
                lines(
                    "本课订正", "记下一个原先的错误想法，改写为条件完整的正确表述。", 2
                ),
            ],
        ),
        5: worksheet(
            "学习单4 强弱电解质",
            [
                table(
                    "知识表",
                    "只讨论所举溶质的电离，不是完整溶液微粒清单。",
                    ["项目", "强电解质", "弱电解质"],
                    [
                        "水溶液中电离程度",
                        "电离式符号",
                        "所举溶质的存在形式",
                        "教材例子",
                    ],
                ),
                lines(
                    "练习3",
                    "①蔗糖几乎不导电所以是弱电解质。②某溶液导电强所以溶质必为强电解质。③水电离极微弱所以不是电解质。逐项判断并说明依据。",
                    4,
                ),
                lines(
                    "例3笔记",
                    "醋酸能电离，为什么仍是弱电解质？写出判断所依据的条件。",
                    2,
                ),
            ],
        ),
        6: worksheet(
            "学习单5 电离方程式",
            [
                table(
                    "教材书写与复写",
                    "①②先听例题再遮住答案复写；①②③是教材第57页书写任务。④⑤为第58页例式复写，⑥为第58页水的书写任务。均用本课水溶液简写。",
                    ["物质", "电离方程式", "守恒检查"],
                    [
                        "① Ba(OH)₂ 氢氧化钡",
                        "② Na₂SO₄ 硫酸钠",
                        "③ BaCl₂ 氯化钡",
                        "④ CH₃COOH 醋酸",
                        "⑤ NH₃·H₂O 一水合氨",
                        "⑥ H₂O 水",
                    ],
                ),
                lines(
                    "例4与例5的方法",
                    "说明Ba(OH)₂中OH的个数怎样体现，以及Na₂SO₄为何写2Na⁺而不是Na²⁺。",
                    3,
                ),
            ],
        ),
        7: worksheet(
            "学习单6 纠错与综合",
            [
                lines(
                    "练习5",
                    "改正并注明依据：①Na₂SO₄ = Na²⁺ + SO₄²⁻；②BaCl₂ = Ba²⁺ + Cl₂⁻；③CH₃COOH = H⁺ + CH₃COO⁻；④NH₃·H₂O ⇌ NH₃⁺ + OH⁻。",
                    4,
                ),
                lines(
                    "练习6",
                    "NaCl固体、NaCl水溶液、蔗糖水溶液：1 哪个是电解质，哪些是混合物？2 哪个能明显导电，为什么？3 写出NaCl电离式，说明不能仅按导电性分类的原因。",
                    4,
                ),
                lines(
                    "第二课时离堂检测",
                    "1 默写一水合氨电离式并解释可逆符号。2 电荷守恒能否保证电离式正确？举本课一例说明。",
                    3,
                ),
            ],
        ),
    }
    titles = [
        "教材实验与微观观察",
        "电离及导电的区别",
        "定义与分类讲练",
        "第一课时检测与笔记",
        "强弱电解质讲练",
        "电离方程式书写",
        "纠错与综合解释",
        "第二课时检测与笔记",
    ]
    student_actions = [
        "填写学习单1，先预测再用图修正解释。",
        "摘录教材原句，用离子能否自由移动解释例1。",
        "完成学习单2六种样品分类及讲义四选一题，再订正。",
        "独立完成学习单3的两问，补全概念表。",
        "完成学习单4强弱比较及三个判断，说明判据。",
        "完成学习单5六个电离式及守恒检查。",
        "完成学习单6四个纠错式和三问综合题，区分样品与溶质。",
        "完成学习单6两问离堂检测，整理四条核心笔记。",
    ]
    criteria = [
        [
            "能指出NaCl晶体已有离子但离子不能自由移动",
            "记录只对应教材样品与状态，不假称实测数据",
        ],
        [
            "电离定义保留水溶液中或熔融状态及自由移动",
            "知道不接电源也能电离，外接闭合电路才有相应电流",
        ],
        [
            "A F为电解质；D E为非电解质；B C两者都不是",
            "讲义题选B；黄酒和漂白粉是混合物，乙醇为非电解质，冰醋酸是电解质",
        ],
        [
            "第一问答有电离、无外接电源驱动电流；第二问区分分子与自由移动离子",
            "笔记同时保留对象、条件及具体例证",
        ],
        [
            "以水溶液中全部或部分电离作判据",
            "蔗糖非电解质，水极弱电解质；导电强弱不能直接替代电离程度",
        ],
        [
            "六式的符号、离子种类、电荷、系数正确",
            "解释2Na⁺不同于Na²⁺，OH⁻与SO₄²⁻保持整体",
        ],
        [
            "纠错同时检查物种、强弱符号及两种守恒",
            "综合题不把NaCl水溶液混合物说成电解质，能解释导电微观原因",
        ],
        [
            "一水合氨式正确且说明部分电离可逆",
            "能用醋酸错误等号式反驳电荷守恒足以保证正确",
        ],
    ]
    for a, title in enumerate(titles, 1):
        slides = [s for s in raw["slides"] if s["activity_numbers"] == [a]]
        indexes = [i for i, s in enumerate(raw["slides"], 1) if s in slides]
        objectives = sorted({o for s in slides for o in s["objective_numbers"]})
        notes = "\n\n".join(
            f"PPT第{i}页 {s['title']}（{s['minutes']}分钟）\n{s['teacher_notes']}"
            for i, s in zip(indexes, slides, strict=True)
        )
        activity = {
            "title": title,
            "objective_numbers": objectives,
            "minutes": sum(s["minutes"] for s in slides),
            "teacher_action": notes,
            "student_action": student_actions[a - 1],
            "materials": [
                f"PPT第{indexes[0]}—{indexes[-1]}页",
                "教材印刷56—58页及对应Word考点一",
            ],
            "worksheet": sheets.get(a),
        }
        raw["activities"].append(activity)
        raw["assessments"].append(
            {
                "title": title + "学习证据",
                "objective_numbers": objectives,
                "activity_numbers": [a],
                "evidence_of_learning": student_actions[a - 1],
                "success_criteria": criteria[a - 1],
            }
        )
        raw["lesson_stages"].append(
            {
                k: deepcopy(activity[k])
                for k in (
                    "title",
                    "objective_numbers",
                    "minutes",
                    "teacher_action",
                    "student_action",
                    "materials",
                )
            }
        )
        raw["lesson_stages"][-1].update(
            activity_numbers=[a],
            assessment_numbers=[a],
            assessment="；".join(criteria[a - 1]),
        )
    raw["homework"] = {
        "title": "课后订正与复习",
        "tasks": [
            {
                "instruction": "重做学习单中做错的分类、纠错或书写任务，每题补一句判断依据。",
                "objective_numbers": [2, 3, 4],
            },
            {
                "instruction": "不看PPT，用化合物分类、电离与导电、强弱判据、电离式检查四条线索口述本课。",
                "objective_numbers": [1, 2, 3, 4],
            },
        ],
        "estimated_minutes": 10,
    }
    raw["uncertainties"] = [
        {
            "field": "授课前确认",
            "description": "两课时各40分钟为设计预算，已核对教材印刷56—58页；尚未进行真人教师审核或课堂试讲。",
            "teacher_action": "按本班学情决定讲评时间，授课前核对术语、公式和后排投影可读性。",
        },
        {
            "field": "来源缺口",
            "description": "Word区块54、58及60—63等含缺失公式或对象；未猜补，未采用键极性大小直接判强弱。新增书写内容据教材页面核对。",
            "teacher_action": "如需加入酸式盐、多元弱酸或原试卷题，另核原始完整来源。",
        },
        {
            "field": "实验安排",
            "description": "本稿使用教材图和参考现象讨论，不含本班实测数据，不安排熔盐或气体实验。",
            "teacher_action": "若改为现场实验，应另作条件、器材与实验安全核查。",
        },
    ]
    return raw, provenance


def build_brief():
    brief = json.loads((BASE / "teacher-brief.json").read_text("utf-8"))
    brief["lesson_timing"] = "2课时×40分钟"
    brief["objective"] = (
        "依据对应Word考点一和教材印刷56—58页完成两课时讲练，掌握概念、导电解释、强弱判据及教材电离方程式，能独立分类、查错并综合解释。"
    )
    brief["advanced"] = {
        "learning_and_experiment": "2课时各40分钟。增加教材实验与图、具体讲解例题、独立练习和分离的讲评。仅图文分析，不实施熔盐或气体实验。暂不扩展整个2.2节。",
        "template_and_delivery": "保留现有授课模板，章节目题为首页；知识点原句和完整知识表可见，学习单留白；明确第一课时结束和第二课时开始。",
        "homework_and_strategy": "教材原书写任务、教材例式复写、讲义知识点改编和原创课堂练习分别注明。课后以订正与复述为主。",
    }
    brief["materials"] += (
        "\n本次用户更新：2课时完整讲练。增加教材56页导电性实验、58页一水合氨及水的电离；沿用原始来源快照作为来源数据，不沿用上次一课时题量限制。本次无新增模型调用。"
    )
    return brief


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    if output.exists():
        raise RuntimeError("Choose a new output directory; old artifacts are retained")
    assert all(sha(p) == h for p, h in EXPECTED.items()), "Source hash changed"
    raw, provenance = lesson()
    brief = build_brief()
    candidate = normalize_preparation_candidate(raw, brief)
    # This is a local source-reviewed expansion, not a new provider response.
    # Update only its provenance statement and recompute the canonical hash.
    candidate["source_basis"]["statement_zh"] = (
        "依据沪科技化学必修第一册印刷56—58页与第04讲复习讲义考点一。"
        "本稿按两个40分钟课时组织实验分析、概念讲解、例题、练习和笔记整理。"
    )
    for uncertainty in candidate["uncertainties"]:
        if uncertainty["field"] == "source_basis":
            uncertainty["description"] = (
                "本次为本地扩编，已由助手查看教材印刷56—58页及图2.14，"
                "并核对讲义文字；没有新增模型API调用。"
                "助手检查不代替真人教师审核或课堂试讲，仍为个人备课候选。"
            )
    candidate["candidate_id"] = (
        "PREPCAND-" + _canonical_candidate_digest(candidate)[:32]
    )
    assert len(raw["slides"]) == 38
    assert all(
        sum(row["minutes"] for row in candidate[k]) == 80
        for k in ("slides", "activities", "lesson_stages")
    )
    assert sum(s["minutes"] for s in candidate["slides"][:18]) == 40
    assert sum(s["minutes"] for s in candidate["slides"][18:]) == 40
    assert not any(u["field"] == "timing_alignment" for u in candidate["uncertainties"])
    result = BundledArtifactRenderer(args.artifact_python).render(
        candidate,
        output_kind="linked_bundle",
        output_dir=output,
        report_progress=lambda *_: None,
        is_cancelled=lambda: False,
        image_data={"IMG-" + EXPECTED[FIGURE]: FIGURE.read_bytes()},
    )
    for name, value in [
        ("expanded-raw-candidate.json", raw),
        ("teacher-brief.json", brief),
        ("source-coverage.json", provenance),
    ]:
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    report = {
        "new_model_calls": 0,
        "slide_count": 38,
        "period_minutes": [40, 40],
        "worked_examples": 6,
        "practice_groups": 6,
        "exit_check_groups": 2,
        "worksheet_sections": 6,
        "source_files_unchanged": all(sha(p) == h for p, h in EXPECTED.items()),
        "teacher_approval": False,
        "classroom_validation": False,
        "visual_review": "pending",
        "artifacts": {
            p.name: sha(p) for p in output.glob("*") if p.suffix in (".pptx", ".docx")
        },
    }
    (output / "expansion-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    print("renderer returned", sorted(result))


if __name__ == "__main__":
    main()
