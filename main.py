"""
main.py — Punto de entrada principal.
Crea el event loop explícitamente para compatibilidad con Python 3.12+/3.14+
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

    # 2. Crear e instalar el event loop manualmente antes de run_polling
    #    Necesario en Python 3.12+ donde ya no se autocrea en el hilo principal
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 3. Bot de Telegram
    run_bot()
