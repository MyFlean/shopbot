import logging, os, sys

def setup_logging():
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    # Prefer Cloud Logging on Cloud Run if lib is installed
    if os.getenv("K_SERVICE") and os.getenv("USE_GCP_LOGGING", "true").lower() == "true":
        try:
            from google.cloud import logging as cloud_logging
            client = cloud_logging.Client()
            client.setup_logging(log_level=level)   # routes root logger to GCP
            return
        except Exception:
            pass  # fall back to stdout

    # Fallback: stream everything to stdout (captured by Cloud Run)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):  # remove defaults
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s'))
    root.addHandler(sh)

    # Optional: tune noisy libs
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
