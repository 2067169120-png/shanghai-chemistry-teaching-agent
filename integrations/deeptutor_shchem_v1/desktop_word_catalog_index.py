"""Disposable Word boundary indexes and explicitly historical observations.

Source bytes and the native preview remain the authority. This store contains
block references, never a second copy of source text or image metadata. Saved
attribute observations are audit evidence only and are never filter inputs.
"""

from __future__ import annotations

import hashlib
import base64
import json
import marshal
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import FunctionType
from uuid import uuid4

from . import desktop_word_question_index as boundaries
from .reader_cancellation import check_read_cancelled
from .word_native_text import NATIVE_WORD_TEXT_REVISION
from .word_static_eq import REVISION as STATIC_EQ_REVISION

CATALOG_INDEX_REVISION = "native-word-lightweight-catalog-index-v1"
OBSERVATION_REVISION = "native-word-question-observation-v1"
MAX_RECORD_BYTES = 16 * 1024 * 1024
_GROUPS = ("question_blocks", "answer_blocks", "context_blocks")
_UNSET = object()
_LIGHT_FIELDS = frozenset({"excerpt", "source_asset_count", "source_has_unpreviewable_assets", "search_projection"})


def _char_token(character):
    return hashlib.sha256(character.encode()).hexdigest()[:16]


def search_projection(text):
    """An ordered character posting index, with no source text stored in it."""
    positions, tokens = {}, {}
    for offset, character in enumerate(text.casefold()):
        if character not in tokens:
            tokens[character] = _char_token(character)
        positions.setdefault(tokens[character], []).append(offset)
    encoded = {}
    for token, offsets in positions.items():
        raw, previous = bytearray(), -1
        for offset in offsets:
            delta, previous = offset - previous, offset
            while delta >= 128:
                raw.append((delta & 127) | 128)
                delta >>= 7
            raw.append(delta)
        encoded[token] = base64.b64encode(raw).decode("ascii")
    return {"revision": "word-search-character-postings-v1", "positions": encoded}


def _positions(raw):
    values, offset, delta, shift = [], -1, 0, 0
    for byte in base64.b64decode(raw, validate=True):
        delta |= (byte & 127) << shift
        if byte & 128:
            shift += 7
            if shift > 35:
                raise ValueError("Invalid search posting")
        else:
            if delta <= 0:
                raise ValueError("Invalid search posting")
            offset += delta
            values.append(offset)
            delta, shift = 0, 0
    if shift:
        raise ValueError("Incomplete search posting")
    return values


def projection_matches(projection, query):
    if not query:
        return True
    if projection.get("revision") != "word-search-character-postings-v1":
        return False
    positions = projection["positions"]
    decoded, candidates = {}, None
    for offset, character in enumerate(query.casefold()):
        token = _char_token(character)
        if token not in decoded:
            decoded[token] = _positions(positions.get(token, ""))
        found = {position - offset for position in decoded[token] if position >= offset}
        candidates = found if candidates is None else candidates.intersection(found)
        if not candidates:
            return False
    return bool(candidates)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _rule_fingerprint():
    # The recognizer's revision is a serialization contract. Hash its actual
    # functions, patterns and constants too, including packaged applications
    # where a readable .py source is absent. Marshal is only used to hash local
    # code; no stored or external executable bytes are ever deserialized.
    material = []
    for name, value in sorted(vars(boundaries).items()):
        if isinstance(value, FunctionType) and value.__module__ == boundaries.__name__:
            material.append((name, hashlib.sha256(marshal.dumps(value.__code__)).hexdigest()))
        elif isinstance(value, re.Pattern):
            material.append((name, value.pattern, value.flags))
        elif name.isupper() and isinstance(value, (str, int, bool)):
            material.append((name, value))
        elif name.isupper() and isinstance(value, (set, frozenset)):
            material.append((name, sorted(value)))
    return _digest(material)


QUESTION_RULE_SHA256 = _rule_fingerprint()


def source_binding(preview):
    if "_binding" in preview:
        return preview["_binding"]
    return {
        "catalog_index_revision": CATALOG_INDEX_REVISION,
        "question_index_revision": boundaries.QUESTION_INDEX_REVISION,
        "question_rule_sha256": QUESTION_RULE_SHA256,
        "source_sha256": preview["source_sha256"],
        "source_name": preview["source_name"],
        "source_revision": preview["revision"],
        "extraction_revision": preview["extraction_revision"],
        "source_block_count": len(preview["blocks"]),
        "native_word_text_revision": NATIVE_WORD_TEXT_REVISION,
        "static_eq_revision": STATIC_EQ_REVISION,
    }


def compact_questions(preview, items):
    """Replace question block copies with ordered source/split references."""
    blocks = {block["index"]: block for block in preview["blocks"]}
    by_block = {}
    for asset in preview.get("assets", []):
        by_block.setdefault(asset["block_index"], []).append(asset)
    compact = []
    for item in items:
        check_read_cancelled()
        row = deepcopy({key: value for key, value in item.items() if key not in _GROUPS})
        for group in _GROUPS:
            references = []
            for block in item[group]:
                original = blocks[block["index"]]
                expected = {**original, "assets": by_block.get(block["index"], [])}
                if block.get("display_only_split"):
                    start, end = block["text_range"]
                    expected.update(text=original["text"][start:end],
                                    source_text=original["text"], text_range=[start, end],
                                    display_only_split=True)
                    reference = {"index": block["index"], "text_range": [start, end]}
                else:
                    reference = block["index"]
                if block != expected:
                    raise ValueError("Question block does not match the native source")
                references.append(reference)
            row[group] = references
        body_text = "\n".join(block.get("text", "").strip() for group in ("question_blocks", "context_blocks") for block in item[group])
        row["search_projection"] = search_projection(body_text)
        row["excerpt"] = "\n".join(block.get("text", "") for group in ("context_blocks", "question_blocks") for block in item[group])[:480]
        assets = [asset for group in _GROUPS for block in item[group] for asset in block.get("assets", [])]
        row["source_asset_count"] = len(assets)
        row["source_has_unpreviewable_assets"] = any(asset.get("preview_supported") is not True for asset in assets)
        compact.append(row)
    return compact


def expand_questions(preview, compact, *, validate=True):
    """Materialize against this verified preview; source trees stay shared/read-only."""
    if not isinstance(compact, list) or len(compact) > 100_000:
        raise ValueError("Invalid lightweight question index")
    blocks = {block["index"]: block for block in preview["blocks"]}
    by_block = {}
    for asset in preview.get("assets", []):
        by_block.setdefault(asset["block_index"], []).append(asset)
    source_blocks = {index: {**block, "assets": by_block.get(index, [])}
                     for index, block in blocks.items()}
    result, seen = [], set()
    for stored in compact:
        check_read_cancelled()
        if not isinstance(stored, dict):
            raise ValueError("Invalid lightweight question row")
        row = deepcopy({key: value for key, value in stored.items() if key not in _GROUPS and key not in _LIGHT_FIELDS})
        for group in _GROUPS:
            references = stored.get(group)
            if not isinstance(references, list):
                raise ValueError("Invalid lightweight block references")
            selected = []
            previous = 0
            for reference in references:
                index = reference.get("index") if isinstance(reference, dict) else reference
                if type(index) is not int or index <= previous or index not in source_blocks:
                    raise ValueError("Invalid source block reference")
                previous = index
                block = source_blocks[index]
                if isinstance(reference, dict):
                    text_range = reference.get("text_range")
                    if (set(reference) != {"index", "text_range"}
                            or not isinstance(text_range, list) or len(text_range) != 2
                            or any(type(value) is not int for value in text_range)
                            or not 0 <= text_range[0] < text_range[1] <= len(block["text"])):
                        raise ValueError("Invalid split source block reference")
                    start, end = text_range
                    block = {**block, "text": block["text"][start:end],
                             "source_text": block["text"], "text_range": [start, end],
                             "display_only_split": True}
                selected.append(block)
            row[group] = selected
        if validate:
            if (row.get("key") in seen
                    or row.get("key") != boundaries._key(preview["source_sha256"], row.get("origin_block_start"))
                    or row.get("source_revision") != preview["revision"]
                    or row.get("source_sha256") != preview["source_sha256"]
                    or row.get("extraction_revision") != preview["extraction_revision"]
                    or row.get("index_revision") != boundaries.QUESTION_INDEX_REVISION
                    or row.get("revision") != _digest({key: value for key, value in row.items() if key != "revision"})):
                raise ValueError("Lightweight question binding changed")
            seen.add(row["key"])
        result.append(row)
    return result


class WordCatalogIndex:
    """Read-only misses never create state. SQLite commits provide source CAS."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "catalog.sqlite3"
        self.recovered_databases = []

    def _safe(self):
        return not (self.root.is_symlink() or self.root.is_junction()
                    or self.path.is_symlink() or self.path.is_junction())

    @contextmanager
    def _connect(self, *, readonly):
        if not self._safe():
            raise OSError("Index path cannot be a link")
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro" if readonly else self.path,
                                     uri=readonly, timeout=10)
        try:
            if readonly:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("CREATE TABLE IF NOT EXISTS sources (identity TEXT PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS observations (identity TEXT PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS searches (identity TEXT PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS ranges (identity TEXT PRIMARY KEY, revision TEXT NOT NULL, payload TEXT NOT NULL)")
                for table in ("sources", "observations", "searches", "ranges"):
                    if [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] != ["identity", "revision", "payload"]:
                        raise sqlite3.DatabaseError("derived catalog schema invalid")
                connection.commit()
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _identity(binding):
        return _digest([binding["catalog_index_revision"], binding["question_rule_sha256"],
                        binding["source_sha256"], binding["source_name"]])

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        revision, payload = row
        if not isinstance(payload, str) or len(payload.encode()) > MAX_RECORD_BYTES:
            return None
        record = json.loads(payload)
        return record if isinstance(record, dict) and revision == _digest(record) else None

    def load(self, source_bytes, source_name, preview):
        if (hashlib.sha256(source_bytes).hexdigest() != preview.get("source_sha256")
                or source_name != preview.get("source_name") or not self.path.exists()):
            return None
        record = self.load_source(source_bytes, source_name)
        try:
            if record is None or record.get("binding") != source_binding(preview):
                return None
            expand_questions(preview, record["items"])
            return record["items"]
        except (ValueError, TypeError, KeyError, RecursionError):
            return None

    def load_source(self, source_bytes, source_name):
        """Read boundaries without loading native text/image previews."""
        digest = hashlib.sha256(source_bytes).hexdigest()
        identity = self._identity({"catalog_index_revision": CATALOG_INDEX_REVISION,
            "question_rule_sha256": QUESTION_RULE_SHA256, "source_sha256": digest,
            "source_name": source_name})
        if not self.path.exists():
            return None
        try:
            with self._connect(readonly=True) as connection:
                record = self._decode(connection.execute("SELECT revision,payload FROM sources WHERE identity=?", (identity,)).fetchone())
            if not record:
                return None
            binding = record.get("binding", {})
            if (binding.get("catalog_index_revision") != CATALOG_INDEX_REVISION
                    or binding.get("question_rule_sha256") != QUESTION_RULE_SHA256
                    or binding.get("question_index_revision") != boundaries.QUESTION_INDEX_REVISION
                    or binding.get("source_sha256") != digest or binding.get("source_name") != source_name
                    or binding.get("native_word_text_revision") != NATIVE_WORD_TEXT_REVISION
                    or binding.get("static_eq_revision") != STATIC_EQ_REVISION
                    or binding.get("extraction_revision") not in {NATIVE_WORD_TEXT_REVISION, NATIVE_WORD_TEXT_REVISION + "+" + STATIC_EQ_REVISION}
                    or not isinstance(record.get("items"), list)):
                return None
            for row in record["items"]:
                if (not isinstance(row, dict) or row.get("source_sha256") != digest
                        or row.get("source_revision") != binding["source_revision"]
                        or row.get("key") != boundaries._key(digest, row.get("origin_block_start"))
                        or any(not isinstance(row.get(group), list) for group in _GROUPS)
                        or any(not (type(ref) is int or isinstance(ref, dict) and set(ref) == {"index", "text_range"}) for group in _GROUPS for ref in row[group])):
                    return None
            return record
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def _write(self, table, records, expected_revisions, extras=()):
        with self._connect(readonly=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for identity, record in records:
                    check_read_cancelled()
                    old = connection.execute(f"SELECT revision FROM {table} WHERE identity=?", (identity,)).fetchone()
                    if expected_revisions is not None and (old[0] if old else None) != expected_revisions[identity]:
                        connection.rollback()
                        return False
                    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    if len(payload.encode()) > MAX_RECORD_BYTES:
                        connection.rollback()
                        return False
                    connection.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)",
                                       (identity, _digest(record), payload))
                for extra_table, identity, record in extras:
                    check_read_cancelled()
                    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    if len(payload.encode()) > MAX_RECORD_BYTES:
                        connection.rollback()
                        return False
                    connection.execute(f"INSERT OR REPLACE INTO {extra_table} VALUES (?,?,?)", (identity, _digest(record), payload))
                check_read_cancelled()
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def _save(self, table, records, expected_revisions, extras=()):
        try:
            return self._write(table, records, expected_revisions, extras)
        except sqlite3.DatabaseError as exc:
            # Quarantine only our derived index. Archived DOCX and other state
            # are never removed, rewritten or opened by this recovery path.
            corrupt = getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB} or str(exc) == "derived catalog schema invalid"
            if not corrupt or expected_revisions is not None or not self._safe():
                return False
            try:
                check_read_cancelled()
                quarantine = self.root / ("catalog-unreadable-" + uuid4().hex + ".sqlite3")
                os.replace(self.path, quarantine)
                self.recovered_databases.append(str(quarantine))
                return self._write(table, records, None, extras)
            except (OSError, sqlite3.DatabaseError):
                return False
        except OSError:
            return False

    def save(self, source_bytes, source_name, preview, items, *, expected_revision=_UNSET):
        if hashlib.sha256(source_bytes).hexdigest() != preview["source_sha256"] or source_name != preview["source_name"]:
            raise ValueError("Index source bytes changed")
        expand_questions(preview, items)
        record, extras = self._records(source_binding(preview), items)
        identity = self._identity(record["binding"])
        expected = None if expected_revision is _UNSET else {identity: expected_revision}
        return self._save("sources", [(identity, record)], expected, extras)

    def _records(self, binding, items):
        rows, searches = [], []
        for row in items:
            rows.append({key: value for key, value in row.items() if key != "search_projection"})
            if "search_projection" in row:
                searches.append(("searches", self.search_identity(binding, row["key"], row["revision"]),
                    {"binding": binding, "key": row["key"], "question_revision": row["revision"], "projection": row["search_projection"]}))
        return {"binding": binding, "items": rows}, searches

    @staticmethod
    def search_identity(binding, key, revision):
        return _digest([WordCatalogIndex._identity(binding), key, revision])

    def save_ranges(self, preview, ranges_revision, items):
        record, extras = self._records(source_binding(preview), items)
        record["ranges_revision"] = ranges_revision
        identity = _digest([self._identity(record["binding"]), ranges_revision])
        return self._save("ranges", [(identity, record)], None, extras)

    def load_ranges(self, binding, ranges_revision):
        if not self.path.exists():
            return None
        try:
            with self._connect(readonly=True) as connection:
                record = self._decode(connection.execute("SELECT revision,payload FROM ranges WHERE identity=?", (_digest([self._identity(binding), ranges_revision]),)).fetchone())
            return record["items"] if record and record.get("binding") == binding and record.get("ranges_revision") == ranges_revision else None
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError, KeyError):
            return None

    def search_many(self, bindings, query, *, memory=None):
        found = set()
        memory = memory or {}
        if self.path.exists():
            try:
                with self._connect(readonly=True) as connection:
                    for key, (binding, revision) in bindings.items():
                        check_read_cancelled()
                        record = self._decode(connection.execute("SELECT revision,payload FROM searches WHERE identity=?", (self.search_identity(binding, key, revision),)).fetchone())
                        projection = memory.get(key) or (record.get("projection") if record and record.get("binding") == binding and record.get("question_revision") == revision else None)
                        if projection and projection_matches(projection, query):
                            found.add(key)
                return found
            except (OSError, sqlite3.DatabaseError, ValueError, TypeError, KeyError):
                pass
        for key, projection in memory.items():
            if projection_matches(projection, query):
                found.add(key)
        return found

    def revision(self, preview):
        """Read the compare-and-swap token without creating a database."""
        if not self.path.exists():
            return None
        try:
            with self._connect(readonly=True) as connection:
                row = connection.execute("SELECT revision FROM sources WHERE identity=?",
                                         (self._identity(source_binding(preview)),)).fetchone()
            return row[0] if row else None
        except (OSError, sqlite3.DatabaseError):
            return None

    def save_observations(self, records):
        return self._save("observations", [(self._identity(record["binding"]), record) for record in records], None)

    def observations(self):
        """Historical projections only; callers must revalidate before use."""
        if not self.path.exists():
            return []
        try:
            with self._connect(readonly=True) as connection:
                records = [self._decode(row) for row in connection.execute("SELECT revision,payload FROM observations ORDER BY identity")]
            return [record for record in records if record is not None
                    and record.get("observation_revision") == OBSERVATION_REVISION
                    and record.get("freshness", {}).get("live_revalidated") is False]
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError, RecursionError):
            return []

    def locations_for_keys(self, keys):
        """Historical lookup hints only; the service must reopen those archives."""
        requested, found = set(keys), {}
        for observation in self.observations():
            location = observation.get("archive_location", {})
            batch_id, source_id = location.get("batch_id"), location.get("archive_source_id")
            if not all(isinstance(value, str) and value for value in (batch_id, source_id)):
                continue
            for row in observation.get("questions", []):
                if row.get("key") in requested:
                    found[row["key"]] = (batch_id, source_id)
        return found


class WordListingSearchProvider:
    """Shared query handle: listing rows carry no postings or full source text."""

    def __init__(self, index, bindings, memory=None):
        self.index, self.bindings, self.memory = index, bindings, memory or {}
        self._lock, self._queries = threading.RLock(), {}

    def __deepcopy__(self, memo):
        return self

    def matches(self, key, revision, query):
        query = query.strip().casefold()
        if not query:
            return True
        if key not in self.bindings or self.bindings[key][1] != revision:
            return False
        with self._lock:
            if query not in self._queries:
                self._queries[query] = frozenset(self.index.search_many(self.bindings, query, memory=self.memory))
                while len(self._queries) > 4:
                    del self._queries[next(iter(self._queries))]
            return key in self._queries[query]
