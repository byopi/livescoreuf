"""
sofascore_stats.py — Cliente ligero para la API pública de Sofascore.

Devuelve las estadísticas en el mismo formato que ya usa bot.py para
pasarlas a generate_match_summary(), es decir:

    raw_stats = [
        {"statistics": [{"type": "Posesion",          "value": 56.0},
                        {"type": "xG",                "value": 1.83},
                        {"type": "Tiros totales",     "value": 12.0},
                        {"type": "Tiros a puerta",    "value": 5.0},
                        {"type": "Corners",           "value": 6.0},
                        {"type": "Tarjetas amarillas","value": 1.0},
                        {"type": "Tarjetas rojas",    "value": 0.0},
                        {"type": "Fuera de juego",    "value": 2.0}]},
        {"statistics": [...]},   # away
    ]

Uso en bot.py:
    from sofascore_stats import sofascore_raw_stats
    raw_stats = sofascore_raw_stats(home_name, away_name)
    # Si devuelve None -> usar fallback ESPN como antes
"""

import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

_BASE = "https://www.sofascore.com/api/v1"

# Claves de Sofascore → tipo que espera image_generator._parse_stats()
_STAT_MAP = {
    "Ball possession":  "Posesion",
    "Expected goals":   "xG",
    "Total shots":      "Tiros totales",
    "Shots on target":  "Tiros a puerta",
    "Corner kicks":     "Corners",
    "Yellow cards":     "Tarjetas amarillas",
    "Red cards":        "Tarjetas rojas",
    "Offsides":         "Fuera de juego",
}


# ─── HTTP ──────────────────────────────────────────────────────────────────────

def _get(url: str) -> dict | None:
    time.sleep(1.5)
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=10)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                logger.warning("Sofascore rate limit, esperando 30s...")
                time.sleep(30)
            elif r.status_code == 404:
                return None
            else:
                time.sleep(5)
        except requests.RequestException as e:
            logger.warning("Sofascore error (intento %d): %s", attempt + 1, e)
            time.sleep(5)
    return None


# ─── Búsqueda de match_id ──────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    text = text.lower()
    for k, v in {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}.items():
        text = text.replace(k, v)
    return re.sub(r"[^a-z0-9 ]", "", text)


def find_sofascore_match_id(home_name: str, away_name: str) -> int | None:
    """
    Busca el evento en vivo de Sofascore que coincida con los equipos dados.
    Devuelve el match_id o None si no lo encuentra.
    """
    data = _get(f"{_BASE}/sport/football/events/live")
    if not data:
        return None

    home_q = _normalize(home_name)
    away_q = _normalize(away_name)

    for ev in data.get("events", []):
        h = _normalize(ev.get("homeTeam", {}).get("name", ""))
        a = _normalize(ev.get("awayTeam", {}).get("name", ""))
        if (home_q in h or h in home_q) and (away_q in a or a in away_q):
            logger.info("Sofascore match: id=%s (%s vs %s)",
                        ev["id"], ev["homeTeam"]["name"], ev["awayTeam"]["name"])
            return ev["id"]

    logger.warning("Sofascore: no encontrado %s vs %s en vivo", home_name, away_name)
    return None


# ─── Parseo ────────────────────────────────────────────────────────────────────

def _parse_sofascore_stats(data: dict) -> tuple[dict, dict] | None:
    groups = data.get("statistics", [])
    target = next((g for g in groups if g.get("period", "").upper() == "ALL"), None)
    if target is None:
        target = groups[0] if groups else None
    if target is None:
        return None

    home_s: dict = {}
    away_s: dict = {}

    def to_float(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(str(v).replace("%", "").strip())
        except (ValueError, TypeError):
            return 0.0

    for group in target.get("groups", []):
        for item in group.get("statisticsItems", []):
            key = _STAT_MAP.get(item.get("name", ""))
            if key is None:
                continue
            home_s[key] = home_s.get(key, 0.0) + to_float(item.get("home"))
            away_s[key] = away_s.get(key, 0.0) + to_float(item.get("away"))

    if not home_s and not away_s:
        return None
    return home_s, away_s


# ─── API pública ───────────────────────────────────────────────────────────────

def sofascore_raw_stats(home_name: str, away_name: str,
                        match_id: int | None = None) -> list[dict] | None:
    """
    Devuelve raw_stats listo para pasar a generate_match_summary(), o None si falla.

    Args:
        home_name:  Nombre del equipo local (el que viene de ESPN).
        away_name:  Nombre del equipo visitante.
        match_id:   ID de Sofascore si ya lo tienes; si no, se busca automáticamente.

    Returns:
        [
            {"statistics": [{"type": "Posesion", "value": 56.0}, ...]},
            {"statistics": [{"type": "Posesion", "value": 44.0}, ...]},
        ]
        o None si no se pudo obtener (el bot hará fallback a ESPN).
    """
    if match_id is None:
        match_id = find_sofascore_match_id(home_name, away_name)
    if match_id is None:
        return None

    data = _get(f"{_BASE}/event/{match_id}/statistics")
    if data is None:
        return None

    result = _parse_sofascore_stats(data)
    if result is None:
        logger.warning("Sofascore: estadísticas vacías para match_id=%s", match_id)
        return None

    home_s, away_s = result

    def to_list(d: dict) -> list[dict]:
        return [{"type": k, "value": v} for k, v in d.items()]

    return [
        {"statistics": to_list(home_s)},
        {"statistics": to_list(away_s)},
    ]
