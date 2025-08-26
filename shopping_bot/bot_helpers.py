"""
Helper utilities for ShoppingBotCore

FIXES (cumulative)
──────────────────
• Permanent user-answer persistence
• Redis-aware compute_still_missing
• Assessment validation helpers
• Conversation memory snapshot with *final-answer summary* in both
  conversation_history and legacy history
• Misc logging & option sanity helpers
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Union

from .config import get_config
from .enums import BackendFunction, UserSlot
from .intent_config import FUNCTION_TTL, SLOT_QUESTIONS, SLOT_TO_SESSION_KEY
from .utils.helpers import iso_now, trim_history
from .models import UserContext

Cfg = get_config()
log = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Flean – six core elements (unchanged)
# ────────────────────────────────────────────────────────────
KEY_ELEMENTS: List[str] = ["+", "ALT", "-", "BUY", "OVERRIDE", "INFO"]
_LABELS = {
    "+": "Why you'll love it",
    "ALT": "Alternatives",
    "-": "Watch-outs",
    "BUY": "Buy",
    "OVERRIDE": "Override tips",
    "INFO": "Extra info",
}

# ────────────────────────────────────────────────────────────
# Public API
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
    "validate_assessment_state",
    "get_user_display_name",
]

# ────────────────────────────────────────────────────────────
# Option validator (unchanged logic, simplified)
# ────────────────────────────────────────────────────────────
def ensure_proper_options(q: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure question has 3 well-formed options."""
    q["type"] = "multi_choice"
    opts = q.get("options", [])
    if isinstance(opts, list) and len(opts) >= 3:
        formatted: list[dict] = []
        for opt in opts[:3]:
            if isinstance(opt, dict) and {"label", "value"} <= set(opt):
                formatted.append(opt)
            elif isinstance(opt, str):
                formatted.append({"label": opt, "value": opt})
        if len(formatted) == 3:
            q["options"] = formatted
            return q

    q["options"] = [
        {"label": "Yes", "value": "Yes"},
        {"label": "No", "value": "No"},
        {"label": "Maybe", "value": "Maybe"},
    ]
    return q


# ────────────────────────────────────────────────────────────
# Section formatter
# ────────────────────────────────────────────────────────────
def sections_to_text(sections: Dict[str, str]) -> str:
    lines: list[str] = []
    for key in KEY_ELEMENTS:
        txt = sections.get(key, "").strip()
        if txt:
            lines.append(f"*{_LABELS[key]}:* {txt}")
    return "\n\n".join(lines)


# ────────────────────────────────────────────────────────────
# Primitive inspectors
# ────────────────────────────────────────────────────────────
def string_to_function(f_str: str) -> BackendFunction | UserSlot | None:
    try:
        return UserSlot(f_str)
    except ValueError:
        try:
            return BackendFunction(f_str)
        except ValueError:
            return None


def is_user_slot(func: BackendFunction | UserSlot | str) -> bool:
    return isinstance(func, UserSlot) or (isinstance(func, str) and func.startswith("ASK_"))


def get_func_value(func: BackendFunction | UserSlot | str) -> str:
    return func.value if isinstance(func, (BackendFunction, UserSlot)) else str(func)


# ────────────────────────────────────────────────────────────
# Data-availability helper (Redis-aware)
# ────────────────────────────────────────────────────────────
def already_have_data(func_str: str, ctx: UserContext) -> bool:
    try:
        slot = UserSlot(func_str)
        session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())

        # delivery_address special-case
        if slot == UserSlot.DELIVERY_ADDRESS:
            return (
                session_key in ctx.session
                or session_key in ctx.permanent
                or "delivery_address" in ctx.permanent
            )

        return session_key in ctx.session
    except ValueError:
        pass

    # backend function
    try:
        func = BackendFunction(func_str)
        rec = ctx.fetched_data.get(func.value)
        if not rec:
            return False
        ts = datetime.fromisoformat(rec["timestamp"])
        ttl = FUNCTION_TTL.get(func, timedelta(minutes=5))
        return datetime.now() - ts < ttl
    except ValueError:
        return False


# ────────────────────────────────────────────────────────────
# Question generation helper
# ────────────────────────────────────────────────────────────
def build_question(func: BackendFunction | UserSlot | str, ctx: UserContext) -> Dict[str, Any]:
    func_val = get_func_value(func)
    log.info(f"BUILD_QUESTION | user={ctx.user_id} | func={func_val}")

    contextual = ctx.session.get("contextual_questions", {}).get(func_val)
    if contextual:
        return ensure_proper_options(contextual)

    if isinstance(func, UserSlot):
        cfg = SLOT_QUESTIONS.get(func, {})
        if "fallback" in cfg:
            return ensure_proper_options(cfg["fallback"].copy())

    try:
        slot = UserSlot(func_val)
        cfg = SLOT_QUESTIONS.get(slot, {})
        if "fallback" in cfg:
            return ensure_proper_options(cfg["fallback"].copy())
    except ValueError:
        pass

    if func_val.startswith("ASK_"):
        slot_name = func_val[4:].lower().replace("_", " ")
        return ensure_proper_options(
            {"message": f"Could you tell me your {slot_name}?", "type": "multi_choice", "options": []}
        )

    return ensure_proper_options(
        {"message": "Could you provide more details?", "type": "multi_choice", "options": []}
    )


# ────────────────────────────────────────────────────────────
# Store user answer (session + permanent)
# ────────────────────────────────────────────────────────────
def store_user_answer(text: str, assessment: Dict[str, Any], ctx: UserContext) -> None:
    target = assessment.get("currently_asking")
    if not target:
        log.warning(f"STORE_ANSWER_NO_TARGET | user={ctx.user_id}")
        return

    log.info(f"STORE_ANSWER | user={ctx.user_id} | slot={target}")

    try:
        slot = UserSlot(target)
        session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
    except ValueError:
        session_key = target[4:].lower() if target.startswith("ASK_") else target

    ctx.session[session_key] = text

    # save to permanent profile snapshot
    ctx.permanent.setdefault("user_answers", {})[target] = {
        "value": text,
        "timestamp": iso_now(),
        "session_id": ctx.session_id,
    }
    ctx.permanent["last_updated"] = iso_now()
    ctx.permanent["last_session"] = ctx.session_id

    if target == UserSlot.DELIVERY_ADDRESS.value:
        ctx.permanent["delivery_address"] = text

    assessment.setdefault("fulfilled", []).append(target)
    assessment["currently_asking"] = None


# ────────────────────────────────────────────────────────────
# Compute unmet reqs
# ────────────────────────────────────────────────────────────
def compute_still_missing(
    assessment: Dict[str, Any], ctx: UserContext
) -> List[BackendFunction | UserSlot]:
    out: list[BackendFunction | UserSlot] = []
    for f_str in assessment.get("priority_order", []):
        if f_str in assessment.get("fulfilled", []):
            continue
        if already_have_data(f_str, ctx):
            assessment["fulfilled"].append(f_str)
            continue
        func = string_to_function(f_str)
        if func:
            out.append(func)
    return out


# ────────────────────────────────────────────────────────────
# Memory snapshotter  ⭐ UPDATED ⭐
# ────────────────────────────────────────────────────────────
def snapshot_and_trim(
    ctx: UserContext,
    *,
    base_query: str,
    internal_actions: Dict[str, Any] | None = None,
    final_answer: Dict[str, Any] | None = None,
) -> None:
    """
    Record a full turn into both new `conversation_history` and legacy `history`.
    """
    try:
        if internal_actions is None:
            assessment = ctx.session.get("assessment", {})
            internal_actions = {
                "intent_classified": ctx.session.get("intent_l3") or ctx.session.get("intent_override"),
                "questions_asked": assessment.get("priority_order", []),
                "user_responses": {
                    k: ctx.session.get(k)
                    for k in SLOT_TO_SESSION_KEY.values()
                    if k in ctx.session
                },
                "fetchers_executed": list(ctx.fetched_data.keys()),
                "fetched_data_summary": {
                    k: {
                        "timestamp": v.get("timestamp", iso_now()),
                        "has_products": bool(
                            isinstance(v, dict)
                            and isinstance(v.get("data"), dict)
                            and v["data"].get("products")
                        ),
                    }
                    for k, v in ctx.fetched_data.items()
                },
            }

        final_answer = final_answer or {
            "response_type": "unknown",
            "message_preview": "",
            "has_sections": False,
            "has_products": False,
            "flow_triggered": False,
        }

        # ---- new structured history
        conv_unit = {
            "user_query": base_query,
            "internal_actions": internal_actions,
            "final_answer": final_answer,
            "timestamp": iso_now(),
            "session_id": ctx.session_id,
        }
        ctx.session.setdefault("conversation_history", []).append(conv_unit)

        # ---- legacy snapshot (keep minimal set + NEW final answer fields)
        legacy_snapshot = {
            "query": base_query,
            "intent": internal_actions.get("intent_classified"),
            "slots": internal_actions.get("user_responses", {}),
            "fetched": {k: v["timestamp"] for k, v in internal_actions.get("fetched_data_summary", {}).items()},
            "finished_at": iso_now(),
            "session_id": ctx.session_id,
            # new fields
            "response_type": final_answer.get("response_type"),
            "message_preview": final_answer.get("message_preview"),
            "has_sections": final_answer.get("has_sections"),
            "has_products": final_answer.get("has_products"),
            "flow_triggered": final_answer.get("flow_triggered"),
        }
        ctx.session.setdefault("history", []).append(legacy_snapshot)

        trim_history(ctx.session["conversation_history"], Cfg.HISTORY_MAX_SNAPSHOTS)
        trim_history(ctx.session["history"], Cfg.HISTORY_MAX_SNAPSHOTS)
    except Exception as e:
        log.error(f"SNAPSHOT_ERROR | user={ctx.user_id} | error={e}", exc_info=True)


# ────────────────────────────────────────────────────────────
# Misc helpers (unchanged)
# ────────────────────────────────────────────────────────────
def pick_tool(resp: Any, name: str):  # noqa: ANN401
    for c in resp.content:
        if getattr(c, "type", None) == "tool_use" and getattr(c, "name", None) == name:
            return c
    return None


# ────────────────────────────────────────────────────────────
# Assessment/ display helpers
# ────────────────────────────────────────────────────────────
def validate_assessment_state(assessment: Dict[str, Any], ctx: UserContext) -> Dict[str, Any]:
    required = {"priority_order", "fulfilled"}
    added = []
    for f in required:
        if f not in assessment:
            assessment[f] = [] if f == "fulfilled" else assessment.get("missing_data", [])
            added.append(f)
    if added:
        log.warning(f"ASSESSMENT_REPAIRED | user={ctx.user_id} | added={added}")
    return assessment


def get_user_display_name(ctx: UserContext) -> str:
    for bucket in (ctx.session.get("user", {}), ctx.permanent.get("profile", {})):
        for k in ("full_name", "name", "first_name", "display_name"):
            if bucket.get(k):
                return str(bucket[k]).split()[0]
    # fallback ⇒ last 4 digits of wa_id
    wa = ctx.session.get("wa_id")
    return f"user {str(wa)[-4:]}" if wa else "there"