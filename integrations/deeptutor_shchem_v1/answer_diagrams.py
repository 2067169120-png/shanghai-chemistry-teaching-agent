"""Editable chemistry answer diagrams from a shared, deterministic geometry."""

from __future__ import annotations

import html
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 360, 240
_INK, _BLUE = "#172d43", "#17619c"


def _geometry(key: str) -> list[tuple]:
    shapes: list[tuple] = []
    if key == "chlorine_atom_shells":
        shapes.append(("circle", 130, 111, 20, "white", _INK))
        shapes.append(("text", 130, 111, "+17", 18))
        for radius, count in ((38, 2), (63, 8), (88, 7)):
            shapes.append(("circle", 130, 111, radius, "none", _INK))
            for index in range(count):
                angle = -math.pi / 2 + index * 2 * math.pi / count
                x, y = 130 + radius * math.cos(angle), 111 + radius * math.sin(angle)
                shapes.append(("circle", x, y, 3.5, _BLUE, _BLUE))
        for y, value in ((62, "K: 2"), (110, "L: 8"), (158, "M: 7")):
            shapes.append(("text", 284, y, value, 21))
        shapes.append(("text", 130, 223, "Cl: 2, 8, 7", 20))
    elif key == "chloride_lewis":
        shapes.append(("text", 171, 119, "Cl", 48))
        for x, y in (
            (161, 76),
            (181, 76),
            (161, 162),
            (181, 162),
            (128, 108),
            (128, 130),
            (214, 108),
            (214, 130),
        ):
            shapes.append(("circle", x, y, 3.7, _INK, _INK))
        for points in (
            ((114, 61), (101, 61), (101, 177), (114, 177)),
            ((228, 61), (241, 61), (241, 177), (228, 177)),
        ):
            shapes.append(("line", points))
        shapes.append(("text", 262, 58, "−", 34))
    else:
        raise ValueError("未知的补充答案图。")
    return shapes


def answer_diagram_svg(key: str) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for shape in _geometry(key):
        if shape[0] == "circle":
            _, x, y, r, fill, stroke = shape
            parts.append(
                f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
            )
        elif shape[0] == "text":
            _, x, y, text, size = shape
            parts.append(
                f'<text x="{x}" y="{y}" text-anchor="middle" dominant-baseline="central" font-family="Arial, sans-serif" font-size="{size}" fill="{_INK}">{html.escape(text)}</text>'
            )
        else:
            points = " ".join(f"{x},{y}" for x, y in shape[1])
            parts.append(
                f'<polyline points="{points}" fill="none" stroke="{_INK}" stroke-width="2"/>'
            )
    return "".join(parts) + "</svg>"


def answer_diagram_png(key: str, *, scale: int = 3) -> bytes:
    image = Image.new("RGB", (WIDTH * scale, HEIGHT * scale), "white")
    draw = ImageDraw.Draw(image)
    font_path = next(
        (
            str(path)
            for path in (
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            )
            if path.is_file()
        ),
        None,
    )
    for shape in _geometry(key):
        if shape[0] == "circle":
            _, x, y, r, fill, stroke = shape
            draw.ellipse(
                ((x - r) * scale, (y - r) * scale, (x + r) * scale, (y + r) * scale),
                fill=None if fill == "none" else fill,
                outline=stroke,
                width=max(1, round(1.6 * scale)),
            )
        elif shape[0] == "text":
            _, x, y, text, size = shape
            font = (
                ImageFont.truetype(font_path, size * scale)
                if font_path
                else ImageFont.load_default(size=size * scale)
            )
            draw.text((x * scale, y * scale), text, font=font, fill=_INK, anchor="mm")
        else:
            draw.line(
                [(x * scale, y * scale) for x, y in shape[1]],
                fill=_INK,
                width=2 * scale,
            )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
