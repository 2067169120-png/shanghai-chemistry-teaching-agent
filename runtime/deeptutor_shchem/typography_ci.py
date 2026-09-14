"""Synthetic font and scale probes. Can also inspect a pinned pre-change checkout."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def baseline(source_root, output):
    sys.path.insert(0, str(Path(source_root).resolve()))
    from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
    from PySide6.QtGui import QFontDatabase, QFontInfo, QTextLayout, QFont
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import install_font_fallbacks
    app = create_application(["font-baseline"])
    chosen = install_font_fallbacks()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    def inspect(font, text):
        layout = QTextLayout(text, font)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(1200)
        layout.endLayout()
        return {"requested": font.families(), "primary": QFontInfo(font).family(),
                "pixels": QFontInfo(font).pixelSize(), "weight": int(font.weight()),
                "runs": [{"family": r.rawFont().familyName(), "style": r.rawFont().styleName(),
                          "missing_glyphs": sum(g == 0 for g in r.glyphIndexes())} for r in layout.glyphRuns()]}
    panel = QWidget()
    layout = QVBoxLayout(panel)
    records = {}
    for key, text, object_name in (
        ("body", "上海高中化学教师工作台，保存草稿，查看试卷。", ""),
        ("heading", "教学要求 · 备课资料 · 进度与结果", "CardTitle"),
        ("small", "上海高中化学 · 教师工作台", "BrandSub"),
        ("chemical", "H₂SO₄  Fe³⁺  ⇌  pH  12.5  mol·L⁻¹", ""),
    ):
        label = QLabel(text)
        label.setObjectName(object_name)
        layout.addWidget(label)
        label.ensurePolished()
        records[key] = inspect(label.font(), text)
    panel.resize(880, 300)
    panel.show()
    app.processEvents()
    panel.grab().save(str(output / "baseline.png"))
    families = {}
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun", "DengXian", "Segoe UI", "Noto Sans CJK SC"):
        families[name] = {"installed": name in QFontDatabase.families(),
                          "styles": QFontDatabase.styles(name)}
    (output / "baseline.json").write_text(json.dumps(
        {"source_tag": "v0.1.91", "selected": chosen, "qt_platform": app.platformName(),
         "widgets": records, "families": families,
         "scope": "Pinned pre-change source in CI, not the user computer."}, ensure_ascii=False, indent=2), encoding="utf-8")
    panel.close()


def scaled_runs(output, executable=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for scale in ("1", "1.25", "1.5"):
        target = output / ("scale-" + scale.replace(".", "_"))
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else "offscreen"
        env["QT_SCALE_FACTOR"] = scale
        args = ([str(executable), "--verify-typography", str(target.resolve())] if executable
                else [sys.executable, str(ROOT / "runtime/deeptutor_shchem/desktop_teacher_workbench.pyw"),
                      "--verify-typography", str(target.resolve())])
        subprocess.run(args, env=env, check=True, timeout=90)
        report = json.loads((target / "typography-probe.json").read_text(encoding="utf-8"))
        assert abs(report["device_pixel_ratio"] - float(scale)) < .02
        assert report["qt_platform"] == env["QT_QPA_PLATFORM"]
        assert report["han"]["missing_glyphs"] == 0 and report["chemistry"]["missing_glyphs"] == 0
        reports.append(report)
    (output / "scaling-summary.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "--baseline":
        baseline(sys.argv[2], sys.argv[3])
    else:
        scaled_runs(sys.argv[1])
