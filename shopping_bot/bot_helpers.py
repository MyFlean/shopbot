"""
Helper utilities for ShoppingBotCore
────────────────────────────────────
Includes:
• KEY_ELEMENTS (Flean’s six-element answer spec)
• normalize_to_mc3  – guarantees every question is 3-option multi_choice
• sections_to_text  – formats the six-element dict into WhatsApp-friendly text
All original helpers are preserved.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Union

from .config import get_config
from .enums import BackendFunction, UserSlot
from .intent_config import FUNCTION_TTL, SLOT_QUESTIONS, SLOT_TO_SESSION_KEY
from .utils.helpers import iso_now, trim_history, safe_get  # noqa: F401
from .models import UserContext

Cfg = get_config()
log = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Flean – six core elements
# ────────────────────────────────────────────────────────────
KEY_ELEMENTS: List[str] = [
    "+",          # Core benefit / positive hook
    "ALT",        # Alternatives
    "-",          # Drawbacks / caveats
    "BUY",        # Purchase CTA
    "OVERRIDE",   # How user can tweak / override
    "INFO",       # Extra facts (nutrition, rating, etc.)
]
_LABELS = {
    "+": "Why you’ll love it",
    "ALT": "Alternatives",
    "-": "Watch-outs",
    "BUY": "Buy",
    "OVERRIDE": "Override tips",
    "INFO": "Extra info",
}

# ────────────────────────────────────────────────────────────
# Public exports
# ────────────────────────────────────────────────────────────
__all__ = [
    "already_have_data",
    "build_question",
    "string_to_function",
    "is_user_slot",
    "get_func_value",
    "compute_still_missing",
    "store_user_answer",
    "snapshot_and_trim",
    "pick_tool",
    "normalize_to_mc3",
    "sections_to_text",
]

# ────────────────────────────────────────────────────────────
# Question normaliser – guarantees MC-3
# ────────────────────────────────────────────────────────────
EXAMPLE_TOKENS = {"e.g.", "e.g", "eg", "eg."}


def _from_placeholder(ph: str) -> List[str]:
    """Extract comma-separated phrases from placeholder minus 'e.g.' prefix."""
    ph = ph.replace("e.g.,", "").replace("e.g.", "").strip()
    parts = [p.strip(" .") for p in ph.split(",") if p.strip()]
    return [p for p in parts if p.lower() not in EXAMPLE_TOKENS]


def _from_hints(hints: List[str]) -> List[str]:
    """Take first 3 hint lines, keep text before ' - ' / ' – '."""
    out: List[str] = []
    for h in hints:
        if len(out) >= 3:
            break
        base = re.split(r"\s[-–]\s", h, maxsplit=1)[0].strip()
        if base and base.lower() not in EXAMPLE_TOKENS:
            out.append(base)
    return out


def normalize_to_mc3(q: Dict[str, Any]) -> Dict[str, Any]:
    """
    Force every question to be multi_choice with exactly 3 clean options.
    Derivation precedence:
    1. existing q['options']
    2. placeholder examples
    3. first few hints
    """
    q["type"] = "multi_choice"

    # 1. start with whatever came from LLM
    opts: List[Any] = q.get("options") or []

    # 2. Derive from placeholder if missing or faulty
    if not opts or all(
        isinstance(o, str) and o.lower() in EXAMPLE_TOKENS for o in opts
    ):
        opts = _from_placeholder(q.get("placeholder", ""))

    # 3. Derive from hints if still empty
    if not opts:
        opts = _from_hints(q.get("hints", []))

    # 4. Trim / pad
    opts = opts[:3]
    while len(opts) < 3:
        opts.append("Other")

    # 5. Canonicalise
    def canon(o):
        if isinstance(o, dict):
            lab = o.get("label") or o.get("value")
            val = o.get("value") or lab
        else:
            lab = val = str(o).strip()
        lab = lab[0].upper() + lab[1:] if lab else lab
        return {"label": lab, "value": val}

    q["options"] = [canon(o) for o in opts]
    return q

# ────────────────────────────────────────────────────────────
# Six-section answer formatter
# ────────────────────────────────────────────────────────────
def sections_to_text(sections: Dict[str, str]) -> str:
    """Convert the six-element dict into WhatsApp-friendly text."""
    lines: List[str] = []
    for key in KEY_ELEMENTS:
        txt = sections.get(key, "").strip()
        if txt:
            lines.append(f"*{_LABELS[key]}:* {txt}")
    return "\n\n".join(lines)

# ────────────────────────────────────────────────────────────
# Primitive converters / inspectors
# (unchanged code below)
# ────────────────────────────────────────────────────────────
def string_to_function(f_str: str) -> Union[BackendFunction, UserSlot, None]:
    try:
        return UserSlot(f_str)
    except ValueError:
        try:
            return BackendFunction(f_str)
        except ValueError:
            return None


def is_user_slot(func: Union[BackendFunction, UserSlot, str]) -> bool:
    return isinstance(func, UserSlot) or (
        isinstance(func, str) and func.startswith("ASK_")
    )


def get_func_value(func: Union[BackendFunction, UserSlot, str]) -> str:
    if isinstance(func, (BackendFunction, UserSlot)):
        return func.value
    return str(func)

# ────────────────────────────────────────────────────────────
# Data-availability helpers (already_have_data) – unchanged
# ────────────────────────────────────────────────────────────
def already_have_data(func_str: str, ctx: UserContext) -> bool:
    try:
        slot = UserSlot(func_str)
        session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
        if slot == UserSlot.DELIVERY_ADDRESS:
            return session_key in ctx.session or session_key in ctx.permanent
        return session_key in ctx.session
    except ValueError:
        pass

    try:
        func = BackendFunction(func_str)
        rec = ctx.fetched_data.get(func.value)
        if not rec:
            return False
        ts = datetime.fromisoformat(rec["timestamp"])
        ttl = FUNCTION_TTL.get(func, timedelta(minutes=5))
        return datetime.now() - ts < ttl
    except ValueError:
        pass
    return False

# ────────────────────────────────────────────────────────────
# Question generation (build_question) – key lines modified
# ────────────────────────────────────────────────────────────
def build_question(func: Union[BackendFunction, UserSlot, str], ctx: UserContext) -> Dict[str, Any]:
    func_value = get_func_value(func)
    contextual_q = ctx.session.get("contextual_questions", {}).get(func_value)
    if contextual_q:
        return normalize_to_mc3(contextual_q)

    if isinstance(func, UserSlot):
        cfg = SLOT_QUESTIONS.get(func, {})
        if "fallback" in cfg:
            return normalize_to_mc3(cfg["fallback"].copy())
    try:
        slot = UserSlot(func_value)
        cfg = SLOT_QUESTIONS.get(slot, {})
        if "fallback" in cfg:
            return normalize_to_mc3(cfg["fallback"].copy())
    except ValueError:
        pass

    if func_value.startswith("ASK_"):
        slot_name = func_value[4:].lower().replace("_", " ")
        q = {
            "message": f"Could you tell me your {slot_name}?",
            "type": "multi_choice",
            "options": [],
        }
        return normalize_to_mc3(q)

    q = {
        "message": "Could you provide more details?",
        "type": "multi_choice",
        "options": [],
    }
    return normalize_to_mc3(q)



def store_user_answer(text: str, assessment: Dict[str, Any], ctx: UserContext) -> None:
    """Persist *text* as the answer to the slot currently being asked."""
    target = assessment.get("currently_asking")
    if not target:
        return

    # Determine session‑key
    try:
        slot = UserSlot(target)
        session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
    except ValueError:
        session_key = target[4:].lower() if target.startswith("ASK_") else target

    ctx.session[session_key] = text
    if target == UserSlot.DELIVERY_ADDRESS.value:
        ctx.permanent["delivery_address"] = text

    assessment["fulfilled"].append(target)
    assessment["currently_asking"] = None

# ────────────────────────────────────────────────────────────
# Assessment helpers
# ────────────────────────────────────────────────────────────

def compute_still_missing(assessment: Dict[str, Any], ctx: UserContext) -> List[Union[BackendFunction, UserSlot]]:
    """Return ordered list of unmet requirements for *assessment*."""
    out: List[Union[BackendFunction, UserSlot]] = []
    for f_str in assessment["priority_order"]:
        if f_str in assessment["fulfilled"]:
            continue
        if already_have_data(f_str, ctx):
            assessment["fulfilled"].append(f_str)
            continue
        func = string_to_function(f_str)
        if func:
            out.append(func)
    return out

# ────────────────────────────────────────────────────────────
# Session snapshotting
# ────────────────────────────────────────────────────────────

def snapshot_and_trim(ctx: UserContext, *, base_query: str) -> None:
    """Append a snapshot of the finished interaction to ``ctx.session['history']``
    and trim it to ``Cfg.HISTORY_MAX_SNAPSHOTS``.
    """
    snapshot = {
        "query": base_query,
        "intent": ctx.session.get("intent_l3") or ctx.session.get("intent_override"),
        "slots": {
            k: ctx.session.get(k) for k in SLOT_TO_SESSION_KEY.values() if k in ctx.session
        },
        "fetched": {k: v["timestamp"] for k, v in ctx.fetched_data.items()},
        "finished_at": iso_now(),
    }
    history = ctx.session.setdefault("history", [])
    history.append(snapshot)
    trim_history(history, Cfg.HISTORY_MAX_SNAPSHOTS)

# ────────────────────────────────────────────────────────────
# Misc.
# ────────────────────────────────────────────────────────────

def pick_tool(resp: Any, name: str):  # noqa: ANN401
    """Return the first ``tool_use`` block with *name* from the Anthropic
    response *resp*."""
    for c in resp.content:
        if getattr(c, "type", None) == "tool_use" and getattr(c, "name", None) == name:
            return c
    return None