"""Tests for admin cards-config reload route."""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from shopping_bot.routes.admin_config import bp as admin_config_bp


@pytest.fixture
def admin_client():
    app = Flask(__name__)
    app.register_blueprint(admin_config_bp, url_prefix="/rs")
    app.config["TESTING"] = True
    return app.test_client()


def test_reload_success(admin_client):
    mock_redis = MagicMock()
    source = {"Default": [{"card": "Flean Rank", "visible": True, "order": 1}]}

    with patch.dict("os.environ", {"REDIS_HOST": "redis.example"}, clear=False):
        with patch(
            "shopping_bot.routes.admin_config.load_cards_config_source",
            return_value=source,
        ):
            with patch(
                "shopping_bot.routes.admin_config.ensure_cards_config_in_redis",
                return_value=1,
            ) as seed_mock:
                with patch(
                    "shopping_bot.routes.admin_config._get_redis_client",
                    return_value=mock_redis,
                ):
                    resp = admin_client.post(
                        "/rs/api/v1/admin/cards-config/reload?force=true",
                    )

    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["success"] is True
    assert data["keys_written"] == 1
    assert data["subcategories_in_source"] == 1
    assert data["force"] is True
    seed_mock.assert_called_once_with(mock_redis, force=True)
