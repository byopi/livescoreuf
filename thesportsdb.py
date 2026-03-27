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
# SE HAN AGREGADO KEYWORDS PARA ELIMINATORIAS Y PARTIDOS INTERNACIONALES
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
    # ── Selecciones y Clasificatorias (Corrección para hoy) ──────────────────
    "international friendly", "friendly international", "amistoso",
    "world cup qualification", "world cup qualifier", "eliminatorias",
    "uefa qualifiers", "conmebol", "concacaf", "afc", "caf", "uefa",
    "euro 2026", "euro qualification",
]

def _match_league(name: str) -> (bool, str):
    """Retorna (True, slug) si el nombre de la liga coincide con alguna keyword."""
    if not name: return False, ""
    n = name.lower()
    for kw in _LEAGUE_KEYWORDS:
        if kw in n:
            return True, kw
    return False, ""

def get_tsdb_today(date_str: str) -> list:
    """
    date_str: "YYYY-MM-DD"
    Retorna lista de partidos en el formato 'fake-ESPN' que espera bot.py
    """
    url = f"{_BASE}/eventsday.php"
    params = {"d": date_str, "s": "Soccer"}
    
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Error en TheSportsDB: %s", e)
        return []

    events = data.get("events")
    if not events:
        logger.info("TheSportsDB: No hay eventos para la fecha %s", date_str)
        return []

    results = []
    for ev in events:
        league_name = ev.get("strLeague", "")
        
        # Filtro de ligas configuradas
        matched, _ = _match_league(league_name)
        if not matched:
            continue

        home_name = ev.get("strHomeTeam", "?")
        away_name = ev.get("strAwayTeam", "?")
        
        # Formatear el kickoff para que bot.py lo entienda
        # TheSportsDB da 'strTimestamp' en formato ISO UTC
        kickoff_utc = ev.get("strTimestamp", "")
        
        # Status mapping
        status_raw = ev.get("strStatus", "").lower()
        status_mapped = "STATUS_SCHEDULED"
        if "live" in status_raw or "half" in status_raw:
            status_mapped = "STATUS_IN_PROGRESS"
        elif "final" in status_raw or "finished" in status_raw:
            status_mapped = "STATUS_FULL_TIME"

        home_score = ev.get("intHomeScore")
        away_score = ev.get("intAwayScore")
        
        # Minuto del partido (si está en vivo)
        clock = ev.get("strProgress", "")

        results.append({
            "id":          str(ev.get("idEvent", "")),
            "date":        date_str,
            "kickoff_utc": kickoff_utc,
            "_league":     league_name,
            "_tsdb":       True,
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
                        "description": status_raw.capitalize(),
                    },
                    "displayClock": clock,
                },
            }],
        })

    logger.info("TheSportsDB total: %d partidos de ligas monitoreadas para %s", len(results), date_str)
    return results
