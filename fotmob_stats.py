"""
fotmob_stats.py
===============
Obtiene goleadores y asistencias en tiempo real desde FotMob.
API pública no oficial — sin autenticación, sin rate limits agresivos.

Endpoints usados:
  GET https://www.fotmob.com/api/matches?date=YYYYMMDD   → partidos del día
  GET https://www.fotmob.com/api/matchDetails?matchId=X  → eventos del partido
"""

import re
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_BASE    = "https://www.fotmob.com/api"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer":         "https://www.fotmob.com/",
    "Origin":          "https://www.fotmob.com",
}

# Cache de match_id para no buscar cada vez
_match_id_cache: dict[str, int] = {}


# ── HTTP ───────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None) -> dict | None:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, params=params, timeout=8)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                logger.warning("FotMob rate limit, esperando 15s...")
                time.sleep(15)
            else:
                logger.debug("FotMob HTTP %s: %s", r.status_code, url)
                time.sleep(2)
        except requests.RequestException as e:
            logger.debug("FotMob error (intento %d): %s", attempt + 1, e)
            time.sleep(3)
    return None


# ── Búsqueda de match_id ───────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    text = text.lower()
    for k, v in {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}.items():
        text = text.replace(k, v)
    return re.sub(r"[^a-z0-9 ]", "", text)


def find_fotmob_match_id(home_name: str, away_name: str) -> int | None:
    """
    Busca el match_id de FotMob para el partido dado.
    Consulta los partidos del día actual y busca por nombre de equipo.
    Cachea el resultado para no repetir la búsqueda.
    """
    cache_key = f"{home_name}|{away_name}"
    if cache_key in _match_id_cache:
        return _match_id_cache[cache_key]

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    data  = _get(f"{_BASE}/matches", params={"date": today})
    if not data:
        return None

    home_q = _normalize(home_name)
    away_q = _normalize(away_name)

    for league in data.get("leagues", []):
        for match in league.get("matches", []):
            h = _normalize(match.get("home", {}).get("name", ""))
            a = _normalize(match.get("away", {}).get("name", ""))
            if (home_q in h or h in home_q) and (away_q in a or a in away_q):
                match_id = match.get("id")
                if match_id:
                    logger.info("FotMob match encontrado: id=%s (%s vs %s)",
                                match_id, match["home"]["name"], match["away"]["name"])
                    _match_id_cache[cache_key] = match_id
                    return match_id

    logger.debug("FotMob: no encontrado %s vs %s", home_name, away_name)
    return None


# ── Extracción de goles ────────────────────────────────────────────────────────

def get_goal_events(match_id: int) -> list[dict]:
    """
    Devuelve la lista de goles del partido con goleador y asistencia.

    Retorna lista de dicts:
    {
        "id":      str,       # ID único del evento (para deduplicar)
        "minute":  int,
        "scorer":  str,
        "assist":  str,       # "" si no hay asistencia
        "team":    "home" | "away",
        "type":    "goal" | "own_goal" | "penalty",
    }
    """
    data = _get(f"{_BASE}/matchDetails", params={"matchId": match_id})
    if not data:
        return []

    goals = []
    content = data.get("content", {})

    # FotMob guarda los eventos en content.matchFacts.events.events
    events = (
        content
        .get("matchFacts", {})
        .get("events", {})
        .get("events", [])
    )

    for ev in events:
        ev_type = ev.get("type", "").lower()
        if ev_type not in ("goal", "owngoal", "penalty"):
            continue

        # Determinar tipo normalizado
        if ev_type == "owngoal":
            goal_type = "own_goal"
        elif ev_type == "penalty":
            goal_type = "penalty"
        else:
            goal_type = "goal"

        minute  = ev.get("timeStr", "") or str(ev.get("time", ""))
        scorer  = ev.get("player", {}).get("name", "") or "-"
        assist  = ev.get("assistStr", "") or ""
        team    = "home" if ev.get("isHome") else "away"
        ev_id   = f"fm_{match_id}_{ev.get('id', minute)}_{scorer}"

        goals.append({
            "id":     ev_id,
            "minute": minute,
            "scorer": scorer,
            "assist": assist,
            "team":   team,
            "type":   goal_type,
        })

    return goals


def get_scorer_assist(home_name: str, away_name: str,
                      match_id: int | None = None) -> list[tuple[str, str, str]]:
    """
    Función principal. Devuelve lista de (scorer, assist, event_id).
    Busca el match_id si no se pasa.

    assist puede ser "" si no hay asistencia registrada.
    """
    if match_id is None:
        match_id = find_fotmob_match_id(home_name, away_name)
    if match_id is None:
        return []

    goals = get_goal_events(match_id)
    return [(g["scorer"], g["assist"], g["id"]) for g in goals]
