from __future__ import annotations

"""Desktop-side orchestration for the formal multi-file intake core.

The production desktop shell is intentionally not changed by this file.  It
provides the typed, in-process seam that a PySide6 ``QThreadPool`` worker can
call later:

* source files have an explicit role, order and grouping (filename heuristics
  are never used for pairing);
* editable DOCX text is inspected separately from page pixels;
* every non-native object keeps its object reference and is sent through the
  visual page lane, never through a text substitute;
* original source bytes and rendered page bytes are content addressed locally;
* the caller injects an already-configured, page-level visual provider; and
* results remain candidate-only and are never written to the central catalogue.

This module depends only on the sibling formal ``intake_batches_v2`` core
and the Python standard library plus Pillow.  It has no Qt, HTTP server, or
document text-layer dependency, so it can be exercised without a GUI.
"""

import hashlib
import inspect
import io
import json
import os
import re
import zipfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

from .word_native_text import (
    NATIVE_WORD_TEXT_REVISION,
    WordNativeTextReader,
    native_body_blocks,
)

try:  # The tests import this module both as a package and by file path.
    from .intake_batches_v2 import (
        CandidateCAS,
        IntakeBatchFile,
        IntakeBatchV2Error,
        MultiFileVisualIntakeV2,
        PixelPageRenderer,
        RenderedPixelPage,
        VisualPixelPage,
        VisualShardRequest,
        candidate_sha256,
        canonical_json_bytes,
        sha256_bytes,
        validate_candidate_v2,
    )
except ImportError:  # pragma: no cover - exercised by an isolated file loader
    from intake_batches_v2 import (  # type: ignore[no-redef]
        CandidateCAS,
        IntakeBatchFile,
        IntakeBatchV2Error,
        MultiFileVisualIntakeV2,
        PixelPageRenderer,
        RenderedPixelPage,
        VisualPixelPage,
        VisualShardRequest,
        candidate_sha256,
        canonical_json_bytes,
        sha256_bytes,
        validate_candidate_v2,
    )


def _strict_json_loads(raw: bytes | str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
    return json.loads(
        text,
        object_pairs_hook=unique,
        parse_constant=reject_constant,
    )


class _CancellationAwareVisualProvider:
    """Narrow adapter: adds cancellation to an injected page-level provider."""

    def __init__(self, provider: Any, should_cancel: Callable[[], bool]) -> None:
        self.provider = provider
        self.should_cancel = should_cancel

    def analyze_shard(self, request: VisualShardRequest) -> Any:
        if self.should_cancel():
            raise DesktopImportBridgeError("visual_cancelled", "视觉导入已取消。", 409)
        method = getattr(self.provider, "analyze_shard", None)
        if callable(method):
            value = method(request)
        elif callable(self.provider):
            value = self.provider(request)
        else:
            raise IntakeBatchV2Error(
                "visual_provider_invalid", "visual shard provider is invalid", 503
            )
        if self.should_cancel():
            raise DesktopImportBridgeError("visual_cancelled", "视觉导入已取消。", 409)
        return value


DESKTOP_IMPORT_BRIDGE_SCHEMA_VERSION = "shchem.desktop-import-bridge.v2"
DIRECT_PAGE_PIXEL_MODE = "direct_original_or_rendered_page_pixels"
SOURCE_ROLES = ("question", "answer", "handout")
IMPORT_STATES = (
    "native_text_complete",
    "hybrid_visual_required",
    "visual_only_required",
)
VISUAL_STATUS = (
    "not_required",
    "awaiting_visual_provider",
    "awaiting_teacher_confirmation",
    "completed",
    "failed",
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_W = "{" + _W_NS + "}"
_M = "{" + _M_NS + "}"
_A = "{" + _A_NS + "}"
_R = "{" + _R_NS + "}"
_REL = "{" + _REL_NS + "}"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})
_ALLOWED_MIMES = _IMAGE_MIMES | {"application/pdf", _DOCX_MIME}
_MIME_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": _DOCX_MIME,
}
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_BASE_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)+/?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_DOCX_ENTRIES = 12_000
_MAX_DOCX_XML_BYTES = 32 * 1024 * 1024
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_PAGE_BYTES = 32 * 1024 * 1024
_MAX_IMAGE_EDGE = 40_000
_MAX_SOURCE_FILES = 100
_MAX_BATCH_BYTES = 512 * 1024 * 1024
_MAX_VISUAL_PAGES = 500
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_VISUAL_TIMEOUT_SECONDS = 900.0
_ALLOWED_CAPABILITIES = frozenset({"text", "vision", "structured_output"})
_ALLOWED_DATA_CLASSES = frozenset(
    {
        "synthetic_only",
        "question_text_redacted",
        "question_image_redacted",
        "source_page_image",
        "student_answer_image",
        "deidentified_student_text",
    }
)
_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "proxy_authorization",
        "password",
        "secret",
        "client_secret",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
    }
)


class DesktopImportBridgeError(ValueError):
    """Sanitized, stable error for a desktop worker or test harness."""

    def __init__(self, code: str, message_zh: str, status: int = 400) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh
        self.status = status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status!r})"


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _identity_render_recipe(mime_type: str) -> str:
    """Return the stable recipe digest for an unmodified source image page."""

    return sha256_bytes(
        canonical_json_bytes(
            {"recipe": "identity_source_image_bytes_v1", "mime_type": mime_type}
        )
    )


def _safe_text(value: Any, *, field_name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise DesktopImportBridgeError(f"{field_name}_invalid", "字段格式不正确。")
    text = value.strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise DesktopImportBridgeError(f"{field_name}_invalid", "字段格式不正确。")
    return text


def _safe_filename(value: Any) -> str:
    text = _safe_text(value, field_name="filename", maximum=180)
    if any(separator in text for separator in ("/", "\\")) or text in {".", ".."}:
        raise DesktopImportBridgeError("filename_invalid", "文件名不安全。")
    return text


def _assert_no_secret_fields(value: Any) -> None:
    """Reject accidental credential material crossing the candidate boundary."""

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if (
                    isinstance(key, str)
                    and key.casefold().replace("-", "_").replace(" ", "_")
                    in _SECRET_FIELD_NAMES
                ):
                    raise DesktopImportBridgeError(
                        "secret_material_detected",
                        "导入回执不能包含模型凭据。",
                        409,
                    )
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(value)


def _validate_declared_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Require every completed visual result to be a strict candidate v2."""

    try:
        return validate_candidate_v2(value)
    except IntakeBatchV2Error as exc:
        raise DesktopImportBridgeError(
            "visual_candidate_schema_invalid",
            "视觉候选不符合 candidate v2 结构。",
            getattr(exc, "status", 502),
        ) from exc


@dataclass(frozen=True, slots=True, repr=False)
class DesktopSourceFile:
    """One explicitly classified file selected in the desktop dialog."""

    role: str
    order_index: int
    filename: str
    mime_type: str
    content: bytes
    group_id: str = "default"
    source_file_id: str | None = None
    path_hint: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in SOURCE_ROLES:
            raise DesktopImportBridgeError("source_role_invalid", "资料角色不正确。")
        if type(self.order_index) is not int or self.order_index < 1:
            raise DesktopImportBridgeError("source_order_invalid", "资料顺序不正确。")
        _safe_filename(self.filename)
        if not isinstance(self.mime_type, str) or self.mime_type not in _ALLOWED_MIMES:
            raise DesktopImportBridgeError(
                "source_mime_invalid", "该文件格式暂不支持视觉导入。"
            )
        if not isinstance(self.content, bytes) or not self.content:
            raise DesktopImportBridgeError(
                "source_bytes_invalid", "资料内容为空或格式不正确。"
            )
        if len(self.content) > _MAX_SOURCE_BYTES:
            raise DesktopImportBridgeError(
                "source_too_large", "资料文件超过安全大小限制。"
            )
        _safe_text(self.group_id, field_name="group_id", maximum=160)
        if self.source_file_id is not None and (
            not isinstance(self.source_file_id, str)
            or _SAFE_ID.fullmatch(self.source_file_id) is None
        ):
            raise DesktopImportBridgeError("source_file_id_invalid", "资料标识不正确。")

    @property
    def source_sha256(self) -> str:
        return sha256_bytes(self.content)

    @property
    def effective_source_file_id(self) -> str:
        return self.source_file_id or (
            f"SRC-{self.role.upper()}-{self.order_index:03d}-{self.source_sha256[:16]}"
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(role={self.role!r}, order_index={self.order_index!r}, "
            f"filename={self.filename!r}, mime_type={self.mime_type!r}, "
            f"size_bytes={len(self.content)!r}, content_hidden=True)"
        )

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        role: str,
        order_index: int,
        group_id: str = "default",
        source_file_id: str | None = None,
    ) -> DesktopSourceFile:
        source = Path(path)
        suffix = source.suffix.casefold()
        mime_type = _MIME_EXTENSIONS.get(suffix)
        if mime_type is None:
            raise DesktopImportBridgeError(
                "source_format_invalid", "该文件格式暂不支持视觉导入。"
            )
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise DesktopImportBridgeError(
                "source_unreadable", "资料文件无法读取。", 409
            ) from exc
        return cls(
            role=role,
            order_index=order_index,
            filename=source.name,
            mime_type=mime_type,
            content=content,
            group_id=group_id,
            source_file_id=source_file_id,
            path_hint=str(source),
        )

    def as_manifest(self) -> dict[str, Any]:
        """Return a safe descriptor; never expose path or file bytes."""

        return {
            "source_file_id": self.effective_source_file_id,
            "role": self.role,
            "order_index": self.order_index,
            "group_id": self.group_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": len(self.content),
            "source_sha256": self.source_sha256,
            "content_addressed": True,
        }

    def as_batch_file(self) -> IntakeBatchFile:
        return IntakeBatchFile(
            filename=self.filename,
            mime_type=self.mime_type,
            content=self.content,
            source_file_id=self.effective_source_file_id,
        )


DesktopImportFile = DesktopSourceFile


@dataclass(frozen=True, slots=True)
class NativeDocxInspection:
    source_file_id: str
    source_sha256: str
    native_text: str
    native_blocks: tuple[Mapping[str, Any], ...]
    features: tuple[str, ...]
    visual_object_refs: tuple[Mapping[str, Any], ...]
    xml_locators: tuple[str, ...]
    import_state: str
    quick_import_eligible: bool
    page_binding_status: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


def _docx_error(
    code: str, message: str = "DOCX 原生结构无法读取。"
) -> DesktopImportBridgeError:
    return DesktopImportBridgeError(code, message, 409)


def _read_docx_xml(package: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        info = package.getinfo(name)
    except KeyError as exc:
        raise _docx_error("docx_document_missing") from exc
    if not 0 < info.file_size <= _MAX_DOCX_XML_BYTES:
        raise _docx_error("docx_xml_size_invalid")
    try:
        raw = package.read(name)
        # ElementTree does not provide a full hostile-XML policy.  Reject
        # declarations that could expand entities before parsing the bounded
        # OOXML part; DOCX produced by the supported editors does not need
        # them.
        upper = raw.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise _docx_error("docx_xml_entities_unsupported")
        return ElementTree.fromstring(raw)
    except DesktopImportBridgeError:
        raise
    except (ElementTree.ParseError, OSError) as exc:
        raise _docx_error("docx_xml_invalid") from exc


def _validate_docx_names(package: zipfile.ZipFile) -> None:
    infos = package.infolist()
    if not infos or len(infos) > _MAX_DOCX_ENTRIES:
        raise _docx_error("docx_container_invalid")
    total = 0
    names: set[str] = set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = name.split("/")
        if (
            not name
            or name in names
            or name.startswith("/")
            or ".." in parts
            or (parts and ":" in parts[0])
        ):
            raise _docx_error("docx_entry_path_invalid")
        names.add(name)
        total += max(info.file_size, 0)
        if total > _MAX_SOURCE_BYTES:
            raise _docx_error("docx_too_large")


_VISUAL_CONTAINERS = frozenset(
    {
        "drawing",
        "pict",
        "object",
        "oleObject",
        "oMath",
        "oMathPara",
        "txbxContent",
        "AlternateContent",
    }
)
_FEATURE_TAGS = {
    "drawing": "word_drawing_or_shape",
    "pict": "vml_drawing_or_shape",
    "object": "ole_object_reference",
    "oleObject": "ole_object_reference",
    "oMath": "omml_equation",
    "oMathPara": "omml_equation",
    "txbxContent": "text_box_layout_dependency",
    "AlternateContent": "unsupported_embedded_part",
    "vertAlign": "run_vertical_alignment",
    "fldSimple": "field_code_dependency",
    "instrText": "field_code_dependency",
    "commentRangeStart": "note_or_comment_reference",
    "footnoteReference": "note_or_comment_reference",
    "endnoteReference": "note_or_comment_reference",
    "sym": "unsupported_symbol",
}


def _relationship_refs(
    package: zipfile.ZipFile,
) -> tuple[set[str], list[dict[str, Any]], set[str]]:
    """Read relationship metadata without reading any embedded object as text."""

    rel_name = "word/_rels/document.xml.rels"
    try:
        root = _read_docx_xml(package, rel_name)
    except DesktopImportBridgeError as exc:
        if exc.code == "docx_document_missing":
            return set(), [], set()
        raise
    features: set[str] = set()
    refs: list[dict[str, Any]] = []
    relationship_ids: set[str] = set()
    for index, relation in enumerate(root.findall(f"{_REL}Relationship"), 1):
        relation_id = relation.get("Id")
        target = relation.get("Target") or ""
        kind = (relation.get("Type") or "").rstrip("/").rsplit("/", 1)[-1].casefold()
        if not relation_id:
            continue
        relationship_ids.add(relation_id)
        feature: str | None = None
        object_kind: str | None = None
        if kind == "image":
            feature, object_kind = "embedded_image_reference", "image"
        elif kind in {"oleobject", "package"}:
            feature, object_kind = "ole_object_reference", "ole"
        elif kind in {
            "chart",
            "diagramdata",
            "diagramlayout",
            "diagramcolors",
            "diagramquickstyle",
        }:
            feature, object_kind = "chart_or_diagram_reference", "chart_or_diagram"
        if feature is not None:
            features.add(feature)
            refs.append(
                {
                    "object_id": f"VISOBJ-REL-{index:04d}",
                    "kind": object_kind,
                    "relationship_id": relation_id,
                    "target": target,
                    "xml_locator": rel_name,
                }
            )
    return relationship_ids, refs, features


def _package_visual_markers(
    package: zipfile.ZipFile,
    *,
    skip_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Conservatively detect visual parts outside the main document body.

    Headers, footers, notes and embedded package parts can carry equations or
    drawings that are not represented in ``word/document.xml``.  We inspect
    only bounded XML bytes and part names here; no text is extracted from
    these parts.  A marker is intentionally enough to route the DOCX to the
    pixel lane rather than risk a false native-text classification.
    """

    skipped = skip_names or set()
    refs: list[dict[str, Any]] = []
    features: set[str] = set()
    tag_features = {
        b"drawing": "word_drawing_or_shape",
        b"pict": "vml_drawing_or_shape",
        b"object": "ole_object_reference",
        b"oMath": "omml_equation",
        b"oMathPara": "omml_equation",
        b"txbxContent": "text_box_layout_dependency",
        b"AlternateContent": "unsupported_embedded_part",
    }
    for info in package.infolist():
        name = info.filename.replace("\\", "/")
        if name in skipped or not name.casefold().startswith("word/"):
            continue
        lowered = name.casefold()
        part_kind: str | None = None
        if lowered.startswith("word/media/"):
            part_kind = "image"
            features.add("embedded_image_reference")
        elif lowered.startswith("word/embeddings/"):
            part_kind = "ole"
            features.add("ole_object_reference")
        elif lowered.startswith(("word/charts/", "word/diagrams/")):
            part_kind = "chart_or_diagram"
            features.add("chart_or_diagram_reference")
        if part_kind is not None:
            refs.append(
                {
                    "object_id": f"VISOBJ-PART-{len(refs) + 1:04d}",
                    "kind": part_kind,
                    "target": name,
                    "xml_locator": name,
                }
            )
            continue
        if not lowered.endswith(".xml") or "/_rels/" in lowered:
            continue
        if not 0 < info.file_size <= _MAX_DOCX_XML_BYTES:
            features.add("unsupported_embedded_part")
            refs.append(
                {
                    "object_id": f"VISOBJ-PART-{len(refs) + 1:04d}",
                    "kind": "unsupported_embedded_part",
                    "target": name,
                    "xml_locator": name,
                }
            )
            continue
        try:
            raw = package.read(name)
        except OSError:
            features.add("unsupported_embedded_part")
            continue
        lowered_raw = raw.lower()
        for marker, feature in tag_features.items():
            if re.search(
                rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?" + marker + rb"(?:\s|>)", raw
            ):
                features.add(feature)
                refs.append(
                    {
                        "object_id": f"VISOBJ-PART-{len(refs) + 1:04d}",
                        "kind": feature,
                        "target": name,
                        "xml_locator": name,
                    }
                )
        # A few producers use unqualified marker strings in extension XML;
        # keep the routing conservative without treating their bytes as text.
        if b"<omath" in lowered_raw and "omml_equation" not in features:
            features.add("omml_equation")
            refs.append(
                {
                    "object_id": f"VISOBJ-PART-{len(refs) + 1:04d}",
                    "kind": "omml_equation",
                    "target": name,
                    "xml_locator": name,
                }
            )
        ancillary = lowered.startswith(
            (
                "word/header",
                "word/footer",
                "word/footnotes",
                "word/endnotes",
                "word/comments",
            )
        )
        if ancillary and b"<w:t" in lowered_raw:
            # The bridge deliberately does not merge header/footer text into
            # the editable body.  Marking it as an unmapped visual/text part
            # prevents a false ``native_text_complete`` quick import.
            features.add("unmapped_text_part")
            refs.append(
                {
                    "object_id": f"VISOBJ-PART-{len(refs) + 1:04d}",
                    "kind": "unmapped_text_part",
                    "target": name,
                    "xml_locator": name,
                }
            )
    return refs, features


def _collect_block_text(
    node: ElementTree.Element,
    *,
    locator: str,
    features: set[str],
    object_refs: list[dict[str, Any]],
    relationship_ids: set[str],
    visual_context: bool = False,
    block_features: set[str] | None = None,
) -> str:
    """Collect only editable ``w:t`` descendants outside visual containers."""

    parts: list[str] = []
    local = _local_name(node.tag)
    namespace = _namespace(node.tag)
    feature = _FEATURE_TAGS.get(local)
    if feature is not None:
        features.add(feature)
        if block_features is not None:
            block_features.add(feature)
    next_visual = visual_context or local in _VISUAL_CONTAINERS
    if local in _VISUAL_CONTAINERS:
        object_refs.append(
            {
                "object_id": f"VISOBJ-{len(object_refs) + 1:04d}",
                "kind": _FEATURE_TAGS.get(local, "visual_object"),
                "xml_locator": locator,
            }
        )
    for key, value in node.attrib.items():
        if (
            key.rsplit("}", 1)[-1] in {"embed", "id", "link"}
            and isinstance(value, str)
            and value in relationship_ids
        ):
            object_refs.append(
                {
                    "object_id": f"VISOBJ-REL-{len(object_refs) + 1:04d}",
                    "kind": "relationship_bound_object",
                    "relationship_id": value,
                    "xml_locator": locator,
                }
            )
            if block_features is not None:
                block_features.add("relationship_bound_object")
    if node.tag == _W + "t" and not next_visual:
        if node.text:
            parts.append(node.text)
    elif node.tag == _W + "tab" and not next_visual:
        parts.append("\t")
    elif node.tag == _W + "br" and not next_visual:
        parts.append("\n")
    # Non-Word text (m:t, a:t, etc.) is deliberately never appended.
    if namespace != _W_NS and local == "t":
        return "".join(parts)
    for child in list(node):
        parts.append(
            _collect_block_text(
                child,
                locator=locator,
                features=features,
                object_refs=object_refs,
                relationship_ids=relationship_ids,
                visual_context=next_visual,
                block_features=block_features,
            )
        )
    return "".join(parts)


def inspect_native_docx(source: DesktopSourceFile) -> NativeDocxInspection:
    """Inspect editable OOXML only; return a conservative visual classification."""

    if source.mime_type != _DOCX_MIME:
        raise DesktopImportBridgeError(
            "native_format_invalid", "只有 DOCX 可走原生文字检查。"
        )
    try:
        package = zipfile.ZipFile(io.BytesIO(source.content))
    except (OSError, zipfile.BadZipFile) as exc:
        raise _docx_error("docx_invalid") from exc
    with package:
        _validate_docx_names(package)
        root = _read_docx_xml(package, "word/document.xml")
        relationship_ids, relationship_refs, relationship_features = _relationship_refs(
            package
        )
        package_visual_refs, package_visual_features = _package_visual_markers(
            package,
            skip_names={"word/document.xml"},
        )
        styles = _read_docx_xml(package, "word/styles.xml") if "word/styles.xml" in package.namelist() else None
        text_reader = WordNativeTextReader(styles)
    body = root.find(f".//{_W}body")
    if body is None:
        raise _docx_error("docx_body_missing")
    features: set[str] = set(relationship_features) | package_visual_features
    object_refs: list[dict[str, Any]] = [
        *deepcopy(relationship_refs),
        *deepcopy(package_visual_refs),
    ]
    blocks: list[dict[str, Any]] = []
    for index, (node, locator) in enumerate(native_body_blocks(body), 1):
        kind = "paragraph" if node.tag == _W + "p" else "table"
        if node.tag != _W + "tbl":
            kind = "paragraph"
        block_features: set[str] = set()
        block_refs: list[dict[str, Any]] = []
        _collect_block_text(
            node,
            locator=locator,
            features=block_features,
            object_refs=block_refs,
            relationship_ids=relationship_ids,
            block_features=block_features,
        )
        native = text_reader.read(node)
        block_features.difference_update({"omml_equation", "run_vertical_alignment"})
        block_features.update(native.features)
        features.update(block_features)
        object_refs.extend(
            ref for ref in block_refs
            if ref.get("kind") != "omml_equation" or "omml_equation" in block_features
        )
        normalized = native.text
        blocks.append(
            {
                "index": index,
                "block_kind": kind,
                "text": normalized,
                "xml_locator": locator,
                "features": sorted(block_features),
                "native_math_count": native.native_math_count,
            }
        )
    native_blocks = tuple(
        block for block in blocks if block["text"] or block["features"]
    )
    native_text = "\n".join(block["text"] for block in native_blocks if block["text"])
    # A relationship can be present without a directly visible drawing node;
    # keep it as a visual blocker rather than pretending the object is text.
    if object_refs:
        features.update(
            ref.get("kind")
            for ref in object_refs
            if ref.get("kind")
            in {
                "image",
                "ole",
                "chart_or_diagram",
                "word_drawing_or_shape",
                "vml_drawing_or_shape",
                "omml_equation",
            }
        )
    features.discard(None)
    actual_text = re.sub(r"【待查看原文：[^】]*】", "", native_text)
    actual_text = re.sub(r"【表格(?:开始[^】]*|结束)】|〔第\d+行[^〕]*〕", "", actual_text)
    has_editable_text = bool(actual_text.strip())
    if not has_editable_text and features:
        state = "visual_only_required"
    elif features:
        state = "hybrid_visual_required"
    elif not has_editable_text:
        state = "visual_only_required"
    else:
        state = "native_text_complete"
    return NativeDocxInspection(
        source_file_id=source.effective_source_file_id,
        source_sha256=source.source_sha256,
        native_text=native_text,
        native_blocks=native_blocks,
        features=tuple(sorted(str(item) for item in features)),
        visual_object_refs=tuple(object_refs),
        xml_locators=tuple(block["xml_locator"] for block in native_blocks),
        import_state=state,
        quick_import_eligible=state == "native_text_complete" and bool(native_text),
        page_binding_status="pending_rendered_page_evidence",
    )


@dataclass(frozen=True, slots=True)
class DesktopImportRequest:
    sources: tuple[DesktopSourceFile, ...]
    batch_id: str | None = None
    source_type: str = "未分类资料"

    @classmethod
    def from_sources(
        cls,
        *,
        question_files: Sequence[DesktopSourceFile] = (),
        answer_files: Sequence[DesktopSourceFile] = (),
        handout_files: Sequence[DesktopSourceFile] = (),
        batch_id: str | None = None,
        source_type: str = "未分类资料",
    ) -> DesktopImportRequest:
        return cls(
            sources=(*question_files, *answer_files, *handout_files),
            batch_id=batch_id,
            source_type=source_type,
        )


@dataclass(frozen=True, slots=True)
class DesktopImportPlan:
    batch_id: str
    sources: tuple[Mapping[str, Any], ...]
    source_import_states: tuple[Mapping[str, str], ...]
    native_inspections: tuple[NativeDocxInspection, ...]
    native_quick_count: int
    visual_queue_count: int
    visual_required: bool
    visual_source_ids: tuple[str, ...]
    visual_context_source_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "sources": [dict(value) for value in self.sources],
            "source_import_states": [
                dict(value) for value in self.source_import_states
            ],
            "native_inspections": [item.as_dict() for item in self.native_inspections],
            "native_quick_count": self.native_quick_count,
            "visual_queue_count": self.visual_queue_count,
            "visual_required": self.visual_required,
            "visual_source_ids": list(self.visual_source_ids),
            "visual_context_source_ids": list(self.visual_context_source_ids),
        }


@dataclass(frozen=True, slots=True)
class DesktopImportResult:
    batch_id: str
    source_type: str
    plan: DesktopImportPlan
    native_records: tuple[Mapping[str, Any], ...]
    native_import_receipt: Mapping[str, Any] | None
    visual_status: str
    visual_candidate: Mapping[str, Any] | None
    visual_candidate_sha256: str | None
    visual_revision_token: str | None
    visual_queue: tuple[Mapping[str, Any], ...]
    pixel_pages: tuple[Mapping[str, Any], ...]
    blockers: tuple[Mapping[str, Any], ...]
    manifest_path: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DESKTOP_IMPORT_BRIDGE_SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "source_type": self.source_type,
            "plan": self.plan.as_dict(),
            "native_records": [dict(value) for value in self.native_records],
            "native_import_receipt": deepcopy(self.native_import_receipt),
            "visual_status": self.visual_status,
            "visual_candidate": deepcopy(self.visual_candidate),
            "visual_candidate_sha256": self.visual_candidate_sha256,
            "visual_revision_token": self.visual_revision_token,
            "visual_queue": [dict(value) for value in self.visual_queue],
            "pixel_pages": [dict(value) for value in self.pixel_pages],
            "blockers": [dict(value) for value in self.blockers],
            "manifest_path": self.manifest_path,
            "candidate_only": True,
            "central_question_bank_write": False,
        }


class NativeImportPort(Protocol):
    def submit(
        self,
        sources: Sequence[DesktopSourceFile],
        inspections: Sequence[NativeDocxInspection],
    ) -> Mapping[str, Any] | None: ...


class VisualBatchRunner(Protocol):
    def process(
        self,
        *,
        question_files: Sequence[IntakeBatchFile],
        answer_files: Sequence[IntakeBatchFile],
        handout_files: Sequence[IntakeBatchFile],
        batch_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class VisualPageRunner(Protocol):
    """Trusted test/adapter seam that receives page shards only.

    Implementations must not expect ``DesktopSourceFile`` instances.  Each
    request contains only rendered/original raster pages and their immutable
    page manifests.  The return value must be a complete
    ``shchem.intake-batch-candidate.v2`` object; two-field authority stubs are
    rejected and can never produce ``visual_status=completed``.
    """

    def __call__(
        self,
        shards: Sequence[VisualShardRequest],
        *,
        batch_id: str,
    ) -> Mapping[str, Any]: ...


class PixelArchive:
    """Content-addressed source/page store used by the personal desktop state."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.sources_root = self.root / "sources"
        self.pages_root = self.root / "pages"
        self.sources_root.mkdir(parents=True, exist_ok=True)
        self.pages_root.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(self.root, 0o700)
            os.chmod(self.sources_root, 0o700)
            os.chmod(self.pages_root, 0o700)

    @staticmethod
    def _extension(mime_type: str) -> str:
        return {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            _DOCX_MIME: ".docx",
        }[mime_type]

    def _put(self, path: Path, content: bytes) -> None:
        if path.exists():
            try:
                if path.read_bytes() != content:
                    raise DesktopImportBridgeError(
                        "pixel_store_collision", "像素内容寻址冲突。", 409
                    )
            except OSError as exc:
                raise DesktopImportBridgeError(
                    "pixel_store_unreadable", "像素归档无法读取。", 409
                ) from exc
            return
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            with suppress(OSError):
                os.chmod(path, 0o600)
        except FileExistsError:
            # Another worker may have won the race; verify its bytes.
            if path.read_bytes() != content:
                raise DesktopImportBridgeError(
                    "pixel_store_collision", "像素内容寻址冲突。", 409
                ) from None
        except OSError as exc:
            raise DesktopImportBridgeError(
                "pixel_store_write_failed", "像素归档无法保存。", 503
            ) from exc
        finally:
            with suppress(OSError):
                temporary.unlink()

    def put_source(self, source: DesktopSourceFile) -> dict[str, Any]:
        digest = source.source_sha256
        path = self.sources_root / f"{digest}{self._extension(source.mime_type)}"
        self._put(path, source.content)
        return {
            "source_file_id": source.effective_source_file_id,
            "source_sha256": digest,
            "size_bytes": len(source.content),
            "relative_path": path.relative_to(self.root).as_posix(),
            "immutable": True,
        }

    def put_page(
        self,
        *,
        source_file_id: str,
        source_sha256: str | None = None,
        source_role: str,
        source_order: int,
        page_number: int,
        mime_type: str,
        pixels: bytes,
        width: int,
        height: int,
        render_recipe_sha256: str | None = None,
        rendered_from_source: bool = False,
    ) -> dict[str, Any]:
        if (
            not isinstance(source_file_id, str)
            or _SAFE_ID.fullmatch(source_file_id) is None
            or (
                source_sha256 is not None
                and (
                    not isinstance(source_sha256, str)
                    or _SHA256.fullmatch(source_sha256) is None
                )
            )
            or source_role not in SOURCE_ROLES
            or type(source_order) is not int
            or source_order < 1
            or type(page_number) is not int
            or page_number < 1
            or not isinstance(mime_type, str)
            or mime_type not in _IMAGE_MIMES
            or not isinstance(pixels, bytes)
            or not pixels
            or type(width) is not int
            or width < 1
            or type(height) is not int
            or height < 1
            or type(rendered_from_source) is not bool
            or (
                render_recipe_sha256 is not None
                and (
                    not isinstance(render_recipe_sha256, str)
                    or _SHA256.fullmatch(render_recipe_sha256) is None
                )
            )
        ):
            raise DesktopImportBridgeError(
                "pixel_page_invalid", "页面像素归档描述不正确。", 409
            )
        actual_width, actual_height = _image_dimensions(pixels, mime_type)
        if (actual_width, actual_height) != (width, height):
            raise DesktopImportBridgeError(
                "pixel_page_dimensions_invalid", "页面像素尺寸与归档描述不一致。", 409
            )
        digest = sha256_bytes(pixels)
        path = self.pages_root / f"{digest}{self._extension(mime_type)}"
        self._put(path, pixels)
        return {
            "source_file_id": source_file_id,
            "source_sha256": source_sha256,
            "source_role": source_role,
            "source_order": source_order,
            "page_number": page_number,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "size_bytes": len(pixels),
            "page_sha256": digest,
            "render_recipe_sha256": render_recipe_sha256,
            "relative_path": path.relative_to(self.root).as_posix(),
            "pixel_bytes_preserved": True,
            "rendered_from_source": rendered_from_source,
            "immutable": True,
        }


class _ArchivingRenderer:
    def __init__(
        self,
        renderer: PixelPageRenderer,
        archive: PixelArchive,
        *,
        source_orders: Mapping[str, int] | None = None,
        source_shas: Mapping[str, str] | None = None,
    ) -> None:
        self.renderer = renderer
        self.archive = archive
        self.source_orders = dict(source_orders or {})
        self.source_shas = dict(source_shas or {})
        self.pages: list[dict[str, Any]] = []

    def render(
        self, source_file: IntakeBatchFile, *, source_role: str
    ) -> list[RenderedPixelPage]:
        pages = list(self.renderer.render(source_file, source_role=source_role))
        # IntakeBatchFile carries the deterministic ID generated by the core.
        source_id = (
            source_file.source_file_id
            or f"SRC-{sha256_bytes(source_file.content)[:16]}"
        )
        for page_number, page in enumerate(pages, 1):
            width, height = _image_dimensions(page.pixels, page.mime_type)
            self.pages.append(
                self.archive.put_page(
                    source_file_id=source_id,
                    source_sha256=self.source_shas.get(source_id),
                    source_role=source_role,
                    source_order=self.source_orders.get(source_id, 1),
                    page_number=page_number,
                    mime_type=page.mime_type,
                    pixels=page.pixels,
                    width=width,
                    height=height,
                    render_recipe_sha256=page.render_recipe_sha256,
                    rendered_from_source=True,
                )
            )
        return pages


class _VisualRunSkipped(DesktopImportBridgeError):
    """Internal sentinel for a deliberate teacher-confirmation pause."""

    def __init__(self) -> None:
        super().__init__("teacher_confirmation_required", "等待教师确认。", 409)


def _image_dimensions(raw: bytes, mime_type: str) -> tuple[int, int]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > _MAX_PAGE_BYTES
        or not isinstance(mime_type, str)
        or mime_type not in _IMAGE_MIMES
    ):
        raise DesktopImportBridgeError("page_mime_invalid", "页面像素格式不支持。", 409)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            actual = (image.format or "").upper()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise DesktopImportBridgeError(
            "page_pixels_invalid", "页面像素无法读取。", 409
        ) from exc
    expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[
        mime_type
    ]
    if actual != expected:
        raise DesktopImportBridgeError(
            "page_mime_invalid", "页面像素格式与 MIME 不一致。", 409
        )
    if (
        type(width) is not int
        or type(height) is not int
        or not 1 <= width <= _MAX_IMAGE_EDGE
        or not 1 <= height <= _MAX_IMAGE_EDGE
    ):
        raise DesktopImportBridgeError(
            "page_dimensions_invalid", "页面像素尺寸超出安全范围。", 409
        )
    return int(width), int(height)


def _render_visual_pages(
    sources: Sequence[DesktopSourceFile],
    *,
    renderer: PixelPageRenderer | None,
    batch_id: str,
    max_pages_per_shard: int,
    archive: PixelArchive | None,
) -> tuple[tuple[VisualShardRequest, ...], list[dict[str, Any]]]:
    """Materialize a page-only callable input.

    The coordinator historically accepted a generic callable as a convenient
    test seam.  Passing ``DesktopSourceFile`` objects to that callable would
    expose raw PDF/DOCX bytes and make it possible to bypass the direct-pixel
    contract.  This helper gives that seam the same immutable page objects as
    the core provider protocol, so a callable can never receive a document
    source or a text layer.
    """

    if type(max_pages_per_shard) is not int or not 1 <= max_pages_per_shard <= 20:
        raise DesktopImportBridgeError(
            "shard_page_limit_invalid", "视觉分片页数上限不正确。", 409
        )
    pages: list[VisualPixelPage] = []
    archived: list[dict[str, Any]] = []
    for role in SOURCE_ROLES:
        role_sources = sorted(
            (source for source in sources if source.role == role),
            key=lambda source: source.order_index,
        )
        for source in role_sources:
            if source.mime_type in _IMAGE_MIMES:
                width, height = _image_dimensions(source.content, source.mime_type)
                rendered_pages: Sequence[RenderedPixelPage] = (
                    RenderedPixelPage(
                        pixels=source.content,
                        mime_type=source.mime_type,
                        width=width,
                        height=height,
                        render_recipe_sha256=_identity_render_recipe(source.mime_type),
                    ),
                )
            else:
                if renderer is None:
                    raise DesktopImportBridgeError(
                        "page_renderer_required",
                        "PDF/DOCX 视觉导入需要显式页面渲染器。",
                        409,
                    )
                try:
                    rendered_pages = tuple(
                        renderer.render(source.as_batch_file(), source_role=role)
                    )
                except DesktopImportBridgeError:
                    raise
                except Exception as exc:
                    raise DesktopImportBridgeError(
                        "page_render_failed", "资料无法渲染为页面像素。", 409
                    ) from exc
            if not rendered_pages:
                raise DesktopImportBridgeError(
                    "page_render_empty", "资料没有产生可观察的页面像素。", 409
                )
            for page_number, rendered in enumerate(rendered_pages, 1):
                if not isinstance(rendered, RenderedPixelPage):
                    raise DesktopImportBridgeError(
                        "rendered_page_invalid", "渲染器返回的页面无效。", 409
                    )
                width, height = _image_dimensions(rendered.pixels, rendered.mime_type)
                if (width, height) != (rendered.width, rendered.height):
                    raise DesktopImportBridgeError(
                        "rendered_page_dimensions_mismatch",
                        "渲染页面尺寸与像素不一致。",
                        409,
                    )
                if (
                    not isinstance(rendered.render_recipe_sha256, str)
                    or _SHA256.fullmatch(rendered.render_recipe_sha256) is None
                ):
                    raise DesktopImportBridgeError(
                        "rendered_page_invalid", "渲染页面配方摘要无效。", 409
                    )
                visual_page = VisualPixelPage(
                    source_file_id=source.effective_source_file_id,
                    source_role=role,
                    source_order=source.order_index,
                    page_number=page_number,
                    mime_type=rendered.mime_type,
                    width=width,
                    height=height,
                    page_sha256=sha256_bytes(rendered.pixels),
                    render_recipe_sha256=rendered.render_recipe_sha256,
                    pixels=rendered.pixels,
                )
                pages.append(visual_page)
                if archive is not None:
                    archived.append(
                        archive.put_page(
                            source_file_id=source.effective_source_file_id,
                            source_sha256=source.source_sha256,
                            source_role=role,
                            source_order=source.order_index,
                            page_number=page_number,
                            mime_type=rendered.mime_type,
                            pixels=rendered.pixels,
                            width=width,
                            height=height,
                            render_recipe_sha256=rendered.render_recipe_sha256,
                            rendered_from_source=source.mime_type not in _IMAGE_MIMES,
                        )
                    )
    if len(pages) > _MAX_VISUAL_PAGES:
        raise DesktopImportBridgeError(
            "batch_page_limit_exceeded", "批次渲染页面数量超过安全上限。", 409
        )
    shards: list[VisualShardRequest] = []
    shard_index = 0
    for role in SOURCE_ROLES:
        role_pages = [page for page in pages if page.source_role == role]
        for offset in range(0, len(role_pages), max_pages_per_shard):
            shard_index += 1
            shards.append(
                VisualShardRequest(
                    batch_id=batch_id,
                    shard_id=f"{batch_id}:{role}:{shard_index:04d}",
                    shard_index=shard_index,
                    source_role=role,
                    pages=tuple(role_pages[offset : offset + max_pages_per_shard]),
                )
            )
    return tuple(shards), archived


class DesktopImportCoordinatorV2:
    """Plan and execute one desktop batch without a transport/UI boundary."""

    def __init__(
        self,
        *,
        visual_runner: Any | None = None,
        visual_provider: Any | None = None,
        renderer: PixelPageRenderer | None = None,
        native_importer: NativeImportPort
        | Callable[..., Mapping[str, Any] | None]
        | None = None,
        archive_root: str | Path | None = None,
        max_pages_per_shard: int = 2,
    ) -> None:
        visual_provider_supplied = visual_provider is not None
        if visual_runner is not None and visual_provider is not None:
            raise DesktopImportBridgeError(
                "visual_runner_duplicate", "视觉执行器只能指定一种。"
            )
        if visual_provider is not None:
            if renderer is None:
                # Images can still run without a renderer; document pages will
                # fail closed with an explicit renderer error.
                pass
            visual_runner = MultiFileVisualIntakeV2(
                visual_provider,
                renderer=renderer,
                max_pages_per_shard=max_pages_per_shard,
            )
        if (
            visual_runner is not None
            and not isinstance(visual_runner, MultiFileVisualIntakeV2)
            and not callable(visual_runner)
        ):
            raise DesktopImportBridgeError(
                "visual_runner_invalid",
                "视觉执行器必须是页面核心或页面分片 callable。",
                503,
            )
        if type(max_pages_per_shard) is not int or not 1 <= max_pages_per_shard <= 20:
            raise DesktopImportBridgeError(
                "shard_page_limit_invalid", "视觉分片页数上限不正确。", 409
            )
        self.visual_runner = visual_runner
        self.renderer = renderer
        self.native_importer = native_importer
        # The configured provider/core path is the production outbound
        # boundary and requires durable page archival.  A generic callable is
        # retained as an explicitly trusted, page-only test adapter; it still
        # cannot receive raw document bytes, but callers should use the core
        # path when they need CAS-backed production persistence.
        self._visual_requires_archive = visual_provider_supplied or isinstance(
            visual_runner, MultiFileVisualIntakeV2
        )
        self.max_pages_per_shard = max_pages_per_shard
        self.archive = PixelArchive(archive_root) if archive_root is not None else None

    @staticmethod
    def _validate_request(
        request: DesktopImportRequest,
    ) -> tuple[DesktopSourceFile, ...]:
        if not isinstance(request, DesktopImportRequest) or not request.sources:
            raise DesktopImportBridgeError(
                "batch_sources_invalid", "请至少选择一份题目或讲义资料。"
            )
        if (
            not isinstance(request.source_type, str)
            or len(request.source_type) > 1000
            or any(ord(char) < 32 for char in request.source_type)
        ):
            raise DesktopImportBridgeError(
                "source_type_invalid", "资料来源类型不正确。"
            )
        if request.batch_id is not None and (
            not isinstance(request.batch_id, str)
            or _SAFE_BATCH_ID.fullmatch(request.batch_id) is None
        ):
            raise DesktopImportBridgeError("batch_id_invalid", "批次标识不正确。")
        sources = tuple(request.sources)
        if len(sources) > _MAX_SOURCE_FILES:
            raise DesktopImportBridgeError(
                "batch_source_limit_exceeded", "一个批次的文件数量超过安全上限。", 409
            )
        if (
            sum(
                len(source.content)
                for source in sources
                if isinstance(source, DesktopSourceFile)
            )
            > _MAX_BATCH_BYTES
        ):
            raise DesktopImportBridgeError(
                "batch_size_limit_exceeded", "一个批次的文件总大小超过安全上限。", 409
            )
        by_role: dict[str, list[DesktopSourceFile]] = {
            role: [] for role in SOURCE_ROLES
        }
        ids: set[str] = set()
        group_ids: set[str] = set()
        for source in sources:
            if not isinstance(source, DesktopSourceFile):
                raise DesktopImportBridgeError("source_invalid", "批次资料描述不正确。")
            # Validate image bytes at the planning boundary as well as when
            # archiving.  A custom/page-only runner must never receive bytes
            # whose declared MIME does not match an actual raster page.
            if source.mime_type in _IMAGE_MIMES:
                _image_dimensions(source.content, source.mime_type)
            by_role[source.role].append(source)
            source_id = source.effective_source_file_id
            if source_id in ids:
                raise DesktopImportBridgeError(
                    "source_file_id_duplicate", "批次资料标识重复。"
                )
            ids.add(source_id)
            group_ids.add(source.group_id)
        if len(group_ids) != 1:
            raise DesktopImportBridgeError(
                "batch_group_ambiguous",
                "一个视觉候选批次只能包含同一资料分组。",
                409,
            )
        if not by_role["question"] and not by_role["handout"]:
            raise DesktopImportBridgeError(
                "question_source_required", "批次必须包含题目或讲义资料。"
            )
        for role, values in by_role.items():
            values.sort(key=lambda item: item.order_index)
            expected = list(range(1, len(values) + 1))
            if [item.order_index for item in values] != expected:
                raise DesktopImportBridgeError(
                    "source_order_invalid",
                    f"{role} 资料顺序必须从 1 连续编号。",
                )
        return tuple(
            item
            for role in SOURCE_ROLES
            for item in sorted(by_role[role], key=lambda value: value.order_index)
        )

    @staticmethod
    def _batch_id(
        request: DesktopImportRequest, sources: Sequence[DesktopSourceFile]
    ) -> str:
        subject = {
            "schema_version": DESKTOP_IMPORT_BRIDGE_SCHEMA_VERSION,
            "native_text_revision": NATIVE_WORD_TEXT_REVISION,
            "source_type": request.source_type.strip() or "未分类资料",
            "sources": [source.as_manifest() for source in sources],
        }
        digest = _canonical_digest(subject)
        return request.batch_id or f"DESKTOPBATCH-{digest[:32]}"

    def plan(self, request: DesktopImportRequest) -> DesktopImportPlan:
        sources = self._validate_request(request)
        batch_id = self._batch_id(request, sources)
        if _SAFE_BATCH_ID.fullmatch(batch_id) is None:
            raise DesktopImportBridgeError("batch_id_invalid", "批次标识不正确。")
        inspections: list[NativeDocxInspection] = []
        source_states: dict[str, str] = {}
        for source in sources:
            if source.mime_type == _DOCX_MIME:
                inspection = inspect_native_docx(source)
                inspections.append(inspection)
                source_states[source.effective_source_file_id] = inspection.import_state
            else:
                source_states[source.effective_source_file_id] = "visual_only_required"
        visual_ids = tuple(
            source.effective_source_file_id
            for source in sources
            if source_states[source.effective_source_file_id] != "native_text_complete"
        )
        visual_id_set = set(visual_ids)
        context_ids: list[str] = []
        if visual_id_set:
            # Keep a native question/handout page only when an answer-only
            # visual source would otherwise have no paper context.  Pure native
            # sources are never sent merely because they exist.
            for role in ("question", "handout"):
                role_sources = [source for source in sources if source.role == role]
                if role_sources and not any(
                    source.effective_source_file_id in visual_id_set
                    for source in role_sources
                ):
                    context_ids.append(role_sources[0].effective_source_file_id)
        return DesktopImportPlan(
            batch_id=batch_id,
            sources=tuple(source.as_manifest() for source in sources),
            source_import_states=tuple(
                {
                    "source_file_id": source.effective_source_file_id,
                    "import_state": source_states[source.effective_source_file_id],
                }
                for source in sources
            ),
            native_inspections=tuple(inspections),
            native_quick_count=sum(item.quick_import_eligible for item in inspections),
            visual_queue_count=sum(
                source_states[source.effective_source_file_id] != "native_text_complete"
                for source in sources
            ),
            visual_required=bool(visual_ids),
            visual_source_ids=visual_ids,
            visual_context_source_ids=tuple(context_ids),
        )

    @staticmethod
    def _native_records(
        plan: DesktopImportPlan,
        *,
        archived_page_source_ids: frozenset[str] = frozenset(),
    ) -> tuple[Mapping[str, Any], ...]:
        records: list[Mapping[str, Any]] = []
        for inspection in plan.native_inspections:
            lane = (
                "native_text_submitted"
                if inspection.quick_import_eligible
                else "visual_completion_queue"
            )
            records.append(
                {
                    "candidate_id": f"NATIVE-{inspection.source_sha256[:24]}",
                    "source_file_id": inspection.source_file_id,
                    "source_sha256": inspection.source_sha256,
                    "native_text": inspection.native_text,
                    "native_blocks": [
                        dict(block) for block in inspection.native_blocks
                    ],
                    "features": list(inspection.features),
                    "visual_object_refs": [
                        dict(ref) for ref in inspection.visual_object_refs
                    ],
                    "import_state": inspection.import_state,
                    "inventory_lane": lane,
                    "quick_import_eligible": inspection.quick_import_eligible,
                    "page_binding_status": (
                        "archived_rendered_page_evidence_pending_bbox"
                        if inspection.source_file_id in archived_page_source_ids
                        else inspection.page_binding_status
                    ),
                    "candidate_only": True,
                    "central_question_bank_write": False,
                }
            )
        return tuple(records)

    def _archive_sources_and_direct_pages(
        self,
        sources: Sequence[DesktopSourceFile],
        *,
        batch_id: str,
        native_page_source_ids: frozenset[str] = frozenset(),
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self.archive is None:
            return [], []
        pages: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for source in sources:
            self.archive.put_source(source)
            if source.mime_type in _IMAGE_MIMES:
                width, height = _image_dimensions(source.content, source.mime_type)
                pages.append(
                    self.archive.put_page(
                        source_file_id=source.effective_source_file_id,
                        source_sha256=source.source_sha256,
                        source_role=source.role,
                        source_order=source.order_index,
                        page_number=1,
                        mime_type=source.mime_type,
                        pixels=source.content,
                        width=width,
                        height=height,
                        render_recipe_sha256=_identity_render_recipe(source.mime_type),
                    )
                )
            elif (
                source.effective_source_file_id in native_page_source_ids
                and self.renderer is not None
            ):
                # Preserve the rendered page of a native-text-complete DOCX
                # locally without sending it to the visual provider.  A
                # rendering failure must not silently downgrade to OCR or
                # prevent the separately authorized editable-text fast lane;
                # it remains an explicit evidence blocker instead.
                try:
                    _shards, rendered = _render_visual_pages(
                        (source,),
                        renderer=self.renderer,
                        batch_id=batch_id,
                        max_pages_per_shard=self.max_pages_per_shard,
                        archive=self.archive,
                    )
                except DesktopImportBridgeError as exc:
                    blockers.append(
                        {
                            "code": "native_page_archive_pending",
                            "source_file_id": source.effective_source_file_id,
                            "cause_code": exc.code,
                            "message_zh": "纯文字资料已保留原文件，渲染页证据等待补全。",
                            "candidate_only": True,
                        }
                    )
                else:
                    pages.extend(rendered)
        return pages, blockers

    def _invoke_native(
        self,
        sources: Sequence[DesktopSourceFile],
        inspections: Sequence[NativeDocxInspection],
    ) -> Mapping[str, Any] | None:
        # Only the pure editable-text lane is eligible for the fast native
        # importer.  Hybrid/visual-only DOCX records remain candidates for the
        # page-pixel lane and must not be silently downgraded to text import.
        quick_inspections = tuple(
            inspection for inspection in inspections if inspection.quick_import_eligible
        )
        if self.native_importer is None or not quick_inspections:
            return None
        submit = getattr(self.native_importer, "submit", self.native_importer)
        if not callable(submit):
            raise DesktopImportBridgeError(
                "native_importer_invalid", "原生文字导入器不可用。", 503
            )
        try:
            inspection_ids = {item.source_file_id for item in quick_inspections}
            native_sources = tuple(
                source
                for source in sources
                if source.effective_source_file_id in inspection_ids
            )
            value = submit(native_sources, quick_inspections)
        except DesktopImportBridgeError:
            raise
        except Exception as exc:
            raise DesktopImportBridgeError(
                "native_import_failed", "原生文字快导入未完成。", 409
            ) from exc
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise DesktopImportBridgeError(
                "native_import_receipt_invalid", "原生文字导入回执格式不正确。", 409
            )
        receipt = deepcopy(dict(value))
        _assert_no_secret_fields(receipt)
        if receipt.get("central_question_bank_write") is True:
            raise DesktopImportBridgeError(
                "native_authority_invalid",
                "原生导入回执声明了中央题库写入，已拒绝。",
                409,
            )
        receipt["candidate_only"] = True
        receipt["central_question_bank_write"] = False
        return receipt

    def _run_visual(
        self,
        sources: Sequence[DesktopSourceFile],
        *,
        batch_id: str,
        archive_pages: list[dict[str, Any]],
        visual_source_ids: Sequence[str] = (),
        visual_context_source_ids: Sequence[str] = (),
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[
        str, Mapping[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]
    ]:
        # The plan separates sources that require visual completion from
        # native-only sources.  A native question/handout is retained only as
        # an explicit context page for an answer-only visual batch; unrelated
        # native pages must never enter the visual provider lane.
        if not visual_source_ids:
            visual_source_ids = tuple(
                source.effective_source_file_id
                for source in sources
                if source.mime_type != _DOCX_MIME
                or inspect_native_docx(source).import_state != "native_text_complete"
            )
        selected_ids = set(visual_source_ids) | set(visual_context_source_ids)
        selected_sources = tuple(
            source
            for source in sources
            if source.effective_source_file_id in selected_ids
        )
        if not selected_ids:
            # Backward-compatible private-call behavior; ``process`` always
            # supplies the explicit plan IDs above.
            selected_sources = tuple(sources)
        if self.visual_runner is None:
            return (
                "awaiting_visual_provider",
                None,
                [
                    {
                        "code": "visual_provider_required",
                        "message_zh": "含图片、公式、结构式、装置或曲线的资料等待视觉模型。",
                        "source_file_ids": list(visual_source_ids),
                    }
                ],
                archive_pages,
            )
        runner = self.visual_runner
        archive_renderer: _ArchivingRenderer | None = None
        try:
            if isinstance(runner, MultiFileVisualIntakeV2):
                renderer: PixelPageRenderer | None = self.renderer
                if self.archive is not None and renderer is not None:
                    archive_renderer = _ArchivingRenderer(
                        renderer,
                        self.archive,
                        source_orders={
                            source.effective_source_file_id: source.order_index
                            for source in selected_sources
                        },
                        source_shas={
                            source.effective_source_file_id: source.source_sha256
                            for source in selected_sources
                        },
                    )
                    renderer = archive_renderer
                grouped = {
                    role: [
                        source.as_batch_file()
                        for source in selected_sources
                        if source.role == role
                    ]
                    for role in SOURCE_ROLES
                }
                # The isolated core owns the renderer passed at construction;
                # if an archive wrapper is needed, temporarily replace it for
                # this call and restore it immediately after.
                previous_renderer = getattr(runner, "renderer", None)
                previous_provider = getattr(runner, "provider", None)
                if renderer is not None and hasattr(runner, "renderer"):
                    runner.renderer = renderer
                if should_cancel is not None and hasattr(runner, "provider"):
                    runner.provider = _CancellationAwareVisualProvider(
                        previous_provider, should_cancel
                    )
                try:
                    candidate = runner.process(
                        question_files=grouped["question"],
                        answer_files=grouped["answer"],
                        handout_files=grouped["handout"],
                        batch_id=batch_id,
                    )
                finally:
                    if hasattr(runner, "renderer"):
                        runner.renderer = previous_renderer
                    if hasattr(runner, "provider"):
                        runner.provider = previous_provider
                if not isinstance(candidate, Mapping):
                    raise DesktopImportBridgeError(
                        "visual_candidate_invalid", "视觉候选格式不正确。", 502
                    )
                _assert_no_secret_fields(candidate)
                candidate = _validate_declared_candidate(candidate)
            else:
                # A generic callable is a deliberately narrow adapter seam
                # for tests/desktop experiments.  It receives only immutable
                # ``VisualShardRequest`` page objects; raw PDF/DOCX bytes and
                # editable text never cross this call boundary.
                if should_cancel is not None and should_cancel():
                    raise DesktopImportBridgeError("cancelled", "导入任务已取消。", 409)
                page_shards, page_archive = _render_visual_pages(
                    selected_sources,
                    renderer=self.renderer,
                    batch_id=batch_id,
                    max_pages_per_shard=self.max_pages_per_shard,
                    archive=self.archive,
                )
                archive_pages.extend(page_archive)
                candidate = runner(page_shards, batch_id=batch_id)
                if should_cancel is not None and should_cancel():
                    raise DesktopImportBridgeError("cancelled", "导入任务已取消。", 409)
                if not isinstance(candidate, Mapping):
                    raise DesktopImportBridgeError(
                        "visual_candidate_invalid", "视觉候选格式不正确。", 502
                    )
                _assert_no_secret_fields(candidate)
                candidate = _validate_declared_candidate(candidate)
        except DesktopImportBridgeError:
            raise
        except IntakeBatchV2Error as exc:
            raise DesktopImportBridgeError(
                exc.code, str(exc), getattr(exc, "status", 409)
            ) from exc
        except Exception as exc:
            code = getattr(exc, "code", "visual_batch_failed")
            raise DesktopImportBridgeError(
                str(code), "视觉页面候选生成未完成。", 409
            ) from exc
        if (
            candidate.get("candidate_status") != "candidate_only"
            or candidate.get("central_question_bank_write") is not False
        ):
            raise DesktopImportBridgeError(
                "candidate_authority_invalid", "视觉候选越过了候选状态边界。", 409
            )
        if archive_renderer is not None:
            archive_pages.extend(archive_renderer.pages)
        return "completed", candidate, [], archive_pages

    def _persist_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        batch_id: str,
    ) -> tuple[str, str, str] | None:
        if self.archive is None:
            return None
        root = self.archive.root / "candidates" / batch_id
        try:
            incoming_hash = candidate_sha256(candidate)
            if root.exists() and any(root.iterdir()):
                cas = CandidateCAS.open(root)
                # A retry for the same batch may see a teacher-edited CAS.
                # Reusing it is safe only when the incoming visual candidate
                # is the immutable initial object; otherwise fail closed
                # instead of returning a hash for a different candidate.
                head_path = root / "head.json"
                head = _strict_json_loads(head_path.read_bytes())
                if (
                    not isinstance(head, Mapping)
                    or head.get("initial_candidate_sha256") != incoming_hash
                ):
                    raise DesktopImportBridgeError(
                        "candidate_cas_batch_conflict",
                        "同一批次已有不同的视觉候选 CAS。",
                        409,
                    )
            else:
                cas = CandidateCAS(candidate, root=root)
        except IntakeBatchV2Error as exc:
            raise DesktopImportBridgeError(
                exc.code, "视觉候选无法写入个人候选 CAS。", 409
            ) from exc
        except DesktopImportBridgeError:
            raise
        except (
            OSError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise DesktopImportBridgeError(
                "candidate_cas_corrupt", "视觉候选 CAS 无法核验。", 409
            ) from exc
        snapshot = cas.snapshot()
        if snapshot["candidate_sha256"] != incoming_hash:
            raise DesktopImportBridgeError(
                "candidate_cas_batch_conflict",
                "该批次的视觉候选已被教师修改，不能静默替换。",
                409,
            )
        return (
            str(snapshot["candidate_sha256"]),
            str(snapshot["revision_token"]),
            str(root),
        )

    @staticmethod
    def _manifest_subject(payload: Mapping[str, Any]) -> dict[str, Any]:
        plan = payload.get("plan")
        if not isinstance(plan, Mapping):
            raise DesktopImportBridgeError(
                "manifest_corrupt", "已有桌面导入回执无法核验。", 409
            )
        return {
            "schema_version": payload.get("schema_version"),
            "batch_id": payload.get("batch_id"),
            "source_type": payload.get("source_type"),
            "sources": plan.get("sources"),
            "source_import_states": plan.get("source_import_states"),
            "visual_source_ids": plan.get("visual_source_ids"),
            "visual_context_source_ids": plan.get("visual_context_source_ids"),
        }

    def _load_existing_manifest(
        self,
        plan: DesktopImportPlan,
        *,
        source_type: str,
    ) -> Mapping[str, Any] | None:
        """Read a same-source receipt before invoking side-effecting ports."""

        if self.archive is None:
            return None
        path = self.archive.root / "batches" / f"{plan.batch_id}.json"
        if path.is_symlink():
            raise DesktopImportBridgeError(
                "manifest_write_failed", "桌面导入回执路径不安全。", 503
            )
        if not path.exists():
            return None
        try:
            existing = _strict_json_loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise DesktopImportBridgeError(
                "manifest_corrupt", "已有桌面导入回执无法核验。", 409
            ) from exc
        if not isinstance(existing, Mapping):
            raise DesktopImportBridgeError(
                "manifest_corrupt", "已有桌面导入回执无法核验。", 409
            )
        expected = {
            "schema_version": DESKTOP_IMPORT_BRIDGE_SCHEMA_VERSION,
            "batch_id": plan.batch_id,
            "source_type": source_type,
            "plan": plan.as_dict(),
        }
        try:
            same_source_closure = canonical_json_bytes(
                self._manifest_subject(existing)
            ) == canonical_json_bytes(self._manifest_subject(expected))
        except (TypeError, ValueError) as exc:
            raise DesktopImportBridgeError(
                "manifest_corrupt", "已有桌面导入回执无法核验。", 409
            ) from exc
        if not same_source_closure:
            raise DesktopImportBridgeError(
                "manifest_conflict", "同一批次已有不同的资料集合。", 409
            )
        if (
            existing.get("candidate_only") is not True
            or existing.get("central_question_bank_write") is not False
        ):
            raise DesktopImportBridgeError(
                "manifest_corrupt", "已有桌面导入回执权限边界不正确。", 409
            )
        return deepcopy(dict(existing))

    def _write_manifest(self, result: DesktopImportResult) -> str | None:
        if self.archive is None:
            return None
        batches = self.archive.root / "batches"
        try:
            if batches.exists() and batches.is_symlink():
                raise DesktopImportBridgeError(
                    "manifest_write_failed", "桌面导入回执目录不安全。", 503
                )
            batches.mkdir(parents=True, exist_ok=True)
            with suppress(OSError):
                os.chmod(batches, 0o700)
        except OSError as exc:
            raise DesktopImportBridgeError(
                "manifest_write_failed", "桌面导入回执无法保存。", 503
            ) from exc
        path = batches / f"{result.batch_id}.json"
        value = result.as_dict()
        value["manifest_path"] = None
        try:
            raw = canonical_json_bytes(value)
        except (TypeError, ValueError) as exc:
            raise DesktopImportBridgeError(
                "manifest_invalid", "桌面导入回执格式不正确。", 409
            ) from exc
        if path.is_symlink():
            raise DesktopImportBridgeError(
                "manifest_write_failed", "桌面导入回执路径不安全。", 503
            )
        if path.exists():
            try:
                existing_raw = path.read_bytes()
                if existing_raw == raw:
                    return str(path)
                existing = _strict_json_loads(existing_raw)
            except (
                OSError,
                UnicodeDecodeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                raise DesktopImportBridgeError(
                    "manifest_corrupt", "已有桌面导入回执无法核验。", 409
                ) from exc
            if not isinstance(existing, Mapping):
                raise DesktopImportBridgeError(
                    "manifest_corrupt", "已有桌面导入回执无法核验。", 409
                )

            try:
                same_source_closure = canonical_json_bytes(
                    self._manifest_subject(existing)
                ) == canonical_json_bytes(self._manifest_subject(value))
            except (DesktopImportBridgeError, TypeError, ValueError) as exc:
                if isinstance(exc, DesktopImportBridgeError):
                    raise
                raise DesktopImportBridgeError(
                    "manifest_corrupt", "已有桌面导入回执无法核验。", 409
                ) from exc
            existing_status = existing.get("visual_status")
            pending_statuses = {
                "awaiting_visual_provider",
                "awaiting_teacher_confirmation",
                "failed",
            }
            # A retry may advance a transient/pending visual lane after the
            # teacher supplies a provider or confirms page egress.  It may
            # replace only a manifest with the exact same immutable source
            # closure and no already-produced candidate.  A completed result
            # (or a teacher-edited CAS) remains immutable and conflicts.
            retryable = (
                same_source_closure
                and existing_status in pending_statuses
                and existing.get("visual_candidate") is None
                and existing.get("visual_candidate_sha256") is None
                and existing.get("candidate_only") is True
                and existing.get("central_question_bank_write") is False
            )
            if not retryable:
                raise DesktopImportBridgeError(
                    "manifest_conflict", "同一批次已有不同的桌面导入回执。", 409
                )
        elif path.exists():
            # Defensive branch for unusual filesystem races where existence
            # changes between ``is_symlink`` and ``exists`` checks.
            raise DesktopImportBridgeError(
                "manifest_write_failed", "桌面导入回执路径无法安全写入。", 503
            )
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, path)
        except OSError as exc:
            raise DesktopImportBridgeError(
                "manifest_write_failed", "桌面导入回执无法保存。", 503
            ) from exc
        finally:
            with suppress(OSError):
                temporary.unlink()
        return str(path)

    def process(
        self,
        request: DesktopImportRequest,
        *,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        visual_confirmation: bool | None = None,
    ) -> DesktopImportResult:
        if visual_confirmation is not None and type(visual_confirmation) is not bool:
            raise DesktopImportBridgeError(
                "teacher_confirmation_invalid", "教师视觉出站确认格式不正确。", 409
            )
        sources = self._validate_request(request)
        plan = self.plan(request)
        source_type = request.source_type.strip() or "未分类资料"
        existing_manifest = self._load_existing_manifest(
            plan,
            source_type=source_type,
        )
        if should_cancel is not None and should_cancel():
            raise DesktopImportBridgeError("cancelled", "导入任务已取消。", 409)
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "planned",
                    "batch_id": plan.batch_id,
                    "source_count": len(sources),
                }
            )
        native_page_source_ids = frozenset(
            row["source_file_id"]
            for row in plan.source_import_states
            if row["import_state"] == "native_text_complete"
        )
        if (
            plan.visual_required
            and self.visual_runner is not None
            and visual_confirmation is True
        ):
            # Context pages will be rendered by the visual core in this same
            # run.  Do not pre-render them through the native archive path as
            # well; a non-deterministic renderer could otherwise create two
            # hashes for the same source/page identity.
            native_page_source_ids = native_page_source_ids.difference(
                plan.visual_context_source_ids
            )
        archive_pages, archive_blockers = self._archive_sources_and_direct_pages(
            sources,
            batch_id=plan.batch_id,
            native_page_source_ids=native_page_source_ids,
        )
        archived_page_source_ids = frozenset(
            str(page["source_file_id"]) for page in archive_pages
        )
        native_records = self._native_records(
            plan,
            archived_page_source_ids=archived_page_source_ids,
        )
        existing_native_receipt = (
            existing_manifest.get("native_import_receipt")
            if existing_manifest is not None
            else None
        )
        if existing_native_receipt is not None:
            if (
                not isinstance(existing_native_receipt, Mapping)
                or existing_native_receipt.get("candidate_only") is not True
                or existing_native_receipt.get("central_question_bank_write")
                is not False
            ):
                raise DesktopImportBridgeError(
                    "manifest_corrupt", "已有原生导入回执权限边界不正确。", 409
                )
            _assert_no_secret_fields(existing_native_receipt)
            native_receipt = deepcopy(dict(existing_native_receipt))
        else:
            native_receipt = self._invoke_native(sources, plan.native_inspections)
        blockers: list[Mapping[str, Any]] = list(archive_blockers)
        visual_candidate: Mapping[str, Any] | None = None
        candidate_sha: str | None = None
        revision_token: str | None = None
        visual_status = "not_required"
        if plan.visual_required:
            if should_cancel is not None and should_cancel():
                raise DesktopImportBridgeError("cancelled", "导入任务已取消。", 409)
            if progress_callback is not None:
                progress_callback({"stage": "visual_pages", "batch_id": plan.batch_id})
            # A page-bearing visual runner is an outbound data boundary.  A
            # caller must opt in explicitly for this batch; an omitted value
            # (``None`` is retained for source compatibility) is fail-closed
            # and therefore has the same effect as ``False``.
            if visual_confirmation is not True and self.visual_runner is not None:
                visual_status = "awaiting_teacher_confirmation"
                blockers.append(
                    {
                        "code": "teacher_confirmation_required",
                        "message_zh": "发送原始页面前需要教师明确确认。",
                        "candidate_only": True,
                    }
                )
                visual_runner_skipped = True
            else:
                visual_runner_skipped = False
            try:
                if visual_runner_skipped:
                    raise _VisualRunSkipped()
                if self._visual_requires_archive and self.archive is None:
                    raise DesktopImportBridgeError(
                        "pixel_archive_required",
                        "视觉导入必须先配置个人像素归档目录。",
                        409,
                    )
                visual_status, visual_candidate, visual_blockers, archive_pages = (
                    self._run_visual(
                        sources,
                        batch_id=plan.batch_id,
                        archive_pages=archive_pages,
                        visual_source_ids=plan.visual_source_ids,
                        visual_context_source_ids=plan.visual_context_source_ids,
                        should_cancel=should_cancel,
                    )
                )
                blockers.extend(visual_blockers)
                if visual_candidate is not None:
                    # Always expose the deterministic candidate digest.  A
                    # desktop caller that omits ``archive_root`` still gets a
                    # content hash, while a configured personal-state root
                    # additionally provides the immutable CAS/revision token.
                    try:
                        candidate_sha = candidate_sha256(visual_candidate)
                    except (TypeError, ValueError) as exc:
                        raise DesktopImportBridgeError(
                            "visual_candidate_invalid",
                            "视觉候选无法生成内容哈希。",
                            502,
                        ) from exc
                    persisted = self._persist_candidate(
                        visual_candidate, batch_id=plan.batch_id
                    )
                    if persisted is not None:
                        candidate_sha, revision_token, _cas_root = persisted
            except DesktopImportBridgeError as exc:
                if not isinstance(exc, _VisualRunSkipped):
                    visual_status = "failed"
                visual_candidate = None
                candidate_sha = None
                revision_token = None
                if not isinstance(exc, _VisualRunSkipped):
                    blockers.append(
                        {
                            "code": exc.code,
                            "message_zh": exc.message_zh,
                            "candidate_only": True,
                        }
                    )
        unique_pages: dict[tuple[str, int], Mapping[str, Any]] = {}
        for page in archive_pages:
            identity = (str(page["source_file_id"]), int(page["page_number"]))
            previous = unique_pages.get(identity)
            if previous is not None:
                if previous.get("page_sha256") != page.get("page_sha256"):
                    raise DesktopImportBridgeError(
                        "pixel_page_identity_conflict",
                        "同一来源页产生了不一致的像素摘要。",
                        409,
                    )
                continue
            unique_pages[identity] = page
        archive_pages = [dict(page) for page in unique_pages.values()]
        visual_queue = tuple(
            {
                "source_file_id": source_id,
                "status": "pending" if visual_status != "completed" else "submitted",
                "reason": "需要页面视觉补全"
                if visual_status != "completed"
                else "已生成候选，等待教师复核",
                "candidate_only": True,
            }
            for source_id in plan.visual_source_ids
        )
        result = DesktopImportResult(
            batch_id=plan.batch_id,
            source_type=source_type,
            plan=plan,
            native_records=native_records,
            native_import_receipt=native_receipt,
            visual_status=visual_status,
            visual_candidate=visual_candidate,
            visual_candidate_sha256=candidate_sha,
            visual_revision_token=revision_token,
            visual_queue=visual_queue,
            pixel_pages=tuple(archive_pages),
            blockers=tuple(blockers),
            manifest_path=None,
        )
        manifest_path = self._write_manifest(result)
        if manifest_path is not None:
            result = replace(result, manifest_path=manifest_path)
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "completed",
                    "batch_id": plan.batch_id,
                    "visual_status": visual_status,
                    "native_quick_count": plan.native_quick_count,
                }
            )
        return result

    @staticmethod
    def run_existing_native_corpus(
        runner: Callable[..., Mapping[str, Any]],
        expanded_root: str | Path,
        *,
        progress_callback: Callable[[Any], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Bridge the existing 98-package/196-DOCX runner without guessing.

        ``runner`` is injected so the desktop seam does not import or mutate
        the legacy service.  The returned summary intentionally keeps the
        native inventory and visual queue separate.
        """

        runner_path = Path(expanded_root)
        try:
            signature = inspect.signature(runner)
        except (TypeError, ValueError):
            signature = None
        if signature is None:
            try:
                value = runner(
                    runner_path,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                )
            except TypeError as exc:
                # Retry only for a genuine legacy signature mismatch; a
                # TypeError raised inside the runner must not be invoked twice.
                message = str(exc)
                if "unexpected keyword argument" not in message:
                    raise
                value = runner(runner_path)
        else:
            parameters = signature.parameters
            accepts_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            positional_only = [
                parameter
                for parameter in parameters.values()
                if parameter.kind is inspect.Parameter.POSITIONAL_ONLY
            ]
            positional_names = {parameter.name for parameter in positional_only}
            args: list[Any] = [runner_path]
            for name, callback in (
                ("progress_callback", progress_callback),
                ("should_cancel", should_cancel),
            ):
                if name in positional_names:
                    # Legacy runners use the root, progress and cancellation
                    # callbacks in this order.  Append only through the
                    # declared positional-only prefix.
                    args.append(callback)
            kwargs: dict[str, Any] = {}
            if (
                accepts_kwargs
                or "progress_callback" in parameters
                and parameters["progress_callback"].kind
                in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
            ):
                kwargs["progress_callback"] = progress_callback
            if (
                accepts_kwargs
                or "should_cancel" in parameters
                and parameters["should_cancel"].kind
                in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
            ):
                kwargs["should_cancel"] = should_cancel
            value = runner(*args, **kwargs)
        if hasattr(value, "as_dict"):
            value = value.as_dict()
        if not isinstance(value, Mapping):
            raise DesktopImportBridgeError(
                "native_corpus_receipt_invalid", "讲义批次回执格式不正确。", 409
            )
        if value.get("central_question_bank_write") is True:
            raise DesktopImportBridgeError(
                "native_authority_invalid",
                "原生导入回执声明了中央题库写入，已拒绝。",
                409,
            )
        result = {
            "schema_version": DESKTOP_IMPORT_BRIDGE_SCHEMA_VERSION,
            "lane": "native_text_and_visual_queue",
            "inventory_complete_98x196": value.get("inventory_complete_98x196") is True,
            "documents_total": value.get("documents_total"),
            "documents_completed": value.get("documents_completed"),
            "documents_failed": value.get("documents_failed"),
            "quick_import_candidates": value.get("quick_import_candidates", 0),
            "visual_completion_candidates": value.get(
                "visual_completion_candidates", 0
            ),
            "candidate_only": True,
            "central_question_bank_write": False,
            "page_visual_completion_complete": False,
            "source_role_pairing_is_explicit_in_v2": True,
        }
        return result


__all__ = [
    "DESKTOP_IMPORT_BRIDGE_SCHEMA_VERSION",
    "IMPORT_STATES",
    "SOURCE_ROLES",
    "DesktopImportBridgeError",
    "DesktopImportCoordinatorV2",
    "DesktopImportFile",
    "DesktopImportPlan",
    "DesktopImportRequest",
    "DesktopImportResult",
    "DesktopSourceFile",
    "NativeDocxInspection",
    "PixelArchive",
    "VisualPageRunner",
    "inspect_native_docx",
]
