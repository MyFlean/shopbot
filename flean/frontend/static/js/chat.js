class ChatApp {
  constructor() {
    this.sessionId = null;
    this.userId = 'web_user';
    this.messageContainer = document.getElementById('messageContainer');
    this.messageInput = document.getElementById('messageInput');
    this.sendBtn = document.getElementById('sendBtn');
    this.isTyping = false;
    this.init();
  }

  async init() {
    await this.createSession();
    this.setupEventListeners();
    await this.loadHistory();
    this.messageInput.focus();
  }

  setupEventListeners() {
    this.sendBtn.addEventListener('click', () => this.handleSend());
    
    this.messageInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.handleSend();
      }
    });

    // Add input animation
    this.messageInput.addEventListener('input', () => {
      this.sendBtn.style.transform = this.messageInput.value.trim() 
        ? 'scale(1.05)' 
        : 'scale(1)';
    });

    // Smooth scroll on new messages
    const observer = new MutationObserver(() => {
      this.scrollToBottom();
    });
    observer.observe(this.messageContainer, { childList: true });
  }

  async createSession() {
    try {
      const res = await fetch('/api/chat/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: this.userId })
      });
      const data = await res.json();
      this.sessionId = data.session_id;
    } catch (e) {
      console.error('Failed to create session:', e);
      this.showError('Failed to connect. Please refresh the page.');
    }
  }

  async loadHistory() {
    try {
      const res = await fetch(
        `/api/chat/history?user_id=${encodeURIComponent(this.userId)}&limit=50`
      );
      const data = await res.json();
      const history = data.history || [];
      
      if (history.length === 0) {
        this.showWelcomeMessage();
      } else {
        for (const turn of history) {
          if (turn.user_query) {
            this.renderMessage(turn.user_query, true, false);
          }
          if (turn.bot_reply || (turn.final_answer && turn.final_answer.message_preview)) {
            const text = turn.bot_reply || turn.final_answer.message_preview || '';
            this.renderMessage(text, false, false);
          }
        }
      }
      
      this.scrollToBottom();
    } catch (e) {
      console.error('Failed to load history:', e);
    }
  }

  showWelcomeMessage() {
    const welcomeText = "Hi! 👋 I'm Flean, your AI shopping assistant. I'm here to help you find the perfect products. What are you looking for today?";
    this.renderMessage(welcomeText, false, true);
  }

  async handleSend() {
    const text = this.messageInput.value.trim();
    if (!text || this.isTyping) return;

    // Render user message immediately
    this.renderMessage(text, true, true);
    this.messageInput.value = '';
    this.sendBtn.style.transform = 'scale(1)';

    // Show typing indicator
    this.showTypingIndicator();
    this.isTyping = true;

    try {
      const res = await fetch('/api/chat/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: this.userId,
          session_id: this.sessionId,
          message: text
        })
      });

      if (!res.ok) throw new Error('Request failed');

      const data = await res.json();
      
      // Remove typing indicator
      this.hideTypingIndicator();
      
      // Render bot response
      const content = data.content || {};
      const summary = content.summary_message || content.message || '';
      
      if (summary) {
        // Simulate typing delay for more natural feel
        await this.delay(300);
        this.renderMessage(summary, false, true);
      }

      // Render products
      const ux = content.ux_response || {};
      const productIds = ux.product_ids || [];
      
      if (Array.isArray(productIds) && productIds.length) {
        await this.delay(400);
        for (const id of productIds.slice(0, 3)) {
          this.renderProduct({
            id,
            name: `Product ${id}`,
            price: '',
            image_url: ''
          }, true);
          await this.delay(150);
        }
      }

      if (Array.isArray(content.products)) {
        await this.delay(400);
        for (const p of content.products.slice(0, 3)) {
          this.renderProduct(p, true);
          await this.delay(150);
        }
      }

      this.scrollToBottom();
    } catch (e) {
      console.error('Message send failed:', e);
      this.hideTypingIndicator();
      this.renderMessage('Sorry, something went wrong. Please try again.', false, true);
    } finally {
      this.isTyping = false;
    }
  }

  renderMessage(message, isUser, animate = true) {
    const wrapper = document.createElement('div');
    wrapper.style.display = 'flex';
    wrapper.style.flexDirection = 'column';
    wrapper.style.alignItems = isUser ? 'flex-end' : 'flex-start';
    
    const bubble = document.createElement('div');
    bubble.className = `bubble ${isUser ? 'user' : 'bot'}`;
    
    if (!animate) {
      bubble.style.animation = 'none';
    }
    
    // Handle line breaks and formatting
    const formattedMessage = this.formatMessage(message);
    bubble.innerHTML = formattedMessage;

    const meta = document.createElement('div');
    meta.className = 'meta';
    const now = new Date();
    meta.innerHTML = `
      <span>${isUser ? '👤 You' : '🤖 Flean'}</span>
      <span>·</span>
      <span>${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
    `;
    
    bubble.appendChild(meta);
    wrapper.appendChild(bubble);
    this.messageContainer.appendChild(wrapper);
    
    if (animate) {
      this.scrollToBottom();
    }
  }

  handleProductClick(product) {
    // Add haptic feedback on mobile
    if (navigator.vibrate) {
      navigator.vibrate(10);
    }
    
    // You can add product detail view or external link here
    console.log('Product clicked:', product);
    
    // Example: Send message about the product
    const message = `Tell me more about ${product.name || 'this product'}`;
    this.messageInput.value = message;
    this.messageInput.focus();
  }

  handleProductView(product) {
    // Add haptic feedback on mobile
    if (navigator.vibrate) {
      navigator.vibrate(10);
    }
    
    console.log('View product:', product);
    
    // You can open product in new tab or modal here
    // window.open(product.url, '_blank');
  }

  showTypingIndicator() {
    const indicator = document.createElement('div');
    indicator.className = 'typing-indicator';
    indicator.id = 'typingIndicator';
    
    for (let i = 0; i < 3; i++) {
      const dot = document.createElement('span');
      indicator.appendChild(dot);
    }
    
    this.messageContainer.appendChild(indicator);
    this.scrollToBottom();
  }

  hideTypingIndicator() {
    const indicator = document.getElementById('typingIndicator');
    if (indicator) {
      indicator.style.animation = 'slideOut 0.2s ease';
      setTimeout(() => indicator.remove(), 200);
    }
  }

  showError(message) {
    const errorDiv = document.createElement('div');
    errorDiv.style.cssText = `
      position: fixed;
      top: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: linear-gradient(135deg, #ef4444, #dc2626);
      color: white;
      padding: 12px 24px;
      border-radius: 12px;
      box-shadow: 0 10px 25px rgba(239, 68, 68, 0.3);
      z-index: 1000;
      animation: slideIn 0.3s ease;
      font-size: 14px;
      font-weight: 500;
    `;
    errorDiv.textContent = message;
    document.body.appendChild(errorDiv);
    
    setTimeout(() => {
      errorDiv.style.animation = 'slideOut 0.3s ease';
      setTimeout(() => errorDiv.remove(), 300);
    }, 3000);
  }

  scrollToBottom() {
    requestAnimationFrame(() => {
      this.messageContainer.scrollTop = this.messageContainer.scrollHeight;
    });
  }

  delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
  
  formatMessage(message) {
    let formatted = message.replace(/\n/g, '<br>');
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');
    return formatted;
  }

  renderProduct(product, animate = true) {
    const card = document.createElement('div');
    card.className = 'product-card';
    if (!animate) {
      card.style.animation = 'none';
    }

    const img = document.createElement('img');
    img.src = product.image_url || 'https://via.placeholder.com/80/667eea/ffffff?text=Product';
    img.alt = product.name || 'Product image';
    img.loading = 'lazy';

    const info = document.createElement('div');
    const name = document.createElement('div');
    name.textContent = product.name || product.title || 'Product';
    name.title = product.name || product.title || 'Product';
    const price = document.createElement('div');
    if (product.price) {
      price.textContent = `₹${product.price}`;
    }
    info.appendChild(name);
    if (product.price) {
      info.appendChild(price);
    }

    const viewBtn = document.createElement('button');
    viewBtn.textContent = '👁️';
    viewBtn.style.cssText = `
      background: var(--green);
      border: none;
      color: white;
      width: 36px;
      height: 36px;
      border-radius: 50%;
      cursor: pointer;
      font-size: 16px;
      transition: transform 0.2s ease;
    `;
    viewBtn.addEventListener('mouseenter', () => { viewBtn.style.transform = 'scale(1.1)'; });
    viewBtn.addEventListener('mouseleave', () => { viewBtn.style.transform = 'scale(1)'; });
    viewBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.handleProductView(product);
    });

    card.appendChild(img);
    card.appendChild(info);
    card.appendChild(viewBtn);
    card.addEventListener('click', () => { this.handleProductClick(product); });
    this.messageContainer.appendChild(card);
    if (animate) {
      this.scrollToBottom();
    }
  }
}

// Add slideOut animation
const style = document.createElement('style');
style.textContent = `
  @keyframes slideOut {
    to {
      opacity: 0;
      transform: translateY(-10px);
    }
  }
`;
document.head.appendChild(style);

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
  new ChatApp();
  
  // Add loading animation
  const container = document.querySelector('.chat-container');
  if (container) {
    container.style.opacity = '0';
    container.style.transform = 'translateY(20px)';
    
    requestAnimationFrame(() => {
      container.style.transition = 'all 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)';
      container.style.opacity = '1';
      container.style.transform = 'translateY(0)';
    });
  }
});