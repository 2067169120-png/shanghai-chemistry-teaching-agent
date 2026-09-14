"""One verified Chinese UI font; source documents keep their own typography.

Uses only fonts installed on this computer. Never downloads, copies or packages
font files. Call GUI functions on the Qt GUI thread, after QApplication exists.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from collections.abc import Callable, Iterable

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QRawFont, QTextLayout
from PySide6.QtWidgets import QApplication

BODY_POINTS = 11.0
SMALL_POINTS = 9.5
HAN_SAMPLE = "上海高中化学教师工作台氢氯钠铁铜锂氧碳硫题库"
CHEMISTRY_SAMPLE = "H₂SO₄  Fe³⁺  ⇌  pH 12.5  mol·L⁻¹"
# Prefer a sans-serif Simplified Chinese face before any Western-only fallback.
PREFERRED_FAMILIES = (
    "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
    "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "思源黑体 CN",
    "DengXian", "等线", "SimHei", "黑体", "SimSun", "宋体",
)


def choose_family(available: Iterable[str], supports: Callable[[str], bool]) -> str | None:
    """Choose an actually available, glyph-checked family (also testable without Qt)."""
    names = {name.casefold(): name for name in available}
    for requested in PREFERRED_FAMILIES:
        actual = names.get(requested.casefold())
        if actual is not None and supports(actual):
            return actual
    return None


def _supports_han(family: str) -> bool:
    raw = QRawFont.fromFont(QFont(family, int(BODY_POINTS)))
    return raw.isValid() and all(raw.supportsCharacter(ord(ch)) for ch in HAN_SAMPLE)


def install_ui_font(application: QApplication | None = None) -> str:
    """Idempotent application font selection, including non-native Qt backends."""
    app = application or QApplication.instance()
    if app is None:
        raise RuntimeError("Create QApplication before selecting the UI font")
    existing = getattr(app, "_shchem_typography", None)
    if existing is not None:
        return existing["family"]
    chosen = choose_family(QFontDatabase.families(), _supports_han)
    # Qt's offscreen Windows backend may not enumerate system CJK files.
    # Register regular AND bold locally; never export their bytes.
    if sys.platform == "win32" and (chosen is None or chosen in ("SimSun", "宋体", "SimHei", "黑体")):
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "Deng.ttf", "Dengb.ttf", "simhei.ttf", "simsun.ttc"):
            path = directory / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        chosen = choose_family(QFontDatabase.families(), _supports_han)
    if chosen is None:
        # Do not silently promote a Western family; try installed SC-capable fonts.
        for family in QFontDatabase.families(QFontDatabase.WritingSystem.SimplifiedChinese):
            if _supports_han(family):
                chosen = family
                break
    verified = chosen is not None
    if chosen is None:
        chosen = QFontInfo(app.font()).family()
    font = QFont(chosen)
    font.setPointSizeF(BODY_POINTS)
    font.setWeight(QFont.Weight.Normal)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.ContextFontMerging)
    app.setFont(font)
    app._shchem_typography = {"family": chosen, "chinese_sample_supported": verified,
                              "body_points": BODY_POINTS, "small_points": SMALL_POINTS}
    return chosen


def ui_font(points: float | None = None) -> QFont:
    """For custom painters and read-only QTextDocuments, without a second font policy."""
    install_ui_font()
    font = QFont(QApplication.font())
    if points is not None:
        font.setPointSizeF(points)
    return font


def glyph_report(font: QFont, text: str) -> dict:
    """Inspect actual glyph-run fonts, not merely QFont's requested family name."""
    layout = QTextLayout(text, font)
    layout.beginLayout()
    line = layout.createLine()
    if line.isValid():
        line.setLineWidth(2000)
    layout.endLayout()
    runs = [
        {"family": run.rawFont().familyName(), "style": run.rawFont().styleName(),
         "missing_glyphs": sum(index == 0 for index in run.glyphIndexes())}
        for run in layout.glyphRuns()
    ]
    info = QFontInfo(font)
    return {"requested": font.families(), "primary": info.family(),
            "points": info.pointSizeF(), "pixels": info.pixelSize(),
            "weight": int(font.weight()), "runs": runs,
            "missing_glyphs": sum(run["missing_glyphs"] for run in runs)}


def typography_report(widget=None) -> dict:
    """Explicit local diagnostic, containing no file paths or user text."""
    app = QApplication.instance()
    install_ui_font(app)
    font = widget.font() if widget is not None else ui_font()
    screen = widget.screen() if widget is not None else app.primaryScreen()
    return {**app._shchem_typography, "qt_platform": app.platformName(),
            "logical_dpi": screen.logicalDotsPerInch() if screen else None,
            "device_pixel_ratio": widget.devicePixelRatioF() if widget is not None else (screen.devicePixelRatio() if screen else None),
            "han": glyph_report(font, HAN_SAMPLE),
            "chemistry": glyph_report(font, CHEMISTRY_SAMPLE),
            "scope": "Current UI glyph samples only; not source images, Word/PPT export or all CJK characters."}
