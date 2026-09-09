from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from .core import sha256_file
from .documents import docx_visible_text

WORKSPACE = Path(__file__).resolve().parents[2]

R17_QA_REPORT_NAMES = (
    "schema.json",
    "conservation.json",
    "inverse.json",
    "components.json",
    "figure.json",
    "dedup.json",
    "versioned_schema.json",
    "coverage_matrix.json",
    "coverage_matrix_validation.json",
    "figure_topology_mutations.json",
    "controller_machine_pass.json",
    "controller_content_governance.json",
    "controller_post_generation_preflight.json",
    "parity_exam_student.json",
    "parity_exam_solutions.json",
    "parity_week_student.json",
    "parity_week_solutions.json",
    "privacy_delivery_artifacts.json",
)
R17_QA_REPORT_COUNT = 18


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(WORKSPACE.resolve()).as_posix()
    except ValueError:
        return "external-path-redacted"


def _safe_portable_path(path: Path) -> str:
    value = _portable_path(path)
    if value == "external-path-redacted" or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError(f"path must be inside workspace: {path}")
    return value


def _render_tree_sha256(rows: list[dict]) -> str:
    payload = [
        {"page_number": row["page_number"], "relative_path": row["relative_path"], "sha256": row["sha256"], "bytes": row["bytes"]}
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


FORBIDDEN_TEXT = (
    "TODO",
    "TBD",
    "待补",
    "占位符",
    "human_reviewed=true",
    "教师审核通过",
    "专家审定",
    "官方采分点：",
)


def _pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _pdf_image_count(path: Path) -> int:
    reader = PdfReader(str(path))
    count = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        if not resources or "/XObject" not in resources:
            continue
        xobjects = resources["/XObject"].get_object()
        for item in xobjects.values():
            obj = item.get_object()
            if obj.get("/Subtype") == "/Image":
                count += 1
    return count


def _docx_image_count(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(1 for name in archive.namelist() if name.startswith("word/media/"))


def _docx_alt_text(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return re.findall(r'(?:descr|title)="([^"]+)"', xml)


def validate_docx_pdf_parity(
    *,
    docx_path: Path,
    pdf_path: Path,
    render_dir: Path,
    version_id: str,
    required_markers: list[str],
    expected_figure_id: str | None,
    semantic_kind: str,
) -> dict:
    errors: list[str] = []
    docx_text = docx_visible_text(docx_path)
    pdf_text_raw = _pdf_text(pdf_path)
    pdf_text = _normalize(pdf_text_raw)
    for marker in [version_id, *required_markers]:
        normalized_marker = _normalize(marker)
        if normalized_marker not in docx_text:
            errors.append(f"DOCX missing marker: {marker}")
        if normalized_marker not in pdf_text:
            errors.append(f"PDF missing marker: {marker}")
    for forbidden in FORBIDDEN_TEXT:
        if forbidden.lower() in (docx_text + pdf_text).lower():
            errors.append(f"forbidden placeholder/review claim: {forbidden}")

    docx_images = _docx_image_count(docx_path)
    pdf_images = _pdf_image_count(pdf_path)
    alt_text = _docx_alt_text(docx_path)
    if expected_figure_id:
        expected_alt = f"SH_CHEM_FIGURE:{expected_figure_id}"
        if expected_alt not in alt_text:
            errors.append(f"missing DOCX figure alt text: {expected_alt}")
        if docx_images < 1 or pdf_images < 1:
            errors.append("apparatus figure missing in DOCX or PDF")
    elif docx_images:
        errors.append("unexpected image in text-only weekpack")

    reader = PdfReader(str(pdf_path))
    render_root = _safe_portable_path(render_dir)
    discovered: list[tuple[int, Path]] = []
    for path in render_dir.glob("page-*.png"):
        match = re.fullmatch(r"page-(\d+)\.png", path.name)
        if not match:
            errors.append(f"noncanonical rendered page filename: {path.name}")
            continue
        discovered.append((int(match.group(1)), path))
    discovered.sort(key=lambda row: row[0])
    expected_page_numbers = list(range(1, len(reader.pages) + 1))
    if [number for number, _ in discovered] != expected_page_numbers:
        errors.append(
            f"rendered pages must be exact contiguous set 1..{len(reader.pages)}: "
            f"{[number for number, _ in discovered]}"
        )
    page_pngs = [path for _, path in discovered]
    if len(reader.pages) != len(page_pngs):
        errors.append(f"rendered page mismatch: PDF={len(reader.pages)}, PNG={len(page_pngs)}")
    page_checks = []
    for page_index, path in enumerate(page_pngs):
        image = Image.open(path).convert("L")
        inverse = image.point(lambda value: 255 - value)
        bbox = inverse.getbbox()
        nonwhite = sum(1 for value in image.getdata() if value < 245)
        page_text = reader.pages[page_index].extract_text() or ""
        if version_id not in page_text or "机器审核候选" not in page_text:
            errors.append(f"missing conservative header on rendered page: {path.name}")
        if "第" not in page_text or "页" not in page_text:
            errors.append(f"missing page footer on rendered page: {path.name}")
        body_text = page_text.replace(version_id, "")
        body_text = re.sub(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页", "", body_text)
        body_text = body_text.replace(
            "机器审核候选｜教师私域交付候选（非官方、非公开出版）｜", ""
        )
        body_text_length = len(_normalize(body_text))
        ok = bool(bbox) and nonwhite > 1500 and body_text_length >= 20
        if bbox and (bbox[0] < 3 or bbox[1] < 3 or bbox[2] > image.width - 3 or bbox[3] > image.height - 3):
            ok = False
        if not ok:
            errors.append(f"blank or clipped rendered page: {path.name}")
        page_checks.append(
            {
                "page_number": page_index + 1,
                "relative_path": path.name,
                "size": [image.width, image.height],
                "bbox": list(bbox) if bbox else None,
                "nonwhite_pixels": nonwhite,
                "body_text_length": body_text_length,
                "status": "pass" if ok else "fail",
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )

    render_total_bytes = sum(row["bytes"] for row in page_checks)
    render_tree_sha256 = _render_tree_sha256(page_checks)

    return {
        "check": "docx_pdf_full_page_parity",
        "schema_version": "1.0.0",
        "semantic_kind": semantic_kind,
        "status": "pass" if not errors else "fail",
        "docx": {"path": _portable_path(docx_path), "sha256": sha256_file(docx_path), "image_count": docx_images},
        "pdf": {"path": _portable_path(pdf_path), "sha256": sha256_file(pdf_path), "page_count": len(reader.pages), "image_count": pdf_images},
        "render_root": render_root,
        "render_root_tree_sha256": render_tree_sha256,
        "render_root_total_bytes": render_total_bytes,
        "render_root_page_count": len(page_checks),
        "rendered_pages": page_checks,
        "required_markers": required_markers,
        "errors": errors,
    }


def validate_parity_report_artifacts(report: dict) -> dict:
    errors: list[str] = []
    raw_root = report.get("render_root")
    if not isinstance(raw_root, str) or raw_root == "external-path-redacted":
        return {"status": "fail", "errors": ["render_root is not a portable workspace-relative path"]}
    root_path = Path(raw_root)
    if root_path.is_absolute() or root_path.drive or ".." in root_path.parts:
        return {"status": "fail", "errors": ["render_root is unsafe"]}
    render_root = (WORKSPACE / root_path).resolve()
    try:
        render_root.relative_to(WORKSPACE.resolve())
    except ValueError:
        return {"status": "fail", "errors": ["render_root escapes workspace"]}
    rows = report.get("rendered_pages", [])
    if not isinstance(rows, list) or not rows:
        return {"status": "fail", "errors": ["rendered_pages is empty or invalid"]}
    page_count = report.get("render_root_page_count")
    expected_numbers = list(range(1, len(rows) + 1))
    numbers = [row.get("page_number") for row in rows if isinstance(row, dict)]
    names = [row.get("relative_path") for row in rows if isinstance(row, dict)]
    expected_names = [f"page-{number}.png" for number in expected_numbers]
    if page_count != len(rows) or report.get("pdf", {}).get("page_count") != len(rows):
        errors.append("render page count does not exactly equal PDF and row counts")
    if numbers != expected_numbers or names != expected_names:
        errors.append("rendered page set is not canonical, ordered and contiguous")
    content_address = report.get("render_tree_content_address")
    if content_address is not None:
        expected_address = f"sha256:{report.get('render_root_tree_sha256')}"
        if content_address != expected_address:
            errors.append("render_tree_content_address mismatch")
        if tuple(render_root.parts[-2:]) != (
            "sha256",
            str(report.get("render_root_tree_sha256")),
        ):
            errors.append("render_root is not located at its content address")
        live_names = sorted(path.name for path in render_root.iterdir()) if render_root.is_dir() else []
    else:
        live_names = (
            sorted(path.name for path in render_root.glob("page-*.png"))
            if render_root.is_dir()
            else []
        )
    if sorted(expected_names) != live_names:
        errors.append("render_root live page set differs from report")
    for row in rows:
        if not isinstance(row, dict):
            errors.append("rendered page row is not an object")
            continue
        relative = Path(str(row.get("relative_path", "")))
        if relative.is_absolute() or relative.drive or len(relative.parts) != 1 or ".." in relative.parts:
            errors.append(f"unsafe rendered page relative path: {relative}")
            continue
        path = render_root / relative
        if not path.is_file():
            errors.append(f"rendered page missing: {relative}")
            continue
        if sha256_file(path) != row.get("sha256") or path.stat().st_size != row.get("bytes"):
            errors.append(f"rendered page hash/bytes mismatch: {relative}")
    if report.get("render_root_total_bytes") != sum(
        int(row.get("bytes", 0)) for row in rows if isinstance(row, dict)
    ):
        errors.append("render_root_total_bytes mismatch")
    if report.get("render_root_tree_sha256") != _render_tree_sha256(rows):
        errors.append("render_root_tree_sha256 mismatch")
    if content_address is not None and any(
        row.get("status") != "pass" for row in rows if isinstance(row, dict)
    ):
        errors.append("every rendered page must have exact PASS status")
    return {"status": "pass" if not errors else "fail", "errors": errors}


def freeze_content_addressed_render_tree(report: dict, *, content_root: Path) -> dict:
    """Copy exact page bytes into ``sha256/<tree-digest>`` and rebind the report."""

    source_validation = validate_parity_report_artifacts(report)
    if source_validation["status"] != "pass":
        raise ValueError(f"source render tree is invalid: {source_validation['errors']}")
    tree_sha = report.get("render_root_tree_sha256")
    if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", tree_sha):
        raise ValueError("render tree SHA-256 is invalid")
    source_root = WORKSPACE / str(report["render_root"])
    destination = content_root / "sha256" / tree_sha
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{tree_sha}.{uuid.uuid4().hex}.pending"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        for row in report["rendered_pages"]:
            source = source_root / row["relative_path"]
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != row["sha256"] or len(payload) != row["bytes"]:
                raise RuntimeError(f"render source changed before content-addressing: {source.name}")
            target = temporary / row["relative_path"]
            target.write_bytes(payload)
        if destination.exists():
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    rebound = dict(report)
    rebound["render_root"] = _safe_portable_path(destination)
    rebound["render_tree_content_address"] = f"sha256:{tree_sha}"
    validation = validate_parity_report_artifacts(rebound)
    if validation["status"] != "pass":
        raise RuntimeError(f"content-addressed render tree validation failed: {validation}")
    return rebound


def validate_r17_manifest_contract(
    manifest: dict,
    *,
    expected_qa_paths: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Enforce count, exact-QA, PASS, and path uniqueness invariants."""

    errors: list[str] = []
    artifacts = manifest.get("artifacts")
    qa_reports = manifest.get("qa_reports")
    if not isinstance(artifacts, list):
        artifacts = []
        errors.append("artifacts must be an array")
    if not isinstance(qa_reports, list):
        qa_reports = []
        errors.append("qa_reports must be an array")
    if manifest.get("artifact_count") != len(artifacts):
        errors.append("artifact_count must exactly equal len(artifacts)")
    if (
        manifest.get("qa_report_count") != len(qa_reports)
        or len(qa_reports) != R17_QA_REPORT_COUNT
    ):
        errors.append("qa_report_count must exactly equal len(qa_reports) == 18")

    artifact_paths = [row.get("path") for row in artifacts if isinstance(row, dict)]
    archive_paths = [row.get("archive_path") for row in artifacts if isinstance(row, dict)]
    if len(artifact_paths) != len(artifacts) or any(not isinstance(path, str) for path in artifact_paths):
        errors.append("every artifact must have a string path")
    if len(archive_paths) != len(artifacts) or any(not isinstance(path, str) for path in archive_paths):
        errors.append("every artifact must have a string archive_path")
    if len({str(path).casefold() for path in artifact_paths}) != len(artifact_paths):
        errors.append("artifact paths must be case-insensitively unique")
    if len({str(path).casefold() for path in archive_paths}) != len(archive_paths):
        errors.append("archive paths must be case-insensitively unique")
    if any(str(path).casefold() == "manifest.json" for path in archive_paths):
        errors.append("artifact archive_path must not shadow manifest.json")

    qa_paths = [row.get("path") for row in qa_reports if isinstance(row, dict)]
    if len(qa_paths) != len(qa_reports):
        errors.append("every QA report row must be an object with a path")
    if expected_qa_paths is not None and qa_paths != list(expected_qa_paths):
        errors.append("QA report paths differ from the exact ordered R17 allowlist")
    if tuple(Path(str(path)).name for path in qa_paths) != R17_QA_REPORT_NAMES:
        errors.append("QA report basenames differ from the exact ordered 18-file allowlist")
    if len({str(path).casefold() for path in qa_paths}) != len(qa_paths):
        errors.append("QA report paths must be unique")
    if any(row.get("status") != "pass" for row in qa_reports if isinstance(row, dict)):
        errors.append("every QA report must have exact PASS status")
    qa_archive_paths = [f"qa/{Path(str(path)).name}" for path in qa_paths]
    for source_path, archive_path in zip(qa_paths, qa_archive_paths, strict=True):
        matches = [
            row
            for row in artifacts
            if isinstance(row, dict)
            and row.get("path") == source_path
            and row.get("archive_path") == archive_path
            and row.get("kind") == "qa_evidence"
        ]
        if len(matches) != 1:
            errors.append(f"QA artifact binding must be exact and unique: {source_path}")
    return {
        "check": "r17_manifest_exact_counts_qa_and_uniqueness",
        "status": "pass" if not errors else "fail",
        "artifact_count": len(artifacts),
        "qa_report_count": len(qa_reports),
        "errors": sorted(dict.fromkeys(errors)),
    }


HOST_PATH_PATTERNS = (
    ("windows_drive_absolute_path", re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")),
    ("windows_user_directory", re.compile(r"(?i)[\\/]Users[\\/]")),
    ("posix_user_directory", re.compile(r"(?i)(?:/home/|/Users/)")),
)

SECRET_PATTERNS = (
    ("api_key", re.compile(r"(?i)(?:api[_-]?key|WEREAD_API_KEY)\s*[:=]\s*[^\s,;]{6,}")),
    ("oauth_secret", re.compile(r"(?i)(?:oauth|client[_-]?secret)\s*[:=]\s*[^\s,;]{6,}")),
    ("access_token", re.compile(r"(?i)(?:access|refresh)[_-]?token\s*[:=]\s*[^\s,;]{6,}")),
    ("bearer_token", re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[^\s,;]{6,}")),
)


def _scan_text(text: str, location: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for finding_type, pattern in (*HOST_PATH_PATTERNS, *SECRET_PATTERNS):
        if pattern.search(text):
            findings.append({"location": location, "finding_type": finding_type})
    return findings


def _scan_docx_bytes(payload: bytes, location: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as nested:
            for name in nested.namelist():
                if name.endswith((".xml", ".rels", ".txt", ".json")):
                    text = nested.read(name).decode("utf-8", errors="replace")
                    findings.extend(_scan_text(text, f"{location}!/{name}"))
    except zipfile.BadZipFile:
        findings.append({"location": location, "finding_type": "invalid_docx_container"})
    return findings


def _scan_pdf_bytes(payload: bytes, location: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    try:
        reader = PdfReader(io.BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        metadata = "\n".join(str(value) for value in (reader.metadata or {}).values())
        findings.extend(_scan_text(text + "\n" + metadata, location))
    except Exception:
        findings.append({"location": location, "finding_type": "invalid_pdf_container"})
    return findings


def scan_archive_privacy_bytes(payload: bytes, *, archive_label: str) -> dict:
    findings: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            name = info.filename
            findings.extend(_scan_text(name, f"member-name:{name}"))
            payload = archive.read(info)
            suffix = Path(name).suffix.casefold()
            if suffix in {".json", ".txt", ".md", ".svg", ".xml", ".rels", ".csv"}:
                findings.extend(_scan_text(payload.decode("utf-8", errors="replace"), name))
            elif suffix == ".docx":
                findings.extend(_scan_docx_bytes(payload, name))
            elif suffix == ".pdf":
                findings.extend(_scan_pdf_bytes(payload, name))
    return {
        "check": "zip_recursive_host_path_and_secret_scan",
        "status": "pass" if not findings else "fail",
        "scanned_archive": archive_label,
        "findings": findings,
        "errors": [
            f"{row['finding_type']}:{row['location']}" for row in findings
        ],
    }


def scan_archive_privacy(archive_path: Path) -> dict:
    payload = archive_path.read_bytes()
    return scan_archive_privacy_bytes(
        payload,
        archive_label=_portable_path(archive_path),
    )


def scan_files_privacy(paths: list[Path]) -> dict:
    findings: list[dict[str, str]] = []
    scanned: list[dict[str, str]] = []
    for path in paths:
        location = _portable_path(path)
        payload = path.read_bytes()
        suffix = path.suffix.casefold()
        scanned.append({"path": location, "sha256": sha256_file(path)})
        findings.extend(_scan_text(path.name, f"file-name:{location}"))
        if suffix in {".json", ".txt", ".md", ".svg", ".xml", ".rels", ".csv"}:
            findings.extend(_scan_text(payload.decode("utf-8", errors="replace"), location))
        elif suffix == ".docx":
            findings.extend(_scan_docx_bytes(payload, location))
        elif suffix == ".pdf":
            findings.extend(_scan_pdf_bytes(payload, location))
    return {
        "check": "delivery_artifact_host_path_and_secret_scan",
        "status": "pass" if not findings else "fail",
        "scanned": scanned,
        "findings": findings,
        "errors": [f"{row['finding_type']}:{row['location']}" for row in findings],
        "human_reviewed": False,
    }


def validate_archive_bytes(
    archive_payload: bytes,
    manifest: dict,
    *,
    archive_label: str,
    manifest_bytes: bytes | None = None,
) -> dict:
    """Validate a delivery archive against one exact controlled manifest snapshot.

    ``manifest`` supplies the already parsed object used by the structural checks.
    ``manifest_bytes`` is the immutable byte snapshot controlled by the caller.  The
    ZIP member must be byte-for-byte identical to it: reparsing to an equal object is
    deliberately insufficient because whitespace/key-order rewrites are still a
    different publication input.  Callers that only have an object are restricted to
    the one serialization emitted by :func:`core.write_json`.
    """

    errors: list[str] = []
    controlled_manifest_bytes = (
        manifest_bytes
        if manifest_bytes is not None
        else (
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
    )
    try:
        controlled_manifest = json.loads(controlled_manifest_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        controlled_manifest = None
        errors.append(f"controlled manifest bytes are not valid JSON: {type(exc).__name__}")
    if controlled_manifest != manifest:
        errors.append("controlled manifest bytes do not decode to supplied manifest object")
    manifest_contract = None
    if manifest.get("schema_version") == "3.0.0-r17":
        manifest_contract = validate_r17_manifest_contract(manifest)
        errors.extend(manifest_contract["errors"])
    manifest_member_bytes_exact = False
    with zipfile.ZipFile(io.BytesIO(archive_payload)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            errors.append("ZIP contains duplicate member names")
        if len(names) != len({name.casefold() for name in names}):
            errors.append("ZIP contains case-colliding member names")
        for name in names:
            path = Path(name)
            if path.is_absolute() or path.drive or ".." in path.parts or "\\" in name:
                errors.append(f"ZIP contains unsafe member path: {name}")
        manifest_infos = [info for info in infos if info.filename == "manifest.json"]
        if len(manifest_infos) != 1:
            errors.append("manifest.json missing from ZIP")
        else:
            archived_manifest_bytes = archive.read(manifest_infos[0])
            manifest_member_bytes_exact = archived_manifest_bytes == controlled_manifest_bytes
            if not manifest_member_bytes_exact:
                errors.append("archived manifest bytes differ from controlled manifest snapshot")
            try:
                archived_manifest = json.loads(archived_manifest_bytes.decode("utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                archived_manifest = None
                errors.append(f"archived manifest is not valid JSON: {type(exc).__name__}")
            if archived_manifest != manifest:
                errors.append("archived manifest object differs from controlled manifest")
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
        expected_names = sorted(
            item["archive_path"]
            for item in artifacts
            if isinstance(item, dict) and isinstance(item.get("archive_path"), str)
        )
        if sorted(name for name in names if name != "manifest.json") != expected_names:
            errors.append("ZIP member list differs from manifest artifacts")
        if len(infos) != len(artifacts) + 1:
            errors.append("ZIP actual member count differs from artifact_count + manifest")
        info_by_name = {info.filename: info for info in infos}
        for item in artifacts:
            name = item["archive_path"]
            info = info_by_name.get(name)
            if info is None:
                continue
            payload = archive.read(info)
            actual = hashlib.sha256(payload).hexdigest()
            if actual != item["sha256"] or len(payload) != item["bytes"]:
                errors.append(f"ZIP digest/size mismatch: {name}")
    privacy = scan_archive_privacy_bytes(
        archive_payload,
        archive_label=archive_label,
    )
    errors.extend(privacy["errors"])
    return {
        "check": "zip_manifest_integrity",
        "status": "pass" if not errors else "fail",
        "archive": archive_label,
        "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
        "manifest_sha256": hashlib.sha256(controlled_manifest_bytes).hexdigest(),
        "manifest_bytes": len(controlled_manifest_bytes),
        "manifest_member_bytes_exact": manifest_member_bytes_exact,
        "member_count": len(infos),
        "expected_member_count": len(manifest.get("artifacts", [])) + 1,
        "manifest_contract": manifest_contract,
        "privacy_scan": privacy,
        "errors": errors,
    }


def validate_archive(
    archive_path: Path,
    manifest: dict,
    *,
    manifest_bytes: bytes | None = None,
) -> dict:
    payload = archive_path.read_bytes()
    if manifest_bytes is None:
        sibling_manifest = archive_path.parent / "manifest.json"
        if sibling_manifest.is_file():
            manifest_bytes = sibling_manifest.read_bytes()
    return validate_archive_bytes(
        payload,
        manifest,
        archive_label=_portable_path(archive_path),
        manifest_bytes=manifest_bytes,
    )
