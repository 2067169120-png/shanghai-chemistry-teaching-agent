from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


def test_four_primary_routes_keep_all_teacher_workspaces_reachable() -> None:
    html = source("index.html")
    app = source("app.js")
    css = source("styles.css")
    primary = html.split('<nav class="primary-nav"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'href="(#[^"]+)"', primary) == [
        "#/home",
        "#/library",
        "#/students",
        "#/prep",
    ]
    assert re.findall(r">([^<>]+)</a>", primary) == [
        "首页",
        "题库",
        "学生",
        "备课",
    ]
    drawer = html.split('<details class="tool-drawer">', 1)[1].split(
        "</details>", 1
    )[0]
    assert "更多与设置" in drawer
    assert re.findall(r'href="(#[^"]+)"', drawer) == [
        "#/materials",
        "#/curriculum",
        "#/analytics",
        "#/presentations",
        "#/hotspots",
        "#/templates",
        "#/review",
        "#/ai",
        "#/settings",
    ]
    assert set(re.findall(r'href="(#[^"]+)"', primary + drawer)) == {
        "#/home",
        "#/materials",
        "#/curriculum",
        "#/library",
        "#/students",
        "#/analytics",
        "#/prep",
        "#/presentations",
        "#/hotspots",
        "#/review",
        "#/templates",
        "#/ai",
        "#/settings",
    }
    assert 'class="tab-nav legacy-tab-nav"' in html
    assert 'aria-label="兼容工作区" hidden' in html
    legacy = html.split('<nav class="tab-nav legacy-tab-nav"', 1)[1].split("</nav>", 1)[0]
    assert legacy.count('role="tab"') == 10
    assert html.count('role="tab"') == 14
    for marker in (
        'home: ["首页"',
        'materials: ["资料中心"',
        'library: ["题库"',
        'prep: ["备课"',
        'const primaryNavRoutes = Object.freeze(["home", "library", "students", "prep"]);',
        'retrieval: "library?drawer=evidence"',
        'generation: "prep?section=generation"',
        'document.body.dataset.primaryRoute = primary',
        'window.matchMedia("(max-width: 760px)").matches',
        'toolDrawer.open = !narrowTeacherLayout && !primaryNavRoutes.includes(primary);',
    ):
        assert marker in app
    compact = " ".join(css.split())
    assert '@media (max-width: 760px)' in compact
    assert 'grid-template-columns: repeat(4, minmax(0, 1fr))' in compact
    assert 'position: fixed' in compact
    assert 'overflow-x: clip' in compact


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        value = dict(attrs).get("id")
        if value:
            self.ids.append(value)


def test_personal_teacher_desk_exposes_daily_chinese_actions() -> None:
    html = source("index.html")
    panel = html.split('id="panel-candidate-review"', 1)[1].split(
        'id="panel-taxonomy"', 1
    )[0]
    for copy in (
        "快速组卷",
        "当前可用 · 题篮导出闭环",
        "选择题目",
        "核对题篮",
        "设置试卷",
        "生成与下载",
        "收藏",
        "最近看过",
        "题篮",
        "保存方案",
        "载入",
        "加入本题及依赖",
        "仅加入本题",
        "加入整主题",
        "完整“蓝图约束 → 候选缺口 → 整卷质量检查”仍在按设计文档接入",
    ):
        assert copy in panel
    for element_id in (
        "personalFavoritesList",
        "personalRecentList",
        "personalBasketList",
        "personalPlanName",
        "personalPlanSelect",
        "candidateReviewFavorite",
        "candidateReviewAddWithDependencies",
        "candidateReviewAddAtomic",
    ):
        assert f'id="{element_id}"' in panel


def test_local_storage_is_versioned_and_never_persists_the_connection_token() -> None:
    app = source("app.js")
    persistence = app[
        app.index("function persistPersonalWorkbench") : app.index(
            "function loadPersonalWorkbench"
        )
    ]
    assert 'storageKey: "shchem.teacher.personal-workbench.v1"' in app
    assert 'schemaVersion: "shchem_teacher_personal_workbench_v1"' in app
    for key in ("favorites", "recent", "basket", "plans", "local_tags"):
        assert f"{key}: state.personalWorkbench.{key}" in persistence
    assert "state.token" not in persistence
    assert 'localStorage.setItem(personalWorkbenchContract.storageKey' in persistence
    assert 'localStorage.getItem(personalWorkbenchContract.storageKey)' in app


def test_basket_supports_three_units_dependencies_order_and_named_plans() -> None:
    app = source("app.js")
    for marker in (
        'unit: "theme"',
        'addCurrentCandidateToBasket("dependency")',
        'addCurrentCandidateToBasket("atomic")',
        "personalDependencyClosure(found.group, found.row)",
        "movePersonalBasketItem(index, -1)",
        "movePersonalBasketItem(index, 1)",
        "removePersonalBasketItem(entry.key)",
        "function savePersonalPlan()",
        "function loadPersonalPlan()",
        "function deletePersonalPlan()",
    ):
        assert marker in app
    assert "shared_material_count" in app
    assert "dependency_count" in app
    assert "tag_summary" in app
    assert "answer_summary" in app


def test_parent_repair_theme_basket_persists_only_source_master_atomic_ids() -> None:
    app = source("app.js")
    entry = app[
        app.index("function personalParentRepairThemeEntry") : app.index(
            "function personalFindThemeRow"
        )
    ]
    assert "group.atomic_chain.map((row) => row.atomic_part_id)" in entry
    assert "atomic_ids: sourceMasterIds" in entry
    assert "candidate_effective_units.map" not in entry
    assert "MPR-AP-" not in entry
    assert 'scope: "master"' in entry
    assert "机器候选" in entry
    assert "不是正式人审" in entry

    finder = app[
        app.index("function personalFindThemeRow") : app.index(
            "function personalShortAxis"
        )
    ]
    assert "state.candidateReviewThemeCatalog.parentChainOverlay?.groups" in finder
    assert "parentRepair: true" in finder

    opening = app[
        app.index("async function openPersonalWorkbenchEntry") : app.index(
            "function quickQuestionSearchControls"
        )
    ]
    assert "parentRepairGroupById.has(entry.source_id)" in opening
    assert "openCandidateReviewThemeChain(entry.source_id)" in opening


def test_personal_mode_auto_connects_and_keeps_clipboard_as_maintenance_fallback() -> None:
    html = source("index.html")
    app = source("app.js")
    assert '<body class="auto-connect-pending">' in html
    assert 'id="connectFromClipboard"' in html
    assert "从剪贴板读取" in html
    assert "正在自动打开本机工作台" in html
    automatic = app[
        app.index("async function connectLocalAutomatically") : app.index(
            "async function connect()"
        )
    ]
    assert '$("token").value = PERSONAL_AUTO_TOKEN' in automatic
    assert "await connect()" in automatic
    assert 'document.body.classList.remove("auto-connect-pending")' in automatic
    assert app.count("void connectLocalAutomatically();") == 1
    function = app[
        app.index("async function connectFromClipboard") : app.index(
            "async function connectLocalAutomatically"
        )
    ]
    assert "navigator.clipboard.readText()" in function
    assert '$("token").value = clipboardToken' in function
    assert "Ctrl+V" in function
    assert '$("token").focus()' in function
    assert "localStorage" not in function
    assert app.count("connectFromClipboard);") == 1


def test_design_document_home_is_real_status_driven_and_routes_to_workflows() -> None:
    html = source("index.html")
    app = source("app.js")
    home = html.split('id="panel-home"', 1)[1].split(
        'id="panel-overview"', 1
    )[0]
    for element_id in (
        "homeQuestionCount",
        "homeReviewCount",
        "homeBasketCount",
        "homeStudentCount",
        "homeProviderState",
        "homeMaterialState",
        "homeWave1Count",
        "homeMasterCount",
        "homeSupplementalCount",
    ):
        assert f'id="{element_id}"' in home
    for route in ("#/library", "#/prep", "#/review", "#/ai", "#/materials"):
        assert f'href="{route}"' in home
    for copy in (
        "按教材章节找题与导出",
        "导入新试卷和答案",
        "选择图片、PDF 或 DOCX",
        "待教师复核的候选",
        "开始导入",
        "学生分析与组卷备课",
    ):
        assert copy in home
    assert 'class="home-task-grid"' in home
    assert 'href="#/curriculum"' in home
    assert '<summary>查看工作台概览</summary>' in home
    renderer = app[
        app.index("function renderTeacherHome") : app.index(
            "const featureRoadmapCopy"
        )
    ]
    assert "workbenchProductCount(scope)" in renderer
    assert "state.personalWorkbench" in renderer
    assert "state.studentProfilesCount" in renderer
    assert "modelProviderSelectedProfile()" in renderer
    assert "连接成功" not in home
    assert "生成成功" not in home


def test_quick_search_calls_server_and_keeps_theme_as_the_result_unit() -> None:
    html = source("index.html")
    app = source("app.js")
    for copy in (
        "今天想找什么题？",
        "结果保持完整主题",
        "命中的小题只在完整主题内高亮",
    ):
        assert copy in html
    search = app[
        app.index("function quickQuestionSearchControls") : app.index(
            "function candidateReviewThemeCard"
        )
    ]
    assert 'api("/api/v1/questions/search", { method: "POST"' in search
    assert 'payload = { scope: state.candidateReviewScope, filters, limit: 12 }' in search
    assert "quickQuestionSearchCard(card)" in search
    assert "card.atomic_chain.forEach" in search
    assert "new Set(card.matched_atomic_ids)" in search
    assert 'add.textContent = "加入整主题"' in search
    assert 'open.textContent = "打开完整题链"' in search


def test_theme_review_has_same_screen_png_and_controlled_chinese_tag_selectors() -> None:
    html = source("index.html")
    app = source("app.js")
    workspace = html.split('id="themeReviewWorkspace"', 1)[1].split(
        'id="candidateReviewForm"', 1
    )[0]
    for marker in (
        'id="themeReviewQuestionAtomic"',
        'id="themeReviewQuestionImage"',
        "逐题题图与标签",
        "本主题采用全部现有候选",
        "保存到我的题库",
        "不需要填写 JSON 或证据编号",
    ):
        assert marker in workspace
    assert '<details class="theme-review-source"' in workspace
    assert '<details class="theme-review-technical-summary">' in workspace
    for label in (
        "题型",
        "主知识点（K）",
        "能力（A）",
        "情境（C）",
        "作答方式（R）",
        "表征方式（RP）",
        "认知难度（D）",
    ):
        assert f'"{label}"' in app
    assert "themeReviewTaxonomyOptions" in app
    assert 'select.dataset.themeReviewTagField = field' in app
    assert "loadThemeReviewQuestionPreview" in app
    assert 'contentType !== "image/png"' in app


def test_my_tags_are_snapshot_scoped_and_drive_detail_and_local_filters() -> None:
    app = source("app.js")
    assert "`${snapshotId}:${tr.dataset.atomicPartId}`" in app
    assert "candidateReviewLocalTagRecord(item)" in app
    item_type = app[
        app.index("function candidateReviewItemType(item)") : app.index(
            "const candidateReviewItemTypeLabels"
        )
    ]
    axes = app[
        app.index("function candidateReviewAxisEntries") : app.index(
            "function candidateReviewParentChainNodes"
        )
    ]
    assert "candidateReviewLocalTagRecord(item)" in item_type
    assert "candidateReviewLocalTagRecord(item)" in axes
    assert 'rows.push(["我的标记"' in app
    assert "removeCandidateLocalTags" in app
    assert "clearCandidateThemeLocalTags" in app
    assert "populateCandidateReviewFilters(state.candidateReviewAtomicItems)" in app


def test_mobile_layout_and_dom_ids_remain_usable() -> None:
    html = source("index.html")
    styles = source("styles.css")
    parser = _Ids()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    assert "@media (max-width: 420px)" in styles
    for selector in (
        ".personal-workbench-grid",
        ".personal-plan-bar",
        ".candidate-daily-actions",
        ".quick-question-search-row",
        ".quick-question-results",
        ".theme-review-tag-selector",
    ):
        assert selector in styles
    mobile = styles.split("@media (max-width: 420px)", 1)[1]
    assert ".quick-question-search-row, .quick-question-filter-grid" in mobile
    assert ".personal-plan-bar" in mobile
    assert "minmax(0, 1fr)" in mobile
    assert not re.search(r"width:\s*(?:[4-9]\d{2}|\d{4,})px", mobile)
