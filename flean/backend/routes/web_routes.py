
from __future__ import annotations

from flask import Blueprint, jsonify, request

from shopping_bot.redis_manager import RedisContextManager
from shopping_bot.bot_core import ShoppingBotCore
from .services_placeholder import get_chat_service

web_bp = Blueprint('web', __name__, url_prefix='/api/chat')


@web_bp.route('/message', methods=['POST'])
async def send_message():
    data = request.get_json(force=True) or {}
    user_id = str(data.get('user_id') or 'web_user')
    message = str(data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400
    svc = get_chat_service()
    result = await svc.process_message(user_id, message, session_id=data.get('session_id'))
    return jsonify(result), 200


@web_bp.route('/history', methods=['GET'])
def get_history():
    user_id = request.args.get('user_id') or 'web_user'
    limit = int(request.args.get('limit', 50))
    svc = get_chat_service()
    items = svc.get_history(user_id, limit=limit)
    return jsonify({"history": items}), 200


@web_bp.route('/session', methods=['POST'])
def create_session():
    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id') or 'web_user'
    svc = get_chat_service()
    sess = svc.create_session(user_id)
    return jsonify(sess), 201


@web_bp.route('/products', methods=['GET'])
def get_products():
    user_id = request.args.get('user_id') or 'web_user'
    svc = get_chat_service()
    rec = svc.get_product_recommendations(user_id)
    return jsonify(rec), 200


@web_bp.route('/feedback', methods=['POST'])
def submit_feedback():
    data = request.get_json(force=True) or {}
    user_id = str(data.get('user_id') or 'web_user')
    message = str(data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "Feedback message required"}), 400
    # Store feedback in Redis list (reuse key used by simplified chat)
    try:
        ctx_mgr = get_chat_service().ctx_mgr
        ctx_mgr.redis.lpush("feedback:items", __import__('json').dumps({
            "title": "Web Feedback",
            "user_id": user_id,
            "message": message,
        }))
    except Exception:
        pass
    return jsonify({"ok": True}), 200
