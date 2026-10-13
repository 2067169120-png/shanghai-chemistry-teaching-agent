from __future__ import annotations

from hashlib import sha256
import re

from lxml import etree as ET
import pytest

from integrations.deeptutor_shchem_v1 import word_static_eq
from integrations.deeptutor_shchem_v1.word_native_text import WordNativeTextReader


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
XML = "{http://www.w3.org/XML/1998/namespace}"
VALID = r" EQ \o(――→,\s\up7(催化剂)) "


def _run(*, instruction=None, text=None, boundary=None, script=None, font_pt=None):
    run = ET.Element(W + "r")
    if script is not None or font_pt is not None:
        properties = ET.SubElement(run, W + "rPr")
        if script is not None:
            ET.SubElement(properties, W + "vertAlign").set(W + "val", script)
        if font_pt is not None:
            ET.SubElement(properties, W + "sz").set(W + "val", str(font_pt * 2))
    if instruction is not None:
        node = ET.SubElement(run, W + "instrText")
        node.set(XML + "space", "preserve")
        node.text = instruction
    if text is not None:
        ET.SubElement(run, W + "t").text = text
    if boundary is not None:
        ET.SubElement(run, W + "fldChar").set(W + "fldCharType", boundary)
    return run


def _field(*instructions, separate=True, end=True, cache=()):
    runs = [_run(boundary="begin"), *instructions]
    if separate:
        runs.append(_run(boundary="separate"))
    runs.extend(cache)
    if end:
        runs.append(_run(boundary="end"))
    return runs


def _paragraph(*children):
    paragraph = ET.Element(
        W + "p",
        nsmap={"w": W[1:-1], "a": A[1:-1], "wp": WP[1:-1], "r": R[1:-1], "pic": PIC[1:-1]},
    )
    paragraph.extend(children)
    return paragraph


def _direct_property(run, name):
    node = run.find(W + "rPr/" + W + name)
    return None if node is None else node.get(W + "val", "true")


def _rejected(paragraph):
    before = ET.tostring(paragraph)
    assert word_static_eq.paragraph_fields(paragraph, _direct_property) == []
    assert "field_code_dependency" in WordNativeTextReader().read(paragraph).features
    assert ET.tostring(paragraph) == before


def _drawing(*, crop=None, link=None):
    drawing = ET.fromstring(
        f'''<w:drawing xmlns:w="{W[1:-1]}" xmlns:wp="{WP[1:-1]}"
            xmlns:a="{A[1:-1]}" xmlns:pic="{PIC[1:-1]}" xmlns:r="{R[1:-1]}">
          <wp:inline distT="0" distB="0" distL="0" distR="0">
            <wp:extent cx="511200" cy="28800"/>
            <wp:docPr id="7" name="原始箭头"/>
            <wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>
            <a:graphic><a:graphicData uri="{PIC[1:-1]}"><pic:pic>
              <pic:nvPicPr><pic:cNvPr id="7" name="arrow.png"/><pic:cNvPicPr/></pic:nvPicPr>
              <pic:blipFill><a:blip r:embed="rIdArrow"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
              <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="511200" cy="28800"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
              </pic:spPr>
            </pic:pic></a:graphicData></a:graphic>
          </wp:inline>
        </w:drawing>'''
    )
    if crop is not None:
        fill = drawing.find(".//" + PIC + "blipFill")
        rectangle = ET.Element(A + "srcRect", {key: str(value) for key, value in crop.items()})
        fill.insert(1, rectangle)
    if link is not None:
        drawing.find(".//" + A + "blip").set(R + "link", link)
    return drawing


def _image_field(drawing, *, same_run=False):
    prefix = _run(instruction=r" EQ \o(")
    suffix = _run(instruction=r",\s\up7(催化剂)) ")
    if same_run:
        prefix.append(drawing)
        prefix.append(suffix[0])
        return _paragraph(*_field(prefix))
    image_run = _run()
    image_run.append(drawing)
    return _paragraph(*_field(prefix, image_run, suffix))


def test_fragmented_charge_preserves_source_arrow_scripts_offsets_and_exact_span():
    fragments = [
        (r" EQ \o(――→,\s\up7(H", "baseline"),
        ("＋", "superscript"),
        (r"),\s\do5(△)) ", "baseline"),
    ]
    paragraph = _paragraph(
        _run(text="反应前"),
        *_field(*[_run(instruction=text, script=script, font_pt=12) for text, script in fragments]),
        _run(text="反应后"),
    )
    before = ET.tostring(paragraph)
    fields = word_static_eq.paragraph_fields(paragraph, _direct_property)
    assert len(fields) == 1
    field = fields[0]
    assert (field["run_start"], field["run_end"]) == (1, 6)
    assert field["base"] == {"kind": "text", "runs": [{"text": "――→", "script": "baseline"}]}
    assert field["overlays"] == [
        {"position": "up", "offset_pt": 7, "runs": [
            {"text": "H", "script": "baseline"}, {"text": "＋", "script": "superscript"},
        ]},
        {"position": "down", "offset_pt": 5, "runs": [{"text": "△", "script": "baseline"}]},
    ]
    assert field["transcription"] == "――→〔上移7pt：H^{＋}；下移5pt：△〕"
    assert field["instruction"] == "".join(text for text, _ in fragments)
    assert field["instruction_runs"] == [{"text": text, "script": script} for text, script in fragments]
    assert field["source_font_pt"] == 12
    assert re.fullmatch("[a-f0-9]{64}", field["field_sha256"])
    assert word_static_eq.paragraph_fields(paragraph, _direct_property) == fields
    native = WordNativeTextReader().read(paragraph)
    assert native.text == "反应前――→〔上移7pt：H^{＋}；下移5pt：△〕反应后"
    assert "static_eq_overlay" in native.features
    assert "field_code_dependency" not in native.features
    assert ET.tostring(paragraph) == before


def test_script_uses_original_character_offsets_even_when_labels_repeat():
    paragraph = _paragraph(*_field(
        _run(instruction=r" EQ \o(――→,\s\up7(H＋),\s\do5(H"),
        _run(instruction="＋", script="superscript"),
        _run(instruction=")) "),
    ))
    field = word_static_eq.paragraph_fields(paragraph)[0]
    assert field["overlays"][0]["runs"] == [{"text": "H＋", "script": "baseline"}]
    assert field["overlays"][1]["runs"] == [
        {"text": "H", "script": "baseline"}, {"text": "＋", "script": "superscript"},
    ]
    assert field["transcription"] == "――→〔上移7pt：H＋；下移5pt：H^{＋}〕"


def test_effective_style_reader_supplies_script_and_font_size():
    styles = ET.fromstring(f'''<w:styles xmlns:w="{W[1:-1]}">
        <w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
        <w:style w:type="character" w:styleId="Charge"><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>
    </w:styles>''')
    charge = _run(instruction="＋")
    properties = ET.Element(W + "rPr")
    ET.SubElement(properties, W + "rStyle").set(W + "val", "Charge")
    charge.insert(0, properties)
    paragraph = _paragraph(*_field(
        _run(instruction=r" EQ \o(――→,\s\up7(H"), charge, _run(instruction=")) "),
    ))
    reader = WordNativeTextReader(styles)
    field = word_static_eq.paragraph_fields(paragraph, lambda run, name: reader._run_property(run, None, name))[0]
    assert field["source_font_pt"] == 11
    assert field["transcription"] == "――→〔上移7pt：H^{＋}〕"


def test_multiple_closed_fields_preserve_order_and_inclusive_indices():
    paragraph = _paragraph(
        *_field(_run(instruction=VALID)), _run(text="中间"),
        *_field(_run(instruction=r" EQ \o(⇌,\s\do5(高温)) ")),
    )
    fields = word_static_eq.paragraph_fields(paragraph)
    assert [(field["run_start"], field["run_end"]) for field in fields] == [(0, 3), (5, 8)]
    assert [field["transcription"] for field in fields] == ["――→〔上移7pt：催化剂〕", "⇌〔下移5pt：高温〕"]
    assert fields[0]["field_sha256"] != fields[1]["field_sha256"]


def test_stdlib_elementtree_field_is_read_without_lxml_parent_methods():
    from xml.etree import ElementTree

    paragraph = ElementTree.fromstring(ET.tostring(_paragraph(*_field(_run(instruction=VALID)))))
    before = ElementTree.tostring(paragraph)
    field = word_static_eq.paragraph_fields(paragraph)[0]
    assert field["transcription"] == "――→〔上移7pt：催化剂〕"
    assert WordNativeTextReader().read(paragraph).text == field["transcription"]
    assert ElementTree.tostring(paragraph) == before


def test_accepted_eq_does_not_clear_an_unrelated_dynamic_field_dependency():
    paragraph = _paragraph(
        *_field(_run(instruction=VALID)), _run(text=" 页码："),
        *_field(_run(instruction=" PAGE "), cache=[_run(text="12")]),
    )
    fields = word_static_eq.paragraph_fields(paragraph)
    assert len(fields) == 1
    assert (fields[0]["run_start"], fields[0]["run_end"]) == (0, 3)
    native = WordNativeTextReader().read(paragraph)
    assert native.text == "――→〔上移7pt：催化剂〕 页码：12"
    assert {"static_eq_overlay", "field_code_dependency"} <= set(native.features)


@pytest.mark.parametrize("same_run,crop", [
    (False, None),
    (True, {"l": 10000, "t": 2000, "r": 30000, "b": 4000}),
], ids=["drawing-in-separate-run", "drawing-between-instructions-in-one-run"])
def test_first_operand_image_preserves_identity_dimensions_crop_and_source(same_run, crop):
    drawing = _drawing(crop=crop)
    paragraph = _image_field(drawing, same_run=same_run)
    before = ET.tostring(paragraph)
    field = word_static_eq.paragraph_fields(paragraph)[0]
    assert field["base"] == {
        "kind": "image",
        "container_xml_sha256": sha256(ET.tostring(drawing, method="c14n")).hexdigest(),
        "relationship_id": "rIdArrow",
        "width_mm": 14.2,
        "height_mm": 0.8,
        "crop": crop or {"l": 0, "t": 0, "r": 0, "b": 0},
    }
    assert field["transcription"] == "【待查看原文：图片或图形】〔上移7pt：催化剂〕"
    assert field["instruction"] == " EQ \\o(\ufffc,\\s\\up7(催化剂)) "
    assert field["instruction_runs"] == [
        {"text": r" EQ \o(", "script": "baseline"},
        {"text": r",\s\up7(催化剂)) ", "script": "baseline"},
    ]
    assert ET.tostring(paragraph) == before


@pytest.mark.parametrize("instruction", [
    r' TOC \o "1-3" \h \z \u ',
    r' QUOTE "EQ \o(――→,\s\up7(H))" ',
    r" PAGE ",
    r" EQ \f(a,b) ",
    r" EQ \o\ac(――→,\s\up7(H)) ",
    r" EQ \o(――→,\s\up7(H)) \* MERGEFORMAT ",
    r" EQ \o(――→,\s\up7(H)) trailing ",
    r" EQ \o(――→,\s\up7(H),\s\up8(催化剂)) ",
    r" EQ \o(――→,\s\up7()) ",
    r" EQ \o(,\s\up7(H)) ",
    r" EQ \o(――→,\s\up7(H) ",
    r" EQ \o(――→,\s\upNaN(H)) ",
    r" EQ \o(――→,\s\up0(H)) ",
    r" EQ \o(――→,\s\up73(H)) ",
])
def test_unknown_fields_and_unsupported_or_malformed_eq_keep_dependency(instruction):
    _rejected(_paragraph(*_field(_run(instruction=instruction))))


@pytest.mark.parametrize("separate,end", [(False, True), (True, False), (False, False)])
def test_missing_boundary_rejects_whole_field(separate, end):
    _rejected(_paragraph(*_field(_run(instruction=VALID), separate=separate, end=end)))


@pytest.mark.parametrize("boundary", ["begin", "separate", "end", "unknown"])
def test_malformed_boundary_never_promotes_an_earlier_closed_field(boundary):
    _rejected(_paragraph(*_field(_run(instruction=VALID)), _run(boundary=boundary)))


def test_nested_field_is_not_promoted_to_standalone_eq():
    paragraph = _paragraph(
        _run(boundary="begin"), _run(instruction=" QUOTE "),
        *_field(_run(instruction=VALID)),
        _run(boundary="separate"), _run(boundary="end"),
    )
    _rejected(paragraph)


def test_cross_paragraph_field_stays_unresolved_in_every_paragraph():
    first = _paragraph(_run(boundary="begin"), _run(instruction=VALID))
    second = _paragraph(_run(boundary="separate"), _run(boundary="end"))
    _rejected(first)
    _rejected(second)
    body = ET.Element(W + "body")
    body.extend([first, second])
    assert word_static_eq.paragraph_fields(body) == []
    assert "field_code_dependency" in WordNativeTextReader().read(body).features


def test_wrapped_instruction_is_not_treated_as_a_direct_run_field():
    wrapper = ET.Element(W + "sdt")
    content = ET.SubElement(wrapper, W + "sdtContent")
    content.append(_run(instruction=VALID))
    _rejected(_paragraph(*_field(wrapper)))


def test_simple_field_remains_a_dependency_even_with_supported_instruction():
    simple = ET.Element(W + "fldSimple", {W + "instr": VALID})
    simple.append(_run(text="缓存值"))
    _rejected(_paragraph(simple))


@pytest.mark.parametrize("fragments", [
    [(VALID, "superscript")],
    [(r" EQ \o(――→,\s\up7(H", "baseline"), (")", "superscript"), (") ", "baseline")],
    [(" EQ ", "baseline"), (r"\o", "subscript"), (r"(――→,\s\up7(H)) ", "baseline")],
])
def test_script_formatting_on_command_syntax_is_rejected(fragments):
    _rejected(_paragraph(*_field(*[_run(instruction=text, script=script) for text, script in fragments])))


@pytest.mark.parametrize("cached", ["――→", "与指令冲突的答案", ""])
def test_cached_text_including_empty_text_node_is_not_accepted(cached):
    _rejected(_paragraph(*_field(_run(instruction=VALID), cache=[_run(text=cached)])))


def test_cached_image_is_not_used_as_field_evidence():
    cached = _run()
    cached.append(_drawing())
    _rejected(_paragraph(*_field(_run(instruction=VALID), cache=[cached])))


def test_truly_empty_cache_run_with_properties_is_accepted():
    paragraph = _paragraph(*_field(_run(instruction=VALID), cache=[_run(font_pt=12)]))
    assert word_static_eq.paragraph_fields(paragraph)[0]["transcription"] == "――→〔上移7pt：催化剂〕"


def test_external_drawing_reference_is_rejected_even_when_embed_is_present():
    _rejected(_image_field(_drawing(link="rIdExternal")))


@pytest.mark.parametrize("crop", [
    {"l": -1}, {"r": 100000}, {"t": 100001},
    {"l": 50000, "r": 50000}, {"t": 80000, "b": 20000},
    {"l": "invalid"}, {"b": "1.5"},
])
def test_invalid_crop_bounds_or_empty_visible_area_remain_unresolved(crop):
    _rejected(_image_field(_drawing(crop=crop)))


def test_extreme_valid_crop_preserves_exact_source_fraction():
    paragraph = _image_field(_drawing(crop={"l": 99999, "t": 1, "r": 0, "b": 99998}))
    assert word_static_eq.paragraph_fields(paragraph)[0]["base"]["crop"] == {
        "l": 99999, "t": 1, "r": 0, "b": 99998,
    }


@pytest.mark.parametrize("attribute,value", [("rot", "900000"), ("flipH", "1"), ("flipV", "true")])
def test_rotated_or_flipped_drawing_is_not_misrepresented(attribute, value):
    drawing = _drawing()
    drawing.find(".//" + A + "xfrm").set(attribute, value)
    _rejected(_image_field(drawing))


def test_image_in_a_label_is_not_reinterpreted_as_the_first_operand():
    prefix = _run(instruction=r" EQ \o(――→,\s\up7(")
    image = _run()
    image.append(_drawing())
    _rejected(_paragraph(*_field(prefix, image, _run(instruction=")) "))))
