from __future__ import annotations

import json
from typing import Any, Dict

from flask import Blueprint, Response


bp = Blueprint("chat_ui", __name__)


@bp.route("/chat/ui", methods=["GET"])
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
      const askBuffer = new Map(); // slot_name -> entry {slotName, message, options, order, rendered, completed}
      let askQueue = [];
      let askPlan = [];
      let activeAskEntry = null;
      let awaitingAskResponse = false;
      // Streaming products state
      let productsNode = null;
      let productIds = [];
      let heroProductId = null;
      // Streaming quick replies state
      let quickReplies = [];
      let quickRepliesNode = null;

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

      function resetAskState() {
        askBuffer.clear();
        askQueue = [];
        askPlan = [];
        activeAskEntry = null;
        awaitingAskResponse = false;
      }

      function ensureAskEntry(slotName) {
        if (!slotName) return null;
        const existing = askBuffer.get(slotName);
        if (existing) return existing;
        const entry = {
          slotName,
          message: '',
          options: [],
          order: Number.MAX_SAFE_INTEGER,
          rendered: false,
          completed: false,
        };
        askBuffer.set(slotName, entry);
        return entry;
      }

      function applyAskPlan(planSlots) {
        if (!Array.isArray(planSlots)) return;
        askPlan = planSlots;
        planSlots.forEach((item, idx) => {
          if (!item) return;
          const slot = item.slot_name || item.slotName;
          if (!slot) return;
          const entry = ensureAskEntry(slot);
          entry.order = typeof item.order === 'number' ? item.order : idx;
          if (item.message && !entry.message) entry.message = item.message;
          if (Array.isArray(item.options) && item.options.length && entry.options.length === 0) {
            entry.options = item.options.slice(0, 3).map(opt => typeof opt === 'string' ? opt : String(opt));
          }
        });
        rebuildAskQueue();
      }

      function rebuildAskQueue() {
        askQueue = Array.from(askBuffer.values())
          .filter(entry => !entry.completed)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        scheduleNextAsk();
      }

      function scheduleNextAsk() {
        debugLog('🔍 SCHEDULE_NEXT_CHECK', `awaitingAskResponse=${awaitingAskResponse}, activeAskEntry=${activeAskEntry ? activeAskEntry.slotName : 'null'}, queueSize=${askQueue.length}`);
        if (awaitingAskResponse) {
          debugLog('⏸️ SCHEDULE_BLOCKED', 'Still awaiting response');
          return;
        }
        if (activeAskEntry && !activeAskEntry.completed) {
          debugLog('⏸️ SCHEDULE_BLOCKED', `Active entry not completed: ${activeAskEntry.slotName}`);
          return;
        }
        const next = askQueue.find(entry => !entry.rendered && entry.message && entry.options.length);
        if (!next) {
          debugLog('❌ NO_NEXT_FOUND', `Queue entries: ${askQueue.map(e => `${e.slotName}(rendered=${e.rendered}, hasMsg=${!!e.message}, opts=${e.options.length})`).join(', ')}`);
          return;
        }
        debugLog('✅ SHOWING_NEXT', `Slot: ${next.slotName}, Message: ${next.message.substring(0, 30)}...`);
        showAskEntry(next);
      }

      function showAskEntry(entry) {
        const opts = entry.options.map(opt => (typeof opt === 'string' ? opt : String(opt)));
        const node = renderAsk(entry.slotName, entry.message, opts);
        slotToNode.set(entry.slotName, node);
        entry.rendered = true;
        activeAskEntry = entry;
        awaitingAskResponse = true;
      }

      function updateAskMessageDom(slotName, message) {
        const node = slotToNode.get(slotName);
        if (!node) return;
        const title = node.querySelector('div');
        if (title) title.textContent = message;
      }

      function updateAskOptionsDom(slotName, options) {
        const node = slotToNode.get(slotName);
        if (!node) return;
        const normalized = (options || []).map(opt => (typeof opt === 'string' ? opt : String(opt)));
        const existing = node.querySelector('.chips');
        if (existing) existing.remove();
        if (!normalized.length) return;
        const row = document.createElement('div');
        row.className = 'chips';
        for (const opt of normalized) {
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

      function ensureProductsNode() {
        if (productsNode && document.body.contains(productsNode)) return productsNode;
        clearEmptyState();
        const wrap = document.createElement('div');
        wrap.className = 'msg bot';
        const title = document.createElement('div');
        title.style.fontWeight = '600';
        title.style.marginBottom = '6px';
        title.textContent = 'Products';
        wrap.appendChild(title);
        const list = document.createElement('div');
        list.id = '_products_list';
        wrap.appendChild(list);
        chat.appendChild(wrap);
        chat.scrollTop = chat.scrollHeight;
        productsNode = wrap;
        return productsNode;
      }

      function renderProducts() {
        if (!Array.isArray(productIds) || productIds.length === 0) return;
        const node = ensureProductsNode();
        const list = node.querySelector('#_products_list');
        if (!list) return;
        list.innerHTML = '';
        const ul = document.createElement('ul');
        ul.style.paddingLeft = '18px';
        for (const id of productIds) {
          const li = document.createElement('li');
          const isHero = heroProductId && id === heroProductId;
          li.textContent = isHero ? `${id} (hero)` : id;
          ul.appendChild(li);
        }
        list.appendChild(ul);
        chat.scrollTop = chat.scrollHeight;
      }

      function ensureQuickRepliesNode() {
        if (quickRepliesNode && document.body.contains(quickRepliesNode)) return quickRepliesNode;
        clearEmptyState();
        const wrap = document.createElement('div');
        wrap.className = 'msg bot';
        const title = document.createElement('div');
        title.style.fontWeight = '600';
        title.style.margin = '6px 0';
        title.textContent = 'Follow-ups';
        wrap.appendChild(title);
        const row = document.createElement('div');
        row.className = 'chips';
        row.id = '_qr_row';
        wrap.appendChild(row);
        chat.appendChild(wrap);
        chat.scrollTop = chat.scrollHeight;
        quickRepliesNode = wrap;
        return quickRepliesNode;
      }

      function renderQuickReplies() {
        if (!Array.isArray(quickReplies) || quickReplies.length === 0) return;
        const node = ensureQuickRepliesNode();
        const row = node.querySelector('#_qr_row');
        if (!row) return;
        row.innerHTML = '';
        for (const q of quickReplies) {
          const chip = document.createElement('button');
          chip.className = 'chip';
          chip.textContent = String(q);
          chip.addEventListener('click', () => {
            messageInput.value = String(q);
            startStream(String(q));
          });
          row.appendChild(chip);
        }
        chat.scrollTop = chat.scrollHeight;
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
        // DON'T clear slotToNode here - we need it for ask_next events
        // slotToNode.clear();

        // Add user message
        appendBubble(message, 'user');
        messageInput.value = '';

        // Track answer if we're in ASK phase - but DON'T reset state yet
        // The backend will send ask_next event which will handle state transitions
        if (activeAskEntry && awaitingAskResponse) {
          activeAskEntry.answer = message;
          // Mark as completed but keep in buffer - ask_next will handle cleanup
          activeAskEntry.completed = true;
          awaitingAskResponse = false;
          // Don't reset state here - wait for ask_next or ask_complete event
          debugLog('📝 ANSWER_TRACKED', `Answer "${message}" stored for ${activeAskEntry.slotName}, waiting for ask_next event`);
        }
        
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
            
            if (event === 'classification_start') {
              resetAskState();
              slotToNode.clear();
              return;
            }

            if (event === 'ask_message_delta') {
              const d = JSON.parse(data);
              const slot = d.slot_name || 'generic';
              const entry = ensureAskEntry(slot);
              if (d.text) {
                entry.message = d.text;
                if (entry.rendered) updateAskMessageDom(slot, entry.message);
              }
              if (typeof entry.order !== 'number' || Number.isNaN(entry.order)) {
                const planItem = askPlan.find(item => (item.slot_name || item.slotName) === slot);
                if (planItem && typeof planItem.order === 'number') entry.order = planItem.order;
              }
              rebuildAskQueue();
              return;
            }
            
            if (event === 'ask_options_delta') {
              const d = JSON.parse(data);
              const slot = d.slot_name;
              const entry = ensureAskEntry(slot);
              const opts = Array.isArray(d.options) ? d.options.slice(0, 3) : [];
              entry.options = opts.map(opt => (typeof opt === 'string' ? opt : String(opt)));
              if (entry.rendered) updateAskOptionsDom(slot, entry.options);
              rebuildAskQueue();
              return;
            }

            if (event === 'ask_plan') {
              try {
                const d = JSON.parse(data || '{}');
                applyAskPlan(d.slots || []);
              } catch (err) {
                debugLog('ask_plan_parse_error', err);
              }
              return;
            }
            
            if (event === 'ask_phase_start') {
              const d = JSON.parse(data);
              debugLog('🎯 ASK_PHASE_START', `Total questions: ${d.total_questions}, First: ${d.first_question}`);
              setStatus(`Asking ${d.total_questions} questions...`, 'active');
              return;
            }
            
            if (event === 'ask_next') {
              try {
                const d = JSON.parse(data);
                debugLog('➡️ ASK_NEXT', `Completed: ${d.completed_slot}, Next: ${d.slot_name}, Remaining: ${d.remaining_count}`);
                
                // Mark completed question as done
                if (d.completed_slot) {
                  const completedEntry = askBuffer.get(d.completed_slot);
                  if (completedEntry) {
                    completedEntry.completed = true;
                    debugLog('✅ MARK_COMPLETE', `Slot ${d.completed_slot} marked as completed`);
                    // Remove from DOM
                    const node = slotToNode.get(d.completed_slot);
                    if (node) {
                      node.remove();
                      slotToNode.delete(d.completed_slot);
                    }
                  }
                }
                
                // Reset active state BEFORE rebuilding queue
                activeAskEntry = null;
                awaitingAskResponse = false;
                
                // Rebuild queue to include next question
                debugLog('🔄 REBUILD_QUEUE', `Buffer size: ${askBuffer.size}`);
                rebuildAskQueue();
                
                setStatus(`Question ${d.remaining_count} remaining...`, 'active');
                debugLog('✅ ASK_NEXT_COMPLETE', `Queue size: ${askQueue.length}, Active: ${activeAskEntry ? activeAskEntry.slotName : 'none'}`);
              } catch (err) {
                console.error('ask_next handler error:', err);
                debugLog('❌ ASK_NEXT_ERROR', err.message);
              }
              return;
            }
            
            if (event === 'ask_complete') {
              const d = JSON.parse(data);
              debugLog('✅ ASK_COMPLETE', 'All questions answered, proceeding to search');
              setStatus('Searching products...', 'active');
              resetAskState();
              // Backend will now stream product results, so just wait
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

            if (event === 'final_answer.hero_product.delta') {
              const d = JSON.parse(data || '{}');
              if (d.hero_product_id) {
                heroProductId = d.hero_product_id;
                renderProducts();
              }
              return;
            }

            if (event === 'final_answer.product_ids.delta') {
              const d = JSON.parse(data || '{}');
              if (Array.isArray(d.product_ids)) {
                productIds = d.product_ids.slice();
                renderProducts();
              }
              return;
            }

            if (event === 'final_answer.quick_replies.delta') {
              const d = JSON.parse(data || '{}');
              if (Array.isArray(d.quick_replies)) {
                quickReplies = d.quick_replies.slice(0, 4).map(x => String(x));
                renderQuickReplies();
              }
              return;
            }
            
            if (event === 'final_answer.complete') {
              debugLog('✅ COMPLETE', 'Final answer complete');
              // Ensure buffered text is rendered, do not duplicate with summary_message
              flushNow();
              // Parse envelope to pick UX quick replies if present
              try {
                const env = JSON.parse(data || '{}');
                const content = env.content || {};
                const ux = content.ux || {};
                const qrs = ux.quick_replies || content.quick_replies || [];
                if (Array.isArray(qrs) && qrs.length) {
                  quickReplies = qrs.slice(0, 4).map(x => String(x));
                  renderQuickReplies();
                }
              } catch (e) {
                debugLog('final_answer.complete parse error', e);
              }
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


