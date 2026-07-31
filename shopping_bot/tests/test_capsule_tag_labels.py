import json
from unittest.mock import patch

import pytest

from search_v2.extension.product.card import to_product_card
from shopping_bot.data_fetchers.es_products import (
    transform_to_pdp,
    transform_to_product_card,
)
from shopping_bot.utils import capsule_tag_labels


@pytest.fixture(autouse=True)
def clear_label_cache():
    capsule_tag_labels.clear_capsule_tag_label_cache()
    yield
    capsule_tag_labels.clear_capsule_tag_label_cache()


def _source(raw_tags=None):
    tags = {}
    if raw_tags is not None:
        tags["raw_tags"] = raw_tags
    return {
        "id": "product-1",
        "name": "Test Product",
        "category_data": {
            "tags": tags,
            "nutritional": {},
        },
    }


def test_resolve_capsule_tags_maps_in_order_and_deduplicates_labels():
    source = _source(
        [
            "high_protein_density",
            "unknown",
            "gluten_free",
            "protein_alias",
            "",
            42,
        ]
    )
    label_map = {
        "high_protein_density": "High Protein Density",
        "gluten_free": "Gluten-Free",
        "protein_alias": "High Protein Density",
    }

    with patch.object(
        capsule_tag_labels,
        "get_capsule_tag_label_map",
        return_value=label_map,
    ):
        assert capsule_tag_labels.resolve_capsule_tags(source) == [
            "High Protein Density",
            "Gluten-Free",
        ]


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        {"category_data": None},
        {"category_data": {"tags": None}},
        _source("high_protein_density"),
        _source({"high_protein_density": True}),
    ],
)
def test_resolve_capsule_tags_rejects_missing_or_malformed_sources(source):
    with patch.object(
        capsule_tag_labels,
        "get_capsule_tag_label_map",
        return_value={"high_protein_density": "High Protein Density"},
    ):
        assert capsule_tag_labels.resolve_capsule_tags(source) == []


def test_capsule_label_map_is_cached(monkeypatch):
    monkeypatch.setenv("APP_ENV", "lambda")
    with patch.object(
        capsule_tag_labels,
        "_load_labels_from_s3",
        return_value={"high_protein_density": "High Protein Density"},
    ) as load:
        first = capsule_tag_labels.get_capsule_tag_label_map()
        second = capsule_tag_labels.get_capsule_tag_label_map()

    assert first == second == {
        "high_protein_density": "High Protein Density"
    }
    load.assert_called_once_with()


def test_capsule_label_map_fails_closed_when_s3_load_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "lambda")
    with patch.object(
        capsule_tag_labels,
        "_load_labels_from_s3",
        side_effect=RuntimeError("S3 unavailable"),
    ):
        assert capsule_tag_labels.get_capsule_tag_label_map() == {}
        assert capsule_tag_labels.resolve_capsule_tags(
            _source(["high_protein_density"])
        ) == []


def test_local_environment_loads_local_mapping(monkeypatch, tmp_path):
    config_path = tmp_path / "nutra_capsule_tag_labels.json"
    config_path.write_text(
        json.dumps({"high_protein_density": "High Protein Density"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(
        capsule_tag_labels,
        "LOCAL_CONFIG_PATH",
        config_path,
    )

    assert capsule_tag_labels.get_capsule_tag_label_map() == {
        "high_protein_density": "High Protein Density"
    }


def test_capsule_tags_are_added_to_pdp_only():
    source = _source(
        ["high_protein_density", "unknown", "gluten_free"]
    )
    label_map = {
        "high_protein_density": "High Protein Density",
        "gluten_free": "Gluten-Free",
    }

    with (
        patch.object(
            capsule_tag_labels,
            "get_capsule_tag_label_map",
            return_value=label_map,
        ),
        patch(
            "shopping_bot.data_fetchers.es_products."
            "get_subcategory_cards_config_for_path",
            return_value=[],
        ),
    ):
        pdp = transform_to_pdp(source)

    assert pdp["capsule_tags"] == [
        "High Protein Density",
        "Gluten-Free",
    ]
    assert "capsule_tags" not in transform_to_product_card(source)
    assert "capsule_tags" not in to_product_card(source)


def test_pdp_always_includes_empty_capsule_tags():
    source = _source()
    with patch(
        "shopping_bot.data_fetchers.es_products."
        "get_subcategory_cards_config_for_path",
        return_value=[],
    ):
        pdp = transform_to_pdp(source)

    assert pdp["capsule_tags"] == []
