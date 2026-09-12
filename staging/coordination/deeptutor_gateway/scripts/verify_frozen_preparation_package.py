"""Inspect a built desktop archive, zip it and verify a fresh extraction.

Does not start the app, read personal state, import provider credentials or call
models. Generated ZIPs and fresh temporary directories are never overwritten.
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import inspect
import io
import json
import tempfile
import types
import zipfile
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from PyInstaller.archive.readers import CArchiveReader

PROMPT_REVISION = "20260912-source-studied-guidance-v25"
WORD_BOUNDARY_REVIEW_REVISION = "20260912-explicit-source-boundary-review-v1"
WORD_RANGE_PREVIEW_UI_CONSTANTS = (
    "查看本次范围预览",
    "本次范围预览",
    "保存本题范围",
    "已核对无答案标签的原文分界",
    "已核对非标准题目标记的边界",
    "题面本身已包含所引用的全部材料",
    (
        "预览后，仅勾选你已核对的事项；未勾选也可保存范围，但对应提醒不会因此解除。"
        "这些确认只针对所选原文的分界和材料位置，不代表化学正确性或官方答案审核。"
    ),
    "范围已变化；请重新预览，之前的勾选确认已清除。",
    "请先点击“查看本次范围预览”，核对后再保存。",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree(root: Path) -> dict[str, dict]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, types.CodeType):
        value = value.co_consts
    if isinstance(value, (tuple, frozenset)):
        return set().union(*(strings(item) for item in value))
    return set()


def _frozen_function(code, name):
    matches = [
        item
        for item in code.co_consts
        if isinstance(item, types.CodeType) and item.co_name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Frozen function missing or ambiguous: {name}")
    return matches[0]


def frozen_image_namespaces(pyz):
    """Load only pure definitions; no image store or provider is instantiated."""
    allowed = {
        "__future__",
        "collections.abc",
        "typing",
        "hashlib",
        "io",
        "os",
        "re",
        "pathlib",
        "uuid",
        "PIL",
    }

    def pure_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in allowed:
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise RuntimeError("Unexpected dependency in frozen pure image module")

    result = []
    for suffix in ("desktop_preparation_image_input", "desktop_preparation_images"):
        namespace = {
            "__name__": "frozen_" + suffix,
            "__builtins__": dict(vars(builtins), __import__=pure_import),
        }
        exec(  # noqa: S102 - isolated pure definitions and import allowlist
            pyz.extract("integrations.deeptutor_shchem_v1." + suffix), namespace
        )
        result.append(namespace)
    return result


def _synthetic_image_metadata(number=1):
    return {
        "asset_id": "IMG-" + f"{number:064x}",
        "sha256": f"{number:064x}",
        "caption": "合成教学图片",
        "source": "Synthetic inspection fixture",
        "purpose": "检查冻结图片合同",
        "width": 160,
        "height": 90,
        "content_type": "image/png",
    }


def verify_frozen_note_prompt(pyz) -> bool:
    """Execute only the frozen pure prompt formatter, never the provider."""
    code = pyz.extract("integrations.deeptutor_shchem_v1.desktop_preparation_provider")
    constants = strings(code)
    revision = PROMPT_REVISION
    checks = [
        value for value in constants if value.startswith("\n授课与笔记最终检查（")
    ]
    if revision not in constants or len(checks) != 1:
        raise RuntimeError("Frozen classroom note contract missing")
    checklist = checks[0]
    required = (
        "每张知识表在学生可见处写明对象和适用条件",
        "不要把待判断物质的分类结果当成例子提前列出",
        "再提供已填写完整的知识汇总",
        "记录栏目和检查数量必须一致",
        "逐课时核对PPT、活动、教案的时间",
        "具体例题的题干、分析过程、结论和依据",
        "练习页与讲评页保留相同题号和对应评价关联",
        "逐项回看对应讲义的知识点总结、得分速记、例题与变式",
        "不把‘常考’写成未经统计的频次结论",
        "来源支持的完整例式或例证",
        "同页或紧邻页的可编辑原句或知识归纳",
        "不凭图片元数据转写或虚构原句图",
    )
    if any(text not in checklist for text in required):
        raise RuntimeError("Frozen classroom note contract incomplete")
    source_rules = next(
        value
        for value in strings(
            pyz.extract(
                "integrations.deeptutor_shchem_v1.desktop_chemistry_prompt_rules"
            )
        )
        if value.startswith("教师资料与选题完整性规则（teacher-source-closure-v2）")
    )
    prompt_code = next(
        item
        for item in code.co_consts
        if isinstance(item, types.CodeType) and item.co_name == "_prompt"
    )
    modes, images = frozen_image_namespaces(pyz)
    namespace = {
        "deepcopy": deepcopy,
        "json": json,
        "PREPARATION_PROMPT_REVISION": revision,
        "CLASSROOM_NOTE_FINAL_CHECK": checklist,
        "TEACHING_SOURCE_RULES": source_rules,
        "course_composition_contract": frozen_course_namespace(pyz)[
            "course_composition_contract"
        ],
        "image_input_mode": modes["image_input_mode"],
        "normalize_image_assets": images["normalize_image_assets"],
    }
    namespace["_image_input_instructions"] = types.FunctionType(
        _frozen_function(code, "_image_input_instructions"), namespace
    )
    prompt = types.FunctionType(prompt_code, namespace)
    base = {"topic": "课堂笔记检查", "materials": "完整参考资料-不截断"}
    asset = _synthetic_image_metadata()
    cases = [
        (base, "没有看到图片像素"),
        (
            {**base, "image_input_mode": "local_only", "image_assets": [asset]},
            "没有看到图片像素",
        ),
        (
            {**base, "image_input_mode": "vision", "image_assets": [asset]},
            "随本请求附上图片像素",
        ),
        (
            {**base, "image_input_mode": "vision", "image_assets": []},
            "没有收到任何图片像素",
        ),
    ]
    for brief, expected in cases:
        original = deepcopy(brief)
        rendered = prompt(brief)
        embedded, _ = json.JSONDecoder().raw_decode(
            rendered.split("教师备课简报 JSON：\n", 1)[1]
        )
        if (
            brief != original
            or embedded != original
            or not rendered.endswith(checklist)
        ):
            raise RuntimeError("Frozen prompt changed source or omitted final check")
        if "G 课堂投影：默认服务约40人班级" not in rendered:
            raise RuntimeError("Frozen prompt omitted classroom course contract")
        if source_rules not in rendered:
            raise RuntimeError("Frozen prompt omitted teacher source closure contract")
        if expected not in rendered:
            raise RuntimeError("Frozen conditional image prompt differs")
        if (
            brief.get("image_input_mode") == "vision"
            and brief.get("image_assets")
            and (
                "没有看到图片像素" in rendered
                or "图片转写，待教师核对" not in rendered
                or f'"attachment_number":1,"asset_id":"{asset["asset_id"]}"'
                not in rendered
            )
        ):
            raise RuntimeError(
                "Frozen visual prompt lost image order or source caution"
            )
    return True


def verify_frozen_preparation_image_input(pyz) -> dict:
    """Exercise frozen pure bindings and adapter; inspect provider routes statically.

    Synthetic pixels and a recorder function are inspector fixtures. No provider
    object or transport is constructed and no network behavior is claimed.
    """
    modes, images = frozen_image_namespaces(pyz)
    mode, mode_error = modes["image_input_mode"], modes["PreparationImageInputError"]
    for payload, expected in (
        ({}, "local_only"),
        ({"image_input_mode": "local_only"}, "local_only"),
        ({"image_input_mode": "vision"}, "vision"),
    ):
        if mode(payload) != expected:
            raise RuntimeError("Frozen image input mode differs")
    for invalid in (None, True, 1, "auto"):
        try:
            mode({"image_input_mode": invalid})
        except mode_error:
            pass
        else:
            raise RuntimeError("Frozen image input mode accepted invalid value")
    policy = {
        "capability_evidence": {"declared": ["vision"], "catalog": [], "probed": []},
        "effective_capabilities": ["vision", "structured_output"],
        "allowed_data_classes": ["source_page_image"],
        "image_egress": "teacher_confirmed_visual_pages",
    }
    require = modes["require_preparation_vision_policy"]
    require(policy)
    require({**policy, "image_egress": "teacher_confirmed_source_pages"})
    for changed in (
        {"capability_evidence": {"declared": [], "catalog": [], "probed": ["vision"]}},
        {"effective_capabilities": ["vision"]},
        {"allowed_data_classes": []},
        {"image_egress": "deny"},
    ):
        try:
            require({**policy, **changed})
        except mode_error:
            pass
        else:
            raise RuntimeError("Frozen visual policy accepted unconfirmed image egress")

    prefix = "integrations.deeptutor_shchem_v1."
    provider_code = pyz.extract(prefix + "desktop_preparation_provider")
    provider_class = _frozen_function(provider_code, "StructuredPreparationProvider")
    for name in ("generate", "__call__"):
        method = _frozen_function(provider_class, name)
        keyword_only = method.co_varnames[
            method.co_argcount : method.co_argcount + method.co_kwonlyargcount
        ]
        if "image_data" not in keyword_only:
            raise RuntimeError("Frozen provider image_data keyword route missing")
        if name == "generate" and not {
            "_provider_image_pages",
            "build_structured_visual_request",
            "build_structured_text_request",
        }.issubset(method.co_names):
            raise RuntimeError("Frozen provider text/visual builder route missing")
        if name == "__call__" and "image_data" not in strings(method):
            raise RuntimeError("Frozen provider call dropped image_data forwarding")

    namespace = {
        "__name__": "frozen_preparation_pixel_helper",
        "Mapping": Mapping,
        "image_input_mode": mode,
        "normalize_image_assets": images["normalize_image_assets"],
        "verify_image_bytes": images["verify_image_bytes"],
    }
    error_code = _frozen_function(provider_code, "DesktopPreparationProviderError")
    error_type = builtins.__build_class__(
        types.FunctionType(error_code, namespace),
        "DesktopPreparationProviderError",
        RuntimeError,
    )
    namespace["DesktopPreparationProviderError"] = error_type
    pixel_helper = types.FunctionType(
        _frozen_function(provider_code, "_provider_image_pages"), namespace
    )
    from PIL import Image

    assets, data = [], {}
    for number in (1, 2):
        stream = io.BytesIO()
        Image.new("RGB", (40 + number, 30), (number * 40, 60, 90)).save(stream, "PNG")
        raw = stream.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        asset = {
            **_synthetic_image_metadata(number),
            **images["image_info"](raw),
            "sha256": sha,
            "asset_id": "IMG-" + sha,
        }
        assets.append(asset)
        data[asset["asset_id"]] = raw
    payload = {"image_input_mode": "vision", "image_assets": assets}
    expected = [(asset["content_type"], data[asset["asset_id"]]) for asset in assets]
    if pixel_helper(payload, dict(reversed(list(data.items())))) != expected:
        raise RuntimeError("Frozen pixel helper changed bytes or attachment order")
    if (
        pixel_helper({"image_input_mode": "local_only", "image_assets": assets}, None)
        != []
        or pixel_helper({"image_input_mode": "vision", "image_assets": []}, None) != []
    ):
        raise RuntimeError("Frozen empty/local image routing differs")
    for brief, raw_map in (
        (payload, {}),
        (payload, {**data, "unexpected": b"extra"}),
        (payload, {**data, assets[0]["asset_id"]: b"changed"}),
        ({**payload, "image_input_mode": "local_only"}, data),
    ):
        try:
            pixel_helper(brief, raw_map)
        except (error_type, images["PreparationImageError"]):
            pass
        else:
            raise RuntimeError("Frozen pixel helper accepted invalid selected pixels")

    manager_code = pyz.extract(prefix + "desktop_preparation")
    namespace.update(
        {
            "deepcopy": deepcopy,
            "inspect": inspect,
            "preparation_candidate_schema": lambda: {"type": "object"},
        }
    )
    namespace["DesktopPreparationError"] = builtins.__build_class__(
        types.FunctionType(
            _frozen_function(manager_code, "DesktopPreparationError"), namespace
        ),
        "DesktopPreparationError",
        RuntimeError,
    )
    invoke = types.FunctionType(
        _frozen_function(manager_code, "_invoke_provider"), namespace
    )

    def recorder(*args, **kwargs):
        return kwargs.get("image_data")

    if (
        invoke(
            recorder, payload, {}, lambda value: None, lambda: False, image_data=data
        )
        != data
    ):
        raise RuntimeError("Frozen manager adapter dropped image_data")

    def text_only(brief):
        return brief

    local = {"image_input_mode": "local_only", "image_assets": assets}
    if (
        invoke(text_only, local, {}, lambda value: None, lambda: False, image_data=data)
        != local
    ):
        raise RuntimeError("Frozen manager broke legacy local-only adapter")
    try:
        invoke(
            text_only, payload, {}, lambda value: None, lambda: False, image_data=data
        )
    except namespace["DesktopPreparationError"]:
        pass
    else:
        raise RuntimeError("Frozen manager allowed vision with a text-only adapter")
    return {
        "pure_mode_cases": 7,
        "pure_vision_policy_cases": 6,
        "pure_pixel_binding_cases": 7,
        "pure_manager_adapter_cases": 3,
        "provider_keyword_and_builder_route_static_checked": True,
        "synthetic_pixel_count": 2,
        "provider_instantiated": False,
        "transport_executed": False,
        "real_model_called": False,
        "inspection_dependencies": "inspector_interpreter_and_synthetic_fixtures_not_frozen_runtime",
    }


def frozen_course_namespace(pyz):
    """Execute the pure frozen course module with an isolated route fixture.

    The route alias resolver is supplied by the inspector, not asserted as
    frozen behavior here. Route normalization has separate production tests.
    No facade, provider, UI or application state is imported.
    """
    code = pyz.extract("integrations.deeptutor_shchem_v1.desktop_preparation_pedagogy")

    def pure_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "desktop_preparation" and level == 1:
            return types.SimpleNamespace(_LESSON_ROUTE={"review": "review"})
        if name == "__future__":
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise RuntimeError("Unexpected dependency in frozen pure course module")

    namespace = {
        "__name__": "frozen_course_inspection",
        "__builtins__": dict(vars(builtins), __import__=pure_import),
    }
    exec(code, namespace)  # noqa: S102 - our pure frozen module, import allowlist
    if (
        namespace.get("COURSE_DESIGN_REVISION")
        != "20260912-electron-representation-reference-v5"
    ):
        raise RuntimeError("Frozen classroom course revision missing")
    contract = namespace["course_composition_contract"]("review")
    starter = namespace["teacher_design_starter"]("review")
    for required in (
        "advanced.template_and_delivery",
        "G 课堂投影：默认服务约40人班级",
        "普通正文及题干建议28—32pt、标题36—44pt",
        "静态导出采用题目页与解答页分开",
        "现成Word教案是内容来源，不是等待重写的提纲",
        "只收到选段就只使用选段，不声称已读整份",
    ):
        if required not in contract:
            raise RuntimeError("Frozen classroom course contract incomplete")
    if "投影对象：约40人普通课堂" not in starter:
        raise RuntimeError("Frozen editable classroom starter missing")
    courseware_starter = namespace["teacher_design_starter"]("review", "电离平衡常数")
    if (
        "fde67ee1-cbbb-4cb2-828f-e5cd813bc64e" not in courseware_starter
        or "不复用原第二课时第5—7页" not in courseware_starter
        or "未下载原PPT" not in courseware_starter
    ):
        raise RuntimeError("Frozen editable courseware reference incomplete")
    electron_starter = namespace["teacher_design_starter"](
        "review", "核外电子排布的表示方法"
    )
    if (
        "18aa1947-8aac-2411-0633-726953abb358" not in electron_starter
        or "表示法／包含信息／适用问题／易错点" not in electron_starter
        or "未下载原PPT" not in electron_starter
    ):
        raise RuntimeError("Frozen electron notation reference incomplete")
    return namespace


def verify_frozen_classroom_layout(pyz):
    code = pyz.extract(
        "integrations.deeptutor_shchem_v1.desktop_preparation_classroom_layout"
    )
    layout = next(
        item
        for item in code.co_consts
        if isinstance(item, types.CodeType) and item.co_name == "classroom_layout"
    )
    if (
        460,
        400,
        340,
        280,
        220,
    ) not in layout.co_consts or "fitted_body" not in layout.co_varnames:
        raise RuntimeError("Frozen classroom adaptive image sizing missing")
    if "overflow" not in layout.co_names:
        raise RuntimeError("Frozen classroom overflow check missing")
    return True


def verify_frozen_behavior(pyz) -> dict:
    """Run only pure policy and in-memory worksheet code extracted from EXE.

    This does not load the entrypoint, settings, state or provider transport.
    Dependencies for this inspector come from its interpreter, not the EXE.
    """
    prefix = "integrations.deeptutor_shchem_v1."
    runtime_code = pyz.extract(prefix + "visual_provider_runtime")
    limit_code = next(
        item
        for item in runtime_code.co_consts
        if isinstance(item, types.CodeType)
        and item.co_name == "structured_text_output_limit"
    )
    limit = types.FunctionType(limit_code, {"urlsplit": urlsplit})
    contexts = [
        ("https://api.deepseek.com", "deepseek-v4-flash", 65536),
        ("https://api.deepseek.com/v1", "deepseek-v4-pro", 65536),
        ("https://api.deepseek.com", "deepseek-v4-flash-vision-exp", 65536),
        ("https://api.deepseek.com", "unknown-model", 32000),
        ("https://api.deepseek.com.example", "deepseek-v4-flash", 32000),
    ]
    for base_url, model_id, expected in contexts:
        if (
            limit(types.SimpleNamespace(base_url=base_url, model_id=model_id))
            != expected
        ):
            raise RuntimeError("Frozen output-budget behavior differs")
    namespace = {"__name__": "frozen_worksheet_inspection", "Mapping": Mapping}
    worksheet_code = pyz.extract(prefix + "desktop_preparation_worksheet")
    for item in worksheet_code.co_consts:
        if isinstance(item, types.CodeType):
            namespace[item.co_name] = types.FunctionType(item, namespace)
    worksheet = {
        "title": "检查学习单",
        "instructions": ["记录观察"],
        "sections": [
            {
                "heading": "比较",
                "prompt": "填写记录",
                "response_kind": "table",
                "response_lines": 0,
                "columns": ["对象", "观察"],
                "row_labels": ["甲", "乙"],
            }
        ],
    }
    doc = namespace["build_worksheet_document"](
        {"activities": [{"worksheet": worksheet}, {"worksheet": worksheet}]}
    )
    page = doc.sections[0]
    if (page.page_width.twips, page.page_height.twips) != (11906, 16838):
        raise RuntimeError("Frozen worksheet is not A4")
    usable = page.page_width.twips - page.left_margin.twips - page.right_margin.twips
    for table in doc.tables:
        if sum(col.width.twips for col in table.columns) != usable:
            raise RuntimeError("Frozen worksheet table width differs")
        for row in table.rows:
            if sum(cell.width.twips for cell in row.cells) != usable:
                raise RuntimeError("Frozen worksheet cell width differs")
    titles = [p for p in doc.paragraphs if p.style.name == "Title"]
    if len(titles) != 2 or not titles[1].paragraph_format.page_break_before:
        raise RuntimeError("Frozen worksheet title break missing")
    if doc.element.xpath('.//w:br[@w:type="page"]'):
        raise RuntimeError("Frozen worksheet includes standalone page-break")
    return {
        "known_model_budget_cases": len(contexts),
        "worksheet_a4": True,
        "worksheet_table_widths_match": True,
        "worksheet_title_page_break": True,
        "inspection_dependencies": "inspector_interpreter_not_frozen_runtime",
    }


def verify_catalog_session(facade_code) -> bool:
    """Check the frozen catalog method, without importing the facade."""

    def methods(code):
        yield code
        for child in code.co_consts:
            if isinstance(child, types.CodeType):
                yield from methods(child)

    target = [
        code for code in methods(facade_code) if code.co_name == "_load_theme_scope"
    ]
    if len(target) != 1 or not {
        "snapshot_reader_graph",
        "groups",
        "theme_groups",
    }.issubset(target[0].co_names):
        raise RuntimeError("Frozen catalog loading lacks operation-scoped validation")
    return True


def verify_frozen_image_limit(pyz) -> bool:
    # This pure module defines metadata validation and a store class; importing
    # it does not instantiate a store, read app state or load provider settings.
    namespace = {"__name__": "frozen_preparation_images"}
    exec(  # noqa: S102 - execute only our inspected frozen metadata module
        pyz.extract("integrations.deeptutor_shchem_v1.desktop_preparation_images"),
        namespace,
    )
    if namespace["MAX_IMAGES"] != 12:
        raise RuntimeError("Frozen image capacity is not 12")
    assets = [
        {
            "asset_id": "IMG-" + f"{n:064x}",
            "sha256": f"{n:064x}",
            "caption": "Fixture",
            "source": "Synthetic",
            "purpose": "Check capacity",
            "width": 160,
            "height": 90,
            "content_type": "image/png",
        }
        for n in range(13)
    ]
    if namespace["normalize_image_assets"](assets[:12]) != assets[:12]:
        raise RuntimeError("Frozen image metadata was changed")
    try:
        namespace["normalize_image_assets"](assets)
    except namespace["PreparationImageError"]:
        return True
    raise RuntimeError("Frozen image limit was not enforced")


def normalized_code(code):
    """Ignore only the checkout path embedded by the compiler."""
    return code.replace(
        co_filename="<verified-source>",
        co_consts=tuple(
            normalized_code(value) if isinstance(value, types.CodeType) else value
            for value in code.co_consts
        ),
    )


def verify_source_matches_frozen(pyz, module: str, workspace: Path) -> dict:
    """Compare code, constants and nested methods, not just a version string."""
    source = workspace.joinpath(*module.split(".")).with_suffix(".py")
    raw = source.read_bytes()
    local = compile(raw, str(source), "exec", dont_inherit=True, optimize=0)
    frozen = pyz.extract(module)
    if normalized_code(local) != normalized_code(frozen):
        raise RuntimeError(f"Frozen module differs from current source: {module}")
    return {
        "module": module,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "code_matches": True,
    }


def verify_frozen_word_theme_index(pyz) -> dict:
    """Exercise frozen whole themes and narrow reviews with synthetic material."""
    code = pyz.extract("integrations.deeptutor_shchem_v1.desktop_word_question_index")

    def pure_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in {
            "__future__",
            "hashlib",
            "json",
            "re",
            "copy",
            "typing",
        }:
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise RuntimeError("Unexpected dependency in frozen pure Word index")

    namespace = {
        "__name__": "frozen_word_theme_index",
        "__builtins__": dict(vars(builtins), __import__=pure_import),
    }
    exec(code, namespace)  # noqa: S102 - pure frozen index with import allowlist
    if namespace.get("BOUNDARY_REVIEW_REVISION") != WORD_BOUNDARY_REVIEW_REVISION:
        raise RuntimeError("Frozen Word boundary review revision missing")
    index, apply_range = (
        namespace["index_word_questions"],
        namespace["apply_question_range"],
    )

    def make_preview(texts, assets=()):
        return {
            "source_name": "synthetic-theme.docx",
            "source_sha256": "a" * 64,
            "revision": "synthetic-preview-v1",
            "extraction_revision": "synthetic-extractor-v1",
            "blocks": [
                {"index": i, "text": text, "warnings": []}
                for i, text in enumerate(texts, 1)
            ],
            "assets": list(assets),
            "sections": [],
        }

    texts = [
        "考试时间：60分钟，满分：100分",
        "可能用到的相对原子质量：H-1",
        "一、金属应用(20分)",
        "先阅读公共材料",
        "(1)写出化学式____",
        "后续实验数据",
        "(2)计算质量____",
        "【答案】(1)合成示例",
        "(2)合成解析",
        "二、反应原理（20分）",
        "（1）解释原因____",
        "【答案】合成示例",
    ]
    preview = make_preview(texts)
    original = deepcopy(preview)
    items = index(preview)
    if len(items) != 2 or items[0]["selection_unit"] != "theme_big_question":
        raise RuntimeError("Frozen Word index did not retain whole themes")
    if items[0]["printed_subpart_starts"] != [5, 7]:
        raise RuntimeError("Frozen Word theme subparts differ")
    if [block["index"] for block in items[0]["question_blocks"]] != [3, 4, 5, 6, 7]:
        raise RuntimeError("Frozen Word theme lost interleaved shared material")
    if [block["index"] for block in items[0]["answer_blocks"]] != [8, 9]:
        raise RuntimeError("Frozen Word theme answer separation differs")
    if [block["index"] for block in items[1]["context_blocks"]] != [2]:
        raise RuntimeError("Frozen Word theme lost paper context")
    if preview != original:
        raise RuntimeError("Frozen Word index changed the source preview")

    split_asset = {"asset_id": "synthetic-second-question", "block_index": 4}
    split_preview = make_preview(
        [
            "【例1】选择____。",
            "【答案】A",
            "【说明】（多选）【变式训练3】选择下列合成选项____。",
            "【待查看原文：图片或图形】",
            "A．甲 B．乙",
            "【答案】B",
        ],
        [split_asset],
    )
    split_original = deepcopy(split_preview)
    split_items = index(split_preview)
    if len(split_items) != 2 or [
        (
            item["block_start"],
            item["question_end"],
            item["answer_start"],
            item["block_end"],
        )
        for item in split_items
    ] != [(1, 1, 2, 2), (3, 5, 6, 6)]:
        raise RuntimeError("Frozen Word editorial prefix swallowed a separate question")
    if (
        not all(item["export_ready"] for item in split_items)
        or split_items[0]["key"] == split_items[1]["key"]
        or split_items[0]["answer_blocks"][0]["assets"]
        or split_items[1]["question_blocks"][1]["assets"] != [split_asset]
        or split_preview != split_original
    ):
        raise RuntimeError("Frozen Word split changed source text or image ownership")
    quoted = index(
        make_preview(
            [
                "【例1】选择____。",
                "【答案】【说明】（多选）【变式训练3】仅在解析中引用。",
            ]
        )
    )
    if len(quoted) != 1 or quoted[0]["answer_start"] != 2:
        raise RuntimeError(
            "Frozen Word index mistook an answer quotation for a question"
        )

    cases = {
        "unmarked_answer": [
            "1．合成条件下哪项正确____。",
            "A．甲 B．乙 C．丙",
            "(3)C",
            "(3)这是合成原文已有的说明文字，用来解释所选选项的依据。",
        ],
        "nonstandard_label": [
            "【变式训练3·变题型合成材料如下。",
            "(1)填写____。",
            "【答案】合成结果",
        ],
        "self_contained_reference": [
            "【例1】合成材料观点为两个条件共同成立。根据上述观点，选择____。",
            "【答案】合成结果",
        ],
    }
    for issue, case in cases.items():
        source = make_preview(
            case, [{"asset_id": "synthetic-review-image", "block_index": 1}]
        )
        source["blocks"][0]["warnings"] = ["合成原图仍需核对。"]
        source_original = deepcopy(source)
        candidates = index(source)
        if len(candidates) != 1 or candidates[0]["export_ready"]:
            raise RuntimeError(
                "Frozen Word review hold automatically cleared: " + issue
            )
        candidate = candidates[0]
        candidate_original = deepcopy(candidate)
        bounds = {
            key: candidate[key]
            for key in ("block_start", "question_end", "answer_start", "block_end")
        }
        unconfirmed = apply_range(source, candidate, **bounds)
        if unconfirmed["export_ready"] or "boundary_review" in unconfirmed:
            raise RuntimeError(
                "Frozen Word range alone cleared explicit review: " + issue
            )
        reviewed = apply_range(source, candidate, **bounds, reviewed_issues=[issue])
        if (
            not reviewed["export_ready"]
            or reviewed["key"] != candidate["key"]
            or reviewed["revision"] == candidate["revision"]
            or reviewed.get("boundary_review")
            != {
                "revision": WORD_BOUNDARY_REVIEW_REVISION,
                "issues": [issue],
                "scope": "selected_source_ranges_only",
            }
            or "合成原图仍需核对。" not in reviewed["warnings"]
            or any(
                reviewed[role] != candidate[role]
                for role in ("question_blocks", "answer_blocks", "context_blocks")
            )
            or source != source_original
            or candidate != candidate_original
        ):
            raise RuntimeError(
                "Frozen Word explicit review changed source or failed: " + issue
            )
        wrong_issue = next(value for value in cases if value != issue)
        for changed_source, issues in (
            (source, [wrong_issue]),
            ({**source, "revision": "stale-source-preview"}, [issue]),
        ):
            try:
                apply_range(changed_source, candidate, **bounds, reviewed_issues=issues)
            except ValueError:
                pass
            else:
                raise RuntimeError(
                    "Frozen Word review accepted wrong issue or stale source"
                )

    leakage = make_preview(
        [
            "【例1】合成材料条件在此。根据上述观点，选择____。【答案】A",
        ]
    )
    leaking_item = index(leakage)[0]
    still_held = apply_range(
        leakage,
        leaking_item,
        block_start=1,
        question_end=1,
        answer_start=None,
        block_end=1,
        reviewed_issues=["self_contained_reference"],
    )
    if still_held["export_ready"]:
        raise RuntimeError("Frozen Word narrow review cleared answer leakage")
    return {
        "synthetic_themes": 2,
        "shared_material_preserved": True,
        "answers_separated": True,
        "source_unchanged": True,
        "editorial_prefix_separate_questions": 2,
        "editorial_prefix_image_ownership_preserved": True,
        "answer_quotation_not_split": True,
        "explicit_review_cases": len(cases),
        "unconfirmed_ranges_still_held": len(cases),
        "wrong_issue_and_stale_source_cases_rejected": len(cases) * 2,
        "explicit_review_cannot_clear_answer_leakage": True,
        "boundary_review_revision": WORD_BOUNDARY_REVIEW_REVISION,
        "application_or_provider_loaded": False,
    }


def verify_frozen_word_range_preview_ui(pyz) -> dict:
    """Inspect UI constants and preview-state routes without loading Qt or UI."""
    code = pyz.extract(
        "integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog"
    )
    if set(WORD_RANGE_PREVIEW_UI_CONSTANTS) - strings(code):
        raise RuntimeError("Frozen Word range preview UI constants missing")
    dialog = _frozen_function(code, "WordQuestionRangeDialog")
    confirm = _frozen_function(dialog, "_confirm")
    invalidate = _frozen_function(dialog, "_invalidate_range_preview")
    if (
        not {
            "_preview_key",
            "_range_key",
            "_validated_ranges",
            "_preview_ranges",
            "isChecked",
        }.issubset(confirm.co_names)
        or "reviewed_issues" not in strings(confirm)
        or not {"_preview_key", "_preview_ranges", "setChecked", "setEnabled"}.issubset(
            invalidate.co_names
        )
    ):
        raise RuntimeError("Frozen Word range preview state route missing")
    return {
        "constants_checked": len(WORD_RANGE_PREVIEW_UI_CONSTANTS),
        "preview_state_routes_static_checked": True,
        "ui_instantiated": False,
        "ui_interaction_checked": False,
    }


def verify_frozen_import_binding(pyz) -> dict:
    """Run only the final-loaded-input validator, never import or persistence."""
    code = pyz.extract("integrations.deeptutor_shchem_v1.desktop_import_preview")
    validator_code = next(
        item
        for item in code.co_consts
        if isinstance(item, types.CodeType) and item.co_name == "validate_loaded_inputs"
    )

    class ExpectedBindingError(ValueError):
        pass

    validator = types.FunctionType(
        validator_code, {"ImportPreviewError": ExpectedBindingError}
    )
    fields = {
        "role": "question",
        "order_index": 1,
        "filename": "synthetic.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "source_sha256": hashlib.sha256(b"synthetic bytes").hexdigest(),
        "size_bytes": 15,
        "group_id": None,
    }
    source = types.SimpleNamespace(**fields, content=b"synthetic bytes")
    fields["size_bytes"] = len(source.content)
    validator([source], [fields])
    rejected = 0
    for field, changed in (
        ("source_sha256", "b" * 64),
        ("role", "answer"),
        ("order_index", 2),
        ("size_bytes", 16),
        ("filename", "replaced.docx"),
    ):
        altered = {**fields, field: changed}
        try:
            validator([source], [altered])
        except ExpectedBindingError:
            rejected += 1
        else:
            raise RuntimeError(f"Frozen import binding accepted changed {field}")
    return {
        "unchanged_loaded_input_accepted": True,
        "changed_input_cases_rejected": rejected,
        "persistence_or_provider_run": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--workspace", type=Path, default=Path(__file__).resolve().parents[4]
    )
    args = parser.parse_args()
    if args.zip.exists() or args.report.exists():
        raise RuntimeError("ZIP/report exists; use a new output path")
    package = args.package.resolve(strict=True)
    exe = package / "沪上化学智研台/沪上化学智研台.exe"
    archive = CArchiveReader(str(exe))
    pyz_name = next(name for name in archive.toc if name.endswith(".pyz"))
    pyz = archive.open_embedded_archive(pyz_name)
    prefix = "integrations.deeptutor_shchem_v1."
    expected = {
        "desktop_version": [args.version],
        "desktop_preparation_provider": [
            PROMPT_REVISION,
            "20260909-deepseek-v4-output-budget-v12",
            "provider_response_empty",
        ],
        "desktop_preparation_sources": [
            "【Word与教材备课参考：教师选定的本地资料快照】",
            "教师教材摘录必须标记为已确认。",
            "（本项手工摘录结束）",
        ],
        "desktop_workbench.preparation_sources_dialog": ["确认导入参考"],
        "desktop_workbench.textbook_source_dialog": ["查看教材原页 · 本地核对"],
        "desktop_workbench.textbook_excerpt_widget": ["采用并返回选材"],
        "desktop_workbench.workflow_pages": [
            "从 Word 与教材知识点导入参考…",
            "检查课堂结构与笔记",
            "检查返回内容／本地修复表格…",
            "授课结构与学生笔记（可修改）",
            "按当前课题与课型填入建议结构",
        ],
        "desktop_workbench.preparation_design_widget": [],
        "desktop_preparation_pedagogy": [
            "20260912-electron-representation-reference-v5"
        ],
        "desktop_preparation_classroom_layout": ["classroom-v2"],
        "desktop_workbench.library_detail": [
            "将这张原图用于备课…",
            "LibraryShowAnswerImage",
        ],
        "desktop_workbench.library_page": ["选为备课参考…"],
        "desktop_library_preparation": [
            "【题库备课参考：当前阅读快照，不是原题全文或已核定答案】"
        ],
        "desktop_workbench.library_preparation_dialog": ["将题库选题带入备课"],
        "desktop_workbench.main_window": [],
        "desktop_workbench.student_page": ["按本次学情推荐练习"],
        "desktop_workbench.student_practice_panel": [],
        "desktop_student_practice": ["student_practice_preview_required"],
        "student_recommendation_projection": ["analysis_not_ready"],
        "student_recommendation_workbench": [],
        "desktop_word_table_preview": [],
        "desktop_workbench.word_table_layout": [],
        "desktop_facade": ["题库图片无法保存到本地备课素材库。"],
        "desktop_library": ["知识点候选（主辅待核对）", "reference_answer_images"],
        "desktop_library_session": ["visual_scan_identity_index"],
        "question_search_workbench": ["complete_themes_only", "pending_parentage"],
        "theme_workbench": ["partial_source_candidate"],
        "master_direct_visual_scan": [],
        "fudan2026_april_theme5_zns_direct_visual_scan": [
            "FORMAL-PENDING-V2-FUDAN-2026-APRIL-S5-ZNS-A",
        ],
        "songjiang2025_theme2_direct_visual_scan": [],
        "shanghai_high_east2025_theme45_direct_visual_scan": [],
        "level_exam2025_theme3_fosinopril_direct_visual_scan": [
            "FORMAL-PENDING-V2-LEVEL-EXAM-2025-S3-FOSINOPRIL-DUAL-SOURCE-A",
            "source_A",
            "source_page_backed_candidate_dependency",
        ],
        "fosinopril_source_presentation": [],
        "archived_wechat_crop_revision": ["archived-wechat-source-recrop-20260910-r3"],
        "reference_answer_images": [
            "sheast-source-answer-images-20260910-r1",
            "inline_required",
            "preview_only",
        ],
        "paper_export_workbench": [
            "used_by_atomic_ids",
            "paper_export_answer_image_loader_missing",
        ],
        "paper_format_presets": ["reference_answer_invalid", "content_blocks"],
        "paper_export_renderer": [
            "used_by_atomic_ids",
            "shared_material_membership_invalid",
        ],
        "desktop_preparation_review": ["teacher_review_required"],
        "desktop_preparation_source_compare": [],
        "desktop_preparation_revision": ["内容没有变化，无需另存修订版。"],
        "desktop_preparation_structure": [
            "teacher_structure_revision",
            "link_slide_activity",
            "split_image",
            "move_slides",
            "insert_page",
            "align_timing",
            "append_worksheet",
            "读图后，结合下一页文字继续讨论。",
        ],
        "desktop_workbench.preparation_structure_widget": [
            "确认本页归属",
            "图文拆成相邻两页",
            "添加笔记留白",
            "从选中页的知识表建立空白笔记表",
            "按活动预算重新分配PPT分钟数",
        ],
        "desktop_preparation_sequence": ["student_text", "total_minutes"],
        "desktop_preparation_insert": [
            "来源说明（本地修订者提供，未自动核验）：",
            "新增页目前支持正文或知识比较表，不自动生成示意图。",
        ],
        "desktop_workbench.preparation_insert_page_dialog": [
            "补充知识或例题页 · 本地预览",
            "加入本次修订预览",
            "正文＋知识比较表",
        ],
        "desktop_workbench.preparation_sequence_widget": [
            "移动到选定位置",
            "编辑本页文字",
            "目标与学习单",
            "补充知识或例题页",
        ],
        "desktop_preparation_recovery": [
            "修订应包含页码和完整比较表；拆页时还需指定断行位置与第一页分钟数。",
            "split_after_row",
            "first_page_minutes",
        ],
        "desktop_workbench.preparation_recovery_dialog": [
            "本地另存并导出",
            "在完整返回稿中查找",
            "比较表修改前后核对",
            "拆为两页（保留整张表的全部行）",
        ],
        "desktop_workbench.preparation_revision_dialog": [
            "另存修订版并导出",
            "授课顺序",
            "图文课时与学习单",
        ],
        "desktop_preparation": [
            "教案的多活动分组、页面归属或已有分钟数未能对应，不能唯一核对活动时间。"
        ],
        "desktop_paper_preparation": [],
        "word_native_math": [],
        "word_native_text": ["20260909-editable-chemistry-v2"],
        "desktop_word_question_index": [
            "20260910-word-answer-boundary-index-v4",
            WORD_BOUNDARY_REVIEW_REVISION,
        ],
        "desktop_word_metafile_preview": [],
        "desktop_word_source_reference": ["reference_issues", "image_references"],
        "desktop_word_question_recommendations": ["exact_label_match"],
        "desktop_lecture_library": ["ai_distilled_pending_teacher_review"],
        "desktop_lecture_study": ["source_preview_revision", "lecture_block_indices"],
        "desktop_workbench.lecture_library_dialog": ["查找原教案"],
        "desktop_import_preview": ["import_preview_changed"],
        "desktop_word_preview_cache": ["20260910-native-word-preview-cache-v1"],
        "desktop_word_question_attributes": ["word-attributes-20260912-v2"],
        "desktop_word_semantic_tags": [
            "word-semantic-tags-20260913-v1",
            "model_image_observation",
        ],
        "desktop_workbench.word_semantic_tags_dialog": [
            "AI补全题目标签 · 先预览再采用",
            "确认发送并分析",
            "保存勾选的标签建议",
        ],
        "desktop_word_question_filters": [],
        "desktop_workbench.word_question_filter_panel": [],
        "desktop_source_quality": [
            "known_source_issue_pending_revision",
            "source_errata_pending_relocation",
        ],
        "desktop_workbench.import_preview_dialog": [
            "导入前预览与文件选择",
            "确认导入所选 0 份文件",
        ],
        "desktop_word_questions": [
            "native-word-question-selection-v1",
            "当前选定范围尚未识别到答案；请先核对原教案及题答边界，不能据此判断原文没有答案。",
        ],
        "desktop_word_question_export": [
            "20260909-native-selected-blocks-v1",
            "当前选定范围未识别到本题答案，请核对原教案与题答边界；这不表示原文没有答案。",
        ],
        "desktop_mixed_paper_export": [
            "每个原卷条目必须保留一个完整主题及公共材料。",
        ],
        "desktop_mixed_paper_service": [
            "shchem.desktop-mixed-paper-request.v1",
            "not_generated",
        ],
        "desktop_workbench.word_question_dialog": [
            "Word 逐题浏览与选题",
            "AI补全已选题标签…",
            "确认带入备课",
            *WORD_RANGE_PREVIEW_UI_CONSTANTS,
        ],
        "desktop_workbench.word_question_attributes_dialog": [
            "修改教学标签",
            "确认保存修改",
        ],
        "desktop_workbench.preparation_images_widget": ["PreparationImagesWidget"],
        "word_handout_import": ["1.2.1"],
        "desktop_workbench.import_word_dialog": [
            "完整教案原文与图片",
            "用 Word 打开完整原文件",
            "确认追加到备课",
            "通读教案",
            "选段与备课",
        ],
        "desktop_workbench.word_lesson_reader": [
            "WordLessonReader",
            "完整教案原文阅读区",
        ],
        "desktop_paper_preparation_sources": [],
        "desktop_workbench.paper_preparation_dialog": ["将当前组卷带入备课"],
        "desktop_workbench.assembly_page": ["将当前组卷带入备课…", "题面显示分数"],
        "intake_imports": ["20260910-source-edge-recheck-v3"],
        "desktop_workbench.preparation_review_dialog": [
            "资料与课件对照",
            "在原备课资料与学生可见正文中对照检索",
        ],
        "visual_provider_runtime": ["provider_response_empty"],
        "desktop_preparation_images": [
            "Save exact locally verified bytes without an intermediate export file."
        ],
        "desktop_preparation_image_input": [
            "local_only",
            "vision",
            "preparation_image_mode_invalid",
            "vision_capability_unconfirmed",
            "image_egress_not_allowed",
        ],
        "desktop_preparation_renderer": ["w:cantSplit"],
        "desktop_preparation_worksheet": [],
        "desktop_preparation_drafts": [],
        "docx": [],
    }
    checked, source_matches = [], []
    for module, constants in expected.items():
        full_name = module if module == "docx" else prefix + module
        if full_name not in pyz.toc:
            raise RuntimeError(f"Missing frozen module: {full_name}")
        actual = strings(pyz.extract(full_name))
        missing = set(constants) - actual
        if missing:
            raise RuntimeError(f"Frozen constants differ: {full_name}: {missing}")
        if module != "docx":
            source_matches.append(
                verify_source_matches_frozen(pyz, full_name, args.workspace)
            )
        checked.append(full_name)
    forbidden = [prefix + suffix for suffix in ("service", "http_app", "launcher")]
    forbidden += [name for name in pyz.toc if name.startswith("PySide6.QtWebEngine")]
    if any(name in pyz.toc for name in forbidden):
        raise RuntimeError("Legacy web/runtime unexpectedly included")
    for path in (package / "沪上化学智研台/_internal").iterdir():
        if path.name.lower() == "icuuc.dll" or path.name.lower().startswith("icudt"):
            raise RuntimeError("External ICU DLL unexpectedly included")

    qt_pdf_files = [
        "PySide6/QtPdf.pyd",
        "PySide6/QtPdfWidgets.pyd",
        "PySide6/Qt6Pdf.dll",
        "PySide6/Qt6PdfWidgets.dll",
    ]
    for relative in qt_pdf_files:
        if not (package / "沪上化学智研台/_internal" / relative).is_file():
            raise RuntimeError(f"Missing native PDF runtime: {relative}")

    behavior = verify_frozen_behavior(pyz)
    note_prompt = verify_frozen_note_prompt(pyz)
    image_input = verify_frozen_preparation_image_input(pyz)
    classroom_layout = verify_frozen_classroom_layout(pyz)
    image_capacity = verify_frozen_image_limit(pyz)
    catalog_session = verify_catalog_session(pyz.extract(prefix + "desktop_facade"))
    word_theme_index = verify_frozen_word_theme_index(pyz)
    word_range_preview = verify_frozen_word_range_preview_ui(pyz)
    import_binding = verify_frozen_import_binding(pyz)

    original = tree(package)
    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "x", zipfile.ZIP_DEFLATED) as output:
        for relative in original:
            output.write(package / relative, relative)
    fresh = Path(tempfile.mkdtemp(prefix=f"shchem-desktop-{args.version}-extracted-"))
    with zipfile.ZipFile(args.zip) as output:
        for member in output.infolist():
            (fresh / member.filename).resolve().relative_to(fresh.resolve())
        output.extractall(fresh)
    extracted = tree(fresh)
    if original != extracted:
        raise RuntimeError("Fresh extraction differs from package")
    report = {
        "version": args.version,
        "package": str(package),
        "zip": str(args.zip.resolve()),
        "zip_bytes": args.zip.stat().st_size,
        "zip_sha256": digest(args.zip),
        "exe_sha256": digest(exe),
        "frozen_modules_checked": checked,
        "source_module_matches": source_matches,
        "native_pdf_runtime_files": qt_pdf_files,
        "frozen_behavior": behavior,
        "frozen_note_prompt_checked": note_prompt,
        "frozen_note_prompt_mode_cases": 4,
        "frozen_preparation_image_input": image_input,
        "frozen_classroom_adaptive_layout_checked": classroom_layout,
        "frozen_twelve_image_capacity_checked": image_capacity,
        "frozen_catalog_session_checked": catalog_session,
        "frozen_word_theme_index": word_theme_index,
        "frozen_word_range_preview_static": word_range_preview,
        "frozen_import_loaded_input_binding": import_binding,
        "legacy_web_and_external_icu_absent": True,
        "fresh_extraction": str(fresh),
        "fresh_extraction_file_count": len(extracted),
        "fresh_extraction_matches": True,
        "package_files": original,
        "startup_checked": False,
        "frozen_ui_interaction_checked": False,
        "real_model_called": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.zip.with_suffix(args.zip.suffix + ".sha256").write_text(
        report["zip_sha256"] + "  " + args.zip.name + "\n", encoding="ascii"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "package_files"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
