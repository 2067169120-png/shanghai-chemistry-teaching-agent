from __future__ import annotations

"""Fail-closed, read-only curriculum tree and explicit textbook mappings.

The first release deliberately stops at the verified directory boundary:
five books, nineteen chapters, and sixty numbered sections.  It does not
invent an edition/printing, an atomic textbook unit, or a section identifier.

Question mappings are admitted only from the currently activated readers for
the Huangpu/Qibao/Hongkou Master-direct batches and the 57-item Supplemental
WeChat overlay.  K labels are carried as display metadata only; they are never
used to infer a chapter or section.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from .reader_cancellation import check_read_cancelled

SCHEMA_VERSION = "1.0.0-curriculum-workbench"
DATA_SNAPSHOT_ID = "CURRICULUM-WORKBENCH-2026-08-27-V1"
SCOPE = "candidate_only_read_only_curriculum_workbench"

DIRECTORY_RELATIVE = Path(
    "kb/classification/"
    "supplemental_wechat_textbook_tagging_v1_2026-08-27/"
    "textbook_directory_nodes.json"
)
EXPECTED_DIRECTORY_FILE_SHA256 = (
    "3fb5e6c02c37191ca464d36ebfde8020e22e85aec39265eab660c19831b5f9aa"
)
EXPECTED_DIRECTORY_SCHEMA_VERSION = "1.0.0-textbook-directory-nodes"
EXPECTED_VOLUME_COUNT = 5
EXPECTED_CHAPTER_COUNT = 19
EXPECTED_SECTION_COUNT = 60
EXPECTED_VOLUME_IDS = ("TB-M1", "TB-M2", "TB-E1", "TB-E2", "TB-E3")

ACTIVE_MASTER_COUNT = 30
ACTIVE_SUPPLEMENTAL_COUNT = 57
ACTIVE_ATOMIC_COUNT = ACTIVE_MASTER_COUNT + ACTIVE_SUPPLEMENTAL_COUNT
EXPECTED_MAPPING_ENTRY_COUNT = 163
EXPECTED_COMPLETE_ATOMIC_COUNT = 80
EXPECTED_PARTIAL_ATOMIC_COUNT = 7
EXPECTED_BLOCKED_ATOMIC_COUNT = 7
EXPECTED_BLOCKED_ENTRY_COUNT = 7

EDITION_STATUS_UNKNOWN = "unknown_not_externally_verified"
UNIT_STATUS_UNKNOWN = "unknown_no_verified_atomic_unit_registry"
TOP_MAPPING_STATUS = {
    "complete_directory_level_unit_unknown": "complete",
    "partial_blocked": "partial",
}
MAPPED_ENTRY_STATUSES = frozenset(
    {"toc_direct_directory_mapping", "direct_visual_directory_mapping"}
)
BLOCKED_ENTRY_STATUS = "blocked_pending_review"
PUBLIC_MAPPING_STATUSES = frozenset({"complete", "partial", "blocked"})

AUTHORITY = {
    "read_only": True,
    "candidate_mapping_only": True,
    "human_taxonomy_reviewed": False,
    "edition_verified": False,
    "unit_verified": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "cross_layer_sum_allowed": False,
}

_TOP_LEVEL_KEYS = frozenset(
    {
        "chapter_count",
        "nodes",
        "numbered_section_count",
        "schema_version",
        "section_id_policy",
        "title",
        "unit_policy",
        "volume_count",
        "volumes",
    }
)
_VOLUME_KEYS = frozenset(
    {
        "edition_or_printing",
        "edition_status",
        "evidence_level",
        "publisher",
        "source_path",
        "source_root",
        "source_sha256",
        "textbook_family",
        "toc_pdf_pages",
        "toc_visual_evidence",
        "volume_id",
        "volume_title",
    }
)
_NODE_KEYS = frozenset(
    {
        "chapter_id",
        "chapter_title",
        "content_pdf_pages",
        "node_key",
        "printed_pages",
        "section_id",
        "section_number",
        "section_title",
        "source_path",
        "source_root",
        "source_sha256",
        "status",
        "toc_pdf_pages",
        "toc_visual_evidence",
        "unit_id",
        "unit_status",
        "unit_title",
        "volume_id",
        "volume_title",
    }
)
_TOC_EVIDENCE_KEYS = frozenset({"bytes", "path", "role", "sha256"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SECTION_NUMBER = re.compile(r"[0-9]{1,2}\.[0-9]{1,2}\Z")
_FORBIDDEN_PUBLIC_TEXT = re.compile(
    r"(?i:https?://|file:/+|(?<![a-z0-9])[a-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r"|(?<![A-Za-z0-9_.-])(?:sh-chem-db|staging|runtime|integrations|"
    r"\.intake|课本)[\\/])"
)


class CurriculumWorkbenchError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class CurriculumMappingEntry:
    """One evidence-explicit directory mapping; no inferred precision."""

    knowledge_tag: str
    knowledge_role: str
    relation: str
    evidence_status: str
    mapping_status: str
    evidence_level: str
    volume_id: str
    volume_title: str
    chapter_id: str
    chapter_title: str
    section_key: str | None
    section_id: str | None
    section_number: str | None
    section_title: str | None
    unit_id: None
    unit_title: None
    unit_status: str
    edition_or_printing: None
    edition_status: str

    @property
    def blocked(self) -> bool:
        return self.mapping_status == "blocked"

    def public(self) -> dict[str, Any]:
        return {
            "knowledge_tag": self.knowledge_tag,
            "knowledge_role": self.knowledge_role,
            "relation": self.relation,
            "evidence_status": self.evidence_status,
            "mapping_status": self.mapping_status,
            "evidence_level": self.evidence_level,
            "volume_id": self.volume_id,
            "volume_title": self.volume_title,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "section_key": self.section_key,
            "section_id": self.section_id,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "unit_id": self.unit_id,
            "unit_title": self.unit_title,
            "unit_status": self.unit_status,
            "edition_or_printing": self.edition_or_printing,
            "edition_status": self.edition_status,
        }


@dataclass(frozen=True, slots=True)
class CurriculumAtomicMapping:
    """A question-level mapping admitted to the canonical active read set."""

    atomic_id: str
    source_layer: str
    source_batch: str
    mapping_status: str
    entries: tuple[CurriculumMappingEntry, ...]

    def public(self) -> dict[str, Any]:
        return {
            "atomic_id": self.atomic_id,
            "source_layer": self.source_layer,
            "source_batch": self.source_batch,
            "mapping_status": self.mapping_status,
            "entries": [entry.public() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class _Section:
    section_key: str
    section_id: str | None
    section_number: str
    section_title: str
    volume_id: str
    volume_title: str
    chapter_id: str
    chapter_title: str
    unit_id: None
    unit_title: None
    unit_status: str


@dataclass(frozen=True, slots=True)
class _Chapter:
    chapter_id: str
    chapter_title: str
    volume_id: str
    sections: tuple[_Section, ...]


@dataclass(frozen=True, slots=True)
class _Volume:
    volume_id: str
    volume_title: str
    textbook_family: str
    publisher: str
    evidence_level: str
    edition_or_printing: None
    edition_status: str
    chapters: tuple[_Chapter, ...]


@dataclass(frozen=True, slots=True)
class _NodeStats:
    mapped_ids: frozenset[str]
    complete_ids: frozenset[str]
    partial_ids: frozenset[str]
    blocked_ids: frozenset[str]
    mapped_entry_count: int
    blocked_entry_count: int

    def public(self) -> dict[str, int]:
        return {
            "mapped_atomic_count": len(self.mapped_ids),
            "complete_atomic_count": len(self.complete_ids),
            "partial_atomic_count": len(self.partial_ids),
            "blocked_atomic_count": len(self.blocked_ids),
            "mapped_entry_count": self.mapped_entry_count,
            "blocked_entry_count": self.blocked_entry_count,
        }


@dataclass(frozen=True, slots=True)
class _Snapshot:
    volumes: tuple[_Volume, ...]
    mappings: tuple[CurriculumAtomicMapping, ...]
    section_by_key: Mapping[str, _Section]
    section_key_by_id: Mapping[str, str]
    chapter_to_volume: Mapping[str, str]
    stats: Mapping[tuple[str, str], _NodeStats]
    global_stats: _NodeStats
    active_layers: tuple[dict[str, Any], ...]
    excluded_layers: tuple[dict[str, Any], ...]


@dataclass
class _MutableNodeStats:
    mapped_ids: set[str]
    complete_ids: set[str]
    partial_ids: set[str]
    blocked_ids: set[str]
    mapped_entry_count: int = 0
    blocked_entry_count: int = 0

    @classmethod
    def new(cls) -> _MutableNodeStats:
        return cls(set(), set(), set(), set())

    def freeze(self) -> _NodeStats:
        return _NodeStats(
            mapped_ids=frozenset(self.mapped_ids),
            complete_ids=frozenset(self.complete_ids),
            partial_ids=frozenset(self.partial_ids),
            blocked_ids=frozenset(self.blocked_ids),
            mapped_entry_count=self.mapped_entry_count,
            blocked_entry_count=self.blocked_entry_count,
        )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CurriculumWorkbenchError(
            "curriculum_directory_json_invalid",
            "教材目录不是严格 UTF-8 JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise CurriculumWorkbenchError(
            "curriculum_directory_json_invalid", "教材目录顶层必须是对象。"
        )
    return value


def _require_safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise CurriculumWorkbenchError(
            "curriculum_identifier_invalid", f"{label} 标识无效。"
        )
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CurriculumWorkbenchError(
            "curriculum_directory_structure_invalid", f"{label} 文本无效。"
        )
    return value


def _require_page_pair(value: Any, label: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(item) is not int or item < 1 for item in value)
        or value[0] > value[1]
    ):
        raise CurriculumWorkbenchError(
            "curriculum_directory_structure_invalid", f"{label} 页码范围无效。"
        )


def _assert_safe_public(value: Any, *, key: str | None = None) -> None:
    if isinstance(key, str) and any(
        token in key.casefold() for token in ("path", "url", "sha256", "hash")
    ):
        raise CurriculumWorkbenchError(
            "curriculum_projection_leak", "教材工作台响应包含禁止字段。"
        )
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _assert_safe_public(nested_value, key=str(nested_key))
        return
    if isinstance(value, list):
        for nested in value:
            _assert_safe_public(nested, key=key)
        return
    if isinstance(value, str) and (
        _FORBIDDEN_PUBLIC_TEXT.search(value) or _HEX64.fullmatch(value)
    ):
        raise CurriculumWorkbenchError(
            "curriculum_projection_leak",
            "教材工作台响应包含本地位置、链接或摘要值。",
        )


def _same(value: Any, expected: Any, code: str, message: str) -> None:
    if value != expected:
        raise CurriculumWorkbenchError(code, message)


class CurriculumWorkbenchReader:
    """Cache one verified curriculum/mapping snapshot and return safe copies."""

    def __init__(
        self,
        shchem_root: Path,
        *,
        mapping_loader: Callable[
            [], tuple[
                Iterable[CurriculumAtomicMapping],
                Iterable[dict[str, Any]],
            ]
        ]
        | None = None,
    ):
        self.shchem_root = shchem_root.resolve()
        self._mapping_loader = mapping_loader
        self._snapshot_cache: _Snapshot | None = None
        self._snapshot_lock = Lock()

    def _directory_bytes(self) -> bytes:
        check_read_cancelled()
        cursor = self.shchem_root
        for part in DIRECTORY_RELATIVE.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise CurriculumWorkbenchError(
                    "curriculum_directory_location_invalid",
                    "教材目录位置包含符号链接。",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise CurriculumWorkbenchError(
                "curriculum_directory_unavailable", "教材目录不可用。"
            ) from exc
        if not resolved.is_relative_to(self.shchem_root) or not resolved.is_file():
            raise CurriculumWorkbenchError(
                "curriculum_directory_location_invalid", "教材目录越出资料库。"
            )
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise CurriculumWorkbenchError(
                "curriculum_directory_unavailable", "教材目录无法读取。"
            ) from exc
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise CurriculumWorkbenchError(
                "curriculum_directory_size_invalid", "教材目录大小无效。"
            )
        if (
            _HEX64.fullmatch(EXPECTED_DIRECTORY_FILE_SHA256) is None
            or _sha256(raw) != EXPECTED_DIRECTORY_FILE_SHA256
        ):
            raise CurriculumWorkbenchError(
                "curriculum_directory_drift", "教材目录与当前激活版本不一致。"
            )
        return raw

    @staticmethod
    def _validate_toc_evidence(value: Any) -> None:
        if not isinstance(value, list) or not value:
            raise CurriculumWorkbenchError(
                "curriculum_directory_structure_invalid", "目录图证据结构无效。"
            )
        for row in value:
            if (
                not isinstance(row, dict)
                or set(row) != _TOC_EVIDENCE_KEYS
                or type(row.get("bytes")) is not int
                or row["bytes"] < 1
                or row.get("role") != "textbook_toc_visual_evidence"
                or not isinstance(row.get("path"), str)
                or not row["path"]
                or not isinstance(row.get("sha256"), str)
                or _HEX64.fullmatch(row["sha256"]) is None
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_directory_structure_invalid",
                    "目录图证据结构无效。",
                )

    def _load_directory(
        self,
    ) -> tuple[
        tuple[_Volume, ...],
        dict[str, _Section],
        dict[str, str],
        dict[str, str],
    ]:
        value = _strict_json_object(self._directory_bytes())
        if set(value) != _TOP_LEVEL_KEYS:
            raise CurriculumWorkbenchError(
                "curriculum_directory_structure_invalid", "教材目录顶层字段漂移。"
            )
        for key, expected in (
            ("schema_version", EXPECTED_DIRECTORY_SCHEMA_VERSION),
            ("volume_count", EXPECTED_VOLUME_COUNT),
            ("chapter_count", EXPECTED_CHAPTER_COUNT),
            ("numbered_section_count", EXPECTED_SECTION_COUNT),
            ("section_id_policy", "only_reuse_existing_verified_ids_otherwise_null"),
            ("unit_policy", UNIT_STATUS_UNKNOWN),
        ):
            _same(
                value.get(key),
                expected,
                "curriculum_directory_structure_invalid",
                f"教材目录 {key} 漂移。",
            )
        _require_text(value.get("title"), "目录标题")

        volumes_raw = value.get("volumes")
        nodes_raw = value.get("nodes")
        if (
            not isinstance(volumes_raw, list)
            or len(volumes_raw) != EXPECTED_VOLUME_COUNT
            or not isinstance(nodes_raw, list)
            or len(nodes_raw) != EXPECTED_SECTION_COUNT
        ):
            raise CurriculumWorkbenchError(
                "curriculum_directory_count_invalid", "教材册或节数量不是 5/60。"
            )

        volume_meta: dict[str, dict[str, Any]] = {}
        for row in volumes_raw:
            if not isinstance(row, dict) or set(row) != _VOLUME_KEYS:
                raise CurriculumWorkbenchError(
                    "curriculum_directory_structure_invalid", "教材册字段漂移。"
                )
            volume_id = _require_safe_id(row.get("volume_id"), "教材册")
            if volume_id in volume_meta:
                raise CurriculumWorkbenchError(
                    "curriculum_directory_duplicate_node", "教材册标识重复。"
                )
            if (
                row.get("edition_or_printing") is not None
                or row.get("edition_status") != EDITION_STATUS_UNKNOWN
                or row.get("evidence_level") != "L1_LOCAL_TEXTBOOK"
                or row.get("source_root") != "workspace_root"
                or not isinstance(row.get("source_path"), str)
                or not row["source_path"]
                or not isinstance(row.get("source_sha256"), str)
                or _HEX64.fullmatch(row["source_sha256"]) is None
                or not isinstance(row.get("toc_pdf_pages"), list)
                or not row["toc_pdf_pages"]
                or any(
                    type(page) is not int or page < 1
                    for page in row["toc_pdf_pages"]
                )
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_directory_boundary_invalid",
                    "教材册版本或证据边界漂移。",
                )
            self._validate_toc_evidence(row.get("toc_visual_evidence"))
            _require_text(row.get("volume_title"), "教材册标题")
            _require_text(row.get("textbook_family"), "教材系列")
            _require_text(row.get("publisher"), "出版社")
            volume_meta[volume_id] = row
        if tuple(volume_meta) != EXPECTED_VOLUME_IDS:
            raise CurriculumWorkbenchError(
                "curriculum_directory_identity_invalid", "五册教材顺序或标识漂移。"
            )

        section_by_key: dict[str, _Section] = {}
        section_key_by_id: dict[str, str] = {}
        chapter_to_volume: dict[str, str] = {}
        chapter_titles: dict[str, str] = {}
        sections_by_chapter: dict[str, list[_Section]] = defaultdict(list)
        chapter_order_by_volume: dict[str, list[str]] = defaultdict(list)

        for row in nodes_raw:
            if not isinstance(row, dict) or set(row) != _NODE_KEYS:
                raise CurriculumWorkbenchError(
                    "curriculum_directory_structure_invalid", "教材节字段漂移。"
                )
            volume_id = _require_safe_id(row.get("volume_id"), "教材册")
            chapter_id = _require_safe_id(row.get("chapter_id"), "教材章")
            section_key = _require_safe_id(row.get("node_key"), "教材节")
            section_number = row.get("section_number")
            section_id = row.get("section_id")
            if (
                volume_id not in volume_meta
                or not isinstance(section_number, str)
                or _SECTION_NUMBER.fullmatch(section_number) is None
                or section_key != f"{chapter_id}:{section_number}"
                or (section_id is not None and _SAFE_ID.fullmatch(str(section_id)) is None)
                or row.get("unit_id") is not None
                or row.get("unit_title") is not None
                or row.get("unit_status") != UNIT_STATUS_UNKNOWN
                or row.get("status") != "toc_visual_verified_directory_node"
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_directory_boundary_invalid",
                    "教材节标识、单元或状态边界漂移。",
                )
            metadata = volume_meta[volume_id]
            if any(
                row.get(key) != metadata.get(key)
                for key in (
                    "volume_title",
                    "source_path",
                    "source_root",
                    "source_sha256",
                    "toc_pdf_pages",
                    "toc_visual_evidence",
                )
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_directory_source_mismatch",
                    "教材节与所属册的证据边界不一致。",
                )
            self._validate_toc_evidence(row.get("toc_visual_evidence"))
            _require_page_pair(row.get("content_pdf_pages"), "内容")
            _require_page_pair(row.get("printed_pages"), "印刷")
            volume_title = _require_text(row.get("volume_title"), "教材册标题")
            chapter_title = _require_text(row.get("chapter_title"), "章标题")
            section_title = _require_text(row.get("section_title"), "节标题")
            if section_key in section_by_key:
                raise CurriculumWorkbenchError(
                    "curriculum_directory_duplicate_node", "教材节节点重复。"
                )
            if section_id is not None:
                if section_id in section_key_by_id:
                    raise CurriculumWorkbenchError(
                        "curriculum_directory_duplicate_node", "教材节 ID 重复。"
                    )
                section_key_by_id[section_id] = section_key
            previous_volume = chapter_to_volume.setdefault(chapter_id, volume_id)
            previous_title = chapter_titles.setdefault(chapter_id, chapter_title)
            if previous_volume != volume_id or previous_title != chapter_title:
                raise CurriculumWorkbenchError(
                    "curriculum_directory_parent_mismatch", "教材章父链不一致。"
                )
            if chapter_id not in chapter_order_by_volume[volume_id]:
                chapter_order_by_volume[volume_id].append(chapter_id)
            section = _Section(
                section_key=section_key,
                section_id=section_id,
                section_number=section_number,
                section_title=section_title,
                volume_id=volume_id,
                volume_title=volume_title,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                unit_id=None,
                unit_title=None,
                unit_status=UNIT_STATUS_UNKNOWN,
            )
            section_by_key[section_key] = section
            sections_by_chapter[chapter_id].append(section)

        if (
            len(chapter_to_volume) != EXPECTED_CHAPTER_COUNT
            or len(section_by_key) != EXPECTED_SECTION_COUNT
            or len(section_key_by_id) != 3
        ):
            raise CurriculumWorkbenchError(
                "curriculum_directory_count_invalid",
                "教材目录不是精确的 19 章/60 节或 nullable 节 ID 边界漂移。",
            )

        volumes: list[_Volume] = []
        for volume_id in EXPECTED_VOLUME_IDS:
            meta = volume_meta[volume_id]
            chapters = tuple(
                _Chapter(
                    chapter_id=chapter_id,
                    chapter_title=chapter_titles[chapter_id],
                    volume_id=volume_id,
                    sections=tuple(sections_by_chapter[chapter_id]),
                )
                for chapter_id in chapter_order_by_volume[volume_id]
            )
            volumes.append(
                _Volume(
                    volume_id=volume_id,
                    volume_title=meta["volume_title"],
                    textbook_family=meta["textbook_family"],
                    publisher=meta["publisher"],
                    evidence_level=meta["evidence_level"],
                    edition_or_printing=None,
                    edition_status=EDITION_STATUS_UNKNOWN,
                    chapters=chapters,
                )
            )
        return (
            tuple(volumes),
            section_by_key,
            section_key_by_id,
            chapter_to_volume,
        )

    @staticmethod
    def _directory_lookup(
        section_by_key: Mapping[str, _Section],
    ) -> dict[tuple[str, str, str, str], _Section]:
        return {
            (
                section.volume_id,
                section.chapter_id,
                section.section_number,
                section.section_title,
            ): section
            for section in section_by_key.values()
        }

    @staticmethod
    def _normalize_mapping(
        *,
        atomic_id: Any,
        source_layer: str,
        source_batch: str,
        raw_mapping: Any,
        section_lookup: Mapping[tuple[str, str, str, str], _Section],
        chapter_to_volume: Mapping[str, str],
        volume_titles: Mapping[str, str],
        chapter_titles: Mapping[str, str],
    ) -> CurriculumAtomicMapping:
        atomic_id = _require_safe_id(atomic_id, "atomic")
        if not isinstance(raw_mapping, dict):
            raise CurriculumWorkbenchError(
                "curriculum_mapping_structure_invalid", "题级教材映射结构无效。"
            )
        raw_top_status = raw_mapping.get("mapping_status")
        normalized_top = TOP_MAPPING_STATUS.get(raw_top_status)
        raw_entries = raw_mapping.get("entries")
        if normalized_top is None or not isinstance(raw_entries, list) or not raw_entries:
            raise CurriculumWorkbenchError(
                "curriculum_mapping_structure_invalid", "题级教材映射状态无效。"
            )
        entries: list[CurriculumMappingEntry] = []
        blocked_count = 0
        mapped_count = 0
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise CurriculumWorkbenchError(
                    "curriculum_mapping_structure_invalid", "教材映射条目无效。"
                )
            raw_entry_status = (
                raw.get("mapping_status")
                if "mapping_status" in raw
                else raw.get("status")
            )
            if raw_entry_status == BLOCKED_ENTRY_STATUS:
                public_entry_status = "blocked"
                blocked_count += 1
            elif raw_entry_status in MAPPED_ENTRY_STATUSES:
                public_entry_status = "mapped"
                mapped_count += 1
            else:
                raise CurriculumWorkbenchError(
                    "curriculum_mapping_status_invalid", "教材映射条目状态无效。"
                )

            volume_id = _require_safe_id(raw.get("volume_id"), "映射教材册")
            chapter_id = _require_safe_id(raw.get("chapter_id"), "映射教材章")
            if (
                chapter_to_volume.get(chapter_id) != volume_id
                or raw.get("volume_title") != volume_titles.get(volume_id)
                or raw.get("chapter_title") != chapter_titles.get(chapter_id)
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_mapping_parent_mismatch", "教材映射册章父链不一致。"
                )
            if (
                raw.get("unit_id") is not None
                or raw.get("unit_title") is not None
                or raw.get("unit_status") != UNIT_STATUS_UNKNOWN
                or raw.get("edition_or_printing") not in (None,)
                or raw.get("edition_status") != EDITION_STATUS_UNKNOWN
                or raw.get("evidence_level") != "L1_LOCAL_TEXTBOOK"
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_mapping_boundary_invalid",
                    "教材映射版本或单元边界漂移。",
                )

            section_key: str | None
            section_id = raw.get("section_id")
            section_number = raw.get("section_number")
            section_title = raw.get("section_title")
            if public_entry_status == "blocked":
                if any(
                    value is not None
                    for value in (section_id, section_number, section_title)
                ):
                    raise CurriculumWorkbenchError(
                        "curriculum_mapping_blocked_precision_invalid",
                        "阻断映射不得伪造节级精度。",
                    )
                section_key = None
            else:
                if not isinstance(section_number, str) or not isinstance(
                    section_title, str
                ):
                    raise CurriculumWorkbenchError(
                        "curriculum_mapping_section_invalid",
                        "完整映射缺少显式节号或节名。",
                    )
                section = section_lookup.get(
                    (volume_id, chapter_id, section_number, section_title)
                )
                # A mapping may deliberately keep section_id null even when
                # the directory node itself has a reusable ID.  Join by its
                # explicit volume/chapter/number/title tuple, but never fill
                # the mapping's nullable field on its behalf.
                if section is None or (
                    section_id is not None and section.section_id != section_id
                ):
                    raise CurriculumWorkbenchError(
                        "curriculum_mapping_section_invalid",
                        "教材映射节节点不在激活目录中。",
                    )
                section_key = section.section_key

            knowledge_tag = raw.get("knowledge_tag")
            knowledge_role = raw.get("knowledge_role")
            relation = raw.get("relation")
            if (
                not isinstance(knowledge_tag, str)
                or re.fullmatch(r"K[0-9]{2}", knowledge_tag) is None
                or knowledge_role not in {"primary", "supporting"}
                or not isinstance(relation, str)
                or not relation
            ):
                raise CurriculumWorkbenchError(
                    "curriculum_mapping_metadata_invalid", "教材映射说明字段无效。"
                )
            entries.append(
                CurriculumMappingEntry(
                    knowledge_tag=knowledge_tag,
                    knowledge_role=knowledge_role,
                    relation=relation,
                    evidence_status=raw_entry_status,
                    mapping_status=public_entry_status,
                    evidence_level="L1_LOCAL_TEXTBOOK",
                    volume_id=volume_id,
                    volume_title=volume_titles[volume_id],
                    chapter_id=chapter_id,
                    chapter_title=chapter_titles[chapter_id],
                    section_key=section_key,
                    section_id=section_id,
                    section_number=section_number,
                    section_title=section_title,
                    unit_id=None,
                    unit_title=None,
                    unit_status=UNIT_STATUS_UNKNOWN,
                    edition_or_printing=None,
                    edition_status=EDITION_STATUS_UNKNOWN,
                )
            )
        if (
            mapped_count < 1
            or (normalized_top == "complete" and blocked_count != 0)
            or (normalized_top == "partial" and blocked_count < 1)
        ):
            raise CurriculumWorkbenchError(
                "curriculum_mapping_status_invalid",
                "题级 complete/partial 与条目状态不一致。",
            )
        return CurriculumAtomicMapping(
            atomic_id=atomic_id,
            source_layer=source_layer,
            source_batch=source_batch,
            mapping_status=normalized_top,
            entries=tuple(entries),
        )

    def _load_default_mappings(
        self,
        *,
        section_by_key: Mapping[str, _Section],
        chapter_to_volume: Mapping[str, str],
    ) -> tuple[tuple[CurriculumAtomicMapping, ...], tuple[dict[str, Any], ...]]:
        # Imports stay local so this independent core has no import-time cycle
        # with the existing Master aggregate.
        from .huangpu2025_theme4_direct_visual_scan import (
            HONGKOU2026_SECOND_MOCK_THEME4_CONFIG,
            QIBAO2025_OPENING_THEME4_CONFIG,
            Hongkou2026SecondMockTheme4DirectVisualScanReader,
            Huangpu2025Theme4DirectVisualScanReader,
            Qibao2025OpeningTheme4DirectVisualScanReader,
        )
        from .huangpu2025_theme4_direct_visual_scan import (
            PRODUCT_ID as HUANGPU_PRODUCT_ID,
        )
        from .master_wave1_workbench import MasterWave1WorkbenchReader
        from .supplemental_wechat_tagging_overlay import (
            PRODUCT_ID as SUPPLEMENTAL_PRODUCT_ID,
        )
        from .supplemental_wechat_tagging_overlay import (
            SupplementalWechatTaggingOverlayReader,
        )

        section_lookup = self._directory_lookup(section_by_key)
        volume_titles = {
            section.volume_id: section.volume_title
            for section in section_by_key.values()
        }
        chapter_titles = {
            section.chapter_id: section.chapter_title
            for section in section_by_key.values()
        }
        master_identity = MasterWave1WorkbenchReader(self.shchem_root)
        master_specs = (
            (
                "master_direct_huangpu_2025_theme4",
                "黄浦主题四",
                HUANGPU_PRODUCT_ID,
                11,
                Huangpu2025Theme4DirectVisualScanReader(
                    self.shchem_root, master_identity
                ),
            ),
            (
                "master_direct_qibao_2025_theme4",
                "七宝主题四",
                QIBAO2025_OPENING_THEME4_CONFIG.product_id,
                10,
                Qibao2025OpeningTheme4DirectVisualScanReader(
                    self.shchem_root, master_identity
                ),
            ),
            (
                "master_direct_hongkou_2026_theme4",
                "虹口主题四",
                HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.product_id,
                9,
                Hongkou2026SecondMockTheme4DirectVisualScanReader(
                    self.shchem_root, master_identity
                ),
            ),
        )
        mappings: list[CurriculumAtomicMapping] = []
        active_layers: list[dict[str, Any]] = []
        for batch_id, label_zh, product_id, expected_count, reader in master_specs:
            # Each product snapshot performs its own manifest, output, source,
            # Master-membership, and semantic checks before records are read.
            snapshot = reader._snapshot()
            if len(snapshot.records) != expected_count:
                raise CurriculumWorkbenchError(
                    "curriculum_active_mapping_count_invalid",
                    "Master direct 教材映射数量漂移。",
                )
            for record in snapshot.records:
                hierarchy = record.get("hierarchy")
                if not isinstance(hierarchy, dict):
                    raise CurriculumWorkbenchError(
                        "curriculum_mapping_structure_invalid",
                        "Master direct 层级结构无效。",
                    )
                mappings.append(
                    self._normalize_mapping(
                        atomic_id=hierarchy.get("atomic_part_id"),
                        source_layer="master_direct_active",
                        source_batch=batch_id,
                        raw_mapping=reader._textbook_projection(record),
                        section_lookup=section_lookup,
                        chapter_to_volume=chapter_to_volume,
                        volume_titles=volume_titles,
                        chapter_titles=chapter_titles,
                    )
                )
            active_layers.append(
                {
                    "layer_id": batch_id,
                    "label_zh": label_zh,
                    "source_layer": "master_direct_active",
                    "source_product": product_id,
                    "active_atomic_count": expected_count,
                }
            )

        supplemental_reader = SupplementalWechatTaggingOverlayReader(
            self.shchem_root
        )
        supplemental_snapshot = supplemental_reader._snapshot()
        supplemental_status = supplemental_reader.status()
        counts = supplemental_status.get("counts")
        registry = supplemental_status.get("registry")
        if (
            not isinstance(counts, dict)
            or not isinstance(registry, dict)
            or registry.get("product_id") != SUPPLEMENTAL_PRODUCT_ID
            or counts.get("atomic_parts") != 57
            or counts.get("base_shanghai_exam_atomic_parts") != 57
            or counts.get("base_external_handout_atomic_parts") != 29
            or counts.get("overlay_external_handout_atomic_parts") != 0
            or len(supplemental_snapshot.details_by_node_id) != 57
        ):
            raise CurriculumWorkbenchError(
                "curriculum_active_mapping_count_invalid",
                "Supplemental WeChat 教材映射范围漂移。",
            )
        for atomic_id, detail in supplemental_snapshot.details_by_node_id.items():
            mappings.append(
                self._normalize_mapping(
                    atomic_id=atomic_id,
                    source_layer="supplemental_wechat_active",
                    source_batch="supplemental_wechat_57",
                    raw_mapping=detail.get("textbook_directory_mapping"),
                    section_lookup=section_lookup,
                    chapter_to_volume=chapter_to_volume,
                    volume_titles=volume_titles,
                    chapter_titles=chapter_titles,
                )
            )
        active_layers.append(
            {
                "layer_id": "supplemental_wechat_57",
                "label_zh": "公众号上海卷题级映射",
                "source_layer": "supplemental_wechat_active",
                "source_product": SUPPLEMENTAL_PRODUCT_ID,
                "active_atomic_count": 57,
            }
        )

        datong: dict[str, Any] = {
            "layer_id": "datong_parent_chain_candidate",
            "label_zh": "大同父链修复候选",
            "included_in_active": False,
            "pending_source_atomic_count": None,
            "pending_effective_atomic_count": None,
            "verification_status": "暂未载入",
        }
        try:
            from .master_parent_chain_repair_overlay import (
                MasterParentChainRepairOverlayError,
                MasterParentChainRepairOverlayReader,
            )

            overlay = MasterParentChainRepairOverlayReader(self.shchem_root).overlay()
            overlay_counts = overlay.get("counts")
            if isinstance(overlay_counts, dict) and (
                overlay_counts.get("source_master_atomics") == 43
                and overlay_counts.get("effective_atomics") == 56
            ):
                datong.update(
                    {
                        "pending_source_atomic_count": 43,
                        "pending_effective_atomic_count": 56,
                        "verification_status": "只读候选已核对",
                    }
                )
        except (MasterParentChainRepairOverlayError, OSError):
            # Optional pending coverage never widens or blocks the canonical
            # active set.  Unknown remains explicit instead of being guessed.
            datong["verification_status"] = "候选暂不可读，未计入 active"

        excluded_layers = (
            datong,
            {
                "layer_id": "supplemental_external_handout",
                "label_zh": "外部教学讲义",
                "included_in_active": False,
                "pending_atomic_count": 29,
                "verification_status": "Supplemental 分层已核对",
            },
            {
                "layer_id": "fengxian_candidate",
                "label_zh": "奉贤候选",
                "included_in_active": False,
                "pending_atomic_count": None,
                "declared_scope_atomic_count": 10,
                "verification_status": "未载入，不计入分母",
            },
        )
        return tuple(mappings), tuple(active_layers), excluded_layers

    @staticmethod
    def _validate_active_closure(
        mappings: tuple[CurriculumAtomicMapping, ...],
        active_layers: tuple[dict[str, Any], ...],
    ) -> None:
        ids = [record.atomic_id for record in mappings]
        if len(ids) != len(set(ids)):
            raise CurriculumWorkbenchError(
                "curriculum_active_mapping_duplicate", "active atomic 标识重复。"
            )
        source_counts: dict[str, int] = defaultdict(int)
        batch_counts: dict[str, int] = defaultdict(int)
        complete = partial = blocked_atomics = blocked_entries = entries = 0
        for record in mappings:
            source_counts[record.source_layer] += 1
            batch_counts[record.source_batch] += 1
            complete += record.mapping_status == "complete"
            partial += record.mapping_status == "partial"
            record_blocked = False
            for entry in record.entries:
                entries += 1
                if entry.blocked:
                    blocked_entries += 1
                    record_blocked = True
            blocked_atomics += record_blocked
        expected_batches = {
            "master_direct_huangpu_2025_theme4": 11,
            "master_direct_qibao_2025_theme4": 10,
            "master_direct_hongkou_2026_theme4": 9,
            "supplemental_wechat_57": 57,
        }
        if (
            len(mappings) != ACTIVE_ATOMIC_COUNT
            or source_counts
            != {
                "master_direct_active": ACTIVE_MASTER_COUNT,
                "supplemental_wechat_active": ACTIVE_SUPPLEMENTAL_COUNT,
            }
            or dict(batch_counts) != expected_batches
            or complete != EXPECTED_COMPLETE_ATOMIC_COUNT
            or partial != EXPECTED_PARTIAL_ATOMIC_COUNT
            or blocked_atomics != EXPECTED_BLOCKED_ATOMIC_COUNT
            or blocked_entries != EXPECTED_BLOCKED_ENTRY_COUNT
            or entries != EXPECTED_MAPPING_ENTRY_COUNT
            or sum(item.get("active_atomic_count", 0) for item in active_layers)
            != ACTIVE_ATOMIC_COUNT
        ):
            raise CurriculumWorkbenchError(
                "curriculum_active_mapping_count_invalid",
                "active 教材映射闭包不是 Master 30 + Supplemental 57。",
            )

    @staticmethod
    def _build_stats(
        mappings: tuple[CurriculumAtomicMapping, ...]
    ) -> tuple[dict[tuple[str, str], _NodeStats], _NodeStats]:
        mutable: dict[tuple[str, str], _MutableNodeStats] = defaultdict(
            _MutableNodeStats.new
        )
        global_stats = _MutableNodeStats.new()

        for record in mappings:
            record_has_blocked = False
            global_stats.mapped_ids.add(record.atomic_id)
            if record.mapping_status == "complete":
                global_stats.complete_ids.add(record.atomic_id)
            else:
                global_stats.partial_ids.add(record.atomic_id)
            for entry in record.entries:
                keys = [
                    ("volume", entry.volume_id),
                    ("chapter", entry.chapter_id),
                ]
                if entry.section_key is not None:
                    keys.append(("section", entry.section_key))
                if entry.blocked:
                    record_has_blocked = True
                    global_stats.blocked_entry_count += 1
                    for key in keys:
                        mutable[key].blocked_ids.add(record.atomic_id)
                        mutable[key].blocked_entry_count += 1
                    continue
                global_stats.mapped_entry_count += 1
                for key in keys:
                    stats = mutable[key]
                    stats.mapped_ids.add(record.atomic_id)
                    if record.mapping_status == "complete":
                        stats.complete_ids.add(record.atomic_id)
                    else:
                        stats.partial_ids.add(record.atomic_id)
                    stats.mapped_entry_count += 1
            if record_has_blocked:
                global_stats.blocked_ids.add(record.atomic_id)
        return (
            {key: value.freeze() for key, value in mutable.items()},
            global_stats.freeze(),
        )

    def _build_snapshot(self) -> _Snapshot:
        (
            volumes,
            section_by_key,
            section_key_by_id,
            chapter_to_volume,
        ) = self._load_directory()
        if self._mapping_loader is None:
            mappings, active_layers, excluded_layers = self._load_default_mappings(
                section_by_key=section_by_key,
                chapter_to_volume=chapter_to_volume,
            )
        else:
            supplied_mappings, supplied_excluded = self._mapping_loader()
            mappings = tuple(supplied_mappings)
            excluded_layers = tuple(deepcopy(tuple(supplied_excluded)))
            batch_counts: dict[tuple[str, str], int] = defaultdict(int)
            for record in mappings:
                if not isinstance(record, CurriculumAtomicMapping):
                    raise CurriculumWorkbenchError(
                        "curriculum_mapping_structure_invalid",
                        "注入的教材映射必须使用冻结数据结构。",
                    )
                batch_counts[(record.source_layer, record.source_batch)] += 1
            active_layers = tuple(
                {
                    "layer_id": batch,
                    "label_zh": batch,
                    "source_layer": layer,
                    "source_product": batch,
                    "active_atomic_count": count,
                }
                for (layer, batch), count in batch_counts.items()
            )
        self._validate_active_closure(mappings, active_layers)
        stats, global_stats = self._build_stats(mappings)
        return _Snapshot(
            volumes=volumes,
            mappings=mappings,
            section_by_key=section_by_key,
            section_key_by_id=section_key_by_id,
            chapter_to_volume=chapter_to_volume,
            stats=stats,
            global_stats=global_stats,
            active_layers=tuple(deepcopy(active_layers)),
            excluded_layers=tuple(deepcopy(excluded_layers)),
        )

    def _snapshot(self) -> _Snapshot:
        check_read_cancelled()
        cached = self._snapshot_cache
        if cached is not None:
            return cached
        with self._snapshot_lock:
            check_read_cancelled()
            cached = self._snapshot_cache
            if cached is None:
                cached = self._build_snapshot()
                check_read_cancelled()
                self._snapshot_cache = cached
            return cached

    @staticmethod
    def _coverage(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "active_atomic_count": len(snapshot.mappings),
            "active_layers": deepcopy(list(snapshot.active_layers)),
            "excluded_pending_layers": deepcopy(list(snapshot.excluded_layers)),
            "canonical_active_formula_zh": "Master direct 30 + Supplemental WeChat 57",
        }

    @staticmethod
    def _integrity() -> dict[str, bool]:
        return {
            "activated_directory_verified_on_read": True,
            "strict_json_verified": True,
            "directory_identity_unique": True,
            "active_mapping_sources_verified_on_read": True,
            "explicit_directory_mapping_only": True,
            "knowledge_tag_inference_used": False,
            "blocked_section_precision_fabricated": False,
            "theme_parent_chain_projected": False,
            "fail_closed": True,
        }

    @staticmethod
    def _stats_for(snapshot: _Snapshot, level: str, node_id: str) -> _NodeStats:
        return snapshot.stats.get(
            (level, node_id),
            _NodeStats(frozenset(), frozenset(), frozenset(), frozenset(), 0, 0),
        )

    def catalog(self) -> dict[str, Any]:
        """Return a safe 5-volume/19-chapter/60-section tree."""

        snapshot = self._snapshot()
        volumes: list[dict[str, Any]] = []
        for volume in snapshot.volumes:
            chapters: list[dict[str, Any]] = []
            for chapter in volume.chapters:
                sections = [
                    {
                        "section_key": section.section_key,
                        "section_id": section.section_id,
                        "section_number": section.section_number,
                        "section_title": section.section_title,
                        "display_label_zh": (
                            f"{section.section_number} {section.section_title}"
                        ),
                        "unit_id": None,
                        "unit_title": None,
                        "unit_status": section.unit_status,
                        "mapping_counts": self._stats_for(
                            snapshot, "section", section.section_key
                        ).public(),
                    }
                    for section in chapter.sections
                ]
                chapters.append(
                    {
                        "chapter_id": chapter.chapter_id,
                        "chapter_title": chapter.chapter_title,
                        "display_label_zh": chapter.chapter_title,
                        "section_count": len(sections),
                        "mapping_counts": self._stats_for(
                            snapshot, "chapter", chapter.chapter_id
                        ).public(),
                        "sections": sections,
                    }
                )
            volumes.append(
                {
                    "volume_id": volume.volume_id,
                    "volume_title": volume.volume_title,
                    "display_label_zh": volume.volume_title,
                    "textbook_family": volume.textbook_family,
                    "publisher": volume.publisher,
                    "evidence_level": volume.evidence_level,
                    "edition_or_printing": None,
                    "edition_status": volume.edition_status,
                    "chapter_count": len(chapters),
                    "section_count": sum(
                        chapter["section_count"] for chapter in chapters
                    ),
                    "mapping_counts": self._stats_for(
                        snapshot, "volume", volume.volume_id
                    ).public(),
                    "chapters": chapters,
                }
            )
        response = {
            "schema_version": SCHEMA_VERSION,
            "data_snapshot_id": DATA_SNAPSHOT_ID,
            "scope": SCOPE,
            "title_zh": "教材章节工作台",
            "counts": {
                "volumes": len(volumes),
                "chapters": sum(len(volume["chapters"]) for volume in volumes),
                "sections": sum(volume["section_count"] for volume in volumes),
                "section_ids_known": len(snapshot.section_key_by_id),
                "section_ids_unknown": (
                    EXPECTED_SECTION_COUNT - len(snapshot.section_key_by_id)
                ),
                "active_atomic_mappings": len(snapshot.mappings),
                "mapping_entries": sum(
                    len(record.entries) for record in snapshot.mappings
                ),
                **snapshot.global_stats.public(),
            },
            "volumes": volumes,
            "coverage": self._coverage(snapshot),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(),
        }
        _assert_safe_public(response)
        return response

    def mapping_index(self) -> dict[str, Any]:
        """Return the explicit question-level mapping index as safe DTOs."""

        snapshot = self._snapshot()
        response = {
            "schema_version": SCHEMA_VERSION,
            "data_snapshot_id": DATA_SNAPSHOT_ID,
            "scope": SCOPE,
            "counts": {
                "active_atomic_mappings": len(snapshot.mappings),
                "mapping_entries": sum(
                    len(record.entries) for record in snapshot.mappings
                ),
                **snapshot.global_stats.public(),
            },
            "records": [record.public() for record in snapshot.mappings],
            "coverage": self._coverage(snapshot),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(),
        }
        _assert_safe_public(response)
        return response

    def _resolve_query_node(
        self,
        snapshot: _Snapshot,
        *,
        volume_id: str | None,
        chapter_id: str | None,
        section: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        resolved_section_key: str | None = None
        if volume_id is not None:
            _require_safe_id(volume_id, "volume_id")
            if volume_id not in EXPECTED_VOLUME_IDS:
                raise CurriculumWorkbenchError(
                    "curriculum_node_not_found", "教材册不存在。", 404
                )
        if chapter_id is not None:
            _require_safe_id(chapter_id, "chapter_id")
            parent = snapshot.chapter_to_volume.get(chapter_id)
            if parent is None:
                raise CurriculumWorkbenchError(
                    "curriculum_node_not_found", "教材章不存在。", 404
                )
            if volume_id is not None and parent != volume_id:
                raise CurriculumWorkbenchError(
                    "curriculum_query_conflict", "教材册与章不属于同一父链。", 400
                )
            volume_id = parent
        if section is not None:
            _require_safe_id(section, "section")
            resolved_section_key = (
                section
                if section in snapshot.section_by_key
                else snapshot.section_key_by_id.get(section)
            )
            if resolved_section_key is None:
                raise CurriculumWorkbenchError(
                    "curriculum_node_not_found", "教材节不存在。", 404
                )
            node = snapshot.section_by_key[resolved_section_key]
            if chapter_id is not None and node.chapter_id != chapter_id:
                raise CurriculumWorkbenchError(
                    "curriculum_query_conflict", "教材章与节不属于同一父链。", 400
                )
            if volume_id is not None and node.volume_id != volume_id:
                raise CurriculumWorkbenchError(
                    "curriculum_query_conflict", "教材册与节不属于同一父链。", 400
                )
            volume_id = node.volume_id
            chapter_id = node.chapter_id
        return volume_id, chapter_id, resolved_section_key, section

    @staticmethod
    def _entry_matches(
        entry: CurriculumMappingEntry,
        *,
        volume_id: str | None,
        chapter_id: str | None,
        section_key: str | None,
    ) -> bool:
        return (
            (volume_id is None or entry.volume_id == volume_id)
            and (chapter_id is None or entry.chapter_id == chapter_id)
            and (section_key is None or entry.section_key == section_key)
        )

    def search(
        self,
        *,
        volume_id: str | None = None,
        chapter_id: str | None = None,
        section: str | None = None,
        mapping_status: str | None = None,
    ) -> dict[str, Any]:
        """Search explicit mappings by volume/chapter/section/status.

        ``section`` accepts the always-present ``section_key`` or one of the
        three currently known non-null ``section_id`` values.  Omitting
        ``mapping_status`` returns safe mapped edges only.  Use ``blocked`` to
        inspect chapter-level pending edges; a section query can never match a
        blocked edge.
        """

        snapshot = self._snapshot()
        if mapping_status is not None and mapping_status not in PUBLIC_MAPPING_STATUSES:
            raise CurriculumWorkbenchError(
                "curriculum_mapping_status_invalid",
                "mapping_status 仅支持 complete、partial 或 blocked。",
                400,
            )
        (
            resolved_volume,
            resolved_chapter,
            resolved_section_key,
            supplied_section,
        ) = self._resolve_query_node(
            snapshot,
            volume_id=volume_id,
            chapter_id=chapter_id,
            section=section,
        )
        items: list[dict[str, Any]] = []
        group_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
        matched_entry_count = 0
        for record in snapshot.mappings:
            if mapping_status in {"complete", "partial"} and (
                record.mapping_status != mapping_status
            ):
                continue
            wanted_blocked = mapping_status == "blocked"
            matched_entries = [
                entry
                for entry in record.entries
                if entry.blocked == wanted_blocked
                and self._entry_matches(
                    entry,
                    volume_id=resolved_volume,
                    chapter_id=resolved_chapter,
                    section_key=resolved_section_key,
                )
            ]
            if not matched_entries:
                continue
            matched_entry_count += len(matched_entries)
            group_ids[(record.source_layer, record.source_batch)].append(
                record.atomic_id
            )
            items.append(
                {
                    "atomic_id": record.atomic_id,
                    "source_layer": record.source_layer,
                    "source_batch": record.source_batch,
                    "mapping_status": (
                        "blocked" if wanted_blocked else record.mapping_status
                    ),
                    "matched_entry_count": len(matched_entries),
                    "matched_volume_ids": sorted(
                        {entry.volume_id for entry in matched_entries}
                    ),
                    "matched_chapter_ids": sorted(
                        {entry.chapter_id for entry in matched_entries}
                    ),
                    "matched_section_keys": sorted(
                        {
                            entry.section_key
                            for entry in matched_entries
                            if entry.section_key is not None
                        }
                    ),
                }
            )
        groups = [
            {
                "group_kind": "mapping_source",
                "source_layer": layer,
                "source_batch": batch,
                "atomic_count": len(ids),
                "atomic_ids": list(ids),
            }
            for (layer, batch), ids in group_ids.items()
        ]
        response = {
            "schema_version": SCHEMA_VERSION,
            "data_snapshot_id": DATA_SNAPSHOT_ID,
            "scope": SCOPE,
            "query": {
                "volume_id": resolved_volume,
                "chapter_id": resolved_chapter,
                "section": supplied_section,
                "resolved_section_key": resolved_section_key,
                "mapping_status": mapping_status,
            },
            "counts": {
                "active_atomic_total": len(snapshot.mappings),
                "matched_atomic_count": len(items),
                "matched_entry_count": matched_entry_count,
                "source_group_count": len(groups),
            },
            "atomic_ids": [item["atomic_id"] for item in items],
            "groups": groups,
            "items": items,
            "authority": dict(AUTHORITY),
            "integrity": {
                **self._integrity(),
                "blocked_edges_require_explicit_filter": True,
                "complete_theme_chain_returned": False,
                "theme_join_required_downstream": True,
            },
        }
        _assert_safe_public(response)
        return response

    def atomic_ids_for(
        self,
        level: str,
        node_id: str,
        *,
        mapping_status: str | None = None,
    ) -> frozenset[str]:
        """Return an immutable atomic-ID set for one curriculum node."""

        if level == "volume":
            result = self.search(
                volume_id=node_id, mapping_status=mapping_status
            )
        elif level == "chapter":
            result = self.search(
                chapter_id=node_id, mapping_status=mapping_status
            )
        elif level == "section":
            result = self.search(section=node_id, mapping_status=mapping_status)
        else:
            raise CurriculumWorkbenchError(
                "curriculum_query_level_invalid",
                "level 仅支持 volume、chapter 或 section。",
                400,
            )
        return frozenset(result["atomic_ids"])


__all__ = [
    "ACTIVE_ATOMIC_COUNT",
    "ACTIVE_MASTER_COUNT",
    "ACTIVE_SUPPLEMENTAL_COUNT",
    "AUTHORITY",
    "DATA_SNAPSHOT_ID",
    "DIRECTORY_RELATIVE",
    "EDITION_STATUS_UNKNOWN",
    "EXPECTED_CHAPTER_COUNT",
    "EXPECTED_DIRECTORY_FILE_SHA256",
    "EXPECTED_SECTION_COUNT",
    "EXPECTED_VOLUME_COUNT",
    "SCHEMA_VERSION",
    "SCOPE",
    "UNIT_STATUS_UNKNOWN",
    "CurriculumAtomicMapping",
    "CurriculumMappingEntry",
    "CurriculumWorkbenchError",
    "CurriculumWorkbenchReader",
]
