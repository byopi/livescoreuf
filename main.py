"""
main.py — Punto de entrada principal.
Inicia el servidor FastAPI (health check) y luego arranca el bot de Telegram.
"""

import logging
from server import start_health_server
from bot import main as run_bot

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

if __name__ == "__main__":
    # 1. Servidor HTTP para Render health checks
    start_health_server()

    # 2. Bot de Telegram (bloquea el hilo principal con polling)
    run_bot()
