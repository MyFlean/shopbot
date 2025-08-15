import logging, os, sys

def setup_logging():
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    configured_cloud = False
    # Prefer Cloud Logging on Cloud Run if available
    if os.getenv("K_SERVICE") and os.getenv("USE_GCP_LOGGING", "true").lower() == "true":
        try:
            from google.cloud import logging as cloud_logging
            client = cloud_logging.Client()
            client.setup_logging(log_level=level)   # routes root logger to Cloud Logging
            configured_cloud = True
        except Exception:
            configured_cloud = False  # fall through to stdout

    if not configured_cloud:
        # Fallback: log to stdout (captured by Cloud Run)
        root = logging.getLogger()
        root.setLevel(level)
        for h in list(root.handlers):
            root.removeHandler(h)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s'))
        root.addHandler(sh)

    # Tidy / tune levels regardless of backend
    logging.captureWarnings(True)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    # Make our packages and your core logger emit INFO+
    for name in (
        "shopping_bot",                          # whole package
        "shopping_bot.background_processor",     # BG logs
        "shopping_bot.routes.chat",              # chat endpoint decisions
        "bot_core",                              # INTENT/ASK/DEFER/etc.
        "gunicorn.error",                        # optional: align gunicorn severity
        "gunicorn.access",
    ):
        logging.getLogger(name).setLevel(level)
