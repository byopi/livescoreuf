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


# ══════════════════════════════════════════════════════════════════════════════
# LIVESCORE — Estado del partido en vivo desde FotMob
# ══════════════════════════════════════════════════════════════════════════════

# Mapa estado FotMob → nombres usados en bot.py
_FOTMOB_STATUS_MAP = {
    "notstarted":  "STATUS_SCHEDULED",
    "ongoing":     "STATUS_IN_PROGRESS",
    "halftime":    "STATUS_HALFTIME",
    "finished":    "STATUS_FULL_TIME",
    "cancelled":   "STATUS_CANCELED",
    "postponed":   "STATUS_POSTPONED",
}


def get_fotmob_livescore(home_name: str, away_name: str,
                         match_id: int | None = None) -> dict | None:
    """
    Devuelve el estado actual del partido en formato compatible con parse_event()
    de bot.py, o None si no se encuentra.

    Campos devueltos:
    {
        "id":       str,
        "_slug":    "",
        "_league":  str,
        "date":     str (ISO),
        "competitions": [{
            "competitors": [{homeAway, score, team}...],
            "status": {type: {name, description}, displayClock}
        }]
    }
    """
    if match_id is None:
        match_id = find_fotmob_match_id(home_name, away_name)
    if match_id is None:
        return None

    data = _get(f"{_BASE}/matchDetails", params={"matchId": match_id})
    if not data:
        return None

    general = data.get("general", {})
    home_team = general.get("homeTeam", {})
    away_team = general.get("awayTeam", {})

    # Marcador
    home_score = str(general.get("homeScore", {}).get("current", 0) or 0)
    away_score = str(general.get("awayScore", {}).get("current", 0) or 0)

    # Estado y minuto
    status_raw = (general.get("status", {}).get("liveTime", {}).get("short") or
                  general.get("matchStatusId") or "notstarted")
    if isinstance(status_raw, int):
        status_raw = str(status_raw)
    status_raw = status_raw.lower()

    # Minuto mostrado
    clock = general.get("status", {}).get("liveTime", {}).get("long", "") or ""

    # Mapear estado
    if "halftime" in status_raw or status_raw == "ht":
        status_mapped = "STATUS_HALFTIME"
        clock = "HT"
    elif "finished" in status_raw or "ft" in status_raw or status_raw == "fulltime":
        status_mapped = "STATUS_FULL_TIME"
        clock = ""
    elif any(x in status_raw for x in ("ongoing", "live", "inprogress")):
        status_mapped = "STATUS_IN_PROGRESS"
    elif status_raw.isdigit():
        status_mapped = "STATUS_IN_PROGRESS"
        clock = status_raw + "'"
    else:
        status_mapped = _FOTMOB_STATUS_MAP.get(status_raw, "STATUS_SCHEDULED")

    # Logos
    home_logo = f"https://images.fotmob.com/image_resources/logo/teamlogo/{home_team.get('id', '')}.png"
    away_logo = f"https://images.fotmob.com/image_resources/logo/teamlogo/{away_team.get('id', '')}.png"

    league_name = general.get("leagueName", "")
    match_start = general.get("matchTimeUTCDate", "")

    return {
        "id":          str(match_id),
        "_slug":       "",
        "_league":     league_name,
        "date":        match_start,
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "score":    home_score,
                    "team": {
                        "displayName": home_team.get("name", home_name),
                        "logo":        home_logo,
                    },
                },
                {
                    "homeAway": "away",
                    "score":    away_score,
                    "team": {
                        "displayName": away_team.get("name", away_name),
                        "logo":        away_logo,
                    },
                },
            ],
            "status": {
                "type": {
                    "name":        status_mapped,
                    "description": status_raw,
                },
                "displayClock": clock,
            },
        }],
    }


# ══════════════════════════════════════════════════════════════════════════════
# STATS PARA IMAGEN FINAL — Mismo formato que sofascore_raw_stats()
# ══════════════════════════════════════════════════════════════════════════════

def fotmob_raw_stats(home_name: str, away_name: str,
                     match_id: int | None = None) -> list[dict] | None:
    """
    Devuelve estadísticas del partido en el mismo formato que sofascore_raw_stats(),
    listo para pasar a generate_match_summary(). Devuelve None si falla.

    Formato:
    [
        {"statistics": [{"type": "Posesion", "value": 56.0}, ...]},  # home
        {"statistics": [{"type": "Posesion", "value": 44.0}, ...]},  # away
    ]
    """
    if match_id is None:
        match_id = find_fotmob_match_id(home_name, away_name)
    if match_id is None:
        return None

    data = _get(f"{_BASE}/matchDetails", params={"matchId": match_id})
    if not data:
        return None

    # FotMob guarda las stats en content.stats.stats (lista de grupos)
    stat_groups = (
        data.get("content", {})
            .get("stats", {})
            .get("stats", [])
    )
    if not stat_groups:
        return None

    # Mapa de claves FotMob -> tipo que espera image_generator
    STAT_MAP = {
        "Ball possession":        "Posesion",
        "Possession":             "Posesion",
        "Expected goals (xG)":   "xG",
        "xG":                    "xG",
        "Total shots":            "Tiros totales",
        "Shots":                  "Tiros totales",
        "Shots on target":        "Tiros a puerta",
        "Corner kicks":           "Corners",
        "Corners":                "Corners",
        "Yellow cards":           "Tarjetas amarillas",
        "Red cards":              "Tarjetas rojas",
        "Offsides":               "Fuera de juego",
    }

    home_s: dict[str, float] = {}
    away_s: dict[str, float] = {}

    def to_float(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(str(v).replace("%", "").strip())
        except (ValueError, TypeError):
            return 0.0

    for group in stat_groups:
        for item in group.get("items", []):
            title = item.get("title", "")
            key = STAT_MAP.get(title)
            if not key:
                continue
            stats = item.get("stats", [])
            # stats es [home_value, away_value] o {"home": x, "away": y}
            if isinstance(stats, list) and len(stats) >= 2:
                home_s[key] = home_s.get(key, 0.0) + to_float(stats[0])
                away_s[key] = away_s.get(key, 0.0) + to_float(stats[1])
            elif isinstance(stats, dict):
                home_s[key] = home_s.get(key, 0.0) + to_float(stats.get("home"))
                away_s[key] = away_s.get(key, 0.0) + to_float(stats.get("away"))

    if not home_s and not away_s:
        logger.warning("FotMob: stats vacías para match_id=%s", match_id)
        return None

    def to_list(d: dict) -> list[dict]:
        return [{"type": k, "value": v} for k, v in d.items()]

    return [
        {"statistics": to_list(home_s)},
        {"statistics": to_list(away_s)},
    ]
