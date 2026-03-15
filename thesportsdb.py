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

# Ligas que nos interesan (nombre normalizado -> nombre display)
# TheSportsDB devuelve strLeague, filtramos solo estas
_LEAGUES_WANTED = {
    "serie a",
    "coppa italia",
    "la liga",
    "laliga",
    "copa del rey",
    "ligue 1",
    "coupe de france",
    "bundesliga",
    "dfb pokal",
    "dfb-pokal",
    "premier league",
    "fa cup",
    "efl cup",
    "league cup",
    "champions league",
    "uefa champions league",
    "europa league",
    "uefa europa league",
    "conference league",
    "uefa europa conference league",
    "copa libertadores",
    "copa sudamericana",
    "nations league",
    "uefa nations league",
    "copa america",
    "copa américa",
    "world cup",
    "fifa world cup",
    "club world cup",
    "fifa club world cup",
    "recopa sudamericana",
}

# Mapa TheSportsDB strLeague -> slug ESPN (para el livescore)
_LEAGUE_TO_ESPN_SLUG = {
    "Serie A":                        "ita.1",
    "Coppa Italia":                   "ita.coppa_italia",
    "La Liga":                        "esp.1",
    "LaLiga":                         "esp.1",
    "Copa del Rey":                   "esp.copa_del_rey",
    "Ligue 1":                        "fra.1",
    "Coupe de France":                "fra.coupe_de_france",
    "Bundesliga":                     "ger.1",
    "DFB Pokal":                      "ger.dfb_pokal",
    "DFB-Pokal":                      "ger.dfb_pokal",
    "Premier League":                 "eng.1",
    "FA Cup":                         "eng.fa",
    "EFL Cup":                        "eng.league_cup",
    "League Cup":                     "eng.league_cup",
    "Champions League":               "uefa.champions",
    "UEFA Champions League":          "uefa.champions",
    "Europa League":                  "uefa.europa",
    "UEFA Europa League":             "uefa.europa",
    "Conference League":              "uefa.europa.conf",
    "UEFA Europa Conference League":  "uefa.europa.conf",
    "Copa Libertadores":              "conmebol.libertadores",
    "Copa Sudamericana":              "conmebol.sudamericana",
    "Nations League":                 "uefa.nations",
    "UEFA Nations League":            "uefa.nations",
    "Copa America":                   "conmebol.america",
    "Copa América":                   "conmebol.america",
    "FIFA Club World Cup":            "fifa.cwc",
    "Club World Cup":                 "fifa.cwc",
    "FIFA World Cup":                 "fifa.world",
    "Recopa Sudamericana":            "conmebol.recopa",
}


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
            if _norm(league_name) not in _LEAGUES_WANTED:
                continue

            seen.add(ev_id)

            home_name  = ev.get("strHomeTeam", "?")
            away_name  = ev.get("strAwayTeam", "?")
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

            # Slug ESPN
            slug = _LEAGUE_TO_ESPN_SLUG.get(league_name, "")

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
