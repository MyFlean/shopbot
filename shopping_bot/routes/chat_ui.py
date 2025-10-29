from __future__ import annotations

import json
from typing import Any, Dict

from flask import Blueprint, Response


bp = Blueprint("chat_ui", __name__)


@bp.get("/chat/ui")
def chat_ui() -> Response:
    html = _build_html_page()
    return Response(html, mimetype="text/html; charset=utf-8")


def _build_html_page() -> str:
    return """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ShopBot - Streaming Chat</title>
    <style>
      :root { 
        color-scheme: light dark;
        --primary: #1976d2;
        --primary-light: #1976d220;
        --surface: #fafafa08;
        --border: #8883;
        --text-secondary: #666;
      }
      
      * { box-sizing: border-box; }
      
      body { 
        margin: 0; 
        font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
        background: #f5f5f5;
        color: #333;
      }
      
      @media (prefers-color-scheme: dark) {
        body { background: #1a1a1a; color: #e0e0e0; }
      }
      
      .wrap { 
        max-width: 960px; 
        margin: 0 auto; 
        padding: 16px; 
        min-height: 100vh;
        display: flex;
        flex-direction: column;
      }
      
      header {
        padding: 16px 0;
        border-bottom: 2px solid var(--primary);
        margin-bottom: 16px;
      }
      
      h1 { 
        margin: 0; 
        font-size: 24px; 
        font-weight: 600;
        color: var(--primary);
      }
      
      .config-section {
        background: white;
        padding: 16px;
        border-radius: 12px;
        margin-bottom: 16px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      }
      
      @media (prefers-color-scheme: dark) {
        .config-section { background: #2a2a2a; }
      }
      
      .row { 
        display: flex; 
        gap: 8px; 
        align-items: center; 
        flex-wrap: wrap;
        margin-bottom: 12px;
      }
      
      .row:last-child { margin-bottom: 0; }
      
      .label { 
        min-width: 100px; 
        font-weight: 500;
        color: var(--text-secondary);
        font-size: 14px;
      }
      
      input[type=text] { 
        flex: 1; 
        padding: 10px 12px; 
        border: 1px solid var(--border); 
        border-radius: 8px;
        font-size: 14px;
        background: white;
        color: #333;
        min-width: 200px;
      }
      
      @media (prefers-color-scheme: dark) {
        input[type=text] { 
          background: #1a1a1a; 
          color: #e0e0e0; 
          border-color: #444;
        }
      }
      
      input[type=text]:focus {
        outline: none;
        border-color: var(--primary);
        box-shadow: 0 0 0 3px rgba(25, 118, 210, 0.1);
      }
      
      button { 
        padding: 10px 20px; 
        border-radius: 8px; 
        border: 1px solid var(--primary); 
        background: var(--primary); 
        color: white;
        cursor: pointer;
        font-weight: 500;
        font-size: 14px;
        transition: all 0.2s;
      }
      
      button:hover:not(:disabled) { 
        background: #1565c0;
        transform: translateY(-1px);
        box-shadow: 0 2px 8px rgba(25, 118, 210, 0.3);
      }
      
      button:disabled { 
        opacity: .5; 
        cursor: not-allowed;
        transform: none;
      }
      
      button.secondary {
        background: transparent;
        color: var(--primary);
      }
      
      button.secondary:hover:not(:disabled) {
        background: var(--primary-light);
      }
      
      .input-row {
        display: flex;
        gap: 8px;
        margin-bottom: 8px;
      }
      
      .input-row input {
        flex: 1;
      }
      
      .status { 
        padding: 8px 12px;
        font-size: 13px; 
        color: var(--text-secondary);
        background: var(--surface);
        border-radius: 6px;
        display: inline-block;
      }
      
      .status.active {
        color: var(--primary);
        background: var(--primary-light);
      }
      
      .status.error {
        color: #d32f2f;
        background: #ffebee;
      }
      
      .chat-container {
        flex: 1;
        display: flex;
        flex-direction: column;
        min-height: 0;
      }
      
      .chat { 
        flex: 1;
        border: 1px solid var(--border); 
        border-radius: 12px; 
        padding: 16px; 
        overflow-y: auto;
        background: white;
        box-shadow: inset 0 2px 4px rgba(0,0,0,0.05);
      }
      
      @media (prefers-color-scheme: dark) {
        .chat { background: #2a2a2a; }
      }
      
      .msg { 
        margin: 12px 0; 
        padding: 12px 16px; 
        border-radius: 12px; 
        max-width: 75%; 
        white-space: pre-wrap;
        word-wrap: break-word;
        animation: slideIn 0.2s ease-out;
      }
      
      @keyframes slideIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
      }
      
      .user { 
        background: var(--primary); 
        color: white;
        margin-left: auto;
        border-bottom-right-radius: 4px;
      }
      
      .bot { 
        background: #f0f0f0;
        color: #333;
        border-bottom-left-radius: 4px;
      }
      
      @media (prefers-color-scheme: dark) {
        .bot { background: #3a3a3a; color: #e0e0e0; }
      }
      
      .ask { 
        border-left: 3px solid var(--primary); 
        padding-left: 12px;
        background: var(--primary-light);
      }
      
      .chips { 
        display: flex; 
        gap: 8px; 
        flex-wrap: wrap; 
        margin-top: 8px;
      }
      
      .chip { 
        padding: 6px 12px; 
        border-radius: 20px; 
        border: 1px solid var(--primary); 
        font-size: 13px; 
        background: white;
        color: var(--primary);
        cursor: pointer;
        transition: all 0.2s;
      }
      
      .chip:hover {
        background: var(--primary);
        color: white;
        transform: translateY(-1px);
      }
      
      .empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        height: 100%;
        color: var(--text-secondary);
        text-align: center;
        padding: 32px;
      }
      
      .empty-state svg {
        width: 64px;
        height: 64px;
        margin-bottom: 16px;
        opacity: 0.5;
      }
      
      .typing-indicator {
        display: inline-flex;
        gap: 4px;
        padding: 8px 12px;
      }
      
      .typing-indicator span {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--primary);
        animation: bounce 1.4s infinite ease-in-out both;
      }
      
      .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
      .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
      
      @keyframes bounce {
        0%, 80%, 100% { transform: scale(0); }
        40% { transform: scale(1); }
      }
      
      .clear-btn {
        padding: 8px 12px;
        font-size: 13px;
        background: transparent;
        border: 1px solid #ccc;
        color: var(--text-secondary);
      }
      
      .clear-btn:hover {
        background: #f5f5f5;
        border-color: #999;
      }
      
      @media (max-width: 768px) {
        .wrap { padding: 8px; }
        .label { min-width: 80px; font-size: 13px; }
        input[type=text] { font-size: 16px; } /* Prevents zoom on iOS */
        .msg { max-width: 85%; }
      }
    </style>
  </head>
  <body>
    <div class="wrap">
      <header>
        <h1>🛒 ShopBot - Streaming Chat</h1>
      </header>
      
      <div class="config-section">
        <div class="row">
          <div class="label">User ID</div>
          <input id="userId" type="text" placeholder="Enter user ID" value="demo_user" />
        </div>
        <div class="row">
          <div class="label">Session ID</div>
          <input id="sessionId" type="text" placeholder="Auto-generated" />
          <button class="clear-btn" onclick="$('sessionId').value = ''; generateSession();">New Session</button>
        </div>
      </div>
      
      <div class="config-section">
        <div class="input-row">
          <input id="message" type="text" placeholder="Type your message... (e.g., 'I want chips for a party')" />
          <button id="sendBtn">Send</button>
          <button id="stopBtn" class="secondary" disabled>Stop</button>
        </div>
        <div id="status" class="status">Ready to chat</div>
      </div>

      <div class="chat-container">
        <div id="chat" class="chat">
          <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
            <p>Start a conversation by typing a message above</p>
          </div>
        </div>
      </div>
    </div>

    <script>
      const $ = (id) => document.getElementById(id);
      const chat = $("chat");
      const statusEl = $("status");
      const sendBtn = $("sendBtn");
      const stopBtn = $("stopBtn");
      const messageInput = $("message");

      let controller = null;
      let currentFinalBubble = null;
      let pendingFinalText = '';
      let rafScheduled = false;
      const slotToNode = new Map();
      let debugEventCount = 0;
      let debugChunkCount = 0;

      function debugLog(msg, data) {
        const timestamp = new Date().toISOString().split('T')[1];
        console.log(`[${timestamp}] ${msg}`, data || '');
      }

      function flushNow() {
        if (!pendingFinalText) return;
        if (!currentFinalBubble) {
          debugLog('🟢 CREATE_BUBBLE', 'Creating new bot bubble');
          currentFinalBubble = appendBubble('', 'bot');
        }
        debugLog('🎨 DOM_UPDATE', `Appending ${pendingFinalText.length} chars: "${pendingFinalText.substring(0, 20)}..."`);
        currentFinalBubble.textContent += pendingFinalText;
        pendingFinalText = '';
        chat.scrollTop = chat.scrollHeight;
      }
      
      function scheduleFlushFinal() {
        if (rafScheduled) return;
        rafScheduled = true;
        requestAnimationFrame(() => {
          rafScheduled = false;
          flushNow();
        });
      }

      // Auto-generate session on load
      function generateSession() {
        const timestamp = Date.now();
        const random = Math.random().toString(36).substring(2, 7);
        $("sessionId").value = `sess_${timestamp}_${random}`;
      }
      
      generateSession();

      function clearEmptyState() {
        const emptyState = chat.querySelector('.empty-state');
        if (emptyState) emptyState.remove();
      }

      function setStatus(text, type = 'normal') {
        statusEl.textContent = text;
        statusEl.className = 'status';
        if (type === 'active') statusEl.classList.add('active');
        if (type === 'error') statusEl.classList.add('error');
      }

      function appendBubble(text, role = 'bot', extraClass = '') {
        clearEmptyState();
        const div = document.createElement('div');
        div.className = `msg ${role} ${extraClass}`.trim();
        div.textContent = text;
        chat.appendChild(div);
        chat.scrollTop = chat.scrollHeight;
        return div;
      }
      
      function showTypingIndicator() {
        clearEmptyState();
        const indicator = document.createElement('div');
        indicator.className = 'msg bot';
        indicator.id = '_typing';
        indicator.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
        chat.appendChild(indicator);
        chat.scrollTop = chat.scrollHeight;
      }
      
      function hideTypingIndicator() {
        const indicator = document.getElementById('_typing');
        if (indicator) indicator.remove();
      }

      function renderAsk(slotName, message, options) {
        clearEmptyState();
        hideTypingIndicator();
        
        const wrap = document.createElement('div');
        wrap.className = 'msg bot ask';
        const title = document.createElement('div');
        title.textContent = message || slotName;
        wrap.appendChild(title);
        
        if (options && options.length) {
          const row = document.createElement('div');
          row.className = 'chips';
          for (const opt of options) {
            const chip = document.createElement('button');
            chip.className = 'chip';
            chip.textContent = opt;
            chip.addEventListener('click', () => {
              messageInput.value = opt;
              startStream(opt);
            });
            row.appendChild(chip);
          }
          wrap.appendChild(row);
        }
        
        chat.appendChild(wrap);
        chat.scrollTop = chat.scrollHeight;
        return wrap;
      }

      function nowSession() {
        const userId = $("userId").value.trim() || 'anonymous';
        let sid = $("sessionId").value.trim();
        if (!sid) {
          generateSession();
          sid = $("sessionId").value;
        }
        return { user_id: userId, session_id: sid };
      }

      function sseParseLines(onEvent) {
        let buffer = '';
        let eventType = null;
        let dataLines = [];
        let flushCount = 0;
        
        function flush() {
          if (!eventType && dataLines.length === 0) return;
          flushCount++;
          const data = dataLines.join('\\n');
          debugLog(`🔄 SSE_FLUSH_${flushCount}`, `Emitting ${eventType || 'message'} event`);
          onEvent({ event: eventType || 'message', data });
          eventType = null;
          dataLines = [];
        }
        
        return (text) => {
          buffer += text;
          const lines = buffer.split(/\\r?\\n/);
          buffer = lines.pop() || '';
          
          debugLog('🔍 SSE_PARSE', `Processing ${lines.length} lines`);
          
          for (const line of lines) {
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice(5).trim());
            } else if (line.trim() === '') {
              flush();
            }
          }
        };
      }

      async function startStream(message) {
        if (!message || !message.trim()) return;
        
        // Abort any existing stream
        if (controller) {
          controller.abort();
          hideTypingIndicator();
        }
        
        controller = new AbortController();
        const { user_id, session_id } = nowSession();
        
        // Reset debug counters
        debugEventCount = 0;
        debugChunkCount = 0;
        debugLog('🚀 STREAM_INIT', `Starting new stream for message: "${message.substring(0, 30)}..."`);
        
        // Clear current final bubble for new conversation
        currentFinalBubble = null;
        slotToNode.clear();

        // Add user message
        appendBubble(message, 'user');
        messageInput.value = '';
        
        // Show loading state
        showTypingIndicator();
        setStatus('Connecting...', 'active');
        sendBtn.disabled = true;
        stopBtn.disabled = false;

        const payload = { 
          user_id, 
          session_id, 
          message, 
          channel: 'web', 
          wa_id: '' 
        };
        
        try {
          const res = await fetch('/rs/chat/stream', {
            method: 'POST',
            headers: { 
              'Content-Type': 'application/json', 
              'Accept': 'text/event-stream' 
            },
            body: JSON.stringify(payload),
            signal: controller.signal,
          });
          
          if (!res.ok) {
            const errorText = await res.text();
            throw new Error(`Server error (${res.status}): ${errorText}`);
          }
          
          if (!res.body) {
            throw new Error('No response body');
          }

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          const feed = sseParseLines(handleEvent);

          debugLog('📡 STREAM_START', 'Beginning to read chunks from server');

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              debugLog('📡 STREAM_DONE', 'No more chunks');
              break;
            }
            debugChunkCount++;
            const chunkText = decoder.decode(value, { stream: true });
            debugLog(`📦 CHUNK_${debugChunkCount}`, `Received ${value.length} bytes, ${chunkText.length} chars`);
            feed(chunkText);
          }
          
        } catch (err) {
          hideTypingIndicator();
          if (err.name === 'AbortError') {
            setStatus('Stopped by user', 'normal');
          } else {
            console.error('Stream error:', err);
            setStatus('Error: ' + (err.message || 'Connection failed'), 'error');
            appendBubble('Sorry, something went wrong. Please try again.', 'bot');
          }
        } finally {
          hideTypingIndicator();
          sendBtn.disabled = false;
          stopBtn.disabled = true;
          controller = null;
        }

        function handleEvent(evt) {
          try {
            const { event, data } = evt;
            debugEventCount++;
            debugLog(`🔔 EVENT_${debugEventCount}`, `type=${event}`);
            
            if (event === 'ack') {
              const d = JSON.parse(data);
              setStatus(`Connected • ${d.session_id.substring(0, 20)}...`, 'active');
              return;
            }
            
            if (event === 'status') {
              const d = JSON.parse(data);
              const stage = d.stage || 'processing';
              setStatus(`Processing: ${stage}`, 'active');
              return;
            }
            
            if (event === 'ask_message_delta') {
              const d = JSON.parse(data);
              const slot = d.slot_name || 'generic';
              const node = renderAsk(slot, d.text, []);
              slotToNode.set(slot, node);
              return;
            }
            
            if (event === 'ask_options_delta') {
              const d = JSON.parse(data);
              const slot = d.slot_name;
              const node = slotToNode.get(slot);
              
              if (node) {
                const old = node.querySelector('.chips');
                if (old) old.remove();
                
                const row = document.createElement('div');
                row.className = 'chips';
                
                for (const opt of d.options || []) {
                  const chip = document.createElement('button');
                  chip.className = 'chip';
                  chip.textContent = opt;
                  chip.addEventListener('click', () => {
                    messageInput.value = opt;
                    startStream(opt);
                  });
                  row.appendChild(chip);
                }
                
                node.appendChild(row);
                chat.scrollTop = chat.scrollHeight;
              }
              return;
            }
            
            if (event === 'final_answer.delta') {
              const d = JSON.parse(data);
              const delta = d.delta || '';
              debugLog(`⚡ DELTA_RECEIVED`, `"${delta}" (${delta.length} chars)`);
              hideTypingIndicator();
              pendingFinalText += delta;
              debugLog(`📝 BUFFER_STATUS`, `Pending text now ${pendingFinalText.length} chars`);
              // Flush immediately to guarantee progressive paint
              flushNow();
              return;
            }
            
            if (event === 'final_answer.complete') {
              debugLog('✅ COMPLETE', 'Final answer complete');
              // Ensure buffered text is rendered, do not duplicate with summary_message
              flushNow();
              return;
            }
            
            if (event === 'end') {
              debugLog('🏁 END', 'Stream ended');
              hideTypingIndicator();
              setStatus('Ready to chat', 'normal');
              // Final synchronous flush to avoid duplicates
              flushNow();
              return;
            }
            
            if (event === 'error') {
              hideTypingIndicator();
              const d = JSON.parse(data);
              const errorMsg = d.message || 'Unknown error';
              setStatus('Error: ' + errorMsg, 'error');
              appendBubble('Sorry, an error occurred: ' + errorMsg, 'bot');
              return;
            }
            
          } catch (e) {
            console.warn('Event parse error:', e, evt);
          }
        }
      }

      // Event listeners
      sendBtn.addEventListener('click', () => {
        const msg = messageInput.value.trim();
        if (msg) startStream(msg);
      });
      
      messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          const msg = messageInput.value.trim();
          if (msg) startStream(msg);
        }
      });
      
      stopBtn.addEventListener('click', () => {
        if (controller) {
          controller.abort();
          setStatus('Stopped', 'normal');
        }
        sendBtn.disabled = false;
        stopBtn.disabled = true;
      });
      
      // Focus message input on load
      messageInput.focus();
    </script>
  </body>
</html>
"""


