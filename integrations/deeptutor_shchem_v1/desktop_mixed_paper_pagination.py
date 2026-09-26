"""Freeze real student/teacher DOCX -> PDF -> PNG pagination for local review.

Only prepare_pages invokes a renderer. Readers verify the frozen manifest and
all artifacts, never rerender or substitute a content-only preview. Source and
settings identity belong to the caller, which should bind manifest_sha256.
Cancellation is cooperative before/after each conversion; this module cannot
interrupt an already running Word/LibreOffice conversion.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re

from PIL import Image
from pypdf import PdfReader

from .reader_cancellation import ReadCancelled, check_read_cancelled

SCHEMA_VERSION = "shchem.mixed-paper-pagination.v1"
MANIFEST_FILENAME = "manifest.json"
FAILURE_FILENAME = "pagination-failure.json"
MAX_PAGES = 500
_AUDIENCES = ("student", "teacher")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PAGE = re.compile(r"page-([0-9]+)\.png\Z")
_GATES = {"human_reviewed": False, "teacher_confirmed": False,
          "publication_allowed": False, "central_question_bank_write": False}
_FILE_KEYS = {"path", "sha256", "size_bytes"}
_PAGE_KEYS = _FILE_KEYS | {"page_number", "width_px", "height_px", "pdf_width_pt", "pdf_height_pt"}


class MixedPaperPaginationError(RuntimeError):
    def __init__(self, code, message_zh):
        self.code, self.message_zh = code, message_zh
        super().__init__(message_zh)


def _require(condition, code, message):
    if not condition:
        raise MixedPaperPaginationError(code, message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _manifest_hash(value):
    return _sha(_canonical({key: item for key, item in value.items() if key != "manifest_sha256"}))


def _path(value):
    path = Path(value).absolute()
    for part in (path, *path.parents):
        _require(not part.is_symlink() and not getattr(part, "is_junction", lambda: False)(),
                 "pagination_path_invalid", "分页文件或目录不能使用符号链接或目录联接。")
    return path.resolve()


def _inside(root, relative):
    _require(isinstance(relative, str) and relative and "\\" not in relative and ":" not in relative,
             "pagination_path_invalid", "分页文件定位不正确。")
    pure = PurePosixPath(relative)
    _require(not pure.is_absolute() and all(part not in {"", ".", ".."} for part in relative.split("/")),
             "pagination_path_invalid", "分页文件不能超出本次冻结目录。")
    path = _path(root / relative)
    _require(path.is_relative_to(root) and path.is_file(), "pagination_file_missing", "冻结分页文件缺失或位置发生变化。")
    return path


def _docx_valid(path):
    from .intake_imports import _validate_docx_container

    try:
        _validate_docx_container(path)
    except Exception as exc:
        raise MixedPaperPaginationError(getattr(exc, "code", "pagination_docx_invalid"),
                                       "待分页 DOCX 不完整或含不允许的宏、外部关系。") from None


def _pdf_geometry(raw):
    try:
        _require(raw.startswith(b"%PDF-"), "pagination_pdf_invalid", "渲染器未生成真实 PDF。")
        reader = PdfReader(io.BytesIO(raw), strict=True)
        _require(not reader.is_encrypted, "pagination_pdf_invalid", "不能使用加密 PDF 作为分页成品。")
        result = []
        for page in reader.pages:
            # The reused Poppler/canonical rasterizer renders the MediaBox
            # unless explicitly told to crop; do not invent crop-box clipping.
            box = page.mediabox
            width, height = float(box.width), float(box.height)
            rotation = int(page.get("/Rotate", 0)) % 360
            unit = float(page.get("/UserUnit", 1))
            _require(rotation in {0, 90, 180, 270} and math.isfinite(unit) and unit > 0,
                     "pagination_pdf_invalid", "PDF 页面的旋转或尺寸不正确。")
            width, height = width * unit, height * unit
            if rotation in {90, 270}:
                width, height = height, width
            _require(all(math.isfinite(value) and 0 < value <= 14400 for value in (width, height)),
                     "pagination_pdf_invalid", "PDF 页面尺寸不正确。")
            result.append((width, height))
        _require(1 <= len(result) <= MAX_PAGES, "pagination_page_count_invalid", "PDF 页数为空或超出分页限制。")
        return result
    except MixedPaperPaginationError:
        raise
    except Exception:
        raise MixedPaperPaginationError("pagination_pdf_invalid", "PDF 解析失败，不能用占位 PDF 代替真实成品。") from None


def _png_size(raw):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            _require(image.format == "PNG", "pagination_image_invalid", "分页图片必须是真实 PNG。")
            image.load()
            width, height = image.size
            _require(0 < width <= 20000 and 0 < height <= 20000 and width * height <= 100_000_000,
                     "pagination_image_invalid", "分页图片尺寸超出安全范围。")
            return width, height
    except MixedPaperPaginationError:
        raise
    except Exception:
        raise MixedPaperPaginationError("pagination_image_invalid", "分页图片损坏，无法确认实际页面。") from None


def _renderer_metadata(value):
    _require(isinstance(value, Mapping), "pagination_renderer_invalid", "分页工具没有返回可核对的渲染记录。")
    dpi = value.get("dpi")
    _require(type(dpi) is int and 150 <= dpi <= 2400, "pagination_renderer_invalid", "分页工具的实际清晰度记录不正确。")
    result = {"dpi": dpi}
    for name in ("tool", "invocation_mode", "conversion_backend"):
        text = value.get(name)
        if text is not None:
            _require(isinstance(text, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", text),
                     "pagination_renderer_invalid", "分页工具标识不正确。")
            result[name] = text
    for name in ("script_sha256", "executable_sha256", "stderr_sha256"):
        text = value.get(name)
        if text is not None:
            _require(isinstance(text, str) and _SHA.fullmatch(text), "pagination_renderer_invalid", "分页工具校验值不正确。")
            result[name] = text
    return result


def _record(path, root):
    path = _path(path)
    _require(path.is_relative_to(root) and path.is_file(), "pagination_path_invalid", "渲染产物不在本次冻结目录内。")
    raw = path.read_bytes()
    _require(bool(raw), "pagination_file_missing", "渲染产物为空。")
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(raw), "size_bytes": len(raw)}


def _record_bytes(root, record, keys):
    _require(isinstance(record, Mapping) and set(record) == keys, "pagination_manifest_invalid", "分页文件记录字段不完整。")
    _require(isinstance(record["sha256"], str) and _SHA.fullmatch(record["sha256"])
             and type(record["size_bytes"]) is int and record["size_bytes"] > 0,
             "pagination_manifest_invalid", "分页文件校验记录不正确。")
    path = _inside(root, record["path"])
    raw = path.read_bytes()
    _require(len(raw) == record["size_bytes"] and _sha(raw) == record["sha256"],
             "pagination_file_changed", "冻结 DOCX、PDF 或分页图片已经变化，请重新准备并确认。")
    return raw


def _page_geometry_matches(size, geometry, dpi):
    return all(abs(actual - points * dpi / 72) <= 2 for actual, points in zip(size, geometry, strict=True))


def _verify(output_root, manifest, expected_manifest_sha256=None):
    root = _path(output_root)
    _require(root.is_dir(), "pagination_file_missing", "冻结分页目录不存在。")
    _require(not (root / FAILURE_FILENAME).exists(), "pagination_failed_output",
             "该分页目录记录过失败或取消；请保留诊断并重新准备，不能复用为完成成品。")
    _require(isinstance(manifest, Mapping) and set(manifest) == {
        "schema_version", "status", "manifest_sha256", "gates", "versions"},
        "pagination_manifest_invalid", "冻结分页记录字段不正确。")
    value = deepcopy(dict(manifest))
    _require(value["schema_version"] == SCHEMA_VERSION and value["status"] == "rendered_pending_review"
             and value["gates"] == _GATES and all(flag is False for flag in value["gates"].values()),
             "pagination_manifest_invalid", "冻结分页版本或人工审核边界不正确。")
    _require(isinstance(value["manifest_sha256"], str) and _SHA.fullmatch(value["manifest_sha256"])
             and _manifest_hash(value) == value["manifest_sha256"],
             "pagination_manifest_changed", "冻结分页记录的校验值已变化。")
    if expected_manifest_sha256 is not None:
        _require(expected_manifest_sha256 == value["manifest_sha256"], "pagination_manifest_changed", "本次确认绑定的分页记录已变化。")
    _require(isinstance(value["versions"], Mapping) and set(value["versions"]) == set(_AUDIENCES),
             "pagination_manifest_invalid", "必须同时具备学生版和教师版的完整分页。")
    paths = set()
    for audience in _AUDIENCES:
        check_read_cancelled()
        version = value["versions"][audience]
        _require(isinstance(version, Mapping) and set(version) == {"audience", "docx", "pdf", "page_count", "renderer", "pages"}
                 and version["audience"] == audience, "pagination_manifest_invalid", "分页版本归属不正确。")
        metadata = _renderer_metadata(version["renderer"])
        _require(metadata == version["renderer"], "pagination_manifest_invalid", "分页工具记录含未识别字段。")
        _require(type(version["page_count"]) is int and 1 <= version["page_count"] <= MAX_PAGES
                 and isinstance(version["pages"], list) and len(version["pages"]) == version["page_count"],
                 "pagination_page_count_invalid", "分页图片清单与页数不一致。")
        _record_bytes(root, version["docx"], _FILE_KEYS)
        _docx_valid(_inside(root, version["docx"]["path"]))
        geometry = _pdf_geometry(_record_bytes(root, version["pdf"], _FILE_KEYS))
        _require(len(geometry) == version["page_count"], "pagination_page_count_invalid", "真实 PDF 页数与 PNG 页数不一致。")
        for record, suffix in ((version["docx"], ".docx"), (version["pdf"], ".pdf")):
            _require(record["path"].startswith(audience + "/") and record["path"].endswith(suffix),
                     "pagination_manifest_invalid", "学生版和教师版产物不能串用。")
        for index, record in enumerate(version["pages"], 1):
            check_read_cancelled()
            raw = _record_bytes(root, record, _PAGE_KEYS)
            match = _PAGE.fullmatch(PurePosixPath(record["path"]).name)
            _require(record["path"].startswith(audience + "/render/") and match and int(match[1]) == index
                     and type(record["page_number"]) is int and record["page_number"] == index,
                     "pagination_page_order_invalid", "分页图片必须按连续真实页码排序。")
            size = _png_size(raw)
            _require(type(record["width_px"]) is int and type(record["height_px"]) is int
                     and size == (record["width_px"], record["height_px"])
                     and all(type(record[field]) in {int, float} for field in ("pdf_width_pt", "pdf_height_pt"))
                     and (record["pdf_width_pt"], record["pdf_height_pt"]) == geometry[index - 1]
                     and _page_geometry_matches(size, geometry[index - 1], metadata["dpi"]),
                     "pagination_geometry_invalid", "PNG 尺寸与冻结记录或真实 PDF 页面不一致。")
        for record in [version["docx"], version["pdf"], *version["pages"]]:
            _require(record["path"] not in paths, "pagination_manifest_invalid", "分页产物路径重复。")
            paths.add(record["path"])
    check_read_cancelled()
    return value


def verify_prepared_pages(output_root, manifest, *, expected_manifest_sha256=None):
    """Read-only, full-bundle verification; bind the expected SHA in the caller."""
    try:
        return _verify(output_root, manifest, expected_manifest_sha256)
    except (MixedPaperPaginationError, ReadCancelled):
        raise
    except Exception:
        raise MixedPaperPaginationError("pagination_manifest_invalid", "冻结分页记录无法完整读取或验证。") from None


def load_prepared_pages(output_root, *, expected_manifest_sha256=None):
    """Load manifest.json and verify every frozen file; never rerender."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        root = _path(output_root)
        raw = _inside(root, MANIFEST_FILENAME).read_bytes()
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")))
    except MixedPaperPaginationError:
        raise
    except Exception:
        raise MixedPaperPaginationError("pagination_manifest_invalid", "冻结分页清单不存在或格式损坏。") from None
    return verify_prepared_pages(root, value, expected_manifest_sha256=expected_manifest_sha256)


def read_frozen_page(output_root, manifest, audience, page_number, *, expected_manifest_sha256=None):
    _require(audience in _AUDIENCES and type(page_number) is int and page_number > 0,
             "pagination_page_invalid", "请选择明确版本及有效页码。")
    value = verify_prepared_pages(output_root, manifest, expected_manifest_sha256=expected_manifest_sha256)
    version = value["versions"][audience]
    _require(page_number <= version["page_count"], "pagination_page_invalid", "该版本没有所选页码。")
    return _record_bytes(_path(output_root), version["pages"][page_number - 1], _PAGE_KEYS)


def read_frozen_artifact(output_root, manifest, audience, artifact_format, *, expected_manifest_sha256=None):
    _require(audience in _AUDIENCES and artifact_format in {"docx", "pdf"},
             "pagination_artifact_invalid", "请选择学生版或教师版的 DOCX/PDF 文件。")
    value = verify_prepared_pages(output_root, manifest, expected_manifest_sha256=expected_manifest_sha256)
    return _record_bytes(_path(output_root), value["versions"][audience][artifact_format], _FILE_KEYS)


def prepare_pages(docx_paths, output_root, *, toolchain=None, renderer=None, max_pages=MAX_PAGES):
    """Render both versions into a NEW directory, or raise with diagnostics.

    An injected renderer has the canonical signature
    renderer(docx_path, output_dir, *, toolchain) -> (ordered_png_paths, pdf_path,
    metadata). Injection never bypasses real PDF parsing/PNG byte verification.
    """
    _require(isinstance(docx_paths, Mapping) and set(docx_paths) == set(_AUDIENCES),
             "pagination_inputs_invalid", "真实分页需要明确的学生版和教师版 DOCX。")
    _require(type(max_pages) is int and 1 <= max_pages <= MAX_PAGES,
             "pagination_page_count_invalid", "分页上限设置不正确。")
    root = _path(output_root)
    _require(not root.exists(), "pagination_output_exists", "分页目录已存在；请保留旧结果并使用新的目录。")
    root.mkdir(parents=True, exist_ok=False)
    stage, audience = "input_validation", None
    try:
        check_read_cancelled()
        sources, initial_hashes = {}, {}
        for audience in _AUDIENCES:
            source = _path(docx_paths[audience])
            _require(source.is_file() and source.suffix.lower() == ".docx", "pagination_inputs_invalid", "待分页 DOCX 文件不存在或格式不正确。")
            _docx_valid(source)
            raw = source.read_bytes()
            sources[audience], initial_hashes[audience] = source, _sha(raw)
            target_dir = root / audience
            target_dir.mkdir()
            with (target_dir / (audience + ".docx")).open("xb") as stream:
                stream.write(raw)
        _require(sources["student"] != sources["teacher"], "pagination_inputs_invalid", "学生版与教师版必须由各自明确的 DOCX 文件提供。")
        if renderer is None:
            stage = "toolchain"
            from .paper_export_renderer import _render_with_canonical_docx_tool
            from .paper_export_workbench import _locate_toolchain

            if toolchain is not None:
                toolchain = toolchain.validated()
                renderer = _render_with_canonical_docx_tool
            else:
                candidate = _locate_toolchain()
                if candidate.render_docx_script.is_file() and candidate.pdftoppm_exe.is_file():
                    toolchain = candidate.validated()
                    renderer = _render_with_canonical_docx_tool
                else:
                    from .desktop_local_pagination import render_docx
                    renderer = render_docx
        versions = {}
        for audience in _AUDIENCES:
            stage = "render_" + audience
            check_read_cancelled()
            docx = root / audience / (audience + ".docx")
            docx_record = _record(docx, root)
            _require(docx_record["sha256"] == initial_hashes[audience], "pagination_source_changed", "待分页 DOCX 在准备期间发生变化。")
            render_dir = root / audience / "render"
            pages, pdf, metadata = renderer(docx, render_dir, toolchain=toolchain)
            check_read_cancelled()
            stage = "validate_" + audience
            metadata = _renderer_metadata(metadata)
            pdf_record = _record(pdf, root)
            _require(_path(pdf).is_relative_to(render_dir), "pagination_path_invalid", "PDF 必须来自本版本真实渲染目录。")
            geometry = _pdf_geometry(Path(pdf).read_bytes())
            _require(isinstance(pages, (list, tuple)) and 1 <= len(pages) == len(geometry) <= max_pages,
                     "pagination_page_count_invalid", "真实 PDF 与逐页 PNG 不完整或页数超限。")
            page_records = []
            for index, page in enumerate(pages, 1):
                check_read_cancelled()
                record = _record(page, root)
                match = _PAGE.fullmatch(Path(page).name)
                _require(_path(page).parent == render_dir and match and int(match[1]) == index,
                         "pagination_page_order_invalid", "渲染器返回的页码不连续或来源目录不正确。")
                width, height = _png_size(Path(page).read_bytes())
                record.update(page_number=index, width_px=width, height_px=height,
                              pdf_width_pt=geometry[index - 1][0], pdf_height_pt=geometry[index - 1][1])
                page_records.append(record)
            emitted = {path.resolve() for path in render_dir.glob("page-*.png")}
            _require(emitted == {_path(path) for path in pages}, "pagination_page_count_invalid", "渲染目录含未登记分页图片，不能遗漏。")
            versions[audience] = {"audience": audience, "docx": docx_record, "pdf": pdf_record,
                                  "page_count": len(page_records), "renderer": metadata, "pages": page_records}
            _require(_sha(sources[audience].read_bytes()) == initial_hashes[audience],
                     "pagination_source_changed", "输入 DOCX 在渲染期间发生变化。")
        stage = "freeze"
        for audience in _AUDIENCES:
            _require(_sha(sources[audience].read_bytes()) == initial_hashes[audience],
                     "pagination_source_changed", "输入 DOCX 在分页完成前发生变化。")
        manifest = {"schema_version": SCHEMA_VERSION, "status": "rendered_pending_review",
                    "gates": deepcopy(_GATES), "versions": versions}
        manifest["manifest_sha256"] = _manifest_hash(manifest)
        verified = verify_prepared_pages(root, manifest, expected_manifest_sha256=manifest["manifest_sha256"])
        check_read_cancelled()
        with (root / MANIFEST_FILENAME).open("xb") as stream:
            stream.write(_canonical(verified))
        return verified
    except Exception as exc:
        cancelled = isinstance(exc, ReadCancelled)
        code = getattr(exc, "code", "pagination_render_failed")
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,100}", code) else "pagination_render_failed"
        message = ("分页已在协作取消检查点停止；不会自动终止已运行的 Word 转换。" if cancelled else
                   "缺少本地分页工具，请检查 render_docx.py、Python 和 Poppler；未生成可确认分页。"
                   if code == "renderer_tool_missing" else
                   "真实分页未完成，已保留诊断和中间文件；不能确认或导出为完成成品。")
        diagnostic = {"schema_version": SCHEMA_VERSION, "status": "cancelled" if cancelled else "failed",
                      "stage": stage, "audience": audience, "error_code": code, "error_type": type(exc).__name__,
                      "message_zh": message, "ready": False, "automatic_retry": False, "gates": deepcopy(_GATES)}
        try:
            with (root / FAILURE_FILENAME).open("xb") as stream:
                stream.write(_canonical(diagnostic))
        except OSError:
            pass
        if isinstance(exc, (MixedPaperPaginationError, ReadCancelled)):
            raise
        raise MixedPaperPaginationError(code, message) from None
