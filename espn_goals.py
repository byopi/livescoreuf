"""
espn_goals.py
=============
Obtiene goleadores en tiempo real desde ESPN buscando por nombre de equipo.
No requiere API key. Funciona siempre que ESPN tenga el partido cubierto.

Uso:
    from espn_goals import get_espn_scorer

    results = get_espn_scorer("Fiorentina", "Rakow Czestochowa")
    for scorer, assist, kid in results:
        print(scorer, assist, kid)
"""

import re
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
_BASE_SUMMARY    = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept": "application/json",
}

# Slugs en orden de probabilidad (ligas donde más partidos hay)
_SLUGS = [
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "esp.1", "eng.1", "ger.1", "ita.1", "fra.1",
    "conmebol.libertadores", "conmebol.sudamericana",
    "esp.copa_del_rey", "eng.fa", "eng.league_cup",
    "ger.dfb_pokal", "ita.coppa_italia", "fra.coupe_de_france",
    "uefa.nations", "conmebol.america",
    "conmebol.worldq", "uefa.worldq", "concacaf.worldq",
    "afc.worldq", "caf.worldq", "fifa.cwc", "fifa.world",
]

# Cache: "home|away" -> (espn_event_id, slug)
_match_cache: dict[str, tuple[str, str]] = {}


def _norm(text: str) -> str:
    text = text.lower()
    for k, v in {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n","ć":"c","ą":"a","ę":"e","ó":"o","ź":"z","ż":"z","ł":"l"}.items():
        text = text.replace(k, v)
    return re.sub(r"[^a-z0-9 ]", "", text).strip()


def _get(url: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(url, headers=_HEADERS, params=params, timeout=8)
        if r.status_code == 200:
            return r.json()
        logger.debug("ESPN HTTP %s: %s", r.status_code, url)
    except Exception as exc:
        logger.debug("ESPN request error: %s", exc)
    return None


def _find_espn_event(home_name: str, away_name: str) -> tuple[Optional[str], str]:
    """
    Busca el evento ESPN por nombre de equipo en todos los slugs.
    Devuelve (event_id, slug) o (None, "").
    """
    cache_key = f"{home_name}|{away_name}"
    if cache_key in _match_cache:
        return _match_cache[cache_key]

    home_q = _norm(home_name)
    away_q = _norm(away_name)

    for slug in _SLUGS:
        data = _get(_BASE_SCOREBOARD.format(slug=slug))
        if not data:
            continue
        for ev in data.get("events", []):
            comps = ev.get("competitions", [{}])[0].get("competitors", [])
            h = next((c for c in comps if c.get("homeAway") == "home"), {})
            a = next((c for c in comps if c.get("homeAway") == "away"), {})
            h_name = _norm(h.get("team", {}).get("displayName", ""))
            a_name = _norm(a.get("team", {}).get("displayName", ""))
            # Match flexible: uno contiene al otro
            if ((home_q in h_name or h_name in home_q) and
                    (away_q in a_name or a_name in away_q)):
                event_id = ev["id"]
                _match_cache[cache_key] = (event_id, slug)
                logger.info("ESPN partido encontrado: id=%s slug=%s (%s vs %s)",
                            event_id, slug, h_name, a_name)
                return event_id, slug

    logger.warning("ESPN: no encontrado %s vs %s", home_name, away_name)
    return None, ""


def _parse_goal_event(ev: dict) -> tuple[str, str]:
    """Extrae scorer y assist de un keyEvent de ESPN."""
    scorer = assist = ""

    for ath in ev.get("athletes", []):
        role = (ath.get("type") or "").lower()
        name = ath.get("displayName") or ath.get("fullName", "")
        if role in ("scorer", "goal", "goalscorer") and name:
            scorer = name
        elif role in ("assist", "assister") and name:
            assist = name

    # Fallback: leer del texto del evento
    raw = ev.get("shortText") or ev.get("text", "")
    if not scorer and raw:
        if re.search(r"own goal|autogol|en propia", raw, re.I):
            scorer = "Autogol"
        else:
            m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑćąęóźżł]+?)\s+\d+[''']", raw)
            if m:
                scorer = m.group(1).strip()

    return scorer or "", assist or ""


def get_espn_scorer(
    home_name: str,
    away_name: str,
    seen: set = None,
) -> list[tuple[str, str, str]]:
    """
    Devuelve lista de (scorer, assist, kid) para todos los goles
    que ESPN tenga registrados en el partido.

    'seen' es un set opcional de kids ya procesados (para deduplicar).
    """
    if seen is None:
        seen = set()

    event_id, slug = _find_espn_event(home_name, away_name)
    if not event_id:
        return []

    summary = _get(_BASE_SUMMARY.format(slug=slug), params={"event": event_id})
    if not summary:
        logger.debug("ESPN summary None para event_id=%s", event_id)
        return []

    key_events = [
        ev for ev in summary.get("keyEvents", [])
        if "goal" in (ev.get("type", {}).get("text") or "").lower()
    ]
    logger.debug("ESPN keyEvents para %s vs %s: %d eventos",
                 home_name, away_name, len(key_events))

    results = []
    for ev in key_events:
        scorer, assist = _parse_goal_event(ev)
        if not scorer:
            continue
        clock = (ev.get("clock") or {}).get("displayValue", "?")
        kid   = f"espn_{event_id}_{clock}_{scorer}"
        if kid in seen:
            continue
        results.append((scorer, assist, kid))

    return results
