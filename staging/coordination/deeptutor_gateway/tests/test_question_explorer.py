"""Facets and durable basket over authored data; no network or private materials."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_word_question_filters import _row, _catalog
from integrations.deeptutor_shchem_v1.desktop_question_explorer import personal_results, entry_is_selected
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade


def catalog():
    return {"items": [_row(key="q1", knowledge=("K09",), exam="first_mock"),
                      _row(key="q2", knowledge=("K09",), exam="second_mock"),
                      _row(key="q3", knowledge=("K10",), exam="school_exam")],
            "attribute_catalog": _catalog(), "warnings": []}


def test_multiple_values_or_across_facets_and():
    c = catalog()
    result = personal_results(c, "word_native", {"knowledge": ["K09"], "exam": ["first_mock", "second_mock"]}, "")
    assert [r["key"] for r in result["entries"]] == ["q1", "q2"]
    assert personal_results(c, "word_native", {"knowledge": ["K10"], "exam": ["first_mock"]}, "")["total"] == 0


def test_no_answer_leak_into_search_and_no_source_mutation():
    c = catalog(); before = deepcopy(c)
    assert personal_results(c, "word_native", {}, "OMEGA")["total"] == 0
    assert personal_results(c, "word_native", {}, "CONTEXT")["total"] == 3
    assert c == before


def test_pagination_preserves_identity_and_options_not_page_only():
    result = personal_results(catalog(), "word_native", {}, "", page=1, page_size=1)
    assert result["entries"][0]["key"] == "q2" and result["has_more"]
    assert {r["value"] for r in result["facets"]["exam"]["values"]} >= {"school_exam", "second_mock"}


def test_stale_attributes_do_not_match():
    c = catalog(); c["items"][0]["revision"] = "new"
    assert [r["key"] for r in personal_results(c, "word_native", {"knowledge": ["K09"]}, "")["entries"]] == ["q2"]


def test_selected_status_uses_source_identity_not_title():
    assert entry_is_selected({"key":"q1", "lane":"word_native"}, [{"key":"basket-1", "word_selection":{"key":"q1"}}])
    assert not entry_is_selected({"key":"q2", "lane":"word_native"}, [{"key":"q2"}])
    assert entry_is_selected({"key":"v1", "lane":"visual_native"}, [{"visual_selections":[{"key":"v1"},{"key":"v2"}]}])


def test_cart_move_remove_persist_and_preserve_other_state(tmp_path):
    store = DesktopStateStore(tmp_path)
    store.save_studio_favorites(["concept"])
    store.add_many_to_basket([{"key": str(n), "title_zh":str(n)} for n in range(3)])
    assert store.move_basket_item("0",1) == 3
    assert [r["key"] for r in store.basket()] == ["1","0","2"]
    assert store.remove_basket_item("0") == 2
    reopened = DesktopStateStore(tmp_path)
    assert [r["key"] for r in reopened.basket()] == ["1","2"]
    assert reopened.snapshot()["studio"]["favorites"] == ["concept"]


def test_invalid_moves_and_missing_identity_do_not_drop_items(tmp_path):
    store = DesktopStateStore(tmp_path)
    store.add_many_to_basket([{"key":"a"},{"key":"b"}])
    before=store.basket()
    store.move_basket_item("a",-1); store.move_basket_item("missing",1)
    store.remove_basket_item("missing")
    assert store.basket() == before
    with pytest.raises(Exception):store.move_basket_item("a",7)


def test_facade_forwards_atomic_filters_and_cursor_without_flattening_theme():
    calls=[]
    def search(payload, **kwargs):
        calls.append(payload)
        return {"items": [], "page":{"total_theme_cards":0,"has_more":False,"next_cursor":None},
                "facets":{"K":{"label_zh":"知识点","values":[]}}}
    facade=object.__new__(DesktopWorkbenchFacade)
    facade._search=SimpleNamespace(search=search)
    result=facade.search_themes(scope="master", filters={"K":["K09"],"year":["2025"]},cursor="opaque",limit=8)
    assert calls[0]["filters"] == {"K":["K09"],"year":["2025"]}
    assert calls[0]["cursor"] == "opaque" and result.facets["K"]["label_zh"] == "知识点"


def test_authored_demo_range_omits_next_chapter_without_altering_source(tmp_path):
    import sys
    from pathlib import Path
    from docx import Document
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / "runtime/deeptutor_shchem"))
    from explorer_demo_data import seed_demo
    workspace = tmp_path / "workspace"
    facade = seed_demo(workspace, tmp_path / "state")
    try:
        rows = facade.word_question_catalog()["items"]
        assert len(rows) == 6
        second_questions = [row for row in rows if row["title"].startswith("【例2】")]
        assert len(second_questions) == 2
        for row in second_questions:
            assert "3.0" in str(row["answer_blocks"])
            assert "电离与离子反应" not in str(row["answer_blocks"])
            assert row["attributes"]["question_revision"] == row["revision"]
        for path in workspace.glob("*.docx"):
            assert "电离与离子反应" in [p.text for p in Document(path).paragraphs]
    finally:
        facade.shutdown()
