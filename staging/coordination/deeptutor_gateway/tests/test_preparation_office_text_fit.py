import math

import pytest
from PIL import Image, ImageDraw

from integrations.deeptutor_shchem_v1.desktop_preparation_renderer import (
    _FontBook, _fit_text, _text_length,
)


@pytest.mark.parametrize("text", [
    "弱电解质用可逆符号，不写成完全电离",
    "多元弱酸分步，不能一步写到2H⁺＋S²⁻",
    "CH₃COOH ⇌ H⁺＋CH₃COO⁻",
    "NH₃·H₂O ⇌ NH₄⁺＋OH⁻",
])
def test_real_table_cells_reserve_office_extent_without_rewriting(text):
    draw = ImageDraw.Draw(Image.new("RGB", (1600, 900)))
    fonts = _FontBook()
    wrapped, size, overflow = _fit_text(draw, fonts, text, width=552, height=156, preferred_px=32, minimum_px=28)
    assert wrapped.replace("\n", "") == text
    assert not overflow and size == 32
    font = fonts.get(size)
    assert all(_text_length(draw, line, font) + math.ceil(size * 0.4) <= 552 for line in wrapped.splitlines())
    if text.startswith("弱电解质"):
        assert len(wrapped.splitlines()) == 2
    if text.startswith("CH"):
        assert "CH₃COOH" in wrapped and "CH₃COO⁻" in wrapped


def test_multiline_cell_reserves_ppt_paragraph_line_advance():
    draw = ImageDraw.Draw(Image.new("RGB", (400, 100)))
    fonts = _FontBook()
    text, size, overflow = _fit_text(draw, fonts, "水溶液\n熔融", width=188, height=68, preferred_px=32, minimum_px=28)
    assert text == "水溶液\n熔融" and size == 28 and not overflow
    assert math.ceil(len(text.splitlines()) * size * 1.18) <= 68


def test_unbreakable_formula_or_too_many_lines_remain_visible_and_flagged():
    draw = ImageDraw.Draw(Image.new("RGB", (400, 100)))
    fonts = _FontBook()
    for source, width, height in [("(NH4)2SO4", 8, 80), ("甲\n乙\n丙\n丁", 188, 68)]:
        text, size, overflow = _fit_text(draw, fonts, source, width=width, height=height, preferred_px=32, minimum_px=28)
        assert text.replace("\n", "") == source.replace("\n", "")
        assert size == 28 and overflow
