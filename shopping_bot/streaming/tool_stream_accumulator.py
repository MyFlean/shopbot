"""
Tool Stream Accumulator for Anthropic's streaming tool-use API.

Processes input_json_delta events from Anthropic's streaming Messages API,
accumulating the complete tool payload while extracting user-facing strings
for progressive display.

Based on Anthropic's official streaming tool-use documentation:
https://docs.anthropic.com/en/api/messages-streaming
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class ToolStreamAccumulator:
    """
    Accumulates tool_use input from Anthropic's streaming API.
    
    Event flow:
    1. content_block_start (type=tool_use) → capture tool name
    2. content_block_delta (type=input_json_delta) → accumulate partial_json
    3. content_block_stop → parse complete JSON
    
    Features:
    - Accumulates raw JSON strings from input_json_delta events
    - Extracts user-visible strings incrementally (ASK messages, simple_response)
    - Parses complete payload at content_block_stop
    - Tracks emitted strings to prevent duplicates
    """
    
    def __init__(self):
        self.tool_name: Optional[str] = None
        self.tool_id: Optional[str] = None
        self.input_buffer: str = ""  # Accumulated partial_json strings
        self.complete_input: Optional[Dict[str, Any]] = None
        self._emitted_texts: set[str] = set()  # Prevent duplicate emissions
        self._last_buffer_scan_pos: int = 0  # Track last scanned position
        self._current_block_type: Optional[str] = None
        self._in_tool_block: bool = False
        # Track per-slot emission state for robust streaming of messages and options
        self._slot_emit_state: Dict[str, Dict[str, bool]] = {}
        # Track options already emitted per slot (to support incremental emissions)
        self._slot_seen_options: Dict[str, List[str]] = {}
        # Track simple_response streaming position
        self._simple_response_emitted_len: int = 0
        
    def process_event(self, event) -> Optional[Dict[str, Any]]:
        """
        Process a single streaming event from Anthropic.
        
        Args:
            event: Anthropic stream event object
            
        Returns:
            Dict with extracted user-facing content, or None
            Format: {"type": "ask_message" | "simple_response", "text": "..."}
        """
        event_type = getattr(event, 'type', None)
        log.info(f"STREAM_EVENT | type={event_type}")
        
        if event_type == 'content_block_start':
            return self._handle_block_start(event)
            
        elif event_type == 'content_block_delta':
            return self._handle_block_delta(event)
            
        elif event_type == 'content_block_stop':
            return self._handle_block_stop(event)
            
        return None
    
    def _handle_block_start(self, event) -> Optional[Dict[str, Any]]:
        """Handle content_block_start event"""
        try:
            content_block = getattr(event, 'content_block', None)
            if content_block is None and isinstance(event, dict):
                content_block = event.get('content_block')
            if content_block:
                block_type = getattr(content_block, 'type', None)
                if block_type is None and isinstance(content_block, dict):
                    block_type = content_block.get('type')
                self._current_block_type = block_type
                self._in_tool_block = (block_type == 'tool_use')
                if self._in_tool_block:
                    tool_name = getattr(content_block, 'name', None)
                    if tool_name is None and isinstance(content_block, dict):
                        tool_name = content_block.get('name')
                    tool_id = getattr(content_block, 'id', None)
                    if tool_id is None and isinstance(content_block, dict):
                        tool_id = content_block.get('id')
                    self.tool_name = tool_name
                    self.tool_id = tool_id
                    log.info(f"TOOL_STREAM_START | tool={self.tool_name} | id={self.tool_id}")
                    return {
                        "type": "tool_start",
                        "tool_name": self.tool_name,
                        "tool_id": self.tool_id
                    }
                else:
                    # Helpful to know if we only ever receive non-tool blocks
                    log.info(f"NON_TOOL_BLOCK_START | type={block_type}")
        except Exception as e:
            log.debug(f"Block start parsing error: {e}")
        return None
    
    def _handle_block_delta(self, event) -> Optional[Dict[str, Any]]:
        """Handle content_block_delta event with input_json_delta"""
        try:
            delta = getattr(event, 'delta', None)
            if delta is None and isinstance(event, dict):
                delta = event.get('delta')
            if not delta:
                return None
                
            delta_type = getattr(delta, 'type', None)
            if delta_type is None and isinstance(delta, dict):
                delta_type = delta.get('type')
            # Ignore deltas outside tool_use blocks
            if not self._in_tool_block:
                return None

            # Some SDK/event variants may omit delta.type; prefer presence of partial_json
            partial_json = getattr(delta, 'partial_json', None)
            if partial_json is None and isinstance(delta, dict):
                partial_json = delta.get('partial_json', '')
            if delta_type == 'input_json_delta' or partial_json:
                if partial_json:
                    self.input_buffer += partial_json
                    try:
                        preview = str(partial_json)[:160]
                    except Exception:
                        preview = "<unprintable>"
                    log.info(f"JSON_DELTA | size={len(partial_json)} | total={len(self.input_buffer)} | preview='{preview}'")
                    # Extract user-facing strings incrementally
                    return self._extract_user_strings()
                    
        except Exception as e:
            log.debug(f"Block delta parsing error: {e}")
        return None
    
    def _handle_block_stop(self, event) -> Optional[Dict[str, Any]]:
        """Handle content_block_stop - only parse if current block is tool_use"""
        try:
            if self._in_tool_block:
                log.info(f"TOOL_STREAM_STOP | buffer_size={len(self.input_buffer)}")
                if self.input_buffer:
                    self.complete_input = json.loads(self.input_buffer)
                    log.info(f"TOOL_PARSED | keys={list(self.complete_input.keys())}")
                    # Compute pending options for any slots not fully emitted yet
                    pending_options: Dict[str, List[str]] = {}
                    try:
                        ask_slots = self.complete_input.get("ask_slots") or []
                        if isinstance(ask_slots, list):
                            for slot in ask_slots:
                                try:
                                    slot_name = slot.get("slot_name")
                                    options = slot.get("options") or []
                                    if not slot_name or not isinstance(options, list) or not options:
                                        continue
                                    seen = self._slot_seen_options.get(slot_name, [])
                                    if not seen or len(options) > len(seen):
                                        pending_options[slot_name] = options
                                except Exception:
                                    continue
                    except Exception:
                        pass
                    return {
                        "type": "tool_complete",
                        "tool_name": self.tool_name,
                        "input": self.complete_input,
                        "pending_options": pending_options
                    }
        except json.JSONDecodeError as e:
            log.error(f"TOOL_JSON_PARSE_ERROR | error={e} | buffer_preview={self.input_buffer[:200]}")
            self.complete_input = {}
        except Exception as e:
            log.error(f"Block stop error: {e}")
        finally:
            # Reset block tracking at the end of any block
            self._current_block_type = None
            self._in_tool_block = False
            self.input_buffer = self.input_buffer if self.complete_input is None else self.input_buffer
        return None

    def _extract_user_strings(self) -> Optional[Dict[str, Any]]:
        """
        Extract user-visible strings from accumulated JSON buffer.
        
        Uses regex patterns to find message strings before full JSON parsing.
        Scans only the newly added portion to avoid duplicate emissions.
        
        Returns:
            Dict with extracted string, or None if nothing new found
        """
        try:
            # Scan only the newly added portion
            scan_start = max(0, self._last_buffer_scan_pos - 100)  # Small overlap for split patterns
            scan_region = self.input_buffer[scan_start:]
            
            # Structured-first: slot_name + message + options (incremental)
            full_region = self.input_buffer
            for slot_match in re.finditer(r'"slot_name"\s*:\s*"([^"]+)"', full_region):
                slot_name = slot_match.group(1)
                state = self._slot_emit_state.get(slot_name, {"message": False, "options": False})
                forward_slice = full_region[slot_match.end(): slot_match.end() + 1200]

                # Message per slot
                if not state["message"]:
                    msg_match = re.search(r'"message"\s*:\s*"([^"]{5,})"', forward_slice)
                    if msg_match:
                        msg_text = msg_match.group(1)
                        if msg_text in self._emitted_texts:
                            self._slot_emit_state[slot_name] = {**state, "message": True}
                        else:
                            self._slot_emit_state[slot_name] = {**state, "message": True}
                            self._last_buffer_scan_pos = len(self.input_buffer)
                            log.info(f"EXTRACTED_ASK | slot={slot_name} | text='{msg_text[:60]}...'")
                            return {
                                "type": "ask_message",
                                "slot_name": slot_name,
                                "text": msg_text
                            }

                # Options per slot (incremental streaming)
                if not state["options"]:
                    partial_opt_match = re.search(r'"options"\s*:\s*\[([^\]]*)', forward_slice, re.S)
                    if partial_opt_match:
                        raw_partial = partial_opt_match.group(1)
                        options_partial = re.findall(r'"([^"]+)"', raw_partial)
                        if options_partial:
                            seen = self._slot_seen_options.get(slot_name, [])
                            if len(options_partial) > len(seen):
                                self._slot_seen_options[slot_name] = options_partial[:]
                                closed = bool(re.search(r'"options"\s*:\s*\[[^\]]*\]', forward_slice, re.S))
                                if closed:
                                    self._slot_emit_state[slot_name] = {**state, "options": True}
                                self._last_buffer_scan_pos = len(self.input_buffer)
                                log.info(f"EXTRACTED_OPTIONS | slot={slot_name} | count={len(options_partial)} | preview={options_partial[:3]}")
                                return {
                                    "type": "ask_options",
                                    "slot_name": slot_name,
                                    "options": options_partial
                                }

            # Generic fallback: only emit message if no slot structure is present
            ask_pattern = r'"message"\s*:\s*"([^"]{10,})"'
            ask_matches = re.findall(ask_pattern, scan_region)
            for match_text in ask_matches:
                if match_text and match_text not in self._emitted_texts:
                    if re.search(r'"slot_name"\s*:\s*"', full_region):
                        continue
                    self._emitted_texts.add(match_text)
                    self._last_buffer_scan_pos = len(self.input_buffer)
                    log.info(f"EXTRACTED_ASK | text='{match_text[:60]}...'")
                    return {
                        "type": "ask_message",
                        "text": match_text
                    }

            # Pattern 2: Simple response messages - STREAMING DELTAS
            # Extract incrementally as the message grows character-by-character
            # This provides true real-time streaming as Anthropic generates text
            # Match even WITHOUT closing quote to catch text as it arrives
            simple_pattern = r'"simple_response"[^}]*?"message"\s*:\s*"([^"]*)'
            simple_match = re.search(simple_pattern, self.input_buffer)
            
            if simple_match:
                current_simple = simple_match.group(1)  # Text so far (may be incomplete)
                current_len = len(current_simple)
                
                # Check if the message has grown since last emission
                if current_len > self._simple_response_emitted_len and current_len >= 3:
                    # Emit only the NEW characters (delta)
                    delta_text = current_simple[self._simple_response_emitted_len:]
                    self._simple_response_emitted_len = current_len
                    self._last_buffer_scan_pos = len(self.input_buffer)
                    log.info(f"EXTRACTED_SIMPLE_DELTA | delta_len={len(delta_text)} | total_len={current_len} | preview='{delta_text[:40]}...'")
                    return {
                        "type": "simple_response_delta",
                        "text": delta_text,
                        "total_length": current_len
                    }
            
            # Update scan position even if nothing found
            self._last_buffer_scan_pos = len(self.input_buffer)
            
        except Exception as e:
            log.debug(f"String extraction error: {e}")
        
        return None
    
    def get_complete_input(self) -> Dict[str, Any]:
        """Return the fully accumulated and parsed tool input"""
        return self.complete_input or {}
    
    def is_complete(self) -> bool:
        """Check if tool payload has been fully parsed"""
        return self.complete_input is not None
    
    def reset(self):
        """Reset accumulator for next tool call"""
        self.tool_name = None
        self.tool_id = None
        self.input_buffer = ""
        self.complete_input = None
        self._emitted_texts.clear()
        self._last_buffer_scan_pos = 0
        self._slot_emit_state.clear()
        self._slot_seen_options.clear()
        self._simple_response_emitted_len = 0

