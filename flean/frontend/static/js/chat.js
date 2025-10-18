
class ChatApp {
  constructor() {
    this.sessionId = null;
    this.userId = 'web_user'; // static per instructions
    this.messageContainer = document.getElementById('messageContainer');
    this.messageInput = document.getElementById('messageInput');
    this.sendBtn = document.getElementById('sendBtn');
    this.init();
  }

  async init() {
    await this.createSession();
    this.sendBtn.addEventListener('click', () => this.handleSend());
    this.messageInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.handleSend(); });
    await this.loadHistory();
  }

  async createSession() {
    const res = await fetch('/api/chat/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: this.userId }) });
    const data = await res.json();
    this.sessionId = data.session_id;
  }

  async loadHistory() {
    const res = await fetch(`/api/chat/history?user_id=${encodeURIComponent(this.userId)}&limit=50`);
    const data = await res.json();
    const history = data.history || [];
    for (const turn of history) {
      if (turn.user_query) this.renderMessage(turn.user_query, true);
      if (turn.bot_reply || (turn.final_answer && turn.final_answer.message_preview)) {
        const text = turn.bot_reply || (turn.final_answer && turn.final_answer.message_preview) || '';
        this.renderMessage(text, false);
      }
    }
    this.scrollToBottom();
  }

  async handleSend() {
    const text = this.messageInput.value.trim();
    if (!text) return;
    this.renderMessage(text, true);
    this.messageInput.value = '';
    try {
      const res = await fetch('/api/chat/message', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: this.userId, session_id: this.sessionId, message: text }) });
      const data = await res.json();
      const content = data.content || {};
      const summary = content.summary_message || content.message || '';
      if (summary) this.renderMessage(summary, false);
      const ux = content.ux_response || {};
      const productIds = (ux.product_ids || []);
      if (Array.isArray(productIds) && productIds.length) {
        for (const id of productIds.slice(0, 3)) {
          this.renderProduct({ id, name: `Product ${id}`, price: '', image_url: '' });
        }
      }
      // Also render products if provided differently
      if (Array.isArray(content.products)) {
        for (const p of content.products.slice(0, 3)) this.renderProduct(p);
      }
      this.scrollToBottom();
    } catch (e) {
      this.renderMessage('Sorry, something went wrong.', false);
    }
  }

  renderMessage(message, isUser) {
    const div = document.createElement('div');
    div.className = 'bubble ' + (isUser ? 'user' : 'bot');
    div.textContent = message;
    const meta = document.createElement('div');
    meta.className = 'meta';
    const now = new Date();
    meta.textContent = (isUser ? 'You' : 'Flean') + ' · ' + now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    div.appendChild(document.createElement('br'));
    div.appendChild(meta);
    this.messageContainer.appendChild(div);
  }

  renderProduct(product) {
    const card = document.createElement('div');
    card.className = 'product-card';
    const img = document.createElement('img');
    img.src = product.image_url || 'https://via.placeholder.com/64';
    const info = document.createElement('div');
    const name = document.createElement('div');
    name.textContent = product.name || product.title || 'Product';
    const price = document.createElement('div');
    price.textContent = product.price ? `₹${product.price}` : '';
    info.appendChild(name);
    info.appendChild(price);
    card.appendChild(img);
    card.appendChild(info);
    this.messageContainer.appendChild(card);
  }

  scrollToBottom() {
    this.messageContainer.scrollTop = this.messageContainer.scrollHeight;
  }
}

document.addEventListener('DOMContentLoaded', () => new ChatApp());
