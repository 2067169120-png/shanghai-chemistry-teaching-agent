from __future__ import annotations

"""Paths owned by the native teacher workbench.

The desktop application reads the existing evidence library in place and keeps
all mutable personal state below the current Windows user's local application
data directory.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


class DesktopPathError(RuntimeError):
    """Raised when the local evidence workspace cannot be located safely."""


def _candidate_roots(anchor: Path | None) -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("SHCHEM_WORKSPACE_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser())
    if anchor is not None:
        candidates.append(anchor)
    candidates.extend((Path.cwd(), Path(sys.executable).resolve().parent))

    expanded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            resolved = resolved.parent
        for value in (resolved, *resolved.parents):
            if value not in seen:
                seen.add(value)
                expanded.append(value)
    return expanded


def discover_workspace_root(anchor: str | Path | None = None) -> Path:
    """Prefer an existing library; allow a clean source checkout on first use.

    An explicit workspace setting is never silently replaced by another
    checkout. A source checkout does not ship the teacher's private library.
    """
    configured = os.environ.get("SHCHEM_WORKSPACE_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not (candidate / "integrations" / "deeptutor_shchem_v1").is_dir():
            raise DesktopPathError(
                "SHCHEM_WORKSPACE_ROOT 未指向有效的工作台目录，请更正此设置。"
            )
        return candidate
    start = Path(anchor) if anchor is not None else Path(__file__)
    checkouts = [
        candidate for candidate in _candidate_roots(start)
        if (candidate / "integrations" / "deeptutor_shchem_v1").is_dir()
    ]
    for candidate in checkouts:
        if (candidate / "sh-chem-db").is_dir():
            return candidate
    if checkouts:
        return checkouts[0]
    raise DesktopPathError("未找到上海高中化学工作台源码或安装目录。")


def default_state_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base).expanduser() / "ShanghaiChem" / "DesktopWorkbench"
    return Path.home() / "AppData" / "Local" / "ShanghaiChem" / "DesktopWorkbench"


@dataclass(frozen=True)
class DesktopPaths:
    workspace_root: Path
    shchem_root: Path
    runtime_root: Path
    state_root: Path
    settings_root: Path
    drafts_root: Path
    task_root: Path

    @classmethod
    def from_workspace(
        cls,
        workspace_root: str | Path,
        *,
        state_root: str | Path | None = None,
    ) -> "DesktopPaths":
        root = Path(workspace_root).resolve()
        state = Path(state_root).resolve() if state_root else default_state_root().resolve()
        return cls(
            workspace_root=root,
            shchem_root=(
                root / "sh-chem-db"
                if (root / "sh-chem-db").exists()
                else state / "library" / "sh-chem-db"
            ),
            runtime_root=root / "runtime" / "deeptutor_shchem",
            state_root=state,
            settings_root=state / "model-settings",
            drafts_root=state / "drafts",
            task_root=state / "tasks",
        )

    @classmethod
    def discover(cls, anchor: str | Path | None = None) -> "DesktopPaths":
        return cls.from_workspace(discover_workspace_root(anchor))

    def ensure_mutable_roots(self) -> None:
        for path in (
            self.state_root,
            self.settings_root,
            self.drafts_root,
            self.task_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def uses_personal_library(self) -> bool:
        return self.shchem_root == self.state_root / "library" / "sh-chem-db"

    def validate_read_roots(self) -> None:
        # Only the app-owned empty library may be created. Existing source
        # libraries, including malformed paths, must not be replaced or edited.
        if self.uses_personal_library:
            self.shchem_root.mkdir(parents=True, exist_ok=True)
        if not self.shchem_root.is_dir():
            raise DesktopPathError("本地化学资料库不可用。")


__all__ = [
    "DesktopPathError",
    "DesktopPaths",
    "default_state_root",
    "discover_workspace_root",
]
