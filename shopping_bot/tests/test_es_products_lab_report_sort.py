"""Tests for ES lab-report-first relevance sort behavior."""

from shopping_bot.data_fetchers.es_products import _build_relevance_sort_with_lab_report_priority


def test_relevance_sort_prioritizes_lab_report_presence_before_score():
    sort_config = _build_relevance_sort_with_lab_report_priority()

    assert len(sort_config) == 2
    script_sort = sort_config[0]
    assert "_script" in script_sort
    assert script_sort["_script"]["order"] == "desc"
    assert script_sort["_script"]["type"] == "number"
    assert "category_data" in script_sort["_script"]["script"]["source"]
    assert "lab_reports" in script_sort["_script"]["script"]["source"]
    assert "url" in script_sort["_script"]["script"]["source"]
    assert sort_config[1] == {"_score": "desc"}
