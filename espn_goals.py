"""
espn_goals.py
=============
Obtiene goleadores en tiempo real desde ESPN buscando por nombre de equipo.
No requiere API key ni autenticacion.
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
    for k, v in {
        "á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n",
        "ć":"c","ą":"a","ę":"e","ź":"z","ż":"z","ł":"l","š":"s",
        "č":"c","ž":"z","ř":"r","ů":"u","ď":"d","ť":"t","ň":"n",
    }.items():
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
            if ((home_q in h_name or h_name in home_q) and
                    (away_q in a_name or a_name in away_q)):
                event_id = ev["id"]
                _match_cache[cache_key] = (event_id, slug)
                logger.info("ESPN partido encontrado: id=%s slug=%s (%s vs %s)",
                            event_id, slug, h_name, a_name)
                return event_id, slug

    logger.warning("ESPN: no encontrado '%s' vs '%s'", home_name, away_name)
    return None, ""


def _is_goal_event(ev: dict) -> bool:
    """Detecta si un keyEvent es un gol, independiente de mayusculas/formato."""
    type_text = (ev.get("type", {}).get("text") or "").lower()
    type_id   = str(ev.get("type", {}).get("id") or "")
    short     = (ev.get("shortText") or "").lower()
    text      = (ev.get("text") or "").lower()

    if "goal" in type_text:
        return True
    # IDs conocidos de gol en ESPN: 95=goal, 96=own goal, 98=penalty goal
    if type_id in ("95", "96", "98", "99"):
        return True
    # Texto del evento contiene marcador tipo "1-0", "2-1"
    if re.search(r"\b\d+[-]\d+\b", short) or re.search(r"\b\d+[-]\d+\b", text):
        return True
    return False


def _parse_goal_event(ev: dict) -> tuple[str, str]:
    scorer = assist = ""

    for ath in ev.get("athletes", []):
        role = (ath.get("type") or "").lower()
        name = ath.get("displayName") or ath.get("fullName", "")
        if role in ("scorer", "goal", "goalscorer", "athlete") and name and not scorer:
            scorer = name
        elif role in ("assist", "assister") and name:
            assist = name

    raw = ev.get("shortText") or ev.get("text", "")
    if not scorer and raw:
        if re.search(r"own goal|autogol|en propia", raw, re.I):
            scorer = "Autogol"
        else:
            # Formato ESPN: "Nombre Apellido Goal - Tipo" o "Nombre 45'"
            m = re.match(r"^(.+?)\s+Goal\b", raw, re.I)
            if m:
                scorer = m.group(1).strip()
            else:
                m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑćąęóźżłšč]+?)\s*[\(\d]", raw)
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
    """
    if seen is None:
        seen = set()

    event_id, slug = _find_espn_event(home_name, away_name)
    if not event_id:
        return []

    summary = _get(_BASE_SUMMARY.format(slug=slug), params={"event": event_id})
    if not summary:
        logger.warning("ESPN summary None para event_id=%s slug=%s", event_id, slug)
        return []

    all_key_events = summary.get("keyEvents", [])

    # Log raw completo para diagnostico
    logger.info(
        "ESPN keyEvents raw [%s vs %s] (%d eventos): %s",
        home_name, away_name, len(all_key_events),
        [
            {
                "type": ev.get("type", {}).get("text"),
                "type_id": ev.get("type", {}).get("id"),
                "shortText": ev.get("shortText"),
                "athletes": [
                    {"role": a.get("type"), "name": a.get("displayName")}
                    for a in ev.get("athletes", [])
                ],
            }
            for ev in all_key_events
        ]
    )

    key_events = [ev for ev in all_key_events if _is_goal_event(ev)]
    logger.info("ESPN goles filtrados: %d de %d keyEvents para %s vs %s",
                len(key_events), len(all_key_events), home_name, away_name)

    results = []
    for ev in key_events:
        scorer, assist = _parse_goal_event(ev)
        if not scorer:
            logger.info("ESPN gol sin scorer parseado: shortText='%s'",
                        ev.get("shortText") or ev.get("text"))
            continue
        clock = (ev.get("clock") or {}).get("displayValue", "?")
        kid   = f"espn_{event_id}_{clock}_{scorer}"
        if kid in seen:
            continue
        results.append((scorer, assist, kid))

    return results
