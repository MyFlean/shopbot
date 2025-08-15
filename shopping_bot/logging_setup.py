import logging, os, sys

def setup_logging():
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    # If Cloud Run, prefer Cloud Logging if present; else stdout
    if os.getenv("K_SERVICE") and os.getenv("USE_GCP_LOGGING", "true").lower() == "true":
        try:
            from google.cloud import logging as cloud_logging
            client = cloud_logging.Client()
            client.setup_logging(log_level=level)
            return
        except Exception:
            pass

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s'))
    root.addHandler(sh)

    # Quiet very noisy libs
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
