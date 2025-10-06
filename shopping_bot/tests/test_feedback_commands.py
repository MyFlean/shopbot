from __future__ import annotations

import pytest

from shopping_bot.routes.chat import _extract_feedback


@pytest.mark.parametrize(
    "text,prefix,rest",
    [
        ("/r this is great", "/r", "this is great"),
        ("@r love the new UI", "@r", "love the new UI"),
        ("-r needs more filters", "-r", "needs more filters"),
        (" /r   spaced out  ", "/r", "spaced out"),
        ("random text", None, ""),
        ("/x not feedback", None, ""),
    ],
)
def test_extract_feedback(text: str, prefix: str | None, rest: str):
    got_prefix, feedback = _extract_feedback(text)
    assert got_prefix == prefix
    assert feedback == rest


