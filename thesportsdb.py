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

# KEYWORDS EXPANDIDAS PARA DETECTAR ELIMINATORIAS 2026
_LEAGUE_KEYWORDS = [
    "serie a", "coppa italia", "la liga", "laliga", "copa del rey",
    "ligue 1", "coupe de france", "bundesliga", "dfb pokal",
    "premier league", "fa cup", "efl cup", "champions league", 
    "europa league", "conference league", "libertadores", "sudamericana",
    "nations league", "copa america", "world cup",
    # --- Agregados para hoy (Clasificatorias y Amistosos) ---
    "qualification", "qualifier", "eliminatorias", "fifa", "friendly", 
    "amistoso", "conmebol", "uefa", "concacaf", "afc", "caf"
]

def _match_league(name: str) -> (bool, str):
    if not name: return False, ""
    n = name.lower()
    for kw in _LEAGUE_KEYWORDS:
        if kw in n:
            return True, kw
    return False, ""

def get_tsdb_today(date_str: str) -> list:
    """ date_str: 'YYYY-MM-DD' """
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
        return []

    results = []
    for ev in events:
        league_name = ev.get("strLeague", "")
        matched, _ = _match_league(league_name)
        
        if not matched: continue

        home_name = ev.get("strHomeTeam", "?")
        away_name = ev.get("strAwayTeam", "?")
        kickoff_utc = ev.get("strTimestamp", "")
        status_raw = ev.get("strStatus", "").lower()
        
        status_mapped = "STATUS_SCHEDULED"
        if any(x in status_raw for x in ["live", "half", "progress"]):
            status_mapped = "STATUS_IN_PROGRESS"
        elif any(x in status_raw for x in ["final", "finished"]):
            status_mapped = "STATUS_FULL_TIME"

        results.append({
            "id": str(ev.get("idEvent", "")),
            "date": date_str,
            "kickoff_utc": kickoff_utc,
            "_league": league_name,
            "_tsdb": True,
            "competitions": [{
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": str(ev.get("intHomeScore") or "0"),
                        "team": {"displayName": home_name, "logo": ""}
                    },
                    {
                        "homeAway": "away",
                        "score": str(ev.get("intAwayScore") or "0"),
                        "team": {"displayName": away_name, "logo": ""}
                    }
                ],
                "status": {
                    "type": {"name": status_mapped, "description": status_raw.capitalize()},
                    "displayClock": ev.get("strProgress", "")
                }
            }]
        })
    return results
