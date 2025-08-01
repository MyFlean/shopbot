# run.py


from __future__ import annotations

import os

from shopping_bot import create_app

app = create_app()  # Gunicorn / Hypercorn will look for `app` at module level

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    # `debug=True` only when FLASK_ENV=development
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", True))
