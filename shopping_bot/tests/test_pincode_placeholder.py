"""Tests for null / 000000 placeholder and unmapped pincode handling."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

from shopping_bot.routes import home_page
from shopping_bot.utils import pincode_mapping
from shopping_bot.utils.pincode_mapping import (
    PincodeMappingError,
    UnmappedPincodeError,
    is_placeholder_pincode,
    try_resolve_canonical_pincode,
)


@pytest.mark.parametrize(
    "pincode,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("000000", True),
        ("201303", False),
        (" 201303 ", False),
    ],
)
def test_is_placeholder_pincode(pincode, expected):
    assert is_placeholder_pincode(pincode) is expected


def test_validation_filter_inactive_for_placeholder():
    assert home_page._is_validation_filter_active_for_pincode("000000") is False
    assert home_page._is_validation_filter_active_for_pincode(None) is False
    assert home_page._is_validation_filter_active_for_pincode("201303") is True


def test_resolve_canonical_request_pincode_skips_placeholder():
    app = Flask(__name__)
    with app.test_request_context("/?pincode=000000"):
        assert home_page._resolve_canonical_request_pincode() is None

    with app.test_request_context("/"):
        assert home_page._resolve_canonical_request_pincode() is None


def test_unmapped_pincode_raises_unmapped_error():
    with patch.object(
        pincode_mapping,
        "_load_serviceable_mapping",
        return_value={"201303": {"serviceable_pincodes": ["201303"]}},
    ):
        with pytest.raises(UnmappedPincodeError):
            pincode_mapping.resolve_canonical_pincode("560001")


def test_try_resolve_canonical_pincode_unmapped_returns_none():
    with patch.object(
        pincode_mapping,
        "resolve_canonical_pincode",
        side_effect=UnmappedPincodeError("unmapped"),
    ):
        assert try_resolve_canonical_pincode("560001") is None


def test_try_resolve_canonical_pincode_infra_error_propagates():
    with patch.object(
        pincode_mapping,
        "resolve_canonical_pincode",
        side_effect=PincodeMappingError("S3 down"),
    ):
        with pytest.raises(PincodeMappingError):
            try_resolve_canonical_pincode("560001")


def test_resolve_canonical_request_pincode_unmapped_fail_open():
    app = Flask(__name__)
    with patch.object(
        home_page,
        "try_resolve_canonical_pincode",
        return_value=None,
    ):
        with app.test_request_context("/?pincode=560001"):
            assert home_page._resolve_canonical_request_pincode() is None
