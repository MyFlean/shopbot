"""Tests for null / 000000 placeholder pincode handling."""

from __future__ import annotations

import pytest
from flask import Flask

from shopping_bot.routes import home_page
from shopping_bot.utils.pincode_mapping import is_placeholder_pincode


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
