"""
Helper utilities for ShoppingBotCore - UPDATED WITH FIXES
──────────────────────────────────────────────────────────
FIXES APPLIED:
- Enhanced user answer storage with permanent persistence (Issue #5)
- Improved compute_still_missing logic for Redis data (Issue #4)
- Added assessment state management and cleanup (Issue #8)
- Added missing error handling and logging

PRESERVED:
- All original helpers and six-element answer spec
- Existing question processing and options handling
- Original function signatures and behavior
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Union, Optional

from .config import get_config
from .enums import BackendFunction, UserSlot
from .intent_config import FUNCTION_TTL, SLOT_QUESTIONS, SLOT_TO_SESSION_KEY
from .utils.helpers import iso_now, trim_history, safe_get  # noqa: F401
from .models import UserContext

Cfg = get_config()
log = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# Flean – six core elements (UNCHANGED)
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
# Public exports (UNCHANGED)
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
# Question option validator (UNCHANGED)
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
# Six-section answer formatter (UNCHANGED)
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
# Primitive converters / inspectors (UNCHANGED)
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
# Data-availability helpers (ENHANCED for Redis integration)
# ────────────────────────────────────────────────────────────
def already_have_data(func_str: str, ctx: UserContext) -> bool:
    """
    ENHANCED: Now also checks fetched_data for backend functions
    to work properly with Redis persistence.
    """
    try:
        slot = UserSlot(func_str)
        session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
        
        # FIX: Check both session and permanent storage
        if slot == UserSlot.DELIVERY_ADDRESS:
            has_data = (session_key in ctx.session or 
                       session_key in ctx.permanent or
                       "delivery_address" in ctx.permanent)
            if has_data:
                log.debug(f"ALREADY_HAVE_SLOT | user={ctx.user_id} | slot={func_str} | found=true")
            return has_data
        
        has_data = session_key in ctx.session
        if has_data:
            log.debug(f"ALREADY_HAVE_SLOT | user={ctx.user_id} | slot={func_str} | found=true")
        return has_data
        
    except ValueError:
        pass

    # FIX: Enhanced backend function checking with Redis data
    try:
        func = BackendFunction(func_str)
        
        # Check in fetched_data (in-memory)
        rec = ctx.fetched_data.get(func.value)
        if rec:
            # Check if data is still valid (TTL)
            try:
                ts = datetime.fromisoformat(rec["timestamp"])
                ttl = FUNCTION_TTL.get(func, timedelta(minutes=5))
                is_valid = datetime.now() - ts < ttl
                
                if is_valid:
                    log.debug(f"ALREADY_HAVE_FUNC | user={ctx.user_id} | func={func_str} | source=fetched_data")
                    return True
                else:
                    log.debug(f"ALREADY_HAVE_FUNC_EXPIRED | user={ctx.user_id} | func={func_str}")
            except Exception as e:
                log.warning(f"ALREADY_HAVE_FUNC_TIME_ERROR | user={ctx.user_id} | func={func_str} | error={e}")
        
        log.debug(f"ALREADY_HAVE_FUNC | user={ctx.user_id} | func={func_str} | found=false")
        return False
        
    except ValueError:
        pass
    
    return False

# ────────────────────────────────────────────────────────────
# Question generation (UNCHANGED but with enhanced logging)
# ────────────────────────────────────────────────────────────
def build_question(func: Union[BackendFunction, UserSlot, str], ctx: UserContext) -> Dict[str, Any]:
    """
    Build a question for the given function/slot.
    Now simplified since contextual questions from LLM should have proper options.
    """
    func_value = get_func_value(func)
    log.info(f"BUILD_QUESTION | user={ctx.user_id} | func={func_value}")
    
    # First, try to get contextual question from LLM
    contextual_q = ctx.session.get("contextual_questions", {}).get(func_value)
    if contextual_q:
        log.info(f"CONTEXTUAL_QUESTION | user={ctx.user_id} | func={func_value}")
        # Just ensure it has proper format, no complex parsing
        return ensure_proper_options(contextual_q)

    # Fallback to predefined questions
    if isinstance(func, UserSlot):
        cfg = SLOT_QUESTIONS.get(func, {})
        if "fallback" in cfg:
            fallback_q = cfg["fallback"].copy()
            log.info(f"PREDEFINED_QUESTION | user={ctx.user_id} | func={func_value}")
            return ensure_proper_options(fallback_q)
    
    try:
        slot = UserSlot(func_value)
        cfg = SLOT_QUESTIONS.get(slot, {})
        if "fallback" in cfg:
            fallback_q = cfg["fallback"].copy()
            log.info(f"SLOT_QUESTION | user={ctx.user_id} | func={func_value}")
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
        log.info(f"GENERATED_QUESTION | user={ctx.user_id} | func={func_value}")
        return ensure_proper_options(q)

    # Ultimate fallback
    q = {
        "message": "Could you provide more details?",
        "type": "multi_choice",
        "options": []
    }
    log.info(f"FALLBACK_QUESTION | user={ctx.user_id} | func={func_value}")
    return ensure_proper_options(q)


def store_user_answer(text: str, assessment: Dict[str, Any], ctx: UserContext) -> None:
    """
    ENHANCED: Persist *text* as the answer to the slot currently being asked.
    FIX: Now also stores to permanent user profile for cross-session persistence.
    """
    target = assessment.get("currently_asking")
    if not target:
        log.warning(f"STORE_ANSWER_NO_TARGET | user={ctx.user_id} | text='{text[:30]}...'")
        return

    log.info(f"STORE_ANSWER | user={ctx.user_id} | target={target} | text='{text[:50]}...'")

    # Determine session‑key
    try:
        slot = UserSlot(target)
        session_key = SLOT_TO_SESSION_KEY.get(slot, slot.name.lower())
    except ValueError:
        session_key = target[4:].lower() if target.startswith("ASK_") else target

    # Store in session (existing behavior)
    ctx.session[session_key] = text
    log.debug(f"SESSION_ANSWER | user={ctx.user_id} | key={session_key} | stored=true")
    
    # FIX: Enhanced permanent storage for delivery address and other key data
    if target == UserSlot.DELIVERY_ADDRESS.value:
        ctx.permanent["delivery_address"] = text
        log.info(f"PERMANENT_ADDRESS | user={ctx.user_id} | stored=true")
    
    # FIX: Store all user answers in permanent profile with metadata
    try:
        if "user_answers" not in ctx.permanent:
            ctx.permanent["user_answers"] = {}
        
        ctx.permanent["user_answers"][target] = {
            "value": text,
            "timestamp": datetime.now().isoformat(),
            "session_id": ctx.session_id
        }
        
        # FIX: Map to common profile fields for easy access
        profile_mappings = {
            "ASK_USER_PREFERENCES": "preferences",
            "ASK_USER_BUDGET": "budget",
            "ASK_DELIVERY_ADDRESS": "address",
            "ASK_PRODUCT_CATEGORY": "preferred_category",
        }
        
        if target in profile_mappings:
            if "profile" not in ctx.permanent:
                ctx.permanent["profile"] = {}
            
            profile_key = profile_mappings[target]
            ctx.permanent["profile"][profile_key] = text
            log.info(f"PROFILE_MAPPED | user={ctx.user_id} | target={target} | profile_key={profile_key}")
        
        # Update metadata
        ctx.permanent["last_updated"] = datetime.now().isoformat()
        ctx.permanent["last_session"] = ctx.session_id
        
        log.info(f"PERMANENT_ANSWER | user={ctx.user_id} | target={target} | stored=true")
        
    except Exception as e:
        log.error(f"PERMANENT_STORE_ERROR | user={ctx.user_id} | target={target} | error={e}", exc_info=True)

    # Mark as fulfilled
    if "fulfilled" not in assessment:
        assessment["fulfilled"] = []
    
    assessment["fulfilled"].append(target)
    assessment["currently_asking"] = None
    
    log.info(f"ASSESSMENT_UPDATED | user={ctx.user_id} | target={target} | fulfilled_count={len(assessment['fulfilled'])}")

# ────────────────────────────────────────────────────────────
# Assessment helpers (ENHANCED for Redis integration)
# ────────────────────────────────────────────────────────────

def compute_still_missing(assessment: Dict[str, Any], ctx: UserContext) -> List[Union[BackendFunction, UserSlot]]:
    """
    ENHANCED: Return ordered list of unmet requirements for *assessment*.
    FIX: Now properly works with Redis-persisted fetched data.
    """
    try:
        out: List[Union[BackendFunction, UserSlot]] = []
        priority_order = assessment.get("priority_order", [])
        fulfilled = assessment.get("fulfilled", [])
        
        log.debug(f"COMPUTE_MISSING | user={ctx.user_id} | priority_order={priority_order} | fulfilled={fulfilled}")
        
        for f_str in priority_order:
            if f_str in fulfilled:
                log.debug(f"ALREADY_FULFILLED | user={ctx.user_id} | func={f_str}")
                continue
                
            if already_have_data(f_str, ctx):
                # FIX: Mark as fulfilled if we found the data
                assessment["fulfilled"].append(f_str)
                log.debug(f"FOUND_EXISTING_DATA | user={ctx.user_id} | func={f_str}")
                continue
                
            func = string_to_function(f_str)
            if func:
                out.append(func)
                log.debug(f"STILL_MISSING | user={ctx.user_id} | func={f_str}")
        
        log.info(f"COMPUTE_MISSING_RESULT | user={ctx.user_id} | still_missing={[get_func_value(f) for f in out]}")
        return out
        
    except Exception as e:
        log.error(f"COMPUTE_MISSING_ERROR | user={ctx.user_id} | error={e}", exc_info=True)
        # Fallback to basic behavior
        return []

# ────────────────────────────────────────────────────────────
# Session snapshotting (ENHANCED with better cleanup)
# ────────────────────────────────────────────────────────────

def snapshot_and_trim(ctx: UserContext, *, base_query: str) -> None:
    """
    ENHANCED: Append a snapshot of the finished interaction to ``ctx.session['history']``
    and trim it to ``Cfg.HISTORY_MAX_SNAPSHOTS``.
    FIX: Added better cleanup and state management.
    """
    try:
        log.info(f"SNAPSHOT_TRIM | user={ctx.user_id} | base_query='{base_query[:50]}...'")
        
        # FIX: Create comprehensive snapshot
        snapshot = {
            "query": base_query,
            "intent": ctx.session.get("intent_l3") or ctx.session.get("intent_override"),
            "slots": {
                k: ctx.session.get(k) for k in SLOT_TO_SESSION_KEY.values() if k in ctx.session
            },
            "fetched": {k: v.get("timestamp") if isinstance(v, dict) else str(v) for k, v in ctx.fetched_data.items()},
            "finished_at": iso_now(),
            "session_id": ctx.session_id,  # FIX: Track session
        }
        
        history = ctx.session.setdefault("history", [])
        history.append(snapshot)
        
        # FIX: Enhanced trimming
        original_len = len(history)
        trim_history(history, Cfg.HISTORY_MAX_SNAPSHOTS)
        
        if len(history) < original_len:
            log.debug(f"HISTORY_TRIMMED | user={ctx.user_id} | from={original_len} | to={len(history)}")
        
        log.info(f"SNAPSHOT_COMPLETE | user={ctx.user_id} | history_entries={len(history)}")
        
    except Exception as e:
        log.error(f"SNAPSHOT_ERROR | user={ctx.user_id} | error={e}", exc_info=True)

# ────────────────────────────────────────────────────────────
# Misc. (UNCHANGED)
# ────────────────────────────────────────────────────────────

def pick_tool(resp: Any, name: str):  # noqa: ANN401
    """Return the first ``tool_use`` block with *name* from the Anthropic
    response *resp*."""
    for c in resp.content:
        if getattr(c, "type", None) == "tool_use" and getattr(c, "name", None) == name:
            return c
    return None

# ────────────────────────────────────────────────────────────
# FIX: Additional helper functions for enhanced functionality
# ────────────────────────────────────────────────────────────

def validate_assessment_state(assessment: Dict[str, Any], ctx: UserContext) -> Dict[str, Any]:
    """
    FIX: Validate and repair assessment state to prevent corruption.
    """
    try:
        log.debug(f"VALIDATE_ASSESSMENT | user={ctx.user_id}")

        # Ensure required fields exist
        required_fields = ["priority_order", "fulfilled"]
        for field in required_fields:
            if field not in assessment:
                if field == "fulfilled":
                    assessment[field] = []
                elif field == "priority_order":
                    assessment[field] = assessment.get("missing_data", [])
                    
                log.warning(f"ASSESSMENT_FIELD_MISSING | user={ctx.user_id} | field={field} | repaired=true")

        # Ensure fulfilled is a list
        if not isinstance(assessment.get("fulfilled"), list):
            assessment["fulfilled"] = []
            log.warning(f"ASSESSMENT_FULFILLED_FIXED | user={ctx.user_id}")

        return assessment

    except Exception as e:
        log.error(f"VALIDATE_ASSESSMENT_ERROR | user={ctx.user_id} | error={e}", exc_info=True)
        return assessment

def get_user_display_name(ctx: UserContext) -> str:
    """
    FIX: Get user display name from various sources with fallbacks.
    """
    try:
        # Try session user data first
        user_data = ctx.session.get("user", {})
        for name_field in ["full_name", "name", "first_name", "display_name"]:
            if name_field in user_data and user_data[name_field]:
                name = str(user_data[name_field]).strip()
                if name:
                    return name.split()[0]  # First name only

        # Try permanent profile data
        profile_data = ctx.permanent.get("profile", {})
        for name_field in ["full_name", "name", "first_name", "display_name"]:
            if name_field in profile_data and profile_data[name_field]:
                name = str(profile_data[name_field]).strip()
                if name:
                    return name.split()[0]  # First name only

        # Try wa_id as fallback
        wa_id = ctx.session.get("wa_id")
        if wa_id:
            return f"user {str(wa_id)[-4:]}"

        return "there"

    except Exception as e:
        log.warning(f"GET_DISPLAY_NAME_ERROR | user={ctx.user_id} | error={e}")
        return "there"

# ────────────────────────────────────────────────────────────
# FIX: Add these to __all__ exports
# ────────────────────────────────────────────────────────────
__all__.extend([
    "validate_assessment_state",
    "get_user_display_name",
])