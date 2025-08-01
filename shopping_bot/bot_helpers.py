"""
Helper utilities for ShoppingBotCore
────────────────────────────────────
Includes:
• KEY_ELEMENTS (Flean's six-element answer spec)
• Simplified question processing (no more complex parsing)
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
    "+": "Why you'll love it",
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
    "ensure_proper_options",
    "sections_to_text",
]

# ────────────────────────────────────────────────────────────
# Question option validator – ensures we have proper MC-3 format
# ────────────────────────────────────────────────────────────

def ensure_proper_options(q: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure question has proper multi_choice format with exactly 3 options.
    This is a simplified version that doesn't do complex parsing since
    the LLM now provides proper options directly.
    """
    q["type"] = "multi_choice"
    
    options = q.get("options", [])
    
    # If we already have proper options format, use them
    if isinstance(options, list) and len(options) >= 3:
        # Ensure each option is in proper format
        formatted_options = []
        for opt in options[:3]:  # Take first 3
            if isinstance(opt, dict) and "label" in opt and "value" in opt:
                formatted_options.append(opt)
            elif isinstance(opt, str):
                formatted_options.append({"label": opt, "value": opt})
        
        if len(formatted_options) == 3:
            q["options"] = formatted_options
            return q
    
    # Fallback: generate default options
    q["options"] = [
        {"label": "Yes", "value": "Yes"},
        {"label": "No", "value": "No"},
        {"label": "Maybe", "value": "Maybe"}
    ]
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
# Question generation (build_question) – simplified version
# ────────────────────────────────────────────────────────────
def build_question(func: Union[BackendFunction, UserSlot, str], ctx: UserContext) -> Dict[str, Any]:
    """
    Build a question for the given function/slot.
    Now simplified since contextual questions from LLM should have proper options.
    """
    func_value = get_func_value(func)
    
    # First, try to get contextual question from LLM
    contextual_q = ctx.session.get("contextual_questions", {}).get(func_value)
    if contextual_q:
        # Just ensure it has proper format, no complex parsing
        return ensure_proper_options(contextual_q)

    # Fallback to predefined questions
    if isinstance(func, UserSlot):
        cfg = SLOT_QUESTIONS.get(func, {})
        if "fallback" in cfg:
            fallback_q = cfg["fallback"].copy()
            return ensure_proper_options(fallback_q)
    
    try:
        slot = UserSlot(func_value)
        cfg = SLOT_QUESTIONS.get(slot, {})
        if "fallback" in cfg:
            fallback_q = cfg["fallback"].copy()
            return ensure_proper_options(fallback_q)
    except ValueError:
        pass

    # Generate basic question for ASK_* slots
    if func_value.startswith("ASK_"):
        slot_name = func_value[4:].lower().replace("_", " ")
        q = {
            "message": f"Could you tell me your {slot_name}?",
            "type": "multi_choice",
            "options": []
        }
        return ensure_proper_options(q)

    # Ultimate fallback
    q = {
        "message": "Could you provide more details?",
        "type": "multi_choice",
        "options": []
    }
    return ensure_proper_options(q)


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