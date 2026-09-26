#!/usr/bin/env python3
"""Bootstrap/check legacy non-recognition image utilities.

No text-recognition executable or language data is installed, discovered, or
required.  Wheels are verified then unpacked into ``.runtime/python``.
``check`` is network-free and fails closed.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "dependency-lock.json"
RUNTIME_ROOT = HERE / ".runtime"
CACHE_ROOT = RUNTIME_ROOT / "cache"
PYTHON_ROOT = RUNTIME_ROOT / "python"
STATE_PATH = RUNTIME_ROOT / "bootstrap-state.json"


class BootstrapError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_verified(artifact: dict[str, Any], offline: bool) -> Path:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    target = CACHE_ROOT / artifact["filename"]
    if target.is_file() and sha256_file(target) == artifact["sha256"]:
        return target
    if target.exists():
        target.unlink()
    if offline:
        raise BootstrapError(f"offline cache miss: {artifact['filename']}")
    partial = target.with_suffix(target.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    request = urllib.request.Request(artifact["url"], headers={"User-Agent": "student-learning-v1-local-bootstrap"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    actual = sha256_file(partial)
    if actual != artifact["sha256"]:
        partial.unlink(missing_ok=True)
        raise BootstrapError(f"hash mismatch for {artifact['filename']}: {actual}")
    if "byte_size" in artifact and partial.stat().st_size != artifact["byte_size"]:
        partial.unlink(missing_ok=True)
        raise BootstrapError(f"size mismatch for {artifact['filename']}")
    partial.replace(target)
    return target


def install_wheel(path: Path) -> None:
    PYTHON_ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as archive:
        for member in archive.infolist():
            destination = (PYTHON_ROOT / member.filename).resolve()
            if PYTHON_ROOT.resolve() not in destination.parents and destination != PYTHON_ROOT.resolve():
                raise BootstrapError(f"wheel path escape: {member.filename}")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if _installed_member_matches(destination, member):
                continue
            partial = destination.with_name(destination.name + ".installing")
            partial.unlink(missing_ok=True)
            with archive.open(member, "r") as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target)
            if not _installed_member_matches(partial, member):
                partial.unlink(missing_ok=True)
                raise BootstrapError(f"wheel member verification failed: {member.filename}")
            try:
                os.replace(partial, destination)
            except OSError as exc:
                partial.unlink(missing_ok=True)
                raise BootstrapError(
                    f"wheel member replacement blocked:{member.filename}:{type(exc).__name__}"
                ) from exc


def _installed_member_matches(path: Path, member: zipfile.ZipInfo) -> bool:
    if not path.is_file() or path.stat().st_size != member.file_size:
        return False
    checksum = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum = binascii.crc32(block, checksum)
    return checksum & 0xFFFFFFFF == member.CRC


def wheel_install_errors(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                destination = (PYTHON_ROOT / member.filename).resolve()
                if PYTHON_ROOT.resolve() not in destination.parents:
                    errors.append(f"path_escape:{member.filename}")
                elif not _installed_member_matches(destination, member):
                    errors.append(f"missing_or_mismatch:{member.filename}")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"wheel_unreadable:{type(exc).__name__}")
    return errors


def probe_local_runtime() -> tuple[dict[str, Any] | None, str | None]:
    """Import native modules in a fresh process and require clean JSON stdout."""
    probe = r'''
import json, pathlib, sys
runtime = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(runtime))
import cv2
import zxingcpp
import numpy
from PIL import Image
cv2_path = pathlib.Path(cv2.__file__).resolve()
zxing_path = pathlib.Path(zxingcpp.__file__).resolve()
cascade_path = pathlib.Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
storage = cv2.FileStorage(
    cascade_path.read_text(encoding="utf-8"),
    cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY,
)
cascade = cv2.CascadeClassifier()
haar_ready = (
    cascade_path.is_file()
    and storage.isOpened()
    and cascade.read(storage.getFirstTopLevelNode())
    and not cascade.empty()
)
inside = lambda path: runtime == path or runtime in path.parents
print(json.dumps({
    "cv2_version": cv2.__version__,
    "cv2_path": str(cv2_path),
    "cv2_local": inside(cv2_path),
    "zxingcpp_version": getattr(zxingcpp, "__version__", None),
    "zxingcpp_path": str(zxing_path),
    "zxingcpp_local": inside(zxing_path),
    "numpy_version": numpy.__version__,
    "pillow_version": getattr(Image, "__version__", None),
    "haar_cascade_path": str(cascade_path),
    "haar_face_backend_ready": haar_ready,
}, ensure_ascii=False, sort_keys=True))
'''
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", probe, str(PYTHON_ROOT)],
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    try:
        stdout = process.stdout.decode("utf-8", errors="strict")
        stderr = process.stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, "probe_output_not_utf8"
    if process.returncode != 0:
        return None, f"probe_failed:{process.returncode}:{stderr.strip()[:160]}"
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None, "probe_stdout_not_single_json_document"
    if not isinstance(value, dict):
        return None, "probe_json_not_object"
    return value, None


def check() -> dict[str, Any]:
    lock = read_json(LOCK_PATH)
    checks: dict[str, Any] = {}
    blockers: list[str] = []
    if platform.system() != "Windows" or platform.machine().casefold() not in {"amd64", "x86_64"}:
        blockers.append("locked_runtime_platform_mismatch")
    if sys.version_info[:2] != (3, 12):
        blockers.append("locked_runtime_python_abi_mismatch")
    for artifact in lock["artifacts"]:
        target = CACHE_ROOT / artifact["filename"]
        valid = target.is_file() and sha256_file(target) == artifact["sha256"]
        checks[f"artifact:{artifact['name']}"] = {"status": "pass" if valid else "fail", "path": str(target), "sha256": sha256_file(target) if target.is_file() else None}
        if not valid:
            blockers.append(f"artifact_invalid_or_missing:{artifact['name']}")
        elif artifact["kind"] == "python_wheel":
            install_errors = wheel_install_errors(target)
            checks[f"installed-wheel:{artifact['name']}"] = {
                "status": "pass" if not install_errors else "fail",
                "error_count": len(install_errors),
                "errors": install_errors[:8],
            }
            if install_errors:
                blockers.append(f"installed_wheel_incomplete:{artifact['name']}")
    checks["text_recognition_runtime"] = {
        "status": "disabled",
        "required": False,
        "production_entry_reachable": False,
    }
    probe, probe_error = probe_local_runtime()
    checks["isolated_import_probe"] = {
        "status": "pass" if probe_error is None else "fail",
        "stdout_contract": "single_json_document_no_library_noise",
        "reason": probe_error,
    }
    if probe_error is not None or probe is None:
        blockers.append("isolated_local_runtime_import_failed")
    else:
        checks["module:cv2"] = {
            "status": "pass" if probe.get("cv2_local") is True else "fail",
            "version": probe.get("cv2_version"),
            "path": probe.get("cv2_path"),
        }
        checks["module:zxingcpp"] = {
            "status": "pass" if probe.get("zxingcpp_local") is True else "fail",
            "version": probe.get("zxingcpp_version"),
            "path": probe.get("zxingcpp_path"),
        }
        checks["module:numpy"] = {"status": "pass", "version": probe.get("numpy_version")}
        checks["module:Pillow"] = {"status": "pass", "version": probe.get("pillow_version")}
        checks["face_detector_backend"] = {
            "status": "pass" if probe.get("haar_face_backend_ready") is True else "fail",
            "backend": "opencv_haar_frontalface_default",
            "path": probe.get("haar_cascade_path"),
        }
        if probe.get("cv2_local") is not True:
            blockers.append("cv2_import_not_project_local")
        if probe.get("zxingcpp_local") is not True:
            blockers.append("zxingcpp_import_not_project_local")
        if probe.get("haar_face_backend_ready") is not True:
            blockers.append("opencv_haar_face_backend_unavailable")
    status = {
        "schema_version": "student_learning_local_runtime_state_v1",
        "status": "ready_local_only" if not blockers else "blocked_uncertain",
        "egress_detector_runtime_ready": not blockers,
        "runtime_root": str(RUNTIME_ROOT),
        "global_environment_mutated": False,
        "checks": checks,
        "blockers": blockers,
        "dependency_lock_sha256": sha256_file(LOCK_PATH),
    }
    return status


def bootstrap(offline: bool) -> dict[str, Any]:
    lock = read_json(LOCK_PATH)
    if lock["platform"] != "win_amd64" or lock["python_abi"] != "cp312":
        raise BootstrapError("unsupported dependency lock")
    # A verified current runtime is already the desired idempotent result. This
    # also avoids trying to delete a cv2 DLL that another local worker may have
    # loaded on Windows while parallel tests are running.
    if PYTHON_ROOT.is_dir():
        current = check()
        if current["status"] == "ready_local_only":
            RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return current
    artifacts = {item["name"]: item for item in lock["artifacts"]}
    if PYTHON_ROOT.exists():
        resolved = PYTHON_ROOT.resolve()
        if resolved.parent != RUNTIME_ROOT.resolve():
            raise BootstrapError("refusing unsafe local runtime replacement")
        # Repair from the pinned wheels in place. Matching members (including
        # a loaded cv2.pyd) are verified and left untouched; missing/mismatched
        # members are atomically replaced. The final whole-wheel member audit
        # and isolated import probe remain the authority for readiness.
    for name in ("opencv-python-headless", "zxing-cpp"):
        install_wheel(download_verified(artifacts[name], offline))
    state = check()
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if state["status"] != "ready_local_only":
        raise BootstrapError("local runtime check failed: " + ", ".join(state["blockers"]))
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("bootstrap", "check"))
    parser.add_argument("--offline", action="store_true", help="never use network; require a complete verified cache")
    args = parser.parse_args()
    try:
        state = bootstrap(args.offline) if args.command == "bootstrap" else check()
    except BootstrapError as exc:
        print(json.dumps({"status": "blocked_uncertain", "error": str(exc), "global_environment_mutated": False}, ensure_ascii=False, indent=2))
        return 3
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["status"] == "ready_local_only" else 3


if __name__ == "__main__":
    raise SystemExit(main())
