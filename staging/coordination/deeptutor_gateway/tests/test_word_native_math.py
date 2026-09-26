from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from integrations.deeptutor_shchem_v1 import word_native_math as native_math

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _object(body: str, *, paragraph: bool = False) -> ET.Element:
    tag = "oMathPara" if paragraph else "oMath"
    return ET.fromstring(
        f'<m:{tag} xmlns:m="{M}" xmlns:w="{W}" xmlns:x="urn:unknown">{body}</m:{tag}>'
    )


def _r(text: str) -> str:
    return f"<m:r><m:t>{text}</m:t></m:r>"


def _sub(base: str, sub: str) -> str:
    return f"<m:sSub><m:e>{base}</m:e><m:sub>{sub}</m:sub></m:sSub>"


def _fraction(num: str, den: str, properties: str = "") -> str:
    return f"<m:f>{properties}<m:num>{num}</m:num><m:den>{den}</m:den></m:f>"


def test_editable_chemical_subscripts_charges_and_unicode_are_preserved() -> None:
    sulfate = (
        "<m:sSubSup><m:e>"
        + _r("SO")
        + "</m:e><m:sub>"
        + _r("4")
        + "</m:sub><m:sup>"
        + _r("2−")
        + "</m:sup></m:sSubSup>"
    )
    root = _object(_sub(_r("H"), _r("2")) + _r("O + ") + sulfate)
    before = ET.tostring(root)
    assert native_math.omml_text(root) == "H_{2}O + SO_{4}^{2−}"
    assert ET.tostring(root) == before


def test_nested_fraction_and_script_grouping_do_not_flatten_operators() -> None:
    fraction = _fraction(_r("a+b"), _fraction(_r("c"), _sub(_r("K"), _r("a"))))
    power = f"<m:sSup><m:e>{fraction}</m:e><m:sup>{_r('2')}</m:sup></m:sSup>"
    assert native_math.omml_text(_object(power)) == r"{\frac{a+b}{\frac{c}{K_{a}}}}^{2}"
    assert native_math.omml_text(_object(_sub(_r("x+y"), _r("i+j")))) == "{x+y}_{i+j}"


def test_nested_upper_lower_reaction_conditions_keep_position_and_scope() -> None:
    condition = _sub(_r("MnO"), _r("2"))
    upper = f"<m:limUpp><m:e>{_r('→')}</m:e><m:lim>{condition}</m:lim></m:limUpp>"
    lower = f"<m:limLow><m:e>{upper}</m:e><m:lim>{_r('Δ')}</m:lim></m:limLow>"
    assert (
        native_math.omml_text(_object(lower)) == r"\underset{Δ}{\overset{MnO_{2}}{→}}"
    )


@pytest.mark.parametrize("style", ["bar", "lin", "skw"])
def test_supported_fraction_styles_retain_explicit_grouping(style: str) -> None:
    properties = f'<m:fPr><m:type m:val="{style}"/></m:fPr>'
    assert (
        native_math.omml_text(_object(_fraction(_r("a"), _r("b"), properties)))
        == r"\frac{a}{b}"
    )


def test_square_and_higher_roots() -> None:
    square = (
        f"<m:rad><m:radPr><m:degHide/></m:radPr><m:deg/><m:e>{_r('x+1')}</m:e></m:rad>"
    )
    cube = f"<m:rad><m:deg>{_r('3')}</m:deg><m:e>{square}</m:e></m:rad>"
    assert native_math.omml_text(_object(cube)) == r"\sqrt[3]{\sqrt{x+1}}"


def test_basic_delimiters_and_multiple_operands() -> None:
    body = (
        f'<m:d><m:dPr><m:begChr m:val="["/><m:sepChr m:val=";"/>'
        f'<m:endChr m:val="]"/><m:grow m:val="true"/></m:dPr>'
        f"<m:e>{_r('a')}</m:e><m:e>{_sub(_r('H'), _r('2'))}</m:e></m:d>"
    )
    assert native_math.omml_text(_object(body)) == "[a;H_{2}]"
    assert native_math.omml_text(_object(f"<m:d><m:e>{_r('x')}</m:e></m:d>")) == "(x)"


def test_only_known_metadata_is_ignored_and_text_spacing_is_preserved() -> None:
    body = (
        '<m:r><m:rPr><m:lit/><m:nor/><m:scr m:val="roman"/>'
        '<m:sty m:val="p"/><m:brk m:alnAt="1"/></m:rPr>'
        '<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/>'
        '<w:sz w:val="24"/><w:lang w:val="zh-CN"/><w:b w:val="0"/></w:rPr>'
        '<m:t xml:space="preserve"> A + B </m:t></m:r>'
    )
    assert native_math.omml_text(_object(body)) == " A + B "
    para = '<m:oMathParaPr><m:jc m:val="centerGroup"/></m:oMathParaPr>'
    para += f"<m:oMath>{body}</m:oMath><m:oMath>{_r('C')}</m:oMath>"
    assert native_math.omml_text(_object(para, paragraph=True)) == " A + B \nC"


@pytest.mark.parametrize(
    "bad",
    [
        "<m:unknown/>",
        "<m:nary/>",
        "<m:acc/>",
        "<m:m/>",
        "<m:eqArr/>",
        "<m:phant/>",
        "<m:box/>",
        "<w:object/>",
        "<w:drawing/>",
        "<x:r><x:t>Hidden</x:t></x:r>",
        "<w:del><m:r><m:t>Deleted</m:t></m:r></w:del>",
        "<w:ins><m:r><m:t>Changed</m:t></m:r></w:ins>",
        "<m:t>Outside run</m:t>",
        "<m:r><m:unknown/><m:t>Partial</m:t></m:r>",
        "<m:r><m:rPr><m:unknown/></m:rPr><m:t>Partial</m:t></m:r>",
        '<m:r><m:rPr><m:scr m:val="fraktur"/></m:rPr><m:t>F</m:t></m:r>',
        "<m:r><w:rPr><w:vanish/></w:rPr><m:t>Hidden</m:t></m:r>",
        "<m:r><w:rPr><w:webHidden/></w:rPr><m:t>Hidden</m:t></m:r>",
        '<m:r><w:rPr><w:rStyle w:val="PossiblyHidden"/></w:rPr><m:t>X</m:t></m:r>',
        "<m:r><w:rPr><w:rPrChange/></w:rPr><m:t>X</m:t></m:r>",
        '<m:r><w:rPr><w:rFonts w:ascii="Symbol"/></w:rPr><m:t>X</m:t></m:r>',
        '<m:r x:unknown="1"><m:t>X</m:t></m:r>',
        '<m:r><m:t x:unknown="1">X</m:t></m:r>',
        "<m:r><m:t><m:r><m:t>X</m:t></m:r></m:t></m:r>",
        "<m:r><m:t>X</m:t></m:r>unwrapped",
        "<m:r>unwrapped<m:t>X</m:t></m:r>",
        "<m:r><m:t>\u202eHidden</m:t></m:r>",
        "<m:oMathPara><m:oMath><m:r><m:t>X</m:t></m:r></m:oMath></m:oMathPara>",
    ],
)
def test_unsupported_descendant_rejects_whole_object_without_partial_text(
    bad: str,
) -> None:
    assert native_math.omml_text(_object(_r("Before") + bad + _r("After"))) is None


@pytest.mark.parametrize(
    "bad",
    [
        "<m:sSub><m:e/><m:sub><m:r><m:t>2</m:t></m:r></m:sub></m:sSub>",
        "<m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e></m:sSup>",
        "<m:sSubSup><m:e/><m:sup/><m:sub/></m:sSubSup>",
        "<m:f><m:num/><m:den/></m:f>",
        "<m:f><m:num><m:r><m:t>a</m:t></m:r></m:num></m:f>",
        "<m:f><m:den/><m:num/></m:f>",
        '<m:f><m:fPr><m:type m:val="noBar"/></m:fPr><m:num/><m:den/></m:f>',
        '<m:r><m:rPr><m:sty m:val="p"/><m:sty m:val="b"/></m:rPr><m:t>x</m:t></m:r>',
        "<m:r><m:t>x</m:t><m:rPr/></m:r>",
        '<m:r><m:t xml:space="bad">x</m:t></m:r>',
        "<m:r><m:t/></m:r>",
        "<m:rad><m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad>",
        "<m:rad><m:deg/><m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad>",
        "<m:rad><m:radPr><m:degHide/></m:radPr><m:deg><m:r><m:t>3</m:t></m:r></m:deg><m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad>",
        '<m:d><m:dPr><m:begChr m:val="ABC"/></m:dPr><m:e/></m:d>',
        "<m:limLow><m:e><m:r><m:t>→</m:t></m:r></m:e><m:lim/></m:limLow>",
        "<m:sSub><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sub><m:r><m:t>2</m:t></m:r></m:sub><m:sub/></m:sSub>",
    ],
)
def test_malformed_operands_or_properties_fail_closed(bad: str) -> None:
    assert native_math.omml_text(_object(bad)) is None


def test_no_bar_stack_is_not_misrepresented_as_a_fraction() -> None:
    properties = '<m:fPr><m:type m:val="noBar"/></m:fPr>'
    assert (
        native_math.omml_text(_object(_fraction(_r("a"), _r("b"), properties))) is None
    )


def test_unknown_content_inside_control_properties_is_not_skipped() -> None:
    properties = "<m:fPr><m:ctrlPr><w:rPr><w:vanish/></w:rPr></m:ctrlPr></m:fPr>"
    assert (
        native_math.omml_text(_object(_fraction(_r("a"), _r("b"), properties))) is None
    )


def test_empty_wrong_root_and_foreign_namespace_are_rejected() -> None:
    for value in (
        None,
        "<m:oMath/>",
        ET.Element("oMath"),
        ET.Element("{urn:unknown}oMath"),
        ET.Element(f"{{{W}}}p"),
        _object(""),
        _object(_r("   ")),
    ):
        assert native_math.omml_text(value) is None


def test_limits_cover_large_text_many_elements_and_metadata() -> None:
    assert (
        native_math.omml_text(_object(_r("x" * (native_math.MAX_MATH_TEXT + 1))))
        is None
    )
    assert (
        native_math.omml_text(_object(_r("x") * native_math.MAX_MATH_ELEMENTS)) is None
    )
    huge_metadata = (
        '<m:r><w:rPr><w:lang w:val="'
        + ("a" * native_math.MAX_MATH_TEXT)
        + '"/></w:rPr><m:t>x</m:t></m:r>'
    )
    assert native_math.omml_text(_object(huge_metadata)) is None


def test_depth_cycles_and_output_expansion_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deep = _r("x")
    for _ in range(native_math.MAX_MATH_DEPTH):
        deep = _sub(deep, _r("1"))
    assert native_math.omml_text(_object(deep)) is None
    cycle = ET.Element(f"{{{M}}}oMath")
    cycle.append(cycle)
    assert native_math.omml_text(cycle) is None
    monkeypatch.setattr(native_math, "MAX_MATH_OUTPUT", 10)
    assert native_math.omml_text(_object(_fraction(_r("a"), _r("b")))) is None


def test_lxml_elements_and_outer_tail_are_supported() -> None:
    etree = pytest.importorskip("lxml.etree")
    element = etree.fromstring(ET.tostring(_object(_sub(_r("H"), _r("2")))))
    element.tail = "text outside this object"
    assert native_math.omml_text(element) == "H_{2}"
