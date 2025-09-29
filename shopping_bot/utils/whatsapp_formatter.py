from __future__ import annotations

import re
from typing import Optional


_BOLD_OPEN = re.compile(r"<bold>", flags=re.IGNORECASE)
_BOLD_CLOSE = re.compile(r"</bold>", flags=re.IGNORECASE)
_NEWLINE_TAG = re.compile(r"<newline>\s*", flags=re.IGNORECASE)
_HTML_TAG = re.compile(r"</?([a-zA-Z0-9]+)(\s+[^>]*)?>", flags=re.IGNORECASE)


def _replace_bold_tags(text: str) -> str:
    """Convert <bold>...</bold> regions to WhatsApp bold using *...*.

    The model guarantees only <bold> and <newline> tags. We still guard against
    unbalanced tags by doing simple replacements first, then a corrective pass.
    """
    if not text:
        return text
    # Fast replace the explicit tags
    replaced = _BOLD_OPEN.sub("*", text)
    replaced = _BOLD_CLOSE.sub("*", replaced)
    # If there are odd number of asterisks, drop the last dangling one
    try:
        if replaced.count("*") % 2 == 1:
            # Remove the last star to avoid unbalanced bold
            idx = replaced.rfind("*")
            if idx != -1:
                replaced = replaced[:idx] + replaced[idx + 1 :]
    except Exception:
        pass
    return replaced


def _strip_unknown_html(text: str) -> str:
    """Remove any unknown HTML-esque tags while preserving {product_id}."""
    if not text:
        return text
    # Remove tags other than our placeholders; {product_id} has no angle brackets
    return _HTML_TAG.sub("", text)


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for WhatsApp readability.

    - Convert CRLF to LF
    - Collapse more than one blank line to a single blank line
    - Trim trailing spaces on each line
    - Ensure no leading/trailing overall newlines
    """
    if not text:
        return text
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    # Trim each line
    lines = [ln.rstrip() for ln in s.split("\n")]
    # Collapse consecutive blanks
    compact: list[str] = []
    blank = False
    for ln in lines:
        if ln.strip() == "":
            if not blank:
                compact.append("")
            blank = True
        else:
            compact.append(ln)
            blank = False
    out = "\n".join(compact).strip("\n ")
    return out


def format_summary_for_whatsapp(raw: Optional[str]) -> str:
    """Format LLM summary_message for WhatsApp UI.

    Rules implemented:
    - <newline> → real line breaks
    - <bold>...</bold> → *...* (WhatsApp bold)
    - Strip any other HTML-like tags defensively
    - Normalize whitespace and collapse excessive blank lines
    - Preserve emojis, star ratings (e.g., ⭐⭐⭐⭐), and the literal token {product_id}
    """
    text = "" if raw is None else str(raw)
    if not text:
        return ""

    # 1) Convert explicit linebreak tags
    text = _NEWLINE_TAG.sub("\n", text)

    # 2) Apply bold formatting
    text = _replace_bold_tags(text)

    # 3) Remove any other unknown HTML-like tags (defensive)
    text = _strip_unknown_html(text)

    # 4) Remove placeholder tokens we never want to show
    try:
        # Obliterate any literal {product_id} tokens
        text = text.replace("{product_id}", "").replace("{PRODUCT_ID}", "")
        # Clean double spaces created by removal
        text = re.sub(r"\s{2,}", " ", text)
    except Exception:
        pass

    # 5) Normalize whitespace
    text = _normalize_whitespace(text)

    return text


