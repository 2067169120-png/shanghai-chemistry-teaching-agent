from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..desktop_facade import (
    PERSONAL_HANDOUT_SCOPE,
    DesktopWorkbenchFacade,
    ThemeCard,
    ThemeSearchResult,
)
from ..desktop_library import LibraryThemeDetail
from .components import CardFrame, page_scroll, section_title, set_status
from .library_detail import LibraryDetailDialog
from .tasks import DesktopTaskBridge


def _mapping_count(value: object) -> int | None:
    """Read a public curriculum count without inventing a mapping."""

    if not isinstance(value, dict):
        return None
    for key in ("mapped_atomic_count", "atomic_count", "mapped_entry_count"):
        count = value.get(key)
        if type(count) is int and count >= 0:
            return count
    return None


class LibraryPage(QWidget):
    """Two-pane theme-first library with a responsive supporting detail pane."""

    basket_changed = Signal(int)
    preparation_image_requested = Signal(dict)
    preparation_reference_requested = Signal(object)
    word_reference_requested = Signal(dict)

    def __init__(
        self,
        facade: DesktopWorkbenchFacade,
        tasks: DesktopTaskBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.tasks = tasks
        self._cards: list[ThemeCard] = []
        self._loading = False
        self._compact = False
        self._curriculum_catalog: dict[str, object] | None = None
        self._curriculum_loading = False
        self._detail_generation = 0
        self._detail_task_id: str | None = None
        self._selected_detail: LibraryThemeDetail | None = None
        self._selected_detail_key: str | None = None
        self._detail_dialog: LibraryDetailDialog | None = None
        self._handout_candidate_dialog = None

        content = QWidget()
        content.setObjectName("PageContent")
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 32)
        root.setSpacing(16)
        root.addWidget(
            section_title(
                "题库",
                "按完整大题浏览；匹配到的小问只在所属大题内标出。",
            )
        )

        filters = CardFrame()
        # Stack filter controls so the narrow 360px window never relies on a
        # horizontal minimum size (QFormLayout's label column otherwise
        # forces the query row wider than the viewport).
        filter_form = QVBoxLayout(filters)
        filter_form.setContentsMargins(16, 12, 16, 12)
        filter_form.setSpacing(6)
        self.scope = QComboBox()
        self.scope.addItem("核心题库", "master")
        self.scope.addItem("已细分题库", "wave1")
        self.scope.addItem("补充题库", "supplemental")
        self.scope.addItem("我的讲义（Word）", PERSONAL_HANDOUT_SCOPE)
        self.scope.setAccessibleName("题库范围")
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.handout_state = QComboBox()
        self.handout_state.addItem("全部讲义题目", None)
        self.handout_state.addItem("可直接收录", "quick_import_ready")
        self.handout_state.addItem("待图片识别", "visual_completion_queue")
        self.handout_state.addItem("答案未对应", "unpaired")
        self.handout_state.setAccessibleName("我的讲义状态筛选")
        self.handout_state.setVisible(False)
        self.handout_label = QLabel("讲义状态")
        self.handout_label.setObjectName("MutedLabel")
        self.handout_label.setVisible(False)
        self.query = QLineEdit()
        self.query.setPlaceholderText("知识点、题型或大题关键词")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("题库关键词")
        self.query.returnPressed.connect(self.search)
        self.search_button = QPushButton("查找大题")
        self.search_button.setAccessibleName("查找大题")
        self.search_button.clicked.connect(self.search)
        scope_label = QLabel("题库范围")
        scope_label.setObjectName("MutedLabel")
        filter_form.addWidget(scope_label)
        filter_form.addWidget(self.scope)
        filter_form.addWidget(self.handout_label)
        filter_form.addWidget(self.handout_state)

        # The three selectors are deliberately a small cascade rather than a
        # giant filter dialog.  Their values come only from the explicit
        # CurriculumWorkbenchReader tree; no K tag, title or filename is ever
        # converted into a guessed textbook section.
        self.curriculum_label = QLabel("教材章节")
        self.curriculum_label.setObjectName("MutedLabel")
        filter_form.addWidget(self.curriculum_label)
        self.curriculum_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.curriculum_row.setContentsMargins(0, 0, 0, 0)
        self.curriculum_row.setSpacing(6)
        self.volume_combo = self._new_curriculum_combo(
            "教材册筛选", "CurriculumVolumeCombo"
        )
        self.chapter_combo = self._new_curriculum_combo(
            "教材章筛选", "CurriculumChapterCombo"
        )
        self.section_combo = self._new_curriculum_combo(
            "教材小节筛选", "CurriculumSectionCombo"
        )
        self.volume_combo.addItem("全部教材册", None)
        self.chapter_combo.addItem("先选择教材册", None)
        self.section_combo.addItem("先选择教材章", None)
        self.chapter_combo.setEnabled(False)
        self.section_combo.setEnabled(False)
        self.volume_combo.currentIndexChanged.connect(self._volume_changed)
        self.chapter_combo.currentIndexChanged.connect(self._chapter_changed)
        self.section_combo.currentIndexChanged.connect(self._section_changed)
        self.curriculum_row.addWidget(self.volume_combo, 1)
        self.curriculum_row.addWidget(self.chapter_combo, 1)
        self.curriculum_row.addWidget(self.section_combo, 1)
        self.clear_curriculum_button = QPushButton("清除")
        self.clear_curriculum_button.setObjectName("QuietButton")
        self.clear_curriculum_button.setAccessibleName("清除教材章节筛选")
        self.clear_curriculum_button.clicked.connect(self._clear_curriculum)
        self.curriculum_row.addWidget(self.clear_curriculum_button)
        filter_form.addLayout(self.curriculum_row)
        self.curriculum_hint = QLabel("正在读取教材目录…")
        self.curriculum_hint.setObjectName("MutedLabel")
        self.curriculum_hint.setWordWrap(True)
        filter_form.addWidget(self.curriculum_hint)
        search_label = QLabel("查找")
        search_label.setObjectName("MutedLabel")
        filter_form.addWidget(search_label)
        search_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.search_button)
        self.search_row = search_row
        filter_form.addLayout(search_row)
        root.addWidget(filters)
        self.word_questions_button = QPushButton("已导入 Word · 逐题预览与挑选")
        self.word_questions_button.setObjectName("PrimaryButton")
        self.word_questions_button.clicked.connect(self._open_word_questions)
        root.addWidget(self.word_questions_button)
        self.handout_candidates_button = QPushButton("已整理讲义 · 查看题面与配对答案")
        self.handout_candidates_button.setObjectName("QuietButton")
        self.handout_candidates_button.clicked.connect(self._open_handout_candidates)
        root.addWidget(self.handout_candidates_button)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("LibrarySplitter")
        self.splitter.setChildrenCollapsible(False)

        left = CardFrame()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        self.result_summary = QLabel("选择范围后查找大题")
        self.result_summary.setObjectName("StatusInfo")
        self.result_summary.setWordWrap(True)
        left_layout.addWidget(self.result_summary)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        self.progress.setAccessibleName("题库读取进度")
        left_layout.addWidget(self.progress)
        self.results = QListWidget()
        self.results.setAccessibleName("大题列表")
        self.results.setWordWrap(True)
        self.results.setUniformItemSizes(False)
        self.results.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.currentRowChanged.connect(self._select_row)
        left_layout.addWidget(self.results, 1)
        self.splitter.addWidget(left)

        detail = CardFrame()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(20, 18, 20, 18)
        detail_layout.setSpacing(10)
        self.detail_title = QLabel("选择左侧大题")
        self.detail_title.setObjectName("CardTitle")
        self.detail_title.setWordWrap(True)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_paper = QLabel("大题、共享材料和前面小问的关联会保留在一起。")
        self.detail_paper.setObjectName("StatusInfo")
        self.detail_paper.setWordWrap(True)
        self.detail_source = QLabel("")
        self.detail_source.setObjectName("StatusInfo")
        self.detail_source.setWordWrap(True)
        self.detail_context = QLabel("")
        self.detail_context.setObjectName("StatusInfo")
        self.detail_context.setWordWrap(True)
        self.detail_view_button = QPushButton("查看题面与答案")
        self.detail_view_button.setObjectName("QuietButton")
        self.detail_view_button.setAccessibleName("查看当前完整大题题面答案与来源")
        self.detail_view_button.setEnabled(False)
        self.detail_view_button.clicked.connect(self._open_detail)
        self.preparation_button = QPushButton("选为备课参考…")
        self.preparation_button.setObjectName("QuietButton")
        self.preparation_button.setAccessibleName("选择当前主题的作答单元并预览备课参考")
        self.preparation_button.setEnabled(False)
        self.preparation_button.clicked.connect(self._send_preparation_reference)
        self.add_button = QPushButton("加入题篮")
        self.add_button.setAccessibleName("将当前完整大题加入题篮")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_current)
        self.basket_label = QLabel("")
        self.basket_label.setObjectName("StatusInfo")
        self.basket_label.setWordWrap(True)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_paper)
        detail_layout.addWidget(self.detail_source)
        detail_layout.addWidget(self.detail_context)
        detail_layout.addStretch(1)
        detail_layout.addWidget(self.basket_label)
        detail_layout.addWidget(self.detail_view_button)
        detail_layout.addWidget(self.preparation_button)
        detail_layout.addWidget(self.add_button)
        self.splitter.addWidget(detail)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([360, 640])
        root.addWidget(self.splitter, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page_scroll(content))
        self._refresh_basket_label()
        # Defer the first tree read until the window has been laid out.  This
        # keeps opening the desktop app responsive while the reader validates
        # its immutable local snapshot in the background.
        QTimer.singleShot(0, self._begin_curriculum_load)

    @staticmethod
    def _new_curriculum_combo(accessible_name: str, object_name: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(object_name)
        combo.setAccessibleName(accessible_name)
        combo.setMinimumWidth(0)
        combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        combo.setToolTip(accessible_name)
        return combo

    def _begin_curriculum_load(self) -> None:
        if self._curriculum_loading:
            return
        loader = getattr(self.facade, "curriculum_catalog", None)
        if not callable(loader):
            self.curriculum_hint.setText("教材目录暂未接入；可使用关键词查找大题。")
            return
        self._curriculum_loading = True
        self.curriculum_hint.setText("正在读取教材目录…")
        self.tasks.submit(
            "读取教材目录",
            loader,
            on_success=self._apply_curriculum_catalog,
            on_failure=self._curriculum_load_failed,
        )

    def _curriculum_load_failed(self, _message: str) -> None:
        self._curriculum_loading = False
        self.curriculum_hint.setText("教材目录暂时无法读取，可先用关键词查找大题。")

    def _apply_curriculum_catalog(self, value: object) -> None:
        self._curriculum_loading = False
        if not isinstance(value, dict) or not isinstance(value.get("volumes"), list):
            self.curriculum_hint.setText("教材目录格式暂时不可用，可先用关键词查找大题。")
            return
        self._curriculum_catalog = value
        volumes = [
            item
            for item in value["volumes"]
            if isinstance(item, dict)
            and isinstance(item.get("volume_id"), str)
            and isinstance(item.get("volume_title"), str)
        ]
        with QSignalBlocker(self.volume_combo):
            self.volume_combo.clear()
            self.volume_combo.addItem("全部教材册", None)
            for volume in volumes:
                count = _mapping_count(volume.get("mapping_counts"))
                label = str(volume["volume_title"])
                if count is not None:
                    label += f"（{count}题）"
                self.volume_combo.addItem(label, volume["volume_id"])
        self._reset_chapter_combo()
        self._reset_section_combo()
        self.curriculum_hint.setText(
            "只按已有教材目录映射筛选；没有明确映射的小问不会被自动归类。"
        )
        self._update_curriculum_scope_state()

    def _volume_record(self, volume_id: object) -> dict[str, object] | None:
        if not isinstance(self._curriculum_catalog, dict) or not isinstance(
            self._curriculum_catalog.get("volumes"), list
        ):
            return None
        for item in self._curriculum_catalog["volumes"]:
            if isinstance(item, dict) and item.get("volume_id") == volume_id:
                return item
        return None

    def _reset_chapter_combo(self) -> None:
        self.chapter_combo.clear()
        self.chapter_combo.addItem("先选择教材册", None)
        self.chapter_combo.setEnabled(False)

    def _reset_section_combo(self) -> None:
        self.section_combo.clear()
        self.section_combo.addItem("先选择教材章", None)
        self.section_combo.setEnabled(False)

    def _volume_changed(self, _index: int) -> None:
        volume_id = self.volume_combo.currentData()
        self._reset_chapter_combo()
        self._reset_section_combo()
        volume = self._volume_record(volume_id)
        if volume is None:
            return
        chapters = volume.get("chapters")
        if not isinstance(chapters, list):
            return
        self.chapter_combo.clear()
        self.chapter_combo.addItem("全部教材章", None)
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_id = chapter.get("chapter_id")
            title = chapter.get("chapter_title")
            if not isinstance(chapter_id, str) or not isinstance(title, str):
                continue
            count = _mapping_count(chapter.get("mapping_counts"))
            label = title + (f"（{count}题）" if count is not None else "")
            self.chapter_combo.addItem(label, chapter_id)
        self.chapter_combo.setEnabled(self.chapter_combo.count() > 1)

    def _chapter_changed(self, _index: int) -> None:
        volume = self._volume_record(self.volume_combo.currentData())
        self._reset_section_combo()
        if volume is None:
            return
        chapter_id = self.chapter_combo.currentData()
        if chapter_id is None:
            return
        chapters = volume.get("chapters")
        if not isinstance(chapters, list):
            return
        chapter = next(
            (
                item
                for item in chapters
                if isinstance(item, dict) and item.get("chapter_id") == chapter_id
            ),
            None,
        )
        if not isinstance(chapter, dict) or not isinstance(chapter.get("sections"), list):
            return
        self.section_combo.clear()
        self.section_combo.addItem("全部教材小节", None)
        for section in chapter["sections"]:
            if not isinstance(section, dict):
                continue
            section_key = section.get("section_key")
            label = section.get("display_label_zh") or section.get("section_title")
            if not isinstance(section_key, str) or not isinstance(label, str):
                continue
            count = _mapping_count(section.get("mapping_counts"))
            self.section_combo.addItem(
                label + (f"（{count}题）" if count is not None else ""),
                section_key,
            )
        self.section_combo.setEnabled(self.section_combo.count() > 1)

    def _section_changed(self, _index: int) -> None:
        # Kept as a named slot for keyboard accessibility and future instant
        # search; the teacher presses the existing primary “查找大题” button
        # after choosing a level, avoiding three expensive reads in a row.
        return

    def _clear_curriculum(self) -> None:
        with QSignalBlocker(self.volume_combo):
            self.volume_combo.setCurrentIndex(0)
        self._volume_changed(0)

    def _curriculum_selector(self) -> dict[str, str]:
        # Only scopes with an explicit active mapping join may carry this
        # selector.  In particular, never leak a previous master selection
        # into Wave1 (which has no canonical curriculum join) or the personal
        # Word lane (which intentionally remains unmapped).
        if self.scope.currentData() not in {"master", "supplemental"}:
            return {}
        selector: dict[str, str] = {}
        volume_id = self.volume_combo.currentData()
        chapter_id = self.chapter_combo.currentData()
        section = self.section_combo.currentData()
        if isinstance(volume_id, str) and volume_id:
            selector["volume_id"] = volume_id
        if isinstance(chapter_id, str) and chapter_id:
            selector["chapter_id"] = chapter_id
        if isinstance(section, str) and section:
            selector["section"] = section
        return selector

    def _update_curriculum_scope_state(self) -> None:
        personal = self.scope.currentData() == PERSONAL_HANDOUT_SCOPE
        supported = self.scope.currentData() in {"master", "supplemental"}
        visible = not personal
        self.curriculum_label.setVisible(visible)
        self.curriculum_hint.setVisible(visible)
        self.clear_curriculum_button.setVisible(visible)
        for combo in (self.volume_combo, self.chapter_combo, self.section_combo):
            combo.setEnabled(visible and supported and combo.count() > 1)
        if visible and not supported:
            self.curriculum_hint.setText("该题库暂时没有明确教材映射，仍可用关键词查找。")
        elif visible and self._curriculum_catalog is not None:
            self.curriculum_hint.setText(
                "只按已有教材目录映射筛选；没有明确映射的小问不会被自动归类。"
            )

    def _refresh_basket_label(self) -> None:
        try:
            self.basket_label.setText(f"题篮：{len(self.facade.basket())} 个完整大题")
        except Exception:
            self.basket_label.setText("题篮状态暂时无法读取")

    def search(self) -> None:
        if self._loading:
            return
        self._invalidate_detail(close_dialog=True)
        self._loading = True
        self.search_button.setEnabled(False)
        self.progress.setVisible(True)
        set_status(self.result_summary, "info", "正在读取并检索完整大题…")
        scope = str(self.scope.currentData())
        query = self.query.text().strip()
        state = self.handout_state.currentData() if scope == PERSONAL_HANDOUT_SCOPE else None
        curriculum = self._curriculum_selector()

        def _search() -> ThemeSearchResult:
            if scope == PERSONAL_HANDOUT_SCOPE:
                return self.facade.search_personal_handouts(
                    query=query, state=state, limit=40
                )
            return self.facade.search_themes(
                scope=scope,
                query=query,
                limit=40,
                **curriculum,
            )

        self.tasks.submit(
            "查找我的讲义" if scope == PERSONAL_HANDOUT_SCOPE else "查找完整大题",
            _search,
            on_success=self._apply_results,
            on_failure=self._show_failure,
        )

    def _scope_changed(self, _index: int) -> None:
        is_personal = self.scope.currentData() == PERSONAL_HANDOUT_SCOPE
        self._update_curriculum_scope_state()
        self.handout_state.setVisible(is_personal)
        self.handout_label.setVisible(is_personal)
        self.query.setPlaceholderText(
            "讲义章节、包号或题目关键词"
            if is_personal
            else "知识点、题型或大题关键词"
        )

    def _apply_results(self, result: ThemeSearchResult) -> None:
        self._invalidate_detail(close_dialog=True)
        self._loading = False
        self.search_button.setEnabled(True)
        self.progress.setVisible(False)
        self._cards = list(result.cards)
        self.results.clear()
        for card in self._cards:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, card.key)
            unit_count = card.display_atomic_units or card.atomic_total
            item.setText(f"{card.title_zh}\n{card.paper_title_zh}\n{unit_count} 个作答单元 · {card.source_zh}")
            item.setToolTip(f"{card.title_zh}\n{card.paper_title_zh}")
            self.results.addItem(item)
        suffix = "（还有更多，可补充关键词缩小范围）" if result.has_more else ""
        self.result_summary.setText(
            f"找到 {result.total_themes} 道大题，当前显示 {len(result.cards)} 道{suffix}"
        )
        if result.pending_atomic_parts:
            self.result_summary.setText(
                self.result_summary.text()
                + f"\n本题库另有 {result.pending_atomic_parts} 个作答单元待补大题归属"
                + f"（本次匹配 {result.pending_matched_atomic_parts} 个），未计入上方大题。"
                + "原资料保留，需补齐完整材料后选用。"
            )
        if self._cards:
            self.results.setCurrentRow(0)
        else:
            self._clear_detail("没有找到匹配大题")
            self.detail_paper.setText("可更换关键词、题库范围或讲义状态筛选。")

    def _show_failure(self, message: str) -> None:
        self._invalidate_detail(close_dialog=True)
        self._loading = False
        self.search_button.setEnabled(True)
        self.progress.setVisible(False)
        self.results.clear()
        self._cards = []
        set_status(
            self.result_summary,
            "error",
            message + " 可点击“查找大题”重试。",
        )
        self._clear_detail("本次检索未完成")

    def _clear_detail(self, message: str) -> None:
        self.preparation_button.setEnabled(False)
        self.detail_title.setText(message)
        self.detail_paper.setText("")
        self.detail_source.setText("")
        self.detail_context.setText("")
        self.detail_view_button.setText("查看题面与答案")
        self.detail_view_button.setEnabled(False)
        self.add_button.setEnabled(False)

    def _select_row(self, row: int) -> None:
        self._invalidate_detail(close_dialog=True)
        if not 0 <= row < len(self._cards):
            self._clear_detail("选择左侧大题")
            return
        card = self._cards[row]
        self.detail_title.setText(card.title_zh)
        self.detail_paper.setText(
            f"来源卷：{card.paper_title_zh}\n{card.page_zh} · 共 {card.display_atomic_units or card.atomic_total} 个作答单元"
        )
        self.detail_source.setText(f"来源信息：{card.source_zh}")
        self.detail_context.setText(f"共同材料：{card.shared_context_zh}")
        self.add_button.setEnabled(True)
        self.add_button.setText("加入题篮")
        if card.scope == PERSONAL_HANDOUT_SCOPE:
            self.detail_view_button.setText("讲义详情暂未接入")
            self.detail_view_button.setEnabled(False)
            self.detail_context.setText(
                f"共同材料：{card.shared_context_zh}\n"
                "这是旧版讲义汇总。新导入的 Word 请点上方“逐题预览与挑选”；已整理讲义请进入对应题面与答案入口。"
            )
            self.add_button.setEnabled(False)
            self.add_button.setText("请在逐题预览中选题")
            return
        detail_loader = getattr(self.facade, "library_theme_detail", None)
        if not callable(detail_loader):
            self.detail_view_button.setText("题图详情暂不可用")
            self.detail_view_button.setEnabled(False)
            return
        generation = self._detail_generation
        self._selected_detail_key = card.key
        self.detail_view_button.setText("正在读取题面…")
        self.detail_view_button.setEnabled(False)
        self._detail_task_id = self.tasks.submit(
            "读取完整大题详情",
            lambda: detail_loader(card),
            on_success=lambda value: self._detail_loaded(
                generation, card.key, value
            ),
            on_failure=lambda message: self._detail_failed(
                generation, card.key, message
            ),
        )

    def _invalidate_detail(self, *, close_dialog: bool) -> None:
        self.preparation_button.setEnabled(False)
        self._detail_generation += 1
        if self._detail_task_id:
            cancel = getattr(self.tasks, "cancel", None)
            if callable(cancel):
                cancel(self._detail_task_id)
        self._detail_task_id = None
        self._selected_detail = None
        self._selected_detail_key = None
        self.detail_view_button.setText("查看题面与答案")
        self.detail_view_button.setEnabled(False)
        if close_dialog and self._detail_dialog is not None:
            try:
                self._detail_dialog.close()
            except RuntimeError:
                pass
            self._detail_dialog = None

    def _detail_loaded(
        self, generation: int, card_key: str, value: object
    ) -> None:
        if (
            generation != self._detail_generation
            or card_key != self._selected_detail_key
        ):
            return
        self._detail_task_id = None
        row = self.results.currentRow()
        if (
            not isinstance(value, LibraryThemeDetail)
            or value.key != card_key
            or not 0 <= row < len(self._cards)
            or value.scope != self._cards[row].scope
        ):
            self._detail_failed(
                generation, card_key, "本地主题详情格式暂时不可用"
            )
            return
        self._selected_detail = value
        self.preparation_button.setEnabled(bool(value.parts))
        self.detail_view_button.setText("查看题面与答案")
        self.detail_view_button.setEnabled(True)
        set_status(
            self.detail_context,
            "success",
            f"共同材料：{value.context_zh}\n"
            f"题面详情已就绪：{len(value.parts)} 个作答单元。",
        )

    def _detail_failed(
        self, generation: int, card_key: str, message: str
    ) -> None:
        if (
            generation != self._detail_generation
            or card_key != self._selected_detail_key
        ):
            return
        self._detail_task_id = None
        self._selected_detail = None
        self.preparation_button.setEnabled(False)
        self.detail_view_button.setText("题面详情暂不可用")
        self.detail_view_button.setEnabled(False)
        set_status(
            self.detail_context,
            "attention",
            f"题面详情读取失败：{message}。仍可将完整大题加入题篮。",
        )

    def _send_preparation_reference(self) -> None:
        detail = self._selected_detail
        row = self.results.currentRow()
        if (
            detail is not None
            and detail.parts
            and 0 <= row < len(self._cards)
            and detail.key == self._cards[row].key == self._selected_detail_key
            and detail.scope == self._cards[row].scope
        ):
            self.preparation_reference_requested.emit(detail)

    def _open_detail(self) -> None:
        detail = self._selected_detail
        if detail is None:
            return
        image_loader = getattr(self.facade, "library_image", None)
        if not callable(image_loader):
            set_status(self.detail_context, "attention", "本地题图读取接口暂不可用。")
            return
        if self._detail_dialog is not None:
            try:
                self._detail_dialog.raise_()
                self._detail_dialog.activateWindow()
                return
            except RuntimeError:
                self._detail_dialog = None
        dialog = LibraryDetailDialog(
            detail, self.tasks, image_loader, parent=self.window()
        )
        self._detail_dialog = dialog
        dialog.preparation_image_requested.connect(self.preparation_image_requested)
        dialog.destroyed.connect(lambda: setattr(self, "_detail_dialog", None))
        dialog.show()

    def _open_word_questions(self) -> None:
        from .word_question_dialog import WordQuestionDialog

        dialog = WordQuestionDialog(self.facade, self.tasks, self.window())
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.preparation_reference is not None:
            self.word_reference_requested.emit(dialog.preparation_reference)
        dialog.deleteLater()

    def _open_handout_candidates(self) -> None:
        from .handout_candidate_dialog import HandoutCandidateDialog

        if self._handout_candidate_dialog is not None and self._handout_candidate_dialog.isVisible():
            self._handout_candidate_dialog.raise_()
            self._handout_candidate_dialog.activateWindow()
            return
        self._handout_candidate_dialog = HandoutCandidateDialog(self.facade, self.tasks, self.window())
        self._handout_candidate_dialog.destroyed.connect(
            lambda: setattr(self, "_handout_candidate_dialog", None)
        )
        self._handout_candidate_dialog.show()

    def _add_current(self) -> None:
        row = self.results.currentRow()
        if not 0 <= row < len(self._cards):
            return
        try:
            count = self.facade.add_theme_to_basket(self._cards[row])
        except Exception as exc:
            message = getattr(exc, "message_zh", "题篮暂时无法保存，请稍后重试。")
            QMessageBox.warning(self, "未加入题篮", str(message))
            return
        self._refresh_basket_label()
        self.add_button.setText("已加入题篮")
        self.basket_changed.emit(count)

    def resizeEvent(self, event: QResizeEvent) -> None:
        compact = event.size().width() < 820
        narrow_filters = event.size().width() < 600
        self.search_row.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow_filters
            else QBoxLayout.Direction.LeftToRight
        )
        self.curriculum_row.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow_filters
            else QBoxLayout.Direction.LeftToRight
        )
        if compact != self._compact:
            self._compact = compact
            self.splitter.setOrientation(
                Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal
            )
            self.splitter.setSizes([260, 360] if compact else [360, 640])
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._invalidate_detail(close_dialog=True)
        super().closeEvent(event)


__all__ = ["LibraryPage"]
