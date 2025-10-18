

## Flean Web Frontend (Chat UI)

Run local server:

```bash
export FLASK_APP=flean.backend.app:app
export FLASK_ENV=development
# Ensure Redis is running and ANTHROPIC_API_KEY is set for full pipeline
python3 -m flask run --port 8000
```

Open `http://localhost:8000` for the chat UI.

API endpoints:
- POST `/api/chat/session` → { user_id } → { session_id }
- POST `/api/chat/message` → { user_id, session_id, message } → response payload
- GET `/api/chat/history?user_id=...&limit=50`
- GET `/api/chat/products?user_id=...`
- POST `/api/chat/feedback` → { user_id, message }

Folder layout:
- `flean/backend/app.py` (Flask app + CORS)
- `flean/backend/routes/web_routes.py` (API)
- `flean/backend/services/chat_service.py` (shared chat logic)
- `flean/frontend/templates/*.html` (UI)
- `flean/frontend/static/{css,js}` (assets)
