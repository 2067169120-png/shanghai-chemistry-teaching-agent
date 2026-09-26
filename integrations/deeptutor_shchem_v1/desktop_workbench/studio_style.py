"""Shared native style. Font family comes from the verified Qt application font."""
from .studio_control_style import control_style

TOKENS = {
    "surface_app": "#F7F6F0", "surface_panel": "#FFFFFF", "surface_subtle": "#F0F2EA",
    "surface_rail": "#EDF0E8", "ink": "#243B31", "ink_muted": "#5D6B5E",
    "brand": "#2D634E", "brand_dark": "#204B3A", "brand_soft": "#E5EDDE",
    "attention": "#865A12", "danger": "#B42318", "success": "#216846",
    "line": "#D9DDD1", "line_focus": "#416A54",
}


def _build_style(t):
    return f'''
QWidget {{ font-size: 11pt; color: {t['ink']}; background: transparent; }}
QMainWindow, QDialog, QWidget#WindowRoot, QStackedWidget, QScrollArea#PageScroll,
QWidget#PageViewport, QWidget#PageContent {{ background: {t['surface_app']}; }}
QLabel {{ background: transparent; }}
QFrame#SideRail {{ background: {t['surface_rail']}; border-right: 1px solid {t['line']}; }}
QLabel#Brand {{ font-size: 14pt; font-weight: 600; color: {t['brand_dark']}; padding-bottom: 3px; }}
QLabel#BrandSub, QLabel#RailSection {{ font-size: 9.5pt; color: {t['ink_muted']}; }}
QLabel#RailSection {{ margin-top: 12px; margin-bottom: 4px; }}
QPushButton#NavButton, QPushButton#SettingsButton {{ text-align: left; padding: 10px 12px; min-height: 26px; border: 1px solid transparent; border-radius: 7px; background: transparent; color: {t['ink_muted']}; }}
QPushButton#NavButton:hover, QPushButton#SettingsButton:hover {{ background: {t['surface_subtle']}; color: {t['brand_dark']}; }}
QPushButton#NavButton:checked {{ background: {t['brand_soft']}; color: {t['brand_dark']}; border-left: 3px solid {t['brand']}; padding-left: 10px; font-weight: 600; }}
QPushButton#NavButton:focus, QPushButton#SettingsButton:focus {{ border-color: {t['line_focus']}; }}
QFrame#TopBar {{ background: {t['surface_panel']}; border-bottom: 1px solid {t['line']}; }}
QLabel#TopTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#PageTitle {{ font-size: 20pt; font-weight: 600; color: {t['brand_dark']}; }}
QLabel#PageSubtitle, QLabel#MutedLabel, QLabel#StatusInfo {{ color: {t['ink_muted']}; }}
QLabel#CardTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#HeroTitle {{ font-size: 21pt; font-weight: 600; color: {t['brand_dark']}; }}
QLabel#Badge {{ color: {t['brand_dark']}; background: {t['brand_soft']}; border-radius: 5px; padding: 4px 9px; font-size: 9.5pt; }}
QLabel#MetricTitle {{ color: {t['ink_muted']}; font-size: 10pt; }}
QLabel#MetricValue {{ font-size: 18pt; font-weight: 600; color: {t['brand_dark']}; }}
QFrame#Card, QFrame#ThemeCard {{ background: white; border: 1px solid {t['line']}; border-radius: 11px; }}
QFrame#HeroCard {{ background: #EDF2E6; border: 1px solid {t['line']}; border-radius: 14px; }}
QFrame#TemplateCard:hover {{ border-color: #85B899; }}
QFrame#TemplateCard {{ background: white; border: 1px solid {t['line']}; border-radius: 12px; }}
QFrame#CollapsibleContent, QFrame#QuestionDetails, QFrame#SharedMaterial {{ background: {t['surface_subtle']}; border: 1px solid {t['line']}; border-radius: 9px; }}
QFrame#QuestionRow {{ background: white; border: 0; border-bottom: 1px solid {t['line']}; }}
QLabel#QuestionResponse, QLabel#QuestionMeta, QLabel#ThemeMeta, QLabel#SharedSummary {{ color: {t['ink_muted']}; }}
QPushButton {{ background: {t['brand']}; color: white; border: 1px solid {t['brand']}; border-radius: 8px; padding: 8px 14px; min-height: 22px; }}
QPushButton:hover {{ background: {t['brand_dark']}; border-color: {t['brand_dark']}; }}
QPushButton:pressed {{ background: #173B2C; }}
QPushButton#PrimaryAction {{ font-weight: 600; }}
QPushButton#QuietButton {{ background: white; color: {t['ink']}; border: 1px solid {t['line']}; }}
QPushButton#QuietButton:hover {{ background: {t['brand_soft']}; border-color: #A3B79A; }}
QPushButton#Chip {{ background: white; color: {t['ink_muted']}; border-color: {t['line']}; border-radius: 14px; padding: 4px 12px; min-height: 18px; }}
QPushButton#Chip:checked {{ background: {t['brand_soft']}; color: {t['brand_dark']}; border-color: #9FC9AF; }}
QPushButton:disabled, QPushButton#QuietButton:disabled, QPushButton#PrimaryAction:disabled {{ background: {t['surface_subtle']}; color: #7C877A; border-color: {t['line']}; }}
QPushButton#LinkButton, QPushButton#ThemeTitleButton {{ background: transparent; color: {t['brand_dark']}; border: 0; padding: 4px; }}
QPushButton:focus, QToolButton:focus {{ border-color: {t['line_focus']}; }}
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QListWidget, QTreeWidget, QTableWidget {{ background: white; color: {t['ink']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 8px; selection-background-color: {t['brand_soft']}; selection-color: {t['brand_dark']}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {t['line_focus']}; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox QAbstractItemView {{ background: white; color: {t['ink']}; selection-background-color: {t['brand_soft']}; selection-color: {t['brand_dark']}; }}
QListWidget::item {{ padding: 10px; border-bottom: 1px solid {t['surface_subtle']}; }}
QListWidget::item:selected {{ background: {t['brand_soft']}; color: {t['brand_dark']}; border-radius: 6px; }}
QListWidget#DropFileList {{ border: 1px dashed #9DB8A7; background: #FCFEFD; }}
QToolButton {{ padding: 5px; border: 1px solid transparent; border-radius: 6px; }}
QToolButton:hover {{ background: {t['brand_soft']}; }}
QTabWidget::pane {{ background: white; border: 1px solid {t['line']}; border-radius: 10px; }}
QTabBar::tab {{ background: {t['surface_subtle']}; color: {t['ink_muted']}; padding: 10px 16px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabBar::tab:selected {{ background: white; color: {t['brand_dark']}; font-weight: 600; }}
QTabBar::tab:hover {{ background: {t['brand_soft']}; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: #BDCEC3; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QSplitter::handle {{ background: {t['line']}; }}
QStatusBar {{ background: white; border-top: 1px solid {t['line']}; color: {t['ink_muted']}; font-size: 9.5pt; }}
QStatusBar QPushButton#QuietButton {{ padding: 4px 9px; min-height: 18px; font-size: 9.5pt; }}
QProgressBar {{ border: 0; border-radius: 5px; background: {t['surface_subtle']}; text-align: center; min-height: 16px; }}
QProgressBar::chunk {{ background: #70B78B; border-radius: 5px; }}
QLabel#StatusSuccess {{ color: {t['success']}; }}
QLabel#StatusAttention {{ color: {t['attention']}; }}
QLabel#StatusError {{ color: {t['danger']}; }}
QLabel#TimerDisplay {{ font-size: 63pt; font-weight: 600; color: {t['brand_dark']}; }}
QLabel#DrawDisplay {{ font-size: 30pt; font-weight: 600; color: {t['brand_dark']}; }}
QSlider::groove:horizontal {{ background: {t['line']}; height: 6px; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {t['brand']}; width: 16px; margin: -5px 0; border-radius: 8px; }}
QFrame#ExplorerSidebar {{ background: white; border: 1px solid {t['line']}; border-radius: 10px; }}
QFrame#ExplorerQuestionCard {{ background: white; border: 1px solid {t['line']}; border-radius: 9px; }}
QFrame#ExplorerQuestionCard:hover {{ border-color: #9FC9AF; }}
QFrame#ExplorerBasketFooter {{ background: {t['brand_soft']}; border: 1px solid #B8DCC7; border-radius: 10px; }}
QLabel#ExplorerEyebrow {{ font-size: 10pt; color: {t['brand']}; font-weight: 600; }}
QLabel#ExplorerExcerpt {{ font-size: 11pt; color: {t['ink']}; padding: 10px 0; }}
QTreeWidget#ExplorerFacetTree {{ border: 0; padding: 0; border-radius: 0; }}
QTreeWidget#ExplorerFacetTree::item {{ padding: 6px 2px; }}
QTreeWidget#ExplorerFacetTree::item:hover {{ background: {t['surface_subtle']}; }}
QTreeWidget#ExplorerFacetTree::item:selected {{ background: {t['brand_soft']}; color: {t['brand_dark']}; }}
QPushButton#ExplorerFilterChip {{ background: {t['brand_soft']}; color: {t['brand_dark']}; border: 1px solid #BFDFCD; padding: 3px 8px; min-height: 18px; border-radius: 5px; }}
QPushButton#ExplorerBasketButton {{ background: white; color: {t['brand_dark']}; border-color: #9FC9AF; font-weight: 600; }}
QFrame#DeskPanel {{ background: {t['surface_panel']}; border: 1px solid {t['line']}; border-radius: 11px; }}
QFrame#DeskBasket {{ background: #F0F3E9; border: 1px solid #D5DFCB; border-top: 3px solid {t['brand']}; border-radius: 11px; }}
QFrame#DeskEditing {{ background: {t['brand_soft']}; border: 1px solid #D5DFCB; border-left: 3px solid {t['brand']}; border-radius: 8px; }}
QLabel#DeskText {{ color: {t['ink']}; }}
QLabel#DeskCount {{ color: {t['brand_dark']}; font-size: 21pt; font-weight: 600; padding: 2px 0 4px 0; }}
QLabel#DeskMeta {{ color: {t['ink_muted']}; font-size: 9.5pt; }}
QLabel#DeskEmptyTitle {{ color: {t['ink']}; font-size: 13pt; font-weight: 600; }}
QLabel#DeskSelection {{ color: {t['ink_muted']}; font-size: 9.5pt; }}
QTableWidget#DeskWorkTable {{ border: 0; padding: 0; border-radius: 0; }}
QTableWidget#DeskWorkTable::item {{ padding: 10px 16px; border-bottom: 1px solid {t['surface_subtle']}; }}
QTableWidget#DeskWorkTable::item:hover {{ background: #F5F7EF; }}
QTableWidget#DeskWorkTable::item:selected {{ background: {t['brand_soft']}; color: {t['brand_dark']}; }}
QTableWidget#DeskWorkTable:focus {{ border: 1px solid {t['line_focus']}; }}
QHeaderView::section {{ background: {t['surface_subtle']}; color: {t['ink_muted']}; padding: 12px; border: 0; border-bottom: 1px solid {t['line']}; text-align: left; }}
QWidget#WorkActionBar {{ background: white; border-top: 1px solid {t['line']}; }}
'''

WORKBENCH_STYLE = _build_style(TOKENS) + control_style(TOKENS)
