from xml.etree import ElementTree as ET

import pytest

from integrations.deeptutor_shchem_v1 import word_native_text
from integrations.deeptutor_shchem_v1.word_native_text import (
    M,
    W,
    WordNativeTextReader,
    native_body_blocks,
)


def xml(body):
    return ET.fromstring(f'<w:p xmlns:w="{W[1:-1]}" xmlns:m="{M[1:-1]}">{body}</w:p>')


def test_scripts_inherit_styles_and_explicit_baseline_wins():
    styles = ET.fromstring(f'''<w:styles xmlns:w="{W[1:-1]}">
      <w:style w:styleId="Base"><w:rPr><w:vertAlign w:val="subscript"/></w:rPr></w:style>
      <w:style w:styleId="Sub"><w:basedOn w:val="Base"/></w:style>
    </w:styles>''')
    node = xml("""<w:r><w:t>SO</w:t></w:r>
      <w:r><w:rPr><w:rStyle w:val="Sub"/></w:rPr><w:t>4</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2−</w:t></w:r>
      <w:r><w:rPr><w:rStyle w:val="Sub"/><w:vertAlign w:val="baseline"/></w:rPr><w:t>条件</w:t></w:r>""")
    result = WordNativeTextReader(styles).read(node)
    assert result.text == "SO_{4}^{2−}条件"
    assert result.features == ()


def test_hidden_and_deleted_ancestors_do_not_leak_math():
    node = xml("""<w:r><w:t>可见</w:t></w:r>
      <w:r><w:rPr><w:vanish/></w:rPr><m:oMath><m:r><m:t>HIDDEN</m:t></m:r></m:oMath></w:r>
      <w:del><m:oMath><m:r><m:t>DELETED</m:t></m:r></m:oMath></w:del>
      <m:oMath><m:r><m:t>H₂O</m:t></m:r></m:oMath>""")
    result = WordNativeTextReader().read(node)
    assert result.text == "可见H₂O"
    assert set(result.features) == {"hidden_text_omitted", "tracked_changes"}
    assert result.native_math_count == 1


def test_unknown_math_is_one_gap_not_a_partial_equation():
    result = WordNativeTextReader().read(
        xml("""<w:r><w:t>前</w:t></w:r>
      <m:oMath><m:r><m:t>DO_NOT_FLATTEN</m:t></m:r><m:box/></m:oMath>
      <w:r><w:t>后</w:t></w:r>""")
    )
    assert result.text == "前【待查看原文：数学公式】后"
    assert result.features == ("omml_equation",)


def test_table_keeps_cells_and_paragraph_boundaries():
    root = xml("""<w:tbl><w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr>
      <w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>
        <w:p><w:r><w:t>条件甲</w:t></w:r></w:p><w:p><w:r><w:t>条件乙</w:t></w:r></w:p>
      </w:tc><w:tc><w:p><w:r><w:t>结论</w:t></w:r></w:p></w:tc></w:tr></w:tbl>""")
    text = WordNativeTextReader().read(root[0]).text
    assert "第1行·第2列；横向合并2列；纵向合并起点" in text
    assert "条件甲\n条件乙" in text
    assert "第1行·第4列" in text


def test_wrapped_blocks_have_resolvable_source_positions():
    root = xml("""<w:body><w:p><w:r><w:t>甲</w:t></w:r></w:p>
      <w:sdt><w:sdtPr/><w:sdtContent><w:p><w:r><w:t>乙</w:t></w:r></w:p></w:sdtContent></w:sdt>
      <w:p><w:r><w:t>丙</w:t></w:r></w:p></w:body>""")[0]
    blocks = list(native_body_blocks(root))
    assert [WordNativeTextReader().read(node).text for node, _ in blocks] == [
        "甲",
        "乙",
        "丙",
    ]
    assert blocks[1][1] == "word/document.xml#/w:body/*[2]/*[2]/*[1]"


def test_depth_limit_stops_overdeep_word_containers():
    node = xml("")
    current = node
    for _ in range(165):
        current = ET.SubElement(current, W + "sdtContent")
    with pytest.raises(ValueError, match="structural"):
        WordNativeTextReader().read(node)


def _paragraph(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _control(content):
    return f"<w:sdt><w:sdtPr/><w:sdtContent>{content}</w:sdtContent></w:sdt>"


def test_whole_rows_and_cells_in_content_controls_preserve_grid_order():
    first_row = (
        "<w:tr><w:trPr><w:gridBefore w:val='1'/></w:trPr>"
        + _control(
            "<w:tc><w:tcPr><w:gridSpan w:val='2'/></w:tcPr>"
            + _paragraph("合并甲")
            + "</w:tc>"
        )
        + "<w:customXml><w:customXmlPr/><w:tc>"
        + _paragraph("乙")
        + "</w:tc></w:customXml></w:tr>"
    )
    second_row = "<w:tr><w:tc>" + _paragraph("丙") + "</w:tc></w:tr>"
    table = xml(
        "<w:tbl>"
        + _control(first_row)
        + "<w:customXml>"
        + _control(second_row)
        + "</w:customXml></w:tbl>"
    )[0]
    result = WordNativeTextReader().read(table)
    assert result.features == ()
    assert "第1行·第2列；横向合并2列" in result.text
    assert "第1行·第4列" in result.text
    assert "第2行·第1列" in result.text
    assert (
        result.text.index("合并甲") < result.text.index("乙") < result.text.index("丙")
    )


def test_adjacent_wrapped_paragraphs_keep_boundaries_but_inline_controls_do_not_add_them():
    node = xml(
        "<w:body>"
        + _control(_paragraph("甲"))
        + "<w:customXml>"
        + _control(_paragraph("乙"))
        + "</w:customXml>"
        + _paragraph("丙")
        + "</w:body>"
    )[0]
    assert WordNativeTextReader().read(node).text == "甲\n乙\n丙"
    inline = xml(
        "<w:r><w:t>左</w:t></w:r>"
        + _control("<w:r><w:t>中</w:t></w:r>")
        + "<w:r><w:t>右</w:t></w:r>"
    )
    assert WordNativeTextReader().read(inline).text == "左中右"


def test_wrapped_cell_paragraphs_and_nested_table_remain_separate_blocks():
    nested = (
        "<w:tbl>"
        + _control(
            "<w:tr>" + _control("<w:tc>" + _paragraph("内表") + "</w:tc>") + "</w:tr>"
        )
        + "</w:tbl>"
    )
    cell = (
        "<w:tc>"
        + _control(_paragraph("第一段"))
        + _control(_paragraph("第二段"))
        + _control(nested)
        + _control(_paragraph("表后段"))
        + "</w:tc>"
    )
    table = xml("<w:tbl><w:tr>" + _control(cell) + "</w:tr></w:tbl>")[0]
    result = WordNativeTextReader().read(table)
    assert result.features == ()
    assert "第一段\n第二段\n【表格开始" in result.text
    assert "【表格结束】\n表后段" in result.text
    assert result.text.count("【表格开始") == 2
    assert result.text.count("【表格结束】") == 2
    assert "内表" in result.text


def test_wrapped_table_revisions_and_hidden_runs_remain_omitted_or_flagged():
    hidden = "<w:r><w:rPr><w:vanish/></w:rPr><w:t>HIDDEN</w:t></w:r>"
    deleted_row = (
        "<w:del>"
        + _control("<w:tr><w:tc>" + _paragraph("DELETED") + "</w:tc></w:tr>")
        + "</w:del>"
    )
    current_row = (
        "<w:ins><w:tr><w:del>"
        + _control("<w:tc>" + _paragraph("CELLDELETED") + "</w:tc>")
        + "</w:del>"
        + _control("<w:tc><w:p>" + hidden + "<w:r><w:t>可见</w:t></w:r></w:p></w:tc>")
        + "</w:tr></w:ins>"
    )
    table = xml("<w:tbl>" + _control(deleted_row + current_row) + "</w:tbl>")[0]
    result = WordNativeTextReader().read(table)
    assert "可见" in result.text
    assert "DELETED" not in result.text and "HIDDEN" not in result.text
    assert set(result.features) == {"tracked_changes", "hidden_text_omitted"}


@pytest.mark.parametrize(
    "body",
    [
        "<w:tbl><w:unknown><w:tr><w:tc>"
        + _paragraph("NOT_RECOVERED")
        + "</w:tc></w:tr></w:unknown></w:tbl>",
        "<w:tbl><w:tr><w:unknown><w:tc>"
        + _paragraph("NOT_RECOVERED")
        + "</w:tc></w:unknown></w:tr></w:tbl>",
        "<w:tc><w:unknown>" + _paragraph("NOT_RECOVERED") + "</w:unknown></w:tc>",
    ],
)
def test_unknown_table_or_body_containers_produce_an_explicit_gap(body):
    result = WordNativeTextReader().read(xml(body)[0])
    assert result.features == ("unsupported_embedded_part",)
    assert "【待查看原文：" in result.text
    assert "NOT_RECOVERED" not in result.text


def test_table_wrapper_and_skipped_subtree_nodes_count_toward_budget(monkeypatch):
    monkeypatch.setattr(word_native_text, "_MAX_NODES", 25)
    metadata = "<w:sdtPr>" + '<w:tag w:val="metadata"/>' * 26 + "</w:sdtPr>"
    deleted = "<w:del>" + _paragraph("deleted") * 26 + "</w:del>"
    wrapped = "<w:sdtContent>" * 26 + "</w:sdtContent>" * 26
    for content in (metadata, deleted, wrapped):
        table = xml("<w:tbl><w:sdt>" + content + "</w:sdt></w:tbl>")[0]
        with pytest.raises(ValueError, match="structural"):
            WordNativeTextReader().read(table)


def test_skipped_text_cannot_bypass_text_budget(monkeypatch):
    monkeypatch.setattr(word_native_text, "_MAX_TEXT", 50)
    node = xml("<w:del><w:r><w:t>" + "X" * 51 + "</w:t></w:r></w:del>")
    with pytest.raises(ValueError, match="text limit"):
        WordNativeTextReader().read(node)
