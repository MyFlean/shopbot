# run.py

from __future__ import annotations

import os
from shopping_bot import create_app
from shopping_bot.utils.smart_logger import configure_logging, LogLevel

# Configure smart logging based on environment
def setup_smart_logging():
    """Configure the smart logging system"""
    # Get log level from environment or default to STANDARD
    log_level_name = os.getenv('BOT_LOG_LEVEL', 'STANDARD').upper()
    
    try:
        log_level = getattr(LogLevel, log_level_name)
    except AttributeError:
        print(f"⚠️ Invalid log level '{log_level_name}', using STANDARD")
        log_level = LogLevel.STANDARD
    
    # Configure the system
    configure_logging(
        level=log_level,
        format_string='%(asctime)s | %(message)s',
        silence_external=True
    )
    
    return log_level

app = create_app()

if __name__ == "__main__":
    # Set up smart logging
    current_level = setup_smart_logging()
    
    port = int(os.getenv("PORT", 8080))
    debug_mode = app.config.get("DEBUG", True)
    
    print(f"🚀 Starting shopping bot on port {port}")
    print(f"📊 Debug mode: {debug_mode}")
    print(f"📝 Log level: {current_level.name}")
    print("─" * 50)
    
    # Available log levels you can set via BOT_LOG_LEVEL environment variable:
    # - MINIMAL: Only critical flow events
    # - STANDARD: Key decisions and state changes (default)  
    # - DETAILED: Include data sizes and timing
    # - DEBUG: Everything including API calls
    
    app.run(host="0.0.0.0", port=port, debug=debug_mode)