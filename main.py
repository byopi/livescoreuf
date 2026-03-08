"""
main.py — Punto de entrada principal.
Inicia el servidor de health check y luego el bot de Telegram.
"""

import asyncio
import logging
from server import start_health_server
from bot import main as run_bot

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

if __name__ == "__main__":
    # 1. Servidor HTTP para UptimeRobot / Render health checks
    start_health_server()

    # 2. Bot de Telegram — run_polling maneja su propio event loop internamente
    run_bot()
