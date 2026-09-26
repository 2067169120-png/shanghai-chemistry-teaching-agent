"""Visible indicator assets for controls whose native arrows are masked by QSS."""
from pathlib import Path


def control_style(tokens=None) -> str:
    assets = Path(__file__).with_name("studio_assets").as_posix()
    colors = tokens or {"surface_subtle": "#F0F2EA", "brand_soft": "#E5EDDE"}
    return f'''
QComboBox {{ padding-right: 32px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: 0px; }}
QComboBox::down-arrow {{ image: url("{assets}/down.svg"); width: 16px; height: 16px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 30px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 24px; height: 18px; border: 0px; background: {colors['surface_subtle']}; border-top-right-radius: 8px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; height: 18px; border: 0px; background: {colors['surface_subtle']}; border-bottom-right-radius: 8px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {colors['brand_soft']}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{assets}/up.svg"); width: 14px; height: 14px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{assets}/down.svg"); width: 14px; height: 14px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
QCheckBox::indicator:unchecked {{ image: url("{assets}/unchecked.svg"); }}
QCheckBox::indicator:checked {{ image: url("{assets}/checked.svg"); }}
QCheckBox::indicator:indeterminate {{ image: url("{assets}/mixed.svg"); }}
'''
