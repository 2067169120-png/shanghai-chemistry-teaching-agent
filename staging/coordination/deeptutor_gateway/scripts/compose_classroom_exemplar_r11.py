"""Offline classroom-facing revision; no provider or original-file mutations."""

import hashlib
import json

import compose_course_exemplar_r10 as prior

ROOT = prior.ROOT
SOURCE = ROOT / "outputs/备课/2026-09-09-电解质的电离-课程重组版-r10/投影核对版"
OUT = ROOT / "outputs/备课/2026-09-09-电解质的电离-课堂投影版-r11/课堂版"
QA = ROOT / "runtime/deeptutor_shchem/qa/classroom-projection-r11-20260909/final"
TITLES = [
    "电解质的电离",
    "NaCl晶体为什么不导电？",
    "一、物质的导电性",
    "一、物质的导电性",
    "一、物质的导电性",
    "二、电离",
    "二、电离",
    "二、电离与导电",
    "三、电解质与非电解质",
    "三、电解质与非电解质",
    "练习：判断物质类别",
    "物质类别的判断依据",
    "例题：电解质的判断",
    "电解质的判断方法",
    "练习：导电的条件",
    "导电的条件",
    "练习：NaCl的三种状态",
    "练习：NaCl的三种状态",
    "NaCl的三种状态",
    "NaCl晶体为什么不导电？",
    "小结：分类与导电",
    "四、强电解质与弱电解质",
    "四、强电解质与弱电解质",
    "四、强电解质与弱电解质",
    "强弱电解质的比较",
    "例题：弱电解质的判断",
    "弱电解质的判断依据",
    "练习：氯化铵的类别",
    "氯化铵的电离",
    "强弱电解质的比较",
    "五、电离方程式",
    "例题：硫酸铵的电离",
    "练习：强电解质的电离",
    "强电解质的电离方程式",
    "弱电解质的电离方程式",
    "水的电离",
    "水的电离",
    "练习：弱电解质的电离",
    "弱电解质的电离方程式",
    "多元弱酸的电离",
    "练习：电离方程式辨析",
    "电离方程式的易错点",
    "课堂检测",
    "小结：强弱电解质",
    "小结：电离方程式",
]


def compose(raw):
    for i, (slide, title) in enumerate(zip(raw["slides"], TITLES, strict=True), 1):
        slide["teacher_notes"] += (
            f"\nR11课堂呈现：原设计标题为{slide['title']}；保留来源编号供备课检索。"
        )
        slide["title"] = title
    edits = {
        2: ["NaCl晶体不导电，能否据此判定它不是电解质？", "写出你的判断和理由。"],
        3: ["固体与水溶液中，灯泡发光情况有何不同？"],
        4: ["不加水，只改变硝酸钾的状态，能否导电？"],
        5: ["比较硝酸钾在三种状态下的导电性。"],
        6: ["晶体中原来就有离子吗？两条路径的共同变化是什么？"],
        7: [
            "教材摘录：电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
        ],
        9: ["注意定义中的“化合物”及“或／和”。"],
        17: ["X接电源正极，Y接电源负极。"],
        20: [
            "NaCl晶体不导电，能否据此否定其电解质身份？",
            "从定义中的条件、晶体中离子的移动能力两方面说明。",
        ],
        21: ["分类看规定条件，导电看当前状态。"],
        22: [
            "电解质进入水中后，是否都能全部电离？",
            "电离程度不同，电离方程式应怎样表示？",
        ],
        23: ["区别在于水溶液中的电离程度，不是灯泡亮暗。"],
        28: [
            "氯化铵属于什么？写出判断依据。",
            "A．强电解质　　B．弱电解质",
            "C．非电解质　　D．以上都不正确",
        ],
        32: [
            "硫酸铵（水溶液）：(NH₄)₂SO₄＝2NH₄⁺＋SO₄²⁻",
            "NH₄⁺、SO₄²⁻保持整体。",
            "电荷检查：2×(+1)+(−2)=0。",
            "原子检查：两侧N、H、S、O原子数分别相等。",
        ],
        33: [
            "写出下列物质在水溶液中的电离方程式。",
            "（1）氢氧化钡　Ba(OH)₂",
            "（2）硫酸钠　Na₂SO₄",
            "（3）氯化钡　BaCl₂",
        ],
        38: [
            "独立写出下列物质在水溶液中的电离方程式。",
            "（4）醋酸　CH₃COOH",
            "（5）一水合氨　NH₃·H₂O",
            "（6）水　H₂O",
        ],
        43: [
            "判断物质类别、强弱电解质，各需要哪些条件？",
            "选本课一道错题，说明错误原因并改正。",
        ],
        44: ["判断强弱的依据是电离程度。"],
        45: ["先判类别与条件，再写离子、选符号，最后检查。"],
    }
    prompts = {
        3: "比较KNO₃固体与水溶液。",
        4: "观察教材图2.13中的加热过程。",
        6: "比较NaCl溶于水与熔融两条路径。",
        7: "圈出“自由移动离子”和“过程”。",
        9: "阅读教材定义。",
        17: "观察图a、b、c中的粒子与状态。",
        23: "阅读教材原句，找出“全部”与“部分”。",
        36: "观察反应前后微粒的变化。",
    }
    edits[36] = ["水的电离怎样用方程式表示？"]
    for n, lines in edits.items():
        slide = raw["slides"][n - 1]
        slide["teacher_notes"] += "\n调整前投影文字：" + "；".join(slide["content"])
        slide["content"] = lines
    for n, prompt in prompts.items():
        raw["slides"][n - 1]["image"]["observation_prompt"] = prompt
    return raw


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    QA.mkdir(parents=True, exist_ok=False)
    original = (SOURCE / "edited-raw-candidate.json").read_bytes()
    raw = compose(json.loads(original))
    brief = json.loads((SOURCE / "teacher-brief.json").read_text("utf-8"))
    brief["advanced"]["template_and_delivery"] += (
        "\n约40人课堂；短学科标题、充分放大的题干和教材图；教学设计语言只放备注。"
    )
    canonical = prior.normalize_preparation_candidate(
        raw, prior.normalize_preparation_payload(brief)
    )
    for name, value in [
        ("candidate.json", canonical),
        ("edited-raw-candidate.json", raw),
        ("teacher-brief.json", brief),
    ]:
        (OUT / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), "utf-8"
        )
    assets = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义精读生成材料/assets"
    images = {
        "IMG-" + hashlib.sha256(p.read_bytes()).hexdigest(): p.read_bytes()
        for p in assets.glob("*.image")
    }
    layouts = prior._make_layouts(
        canonical, image_data=images, classroom_projection=True
    )
    prior._write_pptx(OUT / "lesson_presentation.pptx", layouts, canonical, root=OUT)
    fonts = prior._FontBook()
    for i, layout in enumerate(layouts, 1):
        prior._save_png(
            prior._draw_layout(layout, fonts), QA / f"slide-{i:02d}.png", root=QA
        )
    (QA / "layout-report.json").write_text(
        json.dumps([x.public() for x in layouts], ensure_ascii=False, indent=2), "utf-8"
    )
    (OUT / "本次完整生成提示词.txt").write_text(prior._prompt(brief), "utf-8")
    guide = [
        "# R11课堂投影版说明",
        "",
        "45页，第一课时1—21页40分钟，第二课时22—45页40分钟。时间为设计预算。",
        "",
        "这是离线编辑样课，不是v21真实模型生成结果。保留教材、讲义来源和先题后答；改为短学科标题、白底宽幅正文，移除装饰侧栏和自动条目编号。",
        "",
        "教材和讲义来源未更换，原图8项未改字节；常考总结依据讲义考向，不代表统计过考频。旧R9/R10及母文件保留。",
        "",
        "本版没有同步Word学习单，请勿与R9学习单混用。40人后排投影可读性尚需在真实教室验证；程序和预览不代替教师审核。",
        "",
        "## 逐页教师备注",
        "",
    ]
    for s in canonical["slides"]:
        guide += [
            f"### {s['order']}. {s['title']}（{s['minutes']}分钟）",
            "",
            s["teacher_notes"],
            "",
        ]
    (OUT / "课堂投影版授课说明.md").write_text("\n".join(guide), "utf-8")
    assert (SOURCE / "edited-raw-candidate.json").read_bytes() == original
    print(
        json.dumps(
            {
                "slides": len(layouts),
                "overflow": [
                    x.page_no for x in layouts if any(e.overflow for e in x.elements)
                ],
                "model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
