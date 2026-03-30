"""
main.py — Punto de entrada principal.
"""

import logging
from server import start_health_server
from bot import main as run_bot

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

if __name__ == "__main__":
    # Servidor HTTP en hilo daemon para UptimeRobot / Render health checks
    start_health_server()
    # Bot — run_polling() maneja su propio event loop internamente
    run_bot()
