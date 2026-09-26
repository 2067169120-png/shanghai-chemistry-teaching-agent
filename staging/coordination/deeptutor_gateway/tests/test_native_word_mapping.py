"""Synthetic XML/package counterexamples; no Office and no private documents."""

import base64
from copy import deepcopy
import io
import json
import zipfile

from lxml import etree as E
import pytest

from integrations.deeptutor_shchem_v1 import desktop_native_word_mapping as mapping
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError

W, M, A, WP, R, PIC = mapping.W, mapping.M, mapping.A, mapping.WP, mapping.R, mapping.PIC
PKG, CT, REL = mapping.PKG, mapping.CT, mapping.REL
NS = {key: value[1:-1] for key, value in {"w": W, "m": M, "a": A, "wp": WP, "r": R, "pic": PIC}.items()}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/wAAAABJRU5ErkJggg==")
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
REL_TYPE = "application/vnd.openxmlformats-package.relationships+xml"


def node(tag, **attrs):
    return E.Element(tag, attrs)


def text(value):
    run = node(W + "r")
    t = E.SubElement(run, W + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
    t.text = value
    return run


def image(number):
    return E.fromstring((f'<w:r xmlns:w="{W[1:-1]}" xmlns:wp="{WP[1:-1]}" xmlns:a="{A[1:-1]}" '
                         f'xmlns:pic="{PIC[1:-1]}" xmlns:r="{R[1:-1]}">'
                         '<w:drawing><wp:inline><wp:extent cx="100" cy="100"/>'
                         f'<wp:docPr id="{number}" name="Picture {number}"/>'
                         '<a:graphic><a:graphicData uri="' + PIC[1:-1] + '"><pic:pic>'
                         '<pic:nvPicPr><pic:cNvPr id="0" name="image.png"/><pic:cNvPicPr/></pic:nvPicPr>'
                         '<pic:blipFill><a:blip r:embed="sameRid"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                         '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm>'
                         '<a:prstGeom prst="rect"/></pic:spPr></pic:pic></a:graphicData></a:graphic>'
                         '</wp:inline></w:drawing></w:r>').encode())


def equation(value="Ksp=x", split=False, cambria=False):
    math = node(M + "oMath")
    for value in list(value) if split else [value]:
        run = E.SubElement(math, M + "r")
        if cambria:
            prop = E.SubElement(run, W + "rPr")
            E.SubElement(prop, W + "rFonts", {W + "ascii": "Cambria Math", W + "hAnsi": "Cambria Math"})
        E.SubElement(run, M + "t").text = value
    return math


def paragraph(*children):
    p = node(W + "p")
    p.extend(children)
    return p


def table(cells):
    tbl = node(W + "tbl")
    grid = E.SubElement(tbl, W + "tblGrid")
    for _ in cells:
        E.SubElement(grid, W + "gridCol", {W + "w": "2400"})
    row = E.SubElement(tbl, W + "tr")
    for children in cells:
        cell = E.SubElement(row, W + "tc")
        cell.extend(children)
    return tbl


def make_body():
    body = node(W + "body")
    body.append(paragraph(text("Repeated: "), image(1), text(" middle "), image(2), text(" end.")))
    body.append(paragraph(text("Formula: "), equation(), text(" done.")))
    body.append(table([
        [paragraph(text("Cell A "), image(3))],
        [paragraph(text("Cell B "), image(4)), table([[paragraph(text("Nested "), image(5))]]), paragraph()],
    ]))
    body.append(paragraph(text("After table.")))
    E.SubElement(body, W + "sectPr")
    return body


def parts(body):
    document = E.Element(W + "document", nsmap=NS)
    document.append(deepcopy(body))
    root_rels = E.Element(REL + "Relationships", nsmap={None: REL[1:-1]})
    E.SubElement(root_rels, REL + "Relationship", Id="doc", Type=R[1:-1] + "/officeDocument", Target="word/document.xml")
    rels = E.Element(REL + "Relationships", nsmap={None: REL[1:-1]})
    E.SubElement(rels, REL + "Relationship", Id="sameRid", Type=R[1:-1] + "/image", Target="media/image.png")
    return {
        "_rels/.rels": (REL_TYPE, E.tostring(root_rels)),
        "word/document.xml": (MAIN_TYPE, E.tostring(document)),
        "word/_rels/document.xml.rels": (REL_TYPE, E.tostring(rels)),
        "word/media/image.png": ("image/png", PNG),
    }


def docx(body, extra=None):
    members = parts(body)
    members.update(extra or {})
    types = E.Element(CT + "Types", nsmap={None: CT[1:-1]})
    for name, (mime, _) in members.items():
        E.SubElement(types, CT + "Override", PartName="/" + name, ContentType=mime)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", E.tostring(types))
        for name, (_, raw) in members.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def flat(body, mutate=None):
    members = parts(body)
    if mutate:
        mutate(members)
    package = E.Element(PKG + "package", nsmap={"pkg": PKG[1:-1]})
    for name, (mime, raw) in members.items():
        part = E.SubElement(package, PKG + "part", {PKG + "name": "/" + name, PKG + "contentType": mime})
        if mime.endswith("+xml") or mime == "application/xml":
            E.SubElement(part, PKG + "xmlData").append(E.fromstring(raw))
        else:
            E.SubElement(part, PKG + "binaryData").text = base64.b64encode(raw).decode()
    return E.tostring(package, encoding="unicode")


def hosts(body):
    return [item for item in body.iter() if item.tag in {A + "blip", M + "oMath"}]


def locator(target, body):
    result = []
    while target is not body:
        parent = target.getparent()
        result.insert(0, f"/*[{parent.index(target) + 1}]")
        target = parent
    return "word/document.xml#/w:document/w:body" + "".join(result)


def fragment(body, index):
    target = hosts(body)[index]
    if target.tag == A + "blip":
        target = next(parent for parent in target.iterancestors() if parent.tag == W + "drawing")
        run = node(W + "r")
        run.append(deepcopy(target))
        result = paragraph(run)
    else:
        result = paragraph(deepcopy(target))
    selected = node(W + "body")
    selected.append(result)
    return selected


def prefix(body, index, through=False):
    result = deepcopy(body)
    target = hosts(result)[index]
    outer_tables = [p for p in target.iterancestors() if p.tag == W + "tbl"]
    if outer_tables:
        outer = outer_tables[-1]
        while len(result) > result.index(outer) + 1:
            result.remove(result[-1])
        result.append(paragraph())
        return result
    if target.tag == A + "blip":
        target = next(p for p in target.iterancestors() if p.tag == W + "drawing")
    current = target
    while current is not result:
        parent = current.getparent()
        while parent.index(current) < len(parent) - 1:
            parent.remove(parent[-1])
        previous = current
        current = parent
        if previous is target and not through:
            current.remove(previous)
    return result


def proof(body, index, *, selected=None, before=None, through=None, full=None, kind=None, native_type=None):
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[index], body))
    kind = kind or plan["objects"][index]["kind"]
    return mapping.verify_range(plan, flat(full if full is not None else body),
                                flat(selected if selected is not None else fragment(body, index)),
                                flat(before if before is not None else prefix(body, index)),
                                flat(through if through is not None else prefix(body, index, True)),
                                kind, native_type if native_type is not None else 3 if kind == "inline" else 1)


@pytest.mark.parametrize("index", range(6))
def test_each_same_rid_occurrence_has_a_separate_verified_range(index):
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[index], body))
    assert len(plan["objects"]) == 6
    assert plan["target_index"] == index
    assert len({item["signature"] for item in plan["objects"] if item["kind"] == "inline"}) == 1
    result = proof(body, index)
    assert result["target_index"] == index
    assert result["prefix_mode"] == ("expanded_outer_table_unique_drawing" if index >= 3 else "exact_prefix")


@pytest.mark.parametrize("index,other", [(1, 0), (3, 4), (5, 3)])
def test_identical_image_wrong_occurrence_is_rejected(index, other):
    body = make_body()
    with pytest.raises(PreparationSourceError, match="range_occurrence_identity_mismatch"):
        proof(body, index, selected=fragment(body, other))


def test_wrong_prefix_cannot_relabel_a_repeated_image():
    body = make_body()
    with pytest.raises(PreparationSourceError, match="range_prefix_mismatch"):
        proof(body, 1, before=prefix(body, 0))


def test_table_expansion_requires_exact_cells_and_nested_boundaries():
    body = make_body()
    wrong = prefix(body, 5)
    cells = wrong.findall(".//" + W + "tc")
    cells[0][0], cells[1][0] = deepcopy(cells[1][0]), deepcopy(cells[0][0])
    with pytest.raises(PreparationSourceError, match="range_prefix_mismatch"):
        proof(body, 5, before=wrong)


@pytest.mark.parametrize("change", ["text", "cells", "flatten_table", "merge", "bytes"])
def test_document_changes_fail_before_range_mapping(change):
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[5], body))
    changed = deepcopy(body)
    mutate = None
    if change == "text":
        changed.find(".//" + W + "t").text = "Rewritten: "
    elif change == "cells":
        row = changed.find(".//" + W + "tr")
        first, second = row[0], row[1]
        row.remove(second)
        row.insert(0, second)
        assert row[-1] is first
    elif change == "flatten_table":
        nested = changed.findall(".//" + W + "tbl")[1]
        parent = nested.getparent()
        parent.insert(parent.index(nested), deepcopy(nested.find(".//" + W + "p")))
        parent.remove(nested)
    elif change == "merge":
        cell = changed.find(".//" + W + "tc")
        prop = E.Element(W + "tcPr")
        E.SubElement(prop, W + "gridSpan", {W + "val": "2"})
        cell.insert(0, prop)
    else:
        mutate = lambda members: members.update({"word/media/image.png": ("image/png", PNG + b"changed")})
    with pytest.raises(PreparationSourceError, match="document_semantics_changed"):
        mapping.verify_document(plan, flat(changed, mutate))


def test_relationship_id_and_unique_drawing_ids_can_be_rewritten_consistently():
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[5], body))
    native = deepcopy(body)
    for item in native.iter(WP + "docPr"):
        item.set("id", str(100 + int(item.get("id"))))
    for item in native.iter(A + "blip"):
        item.set(R + "embed", "nativeRid")

    def rels(members):
        mime, raw = members["word/_rels/document.xml.rels"]
        members["word/_rels/document.xml.rels"] = (mime, raw.replace(b'sameRid', b'nativeRid'))

    result = mapping.verify_range(plan, flat(native, rels), flat(fragment(native, 5), rels),
                                  flat(prefix(native, 5), rels), flat(prefix(native, 5, True), rels), "inline", 3)
    assert result["verified"]


@pytest.mark.parametrize("side", ["source", "native"])
def test_duplicate_drawing_identity_is_not_usable(side):
    body = make_body()
    original = docx(body)
    path = locator(hosts(body)[0], body)
    list(body.iter(WP + "docPr"))[1].set("id", "1")
    with pytest.raises(PreparationSourceError, match="duplicate_drawing_identity"):
        if side == "source":
            mapping.build_source_plan(docx(body), path)
        else:
            mapping.verify_document(mapping.build_source_plan(original, path), flat(body))


def test_missing_media_and_wrong_relationship_uri_fail():
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))
    with pytest.raises(PreparationSourceError, match="missing_relationship_part"):
        mapping.verify_document(plan, flat(body, lambda members: members.pop("word/media/image.png")))

    def rels(members):
        mime, raw = members["word/_rels/document.xml.rels"]
        members["word/_rels/document.xml.rels"] = (mime, raw.replace(b'/image"', b'/styles"'))

    with pytest.raises(PreparationSourceError, match="invalid_styles_part"):
        mapping.verify_document(plan, flat(body, rels))


def test_math_run_splitting_default_font_and_range_wrapper_are_limited_normalizations():
    body = make_body()
    native = deepcopy(body)
    math = hosts(native)[2]
    math.getparent().replace(math, equation(split=True, cambria=True))
    selected = fragment(native, 2)
    selected_math = hosts(selected)[0]
    parent = selected_math.getparent()
    parent.remove(selected_math)
    wrapper = E.SubElement(parent, M + "oMathPara")
    wrapper.append(equation(cambria=True))
    assert proof(body, 2, full=native, selected=selected)["verified"]
    wrong = fragment(native, 2)
    next(wrong.iter(M + "t")).text = "q"
    with pytest.raises(PreparationSourceError, match="range_object_mismatch"):
        proof(body, 2, selected=wrong)


def test_math_structure_is_not_replaced_with_display_text():
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[2], body))
    math = hosts(body)[2]
    for child in list(math):
        math.remove(child)
    fraction = E.SubElement(math, M + "f")
    E.SubElement(fraction, M + "num").append(equation("Ksp=x")[0])
    E.SubElement(fraction, M + "den").append(equation("")[0])
    with pytest.raises(PreparationSourceError, match="document_semantics_changed"):
        mapping.verify_document(plan, flat(body))


def test_table_math_is_explicitly_unsupported():
    body = make_body()
    p = body.find(".//" + W + "tc/" + W + "p")
    p.append(equation())
    with pytest.raises(PreparationSourceError, match="table_math_has_no_unique_range_identity"):
        mapping.build_source_plan(docx(body), locator(p[-1], body))


def test_prefix_only_terminal_ascii_spaces_may_be_trimmed():
    body = make_body()
    before = prefix(body, 1)
    final = list(before.iter(W + "t"))[-1]
    final.text = final.text.rstrip(" ")
    assert proof(body, 1, before=before)["verified"]
    final.text = final.text.lstrip(" ")
    with pytest.raises(PreparationSourceError, match="range_prefix_mismatch"):
        proof(body, 1, before=before)


def test_native_types_and_extra_selected_content_are_rejected():
    body = make_body()
    with pytest.raises(PreparationSourceError, match="native_object_type_mismatch"):
        proof(body, 0, native_type=1)
    selected = fragment(body, 0)
    selected[0].append(text("extra"))
    with pytest.raises(PreparationSourceError, match="range_has_extra_content_or_containers"):
        proof(body, 0, selected=selected)


@pytest.mark.parametrize("tag", [W + "object", W + "fldSimple", W + "ins", W + "del", WP + "anchor",
                               "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"])
def test_unsupported_active_revisions_branches_are_rejected_before_office(tag):
    body = make_body()
    path = locator(hosts(body)[0], body)
    body[-2].append(node(tag))
    with pytest.raises(PreparationSourceError):
        mapping.build_source_plan(docx(body), path)


@pytest.mark.parametrize("extra", [
    {"word/vbaProject.bin": ("application/vnd.ms-office.vbaProject", b"macro")},
    {"word/embeddings/object.bin": ("application/vnd.openxmlformats-officedocument.oleObject", b"ole")},
    {"word/unknown.bin": ("application/octet-stream", b"unknown")},
    {"word/header1.xml": ("application/xml", E.tostring(paragraph(node(W + "instrText"))))},
    {"word/settings.xml": ("application/xml", E.tostring(node(W + "attachedTemplate")))},
    {"word/header1.xml": ("application/xml", E.tostring(node("{urn:schemas-microsoft-com:vml}imagedata", src="https://example.invalid/a.png")))},
    {"word/image.xml": ("image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"/>')},
])
def test_activity_in_other_package_parts_is_rejected(extra):
    body = make_body()
    with pytest.raises(PreparationSourceError):
        mapping.build_source_plan(docx(body, extra), locator(hosts(body)[0], body))


def test_external_relationship_anywhere_in_package_is_rejected():
    body = make_body()
    rels = E.Element(REL + "Relationships")
    E.SubElement(rels, REL + "Relationship", Id="external", Type=R[1:-1] + "/image",
                 TargetMode="External", Target="https://example.invalid/image.png")
    extra = {"word/_rels/header1.xml.rels": (REL_TYPE, E.tostring(rels))}
    with pytest.raises(PreparationSourceError, match="external_or_active_relationship"):
        mapping.build_source_plan(docx(body, extra), locator(hosts(body)[0], body))


def test_hidden_style_and_unknown_table_metadata_are_rejected():
    body = make_body()
    prop = E.SubElement(body[0][0], W + "rPr")
    E.SubElement(prop, W + "vanish")
    with pytest.raises(PreparationSourceError, match="hidden_content"):
        mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))
    body = make_body()
    prop = E.SubElement(body[2], W + "tblPr")
    E.SubElement(prop, W + "futureUnknown")
    with pytest.raises(PreparationSourceError, match="unsupported_metadata"):
        mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))


def test_grid_width_normalization_keeps_merge_topology():
    body = make_body()
    E.SubElement(body[2].find(W + "tblGrid"), W + "gridCol", {W + "w": "2400"})
    cell = body.find(".//" + W + "tc")
    prop = E.Element(W + "tcPr")
    E.SubElement(prop, W + "vMerge", {W + "val": "restart"})
    E.SubElement(prop, W + "gridSpan", {W + "val": "2"})
    cell.insert(0, prop)
    row = E.SubElement(body[2], W + "tr")
    continued = E.SubElement(row, W + "tc")
    continuation = E.SubElement(continued, W + "tcPr")
    E.SubElement(continuation, W + "vMerge")
    E.SubElement(continuation, W + "gridSpan", {W + "val": "2"})
    continued.append(paragraph())
    E.SubElement(row, W + "tc").append(paragraph(text("Other cell")))
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[3], body))
    for column in body.iter(W + "gridCol"):
        column.set(W + "w", "2350")
    assert mapping.verify_document(plan, flat(body))["verified"]
    next(body.iter(W + "vMerge")).set(W + "val", "continue")
    with pytest.raises(PreparationSourceError, match="document_semantics_changed"):
        mapping.verify_document(plan, flat(body))


def test_locator_cannot_name_a_paragraph_or_execute_xpath():
    body = make_body()
    for path in ["word/document.xml#/w:document/w:body/*[1]", "word/document.xml#//a:blip", "word/document.xml#/w:document/w:body/*[0]"]:
        with pytest.raises(PreparationSourceError):
            mapping.build_source_plan(docx(body), path)


def test_plan_tampering_and_budgets_are_rejected(monkeypatch):
    body = make_body()
    data = docx(body)
    path = locator(hosts(body)[0], body)
    plan = mapping.build_source_plan(data, path)
    plan["target_index"] = 1
    with pytest.raises(PreparationSourceError, match="invalid_plan"):
        mapping.verify_document(plan, flat(body))
    monkeypatch.setattr(mapping, "MAX_SOURCE_BYTES", len(data) - 1)
    with pytest.raises(PreparationSourceError, match="source_budget"):
        mapping.build_source_plan(data, path)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_doctype_is_rejected_even_if_entities_are_unused(encoding):
    value = '<?xml version="1.0" encoding="' + encoding + '"?><!DOCTYPE x [<!ENTITY e "x">]><x/>'
    with pytest.raises(PreparationSourceError, match="xml_declaration_forbidden"):
        mapping._xml(value.encode(encoding))


def test_duplicate_parts_and_xml_node_budget_are_rejected(monkeypatch):
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))
    package = E.fromstring(flat(body).encode())
    package.append(deepcopy(package[0]))
    with pytest.raises(PreparationSourceError, match="duplicate_package_part"):
        mapping.verify_document(plan, E.tostring(package, encoding="unicode"))
    monkeypatch.setattr(mapping, "MAX_XML_NODES", 10)
    with pytest.raises(PreparationSourceError, match="xml_structure_budget"):
        mapping.verify_document(plan, flat(body))


@pytest.mark.parametrize("extra", [
    {"word/payload.exe": ("image/png", PNG)},
    {"word/payload.png": ("image/png", b"MZpretend-png")},
    {"word/payload.dll": ("application/xml", b"<passive/> ")},
])
def test_unreferenced_binary_cannot_hide_behind_content_type(extra):
    body = make_body()
    with pytest.raises(PreparationSourceError):
        mapping.build_source_plan(docx(body, extra), locator(hosts(body)[0], body))


@pytest.mark.parametrize("special", [E.Comment("not a drawing"), E.ProcessingInstruction("example", "data")])
def test_xml_nodes_cannot_silently_renumber_the_source_locator(special):
    body = make_body()
    body[0].insert(0, deepcopy(special))
    with pytest.raises(PreparationSourceError, match="xml_non_element_node"):
        mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))


def test_equivalent_numeric_drawing_ids_are_still_duplicates():
    body = make_body()
    list(body.iter(WP + "docPr"))[1].set("id", "01")
    with pytest.raises(PreparationSourceError, match="duplicate_drawing_identity"):
        mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))


def test_image_bytes_match_does_not_hide_changed_crop():
    body = make_body()
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[0], body))
    fill = body.find(".//" + PIC + "blipFill")
    fill.insert(1, node(A + "srcRect", l="30000"))
    with pytest.raises(PreparationSourceError, match="document_semantics_changed"):
        mapping.verify_document(plan, flat(body))


def test_json_round_trip_plan_preserves_special_character_events():
    body = make_body()
    run = body[0][0]
    E.SubElement(run, W + "tab")
    E.SubElement(run, W + "br", {W + "type": "textWrapping"})
    plan = mapping.build_source_plan(docx(body), locator(hosts(body)[1], body))
    portable = json.loads(json.dumps(plan))
    assert mapping.verify_document(portable, flat(body))["verified"]


@pytest.mark.parametrize("limit,value,error", [("MAX_PART_BYTES", 100, "package_budget"),
                                               ("MAX_OBJECTS", 2, "object_budget"),
                                               ("MAX_PLAN_BYTES", 100, "plan_budget")])
def test_source_work_and_output_are_bounded(monkeypatch, limit, value, error):
    body = make_body()
    data = docx(body)
    monkeypatch.setattr(mapping, limit, value)
    with pytest.raises(PreparationSourceError, match=error):
        mapping.build_source_plan(data, locator(hosts(body)[0], body))


def test_math_display_type_is_not_interchangeable_with_inline():
    body = make_body()
    with pytest.raises(PreparationSourceError, match="native_object_type_mismatch"):
        proof(body, 2, native_type=0)
