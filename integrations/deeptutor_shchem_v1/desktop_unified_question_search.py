"""Read-only five-source search, with original reader/selection identities.

Only source-bound catalogue projections and formal ThemeCards are retained.
The adapter never opens question bodies, images, answers, or selection drafts.
Formal hits are complete themes; visual hits are printed questions whose
existing selection compiler preserves their complete parent theme.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path, PurePosixPath
from threading import RLock
from time import perf_counter

from .desktop_explorer_index import (
    PersonalCatalogSession,
    SearchCancelled,
    check_cancelled,
)
from .desktop_personal_visual_questions import matches_personal_visual_filters
from .desktop_word_question_filters import (
    FILTER_GROUPS,
    _bound_attributes,
    _chosen,
    _directory,
    chapter_filter_id,
    compile_question_matcher,
    section_filter_id,
)
from .reader_cancellation import ReadCancelled, read_cancel_scope

LANES = ("master", "wave1", "supplemental", "word_native", "visual_native")
FORMAL_LANES = LANES[:3]
PERSONAL_LANES = LANES[3:]
COMMON_AXES = {"source", "knowledge", "knowledge_mode", "grade", "exam", "teaching_use"}
FORMAL_AXES = {"K", "A", "C", "R", "RP", "D", "item_type", "answer_status",
               "source_tier", "year", "region"}
UNITS = {**dict.fromkeys(FORMAL_LANES, "theme"),
         "word_native": "word_question_segment", "visual_native": "visual_printed_question"}
_BODY_FIELDS = {"question_blocks", "context_blocks", "answer_blocks", "assets",
                "search_provider", "search_projection", "question_text", "shared_text",
                "answer_text", "question_html", "answer_html", "shared_html"}
_LABELS = {"master": "正式主题主库", "wave1": "扩展主题库", "supplemental": "补充主题库",
           "word_native": "Word原题", "visual_native": "图片印刷题"}


class UnifiedQuestionSearchError(ValueError):
    def __init__(self, message="检索条件或分页已失效，请重新检索。"):
        self.message_zh = message
        self.code = "unified_question_search_invalid"
        super().__init__(message)


class _UnsupportedFilter(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _selection(value, axes):
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - axes:
        raise UnifiedQuestionSearchError("筛选字段无法识别。")
    result = {}
    for key, raw in value.items():
        if key == "knowledge_mode":
            if raw not in ("any", "all"):
                raise UnifiedQuestionSearchError("知识点匹配方式无法识别。")
            result[key] = raw
            continue
        chosen = _chosen(raw)
        if chosen is None:
            raise UnifiedQuestionSearchError("筛选值无法识别。")
        if chosen:
            result[key] = sorted(chosen)
    return result


def _lanes(value):
    if value is None:
        return LANES
    if not isinstance(value, (list, tuple, set, frozenset)) or any(l not in LANES for l in value):
        raise UnifiedQuestionSearchError("资料来源无法识别。")
    return tuple(l for l in LANES if l in value)


def _curriculum(value):
    if value is None or value == {}:
        return None
    fields = {"volume_id", "chapter_id", "section", "mapping_status"}
    if not isinstance(value, Mapping) or set(value) - fields or any(
        not isinstance(v, str) or not v.strip() for v in value.values()
    ):
        raise UnifiedQuestionSearchError("教材筛选无法识别。")
    result = dict(value)
    if ("chapter_id" in result and "volume_id" not in result) or (
        "section" in result and not {"volume_id", "chapter_id"}.issubset(result)
    ):
        raise UnifiedQuestionSearchError("教材章、节须同时指定所属册、章。")
    return result


def _card_projection(card):
    return asdict(card) if is_dataclass(card) else {
        k: getattr(card, k, None) for k in ("key", "scope", "source_identity_sha256",
            "data_snapshot_id", "title_zh", "preview_zh", "atomic_total", "atomic_matched")
    }


def _formal_marker(result):
    cards = list(result.cards)
    snapshots = sorted({getattr(card, "data_snapshot_id", "") for card in cards})
    return {"snapshot_kind": "source_snapshot" if snapshots and all(snapshots) else "result_projection",
            "data_snapshot_ids": snapshots,
            "projection_sha256": _digest({"total": result.total_themes,
                "cards": [_card_projection(c) for c in cards],
                "facets": getattr(result, "facets", {}),
                "has_more": result.has_more, "next_cursor": getattr(result, "next_cursor", None)})}


def _stat(path):
    try:
        value = path.stat()
        return [value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino,
                path.is_symlink()]
    except FileNotFoundError:
        return None


def _file_sha(path, cancelled):
    """Hash source bytes in bounded chunks, without decoding Word or pixels."""
    before = _stat(path)
    if before is None:
        return None
    if path.is_symlink() or not path.is_file():
        raise UnifiedQuestionSearchError("来源位置无法核对。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            check_cancelled(cancelled)
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    if _stat(path) != before:
        raise SearchCancelled("核对期间来源发生变化，请重新检索。")
    return digest.hexdigest()


def _input_json(path, cancelled, stamps=None):
    """Discover declared inputs from a small manifest, without loading readers."""
    check_cancelled(cancelled)
    before = _stat(path)
    if stamps is not None:
        previous = stamps.setdefault(path, before)
        if previous != before:
            raise SearchCancelled("核对期间来源清单发生变化，请重新检索。")
    if before is None:
        return {}
    if path.is_symlink() or not path.is_file() or before[0] > 8 * 1024 * 1024:
        raise UnifiedQuestionSearchError("来源清单无法核对。")
    value = json.loads(path.read_bytes())
    check_cancelled(cancelled)
    if _stat(path) != before:
        raise SearchCancelled("核对期间来源清单发生变化，请重新检索。")
    if not isinstance(value, Mapping):
        raise UnifiedQuestionSearchError("来源清单无法核对。")
    return value


def _bound_path(root, relative):
    if not isinstance(relative, str):
        raise UnifiedQuestionSearchError("来源绑定位置无法核对。")
    value = PurePosixPath(relative)
    if not value.parts or value.is_absolute() or ".." in value.parts or "\\" in relative or ":" in relative:
        raise UnifiedQuestionSearchError("来源绑定位置无法核对。")
    return root.joinpath(*value.parts)


def _authority_hashes(files, lane, cancelled, progress, discovered=None):
    ordered = tuple(sorted(files, key=str))
    before = tuple((str(path), _stat(path)) for path in ordered)
    if discovered and any(_stat(path) != stamp for path, stamp in discovered.items()):
        raise SearchCancelled("核对期间来源清单发生变化，请重新检索。")
    hashes = []
    for i, path in enumerate(ordered):
        check_cancelled(cancelled)
        if progress and i % 8 == 0:
            progress({"lane": lane, "message": f"正在核对{_LABELS[lane]}来源及标签 {i + 1}/{len(ordered)}"})
        hashes.append([str(path), _file_sha(path, cancelled)])
    after = tuple((str(path), _stat(path)) for path in ordered)
    if after != before:
        raise SearchCancelled("核对期间来源发生变化，请重新检索。")
    return hashes, ordered, _digest(before)


def _formal_source_metadata(facade, lane, cancelled, progress, curriculum=None):
    """Hash only the selected reader graph's declared authority inputs.

    Manifests are the file-discovery authority; no catalogue/snapshot builder
    is invoked here. Raw source bytes are streamed, never decoded as Office
    documents or images. Root additions are caught through their manifest.
    """
    themes = getattr(facade, "_themes", None)
    supplemental = getattr(facade, "_supplemental", None)
    reader = supplemental if lane == "supplemental" else themes
    root = getattr(reader, "shchem_root", None)
    if root is None:
        return {"kind": "injected_catalog_snapshot"}, ()
    root = Path(root)
    roots, files, discovered = set(), set(), {}
    def add_reader(value):
        product = getattr(value, "product_root", None)
        if product is not None:
            roots.add(Path(product))
    if lane == "supplemental":
        from .supplemental_visual_scan import REGISTRY_RELATIVE, REGISTRY_SCHEMA_RELATIVE
        registry_path = root / REGISTRY_RELATIVE
        files.update((registry_path, root / REGISTRY_SCHEMA_RELATIVE))
        registry = _input_json(registry_path, cancelled, discovered)
        for product in registry.get("products", []):
            check_cancelled(cancelled)
            if not isinstance(product, Mapping):
                raise UnifiedQuestionSearchError("补充来源清单无法核对。")
            roots.add(_bound_path(root, product.get("product_relative")))
    else:
        scans = getattr(themes, "wave_scans", None)
        for value in getattr(scans, "_batch_readers", ()):
            add_reader(value)
        if lane == "wave1":
            from .candidate_review import _ALLOWED_FILES
            review_root = getattr(getattr(themes, "wave_review", None), "batch_root", None)
            if review_root is not None:
                files.update(Path(review_root) / name for name in _ALLOWED_FILES)
        else:
            from .master_visual_scan_alias import PRODUCT_RELATIVE as ALIAS_RELATIVE
            from .master_wave1_workbench import MASTER_ROOT
            roots.add(root / ALIAS_RELATIVE)
            roots.add(root / MASTER_ROOT)
            add_reader(getattr(themes, "master_workbench", None))
            add_reader(getattr(themes, "parent_chain_overlay", None))
            direct = getattr(themes, "direct_scans", None)
            for registration in getattr(direct, "_reader_registrations", ()):
                add_reader(getattr(direct, registration.attribute, None))
    # Textbook directory/taxonomy participate in every formal filter and
    # label projection, without calling the cross-source curriculum builder.
    from .curriculum_workbench import DIRECTORY_RELATIVE
    files.update((root / DIRECTORY_RELATIVE, root / "kb/knowledge_taxonomy.json"))
    if curriculum:
        from .supplemental_wechat_tagging_overlay import REGISTRY_RELATIVE as MAPPING_REGISTRY, REGISTRY_SCHEMA_RELATIVE as MAPPING_SCHEMA
        registry_path = root / MAPPING_REGISTRY
        files.update((registry_path, root / MAPPING_SCHEMA))
        registry = _input_json(registry_path, cancelled, discovered)
        for product in registry.get("products", []):
            if isinstance(product, Mapping):
                roots.add(_bound_path(root, product.get("product_relative")))
    def bound_file(relative, default_root):
        # Archived adapters use shchem/workspace-relative paths, while native
        # immutable product outputs are product-relative. Preserve the same
        # explicit path namespace used by their existing authority readers.
        if isinstance(relative, str) and relative.startswith("kb/"):
            default_root = root
        elif isinstance(relative, str) and relative.startswith(("sh-chem-db/", "staging/", "课本/", "knowledge/")):
            default_root = root.parent
        return _bound_path(default_root, relative)
    pending = []
    for product_root in sorted(roots, key=str):
        files.update(product_root / name for name in ("validation_report.json", "mutation_test_report.json"))
        pending.extend(product_root / name for name in (
            "manifest.json", "candidate_manifest.json", "source_manifest.json", "evidence_manifest.json"))
    visited = set()
    while pending:
        check_cancelled(cancelled)
        manifest_path = pending.pop()
        if manifest_path in visited:
            continue
        visited.add(manifest_path)
        files.add(manifest_path)
        product_root = manifest_path.parent
        value = _input_json(manifest_path, cancelled, discovered)
        for value in (value, value.get("upstream_theme_evidence", {})):
            if not isinstance(value, Mapping):
                continue
            for field in ("output_bindings", "output_files", "outputs", "files", "source_bindings",
                          "sources", "source_assets", "protected_inputs", "textbook_sources",
                          "scripts", "local_crops", "crops", "bindings"):
                bindings = value.get(field, [])
                if isinstance(bindings, Mapping):
                    bindings = [dict(binding, path=relative) for relative, binding in bindings.items()
                                if isinstance(binding, Mapping)]
                if not isinstance(bindings, list):
                    continue
                source_field = field in ("source_bindings", "sources", "source_assets", "protected_inputs", "textbook_sources", "local_crops", "crops", "bindings")
                binding_root = root if source_field else product_root
                if manifest_path.name == "source_manifest.json" and field == "files":
                    binding_root = root.parent
                for binding in bindings:
                    if isinstance(binding, Mapping):
                        for name in ("path", "asset", "source_asset"):
                            if name in binding:
                                path = bound_file(binding[name], binding_root)
                                files.add(path)
                                if path.name.endswith("manifest.json"):
                                    pending.append(path)
            for field in ("candidate_file", "evidence_map_file", "shared_schema", "taxonomy_asset", "controlled_vocabulary_asset", "pilot_asset", "crop_manifest_asset"):
                if field in value:
                    files.add(bound_file(value[field], root))
    hashes, ordered, stamp = _authority_hashes(files, lane, cancelled, progress, discovered)
    return {"kind": "formal_source_sha256_worker", "sha256": _digest(hashes),
            "stat_sha256": stamp}, ordered


def _source_metadata(facade, lane, cancelled, progress, curriculum=None):
    """Worker-only fingerprint of the selected source's authority inputs.

    Returning the input files also enables a cheap epoch/stat late-result gate.
    The stat gate detects ordinary changes; only this worker SHA check attests
    to source bytes. No cache, CAS object, source, or state file is written.
    """
    port = getattr(facade, "unified_question_source_snapshot", None)
    if callable(port):
        check_cancelled(cancelled)
        value = port(lane, cancelled=cancelled, progress=progress)
        check_cancelled(cancelled)
        if not isinstance(value, Mapping):
            raise TypeError("来源版本无法核对。")
        return {"kind": "source_metadata_port", "sha256": _digest(value)}, ()
    if lane in FORMAL_LANES:
        return _formal_source_metadata(facade, lane, cancelled, progress, curriculum)
    paths, state = getattr(facade, "paths", None), getattr(facade, "state_store", None)
    if paths is None or state is None:
        # Narrow test/injected facades have authority only in their catalogue.
        return {"kind": "injected_catalog_snapshot"}, ()
    root = Path(paths.state_root) / "visual-import-v2"
    workspace = Path(paths.workspace_root)
    state_path = Path(state.path) if hasattr(state, "path") else None
    state_before = _stat(state_path) if state_path is not None else None
    snapshot = state.snapshot()
    state_after = _stat(state_path) if state_path is not None else None
    if state_before != state_after:
        raise SearchCancelled("读取期间个人来源目录发生变化，请重新检索。")
    projection = _personal_state_projection(snapshot, lane)
    descriptors = projection["descriptors"]
    files, source_shas, batch_files = set(), set(), {}
    for descriptor in descriptors:
        check_cancelled(cancelled)
        batch = descriptor.get("batch_id")
        if not isinstance(batch, str) or not re.fullmatch(r"DESKTOPBATCH-[a-f0-9]{32}", batch):
            raise UnifiedQuestionSearchError("导入来源批次无法核对。")
        manifest_path = root / "batches" / (batch + ".json")
        files.add(manifest_path)
        batch_files[batch] = [(manifest_path, None)]
        for source in descriptor.get("sources", []):
            if not isinstance(source, Mapping):
                raise UnifiedQuestionSearchError("导入来源目录无法核对。")
            relative, digest = source.get("archive_relative_path"), source.get("source_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or (
                not isinstance(relative, str) or not re.fullmatch(
                    r"sources/" + digest + r"\.(?:docx|pdf|png|jpg|webp)", relative)):
                raise UnifiedQuestionSearchError("原来源归档位置无法核对。")
            files.add(root / relative)
            batch_files[batch].append((root / relative, digest))
            if str(source.get("filename", "")).casefold().endswith(".docx"):
                source_shas.add(digest)
        if lane == "visual_native":
            cas_root = root / "candidates" / batch
            files.add(cas_root / "head.json")
            # These are compact JSON authority objects, not rendered images.
            # Hashing detects tampering that leaves the CAS head unchanged.
            if (cas_root / "objects").is_dir():
                files.update((cas_root / "objects").glob("*/*.json"))
    db_names = ("word-question-attributes.sqlite3",) if lane == "word_native" else (
        "personal-visual-question-attributes.sqlite3", "personal-visual-crops.sqlite3")
    for name in db_names:
        files.add(Path(paths.state_root) / name)
        files.add(Path(paths.state_root) / (name + "-wal"))
    files.update(workspace / name for name in (
        "sh-chem-db/kb/knowledge_taxonomy.json",
        "sh-chem-db/kb/classification/supplemental_wechat_textbook_tagging_v1_2026-08-27/textbook_directory_nodes.json",
        "knowledge/lectures/source-quality-notes.json"))
    hashes, ordered, stamp = _authority_hashes(files, lane, cancelled, progress)
    basis = {**projection, "files": hashes}
    actual = dict(hashes)
    invalid_batches = [batch for batch, inputs in batch_files.items()
        if any(actual[str(path)] is None or (expected and actual[str(path)] != expected)
               for path, expected in inputs)]
    result = {"kind": "source_sha256_worker", "sha256": _digest(basis),
            "stat_sha256": stamp,
            "invalid_batches": sorted(invalid_batches),
            "expected_word_source_shas": sorted(source_shas) if lane == "word_native" else [],
            "expected_visual_batches": len(descriptors) if lane == "visual_native" else 0,
            "state_projection_sha256": _digest(projection)}
    if state_path is not None:
        # The state file also contains baskets/tasks/window settings. Only
        # source descriptors and Word ranges are source authority, so its
        # broad stat token must never be part of the version fingerprint.
        result["_state_check"] = {"path": str(state_path), "stat": state_after,
                                  "projection_sha256": result["state_projection_sha256"]}
    return result, ordered


def _personal_state_projection(snapshot, lane):
    drafts = snapshot.get("drafts", {})
    if not isinstance(drafts, Mapping):
        raise UnifiedQuestionSearchError("个人来源目录无法核对。")
    descriptors = [d for key, d in sorted(drafts.items()) if isinstance(d, Mapping)
        and d.get("kind") == "desktop_visual_import_v2" and (
            any(isinstance(s, Mapping) and str(s.get("filename", "")).casefold().endswith(".docx")
                for s in d.get("sources", [])) if lane == "word_native" else d.get("visual_status") == "completed")]
    return {"descriptors": descriptors, **({"ranges": drafts.get("native-word-question-ranges-v1")}
                                           if lane == "word_native" else {})}


class _FormalStream:
    PAGE_SIZE = 50

    def __init__(self, owner, lane, variants, curriculum, cancelled):
        self.owner, self.lane = owner, lane
        self.variants, self.curriculum = variants, curriculum
        self.rows, self.seen = [], set()
        self.markers = []
        self.facets = {}
        self.warnings = []
        self.cursor, self.has_more = None, False
        self._visited_cursors = set()
        first = self._read(variants[0], None, cancelled)
        self.markers.append(_formal_marker(first))
        self.facets = getattr(first, "facets", {})
        pending = getattr(first, "pending_atomic_parts", 0)
        if pending:
            self.warnings.append(f"另有{pending}个小问待补主题归属，未计入完整主题。")
        self._append(first.cards)
        self.total = first.total_themes
        self.has_more, self.cursor = first.has_more, getattr(first, "next_cursor", None)
        if len(variants) > 1:
            # Intersections operate on complete theme identities, including tail
            # pages. ThemeCard metadata is the only material read here.
            self._complete(cancelled)
            surviving = set(self.seen)
            for variant in variants[1:]:
                result = self._read(variant, None, cancelled)
                self.markers.append(_formal_marker(result))
                keys = {c.key for c in result.cards}
                visited = set()
                while result.has_more:
                    check_cancelled(cancelled)
                    cursor = getattr(result, "next_cursor", None)
                    if not cursor or cursor in visited:
                        raise UnifiedQuestionSearchError("来源分页没有继续前进。")
                    visited.add(cursor)
                    result = self._read(variant, cursor, cancelled)
                    keys.update(c.key for c in result.cards)
                surviving.intersection_update(keys)
            self.rows = [c for c in self.rows if c.key in surviving]
            self.seen = surviving
            self.total = len(self.rows)
        self.snapshot = {"snapshot_kind": self.markers[0]["snapshot_kind"],
                         "variants": self.markers}

    def _read(self, variant, cursor, cancelled):
        check_cancelled(cancelled)
        with read_cancel_scope(cancelled, check_boundaries=False):
            result = self.owner.facade.search_themes(scope=self.lane, query=variant[0],
                limit=self.PAGE_SIZE, filters=variant[1], curriculum=self.curriculum, cursor=cursor)
        check_cancelled(cancelled)
        return result

    def _append(self, cards):
        for card in cards:
            if getattr(card, "scope", self.lane) != self.lane:
                raise UnifiedQuestionSearchError("来源返回的主题身份不一致。")
            if card.key not in self.seen:
                self.rows.append(card)
                self.seen.add(card.key)

    def _next(self, cancelled):
        previous = self.cursor
        if not previous or previous in self._visited_cursors:
            raise UnifiedQuestionSearchError("来源分页没有继续前进。")
        self._visited_cursors.add(previous)
        result = self._read(self.variants[0], previous, cancelled)
        expected = self.markers[0]["data_snapshot_ids"]
        observed = _formal_marker(result)["data_snapshot_ids"]
        if expected and observed and observed != expected:
            raise UnifiedQuestionSearchError("来源分页版本已改变，请重新检索。")
        self._append(result.cards)
        self.has_more, self.cursor = result.has_more, getattr(result, "next_cursor", None)
        if self.has_more and (not self.cursor or self.cursor == previous):
            raise UnifiedQuestionSearchError("来源分页没有继续前进。")
        if not self.has_more:
            self.total = len(self.rows)

    def _complete(self, cancelled):
        while self.has_more:
            self._next(cancelled)

    def row(self, position, cancelled):
        while position >= len(self.rows) and self.has_more:
            self._next(cancelled)
        return self.rows[position] if position < len(self.rows) else None

    def validate(self, cancelled):
        for variant, marker in zip(self.variants, self.markers):
            if _formal_marker(self._read(variant, None, cancelled)) != marker:
                raise UnifiedQuestionSearchError()


class _PersonalStream:
    def __init__(self, rows, facets, warnings, snapshot, incomplete=False):
        self.rows, self.facets, self.warnings, self.snapshot = rows, facets, warnings, snapshot
        self.total = len(rows)
        self.incomplete = incomplete

    def row(self, position, cancelled):
        check_cancelled(cancelled)
        return self.rows[position] if position < len(self.rows) else None


class UnifiedQuestionSearch:
    """One facade's disposable sessions; no independent question authority."""
    MAX_SEARCHES = 4

    def __init__(self, facade):
        self.facade = facade
        self._lock = RLock()
        self._control_lock = RLock()
        self._generations = dict.fromkeys(LANES, 0)
        self._personal = {}
        self._source_checks = {}
        self._state_checks = {}
        self._searches = OrderedDict()
        self._secret = uuid.uuid4().bytes

    def _epochs(self, lane):
        domains = ("word",) if lane == "word_native" else (
            ("word", "registry") if lane == "visual_native" else ("registry",))
        port = getattr(self.facade, "desktop_cache_epoch", None)
        return {domain: port(domain) if callable(port) else 0 for domain in domains}

    def _basis(self, lanes, *, cancelled=lambda: False, progress=None, curriculum=None):
        result = {}
        for lane in lanes:
            check_cancelled(cancelled)
            with self._control_lock:
                generation = self._generations[lane]
            value = {"epochs": self._epochs(lane), "generation": generation}
            started = perf_counter()
            try:
                with read_cancel_scope(cancelled, check_boundaries=False):
                    source, files = _source_metadata(self.facade, lane, cancelled, progress, curriculum)
            except (SearchCancelled, ReadCancelled):
                raise
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                source, files = {"kind": "source_metadata_unavailable"}, ()
            stamps = _digest(tuple((str(p), _stat(p)) for p in files))
            if source.get("stat_sha256", stamps) != stamps:
                raise SearchCancelled("核对期间来源发生变化，请重新检索。")
            value.update(source_snapshot=source, source_stat_sha256=stamps)
            state_check = source.pop("_state_check", None)
            with self._control_lock:
                self._source_checks[lane] = files
                if state_check is None:
                    self._state_checks.pop(lane, None)
                else:
                    self._state_checks[lane] = state_check
            if progress:
                progress({"lane": lane, "message": f"{_LABELS[lane]}来源版本已核对",
                          "source_verify_ms": round((perf_counter() - started) * 1000, 2)})
            check_cancelled(cancelled)
            result[lane] = value
        return result

    def is_current(self, version_vector):
        """Cheap epoch/stat gate; no source SHA/body reads on the UI thread.

        This detects ordinary disk changes. Full source-SHA attestation occurs
        in search workers and page workers. A changed personal-state stat is
        compared through bounded, lock-free metadata (maximum 8 MiB), because
        unrelated basket/task writes do not alter source authority.
        """
        if not isinstance(version_vector, Mapping) or any(l not in LANES for l in version_vector):
            return False
        # Search holds _lock during worker I/O. A GUI late-result gate must
        # never wait for that lock or trigger a metadata/SHA loader.
        with self._control_lock:
            generations = self._generations.copy()
            checks = self._source_checks.copy()
            state_checks = self._state_checks.copy()
        states = {}
        for lane, value in version_vector.items():
            if not isinstance(value, Mapping) or value.get("epochs") != self._epochs(lane) or value.get("generation") != generations[lane]:
                return False
            files = checks.get(lane, ())
            try:
                if value.get("source_stat_sha256") != _digest(tuple((str(p), _stat(p)) for p in files)):
                    return False
            except OSError:
                return False
            state_check = state_checks.get(lane)
            if state_check:
                source = value.get("source_snapshot")
                expected = source.get("state_projection_sha256") if isinstance(source, Mapping) else None
                if expected != state_check["projection_sha256"]:
                    return False
                path = Path(state_check["path"])
                try:
                    if _stat(path) != state_check["stat"]:
                        if path not in states:
                            states[path] = _input_json(path, lambda: False)
                        if _digest(_personal_state_projection(states[path], lane)) != expected:
                            return False
                except (OSError, ValueError, TypeError, SearchCancelled):
                    return False
        with self._control_lock:
            return all(value.get("generation") == self._generations[lane]
                       for lane, value in version_vector.items())

    def invalidate(self, lanes=None):
        selected = _lanes(lanes)
        # Increment first: a worker already holding a session will fail its
        # final version gate, and cannot publish or authorize an old cursor.
        with self._control_lock:
            for lane in selected:
                self._generations[lane] += 1
                self._personal.pop(lane, None)
                self._source_checks.pop(lane, None)
                self._state_checks.pop(lane, None)
            for token, state in list(self._searches.items()):
                if set(state["lanes"]).intersection(selected):
                    self._searches.pop(token, None)

    def _encode(self, value):
        raw = _json(value).encode("utf-8")
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return "unified-v1:" + base64.urlsafe_b64encode(signature + raw).decode("ascii")

    def _decode(self, cursor):
        try:
            if not isinstance(cursor, str) or not cursor.startswith("unified-v1:") or len(cursor) > 8192:
                raise ValueError()
            raw = base64.b64decode(cursor[11:], altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.new(self._secret, payload, hashlib.sha256).digest()):
                raise ValueError()
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise TypeError()
            return value
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            raise UnifiedQuestionSearchError() from None

    def _request(self, lanes, query, filters, lane_filters, curriculum, limit):
        if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise UnifiedQuestionSearchError("检索文字或每页条数无法识别。")
        selected = _lanes(lanes)
        common = _selection(filters, COMMON_AXES)
        if common.get("source"):
            if any(l not in LANES for l in common["source"]):
                raise UnifiedQuestionSearchError("统一来源筛选无法识别。")
            selected = tuple(l for l in selected if l in common["source"])
        if lane_filters is None:
            lane_filters = {}
        if not isinstance(lane_filters, Mapping) or set(lane_filters) - set(LANES):
            raise UnifiedQuestionSearchError("来源专属筛选无法识别。")
        local = {l: _selection(v, FORMAL_AXES | {"knowledge", "knowledge_mode"}
                 if l in FORMAL_LANES else set(FILTER_GROUPS) | {"knowledge_mode", "teaching_use"})
                 for l, v in lane_filters.items()}
        return {"lanes": selected, "query": _normalize(query), "filters": common,
                "lane_filters": local, "curriculum": _curriculum(curriculum), "limit": limit}

    def _formal_variants(self, common, local, terms):
        self._check_knowledge_support("master", (common, local))
        if any(common.get(axis) for axis in ("grade", "exam", "teaching_use")):
            raise _UnsupportedFilter("本来源尚无当前年级、原考试类型或教学用途筛选。")
        native = {k: v for k, v in local.items() if k in FORMAL_AXES and k != "K"}
        groups = []
        for selection, field in ((common, "knowledge"), (local, "K"), (local, "knowledge")):
            values = selection.get(field, [])
            if values:
                mode = selection.get("knowledge_mode", "any")
                groups.extend([[v] for v in values] if mode == "all" else [values])
        variants = []
        for term in terms or [""]:
            for group in groups or [None]:
                values = {**native, **({"K": group} if group else {})}
                variant = (term, values)
                if variant not in variants:
                    variants.append(variant)
        return variants

    @staticmethod
    def _check_knowledge_support(lane, selections):
        if lane == "visual_native":
            return
        for selection in selections:
            for field in ("knowledge", "K"):
                if any(value != "unknown" and not re.fullmatch(r"K(?:0[1-9]|1[0-9])", value)
                       for value in selection.get(field, [])):
                    raise _UnsupportedFilter("本来源尚无该自定义知识标签的等价筛选。")

    def _personal_session(self, lane, basis, progress, cancelled):
        old = self._personal.get(lane)
        if old and old["basis"] != basis:
            self._personal.pop(lane, None)
            old = None
        if old is None:
            session = PersonalCatalogSession(lane)
            if progress:
                progress({"message": f"正在读取{_LABELS[lane]}轻量目录", "lane": lane})
            def loader():
                check_cancelled(cancelled)
                with read_cancel_scope(cancelled, check_boundaries=False):
                    if lane == "word_native":
                        port = getattr(self.facade, "word_question_listing_progress", None)
                        result = port(progress, cancelled) if progress and callable(port) else self.facade.word_question_listing()
                    else:
                        result = self.facade.personal_visual_questions()
                check_cancelled(cancelled)
                return result
            try:
                index, _ = session.get(loader, cancelled=cancelled)
                check_cancelled(cancelled)
                if self._basis([lane], cancelled=cancelled)[lane] != basis:
                    raise SearchCancelled("目录缓存代次已改变，请重新检索。")
                # Hash small identities/attributes, never body or pixel payloads.
                catalog = session.catalog
                projection = {"revision": catalog.get("revision"), "sources": catalog.get("sources", []),
                    "attribute_catalog": catalog.get("attribute_catalog"),
                    "items": [{k: v for k, v in row.items() if k not in _BODY_FIELDS and k != "images"}
                              for row in catalog["items"]]}
                old = {"basis": basis, "session": session, "index": index,
                       "snapshot": {"snapshot_kind": "source_bound_catalog_projection",
                                    "projection_sha256": _digest(projection),
                                    "catalog_revision": catalog.get("revision")}}
                self._personal[lane] = old
            except (SearchCancelled, ReadCancelled):
                self._personal.pop(lane, None)
                raise
        return old

    def _personal_curriculum(self, selector, catalog):
        if not selector:
            return {}
        if selector.get("mapping_status"):
            raise _UnsupportedFilter("个人原题的教材状态与正式库状态尚无等价筛选。")
        nodes = _directory(catalog.get("attribute_catalog"))
        if not nodes:
            raise _UnsupportedFilter("本来源的教材目录无法核对。")
        volume, chapter, section = (selector.get(k) for k in ("volume_id", "chapter_id", "section"))
        result = {"book": [volume]} if volume else {}
        if chapter:
            result["chapter"] = [chapter_filter_id(volume, chapter)]
        if section:
            candidates = [n for n in nodes.values() if section in (
                n["node_key"], n.get("section_key"), n.get("section_number"))]
            matching = [n for n in candidates if n["volume_id"] == volume and n["chapter_id"] == chapter]
            if candidates and not matching:
                raise UnifiedQuestionSearchError("教材节与指定册、章不一致。")
            result["section"] = [section_filter_id(volume, chapter, n["node_key"]) for n in matching]
            if not matching:
                result["section"] = [section_filter_id(volume, chapter, section)]
        return result

    def _personal_stream(self, lane, common, local, selector, terms, basis, progress, cancelled):
        self._check_knowledge_support(lane, (common, local))
        if selector and selector.get("mapping_status"):
            raise _UnsupportedFilter("个人原题的教材状态与正式库状态尚无等价筛选。")
        prepared = self._personal_session(lane, basis, progress, cancelled)
        index, catalog = prepared["index"], prepared["session"].catalog
        curriculum = self._personal_curriculum(selector, catalog)
        shared = {k: v for k, v in common.items() if k != "source"}
        selections = [s for s in (shared, local, curriculum) if s]
        matchers = [compile_question_matcher(s, catalog.get("attribute_catalog")) for s in selections]
        term_matchers = [compile_question_matcher({"query": t}, catalog.get("attribute_catalog")) for t in terms]
        rows = []
        for i, (row, _, _, text, excerpt) in enumerate(index._rows):
            if i % 64 == 0:
                check_cancelled(cancelled)
            if row.get("batch_id") in basis.get("source_snapshot", {}).get("invalid_batches", []):
                continue
            if lane == "word_native":
                keep = all(m(row) for m in matchers)
                if keep:
                    uses = set(_bound_attributes(row).get("teaching_use_tags", []))
                    keep = all(not s.get("teaching_use") or uses.intersection(s["teaching_use"]) for s in selections)
                if keep:
                    for matcher in term_matchers:
                        if not matcher(row):
                            keep = False
                            break
                        check_cancelled(cancelled)
            else:
                keep = all(matches_personal_visual_filters(row, s) for s in selections)
                keep = keep and all(term in _normalize(text) for term in terms)
            if keep:
                rows.append((row, excerpt))
        check_cancelled(cancelled)
        authority = basis.get("source_snapshot", {})
        expected = set(authority.get("expected_word_source_shas", []))
        observed = {source.get("source_sha256", source.get("source_id")) for source in catalog.get("sources", [])}
        incomplete = (authority.get("kind") == "source_metadata_unavailable"
            or bool(authority.get("invalid_batches"))
            or bool(catalog.get("unavailable_batches"))
            or (lane == "word_native" and not expected.issubset(observed)))
        return _PersonalStream(rows, index._facets, list(catalog.get("warnings", [])), prepared["snapshot"], incomplete)

    def _entry(self, lane, raw, request):
        if lane in FORMAL_LANES:
            card = raw
            locator = {"scope": lane, "key": card.key,
                       "source_identity_sha256": getattr(card, "source_identity_sha256", ""),
                       "data_snapshot_id": getattr(card, "data_snapshot_id", "")}
            title, subtitle = card.title_zh, card.paper_title_zh + " · " + card.source_zh
            excerpt = getattr(card, "preview_zh", "") or card.shared_context_zh
            unit = f"完整主题 · {getattr(card, 'display_atomic_units', 0) or card.atomic_total}个作答单元"
            key, payload = card.key, card
        else:
            row, excerpt = raw
            key = row["key"]
            payload = deepcopy({k: v for k, v in row.items() if k not in _BODY_FIELDS})
            locator = {k: row[k] for k in ("key", "revision", "batch_id", "source_id", "archive_source_id",
                "source_sha256", "source_revision", "candidate_sha256", "theme_key") if k in row}
            title, subtitle = row.get("title", "未命名题目"), row.get("source_name", "来源待核对")
            unit = "Word原题段 · 保留公共材料" if lane == "word_native" else "图片印刷题 · 入篮保留完整主题"
        return {"entry_id": lane + ":" + key, "key": key, "lane": lane, "title": title,
                "subtitle": subtitle, "excerpt": str(excerpt)[:480], "payload": payload,
                "source_locator": locator, "unit_kind": UNITS[lane], "unit": unit,
                "match_evidence": {"terms": request["query"].split(),
                    "filters": deepcopy(request["filters"]),
                    "lane_filters": deepcopy(request["lane_filters"].get(lane, {})),
                    "curriculum": deepcopy(request["curriculum"]),
                    "match_unit": "complete_theme" if lane in FORMAL_LANES else UNITS[lane],
                    "answer_searched": False}}

    def search(self, *, lanes=None, query="", filters=None, lane_filters=None,
               curriculum=None, cursor=None, limit=8, progress=None, cancelled=None):
        request = self._request(lanes, query, filters, lane_filters, curriculum, limit)
        fingerprint = _digest(request)
        decoded = self._decode(cursor) if cursor is not None else None
        cancelled = cancelled or (lambda: False)
        check_cancelled(cancelled)
        while not self._lock.acquire(timeout=.05):
            check_cancelled(cancelled)
        try:
            check_cancelled(cancelled)
            selected = request["lanes"]
            basis = self._basis(selected, cancelled=cancelled, progress=progress, curriculum=request["curriculum"])
            if decoded is not None:
                with self._control_lock:
                    state = self._searches.get(decoded.get("search"))
                    if not state or decoded.get("request") != fingerprint or state["basis"] != basis:
                        raise UnifiedQuestionSearchError()
                    self._searches.move_to_end(state["token"])
                positions, turn = decoded["positions"], decoded["turn"]
                for lane, stream in state["streams"].items():
                    if lane in FORMAL_LANES:
                        stream.validate(cancelled)
            else:
                state = {"token": uuid.uuid4().hex, "request": request, "lanes": selected,
                         "basis": basis, "streams": {}, "status": {}, "warnings": []}
                for lane in selected:
                    check_cancelled(cancelled)
                    if basis[lane]["source_snapshot"].get("kind") == "source_metadata_unavailable":
                        # An unavailable authority is not a permission to use
                        # an old bound-postings provider or a cached card.
                        self._personal.pop(lane, None)
                        state["status"][lane] = {"status": "unavailable", "total": None,
                            "unit_kind": UNITS[lane], "message": f"{_LABELS[lane]}来源版本暂时无法核对，请重新读取来源。"}
                        continue
                    try:
                        if lane in FORMAL_LANES:
                            variants = self._formal_variants(request["filters"], request["lane_filters"].get(lane, {}), request["query"].split())
                            stream = _FormalStream(self, lane, variants, request["curriculum"], cancelled)
                        else:
                            stream = self._personal_stream(lane, request["filters"], request["lane_filters"].get(lane, {}), request["curriculum"], request["query"].split(), basis[lane], progress, cancelled)
                        state["streams"][lane] = stream
                        source_incomplete = getattr(stream, "incomplete", False)
                        state["status"][lane] = {"status": "unavailable" if source_incomplete else "ok",
                                                "total": None if source_incomplete else stream.total,
                                                "available_total": stream.total,
                                                "unit_kind": UNITS[lane], "snapshot": stream.snapshot}
                        state["warnings"].extend(stream.warnings)
                    except _UnsupportedFilter as error:
                        state["status"][lane] = {"status": "unsupported_filter", "total": None,
                            "unit_kind": UNITS[lane], "message": str(error)}
                    except (SearchCancelled, ReadCancelled, UnifiedQuestionSearchError):
                        raise
                    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                        self._personal.pop(lane, None)
                        state["status"][lane] = {"status": "unavailable", "total": None,
                            "unit_kind": UNITS[lane], "message": f"{_LABELS[lane]}暂时无法核对，请重新读取来源。"}
                positions, turn = {lane: 0 for lane in selected}, 0
            entries = []
            misses = 0
            while selected and len(entries) < limit and misses < len(selected):
                check_cancelled(cancelled)
                lane = selected[turn]
                turn = (turn + 1) % len(selected)
                stream = state["streams"].get(lane)
                row = stream.row(positions[lane], cancelled) if stream else None
                if row is None:
                    misses += 1
                    continue
                misses = 0
                positions[lane] += 1
                entries.append(self._entry(lane, row, request))
            check_cancelled(cancelled)
            if self._basis(selected, cancelled=cancelled, curriculum=request["curriculum"]) != basis:
                raise SearchCancelled("目录缓存代次已改变，请重新检索。")
            status = state["status"]
            for lane, stream in state["streams"].items():
                status[lane]["available_total"] = stream.total
                if status[lane]["status"] == "ok":
                    status[lane]["total"] = stream.total
            known = dict.fromkeys(set(UNITS.values()), 0)
            counts = known.copy()
            for lane, source in status.items():
                kind = UNITS[lane]
                if source["total"] is None:
                    counts[kind] = None
                known[kind] += source.get("available_total", source["total"]) or 0
            for kind in counts:
                if counts[kind] is not None:
                    counts[kind] = known[kind]
            total_available = sum(known.values())
            incomplete = any(s["status"] != "ok" for s in status.values())
            has_more = any(positions[lane] < stream.total for lane, stream in state["streams"].items())
            next_cursor = self._encode({"search": state["token"], "request": fingerprint,
                "positions": positions, "turn": turn}) if has_more else None
            facets = self._facets(state)
            vector = {lane: {**basis[lane], "snapshot": status[lane].get("snapshot"),
                            "status": status[lane]["status"]} for lane in selected}
            result = {"entries": entries, "total": None if incomplete else total_available,
                    "total_available": total_available, "counts_by_unit": counts,
                    "known_counts_by_unit": known, "source_status": deepcopy(status),
                    "partial": any(s["status"] == "unavailable" for s in status.values()),
                    "incomplete": incomplete, "has_more": has_more, "next_cursor": next_cursor,
                    "facets": facets, "warnings": list(dict.fromkeys(state["warnings"])),
                    "version_vector": vector, "count_unit": "原来源检索单位（完整主题 / Word题段 / 图片印刷题）"}
            # Invalidation can run while worker I/O owns _lock. Publish the
            # session only under its independent generation lock, after a
            # final cheap gate; a late cancelled worker cannot restore it.
            with self._control_lock:
                check_cancelled(cancelled)
                if not self.is_current(basis):
                    raise SearchCancelled("目录缓存代次已改变，请重新检索。")
                self._searches[state["token"]] = state
                self._searches.move_to_end(state["token"])
                while len(self._searches) > self.MAX_SEARCHES:
                    self._searches.popitem(last=False)
            return result
        except (SearchCancelled, ReadCancelled):
            # A cancelled build never survives as an apparently complete query.
            for lane in request["lanes"]:
                self._personal.pop(lane, None)
            raise
        finally:
            self._lock.release()

    def _facets(self, state):
        groups = {}
        for lane, stream in state["streams"].items():
            for key, facet in stream.facets.items():
                public = "knowledge" if key == "K" else key
                group = groups.setdefault(public, {"label_zh": facet.get("label_zh", public),
                                                   "values": {}, "supported_sources": []})
                group["supported_sources"].append(lane)
                for option in facet.get("values", []):
                    value = option.get("value")
                    if not isinstance(value, str):
                        continue
                    existing = group["values"].setdefault(value, {
                        "value": value, "label_zh": option.get("label_zh", value),
                        "atomic_count": None, "sources": []})
                    existing["sources"].append(lane)
        for group in groups.values():
            group["values"] = list(group["values"].values())
        groups["source"] = {"label_zh": "资料来源", "values": [
            {"value": lane, "label_zh": _LABELS[lane], "unit_kind": UNITS[lane],
             "status": status["status"], "total": status["total"], "atomic_count": None}
            for lane, status in state["status"].items()]}
        return groups
