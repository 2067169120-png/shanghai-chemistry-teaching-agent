"""Editable chemistry answer diagrams from a shared, deterministic geometry."""

from __future__ import annotations

import html
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 360, 240
_INK = "#172d43"


def answer_diagram_dimensions(key: str) -> tuple[int, int]:
    if key in {"chlorine_atom_shells", "oxygen_atom_shells"}:
        return 260, 160
    if key == "ferrate_single_electron_bridge":
        return 960, 215
    return WIDTH, HEIGHT


def _geometry(key: str) -> list[tuple]:
    shapes: list[tuple] = []
    if key in {"chlorine_atom_shells", "oxygen_atom_shells"}:
        # Shanghai HST compulsory vol.1, printed p129, figures 4.22/4.24:
        # nucleus circle, right-hand shell arcs, electron counts on the midline.
        # This is not a Bohr orbital illustration and has no electron dots.
        charge, counts = (
            (17, (2, 8, 7)) if key == "chlorine_atom_shells" else (8, (2, 6))
        )
        shapes.extend(
            [("circle", 90, 80, 26, "white", _INK), ("text", 90, 80, f"+{charge}", 26)]
        )
        for radius, count in zip((44, 67, 90), counts):
            shapes.extend(
                [
                    ("arc", 90, 80, radius, -50, -16),
                    ("arc", 90, 80, radius, 16, 50),
                    ("text", 90 + radius, 80, str(count), 24),
                ]
            )
    elif key == "ferrate_single_electron_bridge":
        # One electron-transfer arrow, Fe -> Cl, wholly on the reactant side.
        shapes.extend(
            [
                ("line", ((217, 70), (217, 42), (68, 42), (68, 70))),
                ("line", ((62, 62), (68, 70), (74, 62))),
                ("text", 143, 21, "6e⁻", 26),
                ("text", 68, 91, "+1", 22),
                ("text", 217, 91, "+3", 22),
            ]
        )
        for x, text in (
            (60, "3KClO"),
            (140, "+"),
            (244, "2Fe(OH)₃"),
            (350, "+"),
            (411, "4KOH"),
            (485, "="),
            (580, "2K₂FeO₄"),
            (682, "+"),
            (748, "3KCl"),
            (820, "+"),
            (900, "5H₂O"),
        ):
            shapes.append(("text", x, 126, text, 28))
        shapes.append(("text", 480, 186, "Fe: +3 → +6     Cl: +1 → −1", 25))
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
    width, height = answer_diagram_dimensions(key)
    font_family = (
        "Cambria, DejaVu Serif, serif"
        if key == "ferrate_single_electron_bridge"
        else "Arial, sans-serif"
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
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
                f'<text x="{x}" y="{y}" text-anchor="middle" dominant-baseline="central" font-family="{font_family}" font-size="{size}" fill="{_INK}">{html.escape(text)}</text>'
            )
        elif shape[0] == "arc":
            _, cx, cy, radius, start, end = shape
            a, b = math.radians(start), math.radians(end)
            x1, y1 = cx + radius * math.cos(a), cy + radius * math.sin(a)
            x2, y2 = cx + radius * math.cos(b), cy + radius * math.sin(b)
            parts.append(
                f'<path d="M {x1:.3f},{y1:.3f} A {radius},{radius} 0 0 1 {x2:.3f},{y2:.3f}" fill="none" stroke="{_INK}" stroke-width="1.6"/>'
            )
        else:
            points = " ".join(f"{x},{y}" for x, y in shape[1])
            parts.append(
                f'<polyline points="{points}" fill="none" stroke="{_INK}" stroke-width="2"/>'
            )
    return "".join(parts) + "</svg>"


def answer_diagram_png(key: str, *, scale: int = 3) -> bytes:
    width, height = answer_diagram_dimensions(key)
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    font_path = next(
        (
            str(path)
            for path in (
                # Cambria carries chemical Unicode subscripts/superscripts;
                # Windows Arial lacks several of these glyphs.
                Path("C:/Windows/Fonts/cambria.ttc")
                if key == "ferrate_single_electron_bridge"
                else Path("C:/Windows/Fonts/arial.ttf"),
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
        elif shape[0] == "arc":
            _, cx, cy, radius, start, end = shape
            draw.arc(
                (
                    (cx - radius) * scale,
                    (cy - radius) * scale,
                    (cx + radius) * scale,
                    (cy + radius) * scale,
                ),
                start,
                end,
                fill=_INK,
                width=max(1, round(1.6 * scale)),
            )
        else:
            draw.line(
                [(x * scale, y * scale) for x, y in shape[1]],
                fill=_INK,
                width=2 * scale,
            )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
