from __future__ import annotations

import hashlib
import io
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from PIL import Image, ImageChops, ImageStat

from .candidate_review import CandidateCropPayload
from .master_wave1_workbench import (
    MasterWave1WorkbenchError,
    MasterWave1WorkbenchReader,
)
from .question_visual_scan import (
    PT_BATCH_SPEC,
    QuestionVisualScanError,
    _BatchQuestionVisualScanReader,
)
from .reference_answer import (
    project_reference_answer,
    reference_answer_catalog_metadata,
)
from .security import SecurityError, validate_identifier


PRODUCT_ID = "MASTER-VISUAL-SCAN-ALIAS-PT2026-V1-2026-08-25"
PRODUCT_RELATIVE = Path(
    "kb/classification/master_visual_scan_alias_pt2026_v1_2026-08-25"
)
SCOPE = "candidate_only_read_only_master_visual_scan_alias"
COVERAGE_KIND = "alias_existing_visual_scan"
EXPECTED_MANIFEST_FILE_SHA256 = (
    "1fb2887c1ab810340d967ff8dc63a77c324caf235e6d1095a8e2b3d28866462b"
)
EXPECTED_MANIFEST_FILE_BYTES = 3182
EXPECTED_MANIFEST_SELF_SHA256 = (
    "6ed204be952b27d795a23af9f81b1bc82aaf636ce1352e240ff7509a056c051e"
)
EXPECTED_OUTPUT_FILES = frozenset(
    {
        "README.md",
        "alias_record_schema.json",
        "alias_records.jsonl",
        "build_product.py",
        "coverage_report.json",
        "evidence_manifest.json",
        "run_mutation_tests.py",
        "test_product.py",
        "validate_product.py",
    }
)
EXPECTED_GATES = {
    "human_reviewed": False,
    "human_chemistry_reviewed": False,
    "verified": False,
    "official": False,
    "retrieval_ready": False,
    "retrieval_allowed": False,
    "teaching_use_allowed": False,
    "generation_allowed": False,
    "publication_allowed": False,
    "answer_verified": False,
    "rubric_verified": False,
    "measured_difficulty_verified": False,
    "component_reuse_allowed": False,
    "direct_source_pixel_reuse_allowed": False,
    "pixel_reuse_allowed": False,
}
AUTHORITY = {"candidate_only": True, "read_only": True, **EXPECTED_GATES}
EXPECTED_COUNTS = {
    "alias_master_covered": 14,
    "new_physical_scans": 0,
    "exact_1_to_1_alias_records": 8,
    "deterministic_1_to_many_alias_records": 6,
    "unmapped_alias_records": 0,
    "unique_target_wave_scan_atomic_parts": 19,
    "unique_physical_printed_questions": 13,
}
EXPECTED_AGGREGATE_EFFECT = {
    "master_visible_coverage_before": 332,
    "master_visible_coverage_after": 346,
    "master_remaining_before": 138,
    "master_remaining_after": 124,
    "exact_1_to_1_count_unchanged": 169,
    "master_direct_count_unchanged": 163,
    "aggregate_mutation_performed_by_this_product": False,
}
EXPECTED_RECORD_EVIDENCE_COUNTS = {
    "master_alias_records": 14,
    "complete_strong_key_records": 5,
    "legacy_pixel_registered_records": 9,
    "target_scan_record_bindings": 21,
    "unique_target_question_crops": 13,
    "legacy_whole_page_pairs": 2,
}
EXPECTED_CLOSURE_CONTRACT = {
    "all_bound_sources_require_sha256_and_bytes": True,
    "all_local_paths_are_database_relative": True,
    "answer_pixels_publishable": False,
    "path_traversal_forbidden": True,
    "symlinks_forbidden": True,
}
MAX_CROP_BYTES = 1024 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class _ExpectedAlias:
    method: str
    chain: tuple[str, str, str]
    wave: tuple[str, ...]
    wave_theme: str
    wave_printed: str
    crop_id: str
    crop_sha256: str
    upstream: tuple[str, str] | None = None
    legacy_crop_sha256: str | None = None
    counterpart: str | None = None
    legacy_question_id: str | None = None


_EXPECTED: dict[str, _ExpectedAlias] = {
    "MASTER-PART-e2f8e8e5a5539ea91234": _ExpectedAlias(
        "complete_package_strong_key_wave_refinement",
        (
            "MASTER-PAPER-64176038a1d9c587f08a",
            "MASTER-THEME-6f640a65bc38f8b9db2e",
            "MASTER-PRINTED-add3c6b7130710b5eefe",
        ),
        (
            "W1-PT2026-EM-AP-PT2026-T1-Q01-P01-S01",
            "W1-PT2026-EM-AP-PT2026-T1-Q01-P01-S02",
        ),
        "W1-PT2026-EM-T01",
        "W1-PT2026-EM-PQ-PT2026-T1-Q01",
        "PT2026-T1-Q01-QUESTION-S1",
        "8f57a34d32d18eeec4183a99ec4e9730ca6b9d0e3254400efe7bec61583fd40a",
        ("PT2026-T1-Q01-P01", "PT2026-T1-Q01"),
    ),
    "MASTER-PART-3344e59715850ab08481": _ExpectedAlias(
        "complete_package_strong_key_wave_refinement",
        (
            "MASTER-PAPER-64176038a1d9c587f08a",
            "MASTER-THEME-345357d26f37b7681519",
            "MASTER-PRINTED-aeb56959380cb5396efc",
        ),
        (
            "W1-PT2026-EM-AP-PT2026-T2-Q02-P01-S01",
            "W1-PT2026-EM-AP-PT2026-T2-Q02-P01-S02",
        ),
        "W1-PT2026-EM-T02",
        "W1-PT2026-EM-PQ-PT2026-T2-Q02",
        "PT2026-T2-Q02-QUESTION-S1",
        "25230d1354be44080028de3f40bdc63ebf71a9f50607067a0f4826646172e056",
        ("PT2026-T2-Q02-P01", "PT2026-T2-Q02"),
    ),
    "MASTER-PART-d8284b71b9b7e14b906a": _ExpectedAlias(
        "complete_package_strong_key_wave_refinement",
        (
            "MASTER-PAPER-64176038a1d9c587f08a",
            "MASTER-THEME-6fc2e50fd278e3bea0de",
            "MASTER-PRINTED-8933200aed417b6b74ee",
        ),
        (
            "W1-PT2026-EM-AP-PT2026-T3-Q02-P01-S01",
            "W1-PT2026-EM-AP-PT2026-T3-Q02-P01-S02",
        ),
        "W1-PT2026-EM-T03",
        "W1-PT2026-EM-PQ-PT2026-T3-Q02",
        "PT2026-T3-Q02-QUESTION-S1",
        "2271172463d7c07e0700d8f0468578e40d2f09c2968be0411388b3550f3f0ea6",
        ("PT2026-T3-Q02-P01", "PT2026-T3-Q02"),
    ),
    "MASTER-PART-91704bf6ac0d72f821cf": _ExpectedAlias(
        "complete_package_strong_key_wave_refinement",
        (
            "MASTER-PAPER-64176038a1d9c587f08a",
            "MASTER-THEME-36a9b11c8a82b87a0a5f",
            "MASTER-PRINTED-7e2f3e1c3bc40e6142b1",
        ),
        (
            "W1-PT2026-EM-AP-PT2026-T4-Q07-P01-S01",
            "W1-PT2026-EM-AP-PT2026-T4-Q07-P01-S02",
        ),
        "W1-PT2026-EM-T04",
        "W1-PT2026-EM-PQ-PT2026-T4-Q07",
        "PT2026-T4-Q07-QUESTION-S1",
        "3b3ec03090328fa182e29d221b9ef059bf34dc73128c9ffb65d2690d134259cd",
        ("PT2026-T4-Q07-P01", "PT2026-T4-Q07"),
    ),
    "MASTER-PART-42e284d0b9dd37af22d0": _ExpectedAlias(
        "complete_package_strong_key_wave_refinement",
        (
            "MASTER-PAPER-64176038a1d9c587f08a",
            "MASTER-THEME-36a9b11c8a82b87a0a5f",
            "MASTER-PRINTED-f67b2bd40085cbc5da22",
        ),
        (
            "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S01",
            "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S02",
            "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S03",
        ),
        "W1-PT2026-EM-T04",
        "W1-PT2026-EM-PQ-PT2026-T4-Q08",
        "PT2026-T4-Q08-QUESTION-S1",
        "d194e68e333c8ee09f1ed717b39da33ec176bb34e9ccbdb13fec0b6ef56758e3",
        ("PT2026-T4-Q08-P01", "PT2026-T4-Q08"),
    ),
}

_LEGACY_COUNTERPARTS = (
    "MASTER-PART-769e002c9bfb6b46bc99",
    "MASTER-PART-d8284b71b9b7e14b906a",
    "MASTER-PART-97e22811427ba3ffaf98",
    "MASTER-PART-97d676f3a08e1f06df40",
    "MASTER-PART-190f453d1caf74dc2876",
    "MASTER-PART-9b5a096016095e689345",
    "MASTER-PART-d285f754451ab9c96ccd",
    "MASTER-PART-7829b0a319fa283930d7",
    "MASTER-PART-f4ce67567006ed0a8ef4",
)
_LEGACY_CROP_HASHES = (
    "c8663b38a7a75840357d601ce39e56041516e7019a8f794f2e0a6368f8b4aaba",
    "a2454754027f8942fde0042817122f9d48cd187588d18ceb91e7a0c0cc382d9c",
    "20684480dadc1aaeff8907fa6a618e3f083723daa046b635ca3fa27b0f501be3",
    "5ba846857945df6105278543c4678ce614ede3f5105cb30fbd067e2b722c7534",
    "139b6727078bf48fa8753bceb551b1bdc503c7cfdbb6cb2d0ae42793e15a3cf5",
    "1da1a0f3c4f85b30af593f9a18dc92f64a251af53f5c20f12641c3435836bbb0",
    "634f0a5f337d009612ca7fde295dcf39caa12d09ecc237a2c70e357079669bce",
    "faabc0cf95ec711abecee94107b67808b63082aec937b89dfb193ec2dbbd880c",
    "8c9ba13b02a7b5809f2000aba1352d7bbb29e23d8488d596e378454e2d0c6bd4",
)
_TARGET_CROP_HASHES = (
    "3e43669d4a53c05b513bed1fa11957987423084a4df71d92e0a54451038d87a1",
    "2271172463d7c07e0700d8f0468578e40d2f09c2968be0411388b3550f3f0ea6",
    "a7e53eb3ca1f4acbb37d5c4b5f434e0008a265409187f93d51bba26a5960481c",
    "c9a2ac1bd7f77ebc644d8cb9486ced40704cb1880fb738b888903c13b86f031b",
    "f7a09290dc9df87835bffc13471d0bd06cc6ee1d884dfd800668508b603ae9b7",
    "ff1ed857212fc0d999e14560a616dc4b77add7bd29a815f2856d664411649691",
    "fdba44b8f00d863a36dde686b0c608a00c4413a414e589a80e7ef63699608aaf",
    "c0c20d9aa94abd50cc89bf69b71391d00a5fccf95b38c261f672beb804b9b566",
    "4bd38d6a260ebd093dff3e7511f088b466e043de9f43fa74d9a34b8bad8334a2",
)
for _q in range(1, 10):
    _wave = (
        (
            "W1-PT2026-EM-AP-PT2026-T3-Q02-P01-S01",
            "W1-PT2026-EM-AP-PT2026-T3-Q02-P01-S02",
        )
        if _q == 2
        else (f"W1-PT2026-EM-AP-PT2026-T3-Q{_q:02d}-P01",)
    )
    _EXPECTED[f"PT2026-EM-S3-Q{_q}-P1"] = _ExpectedAlias(
        "legacy_scaled_pixel_and_visual_correspondence",
        (
            "PAPER-d2cbed4449c07a3b8799",
            "THEME-46078b96d3ae9af5f430",
            f"PT2026-EM-S3-Q{_q}",
        ),
        _wave,
        "W1-PT2026-EM-T03",
        f"W1-PT2026-EM-PQ-PT2026-T3-Q{_q:02d}",
        f"PT2026-T3-Q{_q:02d}-QUESTION-S1",
        _TARGET_CROP_HASHES[_q - 1],
        legacy_crop_sha256=_LEGACY_CROP_HASHES[_q - 1],
        counterpart=_LEGACY_COUNTERPARTS[_q - 1],
        legacy_question_id=f"PT2026-EM-S3-Q{_q}",
    )

EXPECTED_MASTER_NODE_IDS = tuple(_EXPECTED)
EXPECTED_TARGET_WAVE_IDS = frozenset(
    wave for expected in _EXPECTED.values() for wave in expected.wave
)
EXPECTED_TARGET_PRINTED_IDS = frozenset(
    expected.wave_printed for expected in _EXPECTED.values()
)


class MasterVisualScanAliasError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[dict[str, Any], ...]
    by_master_id: dict[str, dict[str, Any]]
    source_bindings: dict[str, dict[str, Any]]
    source_bytes: dict[str, bytes]
    pt_snapshot: Any
    pt_reader: _BatchQuestionVisualScanReader
    master_manifest_self_sha256: str
    pt_manifest_self_sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MasterVisualScanAliasError(
            "master_alias_json_invalid", f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise MasterVisualScanAliasError(
            "master_alias_json_invalid", f"{label} must be a JSON object"
        )
    return value


def _jsonl_objects(raw: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MasterVisualScanAliasError(
            "master_alias_json_invalid", f"{label} is not strict UTF-8"
        ) from exc
    lines = text.splitlines()
    if not text or not text.endswith("\n") or not lines or any(not line for line in lines):
        raise MasterVisualScanAliasError(
            "master_alias_json_invalid",
            f"{label} must be newline-terminated JSONL without blank records",
        )
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_json_invalid", f"{label} record {index} is invalid"
            ) from exc
        if not isinstance(value, dict):
            raise MasterVisualScanAliasError(
                "master_alias_json_invalid", f"{label} record {index} is not an object"
            )
        rows.append(value)
    return rows


def _safe_relative(value: Any, *, one_component: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise MasterVisualScanAliasError(
            "master_alias_path_invalid", "a bound path is unsafe"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != value
        or (one_component and len(pure.parts) != 1)
    ):
        raise MasterVisualScanAliasError(
            "master_alias_path_invalid", "a bound path is unsafe"
        )
    return value


def _false_gates(value: Any, label: str) -> None:
    if value != EXPECTED_GATES or set(value or {}) != set(EXPECTED_GATES):
        raise MasterVisualScanAliasError(
            "master_alias_gate_elevated", f"{label} authority gates drifted"
        )


def _iter_local_paths(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            if (key == "path" or key.endswith("_path")) and isinstance(nested, str):
                yield nested
            else:
                yield from _iter_local_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_local_paths(nested)


def _rounded_mad(image_a: Image.Image, image_b: Image.Image) -> float:
    difference = ImageChops.difference(image_a.convert("RGB"), image_b.convert("RGB"))
    return round(sum(ImageStat.Stat(difference).mean) / 3.0, 6)


class MasterVisualScanAliasReader:
    """Strict read-only projection of frozen Master-to-PT52 alias coverage."""

    def __init__(
        self,
        shchem_root: Path,
        master_workbench: MasterWave1WorkbenchReader | None = None,
    ):
        try:
            self.shchem_root = shchem_root.resolve(strict=True)
        except OSError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_root_missing", "sh-chem-db root is unavailable"
            ) from exc
        self.product_root = self._resolve_product_root(PRODUCT_RELATIVE.as_posix())
        self.master_workbench = master_workbench or MasterWave1WorkbenchReader(
            self.shchem_root
        )

    def _resolve_product_root(self, relative: str) -> Path:
        relative = _safe_relative(relative)
        cursor = self.shchem_root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise MasterVisualScanAliasError(
                    "master_alias_symlink_denied",
                    "the frozen product path may not traverse symbolic links",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except OSError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_file_missing", "the frozen product is unavailable"
            ) from exc
        if not resolved.is_relative_to(self.shchem_root) or not resolved.is_dir():
            raise MasterVisualScanAliasError(
                "master_alias_path_invalid", "the product leaves sh-chem-db"
            )
        return resolved

    def _verified_file(self, root: Path, relative: str) -> tuple[Path, bytes]:
        relative = _safe_relative(relative)
        cursor = root
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise MasterVisualScanAliasError(
                    "master_alias_symlink_denied",
                    "a bound path may not traverse symbolic links",
                )
        try:
            resolved = cursor.resolve(strict=True)
        except OSError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_file_missing", "a bound file is unavailable"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise MasterVisualScanAliasError(
                "master_alias_path_invalid", "a bound file leaves its fixed root"
            )
        try:
            return resolved, resolved.read_bytes()
        except OSError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_file_read_failed", "a bound file could not be read"
            ) from exc

    @staticmethod
    def _binding_index(
        value: Any,
        label: str,
        *,
        expected_count: int,
        one_component: bool,
        with_roles: bool = False,
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(value, list) or len(value) != expected_count:
            raise MasterVisualScanAliasError(
                "master_alias_binding_invalid", f"{label} binding count drifted"
            )
        expected_keys = {
            "path",
            "bytes",
            "sha256",
            *(("roles",) if with_roles else ()),
        }
        result: dict[str, dict[str, Any]] = {}
        for item in value:
            if not isinstance(item, dict) or set(item) != expected_keys:
                raise MasterVisualScanAliasError(
                    "master_alias_binding_invalid", f"{label} binding shape drifted"
                )
            path = _safe_relative(item.get("path"), one_component=one_component)
            roles = item.get("roles")
            if (
                path in result
                or type(item.get("bytes")) is not int
                or item["bytes"] < 1
                or not isinstance(item.get("sha256"), str)
                or _HEX64.fullmatch(item["sha256"]) is None
                or (
                    with_roles
                    and (
                        not isinstance(roles, list)
                        or not roles
                        or roles != sorted(set(roles))
                        or any(not isinstance(role, str) or not role for role in roles)
                    )
                )
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_binding_invalid", f"{label} binding value drifted"
                )
            result[path] = dict(item)
        return result

    def _verify_bindings(
        self, root: Path, bindings: dict[str, dict[str, Any]], label: str
    ) -> dict[str, bytes]:
        output: dict[str, bytes] = {}
        for relative, binding in bindings.items():
            _, raw = self._verified_file(root, relative)
            if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
                raise MasterVisualScanAliasError(
                    "master_alias_binding_mismatch",
                    f"{label} hash or byte binding drifted",
                )
            output[relative] = raw
        return output

    @staticmethod
    def _bound_line(
        binding: Any,
        source_bytes: dict[str, bytes],
        *,
        allowed_extra: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        required = {"path", "line_index_zero_based", "line_sha256"}
        if (
            not isinstance(binding, dict)
            or not required.issubset(binding)
            or not set(binding).issubset(required | set(allowed_extra))
            or type(binding.get("line_index_zero_based")) is not int
            or binding["line_index_zero_based"] < 0
            or not isinstance(binding.get("line_sha256"), str)
            or _HEX64.fullmatch(binding["line_sha256"]) is None
        ):
            raise MasterVisualScanAliasError(
                "master_alias_line_binding_invalid", "a line binding shape drifted"
            )
        path = _safe_relative(binding["path"])
        raw = source_bytes.get(path)
        if raw is None:
            raise MasterVisualScanAliasError(
                "master_alias_line_binding_invalid",
                "a line binding leaves the external evidence closure",
            )
        lines = raw.splitlines()
        index = binding["line_index_zero_based"]
        if index >= len(lines) or any(not line.strip() for line in lines):
            raise MasterVisualScanAliasError(
                "master_alias_line_binding_invalid", "a bound line is unavailable"
            )
        line = lines[index]
        if _sha256(line) != binding["line_sha256"]:
            raise MasterVisualScanAliasError(
                "master_alias_line_binding_invalid", "a bound line hash drifted"
            )
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MasterVisualScanAliasError(
                "master_alias_line_binding_invalid", "a bound line is invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise MasterVisualScanAliasError(
                "master_alias_line_binding_invalid", "a bound line is not an object"
            )
        return value

    @staticmethod
    def _manifest_self_hash(manifest: dict[str, Any]) -> str:
        value = deepcopy(manifest)
        value["manifest_self_sha256"] = None
        return _sha256(_canonical_bytes(value))

    @staticmethod
    def _load_rgb(raw: bytes, label: str) -> Image.Image:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                return image.convert("RGB")
        except Exception as exc:
            raise MasterVisualScanAliasError(
                "master_alias_image_invalid", f"{label} is not a valid image"
            ) from exc

    def _validate_pixel_registration(
        self,
        master_id: str,
        record: dict[str, Any],
        expected: _ExpectedAlias,
        source_bytes: dict[str, bytes],
        image_cache: dict[str, Image.Image],
    ) -> None:
        mapping = record["mapping_evidence"]
        legacy_crop = mapping["legacy_crop"]
        registration = mapping["page_registration"]
        if (
            legacy_crop.get("crop_sha256") != expected.legacy_crop_sha256
            or registration.get("resampling") != "PIL.Image.Resampling.LANCZOS"
            or registration.get("old_dimensions") != [2560, 3620]
            or registration.get("target_dimensions") != [1080, 1527]
            or registration.get("legacy_crop_box_xywh")
            != legacy_crop.get("crop_box_xywh_pixels")
            or registration.get("target_crop_box_xywh")
            != record["target"]["question_crop"].get("crop_box_xywh_pixels")
        ):
            raise MasterVisualScanAliasError(
                "master_alias_pixel_proof_invalid",
                "legacy pixel registration fields drifted",
            )
        for path_key, hash_key in (
            ("legacy_page_path", "legacy_page_sha256"),
            ("target_page_path", "target_page_sha256"),
        ):
            path = registration.get(path_key)
            binding_raw = source_bytes.get(path)
            if binding_raw is None or _sha256(binding_raw) != registration.get(hash_key):
                raise MasterVisualScanAliasError(
                    "master_alias_pixel_proof_invalid",
                    "a registered whole-page pixel binding drifted",
                )
            if path not in image_cache:
                image_cache[path] = self._load_rgb(binding_raw, path_key)
        old = image_cache[registration["legacy_page_path"]]
        target = image_cache[registration["target_page_path"]]
        if list(old.size) != registration["old_dimensions"] or list(target.size) != registration["target_dimensions"]:
            raise MasterVisualScanAliasError(
                "master_alias_pixel_proof_invalid", "registered page dimensions drifted"
            )
        resized = old.resize(target.size, Image.Resampling.LANCZOS)
        x, y, width, height = registration["target_crop_box_xywh"]
        old_x, old_y, old_width, old_height = registration["legacy_crop_box_xywh"]
        scale_x = target.width / old.width
        scale_y = target.height / old.height
        scaled = (
            old_x * scale_x,
            old_y * scale_y,
            (old_x + old_width) * scale_x,
            (old_y + old_height) * scale_y,
        )
        target_box = (x, y, x + width, y + height)
        intersect_width = max(
            0.0, min(scaled[2], target_box[2]) - max(scaled[0], target_box[0])
        )
        intersect_height = max(
            0.0, min(scaled[3], target_box[3]) - max(scaled[1], target_box[1])
        )
        recomputed = {
            "full": _rounded_mad(resized, target),
            "crop": _rounded_mad(
                resized.crop(target_box), target.crop(target_box)
            ),
            "coverage": round(
                100.0 * intersect_width * intersect_height / (width * height), 6
            ),
            "scale_x": round(scale_x, 9),
            "scale_y": round(scale_y, 9),
        }
        recorded = {
            "full": registration.get(
                "full_page_rgb_mean_absolute_difference_0_255"
            ),
            "crop": registration.get(
                "target_crop_rgb_mean_absolute_difference_0_255"
            ),
            "coverage": registration.get("legacy_crop_coverage_of_target_percent"),
            "scale_x": registration.get("scale_x"),
            "scale_y": registration.get("scale_y"),
        }
        expected_full = 0.549679 if master_id.endswith(tuple(f"Q{i}-P1" for i in range(1, 7))) else 0.385289
        if (
            recomputed != recorded
            or recomputed["full"] != expected_full
            or recomputed["crop"] > 1.385
            or recomputed["coverage"] < 96.33
        ):
            raise MasterVisualScanAliasError(
                "master_alias_pixel_proof_invalid",
                "legacy pixel proof does not reproduce its frozen metrics",
            )
        checks = mapping.get("visible_correspondence_checks")
        expected_check_ids = {
            "whole_page_pixel_registration",
            "target_crop_pixel_registration",
            "task_scope_and_response_requirement",
            "visible_chemical_objects_notation_and_structure",
        }
        if (
            not isinstance(checks, list)
            or {check.get("check_id") for check in checks} != expected_check_ids
            or any(
                not isinstance(check, dict)
                or len(str(check.get("basis_zh", ""))) < 20
                or not str(check.get("status", "")).startswith("agree_")
                for check in checks
            )
        ):
            raise MasterVisualScanAliasError(
                "master_alias_pixel_proof_invalid",
                "legacy visible-correspondence proof drifted",
            )

    def _validate_record(
        self,
        record: dict[str, Any],
        expected: _ExpectedAlias,
        source_bindings: dict[str, dict[str, Any]],
        source_bytes: dict[str, bytes],
        pt_snapshot: Any,
        counterpart_rows: dict[str, dict[str, Any]],
        image_cache: dict[str, Image.Image],
    ) -> None:
        master = record["master"]
        master_id = master["atomic_part_id"]
        target = record["target"]
        chain = master["parent_chain"]
        actual_chain = (
            chain.get("paper_id"),
            chain.get("theme_big_question_id"),
            chain.get("printed_question_id"),
        )
        wave_ids = tuple(target.get("wave_scan_atomic_part_ids", ()))
        crop = target["question_crop"]
        if (
            record.get("candidate_only") is not True
            or record.get("coverage_claim")
            != "alias_master_question_pixels_covered_by_existing_pt52_scan"
            or record.get("mapping_method") != expected.method
            or record.get("relation_type")
            != ("alias_coverage_1_to_1" if len(expected.wave) == 1 else "alias_coverage_1_to_many")
            or record.get("not_exact_crosswalk") is not True
            or record.get("not_master_direct_scan") is not True
            or record.get("new_physical_scan_performed") is not False
            or record.get("osv_profile_binding") is not None
            or actual_chain != expected.chain
            or wave_ids != expected.wave
            or target.get("paper_id") != "W1-PT2026-EM"
            or target.get("theme_id") != expected.wave_theme
            or target.get("printed_question_id") != expected.wave_printed
            or target.get("cardinality") != f"1:{len(expected.wave)}"
            or crop.get("crop_id") != expected.crop_id
            or crop.get("crop_sha256") != expected.crop_sha256
        ):
            raise MasterVisualScanAliasError(
                "master_alias_record_invalid",
                "a frozen alias identity, parent chain, or cardinality drifted",
            )
        _false_gates(record.get("authority_gates"), "alias record")
        expected_namespace = (
            "complete_overlay_v2:putuo_2026_second_mock_complete_paper"
            if expected.upstream is not None
            else "core"
        )
        if master.get("identity_namespace") != expected_namespace:
            raise MasterVisualScanAliasError(
                "master_alias_record_invalid", "master identity namespace drifted"
            )

        master_row = self._bound_line(master["record_binding"], source_bytes)
        paper_row = self._bound_line(master["paper_record_binding"], source_bytes)
        theme_row = self._bound_line(master["theme_record_binding"], source_bytes)
        printed_row = self._bound_line(master["printed_record_binding"], source_bytes)
        if (
            master_row.get("part_id") != master_id
            or (
                master_row.get("parent_paper_id"),
                master_row.get("parent_theme_big_question_id"),
                master_row.get("parent_printed_question_id"),
            )
            != expected.chain
            or paper_row.get("paper_id") != expected.chain[0]
            or theme_row.get("theme_id") != expected.chain[1]
            or theme_row.get("parent_paper_id") != expected.chain[0]
            or printed_row.get("printed_question_id") != expected.chain[2]
            or printed_row.get("parent_theme_big_question_id") != expected.chain[1]
            or printed_row.get("parent_paper_id") != expected.chain[0]
        ):
            raise MasterVisualScanAliasError(
                "master_alias_parent_chain_invalid",
                "a bound Master paper/theme/printed/atomic chain drifted",
            )

        scan_bindings = target.get("scan_record_bindings")
        if not isinstance(scan_bindings, list) or len(scan_bindings) != len(wave_ids):
            raise MasterVisualScanAliasError(
                "master_alias_target_invalid", "PT52 scan cardinality drifted"
            )
        scan_rows: dict[str, dict[str, Any]] = {}
        for binding, wave_id in zip(scan_bindings, wave_ids, strict=True):
            if binding.get("wave_atomic_part_id") != wave_id:
                raise MasterVisualScanAliasError(
                    "master_alias_target_invalid", "PT52 target ordering drifted"
                )
            scan_row = self._bound_line(
                binding,
                source_bytes,
                allowed_extra=frozenset({"wave_atomic_part_id"}),
            )
            upstream_record = pt_snapshot.by_node_id.get(wave_id)
            hierarchy = scan_row.get("hierarchy", {})
            question_evidence = [
                item
                for item in scan_row.get("viewed_evidence", [])
                if item.get("evidence_role") == "question"
            ]
            if (
                upstream_record is None
                or scan_row != upstream_record
                or hierarchy.get("atomic_part_id") != wave_id
                or hierarchy.get("paper_id") != "W1-PT2026-EM"
                or hierarchy.get("theme_id") != expected.wave_theme
                or hierarchy.get("printed_question_id") != expected.wave_printed
                or len(question_evidence) != 1
                or question_evidence[0].get("crop_id") != expected.crop_id
                or question_evidence[0].get("sha256") != expected.crop_sha256
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_target_invalid",
                    "a target does not match the fully validated PT52 scan record",
                )
            scan_rows[wave_id] = scan_row

        crop_row = self._bound_line(crop["crop_manifest_record_binding"], source_bytes)
        crop_binding = source_bindings.get(crop.get("crop_path"))
        page_binding = source_bindings.get(crop.get("source_page_path"))
        if (
            crop_row.get("crop_id") != expected.crop_id
            or crop_row.get("crop_sha256") != expected.crop_sha256
            or crop_row.get("crop_asset") != crop.get("crop_path")
            or crop_row.get("source_asset") != crop.get("source_page_path")
            or crop_row.get("source_sha256") != crop.get("source_page_sha256")
            or crop_binding is None
            or crop_binding.get("roles") != ["target_question_crop"]
            or crop_binding.get("sha256") != expected.crop_sha256
            or crop_binding.get("bytes") != crop.get("crop_bytes")
            or page_binding is None
            or page_binding.get("sha256") != crop.get("source_page_sha256")
        ):
            raise MasterVisualScanAliasError(
                "master_alias_crop_binding_invalid",
                "target crop, crop manifest, or source page drifted",
            )
        crop_image = self._load_rgb(source_bytes[crop["crop_path"]], "target crop")
        page_path = crop["source_page_path"]
        if page_path not in image_cache:
            image_cache[page_path] = self._load_rgb(source_bytes[page_path], "source page")
        page_image = image_cache[page_path]
        x, y, width, height = crop["crop_box_xywh_pixels"]
        if (
            list(crop_image.size) != crop.get("crop_dimensions")
            or crop_image.size != (width, height)
            or ImageChops.difference(
                crop_image, page_image.crop((x, y, x + width, y + height))
            ).getbbox()
            is not None
        ):
            raise MasterVisualScanAliasError(
                "master_alias_crop_binding_invalid",
                "target crop pixels no longer match the bound source page box",
            )

        answer = record["answer_projection"]
        statuses = answer.get("unit_statuses")
        if (
            not isinstance(statuses, list)
            or [status.get("wave_atomic_part_id") for status in statuses]
            != list(wave_ids)
            or answer.get("merged_reference_answer_text") is not None
            or answer.get("reference_answer_text_projected") is not False
            or answer.get("answer_image_publish_allowed") is not False
            or answer.get("answer_image_reuse_allowed") is not False
            or answer.get("merge_prohibited_for_one_to_many")
            is not (len(wave_ids) > 1)
        ):
            raise MasterVisualScanAliasError(
                "master_alias_answer_boundary_invalid",
                "answer projection cardinality or no-merge boundary drifted",
            )
        for status, wave_id in zip(statuses, wave_ids, strict=True):
            upstream_answer = scan_rows[wave_id].get("answer", {})
            if (
                status.get("availability") != upstream_answer.get("availability")
                or status.get("source_authority") != upstream_answer.get("authority")
                or status.get("independently_verified") is not False
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_answer_boundary_invalid",
                    "an answer unit status drifted from PT52",
                )

        mapping = record["mapping_evidence"]
        if expected.upstream is not None:
            upstream_part, upstream_question = expected.upstream
            strong_key = mapping.get("strong_key", {})
            if (
                strong_key.get("package_id")
                != "putuo_2026_second_mock_complete_paper"
                or strong_key.get("record_key")
                != f"putuo_2026_second_mock_complete_paper::{upstream_part}"
                or strong_key.get("upstream_part_id") != upstream_part
                or strong_key.get("upstream_question_id") != upstream_question
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_strong_key_invalid", "complete-package strong key drifted"
                )
            overlay_row = self._bound_line(
                mapping["overlay_record_binding"], source_bytes
            )
            source_part_row = self._bound_line(
                mapping["source_part_record_binding"], source_bytes
            )
            source_part_binding = source_bindings.get(
                mapping["source_part_record_binding"]["path"]
            )
            if (
                overlay_row.get("part_id") != upstream_part
                or overlay_row.get("source_record", {}).get("record_key")
                != strong_key["record_key"]
                or source_part_row.get("part_id") != upstream_part
                or source_part_row.get("question_id") != upstream_question
                or source_part_binding is None
                or source_part_binding.get("sha256")
                != strong_key.get("source_part_file_sha256")
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_strong_key_invalid",
                    "complete-package strong-key evidence drifted",
                )
        else:
            if (
                mapping.get("existing_complete_namespace_counterpart_master_part_id")
                != expected.counterpart
                or counterpart_rows.get(expected.counterpart or "", {}).get(
                    "identity_namespace"
                )
                != "complete_overlay_v2:putuo_2026_second_mock_complete_paper"
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_pixel_proof_invalid",
                    "legacy complete-namespace counterpart drifted",
                )
            legacy_source = self._bound_line(
                mapping["legacy_source_record_binding"], source_bytes
            )
            parts = legacy_source.get("parts", [])
            registration = mapping["page_registration"]
            page_refs = [
                ref
                for ref in legacy_source.get("source_locator", {}).get("page_refs", [])
                if ref.get("role") == "question"
            ]
            legacy_crop = mapping["legacy_crop"]
            legacy_binding = source_bindings.get(legacy_crop.get("crop_path"))
            if (
                legacy_source.get("question_id") != expected.legacy_question_id
                or len(parts) != 1
                or parts[0].get("part_id") != master_id
                or len(page_refs) != 1
                or page_refs[0].get("asset") != registration.get("legacy_page_path")
                or page_refs[0].get("crop_box")
                != registration.get("legacy_crop_box_xywh")
                or legacy_crop.get("crop_sha256") != expected.legacy_crop_sha256
                or legacy_binding is None
                or legacy_binding.get("roles") != ["legacy_question_crop"]
                or legacy_binding.get("sha256") != expected.legacy_crop_sha256
                or legacy_binding.get("bytes") != legacy_crop.get("crop_bytes")
            ):
                raise MasterVisualScanAliasError(
                    "master_alias_pixel_proof_invalid", "legacy crop evidence drifted"
                )
            self._validate_pixel_registration(
                master_id, record, expected, source_bytes, image_cache
            )

    def _validated_snapshot(self) -> _Snapshot:
        _, manifest_raw = self._verified_file(self.product_root, "manifest.json")
        if (
            len(manifest_raw) != EXPECTED_MANIFEST_FILE_BYTES
            or _sha256(manifest_raw) != EXPECTED_MANIFEST_FILE_SHA256
        ):
            raise MasterVisualScanAliasError(
                "master_alias_manifest_invalid", "manifest file bytes drifted"
            )
        manifest = _json_object(manifest_raw, "alias manifest")
        if (
            manifest.get("product_id") != PRODUCT_ID
            or manifest.get("schema_version")
            != "1.0.0-master-visual-scan-alias-manifest"
            or manifest.get("status")
            != "PASS_FROZEN_ALIAS_COVERAGE_CANDIDATE_GATES_CLOSED"
            or manifest.get("frozen") is not True
            or manifest.get("manifest_self_hash_contract")
            != "sha256(canonical UTF-8 JSON with manifest_self_sha256 set to null)"
            or manifest.get("manifest_self_sha256")
            != EXPECTED_MANIFEST_SELF_SHA256
            or self._manifest_self_hash(manifest) != EXPECTED_MANIFEST_SELF_SHA256
            or manifest.get("fixed_counts") != EXPECTED_COUNTS
            or manifest.get("metric_semantics") != EXPECTED_AGGREGATE_EFFECT
            or manifest.get("osv_profile_binding") is not None
        ):
            raise MasterVisualScanAliasError(
                "master_alias_manifest_invalid", "manifest identity or semantics drifted"
            )
        _false_gates(manifest.get("authority_gates"), "manifest")
        output_bindings = self._binding_index(
            manifest.get("output_bindings"),
            "output",
            expected_count=len(EXPECTED_OUTPUT_FILES),
            one_component=True,
        )
        if set(output_bindings) != EXPECTED_OUTPUT_FILES:
            raise MasterVisualScanAliasError(
                "master_alias_binding_invalid", "output closure drifted"
            )
        try:
            product_children = list(self.product_root.iterdir())
        except OSError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_file_read_failed", "product directory is unavailable"
            ) from exc
        expected_files = set(EXPECTED_OUTPUT_FILES) | {"manifest.json"}
        if (
            {child.name for child in product_children} != expected_files
            or any(child.is_symlink() or not child.is_file() for child in product_children)
        ):
            raise MasterVisualScanAliasError(
                "master_alias_binding_invalid",
                "recursive product output closure contains an unbound entry",
            )
        output_bytes = self._verify_bindings(
            self.product_root, output_bindings, "output"
        )
        if manifest.get("evidence_manifest_binding") != output_bindings.get(
            "evidence_manifest.json"
        ):
            raise MasterVisualScanAliasError(
                "master_alias_binding_invalid", "evidence manifest binding drifted"
            )

        evidence_manifest = _json_object(
            output_bytes["evidence_manifest.json"], "evidence manifest"
        )
        if (
            set(evidence_manifest)
            != {
                "schema_version",
                "product_id",
                "source_binding_count",
                "source_bindings",
                "record_evidence_counts",
                "closure_contract",
                "osv_profile_binding",
                "authority_gates",
            }
            or evidence_manifest.get("schema_version")
            != "1.0.0-master-visual-scan-alias-evidence-manifest"
            or evidence_manifest.get("product_id") != PRODUCT_ID
            or evidence_manifest.get("source_binding_count") != 44
            or evidence_manifest.get("record_evidence_counts")
            != EXPECTED_RECORD_EVIDENCE_COUNTS
            or evidence_manifest.get("closure_contract") != EXPECTED_CLOSURE_CONTRACT
            or evidence_manifest.get("osv_profile_binding") is not None
        ):
            raise MasterVisualScanAliasError(
                "master_alias_evidence_manifest_invalid",
                "external evidence manifest semantics drifted",
            )
        _false_gates(evidence_manifest.get("authority_gates"), "evidence manifest")
        source_bindings = self._binding_index(
            evidence_manifest.get("source_bindings"),
            "external evidence",
            expected_count=44,
            one_component=False,
            with_roles=True,
        )
        if list(source_bindings) != sorted(source_bindings) or any(
            path == "catalog.csv"
            or path == "kb/coverage"
            or path.startswith("kb/coverage/")
            for path in source_bindings
        ):
            raise MasterVisualScanAliasError(
                "master_alias_evidence_manifest_invalid",
                "external evidence closure is unsorted or dynamic",
            )
        source_bytes = self._verify_bindings(
            self.shchem_root, source_bindings, "external evidence"
        )

        coverage = _json_object(output_bytes["coverage_report.json"], "coverage")
        if (
            coverage.get("product_id") != PRODUCT_ID
            or coverage.get("schema_version")
            != "1.0.0-master-visual-scan-alias-coverage"
            or coverage.get("status")
            != "PASS_ALIAS_COVERAGE_CANDIDATE_GATES_CLOSED"
            or coverage.get("counts") != EXPECTED_COUNTS
            or coverage.get("expected_aggregate_effect_after_separate_integration")
            != EXPECTED_AGGREGATE_EFFECT
            or coverage.get("semantics")
            != {
                "alias_is_not_exact_crosswalk": True,
                "alias_is_not_master_direct_scan": True,
                "existing_pt52_visual_scan_reused_as_coverage_evidence": True,
                "one_to_many_is_not_merged_into_fake_atomic": True,
                "source_pixels_reusable_or_publishable": False,
            }
            or coverage.get("osv_profile_binding") is not None
            or coverage.get("unique_target_wave_scan_atomic_part_ids")
            != sorted(EXPECTED_TARGET_WAVE_IDS)
            or coverage.get("unique_target_printed_question_ids")
            != sorted(EXPECTED_TARGET_PRINTED_IDS)
        ):
            raise MasterVisualScanAliasError(
                "master_alias_coverage_invalid", "coverage report drifted"
            )
        _false_gates(coverage.get("authority_gates"), "coverage")

        schema = _json_object(output_bytes["alias_record_schema.json"], "alias schema")
        records = _jsonl_objects(output_bytes["alias_records.jsonl"], "alias records")
        try:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
        except Exception as exc:
            raise MasterVisualScanAliasError(
                "master_alias_schema_invalid", "alias record schema is invalid"
            ) from exc
        for index, record in enumerate(records, 1):
            issue = next(validator.iter_errors(record), None)
            if issue is not None:
                raise MasterVisualScanAliasError(
                    "master_alias_record_invalid",
                    f"alias record {index} failed its bound Draft 2020-12 schema",
                )
        master_ids = [record.get("master", {}).get("atomic_part_id") for record in records]
        alias_ids = [record.get("alias_record_id") for record in records]
        if (
            len(records) != 14
            or tuple(master_ids) != EXPECTED_MASTER_NODE_IDS
            or len(set(alias_ids)) != 14
            or len(set(master_ids)) != 14
            or sum(record.get("relation_type") == "alias_coverage_1_to_1" for record in records)
            != 8
            or sum(record.get("relation_type") == "alias_coverage_1_to_many" for record in records)
            != 6
        ):
            raise MasterVisualScanAliasError(
                "master_alias_count_mismatch",
                "the exact 14-node alias set or cardinality distribution drifted",
            )
        record_paths = set(_iter_local_paths(records))
        if not record_paths.issubset(source_bindings):
            raise MasterVisualScanAliasError(
                "master_alias_evidence_manifest_invalid",
                "a record path leaves the external evidence closure",
            )
        serialized_product = json.dumps(
            [manifest, evidence_manifest, coverage, records], ensure_ascii=False
        )
        if (
            "OSV1-2024-PUTUO-ERMO-5T" in serialized_product
            or '"reference_summary_zh"' in serialized_product
            or '"answer_text"' in serialized_product
        ):
            raise MasterVisualScanAliasError(
                "master_alias_boundary_invalid",
                "the frozen alias product contains a forbidden OSV or answer-text claim",
            )

        try:
            master_identity = self.master_workbench.visual_scan_identity_index()
        except MasterWave1WorkbenchError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_master_identity_unavailable",
                "verified Master470/exact169 identity is unavailable",
            ) from exc
        master_set = set(master_ids)
        if (
            not master_set.issubset(master_identity["master_atomic_ids"])
            or master_set & set(master_identity["exact_master_ids"])
        ):
            raise MasterVisualScanAliasError(
                "master_alias_master_identity_invalid",
                "an alias is absent from Master470 or overlaps exact169",
            )
        pt_reader = _BatchQuestionVisualScanReader(self.shchem_root, PT_BATCH_SPEC)
        try:
            pt_snapshot = pt_reader._snapshot()
        except QuestionVisualScanError as exc:
            raise MasterVisualScanAliasError(
                "master_alias_pt52_unavailable",
                "the fully validated PT52 scan is unavailable",
            ) from exc
        if (
            pt_snapshot.manifest_self_sha256
            != PT_BATCH_SPEC.expected_manifest_self_sha256
            or not EXPECTED_TARGET_WAVE_IDS.issubset(pt_snapshot.by_node_id)
        ):
            raise MasterVisualScanAliasError(
                "master_alias_pt52_unavailable", "PT52 target identity drifted"
            )

        master_atomic_path = (
            "kb/classification/theme_hierarchy_master_index_v1_2026-08-03/"
            "atomic_part_records.jsonl"
        )
        counterpart_rows = {
            row["part_id"]: row
            for row in _jsonl_objects(
                source_bytes[master_atomic_path], "Master atomic evidence"
            )
        }
        image_cache: dict[str, Image.Image] = {}
        for record, master_id in zip(records, master_ids, strict=True):
            self._validate_record(
                record,
                _EXPECTED[master_id],
                source_bindings,
                source_bytes,
                pt_snapshot,
                counterpart_rows,
                image_cache,
            )
        return _Snapshot(
            records=tuple(records),
            by_master_id={record["master"]["atomic_part_id"]: record for record in records},
            source_bindings=source_bindings,
            source_bytes=source_bytes,
            pt_snapshot=pt_snapshot,
            pt_reader=pt_reader,
            master_manifest_self_sha256=str(
                master_identity["integrity"]["manifest_self_sha256"]
            ),
            pt_manifest_self_sha256=pt_snapshot.manifest_self_sha256,
        )

    def _snapshot(self) -> _Snapshot:
        try:
            return self._validated_snapshot()
        except MasterVisualScanAliasError:
            raise
        except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
            raise MasterVisualScanAliasError(
                "master_alias_data_invalid",
                "alias structural or semantic invariant failed closed",
            ) from exc

    @staticmethod
    def _integrity(snapshot: _Snapshot) -> dict[str, Any]:
        return {
            "manifest_self_sha256": EXPECTED_MANIFEST_SELF_SHA256,
            "manifest_file_sha256": EXPECTED_MANIFEST_FILE_SHA256,
            "master_manifest_self_sha256": snapshot.master_manifest_self_sha256,
            "pt52_manifest_self_sha256": snapshot.pt_manifest_self_sha256,
            "output_binding_count": len(EXPECTED_OUTPUT_FILES),
            "source_binding_count": 44,
            "record_count": 14,
            "hash_verified_on_read": True,
            "semantic_invariants_verified_on_read": True,
            "master_membership_verified_on_read": True,
            "exact169_disjoint_verified_on_read": True,
            "pt52_targets_verified_on_read": True,
            "legacy_pixel_metrics_recomputed_on_read": True,
            "fail_closed": True,
        }

    @staticmethod
    def _catalog_answer_units(record: dict[str, Any], snapshot: _Snapshot) -> list[dict[str, Any]]:
        note = record["answer_projection"].get("quality_note_nonblocking_zh")
        units: list[dict[str, Any]] = []
        for wave_id in record["target"]["wave_scan_atomic_part_ids"]:
            target = snapshot.pt_snapshot.by_node_id[wave_id]
            metadata = reference_answer_catalog_metadata(
                target["answer"], target["risks_and_limits"]
            )
            if note is not None:
                metadata["has_quality_note"] = True
            units.append({"wave_atomic_part_id": wave_id, **metadata})
        return units

    @staticmethod
    def _assert_catalog_has_no_answer_text(value: Any) -> None:
        if isinstance(value, dict):
            forbidden = {
                "reference_answer_text",
                "merged_reference_answer_text",
                "reference_summary_zh",
                "answer_text",
            }
            if forbidden & set(value):
                raise MasterVisualScanAliasError(
                    "master_alias_catalog_leak",
                    "catalog projection contains answer正文",
                )
            for nested in value.values():
                MasterVisualScanAliasReader._assert_catalog_has_no_answer_text(nested)
        elif isinstance(value, list):
            for nested in value:
                MasterVisualScanAliasReader._assert_catalog_has_no_answer_text(nested)

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        return {
            "product_id": PRODUCT_ID,
            "scope": SCOPE,
            "coverage_kind": COVERAGE_KIND,
            "status": "alias_coverage_completed",
            "counts": dict(EXPECTED_COUNTS),
            "new_physical_scans": 0,
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def catalog(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        items: list[dict[str, Any]] = []
        for record in snapshot.records:
            master_id = record["master"]["atomic_part_id"]
            target = record["target"]
            answer_units = self._catalog_answer_units(record, snapshot)
            items.append(
                {
                    "master_node_id": master_id,
                    "coverage_kind": COVERAGE_KIND,
                    "new_physical_scans": 0,
                    "relation_type": record["relation_type"],
                    "cardinality": target["cardinality"],
                    "target_atomic_part_ids": list(
                        target["wave_scan_atomic_part_ids"]
                    ),
                    "target_atomic_count": len(target["wave_scan_atomic_part_ids"]),
                    "target_printed_question_id": target["printed_question_id"],
                    "question_crop_id": target["question_crop"]["crop_id"],
                    "question_pixels_available": True,
                    "detail_available": True,
                    "reference_answer_units": answer_units,
                    # The parent flag is a catalog summary of every projected
                    # atomic answer unit.  It must include quality notes already
                    # present on the PT52 target scans, not only notes introduced
                    # by the alias record itself.
                    "has_quality_note": any(
                        unit["has_quality_note"] for unit in answer_units
                    ),
                }
            )
        response = {
            "product_id": PRODUCT_ID,
            "scope": SCOPE,
            "coverage_kind": COVERAGE_KIND,
            "new_physical_scans": 0,
            "master_node_ids": [item["master_node_id"] for item in items],
            "items": items,
            "count": len(items),
            "counts": dict(EXPECTED_COUNTS),
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }
        self._assert_catalog_has_no_answer_text(response)
        return response

    @staticmethod
    def _target_detail(
        wave_id: str,
        target_record: dict[str, Any],
        quality_note_nonblocking_zh: str | None,
    ) -> dict[str, Any]:
        answer = project_reference_answer(
            target_record["answer"], target_record["risks_and_limits"]
        )
        if quality_note_nonblocking_zh is not None:
            answer["quality_note"] = quality_note_nonblocking_zh
        candidate = target_record["candidate_analysis"]
        return {
            "wave_atomic_part_id": wave_id,
            "scan_status": target_record["scan_status"],
            "hierarchy": deepcopy(target_record["hierarchy"]),
            "visible_summary_zh": target_record["visible_summary_zh"],
            "response_requirement_zh": target_record["response_requirement_zh"],
            "dependency": deepcopy(target_record["dependency"]),
            "scan_classification": deepcopy(target_record["classification"]),
            "cognitive_difficulty": deepcopy(target_record["difficulty"]),
            "chemistry_observations": deepcopy(
                target_record["chemistry_observations"]
            ),
            "model_candidate_analysis": {
                "review_state_zh": "待复核候选分析",
                "solution_path_zh": deepcopy(candidate["solution_path_zh"]),
                "candidate_only": True,
                "correctness_verified": False,
            },
            "risks_and_limits": deepcopy(target_record["risks_and_limits"]),
            "reference_answer": answer,
        }

    def detail(self, master_node_id: str) -> dict[str, Any]:
        try:
            validate_identifier(master_node_id, "master_node_id")
        except (SecurityError, TypeError) as exc:
            raise MasterVisualScanAliasError(
                "master_alias_invalid_node_id", "Master alias ID is invalid", 400
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise MasterVisualScanAliasError(
                "master_alias_node_not_found", "Master alias was not found", 404
            )
        target = record["target"]
        note = record["answer_projection"].get("quality_note_nonblocking_zh")
        target_units = [
            self._target_detail(
                wave_id,
                snapshot.pt_snapshot.by_node_id[wave_id],
                note if len(target["wave_scan_atomic_part_ids"]) == 1 else None,
            )
            for wave_id in target["wave_scan_atomic_part_ids"]
        ]
        crop = target["question_crop"]
        return {
            "product_id": PRODUCT_ID,
            "scope": SCOPE,
            "coverage_kind": COVERAGE_KIND,
            "new_physical_scans": 0,
            "master_node_id": master_node_id,
            "alias_record_id": record["alias_record_id"],
            "relation_type": record["relation_type"],
            "mapping_method": record["mapping_method"],
            "cardinality": target["cardinality"],
            "master_parent_chain": deepcopy(record["master"]["parent_chain"]),
            "target_paper_id": target["paper_id"],
            "target_theme_id": target["theme_id"],
            "target_printed_question_id": target["printed_question_id"],
            "target_atomic_part_ids": list(target["wave_scan_atomic_part_ids"]),
            "target_atomic_parts": target_units,
            "question_evidence": {
                "crop_id": crop["crop_id"],
                "source_page": crop["source_page_number"],
                "width": crop["crop_dimensions"][0],
                "height": crop["crop_dimensions"][1],
                "bytes": crop["crop_bytes"],
                "sha256": crop["crop_sha256"],
                "content_type": "image/png",
                "access": "teacher_loopback_read_only",
            },
            "answer_projection": {
                "merge_prohibited_for_one_to_many": len(target_units) > 1,
                "merged_reference_answer_text": None,
                "unit_count": len(target_units),
                "quality_note_nonblocking_zh": note,
                "independently_verified": False,
                "source_authority": "nonofficial_reference",
            },
            "authority": dict(AUTHORITY),
            "integrity": self._integrity(snapshot),
        }

    def question_crop(
        self, master_node_id: str, crop_id: str
    ) -> CandidateCropPayload:
        try:
            validate_identifier(master_node_id, "master_node_id")
            validate_identifier(crop_id, "crop_id")
        except (SecurityError, TypeError) as exc:
            raise MasterVisualScanAliasError(
                "master_alias_invalid_identifier",
                "Master alias or crop ID is invalid",
                400,
            ) from exc
        snapshot = self._snapshot()
        record = snapshot.by_master_id.get(master_node_id)
        if record is None:
            raise MasterVisualScanAliasError(
                "master_alias_node_not_found", "Master alias was not found", 404
            )
        crop = record["target"]["question_crop"]
        if crop_id != crop["crop_id"]:
            # The alias endpoint serves exactly one frozen question crop.  This
            # deliberately denies answer, shared, unrelated, and guessed IDs.
            raise MasterVisualScanAliasError(
                "master_alias_crop_role_denied",
                "only the frozen question crop can be served",
                403,
            )
        raw = snapshot.source_bytes.get(crop["crop_path"])
        binding = snapshot.source_bindings.get(crop["crop_path"])
        if (
            raw is None
            or binding is None
            or binding.get("roles") != ["target_question_crop"]
            or len(raw) != crop["crop_bytes"]
            or _sha256(raw) != crop["crop_sha256"]
            or len(raw) > MAX_CROP_BYTES
        ):
            raise MasterVisualScanAliasError(
                "master_alias_crop_binding_invalid", "question crop bytes drifted"
            )
        self._load_rgb(raw, "question crop")
        return CandidateCropPayload(data=raw, sha256=crop["crop_sha256"])


__all__ = [
    "AUTHORITY",
    "COVERAGE_KIND",
    "EXPECTED_COUNTS",
    "EXPECTED_MANIFEST_FILE_SHA256",
    "EXPECTED_MANIFEST_SELF_SHA256",
    "EXPECTED_MASTER_NODE_IDS",
    "MasterVisualScanAliasError",
    "MasterVisualScanAliasReader",
    "PRODUCT_ID",
    "PRODUCT_RELATIVE",
    "SCOPE",
]
