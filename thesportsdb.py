"""
thesportsdb.py
==============
Obtiene partidos del dia desde TheSportsDB (gratis, sin auth, sin bloqueos).
Endpoint: /api/v1/json/123/eventsday.php?d=YYYY-MM-DD&s=Soccer

Se usa solo para LISTAR partidos en /partidos.
El livescore y goleadores siguen usando ESPN.
"""

import re
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_BASE    = "https://www.thesportsdb.com/api/v1/json/123"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept": "application/json",
}

# Palabras clave para filtrar ligas (se buscan dentro del nombre normalizado)
_LEAGUE_KEYWORDS = [
    "serie a", "coppa italia",
    "la liga", "laliga", "copa del rey",
    "ligue 1", "coupe de france",
    "bundesliga", "dfb pokal", "dfb-pokal",
    "premier league", "fa cup", "efl cup", "league cup",
    "champions league", "europa league", "conference league",
    "libertadores", "sudamericana", "recopa sudamericana",
    "nations league", "copa america", "copa américa",
    "world cup", "club world cup",
    # ── Amistosos ──────────────────────────────────────────────────────────
    "international friendly", "friendly international",
    "international friendlies", "friendly match",
    "amistoso internacional", "amistosos internacionales",
    "club friendly", "club friendlies",
    "international champions cup", "fifa series",
    "audi cup", "emirates cup", "florida cup",
    "preseason friendly", "pre-season friendly",
    "sb-cup", "intercontinental cup",
]

# Mapa: palabra clave encontrada -> slug ESPN
_KEYWORD_TO_ESPN_SLUG = {
    "serie a":            "ita.1",
    "coppa italia":       "ita.coppa_italia",
    "la liga":            "esp.1",
    "laliga":             "esp.1",
    "copa del rey":       "esp.copa_del_rey",
    "ligue 1":            "fra.1",
    "coupe de france":    "fra.coupe_de_france",
    "bundesliga":         "ger.1",
    "dfb pokal":          "ger.dfb_pokal",
    "dfb-pokal":          "ger.dfb_pokal",
    "premier league":     "eng.1",
    "fa cup":             "eng.fa",
    "efl cup":            "eng.league_cup",
    "league cup":         "eng.league_cup",
    "champions league":   "uefa.champions",
    "europa league":      "uefa.europa",
    "conference league":  "uefa.europa.conf",
    "libertadores":       "conmebol.libertadores",
    "sudamericana":       "conmebol.sudamericana",
    "recopa sudamericana":"conmebol.recopa",
    "nations league":     "uefa.nations",
    "copa america":       "conmebol.america",
    "copa américa":       "conmebol.america",
    "club world cup":     "fifa.cwc",
    "world cup":          "fifa.world",
    # ── Amistosos ──────────────────────────────────────────────────────────
    "international friendly":    "fifa.friendly",
    "friendly international":    "fifa.friendly",
    "international friendlies":  "fifa.friendly",
    "friendly match":            "fifa.friendly",
    "amistoso internacional":    "fifa.friendly",
    "amistosos internacionales": "fifa.friendly",
    "fifa series":               "fifa.friendly",
    "club friendly":             "club.friendly",
    "club friendlies":           "club.friendly",
    "international champions cup": "club.friendly",
    "audi cup":                  "club.friendly",
    "emirates cup":              "club.friendly",
    "florida cup":               "club.friendly",
    "preseason friendly":        "club.friendly",
    "pre-season friendly":       "club.friendly",
    "sb-cup":                    "club.friendly",
    "intercontinental cup":      "club.friendly",
}


def _match_league(league_name: str) -> tuple[bool, str]:
    """
    Devuelve (coincide, slug_espn) buscando palabras clave dentro del nombre.
    Ej: "Italian Serie A" -> coincide con "serie a" -> "ita.1"
    """
    norm = _norm(league_name)
    # Ordenar por longitud desc para que "club world cup" matchee antes que "world cup"
    for kw in sorted(_KEYWORD_TO_ESPN_SLUG, key=len, reverse=True):
        if kw in norm:
            return True, _KEYWORD_TO_ESPN_SLUG[kw]
    return False, ""


def _get(url: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(url, headers=_HEADERS, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        logger.debug("TheSportsDB HTTP %s: %s", r.status_code, url)
    except Exception as exc:
        logger.debug("TheSportsDB error: %s", exc)
    return None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


# Selecciones y clubes relevantes para filtrar amistosos sin interés.
# Si es un amistoso y ninguno de los equipos aparece aquí, se descarta.
_IMPORTANT_TEAMS = {
    # Selecciones top
    "argentina", "brazil", "brasil", "france", "england", "germany",
    "spain", "espana", "portugal", "netherlands", "holland", "italy", "italia",
    "belgium", "croatia", "uruguay", "colombia", "chile", "mexico", "usa",
    "united states", "senegal", "morocco", "japan", "south korea", "australia",
    "denmark", "austria", "switzerland", "poland", "serbia", "turkey", "nigeria",
    "ghana", "ecuador", "peru", "venezuela", "paraguay", "bolivia",
    # Clubes europeos top
    "real madrid", "barcelona", "atletico madrid", "atletico de madrid",
    "manchester city", "manchester united", "liverpool", "chelsea", "arsenal",
    "tottenham", "newcastle",
    "bayern munich", "borussia dortmund", "bayer leverkusen", "rb leipzig",
    "paris saint-germain", "psg", "olympique marseille", "marseille",
    "juventus", "inter", "inter milan", "ac milan", "napoli", "roma", "lazio",
    "ajax", "psv", "feyenoord",
    "porto", "benfica", "sporting cp", "sporting lisbon",
    "celtic", "rangers",
    # Clubes CONMEBOL top
    "boca juniors", "river plate", "flamengo", "palmeiras", "atletico mineiro",
    "fluminense", "sao paulo", "gremio", "corinthians", "santos",
    "nacional", "penarol", "colo-colo", "universidad de chile",
    "estudiantes", "independiente", "racing club", "san lorenzo",
    "america", "club america", "chivas", "guadalajara", "tigres",
    "olimpia", "libertad", "cerro porteno",
}


def _is_important_friendly(home_name: str, away_name: str) -> bool:
    """
    Para amistosos: True si al menos un equipo está en _IMPORTANT_TEAMS.
    Para partidos de liga oficial siempre retornar True directamente.
    """
    h = _norm(home_name)
    a = _norm(away_name)
    for team in _IMPORTANT_TEAMS:
        if team in h or team in a:
            return True
    return False


def get_events_today(tz_offset: int = -4) -> list[dict]:
    """
    Devuelve partidos de hoy y mañana desde TheSportsDB.
    tz_offset: offset de la zona horaria local (por defecto UTC-4).

    Cada evento tiene formato compatible con parse_event() de bot.py:
    {
        "id":       str,   # TheSportsDB event ID (se usa para tracking)
        "_slug":    str,   # slug ESPN para el livescore
        "_league":  str,   # nombre de la liga
        "date":     str,   # ISO datetime
        "competitions": [...],  # formato ESPN
    }
    """
    now_utc = datetime.now(timezone.utc)
    local_tz = timezone(timedelta(hours=tz_offset))
    now_local = now_utc.astimezone(local_tz)

    dates_to_check = {
        now_utc.strftime("%Y-%m-%d"),
        now_local.strftime("%Y-%m-%d"),
        (now_utc + timedelta(days=1)).strftime("%Y-%m-%d"),
    }

    results = []
    seen = set()

    for date_str in sorted(dates_to_check):
        data = _get(f"{_BASE}/eventsday.php", params={"d": date_str, "s": "Soccer"})
        if not data:
            continue
        events = data.get("events") or []
        logger.info("TheSportsDB %s: %d eventos de fútbol", date_str, len(events))

        for ev in events:
            ev_id = str(ev.get("idEvent", ""))
            if not ev_id or ev_id in seen:
                continue

            league_name = ev.get("strLeague", "")
            matched, slug = _match_league(league_name)
            if not matched:
                continue

            seen.add(ev_id)

            home_name  = ev.get("strHomeTeam", "?")
            away_name  = ev.get("strAwayTeam", "?")

            # Para amistosos filtrar solo equipos relevantes
            if slug in ("fifa.friendly", "club.friendly"):
                if not _is_important_friendly(home_name, away_name):
                    seen.discard(ev_id)
                    continue
            home_score = ev.get("intHomeScore")
            away_score = ev.get("intAwayScore")
            date_event = ev.get("dateEvent", "")
            time_event = ev.get("strTime", "00:00:00") or "00:00:00"
            status_raw = (ev.get("strStatus") or "NS").upper()

            # Construir ISO datetime
            date_iso = ""
            kickoff_utc = None
            try:
                dt_str = f"{date_event}T{time_event}"
                kickoff_utc = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
                date_iso = kickoff_utc.isoformat()
            except Exception:
                date_iso = f"{date_event}T{time_event}Z"

            # Mapear status
            if status_raw in ("FT", "AET", "PEN"):
                status_mapped = "STATUS_FINAL"
            elif status_raw in ("HT",):
                status_mapped = "STATUS_HALFTIME"
            elif status_raw == "NS":
                status_mapped = "STATUS_SCHEDULED"
            else:
                # Puede ser "45", "90", etc → en progreso
                status_mapped = "STATUS_IN_PROGRESS" if status_raw.isdigit() else "STATUS_SCHEDULED"

            clock = status_raw if status_raw not in ("NS", "FT") else ""

            # Slug ESPN ya viene de _match_league

            results.append({
                "id":          ev_id,
                "date":        date_iso,
                "kickoff_utc": kickoff_utc,
                "_slug":       slug,
                "_league":     league_name,
                "_tsdb":       True,   # marca para distinguir de ESPN
                "competitions": [{
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": str(home_score) if home_score is not None else "0",
                            "team": {
                                "displayName": home_name,
                                "logo": f"https://www.thesportsdb.com/images/media/team/badge/{ev.get('idHomeTeam','')}.jpg",
                            },
                        },
                        {
                            "homeAway": "away",
                            "score": str(away_score) if away_score is not None else "0",
                            "team": {
                                "displayName": away_name,
                                "logo": f"https://www.thesportsdb.com/images/media/team/badge/{ev.get('idAwayTeam','')}.jpg",
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
            })

    logger.info("TheSportsDB total: %d partidos de ligas monitoreadas", len(results))
    return results
