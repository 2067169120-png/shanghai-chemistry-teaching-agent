"""One explicit live source-led lesson through production facade/provider.

Uses the normally configured DeepSeek profile, isolated task state and bundled
artifact runtime. No credential strings or raw HTTP body are logged. Refuses
existing output directories; never automatically retries a paid invocation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from verify_real_blueprint_preparation import (
    ROOT,
    BundledArtifactRenderer,
    DesktopPaths,
    DesktopWorkbenchFacade,
    ModelProviderSettingsStore,
    ObservedTransport,
    default_state_root,
)

from integrations.deeptutor_shchem_v1.desktop_preparation import (
    _strict_json_load,
    normalize_preparation_payload,
)
from integrations.deeptutor_shchem_v1.desktop_preparation_provider import (
    PREPARATION_PROMPT_REVISION,
)

WORD = (
    ROOT
    / "sh-chem-db/.intake/2026-07-30-user-teaching-pack/expanded/PKG-032/第04讲 离子反应和离子方程式（复习讲义）（上海专用）（解析版）.docx"
)
FIGURE = (
    ROOT / "outputs/备课/2026-09-09-电解质与电离方程式-修订版/textbook-figure-2-14.png"
)
BOOK = ROOT / "课本/沪科技化学必修第一册【高清教材】.pdf"
EXPECTED = {
    WORD: "d60317b8e533b957943e98b481305b85557d030d3056bf2eb0e9273f2811162d",
    FIGURE: "f375638dde10576147a56d6c71c890187ca9a3e6179236612b3077733554f3bf",
    BOOK: "a565f0a15ffd10c704f4be42bfe7200c125b68959d11ef582acdc45dde2ccf22",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def teacher_payload(facade, *, textbook_excerpts=False):
    for path, expected in EXPECTED.items():
        if digest(path) != expected:
            raise RuntimeError("Source changed; review before invoking model")
    word = facade.preparation_word_preview(str(WORD))
    concepts = facade.preparation_concept_options("TB-M1-C2-S22-C0")
    selected = [
        {k: row[k] for k in ("concept_id", "revision")}
        for row in concepts
        if row["concept_id"]
        in {"TB-M1-C2-S22-C05", "TB-M1-C2-S22-C06", "TB-M1-C2-S22-C07"}
    ]
    if len(selected) != 3:
        raise RuntimeError("Expected source concepts missing")
    kwargs = {}
    if textbook_excerpts:
        chosen = next(
            row for row in selected if row["concept_id"] == "TB-M1-C2-S22-C06"
        )
        kwargs["textbook_excerpts"] = [
            {
                **chosen,
                "source_sha256": EXPECTED[BOOK],
                "pdf_page": 62,
                "text": "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。",
                "confirmed": True,
            }
        ]
    ref = facade.preparation_source_reference(
        str(WORD), word["source_sha256"], 42, 63, selected, **kwargs
    )
    image = facade.import_preparation_image(
        str(FIGURE),
        "图2.14 氯化钠电离过程示意图",
        "沪科技化学必修第一册 印刷第57页（PDF第62页）图2.14",
        "观察晶体、溶于水、熔融两条路径；比较离子存在与能否自由移动。图有水分子、水合离子、钠离子和氯离子图例，不是实验录像。",
    )
    return {
        "output_kind": "joint",
        "topic": "电解质与电离方程式",
        "audience": "高二化学复习；实际班级学情尚未提供",
        "lesson_route": "复习",
        "lesson_timing": "1课时×40分钟",
        "objective": "能依据定义条件判断电解质，区分电离与导电，按电离程度比较强弱电解质，写出基础电离方程式并说明原子和电荷守恒；用一页笔记重建知识联系。",
        "materials": ref["materials"]
        + "\n\n本次原教材页面核对补充（模型视觉核对，不是教师正式审核）：\n"
        "教材印刷57页明确区分产生自由移动离子与外接电源形成闭合通路后的电流，不能把C06的压缩表述理解为电离自动产生电流。"
        "同页例式为NaCl=Na⁺+Cl⁻、HCl=H⁺+Cl⁻、NaOH=Na⁺+OH⁻；书写任务为Ba(OH)₂、Na₂SO₄、BaCl₂。"
        "教材58页以水溶液中全部/部分电离区分强弱，弱电解质使用可逆符号，例式CH₃COOH⇌H⁺+CH₃COO⁻。"
        "上述内容据实际页面读取，独立于Word缺失对象；不要把它们描述为恢复了原Word公式。",
        "image_assets": [image],
        "advanced": {
            "learning_and_experiment": "仅设计一课时复习，不扩展酸式盐、多元电离、特殊电解质、离子共存与检验。本课使用教材图和原有现象讨论，不实施熔盐或气体实验。保留资料中的冲突并具体提示核验，不用键极性大小作为强弱电解质的直接判据。",
            "template_and_delivery": "使用现有软件模板。形成开场问题—概念与微观解释—符号表达—回到开场问题的课堂推进。PPT学生页面不堆教师待办；教师备注写具体提问、反馈、过渡及来源位置。把问题和答案反馈分开，安排能实际书写的笔记时间，配套可打印学习单，留定义条件、解释、比较与方程式的填写空间，不预填所有答案。将已提供教材图放进相关观察页，保留原图比例、图例及来源。",
            "homework_and_strategy": "可以围绕本次选定概念设计简短口头辨析和离堂回顾，标作课堂设计而非上海原题；基础方程式练习限于上述教材已列物质。课后任务以补全与重建笔记为主，不生成整套新试卷，不编造学生统计或官方采分点。",
        },
    }, ref


def chapter_notes_payload(payload, *, include_legacy_quote=True):
    """Explicit revised brief, not a like-for-like rerun of the older sample."""
    revised = deepcopy(payload)
    revised["topic"] = "电解质的电离"
    if include_legacy_quote:
        revised["materials"] += (
            "\n\n本轮核对的教材原文（沪科技化学必修第一册，印刷第57页）：\n"
            "电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。\n"
        )
    revised["materials"] += (
        "\n\n"
        "教材第56页小节名为‘电解质的电离’，上位章节为第2章‘海洋中的卤素资源’，"
        "节名为2.2‘氧化还原反应和离子反应’。只覆盖本次已选概念，不扩展为整个2.2节。"
    )
    revised["advanced"]["template_and_delivery"] += (
        "\n教师最新要求：首页标题为教材小节名‘电解质的电离’，导入问题放在后续页。"
        "PPT以授课和学生记笔记为主，知识讲解页采用知识点名称。"
        "保留上述电离定义原句，并以完整的知识表格讲清电解质与非电解质、"
        "电离与导电、强弱电解质的区别；表中写出内容，不只留任务或空格。"
        "原句注明教材页码，归纳表注明依据教材整理；学习单可以留白，不与投影表混淆。"
    )
    return revised


def word_led_two_period_payload(facade):
    """Use the real chapter selection, not the manually authored slide answers."""
    from build_notebook_complete_ionization import CROPS
    from build_word_led_ionization import ASSETS

    for path, expected in EXPECTED.items():
        if digest(path) != expected:
            raise RuntimeError("Source changed; review before invoking model")
    base = ROOT / "outputs/备课/2026-09-09-电解质的电离-讲义笔记完整版-r5"
    payload = json.loads((base / "teacher-brief.json").read_text("utf-8"))
    word = facade.preparation_word_preview(str(WORD))
    chapter = [row for row in word["sections"] if "考点一" in row["title"]]
    if len(chapter) != 1 or (chapter[0]["start"], chapter[0]["end"]) != (39, 123):
        raise RuntimeError("Actual handout chapter changed; review before model call")
    selected = [
        {key: row[key] for key in ("concept_id", "revision")}
        for row in facade.preparation_concept_options("TB-M1-C2-S22-C0")
        if row["concept_id"]
        in {"TB-M1-C2-S22-C05", "TB-M1-C2-S22-C06", "TB-M1-C2-S22-C07"}
    ]
    if len(selected) != 3:
        raise RuntimeError("Expected textbook concepts missing")
    reference = facade.preparation_source_reference(
        str(WORD), word["source_sha256"], 39, 123, selected
    )
    payload["materials"] = reference["materials"] + (
        "\n\n原教材与讲义页面复核补充（主代理此前逐页核对，不是独立教师正式审核）：\n"
        "教材印刷56页小节名为电解质的电离，上位为第2章海洋中的卤素资源、2.2氧化还原反应和离子反应。"
        "第57页原句：电解质在水溶液中或熔融状态下，形成可以自由移动离子的过程称为电离。"
        "电离不要求通电；导电还需外接电源形成闭合通路，不能从C06的压缩语句推出电离自动产生电流。"
        "第58页原句：像氯化钠、氯化氢、氢氧化钠等在水溶液中能够全部电离为自由移动离子的电解质称为强电解质。"
        "像醋酸、一水合氨（NH₃·H₂O）等在水溶液中仅有部分分子能电离出自由移动离子的电解质称为弱电解质。"
        "教材57—58页例式包含NaCl=Na⁺+Cl⁻、HCl=H⁺+Cl⁻、NaOH=Na⁺+OH⁻、CH₃COOH⇌H⁺+CH₃COO⁻、"
        "NH₃·H₂O⇌NH₄⁺+OH⁻、H₂O⇌H⁺+OH⁻；书写任务含Ba(OH)₂、Na₂SO₄、BaCl₂。"
        "这些是独立教材依据，不是补读了Word缺失公式。\n"
        "讲义关系图把弱酸弱碱放入非电解质，属于源图错误，不使用该分类图。"
        "讲义旧公式缺失，不将占位符恢复成已核验原题。允许以下明确标为课堂整理的例2："
        "判断正确项并改正错误项：A KNO₃=K⁺+NO₃⁻并在等号上标通电；B H₂S=2H⁺+S²⁻；"
        "C NH₃·H₂O⇌NH₄⁺+OH⁻；D NaClO=Na⁺+Cl⁻+O²⁻。参考C，A不需通电，B弱酸分步可逆，D保留ClO⁻。"
        "这不是宣称恢复原印刷符号或核验原卷出处。"
        "难溶盐部分必须区分溶解平衡与电离程度；BaSO₄(s)⇌Ba²⁺(aq)+SO₄²⁻描述溶解平衡，本身不说明它是弱电解质。"
        "NaHSO₄水溶液Na⁺、H⁺、SO₄²⁻的表达按本中学讲义简化，不扩张为所有浓度的严格微粒清单。"
        "讲义题目年份/学校仅为讲义自带标注，未核验原卷，不称官方原题或官方评分点。"
    )
    image_paths = {
        digest(path): path
        for folder in (ASSETS, CROPS)
        for path in folder.glob("*.png")
    }
    image_paths[digest(FIGURE)] = FIGURE
    payload["image_assets"] = [
        facade.import_preparation_image(
            str(image_paths[row["sha256"]]),
            row["caption"],
            row["source"],
            row["purpose"],
        )
        for row in payload["image_assets"]
    ]
    payload["advanced"]["learning_and_experiment"] += (
        " 纳入概念分类、强弱判断、基本电离式、H₂S分步与NaHCO₃/NaHSO₄；"
        "两性氢氧化物、硼酸、次磷酸、联氨及下一考点不纳入本课。"
    )
    payload["advanced"]["template_and_delivery"] += (
        " 先分析讲义知识总结和例题如何服务目标，再设计学生先答后评与笔记环节；"
        "无需复制既有示例课的页数。每课时40分钟，保留学生书写时间。"
    )
    return payload, reference


def studied_draft_payload(facade):
    """Use the exact saved, source-studied brief without changing the daily draft."""
    from prepare_word_studied_ionization_draft import OUTPUT, validate_brief

    from integrations.deeptutor_shchem_v1.desktop_preparation_drafts import (
        PreparationDraftService,
    )
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
        PreparationImageStore,
    )
    from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore

    saved = json.loads((OUTPUT / "saved-draft-receipt.json").read_text("utf-8"))
    payload = PreparationDraftService(DesktopStateStore(default_state_root())).load(
        saved["draft_id"], saved["revision"]
    )["payload"]
    if payload != json.loads((OUTPUT / "teacher-brief.json").read_text("utf-8")):
        raise RuntimeError("Saved studied draft changed; inspect before live testing")
    validate_brief(payload)
    for path, expected in EXPECTED.items():
        if digest(path) != expected:
            raise RuntimeError("Source changed; review before invoking model")
    images = PreparationImageStore(OUTPUT / "assets")
    for asset in payload["image_assets"]:
        images.load(asset)
        imported = facade.import_preparation_image(
            str(OUTPUT / "assets" / (asset["sha256"] + ".image")),
            asset["caption"],
            asset["source"],
            asset["purpose"],
        )
        if imported != asset:
            raise RuntimeError("Studied draft image binding changed")
    return payload, {
        "warnings": ["助手据原页整理与转录；尚待教师核对，并非原讲义全文。"]
    }


def source_studied_file_payload(facade, brief_path, assets_root):
    """Read a reviewable brief without mutating a saved daily-app draft."""
    brief_path = brief_path.resolve(strict=True)
    assets_root = assets_root.resolve(strict=True)
    brief_path.relative_to(ROOT)
    assets_root.relative_to(ROOT)
    payload = _strict_json_load(brief_path)
    normalize_preparation_payload(payload)
    for path, expected in EXPECTED.items():
        if digest(path) != expected:
            raise RuntimeError("Source changed; review before invoking model")
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
        PreparationImageStore,
    )

    images = PreparationImageStore(assets_root)
    # Validate the entire supplied set before importing any isolated-state asset.
    for asset in payload.get("image_assets", []):
        images.load(asset)
    for asset in payload.get("image_assets", []):
        imported = facade.import_preparation_image(
            str(assets_root / (asset["sha256"] + ".image")),
            asset["caption"],
            asset["source"],
            asset["purpose"],
        )
        if imported != asset:
            raise RuntimeError("Brief image binding changed")
    return payload, {
        "warnings": ["助手据用户来源整理；仍需教师复核，不是原讲义全文。"],
        "brief_sha256": digest(brief_path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-one-paid-call", action="store_true")
    parser.add_argument("--chapter-notes", action="store_true")
    parser.add_argument("--textbook-excerpts", action="store_true")
    parser.add_argument("--word-led-two-periods", action="store_true")
    parser.add_argument("--studied-draft", action="store_true")
    parser.add_argument("--brief-json", type=Path)
    parser.add_argument("--assets-root", type=Path)
    args = parser.parse_args()
    if bool(args.brief_json) != bool(args.assets_root):
        parser.error("--brief-json and --assets-root must be supplied together")
    if args.brief_json and any(
        (
            args.studied_draft,
            args.word_led_two_periods,
            args.chapter_notes,
            args.textbook_excerpts,
        )
    ):
        parser.error("Choose one explicit test brief")
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    if output.exists():
        raise RuntimeError("Existing run found; inspect it instead of repeating")
    renderer = BundledArtifactRenderer(args.artifact_python)
    state_root = tempfile.mkdtemp(prefix="shchem-live-source-preparation-")
    paths = DesktopPaths.from_workspace(ROOT, state_root=state_root)
    transport = ObservedTransport()
    store = ModelProviderSettingsStore(
        default_state_root() / "model-settings", project_root=ROOT
    )
    facade = DesktopWorkbenchFacade(
        paths,
        provider_store=store,
        preparation_transport=transport,
        preparation_renderer=renderer,
    )
    if args.brief_json:
        payload, reference = source_studied_file_payload(
            facade, args.brief_json, args.assets_root
        )
    elif args.studied_draft:
        if args.word_led_two_periods or args.chapter_notes or args.textbook_excerpts:
            raise RuntimeError("Choose one explicit test brief")
        payload, reference = studied_draft_payload(facade)
    elif args.word_led_two_periods:
        if args.chapter_notes or args.textbook_excerpts:
            raise RuntimeError("Choose one explicit test brief")
        payload, reference = word_led_two_period_payload(facade)
    else:
        payload, reference = teacher_payload(
            facade, textbook_excerpts=args.textbook_excerpts
        )
    if args.chapter_notes or args.textbook_excerpts:
        payload = chapter_notes_payload(
            payload, include_legacy_quote=not args.textbook_excerpts
        )
    profiles = [
        p
        for p in facade.preparation_profiles()
        if urlsplit(p.base_url).hostname == "api.deepseek.com"
    ]
    if len(profiles) != 1:
        raise RuntimeError(
            "Expected one normally configured DeepSeek preparation profile"
        )
    profile = profiles[0]
    output.mkdir(parents=True, exist_ok=False)
    (output / "teacher-brief.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    receipt = {
        "output": str(output),
        "isolated_state": state_root,
        "prompt_revision": PREPARATION_PROMPT_REVISION,
        "brief_revision": "source-studied-explicit-file"
        if args.brief_json
        else "word-studied-saved-draft-20260909-v1"
        if args.studied_draft
        else "word-led-two-periods-v19"
        if args.word_led_two_periods
        else "textbook-excerpts-v15"
        if args.textbook_excerpts
        else ("chapter-notes-v14" if args.chapter_notes else "source-led-v11"),
        "excerpt_confirmation_actor": "assistant_visual_transcription_for_isolated_QA_not_human"
        if args.textbook_excerpts
        else "not_applicable",
        "model_id": profile.model_id,
        "materials_characters": len(payload["materials"]),
        "source_warnings": reference["warnings"],
        "source_sha256": {p.name: h for p, h in EXPECTED.items()},
        "transport": transport.receipts,
        "candidate_only": True,
        "publication_allowed": False,
        "teacher_review_required": True,
        "paid_call_authorized": args.confirm_one_paid_call,
    }
    if args.brief_json:
        receipt["input_brief_sha256"] = reference["brief_sha256"]

    def save():
        (output / "verification.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    task = facade.prepare_preparation(payload, profile.profile_id, profile.revision)
    receipt["task_id"] = task.task_id
    save()
    print(
        json.dumps(
            {
                "output": str(output),
                "task_id": task.task_id,
                "model": profile.model_id,
                "calling_model": args.confirm_one_paid_call,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not args.confirm_one_paid_call:
        return 0
    try:
        result = facade.generate_preparation(
            task.task_id,
            teacher_confirmed=True,
            progress_callback=lambda value: print(
                json.dumps({"progress": value}, ensure_ascii=False), flush=True
            ),
        )
        receipt["result"] = asdict(result)
        if result.status == "completed":
            receipt["artifacts"] = {}
            for artifact_id in result.artifact_ids:
                source = facade.preparation_artifact_path(task.task_id, artifact_id)
                target = output / source.name
                shutil.copy2(source, target)
                receipt["artifacts"][artifact_id] = {
                    "path": str(target),
                    "sha256": digest(target),
                }
    except Exception as exc:  # noqa: BLE001 - sanitized live-test receipt only
        receipt["error_type"] = type(exc).__name__
        code = getattr(exc, "code", "")
        receipt["error_code"] = (
            code
            if isinstance(code, str) and code.replace("_", "").isalnum()
            else "unknown"
        )
    finally:
        if transport.structured_candidate is not None:
            target = output / "returned-candidate.json"
            target.write_text(
                json.dumps(
                    transport.structured_candidate, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            receipt["returned_candidate"] = {
                "path": str(target),
                "sha256": digest(target),
            }
        receipt["original_sources_unchanged"] = all(
            digest(p) == h for p, h in EXPECTED.items()
        )
        save()
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if receipt.get("result", {}).get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
