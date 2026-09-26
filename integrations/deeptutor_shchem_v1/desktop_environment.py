"""On-demand local readiness checks; no credentials or model/network requests.

Discovery is not end-to-end verification: an Office registration says only that
an installation was detected. Existing import/generation flows remain usable
when an unrelated optional dependency is absent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from typing import Any, Callable

from .desktop_paths import DesktopPaths
from .desktop_version import DESKTOP_VERSION


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    title: str
    status: str
    detail: str
    affects: str
    action: str = ""


# Import checks load no application state. Version strings are informational;
# actual successful imports catch missing shared libraries as well as packages.
DEPENDENCIES = (
    ("qt_widgets", "原生桌面", "PySide6.QtWidgets", "打开桌面页面"),
    ("qt_svg", "SVG 图标", "PySide6.QtSvg", "图标与 SVG 显示"),
    ("qt_pdf", "原生 PDF 组件", "PySide6.QtPdf", "导入与教材 PDF 预览"),
    ("qt_pdf_widgets", "原生 PDF 视图", "PySide6.QtPdfWidgets", "原页 PDF 查看窗口"),
    ("docx", "Word 文件支持", "docx", "Word 导入与 DOCX 输出"),
    ("pptx", "PowerPoint 文件支持", "pptx", "PPTX 文件输出"),
    ("pillow", "图片处理", "PIL.Image", "缩略图与图片处理"),
    ("pdfium", "PDF 页图支持", "pypdfium2", "试卷分页和 PDF 预览"),
    ("pypdf", "PDF 读取", "pypdf", "PDF 页面与文本读取"),
    ("jsonschema", "结构化结果校验", "jsonschema", "模型结果格式检查"),
)
ICON_FILES = ("down.svg", "up.svg", "checked.svg", "unchecked.svg", "mixed.svg")


def _module_check(key: str, title: str, name: str, affects: str,
                  importer: Callable[[str], Any]) -> ReadinessCheck:
    try:
        importer(name)
    except Exception:
        return ReadinessCheck(key, title, "missing", "当前进程无法加载所需组件。", affects,
                              "源码版请重新安装 desktop_requirements.txt；打包版请重新解压完整程序目录。")
    return ReadinessCheck(key, title, "ready", "组件可在当前进程加载。", affects)


def detect_word_registration() -> bool | None:
    """Read-only registry discovery. Never start Word or inspect open documents."""
    if os.name != "nt":
        return False
    try:
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID",
                                    0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    if value:
                        return True
            except FileNotFoundError:
                continue
        return False
    except (OSError, ImportError):
        return None


def office_check(*, office_finder: Callable[[], Path | None] | None = None,
                 word_detector: Callable[[], bool | None] | None = None) -> ReadinessCheck:
    from .desktop_local_pagination import find_libreoffice
    try:
        office = (office_finder or find_libreoffice)()
    except OSError:
        office = None
    if office:
        return ReadinessCheck("office", "Office 排版转换", "detected",
                              "发现 LibreOffice；尚未执行文档转换验证。", "DOCX → PDF 真实试卷分页",
                              "选一份小试卷运行“预览试卷排版”，确认本机实际转换效果。")
    word = (word_detector or detect_word_registration)()
    if word:
        return ReadinessCheck("office", "Office 排版转换", "detected",
                              "发现 Microsoft Word 注册信息；尚未验证自动化转换。", "DOCX → PDF 真实试卷分页",
                              "运行一次试卷分页预览；安装状态不等于转换已经成功。")
    if word is None:
        return ReadinessCheck("office", "Office 排版转换", "unknown",
                              "没有找到 LibreOffice，Word 安装状态暂无法读取。", "真实试卷分页",
                              "核对本机 Office 安装；仍可选题、保存草稿和生成可编辑文档。")
    return ReadinessCheck("office", "Office 排版转换", "missing",
                          "未发现 LibreOffice 或可读取的 Microsoft Word 注册信息。", "真实试卷分页",
                          "安装其中一种后重试。选题、草稿、模板和课堂工具不依赖此检查通过。")


def _writable_state_check(path: Path) -> ReadinessCheck:
    """Probe only inside the app-owned state directory, remove only our temp file."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(prefix=".readiness-", dir=path) as handle:
            handle.write(b"shchem-local-check")
            handle.flush()
            os.fsync(handle.fileno())
        return ReadinessCheck("state", "个人数据目录", "ready", "临时写入与清理成功。",
                              "草稿、题篮、恢复副本及个人设置")
    except OSError:
        return ReadinessCheck("state", "个人数据目录", "missing", "当前无法写入个人数据目录。",
                              "保存工作", "检查本机目录权限和剩余空间；不要删除已有个人数据。")


def _resource_check(core_root: Path) -> ReadinessCheck:
    manifest = core_root / "desktop_resources.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        entries = value["files"]
        root = core_root.parents[1].resolve()
        if value.get("schema_version") != "shchem.desktop-resources.v1" or not isinstance(entries, list):
            raise ValueError("Invalid software resource manifest")
        missing = 0
        for name in entries:
            if not isinstance(name, str) or not name.startswith(("integrations/", "knowledge/")):
                raise ValueError("Invalid resource entry")
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Invalid resource entry")
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Invalid resource entry")
            missing += not target.is_file()
        return ReadinessCheck("resources", "已声明的软件资源", "missing" if missing else "ready",
            f"清单共 {len(entries)} 项，缺少 {missing} 项。仅检查存在，不验证教学内容或全部动态脚本。",
            "清单内的图标、JSON 约束与简明知识索引",
            "重新解压完整源码/程序目录；本项不是题库数量检查。" if missing else "")
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return ReadinessCheck("resources", "已声明的软件资源", "missing", "资源清单不可读取。",
            "发行资源检查", "请使用完整的同一版本目录，不要混用单个文件。")


def collect_environment_report(paths: DesktopPaths, *,
                               importer: Callable[[str], Any] = importlib.import_module,
                               office_finder: Callable[[], Path | None] | None = None,
                               word_detector: Callable[[], bool | None] | None = None,
                               resource_root: Path | None = None) -> dict[str, Any]:
    """Return a shareable report with no paths, provider fields, tokens or keys.

    Only local dependency imports, fixed icon resources, library existence and
    an owned temporary write are checked; do not recursively inventory teaching
    materials or touch the credential backend.
    """
    checks = [
        ReadinessCheck("python", "运行解释器", "ready" if sys.version_info >= (3, 10) else "missing",
                       "Python " + platform.python_version(), "应用运行",
                       "源码推荐 Python 3.12；打包版使用随包解释器。"),
        *[_module_check(*item, importer) for item in DEPENDENCIES],
        _writable_state_check(paths.state_root),
    ]
    assets = (resource_root or Path(__file__).parent) / "desktop_workbench" / "studio_assets"
    missing = [name for name in ICON_FILES if not (assets / name).is_file()]
    checks.append(ReadinessCheck("icons", "输入控件图标资源", "missing" if missing else "ready",
                                 "缺少：" + "、".join(missing) if missing else "五项 SVG 资源存在。",
                                 "下拉框、复选框和数值输入箭头",
                                 "重新解压完整程序目录，不要单独移动 EXE。" if missing else ""))
    checks.append(_resource_check(resource_root or Path(__file__).parent))
    checks.append(office_check(office_finder=office_finder, word_detector=word_detector))
    bank = paths.shchem_root
    if bank.is_dir():
        try:
            nonempty = next(bank.iterdir(), None) is not None
            checks.append(ReadinessCheck("library", "题库目录", "detected" if nonempty else "empty",
                                         "目录内有资料；未检查数量、标签或题目质量。" if nonempty
                                         else "当前题库目录为空，不妨碍打开桌面或保存备课。",
                                         "本机题目与教材检索",
                                         "从“导入资料”加入文件，或连接原工作区。" if not nonempty else ""))
        except OSError:
            checks.append(ReadinessCheck("library", "题库目录", "unknown", "题库目录暂不可读取。",
                                         "题目与教材检索", "检查本机目录权限。"))
    else:
        checks.append(ReadinessCheck("library", "题库目录", "missing", "题库路径不存在或不是目录。",
                                     "题目与教材检索", "检查工作区设置；本检查不会清空、搬迁或重导资料。"))
    return {
        "schema_version": "shchem.environment.v1",
        "version": DESKTOP_VERSION,
        "execution_mode": "packaged" if getattr(sys, "frozen", False) else "source",
        "platform": platform.system(),
        "python": platform.python_version(),
        "checks": [asdict(check) for check in checks],
        "network_requests": 0,
        "scope": "本机组件加载、图标存在、目录可写与安装发现；不是模型能力、化学正确性或成品排版验收。报告不含本机路径、题目正文或模型配置。",
    }


def report_text(report: dict[str, Any]) -> str:
    labels = {"ready": "可用", "detected": "已发现·待实测", "missing": "需处理",
              "empty": "空目录", "unknown": "待确认"}
    mode = "打包版" if report["execution_mode"] == "packaged" else "源码版"
    lines = [f"沪上化学智研台 v{report['version']} · {mode}", report["scope"], ""]
    for row in report["checks"]:
        lines += [f"{row['title']}：{labels.get(row['status'], row['status'])}", row["detail"],
                  "影响范围：" + row["affects"]]
        if row["action"]:
            lines.append("下一步：" + row["action"])
        lines.append("")
    return "\n".join(lines)
