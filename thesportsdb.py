import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_BASE = "https://www.thesportsdb.com/api/v1/json/123"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

_LEAGUE_KEYWORDS = [
    "serie a", "la liga", "laliga", "ligue 1", "bundesliga", "premier league",
    "champions league", "europa league", "conference league", "libertadores",
    "nations league", "copa america", "world cup", "qualification", "qualifier",
    "eliminatorias", "fifa", "friendly", "amistoso", "conmebol", "uefa"
]

def get_events_today(tz_offset: int = -4) -> list:
    """Obtiene eventos de hoy y los formatea para bot.py"""
    now_local = datetime.now(timezone.utc) + timedelta(hours=tz_offset)
    date_str = now_local.strftime("%Y-%m-%d")
    
    url = f"{_BASE}/eventsday.php"
    params = {"d": date_str, "s": "Soccer"}
    
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error(f"Error TSDB: {e}")
        return []

    events = data.get("events")
    if not events: return []

    results = []
    for ev in events:
        league = ev.get("strLeague", "")
        if not any(kw in league.lower() for kw in _LEAGUE_KEYWORDS):
            continue

        # Convertir el timestamp de texto a objeto datetime real
        raw_ts = ev.get("strTimestamp", "")
        kickoff_obj = None
        if raw_ts:
            try:
                # TSDB suele enviar "YYYY-MM-DD HH:MM:SS" o ISO
                kickoff_obj = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except:
                kickoff_obj = None

        results.append({
            "id": str(ev.get("idEvent", "")),
            "kickoff_utc": kickoff_obj, # Ahora es un objeto, no un texto
            "_league": league,
            "_tsdb": True,
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "score": ev.get("intHomeScore") or "0",
                     "team": {"displayName": ev.get("strHomeTeam"), "logo": ""}},
                    {"homeAway": "away", "score": ev.get("intAwayScore") or "0",
                     "team": {"displayName": ev.get("strAwayTeam"), "logo": ""}}
                ],
                "status": {
                    "type": {"name": "STATUS_SCHEDULED", "description": ev.get("strStatus")},
                    "displayClock": ev.get("strProgress", "")
                }
            }]
        })
    return results
