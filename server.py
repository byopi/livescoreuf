"""
server.py — Servidor HTTP minimalista para Health Check (stdlib pura).
Sin dependencias externas. Responde 200 OK a cualquier GET.
Compatible con UptimeRobot y Render.
"""

import os
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # Silenciar logs de cada request


def start_health_server():
    """Arranca el servidor HTTP en un hilo daemon."""
    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    t.start()
    logger.info("Servidor de health check iniciado en puerto %s.", port)
