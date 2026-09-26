"""Classroom projection layout; measured defaults, not back-row validation."""

from dataclasses import replace


def classroom_layout(layout, *, draw, fonts):
    # Import lazily: the renderer owns image verification and source tracking.
    from .desktop_preparation_renderer import _LayoutElement, _source_element

    def text(
        old, box, size, minimum, *, center=False, color="172C38", separator="\n\n"
    ):
        x, y, width, height = box
        return _source_element(
            draw=draw,
            fonts=fonts,
            x=x,
            y=y,
            width=width,
            height=height,
            source_texts=list(old.source_texts),
            source_paths=list(old.source_paths),
            preferred_px=size,
            minimum_px=minimum,
            color=color,
            bold=old.bold,
            align="center" if center else "left",
            valign="middle",
            paragraph_separator=separator,
            numbered=False,
        )

    items = list(layout.elements)
    source = [e for e in items if e.element_type == "text" and e.source_paths]
    if not source:
        return layout
    title = source[0]
    if layout.slide_type == "image":
        title = next(
            e for e in source if any(p.endswith("/title") for p in e.source_paths)
        )
    base = [
        _LayoutElement("rect", 0, 0, 1600, 900, fill="FFFFFF"),
        _LayoutElement("rect", 70, 156, 1460, 3, fill="397653"),
    ]
    if layout.slide_type == "cover":
        base = [_LayoutElement("rect", 0, 0, 1600, 900, fill="FFFFFF")]
        base.append(
            text(title, (90, 265, 1420, 165), 88, 66, center=True, color="24543D")
        )
        for supporting in source[1:]:
            base.append(
                text(
                    supporting,
                    (150, 490, 1300, 235),
                    40,
                    34,
                    center=True,
                    separator="\n",
                )
            )
    else:
        base.append(
            text(title, (70, 34, 1460, 110), 68, 54, center=True, color="24543D")
        )
        if layout.slide_type == "image":
            picture = next(e for e in items if e.element_type == "image")
            body = next(
                e
                for e in source
                if any("observation_prompt" in p for p in e.source_paths)
            )
            caption = next(
                e
                for e in source
                if any(p.startswith("/image_assets/") for p in e.source_paths)
            )
            # Keep the original bytes/hash; scale only the contain box.
            wide = picture.width / max(1, picture.height) >= 1.65
            if wide:
                # Use the image's actual bottom, not a fixed 135px text strip.
                # Dense but valid provider output exposed that the old strip
                # could overlap both the picture and the slide footer.
                for image_height in (460, 400, 340, 280, 220):
                    factor = min(1440 / picture.width, image_height / picture.height)
                    w, h = round(picture.width * factor), round(picture.height * factor)
                    text_top = 180 + h + 24
                    fitted_body = text(
                        body,
                        (80, text_top, 1440, 810 - text_top),
                        44,
                        40,
                        separator="\n",
                    )
                    if not fitted_body.overflow:
                        break
                base.append(
                    replace(
                        picture,
                        x=(1600 - w) // 2,
                        y=180,
                        width=w,
                        height=h,
                    )
                )
                base.append(fitted_body)
            else:
                factor = min(890 / picture.width, 610 / picture.height)
                w, h = round(picture.width * factor), round(picture.height * factor)
                base.append(
                    replace(
                        picture,
                        x=70 + (890 - w) // 2,
                        y=180 + (610 - h) // 2,
                        width=w,
                        height=h,
                    )
                )
                base.append(text(body, (1000, 195, 520, 590), 44, 36))
            base.append(text(caption, (75, 828, 1350, 54), 22, 20, separator="  |  "))
        elif layout.slide_type in {"comparison", "process"}:
            # Retain every source-bound cell and arrow; enlarge cell text, not
            # a flattened screenshot. The existing fitter reports overflow.
            for e in items:
                if e is title or e.structural and e.element_type == "text":
                    continue
                if e.element_type == "rect" and e.y < 170:
                    continue
                if layout.slide_type == "comparison" and e.y >= 300:
                    e = replace(
                        e,
                        y=260 + round((e.y - 320) * 1.32),
                        height=round(e.height * 1.32),
                    )
                if e.element_type == "text" and e.source_paths:
                    is_content = any("/content/" in p for p in e.source_paths)
                    box = (
                        (e.x, 171, e.width, 80)
                        if is_content
                        else (e.x, e.y, e.width, e.height)
                    )
                    base.append(
                        text(
                            e,
                            box,
                            38 if is_content else 40,
                            32,
                            center=e.align == "center",
                            color=e.color if e.color == "FFFFFF" else "172C38",
                            separator="\n",
                        )
                    )
                else:
                    base.append(e)
        else:
            for body in source[1:]:
                fitted_body = text(body, (92, 202, 1416, 604), 48, 40)
                if fitted_body.overflow:
                    # Remove blank paragraph gaps before shrinking text. All
                    # source strings and their ordering remain unchanged.
                    fitted_body = text(
                        body, (92, 202, 1416, 604), 48, 40, separator="\n"
                    )
                base.append(fitted_body)
        base.append(
            _LayoutElement(
                "text",
                1450,
                841,
                72,
                34,
                text=str(layout.page_no),
                font_px=22,
                color="68766E",
                align="right",
                structural=True,
            )
        )
    return replace(layout, elements=tuple(base), layout_variant="classroom-v2")
