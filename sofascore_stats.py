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
    time.sleep(0.3)
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


# ══════════════════════════════════════════════════════════════════════════════
# LIVESCORE — Reemplaza ESPN para partidos del día y estado en vivo
# ══════════════════════════════════════════════════════════════════════════════

# IDs de torneos de Sofascore que queremos monitorear
# fmt: "Nombre legible": tournament_id
_TOURNAMENT_IDS = {
    # Italia
    "Serie A":               23,
    "Coppa Italia":          34,
    "Supercopa de Italia":  498,
    # España
    "La Liga":                8,
    "Copa del Rey":         329,
    "Supercopa de España":  480,
    # Francia
    "Ligue 1":               34,
    "Coupe de France":      152,
    "Trophée des Champions": 480,
    # Alemania
    "Bundesliga":           35,
    "DFB-Pokal":           189,
    "Supercopa de Alemania": 440,
    # Inglaterra
    "Premier League":       17,
    "FA Cup":              130,
    "EFL Cup":             132,
    "Community Shield":    480,
    # UEFA
    "Champions League":      7,
    "Europa League":       679,
    "Conference League":   17015,
    "Supercopa de Europa":  480,
    # CONMEBOL
    "Copa Libertadores":    384,
    "Copa Sudamericana":    480,
    "Recopa Sudamericana":  480,
    # Selecciones
    "Nations League":       35,
    "Eurocopa":            1,
    "Copa América":       133,
    "Clasificación UEFA":   679,
    "Clasificación CONMEBOL": 42,
    "Clasificación CONCACAF": 481,
    "Mundial 2026":          16,
    "Mundial de Clubes":  17626,
}

# Mapa estado Sofascore → nombres usados en bot.py
_STATUS_MAP = {
    "notstarted":  "STATUS_SCHEDULED",
    "inprogress":  "STATUS_IN_PROGRESS",
    "halftime":    "STATUS_HALFTIME",
    "finished":    "STATUS_FULL_TIME",
    "postponed":   "STATUS_POSTPONED",
    "canceled":    "STATUS_CANCELED",
    "awarded":     "STATUS_FINAL",
}


def get_events_by_date(date_str: str) -> list[dict]:
    """
    Devuelve todos los eventos de fútbol de una fecha dada.
    date_str: "YYYY-MM-DD"
    Cada evento tiene el mismo formato que parse_event() espera de ESPN.
    """
    data = _get(f"{_BASE}/sport/football/scheduled-events/{date_str}")
    if not data:
        return []
    return [_normalize_event(ev) for ev in data.get("events", [])]


def get_live_events() -> list[dict]:
    """Devuelve los partidos de fútbol en vivo ahora mismo."""
    data = _get(f"{_BASE}/sport/football/events/live")
    if not data:
        return []
    return [_normalize_event(ev) for ev in data.get("events", [])]


def get_event_by_id(event_id: int | str) -> dict | None:
    """Devuelve el estado actual de un evento específico."""
    data = _get(f"{_BASE}/event/{event_id}")
    if not data:
        return None
    ev = data.get("event")
    if not ev:
        return None
    return _normalize_event(ev)


def _normalize_event(ev: dict) -> dict:
    """
    Convierte un evento de Sofascore al formato que usa bot.py
    (compatible con parse_event de ESPN).
    """
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    status = ev.get("status", {})
    score  = ev.get("homeScore", {})
    ascore = ev.get("awayScore", {})
    tournament = ev.get("tournament", {})
    category   = tournament.get("category", {})

    st_type  = status.get("type", "notstarted").lower()
    st_mapped = _STATUS_MAP.get(st_type, "STATUS_SCHEDULED")

    # Minuto mostrado
    clock = ""
    time_data = ev.get("time", {})
    played = time_data.get("played")
    if played is not None:
        clock = str(played)
    elif st_type == "halftime":
        clock = "HT"

    # Hora de inicio (ISO)
    start_ts = ev.get("startTimestamp")
    date_iso = ""
    kickoff_utc = None
    if start_ts:
        from datetime import datetime, timezone
        kickoff_utc = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        date_iso = kickoff_utc.isoformat()

    # Logo URLs de Sofascore
    home_id = home.get("id", "")
    away_id = away.get("id", "")
    home_logo = f"https://api.sofascore.app/api/v1/team/{home_id}/image" if home_id else ""
    away_logo = f"https://api.sofascore.app/api/v1/team/{away_id}/image" if away_id else ""

    # Nombre de liga
    league_name = tournament.get("name", "")
    country = category.get("name", "")

    return {
        # Campos que usa bot.py directamente
        "id":          str(ev.get("id", "")),
        "date":        date_iso,
        "kickoff_utc": kickoff_utc,
        "_sofascore_id": ev.get("id"),
        "_slug":       "",          # no aplica en Sofascore, se deja vacío
        "_league":     league_name,
        "_country":    country,
        # Compatibilidad con parse_event de ESPN
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "score": str(score.get("current", 0) or 0),
                    "team": {
                        "displayName": home.get("name", "?"),
                        "logo": home_logo,
                    },
                },
                {
                    "homeAway": "away",
                    "score": str(ascore.get("current", 0) or 0),
                    "team": {
                        "displayName": away.get("name", "?"),
                        "logo": away_logo,
                    },
                },
            ],
            "status": {
                "type": {
                    "name":        st_mapped,
                    "description": st_type.capitalize(),
                },
                "displayClock": clock,
            },
        }],
    }
