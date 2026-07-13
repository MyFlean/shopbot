from __future__ import annotations

from shopping_bot.data_fetchers.es_products import _resolve_products_index


def test_resolve_products_index_prefers_explicit_override(monkeypatch):
    monkeypatch.setenv("SEARCH_V2_INDEX_NAME", "products-search-v3")
    monkeypatch.setenv("ELASTIC_INDEX", "products_master")
    assert _resolve_products_index("custom-index") == "custom-index"


def test_resolve_products_index_prefers_search_v2_env(monkeypatch):
    monkeypatch.setenv("SEARCH_V2_INDEX_NAME", "products-search-v3")
    monkeypatch.setenv("ELASTIC_INDEX", "products_master")
    assert _resolve_products_index() == "products-search-v3"


def test_resolve_products_index_falls_back_to_legacy_env(monkeypatch):
    monkeypatch.delenv("SEARCH_V2_INDEX_NAME", raising=False)
    monkeypatch.setenv("ELASTIC_INDEX", "products_master")
    assert _resolve_products_index() == "products_master"


def test_resolve_products_index_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SEARCH_V2_INDEX_NAME", raising=False)
    monkeypatch.delenv("ELASTIC_INDEX", raising=False)
    assert _resolve_products_index() == "products_master"
