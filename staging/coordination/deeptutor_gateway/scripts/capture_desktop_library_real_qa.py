from __future__ import annotations

"""Capture real, read-only native LibraryDetailDialog QA evidence.

This script reads the three immutable library scopes and writes only below its
isolated runtime QA directory.  It never invokes a model or a network client.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _workspace_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "sh-chem-db").is_dir() and (
            candidate / "integrations" / "deeptutor_shchem_v1"
        ).is_dir():
            return candidate
    raise RuntimeError("real Shanghai chemistry workspace is unavailable")


def _wait(app: Any, predicate: Any, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise TimeoutError(f"timed out waiting for {label}")


def _layout_signature(dialog: Any) -> tuple[tuple[int, ...], ...]:
    """Capture rendered image geometry after mapping every item to the dialog."""

    from PySide6.QtCore import QPoint

    rows = []
    for image in dialog._image_widgets:
        mapped = image.mapTo(dialog, QPoint(0, 0))
        pixmap = image.pixmap()
        rows.append(
            (
                mapped.x(),
                mapped.y(),
                image.width(),
                image.height(),
                0 if pixmap is None else pixmap.width(),
                0 if pixmap is None else pixmap.height(),
            )
        )
    return tuple(rows)


def _activate_layouts(widget: Any) -> None:
    """Synchronously activate the visible parent-layout chain before capture."""

    current = widget
    while current is not None:
        layout = current.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        current = current.parentWidget()


def _wait_for_stable_images(
    app: Any, dialog: Any, timeout: float, label: str
) -> dict[str, Any]:
    """Wait past the async-success transition until geometry stops changing."""

    deadline = time.monotonic() + timeout
    previous: tuple[tuple[int, ...], ...] | None = None
    stable_rounds = 0
    observed_rounds = 0
    while time.monotonic() < deadline:
        app.processEvents()
        if not all(image._task_id is None for image in dialog._image_widgets):
            stable_rounds = 0
            previous = None
            time.sleep(0.01)
            continue
        _activate_layouts(dialog.tabs.currentWidget())
        _activate_layouts(dialog)
        app.processEvents()
        signature = _layout_signature(dialog)
        observed_rounds += 1
        heights_match = all(
            not image.has_image
            or image.pixmap() is not None
            and image.height() == image.pixmap().height()
            for image in dialog._image_widgets
        )
        if signature and signature == previous and heights_match:
            stable_rounds += 1
        else:
            stable_rounds = 1 if signature and heights_match else 0
        previous = signature
        if stable_rounds >= 3:
            return {
                "stable": True,
                "stable_rounds": stable_rounds,
                "observed_rounds": observed_rounds,
                "image_count": len(signature),
                "heights_match_pixmaps": heights_match,
                "signature_sha256": hashlib.sha256(
                    json.dumps(signature, separators=(",", ":")).encode("ascii")
                ).hexdigest(),
            }
        time.sleep(0.02)
    raise TimeoutError(f"timed out waiting for stable image layout: {label}")


def _save(widget: Any, path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = widget.grab()
    if image.isNull() or not image.save(str(path), "PNG"):
        raise RuntimeError(f"failed to save screenshot: {path}")
    payload = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "width": image.width(),
        "height": image.height(),
    }


def _tab_check(dialog: Any, index: int) -> dict[str, Any]:
    from PySide6.QtWidgets import QScrollArea

    dialog.tabs.setCurrentIndex(index)
    scroll = dialog.tabs.widget(index)
    if not isinstance(scroll, QScrollArea):
        raise TypeError(f"detail tab {index} is not a QScrollArea")
    scroll.verticalScrollBar().setValue(0)
    return {
        "tab": dialog.tabs.tabText(index),
        "horizontal_maximum": scroll.horizontalScrollBar().maximum(),
        "viewport_width": scroll.viewport().width(),
        "content_width": scroll.widget().width() if scroll.widget() else None,
    }


def capture(
    output_root: Path, *, scopes: tuple[str, ...] = ("master", "wave1", "supplemental"),
    query: str = "",
) -> Path:
    workspace = _workspace_root().resolve()
    if str(workspace) not in sys.path:
        sys.path.insert(0, str(workspace))

    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_facade import (
        DesktopWorkbenchFacade,
    )
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
        FitWidthImage,
        LibraryDetailDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        WORKBENCH_STYLE,
        install_font_fallbacks,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )

    output_root = output_root.resolve()
    if output_root == (workspace / "sh-chem-db") or (
        workspace / "sh-chem-db" in output_root.parents
    ):
        raise ValueError("QA output must not be written inside the central database")
    output_root.mkdir(parents=True, exist_ok=False)
    state_root = output_root / "isolated-personal-state"
    paths = DesktopPaths.from_workspace(workspace, state_root=state_root)
    # Library QA never calls provider settings.  Inject an inert port so this
    # read-only run neither opens Credential Manager nor inherits a real key.
    facade = DesktopWorkbenchFacade(paths, provider_store=object())
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyleSheet(WORKBENCH_STYLE)
    font_family = install_font_fallbacks()
    bridge = DesktopTaskBridge()
    manifest: dict[str, Any] = {
        "schema_version": "shchem.native-library-detail-visual-qa.v1",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "output_root": str(output_root),
        "state_root": str(state_root),
        "font_family": font_family,
        "network_used": False,
        "model_used": False,
        "credential_store_accessed": False,
        "central_database_mutated": False,
        "capability_claim": "local read-chain and Qt layout evidence only",
        "scopes": [],
    }
    try:
        for scope in scopes:
            started = time.monotonic()
            result = facade.search_themes(scope=scope, query=query, limit=1)
            if not result.cards:
                raise AssertionError(f"no real theme available for {scope}")
            card = result.cards[0]
            searched = time.monotonic()
            detail = facade.library_theme_detail(card)
            detailed = time.monotonic()
            scope_record: dict[str, Any] = {
                "scope": scope,
                "title_zh": detail.title_zh,
                "paper_title_zh": detail.paper_title_zh,
                "page_zh": detail.page_zh,
                "part_labels": [part.label_zh for part in detail.parts],
                "part_count": len(detail.parts),
                "shared_image_count": len(detail.shared_images),
                "question_image_count": sum(
                    len(part.question_images) for part in detail.parts
                ),
                "search_seconds": round(searched - started, 3),
                "detail_seconds": round(detailed - searched, 3),
                "widths": [],
            }
            for width in (900, 420):
                dialog = LibraryDetailDialog(
                    detail, bridge, facade.library_image
                )
                dialog.resize(width, 760)
                dialog.show()
                layout_stability = _wait_for_stable_images(
                    app,
                    dialog,
                    30.0,
                    f"{scope} {width}px images and geometry",
                )
                images = dialog.findChildren(FitWidthImage)
                if not images or not any(image.has_image for image in images):
                    raise AssertionError(f"no verified real image rendered for {scope}")
                width_record: dict[str, Any] = {
                    "dialog_width": dialog.width(),
                    "dialog_height": dialog.height(),
                    "loaded_images": sum(image.has_image for image in images),
                    "missing_images": sum(not image.has_image for image in images),
                    "layout_stability": layout_stability,
                    "tabs": [],
                    "screenshots": [],
                }
                for index, slug in enumerate(("question", "answer", "source")):
                    tab = _tab_check(dialog, index)
                    app.processEvents()
                    if tab["horizontal_maximum"] != 0:
                        raise AssertionError(
                            f"horizontal overflow in {scope}/{width}/{slug}: {tab}"
                        )
                    width_record["tabs"].append(tab)
                    width_record["screenshots"].append(
                        _save(
                            dialog,
                            output_root / f"{scope}-{width}-{slug}.png",
                        )
                    )
                loaded = next(image for image in images if image.has_image)
                loaded._open_zoom()
                zoom = loaded._zoom_dialog
                if zoom is None:
                    raise AssertionError("zoom dialog was not created")
                zoom.resize(width, 760)
                zoom.zoom.setValue(150)
                zoom.show()
                _wait(app, zoom.isVisible, 2.0, f"{scope} {width}px zoom")
                app.processEvents()
                width_record["zoom"] = {
                    "dialog_width": zoom.width(),
                    "dialog_height": zoom.height(),
                    "percent": zoom.zoom.value(),
                    "minimum_size_hint_width": zoom.minimumSizeHint().width(),
                    "slider_right": zoom.zoom.geometry().right(),
                    "toolbar_fits": zoom.minimumSizeHint().width() <= width,
                    "horizontal_maximum": zoom.scroll.horizontalScrollBar().maximum(),
                    "horizontal_bar_height": zoom.scroll.horizontalScrollBar().height(),
                    "screenshot": _save(
                        zoom, output_root / f"{scope}-{width}-zoom-150.png"
                    ),
                }
                if not width_record["zoom"]["toolbar_fits"]:
                    raise AssertionError(f"zoom toolbar overflows {width}px")
                if width_record["zoom"]["horizontal_maximum"] <= 0 or (
                    width_record["zoom"]["horizontal_bar_height"] <= 0
                ):
                    raise AssertionError(f"zoom image cannot be horizontally panned at {width}px")
                zoom.close()
                dialog.close()
                app.processEvents()
                scope_record["widths"].append(width_record)
            manifest["scopes"].append(scope_record)
    finally:
        bridge.shutdown(5000)
        facade.shutdown()

    screenshots = [
        shot
        for scope in manifest["scopes"]
        for width in scope["widths"]
        for shot in (
            *width["screenshots"],
            width["zoom"]["screenshot"],
        )
    ]
    expected_screenshots = len(scopes) * 8
    if len(screenshots) != expected_screenshots:
        raise AssertionError(f"expected {expected_screenshots} screenshots, got {len(screenshots)}")
    manifest["summary"] = {
        "scope_count": len(manifest["scopes"]),
        "screenshot_count": len(screenshots),
        "all_horizontal_maximum_zero": all(
            tab["horizontal_maximum"] == 0
            for scope in manifest["scopes"]
            for width in scope["widths"]
            for tab in width["tabs"]
        ),
        "all_zoom_toolbars_fit": all(
            width["zoom"]["toolbar_fits"]
            for scope in manifest["scopes"]
            for width in scope["widths"]
        ),
        "all_zoom_images_horizontally_pannable": all(
            width["zoom"]["horizontal_maximum"] > 0
            and width["zoom"]["horizontal_bar_height"] > 0
            for scope in manifest["scopes"]
            for width in scope["widths"]
        ),
        "all_screenshot_hashes_unique": len({shot["sha256"] for shot in screenshots})
        == len(screenshots),
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--scope", choices=("master", "wave1", "supplemental"))
    parser.add_argument("--query", default="")
    args = parser.parse_args()
    workspace = _workspace_root()
    output = args.output_root or (
        workspace
        / "runtime"
        / "deeptutor_shchem"
        / "qa"
        / f"library-detail-real-{datetime.now().astimezone():%Y%m%d-%H%M%S}"
    )
    manifest = capture(
        output, scopes=(args.scope,) if args.scope else ("master", "wave1", "supplemental"),
        query=args.query,
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
