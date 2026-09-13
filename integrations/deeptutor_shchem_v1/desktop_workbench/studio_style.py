"""Shared native design system: calm green, airy surfaces and clear hierarchy."""
TOKENS = {
    "surface_app": "#F5F7F8", "surface_panel": "#FFFFFF", "surface_subtle": "#F0F4F2",
    "surface_rail": "#FAFCFB", "ink": "#233A32", "ink_muted": "#60746B",
    "brand": "#247F58", "brand_dark": "#195F41", "brand_soft": "#E3F2E9",
    "attention": "#865A12", "danger": "#B42318", "success": "#216846",
    "line": "#DCE6E0", "line_focus": "#247F58",
}


def _build_style(t):
    return f'''
QWidget {{ font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC"; font-size: 14px; color: {t['ink']}; background: transparent; }}
QMainWindow, QDialog, QWidget#WindowRoot, QStackedWidget, QScrollArea#PageScroll,
QWidget#PageViewport, QWidget#PageContent {{ background: {t['surface_app']}; }}
QLabel {{ background: transparent; }}
QFrame#SideRail {{ background: {t['surface_rail']}; border-right: 1px solid {t['line']}; }}
QLabel#Brand {{ font-size: 18px; font-weight: 700; color: {t['brand_dark']}; }}
QLabel#BrandSub, QLabel#RailSection {{ font-size: 11px; color: {t['ink_muted']}; }}
QLabel#RailSection {{ margin-top: 12px; margin-bottom: 4px; }}
QPushButton#NavButton, QPushButton#SettingsButton {{ text-align: left; padding: 10px 12px; min-height: 26px; border: 1px solid transparent; border-radius: 9px; background: transparent; color: {t['ink_muted']}; }}
QPushButton#NavButton:hover, QPushButton#SettingsButton:hover {{ background: {t['surface_subtle']}; color: {t['brand_dark']}; }}
QPushButton#NavButton:checked {{ background: {t['brand_soft']}; color: {t['brand_dark']}; font-weight: 700; }}
QFrame#TopBar {{ background: {t['surface_panel']}; border-bottom: 1px solid {t['line']}; }}
QLabel#TopTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#PageTitle {{ font-size: 26px; font-weight: 700; }}
QLabel#PageSubtitle, QLabel#MutedLabel, QLabel#StatusInfo {{ color: {t['ink_muted']}; }}
QLabel#CardTitle {{ font-size: 17px; font-weight: 700; }}
QLabel#HeroTitle {{ font-size: 28px; font-weight: 700; color: {t['brand_dark']}; }}
QLabel#Badge {{ color: {t['brand_dark']}; background: {t['brand_soft']}; border-radius: 5px; padding: 4px 9px; font-size: 11px; }}
QLabel#MetricTitle {{ color: {t['ink_muted']}; font-size: 12px; }}
QLabel#MetricValue {{ font-size: 24px; font-weight: 700; color: {t['brand_dark']}; }}
QFrame#Card, QFrame#ThemeCard {{ background: white; border: 1px solid {t['line']}; border-radius: 14px; }}
QFrame#HeroCard {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E5F3EA, stop:1 #F6F9ED); border: 1px solid #D2E7D8; border-radius: 18px; }}
QFrame#TemplateCard:hover {{ border-color: #85B899; }}
QFrame#TemplateCard {{ background: white; border: 1px solid {t['line']}; border-radius: 12px; }}
QFrame#CollapsibleContent, QFrame#QuestionDetails, QFrame#SharedMaterial {{ background: {t['surface_subtle']}; border: 1px solid {t['line']}; border-radius: 9px; }}
QFrame#QuestionRow {{ background: white; border: 0; border-bottom: 1px solid {t['line']}; }}
QLabel#QuestionResponse, QLabel#QuestionMeta, QLabel#ThemeMeta, QLabel#SharedSummary {{ color: {t['ink_muted']}; }}
QPushButton {{ background: {t['brand']}; color: white; border: 1px solid {t['brand']}; border-radius: 8px; padding: 8px 14px; min-height: 22px; }}
QPushButton:hover {{ background: {t['brand_dark']}; border-color: {t['brand_dark']}; }}
QPushButton:pressed {{ background: #124C33; }}
QPushButton#PrimaryAction {{ font-weight: 600; }}
QPushButton#QuietButton {{ background: white; color: {t['ink']}; border: 1px solid {t['line']}; }}
QPushButton#QuietButton:hover {{ background: {t['brand_soft']}; border-color: #9FC9AF; }}
QPushButton#Chip {{ background: white; color: {t['ink_muted']}; border-color: {t['line']}; border-radius: 14px; padding: 4px 12px; min-height: 18px; }}
QPushButton#Chip:checked {{ background: {t['brand_soft']}; color: {t['brand_dark']}; border-color: #9FC9AF; }}
QPushButton:disabled {{ background: {t['surface_subtle']}; color: #7C8A83; border-color: {t['line']}; }}
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
QStatusBar {{ background: white; border-top: 1px solid {t['line']}; color: {t['ink_muted']}; font-size: 11px; }}
QProgressBar {{ border: 0; border-radius: 5px; background: {t['surface_subtle']}; text-align: center; min-height: 16px; }}
QProgressBar::chunk {{ background: #70B78B; border-radius: 5px; }}
QLabel#StatusSuccess {{ color: {t['success']}; }}
QLabel#StatusAttention {{ color: {t['attention']}; }}
QLabel#StatusError {{ color: {t['danger']}; }}
QLabel#TimerDisplay {{ font-size: 84px; font-weight: 600; color: {t['brand_dark']}; }}
QLabel#DrawDisplay {{ font-size: 40px; font-weight: 700; color: {t['brand_dark']}; }}
QSlider::groove:horizontal {{ background: {t['line']}; height: 6px; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {t['brand']}; width: 16px; margin: -5px 0; border-radius: 8px; }}
'''

WORKBENCH_STYLE = _build_style(TOKENS)
