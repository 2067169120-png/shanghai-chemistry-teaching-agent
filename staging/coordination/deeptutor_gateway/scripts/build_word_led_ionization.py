"""Word-led two-period teaching revision, with source-bound assets and no API calls."""

from __future__ import annotations

import argparse
import json
import struct
from copy import deepcopy
from pathlib import Path

from build_two_period_ionization import (
    B56,
    B57,
    B58,
    EXPECTED,
    FIGURE,
    QUOTE,
    ROOT,
    BundledArtifactRenderer,
    _canonical_candidate_digest,
    build_brief,
    comparison,
    lines,
    normalize_preparation_candidate,
    sha,
    table,
    worksheet,
)

ASSETS = ROOT / "runtime/deeptutor_shchem/qa/word-led-source-assets-20260909-r2"
W3 = "Word讲义第3—4页 知识点1与得分速记"
W4 = "Word讲义第4—5页 知识点2与得分速记"
W5 = "Word讲义第5页 知识点3"
W6 = "Word讲义第6页 考向1"
W7 = "Word讲义第7页 考向2"
W8 = "Word讲义第8页 考向2"


def assets():
    specs = [
        ("experiment", "book-figure-2-12.png", "图2.12 物质导电实验装置", B56),
        ("melt", "book-figure-2-13.png", "图2.13 熔融固体的导电性", B56),
        ("definition", "book-definition.png", "教材电解质与非电解质定义原段", B56),
        ("water", "book-figure-2-15.png", "图2.15 水分子电离过程示意图", B58),
        (
            "states",
            "word-three-states.png",
            "讲义NaCl三种状态题图",
            W8 + " 变式3",
        ),
        ("micro", None, "图2.14 氯化钠电离过程示意图", B57),
    ]
    meta, payload, keys = [], {}, {}
    for key, name, caption, source in specs:
        path = ASSETS / name if name else FIGURE
        data = path.read_bytes()
        digest = sha(path)
        width, height = struct.unpack(">II", data[16:24])
        asset_id = "IMG-" + digest
        meta.append(
            dict(
                asset_id=asset_id,
                sha256=digest,
                caption=caption,
                source=source,
                purpose="阅读原图并完成同页观察或概念判断，不作装饰。",
                width=width,
                height=height,
                content_type="image/png",
            )
        )
        payload[asset_id] = data
        keys[key] = asset_id
    return meta, payload, keys


def lesson(keys):
    raw = dict(
        title="电解质的电离 两课时讲练与笔记",
        objectives=[
            {"statement": "按化合物、自身电离和或与和的条件分类，并用状态反例解释"},
            {"statement": "读教材和讲义微观图，区分电离与电解质导电的条件"},
            {"statement": "按电离程度区分强弱，正确归类常见物质并区分溶解性"},
            {"statement": "按类别与状态写电离方程式，处理弱酸分步和常见酸式盐并纠错"},
        ],
        activities=[],
        assessments=[],
        slides=[],
        lesson_stages=[],
        homework={},
        uncertainties=[],
    )
    coverage = []

    def s(
        a, title, mins, content, source, note, visual=None, img=None, role="知识讲解"
    ):
        objective = {
            1: [1],
            2: [1],
            3: [2],
            4: [3],
            5: [3, 4],
            6: [4],
            7: [4],
            8: [1, 2, 3, 4],
        }[a]
        # One group preserves authored question labels without auto-number duplication.
        raw["slides"].append(
            dict(
                title=title,
                purpose=role,
                objective_numbers=objective,
                activity_numbers=[a],
                assessment_numbers=[a],
                minutes=mins,
                content=["\n\n".join(content)] if not visual and not img else content,
                visual=visual,
                image=dict(
                    asset_id=keys[img], observation_prompt=note.split("。")[0] + "。"
                )
                if img
                else None,
                teacher_notes=note + "\n来源：" + source,
            )
        )
        coverage.append(
            dict(slide=len(raw["slides"]), title=title, source=source, role=role)
        )

    # Period 1: 12 + 6 + 9 + 13 = 40 minutes.
    s(
        1,
        "电解质的电离",
        1,
        [
            "第2章 海洋中的卤素资源",
            "2.2 氧化还原反应和离子反应",
            "第1课时 概念分类与强弱辨析",
        ],
        B56,
        "本课对应讲义考点一，不把后续离子反应全部混入。发学习单1—8，第一课时使用1—4。",
    )
    s(
        1,
        "本课学习路线",
        1,
        [
            "第一课时：导电实验 → 电解质分类 → 电离的微观解释 → 强弱电解质。",
            "第二课时：基本电离式 → 分步与状态 → 讲义纠错题 → 教材书写与综合检测。",
            "每个单元先学知识表，再做例题；先写答案，讲评后订正并保留判断依据。",
        ],
        W3 + "；" + W4 + "；" + W5,
        "本课为两课时复习，预计各40分钟。常考知识总结依据本讲义的考向与得分速记整理，不代表统计考试频率。",
    )
    s(
        1,
        "教材实验 固体与水溶液",
        2,
        [
            "比较同一种物质的固体和水溶液。",
            "图中两种样品都是KNO₃，状态有何不同？",
            "再看教材列出的NaCl、NaOH、蔗糖、无水酒精与盐酸。",
        ],
        B56 + " 图2.12",
        "看清样品标签和电路连接。问：固体不导电能否直接否定其电解质身份？先预测，不立即给分类答案。教材用电源和灯泡比较，本课不做实测。",
        img="experiment",
    )
    s(
        1,
        "教材实验 熔融状态",
        1,
        [
            "图2.13：少量KNO₃受热熔化后接入电路。",
            "没有水，也可能出现导电现象。",
            "问题：定义为什么不能只写水溶液？",
        ],
        B56 + " 图2.13",
        "指出图中加热、熔融样品和闭合电路的关系。本课只读图，不安排学生熔盐实验，不据图编造电流数值。",
        img="melt",
    )
    s(
        1,
        "教材实验的参考现象",
        1,
        ["按教材文字整理，不是本班实测；不比较未经测量的亮度大小。"],
        B56,
        "学习单1记录同类样品的状态与参考现象。盐碱固体这里仅指NaCl、NaOH、KNO₃，不推广到金属。",
        comparison(
            ["样品或状态", "参考现象"],
            [
                ("固体", ["NaCl、NaOH、KNO₃、蔗糖", "不明显导电"]),
                ("水溶液", ["上述盐碱溶液、盐酸", "能导电"]),
                ("分子物质", ["蔗糖溶液、无水酒精、酒精溶液", "无明显导电现象"]),
                ("熔融", ["KNO₃", "能导电"]),
            ],
        ),
    )
    s(
        1,
        "教材原句 电解质与非电解质",
        2,
        [
            "“这类能在水溶液中或熔融状态下导电的化合物叫做电解质。”",
            "非电解质的对应条件是水溶液中和熔融状态下均不能导电。",
            "笔记关键词：化合物；或；和。",
        ],
        B56 + " 定义段",
        "先读左侧教材原段，再抄右侧电解质原句。非电解质右侧为整理，不冒充完整逐字引文；两类对象都是化合物。",
        img="definition",
    )
    s(
        1,
        "常考知识总结一 分类的完整条件",
        2,
        ["先判断是不是化合物，再判断是否自身电离。"],
        W3 + "；" + B56,
        "表中条件须完整记录。强调这里是化合物分类，不是把任何样品按能否导电分两类。",
        comparison(
            ["电解质", "非电解质"],
            [
                ("对象", ["化合物", "化合物"]),
                (
                    "条件",
                    ["水溶液中或熔融状态下能导电", "两种条件下均不能自身电离而导电"],
                ),
                (
                    "常见类型",
                    ["酸、碱、盐；Na₂O等金属氧化物；水", "CO₂、SO₂、NH₃；蔗糖、乙醇等"],
                ),
                ("边界", ["单质与混合物不归入这两类", "NH₃与NH₃·H₂O不能混同"]),
            ],
        ),
    )
    s(
        1,
        "得分速记 三个不能直接推断",
        2,
        ["判断时必须写出反例，不只记对错。"],
        W3,
        "点明CO₂与水反应生成的物质电离，不是CO₂自身电离；本课按教材及讲义表述解释。",
        comparison(
            ["错误推断", "正确依据与反例"],
            [
                (
                    "看当前状态",
                    ["不导电就不是电解质", "NaCl固体不导电，但NaCl是电解质"],
                ),
                ("看导电现象", ["能导电就一定是电解质", "铁是单质；盐酸是混合物"]),
                (
                    "看水溶液",
                    [
                        "溶液导电，溶质就一定是电解质",
                        "CO₂、NH₃先与水反应；不能据此判为电解质",
                    ],
                ),
            ],
        ),
    )
    s(
        2,
        "讲义变式1 先分类再判断",
        2,
        [
            "下列物质属于电解质的是（ ）。",
            "A 黄酒    B 冰醋酸    C 漂白粉    D 乙醇",
            "独立作答：给出选项，并说明另外三项不选的原因。冰醋酸指纯醋酸。",
        ],
        W6 + " 变式2",
        "留90秒写学习单2第一题，问先排除哪两项以及依据。不要预先显示下一页答案。",
        role="独立练习",
    )
    s(
        2,
        "变式1讲评 对象比现象更先判断",
        1,
        [
            "答案 B。冰醋酸是化合物，溶于水能部分电离。",
            "A黄酒、C漂白粉是混合物，不属于这两类。",
            "D乙醇是非电解质。冰醋酸属于弱电解质，不因当前状态而改变分类。",
        ],
        W6 + " 变式2参考解析",
        "请学生订正分类依据；混合物不选与乙醇不选的理由不同。",
        role="答案讲评",
    )
    s(
        2,
        "讲义变式2 导电还要看状态",
        2,
        [
            "下列物质中，只有在水溶液中才能导电的是（ ）。",
            "A 液氯    B HCl气体    C 浓硫酸    D KCl晶体",
            "分别说明物质类别与状态。注意题问不是单纯判断电解质。",
        ],
        W6 + " 变式3（解析跨第7页）",
        "留90秒作答学习单2第二题。HCl与KCl已核对原页，不能仅凭文字提取的缺口省略选项。",
        role="独立练习",
    )
    s(
        2,
        "变式2讲评 HCl与KCl的区别",
        1,
        [
            "答案 B。HCl溶于水可电离；液态纯HCl通常不导电。",
            "KCl在水溶液和熔融状态下均能导电，不能选D。",
            "液氯是单质，通常不导电；浓硫酸是本身能够导电的溶液，不能选C。",
        ],
        W6 + " 变式3参考解析",
        "复述定义的或：满足一种规定条件即可，不要求两种状态都导电。此处不讨论纯硫酸自偶电离。",
        role="答案讲评",
    )
    s(
        3,
        "教材图 从晶体到自由移动离子",
        2,
        ["NaCl晶体里有没有离子？", "溶于水与熔融两条路径共同改变了什么？"],
        B57 + " 图2.14",
        "先辨认图例，再沿两条路径读图。晶体中已有离子，变化是离子能否自由移动；水合离子采用简单符号表示。",
        img="micro",
    )
    s(
        3,
        "教材原句 电离",
        2,
        [
            "“" + QUOTE + "”",
            "NaCl固体中有离子，但离子不能自由移动。",
            "电离不需要通电。电离方程式用简单离子符号表示水合离子。",
            "笔记：摘录原句，圈出状态条件与自由移动。",
        ],
        B57,
        "至少留45秒摘录原句。让学生说电离与把电子从原子中打出的电离概念在本节语境有何不同，不扩展物理细节。",
    )
    s(
        3,
        "常考知识总结二 电离与导电",
        2,
        ["以下比较电解质；金属导电的载流粒子不同。"],
        B57 + "；" + W8,
        "请用这张表解释前面KNO₃实验。能否电离与当前是否有电流不能混为一谈。",
        comparison(
            ["电离", "电解质导电"],
            [
                ("实质", ["形成可自由移动的离子", "离子在电场作用下定向移动"]),
                (
                    "条件",
                    [
                        "水溶液中或熔融状态下，不需通电",
                        "有自由移动离子；实验装置接电源并闭合",
                    ],
                ),
                (
                    "NaCl固体",
                    ["已有离子，但不能自由移动", "不导电，不能解释为没有离子"],
                ),
                ("联系", ["提供自由移动离子", "具备导电能力不等于已产生电流"]),
            ],
        ),
    )
    s(
        3,
        "讲义图像题 NaCl三种状态",
        2,
        [
            "X、Y为电极，X接电源正极，Y接负极。正确选项是（可多选）。",
            "A 图a中的微粒是离子\nB NaCl只有通电才能电离\nC 图b表示熔融NaCl导电\nD 三种状态均可导电",
        ],
        W8 + " 变式3（题干按课堂整理）",
        "观察图a、b、c的离子是否自由移动以及水分子是否出现。学习单3记选项和逐项依据，先不讲答案。",
        img="states",
        role="独立练习",
    )
    s(
        3,
        "图像题讲评 有离子还不够",
        1,
        [
            "答案 AC。图a为晶体：有离子但不能自由移动。",
            "图b为熔融态，图c为水溶液，均有可自由移动的离子，可导电。",
            "B错在把电离与通电混同；D错在忽视图a的状态。",
        ],
        W8 + " 变式3参考解析",
        "闭环回到本课开场：NaCl固体不导电仍是电解质。题图电极用于显示定向移动，不额外讲电极产物。",
        role="答案讲评",
    )
    s(
        4,
        "教材原句 强弱看电离程度",
        2,
        [
            "“像氯化钠、氯化氢、氢氧化钠等在水溶液中能够全部电离为自由移动离子的电解质称为强电解质。”",
            "“像醋酸、一水合氨（NH₃·H₂O）等在水溶液中仅有部分分子能电离出自由移动离子的电解质称为弱电解质。”",
            "圈出：水溶液中；全部；仅有部分。",
            "水也是电解质，只是电离极弱。",
        ],
        B58 + " 定义段",
        "两句按教材第58页核对，化学式以可编辑上下标字符呈现。不要用灯泡亮度或溶解度代替定义。",
    )
    s(
        4,
        "常考知识总结三 强弱电解质",
        2,
        ["判据是水溶液中的电离程度，不是溶解度或导电强弱。"],
        W4 + "；" + B58,
        "仅比较目标溶质的电离，不把表格当作完整溶液微粒清单；不采用键极性直接判强弱的概括。",
        comparison(
            ["强电解质", "弱电解质"],
            [
                ("电离程度", ["全部电离；电离式用 =", "部分电离；电离式用 ⇌"]),
                (
                    "酸与碱",
                    [
                        "HCl、HNO₃等强酸；NaOH、Ba(OH)₂等强碱",
                        "HF、H₃PO₄、CH₃COOH等弱酸；NH₃·H₂O等弱碱",
                    ],
                ),
                ("盐与水", ["大多数盐，如NH₄Cl、BaSO₄", "水是极弱电解质"]),
                (
                    "不要混淆",
                    [
                        "难溶不等于弱，BaSO₄是强电解质",
                        "蔗糖和乙醇是非电解质，不是弱电解质",
                    ],
                ),
            ],
        ),
    )
    s(
        4,
        "讲义例题1 全部为弱电解质",
        2,
        [
            "下列各组物质全部属于弱电解质的是（ ）。",
            "A H₂O、NH₃·H₂O、H₃PO₄、HF\nB Cu(OH)₂、CH₃COOH、乙醇\nC H₂SO₃、Ba(OH)₂、BaSO₄\nD SO₂、H₂S、CO₂",
            "先找每组中的反例，再说明A组的共同判据。",
        ],
        W6 + " 例1",
        "学生先独立选，再逐组圈出导致不选的物质。保留讲义题的组内对照价值。",
        role="独立练习",
    )
    s(
        4,
        "例题1讲评 每个干扰项都有依据",
        1,
        [
            "答案 A。A组依次为水、弱碱、弱酸、弱酸。",
            "B组乙醇是非电解质；不能因为不导电就判为弱电解质。",
            "C组Ba(OH)₂是强碱，BaSO₄是强电解质。",
            "D组SO₂、CO₂为非电解质，H₂S为弱电解质。",
        ],
        W6 + " 例1参考解析",
        "让学生补全错误选项的类型，而不是只抄A。Cu(OH)₂弱碱，H₂SO₃弱酸。",
        role="答案讲评",
    )
    s(
        4,
        "讲义变式3 NH₄Cl属于哪一类",
        1,
        [
            "NH₄Cl属于（ ）。",
            "A 强电解质    B 弱电解质\nC 非电解质    D 既不是电解质也不是非电解质",
            "写出判断依据，不能仅凭它与弱碱有关作答。",
        ],
        W6 + " 变式1",
        "独立作答后检查分类标准有没有从电离程度换成溶液酸碱性。",
        role="独立练习",
    )
    s(
        4,
        "变式3讲评 盐的电离与后续反应",
        1,
        [
            "答案 A。NH₄Cl是强电解质。",
            "NH₄Cl = NH₄⁺ + Cl⁻（氯化铵在水中的电离）",
            "NH₄⁺后续可与水发生反应，但这不意味着NH₄Cl只部分电离。",
        ],
        W6 + " 变式1参考解析",
        "若未学水解，第二句只作边界提醒，不引入水解平衡计算。重点是不能从弱碱对应盐推出弱电解质。",
        role="答案讲评",
    )
    s(
        4,
        "第一课时笔记与自检",
        4,
        [
            "用三句话总结：先看对象与自身电离；导电要看状态；强弱要看电离程度。",
            "合上资料口答：NaCl固体、盐酸、CO₂、NH₄Cl、BaSO₄分别怎样分类？",
            "订正答案：强电解质；混合物两者都不是；非电解质；强电解质；强电解质。",
            "最后1分钟，在学习单4写一个反例及其说明的判断条件。",
        ],
        W3 + "；" + W4,
        "前2分钟整理，教师只口头提问并遮挡投影答案，之后再展示核对。最后1分钟收两名学生的解释，确认条件完整。该页不是独立练习答题页。",
        role="笔记总结",
    )

    # Period 2: 8 + 6 + 13 + 13 = 40 minutes.
    s(
        5,
        "第2课时 电离方程式",
        1,
        [
            "复习：分类看对象与自身电离；强弱看电离程度。",
            "本课目标：写对离子种类和系数，选对符号，保留状态与分步条件。",
        ],
        W5,
        "第二课时从此页开始。用NH₄Cl式复习整体离子NH₄⁺，再过渡水的电离。",
    )
    s(
        5,
        "教材图 水的微弱电离",
        2,
        [
            "图中：2H₂O ⇌ H₃O⁺ + OH⁻。",
            "按教材任务用简单离子符号：H₂O ⇌ H⁺ + OH⁻。",
            "两种表示法不要混写；水是极弱电解质。",
        ],
        B58 + " 图2.15与书写任务",
        "指认两个水分子与水合氢离子、氢氧根离子。可逆符号不能误写成单向箭头；简单符号式为教材要求。",
        img="water",
    )
    s(
        5,
        "讲义知识点 基本电离式",
        2,
        ["以下按本课水溶液中的电离表达。"],
        W5 + "；" + B57,
        "请用名称读出离子，避免只背符号。酸产生氢离子，碱产生氢氧根，盐注意铵根等整体离子。",
        comparison(
            ["物质", "电离方程式"],
            [
                ("强酸", ["HCl 氯化氢", "HCl = H⁺ + Cl⁻"]),
                ("强碱", ["NaOH 氢氧化钠", "NaOH = Na⁺ + OH⁻"]),
                ("铵盐", ["(NH₄)₂SO₄ 硫酸铵", "(NH₄)₂SO₄ = 2NH₄⁺ + SO₄²⁻"]),
                ("弱碱", ["NH₃·H₂O 一水合氨", "NH₃·H₂O ⇌ NH₄⁺ + OH⁻"]),
            ],
        ),
    )
    s(
        5,
        "常考知识总结四 书写检查",
        2,
        ["化学式正确与守恒正确必须同时满足。"],
        W5 + "；" + W7 + " 思维模型",
        "表格对应后面的纠错题。ClO⁻、NO₃⁻、SO₄²⁻、NH₄⁺不能随意拆成原子离子。",
        comparison(
            ["正确做法", "典型错误"],
            [
                ("符号", ["强电解质用 =；弱电解质用 ⇌", "醋酸的电离写等号"]),
                ("离子", ["NaClO = Na⁺ + ClO⁻", "把ClO⁻拆成Cl⁻和O²⁻"]),
                ("系数", ["Na₂SO₄ = 2Na⁺ + SO₄²⁻", "把2Na⁺写成Na²⁺"]),
                ("条件", ["先判类别、强弱和状态", "把通电写成电离条件"]),
            ],
        ),
    )
    s(
        5,
        "讲义方法 先分类再看条件",
        1,
        [
            "第一步：判强弱，选择 = 或 ⇌。",
            "第二步：识别多元弱酸和酸式盐，判断是否分步、是否区分水溶液与熔融。",
            "第三步：写离子种类与系数；检查原子数及总电荷。",
            "不是所有含H的盐都能直接写出H⁺。",
        ],
        W7 + " 思维模型；" + W5,
        "按讲义方法图重组为可编辑文字。不要粘贴存在文字冲突的原分类图。",
    )
    s(
        6,
        "讲义知识点 多元弱酸分步电离",
        2,
        [
            "以H₂S（硫化氢）为例：",
            "第一步 H₂S ⇌ H⁺ + HS⁻\n第二步 HS⁻ ⇌ H⁺ + S²⁻",
            "多元弱酸分步电离，电离程度逐步减弱，以第一步为主。",
            "不要把规范的分步电离式写成H₂S = 2H⁺ + S²⁻。",
        ],
        W5,
        "这是讲义复习补充，不标为教材56—58页原句。用每步释放一个H⁺检验方程式；不扩展电离常数计算。",
    )
    s(
        6,
        "讲义知识点 NaHSO₄看状态",
        2,
        ["硫酸氢钠：题目给出的状态决定HSO₄⁻怎样处理。"],
        W5 + " 酸式盐总结",
        "按中学本讲义的简化表达。水溶液中的式子不当作所有浓度下硫酸氢根全部电离的严格热力学结论。本页不安排熔融实验。",
        comparison(
            ["水溶液", "熔融状态"],
            [
                ("电离式", ["NaHSO₄ = Na⁺ + H⁺ + SO₄²⁻", "NaHSO₄ = Na⁺ + HSO₄⁻"]),
                ("酸式酸根", ["本讲义按生成H⁺、SO₄²⁻处理", "HSO₄⁻保留整体"]),
                ("易错点", ["漏写H⁺", "照搬水溶液的表达"]),
            ],
        ),
    )
    s(
        6,
        "讲义知识点 NaHCO₃分开表达",
        2,
        [
            "碳酸氢钠是盐：NaHCO₃ = Na⁺ + HCO₃⁻。",
            "HCO₃⁻的电离：HCO₃⁻ ⇌ H⁺ + CO₃²⁻。",
            "不要写NaHCO₃ = Na⁺ + H⁺ + CO₃²⁻；这把酸式酸根的弱电离当成完全电离。",
            "盐的电离与HCO₃⁻的后续电离不是同一层次。",
        ],
        W5 + " 酸式盐总结",
        "HCO₃⁻还可水解，本页只讨论其电离路径，不能由该式直接推出溶液呈酸性。不要求本课掌握水解机制；引导在学习单6并列两式。",
    )
    s(
        7,
        "讲义例题2 电离方程式纠错",
        2,
        [
            "根据讲义例题整理：下列写法中正确的是（ ）。均讨论水溶液。",
            "A KNO₃ = K⁺ + NO₃⁻，并在等号上注明通电\nB H₂S = 2H⁺ + S²⁻\nC NH₃·H₂O ⇌ NH₄⁺ + OH⁻\nD NaClO = Na⁺ + Cl⁻ + O²⁻",
            "选出正确项，再写出另外三项错在何处。",
        ],
        W7 + " 例2（原页部分箭头缺字，据知识规则整理）",
        "原Word部分箭头显示缺字符，本页是据讲义整理的等值训练，不宣称原题逐字复刻。独立作答学习单7。",
        role="独立练习",
    )
    s(
        7,
        "例题2讲评 按规则逐项修正",
        2,
        [
            "答案 C。一水合氨是弱电解质，生成NH₄⁺、OH⁻。",
            "A去掉通电条件；电离不是电解。",
            "B改为H₂S ⇌ H⁺ + HS⁻；HS⁻ ⇌ H⁺ + S²⁻。",
            "D改为NaClO = Na⁺ + ClO⁻（次氯酸钠）；ClO⁻保留整体。",
        ],
        W7 + " 例2参考解析与课堂整理",
        "要求每个错项写规则和正确式。仅说不守恒不能覆盖A、B两类错误。",
        role="答案讲评",
    )
    s(
        7,
        "讲义变式4 难溶与弱电离",
        2,
        [
            "根据讲义变式整理：判断两句话，并说明理由。",
            "① BaSO₄难溶，所以它是弱电解质。",
            "② 写出BaSO₄(s) ⇌ Ba²⁺(aq) + SO₄²⁻(aq)，即可说明BaSO₄是弱电解质。",
            "注意：第②式写明了固体与水溶液的状态。",
        ],
        W8 + " 变式2；" + W4 + " 电离与溶解平衡辨析",
        "原四选一题不加语境容易混淆溶解平衡，改成明确状态的两项判断，不能冒称原题。先独立作答。",
        role="独立练习",
    )
    s(
        7,
        "变式4讲评 两种过程不能混淆",
        2,
        [
            "两句话都不正确，但第②句中的溶解平衡式本身并非错误。",
            "BaSO₄是强电解质；溶解度小不等于已溶部分只部分电离。",
            "BaSO₄(s) ⇌ Ba²⁺(aq) + SO₄²⁻(aq)表示固体与离子的溶解平衡。",
            "判强弱看电离程度，不能只看到可逆符号就判断为弱电解质。",
        ],
        W4 + " 得分速记；" + W8 + " 变式2改编",
        "划清表达对象：固体溶解平衡与溶质电离。避免把合法的难溶盐溶解平衡式教成错式。",
        role="答案讲评",
    )
    s(
        7,
        "教材书写任务 独立完成",
        3,
        [
            "按本课水溶液中的简单离子符号，写出下列电离式。",
            "① Ba(OH)₂ 氢氧化钡    ② Na₂SO₄ 硫酸钠\n③ BaCl₂ 氯化钡    ④ CH₃COOH 醋酸\n⑤ NH₃·H₂O 一水合氨    ⑥ H₂O 水",
            "检查：符号、离子整体、原子数、总电荷。①②③为第57页任务，④⑤为第58页例式复写，⑥为第58页任务。",
        ],
        B57 + "；" + B58,
        "至少留2分钟独立写六式。教师巡视收集一个离子电荷错误与一个符号错误，不立刻展示答案。",
        role="独立练习",
    )
    s(
        7,
        "教材书写任务 参考答案",
        2,
        [
            "① Ba(OH)₂ = Ba²⁺ + 2OH⁻\n② Na₂SO₄ = 2Na⁺ + SO₄²⁻\n③ BaCl₂ = Ba²⁺ + 2Cl⁻",
            "④ CH₃COOH ⇌ H⁺ + CH₃COO⁻\n⑤ NH₃·H₂O ⇌ NH₄⁺ + OH⁻\n⑥ H₂O ⇌ H⁺ + OH⁻",
            "用另一种颜色订正；写错系数时同时检查原子数和总电荷。",
        ],
        B57 + "；" + B58 + " 本课推导核对",
        "教师按物质名称逐式读出。六式均需核对，不能只投影无反馈。",
        role="答案讲评",
    )
    s(
        8,
        "书写自检 守恒只是必要检查",
        2,
        [
            "2Na⁺表示两个钠离子，不是一个带两个正电荷的钠离子。",
            "SO₄²⁻、NO₃⁻、ClO⁻、NH₄⁺、OH⁻在相应电离式中保留整体。",
            "醋酸写成CH₃COOH = H⁺ + CH₃COO⁻，原子与电荷都守恒，仍因符号不当而错误。",
            "最终检查：对象和条件 → 强弱符号 → 离子种类 → 系数与守恒。",
        ],
        W5 + "；" + W7 + " 课堂归纳",
        "请学生举一个满足守恒但电离式错误的反例。不要把检查法压缩为只看电荷。",
    )
    s(
        8,
        "常考知识总结五 条件与分步",
        2,
        ["把本页作为学习单6的订正表，保留物质名称和条件。"],
        W5,
        "有余力学生可问HCO₃⁻水解，提醒那是另一过程并留待后续；本课不额外加入整段酸碱平衡计算。",
        comparison(
            ["必须保留", "常见错误"],
            [
                ("多元弱酸", ["H₂S分步、每步可逆", "一步写到底并用等号"]),
                ("NaHSO₄", ["水溶液与熔融分别处理", "不写状态，机械拆HSO₄⁻"]),
                ("NaHCO₃", ["盐电离与HCO₃⁻电离分开", "把两步并成全部电离"]),
                ("难溶盐", ["区分溶解平衡与电离程度", "凭难溶或可逆符号判弱"]),
            ],
        ),
    )
    s(
        8,
        "离堂检测 分类与表达",
        3,
        [
            "① NaCl固体、NaCl水溶液、乙醇分别属于哪一类？哪一种能明显导电？",
            "② 分别写出NaHSO₄在水溶液和熔融状态下的电离式。",
            "③ 某同学说“只要原子数和电荷守恒，电离式就正确”。用本课例子反驳。",
        ],
        "根据教材56—58页与Word考点一设计的课堂检测，不是上海原题",
        "留3分钟独立作答学习单8。以分类条件、状态区分、反例论证三项收集学习证据。",
        role="独立练习",
    )
    s(
        8,
        "离堂检测 参考答案与订正",
        2,
        [
            "① 依次为强电解质、混合物两者都不是、非电解质；NaCl水溶液能明显导电。",
            "② 水溶液：NaHSO₄ = Na⁺ + H⁺ + SO₄²⁻；熔融：NaHSO₄ = Na⁺ + HSO₄⁻。",
            "③ 例如醋酸电离式用等号，虽守恒仍不正确；弱电解质应使用可逆符号。",
        ],
        "本课检测参考答案；NaHSO₄按讲义简化表达",
        "不是官方评分细则。建议分别看对象条件、两式的状态、反例及理由，不只按最后答案评价。",
        role="答案讲评",
    )
    s(
        8,
        "两课时总笔记 四条判断线索",
        3,
        ["补全学习单8；每条结论至少保留一个例子。"],
        W3 + "；" + W4 + "；" + W5 + "；" + B57,
        "预留3分钟整理，不再加入新知识。先请学生口述四条，再投影表核对；总结不替代前面各单元完整表。",
        comparison(
            ["判断依据", "本课例证"],
            [
                (
                    "是什么",
                    ["化合物；规定条件；自身电离", "冰醋酸是电解质；盐酸是混合物"],
                ),
                ("为何导电", ["自由移动离子；外接闭合电路", "NaCl晶体有离子却不导电"]),
                ("强弱如何", ["水溶液中的电离程度", "BaSO₄强；水极弱；乙醇非"]),
                (
                    "怎样表达",
                    ["类别与状态、符号、整体离子、守恒", "弱酸分步；NaHSO₄看状态"],
                ),
            ],
        ),
        role="笔记总结",
    )
    s(
        8,
        "课后任务 订正与可选迁移",
        1,
        [
            "必做：重做本课错题，每题补一句依据；遮住PPT复写六个教材电离式。",
            "可选：已知2H₂O ⇌ H₃O⁺ + OH⁻，类比写出H₂O₂的自偶电离式。",
            "可选题来自讲义第7页变式1。先根据题给信息推导，不作为本课必背式。",
        ],
        W7 + " 变式1；教材书写任务",
        "可选题参考：2H₂O₂ ⇌ H₃O₂⁺ + HO₂⁻。检查H与O及电荷守恒。不扩充两性氢氧化物、硼酸、次磷酸和联氨的整套专题。",
    )

    sheets = [
        worksheet(
            "学习单1 分类条件与实验",
            [
                table(
                    "参考现象",
                    "根据教材及PPT填写，不记为本班实测。",
                    ["样品", "状态与现象", "原因或疑问"],
                    ["KNO₃", "NaCl", "蔗糖与乙醇"],
                ),
                table(
                    "分类笔记",
                    "保留化合物及或与和条件。",
                    ["项目", "电解质", "非电解质"],
                    ["定义条件", "自身电离", "例子"],
                ),
                lines(
                    "易错反例",
                    "各写一个：不导电的电解质；能导电却不是电解质的物质；水溶液导电的非电解质。",
                    3,
                ),
            ],
        ),
        worksheet(
            "学习单2 讲义分类题",
            [
                lines(
                    "变式1",
                    "属于电解质的是：A黄酒 B冰醋酸 C漂白粉 D乙醇。选项____；分别说明四项分类依据。",
                    5,
                ),
                lines(
                    "变式2",
                    "只有水溶液条件下才能导电的是：A液氯 BHCl气体 C浓硫酸 DKCl晶体。选项____；比较HCl和KCl的状态条件。",
                    5,
                ),
                lines(
                    "订正方法",
                    "本组题先判断什么，再判断什么？写出一个不能省略的条件。",
                    3,
                ),
            ],
        ),
        worksheet(
            "学习单3 电离与三状态图",
            [
                lines("教材原句", "摘录电离定义，标出自由移动和状态条件。", 3),
                table(
                    "电离与导电",
                    "不要把离子存在等同于产生电流。",
                    ["项目", "电离", "电解质导电"],
                    ["过程", "条件"],
                ),
                lines(
                    "讲义三状态题",
                    "结合PPT第16页图：A图a中微粒为离子；BNaCl只有通电才能电离；C图b表示熔融NaCl导电；D三种状态均导电。正确项____；分别写出a、b、c的状态及理由。",
                    5,
                ),
            ],
        ),
        worksheet(
            "学习单4 强弱辨析",
            [
                table(
                    "强弱知识表",
                    "按目标溶质的电离程度分类。",
                    ["项目", "强电解质", "弱电解质"],
                    ["判据与符号", "常见酸碱", "盐与水"],
                ),
                lines(
                    "讲义例题1",
                    "全部为弱电解质的是：A H₂O NH₃·H₂O H₃PO₄ HF；B Cu(OH)₂ CH₃COOH 乙醇；C H₂SO₃ Ba(OH)₂ BaSO₄；D SO₂ H₂S CO₂。选____，圈出其他组的反例并说明。",
                    4,
                ),
                lines(
                    "NH₄Cl变式与自检",
                    "NH₄Cl属于哪类电解质？为什么不能由弱碱对应盐推出弱电解质？再解释BaSO₄难溶为何不等于弱。",
                    4,
                ),
            ],
        ),
        worksheet(
            "学习单5 基本式与检查规则",
            [
                table(
                    "例式复写",
                    "听讲后遮住答案，用名称读式。",
                    ["物质", "电离方程式", "需注意"],
                    [
                        "水 简单离子符号",
                        "HCl 氯化氢",
                        "NaOH 氢氧化钠",
                        "(NH₄)₂SO₄ 硫酸铵",
                        "NH₃·H₂O 一水合氨",
                    ],
                ),
                lines(
                    "整体离子与系数",
                    "列出本课5种需保留整体的离子；用Na₂SO₄说明系数2和电荷2的差别。",
                    4,
                ),
                lines("检查方法", "按顺序写出电离方程式的检查步骤。", 3),
            ],
        ),
        worksheet(
            "学习单6 分步与条件",
            [
                table(
                    "讲义知识点整理",
                    "写完整式子并保留条件。",
                    ["物质与条件", "电离方程式", "易错点"],
                    [
                        "H₂S 第一步",
                        "HS⁻ 第二步",
                        "NaHSO₄ 水溶液",
                        "NaHSO₄ 熔融",
                        "NaHCO₃ 盐电离",
                        "HCO₃⁻ 电离",
                    ],
                ),
                lines(
                    "层次辨析", "为什么不把NaHCO₃直接写成Na⁺、H⁺、CO₃²⁻的完全电离？", 4
                ),
            ],
        ),
        worksheet(
            "学习单7 纠错与教材书写",
            [
                lines(
                    "讲义例题2整理",
                    "正确的是____。A KNO₃电离式注明通电；B H₂S = 2H⁺ + S²⁻；C NH₃·H₂O ⇌ NH₄⁺ + OH⁻；D NaClO = Na⁺ + Cl⁻ + O²⁻。写出三个错项的正确式与依据。",
                    4,
                ),
                lines(
                    "难溶盐变式整理",
                    "判断：①BaSO₄难溶所以是弱电解质。②BaSO₄(s) ⇌ Ba²⁺(aq) + SO₄²⁻(aq)能证明它是弱电解质。区分推断与方程式本身。",
                    3,
                ),
                table(
                    "教材书写任务",
                    "独立完成，随后用另一种颜色订正。",
                    ["物质", "电离方程式"],
                    [
                        "Ba(OH)₂ 氢氧化钡",
                        "Na₂SO₄ 硫酸钠",
                        "BaCl₂ 氯化钡",
                        "CH₃COOH 醋酸",
                        "NH₃·H₂O 一水合氨",
                        "H₂O 水",
                    ],
                ),
            ],
        ),
        worksheet(
            "学习单8 检测与总笔记",
            [
                lines(
                    "离堂检测",
                    "①NaCl固体、NaCl水溶液、乙醇分类并判断导电。②分别写NaHSO₄水溶液与熔融电离式。③用一个例子说明守恒不足以保证电离式正确。",
                    6,
                ),
                table(
                    "总笔记",
                    "每条保留条件和一个例子。",
                    ["问题", "判断依据", "例证"],
                    ["是什么", "为何导电", "强弱如何", "怎样表达"],
                ),
                lines(
                    "可选迁移",
                    "类比2H₂O ⇌ H₃O⁺ + OH⁻，写出H₂O₂的自偶电离式。非本课必背式。",
                    2,
                ),
            ],
        ),
    ]
    # Classroom QA refinements: preserve independent answers and consolidate
    # the six textbook equations on one response sheet instead of splitting.
    raw["slides"][13]["content"][0] = "教材第57页原句：\n" + raw["slides"][13]["content"][0]
    raw["slides"][17]["content"][0] = "教材第58页原句：\n" + raw["slides"][17]["content"][0]
    raw["slides"][19]["content"][0] = raw["slides"][19]["content"][0].replace(
        "先找每组中的反例，再说明A组的共同判据。", "逐组判断，并说明所选组的共同判据。"
    )
    raw["slides"][23]["content"][0] = raw["slides"][23]["content"][0].replace(
        "订正答案：强电解质；混合物两者都不是；非电解质；强电解质；强电解质。\n\n", ""
    )
    raw["slides"][23]["teacher_notes"] = (
        "前2分钟整理，再口答五种物质。教师参考：强电解质；混合物两者都不是；非电解质；强电解质；强电解质。"
        "学生答后口头核对，最后1分钟写反例。来源：" + W3 + "；" + W4
    )
    textbook_table = deepcopy(sheets[6]["sections"].pop())
    textbook_table["prompt"] = "先完成下面的规则笔记；到PPT第37页再独立写六式，第38页讲评订正。"
    sheets[4]["title"] = "学习单5 规则与教材书写"
    sheets[4]["sections"][0] = textbook_table
    sheets[4]["sections"][1]["response_lines"] = 2
    sheets[4]["sections"][2]["response_lines"] = 2
    sheets[6]["sections"][0]["response_lines"] = 6
    sheets[6]["sections"][1]["response_lines"] = 5
    sheets[6]["instructions"].append("六个教材电离式写在学习单5的表中，不重复抄题。")
    sheets[7]["sections"].pop()
    sheets[7]["instructions"].append("可选迁移题见PPT第44页，可写在练习本中。")
    raw["slides"][36]["teacher_notes"] += " 本题六个电离式写在学习单5。"
    titles = [
        "教材实验与分类知识",
        "分类例题与条件变式",
        "电离微观图与三状态题",
        "强弱辨析与第一课时整理",
        "电离方程式基础与规则",
        "弱酸分步与酸式盐",
        "讲义纠错与教材书写",
        "综合检测与两课时笔记",
    ]
    criteria = [
        [
            "记录对应教材样品和状态，不编造实验数据",
            "定义保留化合物、或与和、自身电离，并给反例",
        ],
        ["变式1选B，区分混合物与非电解质", "变式2选B，说明HCl与KCl的不同状态条件"],
        [
            "电离不需通电，导电需自由移动离子与电场",
            "三状态题选AC，解释a晶体、b熔融、c水溶液",
        ],
        [
            "全部弱电解质题选A并识别各组反例",
            "NH₄Cl与BaSO₄是强电解质，判据不换成溶解性或酸碱性",
        ],
        [
            "水及四类基本式正确，整体离子不乱拆",
            "检查符号、离子、系数与条件，而非只查电荷",
        ],
        ["H₂S分两步写可逆式", "NaHSO₄区分水溶液与熔融；NaHCO₃盐电离与酸根电离分开"],
        [
            "例题2选C并改正其余三项",
            "难溶题两项推断错，但溶解平衡式本身正确",
            "六个教材电离式的离子、符号、系数正确",
        ],
        [
            "离堂检测能用条件解释分类与状态",
            "能以醋酸符号错误反驳守恒充分论",
            "四条总笔记有依据与具体例证",
        ],
    ]
    for a, title in enumerate(titles, 1):
        selected = [
            (i, s)
            for i, s in enumerate(raw["slides"], 1)
            if s["activity_numbers"] == [a]
        ]
        objectives = sorted({o for _, s in selected for o in s["objective_numbers"]})
        action = "\n\n".join(
            f"PPT第{i}页 {s['title']} {s['minutes']}分钟\n"
            + "\n".join(s["content"])
            + "\n"
            + s["teacher_notes"]
            for i, s in selected
        )
        act = dict(
            title=title,
            objective_numbers=objectives,
            minutes=sum(s["minutes"] for _, s in selected),
            teacher_action=action,
            student_action=f"完成学习单{a}，先答题后订正，保留条件与判断依据。",
            materials=[
                f"PPT第{selected[0][0]}—{selected[-1][0]}页",
                f"学习单{a}",
                "教材56—58页与Word第3—8页",
            ],
            worksheet=sheets[a - 1],
        )
        raw["activities"].append(act)
        raw["assessments"].append(
            dict(
                title=title + "学习证据",
                objective_numbers=objectives,
                activity_numbers=[a],
                evidence_of_learning=act["student_action"],
                success_criteria=criteria[a - 1],
            )
        )
        stage = {k: deepcopy(v) for k, v in act.items() if k != "worksheet"}
        stage.update(
            activity_numbers=[a],
            assessment_numbers=[a],
            assessment="；".join(criteria[a - 1]),
        )
        raw["lesson_stages"].append(stage)
    raw["homework"] = dict(
        title="订正与迁移",
        estimated_minutes=12,
        tasks=[
            dict(
                instruction="必做：重做错题，补一句依据；遮住答案复写教材六个电离式。",
                objective_numbers=[1, 2, 3, 4],
            ),
            dict(
                instruction="可选：类比水写H₂O₂自偶电离式。参考2H₂O₂ ⇌ H₃O₂⁺ + HO₂⁻，不列为必背。",
                objective_numbers=[4],
            ),
        ],
    )
    raw["uncertainties"] = [
        dict(
            field="授课前确认",
            description="两个40分钟为复习设计预算，尚未真人教师审核或课堂试讲。",
            teacher_action="先确认本班已学基础概念；若基础薄弱，酸式盐改作后续课，不挤占答题和笔记时间。",
        ),
        dict(
            field="讲义校正",
            description="原讲义第7页部分箭头缺字，例题2标作整理；难溶盐变式明确区分溶解平衡。",
            teacher_action="讲评使用本稿完整式，不展示缺字原选项；原卷身份未经独立核验，不称官方原题。",
        ),
        dict(
            field="内容层级",
            description="弱酸分步、酸式盐来自讲义补充，并非教材56—58页原句；H₂O₂为可选拓展。",
            teacher_action="教材原句、讲义归纳和课堂整理分别讲清；NaHSO₄按中学讲义简化表达。",
        ),
        dict(
            field="实验安排",
            description="只使用教材图和参考现象，未开展真实实验或取得学生表现数据。",
            teacher_action="不安排熔盐或气体实验，若改现场实验需另作器材条件与安全核查。",
        ),
    ]
    return raw, coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-python", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT)
    if output.exists():
        raise RuntimeError("Retain old outputs; use a new directory")
    assert all(sha(p) == h for p, h in EXPECTED.items())
    meta, data, keys = assets()
    raw, coverage = lesson(keys)
    brief = build_brief()
    brief["image_assets"] = meta
    brief["materials"] = (
        "源文件Word第3—8页已逐页阅读，教材印刷56—58页已逐页核对。"
        "以Word考点一的三个知识点、两个考向、两例六变式组织；其中两个式子辨析题标明整理或改编，H₂O₂留作可选。"
        "定义及图按教材页面核对；未改变教材蒸馏记录的人工审核及权限。"
    )
    brief["advanced"]["learning_and_experiment"] = (
        "两课时各40分钟复习，纳入讲义多元弱酸及酸式盐，H₂O₂课后可选。只读图，不开展实验。"
    )
    candidate = normalize_preparation_candidate(raw, brief)
    candidate["source_basis"]["statement_zh"] = (
        "依据对应Word讲义第3—8页考点一与沪科技教材印刷56—58页。教材原句、讲义归纳、整理题和课堂检测分开标明。"
    )
    for u in candidate["uncertainties"]:
        if u["field"] == "source_basis":
            u["description"] = (
                "本次为本地来源研读后的课件重组。助手已逐页查看上述来源，6幅图片含4幅教材图、1处教材原段、1幅讲义题图；强弱定义另以可编辑原句呈现。未新增模型API调用，仍为个人备课候选，不代替教师审核或试讲。"
            )
    candidate["candidate_id"] = (
        "PREPCAND-" + _canonical_candidate_digest(candidate)[:32]
    )
    assert len(raw["slides"]) == 44
    assert [
        sum(s["minutes"] for s in raw["slides"][:24]),
        sum(s["minutes"] for s in raw["slides"][24:]),
    ] == [40, 40]
    assert all(
        sum(s["minutes"] for s in candidate[k]) == 80
        for k in ("slides", "activities", "lesson_stages")
    )
    assert not any(u["field"] == "timing_alignment" for u in candidate["uncertainties"])
    result = BundledArtifactRenderer(args.artifact_python).render(
        candidate,
        output_kind="linked_bundle",
        output_dir=output,
        report_progress=lambda *_: None,
        is_cancelled=lambda: False,
        image_data=data,
    )
    for name, value in [
        ("word-led-raw-candidate.json", raw),
        ("teacher-brief.json", brief),
        ("source-coverage.json", coverage),
    ]:
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    report = dict(
        slide_count=44,
        period_minutes=[40, 40],
        source_images=6,
        worksheet_units=8,
        word_task_coverage="7 classroom tasks (2 explicitly adapted) + 1 optional homework task",
        source_files_unchanged=all(sha(p) == h for p, h in EXPECTED.items()),
        new_model_calls=0,
        teacher_approval=False,
        classroom_validation=False,
        visual_review="pending",
        artifacts={
            p.name: sha(p) for p in output.iterdir() if p.suffix in (".pptx", ".docx")
        },
    )
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    print("renderer", sorted(result))


if __name__ == "__main__":
    main()
