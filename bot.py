"""
Livescore Bot — Universo Football
ESPN unofficial API · python-telegram-bot v21+ · Python 3.12+
"""

import os
import re
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

import requests
from sofascore_stats import (
    sofascore_raw_stats, find_sofascore_match_id,
    get_events_by_date, get_live_events, get_event_by_id,
    _get as sofascore_get,
)
from lineup_image_generator import generate_lineup_images
from standings_image_generator import generate_standings_image
from fotmob_stats import get_scorer_assist, find_fotmob_match_id
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import BadRequest
from telegram import LinkPreviewOptions
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Zona horaria ──────────────────────────────────────────────────────────────
TZ = timezone(timedelta(hours=-4))   # UTC-4

# ─── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
ADMIN_ID         = int(os.environ["ADMIN_ID"])
CHANNEL_ID       = os.getenv("CHANNEL_ID", "")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL",    "15"))
RESOLVE_INTERVAL = int(os.getenv("RESOLVE_INTERVAL", "15"))
RESOLVE_TIMEOUT  = int(os.getenv("RESOLVE_TIMEOUT",  "180"))
LINEUP_INTERVAL  = int(os.getenv("LINEUP_INTERVAL",  "120"))

# ─── ESPN ──────────────────────────────────────────────────────────────────────
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
ESPN_SUMMARY    = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/summary"
ESPN_HEADERS    = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept": "application/json",
}

ESPN_LEAGUES = {
    # ── Italia ─────────────────────────────────────────────────────────────
    "Serie A":                  "ita.1",
    "Coppa Italia":             "ita.coppa_italia",
    "Supercopa de Italia":      "ita.super_cup",
    # ── España ─────────────────────────────────────────────────────────────
    "La Liga":                  "esp.1",
    "Copa del Rey":             "esp.copa_del_rey",
    "Supercopa de España":      "esp.super_cup",
    # ── Francia ────────────────────────────────────────────────────────────
    "Ligue 1":                  "fra.1",
    "Coupe de France":          "fra.coupe_de_france",
    "Trophée des Champions":    "fra.trophee_champions",
    # ── Alemania ───────────────────────────────────────────────────────────
    "Bundesliga":               "ger.1",
    "DFB-Pokal":                "ger.dfb_pokal",
    "Supercopa de Alemania":    "ger.super_cup",
    # ── Inglaterra ─────────────────────────────────────────────────────────
    "Premier League":           "eng.1",
    "FA Cup":                   "eng.fa",
    "EFL Cup":                  "eng.league_cup",
    "Community Shield":         "eng.community_shield",
    # ── UEFA ───────────────────────────────────────────────────────────────
    "Champions League":         "uefa.champions",
    "Europa League":            "uefa.europa",
    "Conference League":        "uefa.europa.conf",
    "Supercopa de Europa":      "uefa.super_cup",
    # ── CONMEBOL ───────────────────────────────────────────────────────────
    "Copa Libertadores":        "conmebol.libertadores",
    "Copa Sudamericana":        "conmebol.sudamericana",
    "Recopa Sudamericana":      "conmebol.recopa",
    "Liga Argentina":           "arg.1",
    "Liga FUTVE":               "ven.1",
    # ── Selecciones ────────────────────────────────────────────────────────
    "Nations League":           "uefa.nations",
    "Eurocopa":                 "uefa.euro",
    "Copa América":             "conmebol.america",
    # ── Mundial 2026 (clasificatorias + torneo) ────────────────────────────
    "Clasificación UEFA":       "fifa.uefa.worldq",
    "Clasificación CONMEBOL":   "fifa.conmebol.worldq",
    "Clasificación CONCACAF":   "fifa.concacaf.worldq",
    "Clasificación AFC":        "fifa.afc.worldq",
    "Clasificación CAF":        "fifa.caf.worldq",
    "Mundial de Clubes FIFA":   "fifa.cwc",
    "Mundial FIFA 2026":        "fifa.world",
    "Amistosos Países":         "fifa.friendly",
    "Amistosos Clubes":         "club.friendly",
    "Repesca Mundial":          "fifa.wcq.ply",
    
}

ESPN_FINAL  = {
    "STATUS_FINAL",
    "STATUS_FULL_TIME",
    "STATUS_FINAL_AET",
    "STATUS_FINAL_PEN",
    "STATUS_SHOOTOUT_FINAL",
}
ESPN_LIVE   = {
    "STATUS_IN_PROGRESS",
    "STATUS_HALFTIME",
    "STATUS_EXTRA_TIME",
    "STATUS_PENALTY",
    "STATUS_SHOOTOUT",
}
ESPN_PENALTIES = {"STATUS_FINAL_PEN", "STATUS_SHOOTOUT_FINAL"}
ESPN_AET       = {"STATUS_FINAL_AET"}

# Thread pool para requests HTTP (no bloquean el event loop)
_executor = ThreadPoolExecutor(max_workers=16)


# ══════════════════════════════════════════════════════════════════════════════
# MODELOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingGoal:
    fixture_id:   str
    league_slug:  str
    home_name:    str
    away_name:    str
    home_score:   int
    away_score:   int
    league_name:  str
    elapsed:      str
    goal_side:    str         = ""
    scorer:       str         = "Obteniendo..."
    assist:       str         = "Obteniendo..."
    goal_type:    str         = "goal"     # "goal" | "penalty" | "own_goal"
    resolved:     bool        = False
    tg_message:   Optional[Message] = None
    elapsed_secs: float       = 0.0


@dataclass
class TrackedFixture:
    fixture_id:     str
    league_slug:    str
    home_name:      str
    away_name:      str
    league_name:    str
    kickoff_utc:    Optional[datetime] = None
    home_score:     int  = 0
    away_score:     int  = 0
    status:         str  = ""
    finished:       bool = False
    lineup_sent:    bool = False
    lineup_tries:   int  = 0
    result_only:    bool = False           # Solo resultado final, sin goles en vivo
    in_extra_time:  bool = False           # Está en prórroga
    in_penalties:   bool = False           # Está en tanda de penales
    et_notified:    bool = False           # Ya se notificó inicio de prórroga
    pen_notified:   bool = False           # Ya se notificó inicio de penales
    _sofascore_id:  Optional[str] = None   # ID en Sofascore para livescore


# ─── Estado global ─────────────────────────────────────────────────────────────
tracked:       dict[str, TrackedFixture] = {}
pending_goals: list[PendingGoal]         = []
resolved_kev:  dict[str, set]            = {}
result_only_ids: set[str]               = {}   # IDs marcados como solo resultado final

# Cache de eventos del día: {event_id: raw_event_dict}
# Se rellena en /partidos y se reutiliza en cb_toggle sin re-consultar ESPN
_events_cache: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════════
# CAPA ESPN — funciones síncronas (se ejecutan en executor)
# ══════════════════════════════════════════════════════════════════════════════

def _espn_get(url: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(url, headers=ESPN_HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.debug("ESPN error %s: %s", url, exc)
        return None


def _fetch_scoreboard(slug: str, date: str = None) -> list[dict]:
    """date: 'YYYYMMDD' — fuerza la fecha en ESPN para no traer jornada anterior."""
    params = {"dates": date} if date else None
    data = _espn_get(ESPN_SCOREBOARD.format(league=slug), params=params)
    return data.get("events", []) if data else []


def _fetch_summary(slug: str, event_id: str) -> Optional[dict]:
    return _espn_get(ESPN_SUMMARY.format(league=slug), params={"event": event_id})


def _fetch_all_today() -> list[dict]:
    """
    Devuelve partidos del día usando ESPN con ?dates=YYYYMMDD para forzar
    la fecha correcta (sin este param ESPN devuelve la jornada anterior).
    Consulta hoy UTC, hoy local y mañana UTC para no perder partidos nocturnos.
    """
    now_utc     = datetime.now(timezone.utc)
    today_utc   = now_utc.date()
    today_local = now_utc.astimezone(TZ).date()

    dates_to_query = {
        today_utc.strftime("%Y%m%d"),
        today_local.strftime("%Y%m%d"),
        (now_utc + timedelta(hours=24)).date().strftime("%Y%m%d"),
    }

    results = []
    seen    = set()

    for league_name, slug in ESPN_LEAGUES.items():
        try:
            for date_str in dates_to_query:
                for ev in _fetch_scoreboard(slug, date=date_str):
                    if ev["id"] in seen:
                        continue
                    seen.add(ev["id"])
                    ev["_slug"]   = slug
                    ev["_league"] = league_name
                    results.append(ev)
        except Exception as exc:
            logger.debug("ESPN fetch error %s: %s", slug, exc)

    logger.info("ESPN: %d partidos hoy (%s)", len(results), today_utc)
    return results


# ── Wrappers async ─────────────────────────────────────────────────────────────

async def fetch_all_today() -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_all_today)


async def fetch_scoreboard(slug: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_scoreboard, slug)


async def fetch_summary(slug: str, event_id: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_summary, slug, event_id)


# ══════════════════════════════════════════════════════════════════════════════
# PARSEO
# ══════════════════════════════════════════════════════════════════════════════

def parse_event(ev: dict) -> dict:
    comp   = ev.get("competitions", [{}])[0]
    comps  = comp.get("competitors", [])
    home   = next((c for c in comps if c.get("homeAway") == "home"), {})
    away   = next((c for c in comps if c.get("homeAway") == "away"), {})
    status = comp.get("status", {})

    kickoff_utc = None
    kickoff_str = ""
    raw = ev.get("date", "")
    if raw:
        try:
            kickoff_utc = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            kickoff_str = kickoff_utc.astimezone(TZ).strftime("%H:%M")
        except Exception:
            pass

    return {
        "id":          ev["id"],
        "home_name":   home.get("team", {}).get("displayName", "?"),
        "away_name":   away.get("team", {}).get("displayName", "?"),
        "home_score":  int(home.get("score", 0) or 0),
        "away_score":  int(away.get("score", 0) or 0),
        "home_logo":   home.get("team", {}).get("logo", ""),
        "away_logo":   away.get("team", {}).get("logo", ""),
        "status_type": status.get("type", {}).get("name", ""),
        "status_desc": status.get("type", {}).get("description", ""),
        "clock":       status.get("displayClock", ""),
        "slug":        ev.get("_slug", ""),
        "league":      ev.get("_league", ""),
        "kickoff_utc": kickoff_utc,
        "kickoff_str": kickoff_str,
    }


def parse_goal_event(ev: dict) -> tuple[str, str, str]:
    """Devuelve (scorer, assist, goal_type) donde goal_type es 'goal'|'penalty'|'own_goal'."""
    scorer = assist = ""
    goal_type = "goal"

    ev_type_text = (ev.get("type", {}).get("text") or "").lower()
    ev_type_id   = str(ev.get("type", {}).get("id") or "")

    # Detectar tipo desde el evento
    if "own goal" in ev_type_text or "autogol" in ev_type_text:
        goal_type = "own_goal"
    elif "penalty" in ev_type_text or ev_type_id in ("96", "99"):
        goal_type = "penalty"

    for ath in ev.get("athletes", []):
        role = (ath.get("type") or "").lower()
        name = ath.get("displayName") or ath.get("fullName", "")
        if role in ("scorer", "goal", "goalscorer") and name:
            scorer = name
        elif role in ("assist", "assister") and name:
            assist = name

    raw = ev.get("shortText") or ev.get("text", "")
    if not scorer and raw:
        if re.search(r"own goal|autogol|en propia", raw, re.I):
            goal_type = "own_goal"
            m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑ]+?)\s+(own goal|autogol)", raw, re.I)
            if m:
                scorer = m.group(1).strip()
            else:
                scorer = "Autogol"

        # "Viktor Gyökeres Penalty - Scored"  ← formato real de ESPN para penales
        elif re.search(r"Penalty\s*-\s*Scored", raw, re.I):
            goal_type = "penalty"
            m = re.match(r"^(.+?)\s+Penalty\b", raw, re.I)
            if m:
                scorer = m.group(1).strip()

        elif re.search(r"\(pen\b|\bpenalti\b", raw, re.I):
            goal_type = "penalty"
            m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑ]+?)\s*\(", raw)
            if m:
                scorer = m.group(1).strip()

        else:
            # "Viktor Gyökeres Goal 1-0" → antes de "Goal"
            m = re.match(r"^(.+?)\s+Goal\b", raw, re.I)
            if m:
                scorer = m.group(1).strip()
            # "Nombre 45'" → antes del minuto
            if not scorer:
                m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑ]+?)\s+\d+[''']", raw)
                if m:
                    scorer = m.group(1).strip()
            # Último recurso: antes de paréntesis o dígito
            if not scorer:
                m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑćąęóźżłšč]+?)\s*[\(\d]", raw)
                if m:
                    scorer = m.group(1).strip()

    if not assist and raw:
        m = re.search(r"[Aa]ssist[e]?[:\s]+([\w\s.\-'áéíóúñÁÉÍÓÚÑ]+?)[\),\n]", raw)
        if m:
            assist = m.group(1).strip()

    return scorer or "", assist or "", goal_type


def parse_key_events(summary: dict) -> list[dict]:
    return [
        ev for ev in summary.get("keyEvents", [])
        if "goal" in (ev.get("type", {}).get("text") or "").lower()
    ]


def parse_lineups(summary: dict) -> tuple[list[str], list[str], str, str, str, str]:
    """
    Devuelve (home_xi, away_xi, home_formation, away_formation,
               home_logo_url, away_logo_url)
    """
    home_xi: list[str] = []
    away_xi: list[str] = []
    home_formation = "4-3-3"
    away_formation = "4-3-3"
    home_logo_url  = ""
    away_logo_url  = ""

    rosters = summary.get("rosters", [])[:2]
    header  = summary.get("header", {})
    comps   = (header.get("competitions") or [{}])[0].get("competitors", [])

    for i, roster in enumerate(rosters):
        dest = home_xi if i == 0 else away_xi

        # Formación
        formation = roster.get("formation", "")
        if formation:
            if i == 0:
                home_formation = formation
            else:
                away_formation = formation

        # Logo desde header
        team_data = roster.get("team", {})
        logo = team_data.get("logo", "")
        if not logo:
            # Buscar en competitors del header
            if i < len(comps):
                logo = comps[i].get("team", {}).get("logo", "")
        if i == 0:
            home_logo_url = logo
        else:
            away_logo_url = logo

        for entry in roster.get("roster", []):
            if entry.get("starter"):
                name = (entry.get("athlete", {}).get("shortName")
                        or entry.get("athlete", {}).get("displayName", ""))
                if name:
                    dest.append(name)

        if i == 0:
            home_xi = home_xi[:11]
        else:
            away_xi = away_xi[:11]

    return home_xi, away_xi, home_formation, away_formation, home_logo_url, away_logo_url


def parse_stats(summary: dict) -> tuple[dict, dict]:
    """Fallback ESPN — solo se usa si Sofascore no devuelve datos."""
    MAP = {
        "possessionPct": "Posesion",
        "shotsOnTarget": "Tiros a puerta",
        "totalShots":    "Tiros totales",
        "corners":       "Corners",
        "fouls":         "Faltas",
        "yellowCards":   "Tarjetas amarillas",
        "redCards":      "Tarjetas rojas",
    }
    home_s: dict = {}
    away_s: dict = {}
    for i, block in enumerate(summary.get("boxscore", {}).get("teams", [])[:2]):
        dest = home_s if i == 0 else away_s
        for stat in block.get("statistics", []):
            key = MAP.get(stat.get("name", ""))
            if key:
                raw = (stat.get("displayValue") or "0").replace("%", "").strip()
                try:
                    dest[key] = float(raw)
                except ValueError:
                    dest[key] = 0.0
    return home_s, away_s


def build_raw_stats_from_espn(summary: dict) -> list[dict]:
    """Convierte las stats de ESPN al formato raw_stats de image_generator."""
    home_s, away_s = parse_stats(summary)
    def to_list(d: dict) -> list[dict]:
        return [{"type": k, "value": v} for k, v in d.items()]
    return [
        {"statistics": to_list(home_s)},
        {"statistics": to_list(away_s)},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# MENSAJES
# ══════════════════════════════════════════════════════════════════════════════

def msg_goal(home: str, away: str, hs: int, as_: int,
             league: str, scorer: str, assist: str,
             side: str = "", elapsed: str = "",
             goal_type: str = "goal") -> str:
    minute = f"⌚ {elapsed}'" if elapsed and elapsed != "0" else ""

    if side == "home":
        score = f"[{hs}]\u2013{as_}"
    elif side == "away":
        score = f"{hs}\u2013[{as_}]"
    else:
        score = f"{hs}\u2013{as_}"

    lines = [
        "*🥅 | GOOOOOL!*",
        "",
        f"*{home} {score} {away}*",
        "",
    ]
    if minute:
        lines.append(minute)

    # Goleador con indicador de tipo
    if goal_type == "penalty":
        scorer_line = f"⚽ {scorer} *(pen.)*" if scorer and scorer not in ("-", "Obteniendo...") else f"⚽ {scorer}"
    elif goal_type == "own_goal":
        # Autogol: mostrar nombre si está disponible
        if scorer and scorer not in ("-", "Obteniendo...", "Autogol"):
            scorer_line = f"⚽ {scorer} *(en propia)*"
        else:
            scorer_line = "⚽ Autogol"
    else:
        scorer_line = f"⚽ {scorer}"

    lines.append(scorer_line)

    # Asistencia: omitir en penales y autogoles
    if assist and assist != "-" and goal_type == "goal":
        lines.append(f"🅰️ {assist}")

    lines += ["", "*📲 Suscribete en t.me/iUniversoFootball*"]
    return "\n".join(lines)


def msg_goal_cancelled(home: str, away: str, hs: int, as_: int,
                       side: str = "", elapsed: str = "",
                       player: str = "") -> str:
    """Mensaje de gol anulado por VAR."""
    if side == "home":
        score = f"[{hs}]\u2013{as_}"
    elif side == "away":
        score = f"{hs}\u2013[{as_}]"
    else:
        score = f"{hs}\u2013{as_}"

    lines = [
        "*🚩 | GOL ANULADO*",
        "",
        f"*{home} {score} {away}*",
        "",
    ]
    if elapsed and elapsed != "0":
        lines.append(f"⌚️ {elapsed}'")
    if player:
        lines.append(f"❌ {player}")
    lines += ["", "*📲 Suscribete en t.me/iUniversoFootball*"]
    return "\n".join(lines)


def msg_extra_time(home: str, away: str, hs: int, as_: int) -> str:
    return "\n".join([
        "*⏱️ | PRÓRROGA*",
        "",
        f"*{home} {hs}-{as_} {away}*",
        "",
        "_El partido va a tiempo extra._",
        "",
        "*📲 Suscribete en t.me/iUniversoFootball*",
    ])


def msg_penalties_start(home: str, away: str, hs: int, as_: int) -> str:
    return "\n".join([
        "*🥅 | TANDA DE PENALES*",
        "",
        f"*{home} {hs}-{as_} {away}*",
        "",
        "_¡Se define desde los once metros!_",
        "",
        "*📲 Suscribete en t.me/iUniversoFootball*",
    ])


def msg_final(home: str, away: str, hs: int, as_: int) -> str:
    return "\n".join([
        "*📢 | FINAL DEL PARTIDO*",
        "",
        f"↪️ {home} {hs}-{as_} {away}",
        "",
        "*🎦 Todos los videos de los goles disponibles aqui: t.me/ufgoals*",
        "",
        "_⚽ Suscribete en t.me/iUniversoFootball_",
    ])


def msg_final_aet(home: str, away: str, hs: int, as_: int) -> str:
    return "\n".join([
        "*📢 | FINAL — PRÓRROGA*",
        "",
        f"↪️ {home} {hs}-{as_} {away} *(a.e.t.)*",
        "",
        "*🎦 Todos los videos de los goles disponibles aqui: t.me/ufgoals*",
        "",
        "_⚽ Suscribete en t.me/iUniversoFootball_",
    ])


def msg_final_pen(home: str, away: str, hs: int, as_: int,
                  pen_h: int, pen_a: int,
                  home_kicks: list, away_kicks: list) -> str:
    lines = [
        "*📢 | FINAL — PENALES*",
        "",
        f"↪️ {home} {hs}-{as_} {away} *(90')*",
        f"🥅 Penales: *{home} {pen_h}-{pen_a} {away}*",
    ]
    if home_kicks or away_kicks:
        lines.append("")
        max_len = max(len(home_kicks), len(away_kicks))
        for i in range(max_len):
            hk = home_kicks[i] if i < len(home_kicks) else ""
            ak = away_kicks[i] if i < len(away_kicks) else ""
            if hk and ak:
                lines.append(f"  {hk}   |   {ak}")
            elif hk:
                lines.append(f"  {hk}")
            elif ak:
                lines.append(f"  {'':20}|   {ak}")
    lines += [
        "",
        "*🎦 Todos los videos de los goles disponibles aqui: t.me/ufgoals*",
        "",
        "_⚽ Suscribete en t.me/iUniversoFootball_",
    ]
    return "\n".join(lines)


def parse_shootout(summary: dict) -> tuple[list, list]:
    home_kicks: list = []
    away_kicks: list = []
    for ev in summary.get("keyEvents", []):
        ev_type = (ev.get("type", {}).get("text") or "").lower()
        if "shootout" not in ev_type and "penalty" not in ev_type:
            continue
        text  = (ev.get("shortText") or ev.get("text") or "").lower()
        scored = "saved" not in text and "missed" not in text and "post" not in text
        icon   = "✅" if scored else "❌"
        name   = ""
        for ath in ev.get("athletes", []):
            name = ath.get("displayName") or ath.get("fullName", "")
            if name:
                break
        if not name:
            raw = ev.get("shortText") or ev.get("text") or ""
            m = re.match(r"^(.+?)\s+Goal\b", raw, re.I) or re.match(r"^([\w\s.\-']+)", raw)
            name = m.group(1).strip() if m else "?"
        team_id = ev.get("team", {}).get("id", "")
        comps   = (summary.get("header", {}).get("competitions") or [{}])[0].get("competitors", [])
        home_id = comps[0].get("id", "") if comps else ""
        if team_id == home_id:
            home_kicks.append(f"{name} {icon}")
        else:
            away_kicks.append(f"{name} {icon}")
    return home_kicks, away_kicks


def msg_lineup(league: str, home: str, away: str,
               home_xi: list[str], away_xi: list[str]) -> str:
    tag  = league.replace(" ", "")
    h_xi = ", ".join(home_xi) if home_xi else "No disponible"
    a_xi = ", ".join(away_xi) if away_xi else "No disponible"
    return "\n".join([
        f"*👥 ALINEACIONES #{tag} | {home} vs. {away}*",
        "",
        f"*{home} XI:* {h_xi}",
        "",
        f"*{away} XI:* {a_xi}",
        "",
        "_⚽ Suscribete en t.me/iUniversoFootball_",
    ])


# ══════════════════════════════════════════════════════════════════════════════
# LOOPS DE BACKGROUND
# ══════════════════════════════════════════════════════════════════════════════

# Set de partidos con tarea de resolución ya lanzada
_resolving: set[str] = set()


async def _resolve_goal(app: Application, pg: PendingGoal):
    """
    Resuelve el goleador buscando en ESPN por nombre de equipo (fuente principal).
    FotMob y Sofascore como fallback.
    Primer intento inmediato, luego cada RESOLVE_INTERVAL segundos.
    """
    loop     = asyncio.get_running_loop()
    seen     = resolved_kev.setdefault(pg.fixture_id, set())
    elapsed  = 0
    interval = RESOLVE_INTERVAL

    logger.info("Resolviendo gol: %s vs %s min %s",
                pg.home_name, pg.away_name, pg.elapsed)

    while elapsed < RESOLVE_TIMEOUT and not pg.resolved:
        try:
            # ── 1. ESPN por nombre de equipo (principal) ──────────────────
            from espn_goals import get_espn_scorer
            results = await loop.run_in_executor(
                _executor, get_espn_scorer,
                pg.home_name, pg.away_name, seen,
            )
            for scorer, assist, kid in results:
                seen.add(kid)
                pg.scorer   = scorer
                pg.assist   = assist or ""
                pg.resolved = True
                logger.info("ESPN resolvio: '%s' asiste '%s'", scorer, assist or "-")
                break

            # ── 2. FotMob (fallback) ──────────────────────────────────────
            if not pg.resolved:
                try:
                    fm = await loop.run_in_executor(
                        _executor, get_scorer_assist,
                        pg.home_name, pg.away_name, None,
                    )
                    for scorer, assist, kid in fm:
                        if not scorer or scorer == "-":
                            continue
                        if kid in seen:
                            continue
                        seen.add(kid)
                        pg.scorer   = scorer
                        pg.assist   = assist or ""
                        pg.resolved = True
                        logger.info("FotMob resolvio: '%s'", scorer)
                        break
                except Exception as exc:
                    logger.debug("FotMob error: %s", exc)

            # ── 3. Sofascore incidents (ultimo recurso) ───────────────────
            if not pg.resolved:
                try:
                    sf_id = await loop.run_in_executor(
                        _executor, find_sofascore_match_id,
                        pg.home_name, pg.away_name,
                    )
                    if sf_id:
                        sf_data = await loop.run_in_executor(
                            _executor, sofascore_get,
                            f"https://www.sofascore.com/api/v1/event/{sf_id}/incidents",
                        )
                        for inc in (sf_data or {}).get("incidents", []):
                            if inc.get("incidentType") not in ("goal", "penalty"):
                                continue
                            pid = (inc.get("player") or {}).get("id", "")
                            kid = f"sf_{sf_id}_{inc.get('time',0)}_{pid}"
                            if kid in seen:
                                continue
                            scorer = (inc.get("player") or {}).get("name", "")
                            if not scorer:
                                continue
                            seen.add(kid)
                            pg.scorer   = scorer
                            pg.assist   = (inc.get("assist1") or {}).get("name", "") or ""
                            pg.resolved = True
                            logger.info("Sofascore resolvio: '%s'", scorer)
                            break
                except Exception as exc:
                    logger.debug("Sofascore error: %s", exc)

        except Exception as exc:
            logger.warning("_resolve_goal error: %s", exc)

        if pg.resolved:
            break
        await asyncio.sleep(interval)
        elapsed += interval

    # ── Editar mensaje Telegram ───────────────────────────────────────────
    if pg.resolved and pg.scorer and pg.scorer not in ("-", "Obteniendo..."):
        text = msg_goal(
            pg.home_name, pg.away_name,
            pg.home_score, pg.away_score,
            pg.league_name, pg.scorer, pg.assist,
            pg.goal_side, pg.elapsed,
            pg.goal_type,
        )
        if pg.tg_message:
            try:
                await pg.tg_message.edit_text(
                    text, parse_mode="Markdown",
                    link_preview_options=_NO_PREVIEW,
                )
                logger.info("Mensaje editado: %s asiste %s",
                            pg.scorer, pg.assist or "-")
            except BadRequest:
                pass
            except Exception as exc:
                logger.error("Error editando mensaje: %s", exc)
    else:
        pg.resolved = True
        logger.warning("Timeout sin goleador: %s vs %s min %s",
                       pg.home_name, pg.away_name, pg.elapsed)

    _resolving.discard(pg.fixture_id + pg.elapsed)


async def monitor_loop(app: Application):
    logger.info("monitor_loop iniciado (poll cada %ds)", POLL_INTERVAL)
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        for fid, fix in list(tracked.items()):
            if fix.finished:
                continue
            try:
                # Obtener estado actual desde ESPN (fuente confiable)
                events = await fetch_scoreboard(fix.league_slug)
                raw = next((e for e in events if e["id"] == fid), None)
                if not raw:
                    continue
                raw["_slug"]   = fix.league_slug
                raw["_league"] = fix.league_name

                p      = parse_event(raw)
                new_h  = p["home_score"]
                new_a  = p["away_score"]
                status = p["status_type"]
                clock  = p["clock"]

                # ── Actualizar estado ──────────────────────────────────────
                fix.status = status

                # ── Notificación de inicio de prórroga ────────────────────
                if status == "STATUS_EXTRA_TIME" and not fix.et_notified:
                    fix.et_notified    = True
                    fix.in_extra_time  = True
                    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                    try:
                        await app.bot.send_message(
                            chat_id=dest,
                            text=msg_extra_time(fix.home_name, fix.away_name,
                                                fix.home_score, fix.away_score),
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                        )
                    except Exception as exc:
                        logger.error("Error enviando prórroga: %s", exc)

                # ── Notificación de inicio de tanda de penales ────────────
                if status in ("STATUS_PENALTY", "STATUS_SHOOTOUT") and not fix.pen_notified:
                    fix.pen_notified  = True
                    fix.in_penalties  = True
                    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                    try:
                        await app.bot.send_message(
                            chat_id=dest,
                            text=msg_penalties_start(fix.home_name, fix.away_name,
                                                     fix.home_score, fix.away_score),
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                        )
                    except Exception as exc:
                        logger.error("Error enviando penales: %s", exc)

                # ── Gol detectado ──────────────────────────────────────────
                if new_h != fix.home_score or new_a != fix.away_score:
                    dh   = new_h - fix.home_score
                    da   = new_a - fix.away_score
                    side = "home" if dh > 0 and da == 0 else "away" if da > 0 and dh == 0 else ""
                    fix.home_score = new_h
                    fix.away_score = new_a

                    # Si es result_only, no se publican goles en vivo
                    if not fix.result_only:
                        dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                        for _ in range(max(dh + da, 1)):
                            text = msg_goal(fix.home_name, fix.away_name,
                                            new_h, new_a, fix.league_name,
                                            "-", "", side, clock)
                            try:
                                sent = await app.bot.send_message(
                                    chat_id=dest, text=text,
                                    parse_mode="Markdown",
                                    disable_web_page_preview=True,
                                )
                                pg = PendingGoal(
                                    fixture_id=fid, league_slug=fix.league_slug,
                                    home_name=fix.home_name, away_name=fix.away_name,
                                    home_score=new_h, away_score=new_a,
                                    league_name=fix.league_name, elapsed=clock,
                                    goal_side=side, tg_message=sent,
                                )
                                pending_goals.append(pg)
                                # Lanzar resolución inmediata en paralelo
                                asyncio.create_task(_resolve_goal(app, pg))
                                logger.info("⚽ Gol detectado: %s %d-%d %s",
                                            fix.home_name, new_h, new_a, fix.away_name)
                            except Exception as exc:
                                logger.error("Error enviando gol: %s", exc)

                # Final
                if status in ESPN_FINAL and not fix.finished:
                    fix.finished = True
                    summary = await fetch_summary(fix.league_slug, fid)

                    # Imagen
                    img_path = None
                    if summary:
                        try:
                            fd = {
                                "fixture": {"id": fid},
                                "league":  {"name": fix.league_name},
                                "teams": {
                                    "home": {"name": fix.home_name, "logo": p["home_logo"]},
                                    "away": {"name": fix.away_name, "logo": p["away_logo"]},
                                },
                                "goals": {"home": fix.home_score, "away": fix.away_score},
                            }
                            # Intentar Sofascore primero; fallback a ESPN
                            loop = asyncio.get_running_loop()
                            raw_stats = await loop.run_in_executor(
                                _executor, sofascore_raw_stats,
                                fix.home_name, fix.away_name, None,
                            )
                            if raw_stats is None:
                                logger.info("Sofascore sin datos, usando ESPN para stats de imagen.")
                                raw_stats = build_raw_stats_from_espn(summary)
                            else:
                                logger.info("Stats de imagen obtenidas desde Sofascore.")
                            from image_generator import generate_match_summary
                            img_path = await loop.run_in_executor(
                                _executor, generate_match_summary, fd, raw_stats
                            )
                        except Exception as exc:
                            logger.error("Error generando imagen: %s", exc)

                    # Texto según tipo de final
                    if status in ESPN_PENALTIES:
                        pen_h = pen_a = 0
                        home_kicks: list = []
                        away_kicks: list = []
                        if summary:
                            try:
                                comp0 = (summary.get("header", {}).get("competitions") or [{}])[0]
                                for competitor in comp0.get("competitors", []):
                                    pen_score = int(competitor.get("shootoutScore", 0) or 0)
                                    if competitor.get("homeAway") == "home":
                                        pen_h = pen_score
                                    else:
                                        pen_a = pen_score
                                home_kicks, away_kicks = parse_shootout(summary)
                            except Exception as exc:
                                logger.warning("Error leyendo tanda de penales: %s", exc)
                        text = msg_final_pen(
                            fix.home_name, fix.away_name,
                            fix.home_score, fix.away_score,
                            pen_h, pen_a, home_kicks, away_kicks,
                        )
                        logger.info("🥅 Final en penales: %s %d(%d)-%d(%d) %s",
                                    fix.home_name, fix.home_score, pen_h,
                                    fix.away_score, pen_a, fix.away_name)
                    elif status in ESPN_AET:
                        text = msg_final_aet(fix.home_name, fix.away_name,
                                             fix.home_score, fix.away_score)
                        logger.info("⏱️ Final en prórroga: %s %d-%d %s",
                                    fix.home_name, fix.home_score,
                                    fix.away_score, fix.away_name)
                    else:
                        text = msg_final(fix.home_name, fix.away_name,
                                         fix.home_score, fix.away_score)

                    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                    try:
                        if img_path and os.path.exists(img_path):
                            with open(img_path, "rb") as f:
                                await app.bot.send_photo(chat_id=dest, photo=f, caption=text, parse_mode="Markdown")
                        else:
                            await app.bot.send_message(chat_id=dest, text=text, parse_mode="Markdown", disable_web_page_preview=True)
                    except Exception as exc:
                        logger.error("Error enviando final: %s", exc)

                    tracked.pop(fid, None)

            except Exception as exc:
                logger.error("monitor_loop error en %s: %s", fid, exc)



async def lineup_loop(app: Application):
    logger.info("lineup_loop iniciado")
    while True:
        await asyncio.sleep(LINEUP_INTERVAL)
        now = datetime.now(timezone.utc)

        for fid, fix in list(tracked.items()):
            if fix.lineup_sent or fix.finished:
                continue
            # Si kickoff_utc es None, intentar igualmente si el partido ya está activo
            if fix.kickoff_utc is not None:
                mins = (fix.kickoff_utc - now).total_seconds() / 60
                # Buscar desde 90 min antes hasta 30 min después del inicio
                if not (-30 <= mins <= 90):
                    logger.debug(
                        "Lineup %s vs %s: fuera de ventana (%.1f min para inicio)",
                        fix.home_name, fix.away_name, mins,
                    )
                    continue
                logger.info(
                    "Lineup %s vs %s: dentro de ventana (%.1f min para inicio, intento %d)",
                    fix.home_name, fix.away_name, mins, fix.lineup_tries + 1,
                )
            else:
                # Sin hora de inicio conocida: solo intentar si el partido está en vivo
                if fix.status not in ESPN_LIVE:
                    continue
                logger.info(
                    "Lineup %s vs %s: sin kickoff_utc, partido en vivo, intentando...",
                    fix.home_name, fix.away_name,
                )
            try:
                summary = await fetch_summary(fix.league_slug, fid)
                if not summary:
                    logger.warning("Lineup %s vs %s: summary vacío", fix.home_name, fix.away_name)
                    continue
                home_xi, away_xi, home_formation, away_formation, home_logo_url, away_logo_url = parse_lineups(summary)
                if len(home_xi) < 11 or len(away_xi) < 11:
                    fix.lineup_tries += 1
                    logger.info(
                        "Alineaciones incompletas %s vs %s: home=%d away=%d (intento %d)",
                        fix.home_name, fix.away_name, len(home_xi), len(away_xi), fix.lineup_tries,
                    )
                    # Tras 20 intentos fallidos, rendirse para no spamear la API
                    if fix.lineup_tries >= 20:
                        logger.warning(
                            "Lineup %s vs %s: demasiados intentos, marcando como enviado",
                            fix.home_name, fix.away_name,
                        )
                        fix.lineup_sent = True
                    continue

                caption = msg_lineup(fix.league_name, fix.home_name, fix.away_name, home_xi, away_xi)
                dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                await _send_lineup_images(
                    app, dest, fix.home_name, fix.away_name,
                    home_xi, away_xi, home_formation, away_formation,
                    home_logo_url, away_logo_url, fix.league_name,
                    fid, caption,
                )
                fix.lineup_sent = True
                logger.info("Alineaciones enviadas: %s vs %s", fix.home_name, fix.away_name)
            except Exception as exc:
                logger.error("lineup_loop error %s: %s", fid, exc)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE ALINEACIONES
# ══════════════════════════════════════════════════════════════════════════════

async def _send_lineup_images(
    app, dest, home_name, away_name,
    home_xi, away_xi, home_formation, away_formation,
    home_logo_url, away_logo_url, league_name,
    match_id, caption_text,
):
    """Genera las 2 imágenes y las envía como media group."""
    from telegram import InputMediaPhoto
    loop = asyncio.get_running_loop()
    try:
        # run_in_executor solo acepta *args, usamos lambda para pasar kwargs
        path_home, path_away = await loop.run_in_executor(
            _executor,
            lambda: generate_lineup_images(
                home_name=home_name,
                away_name=away_name,
                home_xi=home_xi,
                away_xi=away_xi,
                home_formation=home_formation,
                away_formation=away_formation,
                home_logo_url=home_logo_url,
                away_logo_url=away_logo_url,
                league_name=league_name,
                match_id=str(match_id),
            ),
        )
        # Leer bytes antes de pasarlos a InputMediaPhoto
        with open(path_home, "rb") as f_home, open(path_away, "rb") as f_away:
            media = [
                InputMediaPhoto(media=f_home.read()),
                InputMediaPhoto(media=f_away.read(), caption=caption_text, parse_mode="Markdown"),
            ]
        await app.bot.send_media_group(chat_id=dest, media=media)
    except Exception as exc:
        logger.error("Error enviando imágenes de lineup: %s", exc)
        # Fallback: enviar solo texto
        await app.bot.send_message(chat_id=dest, text=caption_text, parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("Sin permiso.")
            return
        return await func(update, ctx)
    return wrapper


@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "Bot Universo Football activo\n\n"
        "Comandos:\n"
        "/partidos    - Partidos del dia y activar monitoreo\n"
        "/activos     - Ver partidos monitoreados\n"
        "/rf          - Activar modo Solo Resultado Final\n"
        "/tabla       - Tabla de clasificación en imagen (/tabla esp.1)\n"
        "/rd          - Resultados por liga en imagen (/rd esp.1 2026-04-06)\n"
        "/ligas       - Ver todos los slugs de ligas disponibles\n"
        "/stop        - Detener monitoreo de un partido\n"
        "/test        - Preview del post final\n"
        "/preview     - Enviar al canal un ejemplo de alineaciones y gol\n"
        "/lineup      - Enviar alineaciones manualmente al canal\n"
        "/testlineup  - Preview privado de imágenes de alineación\n"
        "/debug       - Diagnóstico de ESPN por liga\n"
        "/espn        - Test directo: /espn <slug> (ej: /espn ita.1)"
    )
    await update.message.reply_text(text)


@admin_only
async def cmd_ligas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /ligas — Muestra todos los slugs de ESPN disponibles organizados por región.
    """
    sections = {
        "🇮🇹 Italia":      ["Serie A", "Coppa Italia", "Supercopa de Italia"],
        "🇪🇸 España":      ["La Liga", "Copa del Rey", "Supercopa de España"],
        "🇫🇷 Francia":     ["Ligue 1", "Coupe de France", "Trophée des Champions"],
        "🇩🇪 Alemania":    ["Bundesliga", "DFB-Pokal", "Supercopa de Alemania"],
        "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Inglaterra": ["Premier League", "FA Cup", "EFL Cup", "Community Shield"],
        "🇪🇺 UEFA":        ["Champions League", "Europa League", "Conference League", "Supercopa de Europa", "Nations League", "Eurocopa"],
        "🌎 CONMEBOL":     ["Copa Libertadores", "Copa Sudamericana", "Recopa Sudamericana", "Copa América"],
        "🌍 Clasificatorias": ["Clasificación UEFA", "Clasificación CONMEBOL", "Clasificación CONCACAF", "Clasificación AFC", "Clasificación CAF"],
        "🌐 FIFA":         ["Mundial de Clubes FIFA", "Mundial FIFA 2026", "Amistosos Países", "Amistosos Clubes", "Repesca Mundial"],
    }
    lines = ["*⚽ Slugs de ligas disponibles (ESPN)*", ""]
    for region, league_names in sections.items():
        lines.append(f"*{region}*")
        for name in league_names:
            slug = ESPN_LEAGUES.get(name)
            if slug:
                lines.append(f"  `{slug}` → {name}")
        lines.append("")
    lines.append("_Uso: /tabla <slug>  |  /rd <slug> [fecha]_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /debug — Muestra qué devuelve ESPN por liga en crudo.
    Útil para diagnosticar por qué no aparecen partidos.
    """
    msg = await update.message.reply_text("Consultando ESPN por liga...")
    lines = []
    today_utc   = datetime.now(timezone.utc).date()
    today_local = datetime.now(TZ).date()
    hora_srv    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("Hora servidor: " + hora_srv)
    lines.append("Hoy UTC: " + str(today_utc) + " | Hoy UTC-4: " + str(today_local))
    lines.append("")

    loop = asyncio.get_running_loop()
    for league_name, slug in ESPN_LEAGUES.items():
        try:
            events = await loop.run_in_executor(_executor, _fetch_scoreboard, slug)
            if not events:
                lines.append("❌ " + league_name + ": sin eventos")
                continue

            today_evs = []
            for ev in events:
                raw = ev.get("date", "")
                if raw:
                    try:
                        dt      = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        d_utc   = dt.astimezone(timezone.utc).date()
                        d_local = dt.astimezone(TZ).date()
                        if d_utc == today_utc or d_local == today_local:
                            today_evs.append(ev)
                    except Exception:
                        today_evs.append(ev)
                else:
                    today_evs.append(ev)

            if today_evs:
                for ev in today_evs[:2]:
                    comps = ev.get("competitions", [{}])[0].get("competitors", [])
                    home  = next((c for c in comps if c.get("homeAway") == "home"), {})
                    away  = next((c for c in comps if c.get("homeAway") == "away"), {})
                    hn    = home.get("team", {}).get("displayName", "?")
                    an    = away.get("team", {}).get("displayName", "?")
                    raw_d = ev.get("date", "?")
                    lines.append("✅ " + league_name + ": " + hn + " vs " + an + " (" + raw_d + ")")
            else:
                dates = [e.get("date", "?") for e in events[:2]]
                lines.append("⚠️ " + league_name + ": " + str(len(events)) + " eventos, ninguno hoy. Fechas: " + str(dates))

        except Exception as exc:
            lines.append("💥 " + league_name + ": ERROR " + str(exc))

    # Enviar en bloques (límite Telegram 4096 chars)
    full_text = "\n".join(lines)
    chunks = [full_text[i:i+3900] for i in range(0, len(full_text), 3900)]
    await msg.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await update.message.reply_text(chunk)


@admin_only
async def cmd_espn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /espn <slug> — Consulta directa a ESPN para un slug concreto.
    Ejemplo: /espn ita.1
    Muestra los primeros 5 eventos RAW sin filtrar fecha.
    """
    slug = (ctx.args[0] if ctx.args else "ita.1").strip()
    msg  = await update.message.reply_text(f"Consultando ESPN: {slug}...")
    loop = asyncio.get_running_loop()
    try:
        events = await loop.run_in_executor(_executor, _fetch_scoreboard, slug)
    except Exception as exc:
        await msg.edit_text(f"Error: {exc}")
        return

    if not events:
        await msg.edit_text(f"ESPN no devolvió eventos para: {slug}")
        return

    lines = [f"ESPN slug {slug} — {len(events)} evento(s) totales:\n"]
    for ev in events[:5]:
        comps = ev.get("competitions", [{}])[0].get("competitors", [])
        home  = next((c for c in comps if c.get("homeAway") == "home"), {})
        away  = next((c for c in comps if c.get("homeAway") == "away"), {})
        hn    = home.get("team", {}).get("displayName", "?")
        an    = away.get("team", {}).get("displayName", "?")
        fecha = ev.get("date", "sin fecha")
        st    = ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("name", "?")
        lines.append(f"• {hn} vs {an}  |  {fecha}  |  {st}")

    await msg.edit_text("\n".join(lines))


@admin_only
async def cmd_partidos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Consultando ESPN...")
    try:
        all_events = await fetch_all_today()
    except Exception as exc:
        logger.error("Error fetch_all_today: %s", exc)
        await msg.edit_text("Error consultando ESPN. Intenta de nuevo.")
        return

    if not all_events:
        today_str = datetime.now(TZ).strftime("%d/%m/%Y")
        await msg.edit_text(f"No hay partidos hoy {today_str} en las ligas configuradas.")
        return

    await msg.delete()

    # Guardar eventos en cache para que cb_toggle no necesite re-consultar ESPN
    for ev in all_events:
        _events_cache[ev["id"]] = ev

    by_league: dict[str, list] = {}
    for ev in all_events:
        by_league.setdefault(ev.get("_league", "Otra"), []).append(ev)

    today_str = datetime.now(TZ).strftime("%d/%m/%Y")
    for league_name, events in by_league.items():
        keyboard = []
        for ev in events:
            p    = parse_event(ev)
            fid  = p["id"]
            slug = ev.get("_slug", "")
            act  = "OK " if fid in tracked else ""
            hora = p["kickoff_str"] or "--:--"
            label = f"{act}{hora} | {p['home_name']} vs {p['away_name']} ({p['home_score']}-{p['away_score']}) {p['status_desc']}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"tog:{fid}:{slug}")])

        try:
            await update.message.reply_text(
                f"{today_str} - {league_name}",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as exc:
            logger.error("Error enviando teclado %s: %s", league_name, exc)


@admin_only
async def cmd_rf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /rf — Alterna el modo "Solo Resultado Final" para partidos activos.
    En este modo el bot NO publica goles en vivo, solo el mensaje de
    resultado al terminar el partido.
    Sin argumentos muestra botones para togglear partido por partido.
    """
    if not tracked:
        await update.message.reply_text("No hay partidos monitoreados. Actívalos primero con /partidos.")
        return

    keyboard = []
    for fid, fix in tracked.items():
        estado = "🔇 RF" if fix.result_only else "📡 Live"
        hora = fix.kickoff_utc.astimezone(TZ).strftime("%H:%M") if fix.kickoff_utc else "--:--"
        label = f"{estado} | {hora} {fix.home_name} vs {fix.away_name}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"rf:{fid}")])

    await update.message.reply_text(
        "📢 *Modo Resultado Final*\n\n"
        "Pulsa un partido para alternar entre:\n"
        "• 📡 *Live* — publica goles en tiempo real\n"
        "• 🔇 *RF* — solo publica el resultado al finalizar\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def cb_rf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback para togglear result_only de un partido."""
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    fid = query.data.split(":")[1]
    fix = tracked.get(fid)
    if not fix:
        try:
            await query.edit_message_text("Partido no encontrado.")
        except Exception:
            pass
        return

    fix.result_only = not fix.result_only
    modo = "🔇 Solo Resultado Final" if fix.result_only else "📡 Livescore completo"
    logger.info("Partido %s → modo: %s", fid, modo)

    # Refrescar teclado
    keyboard = []
    for f_id, f in tracked.items():
        estado = "🔇 RF" if f.result_only else "📡 Live"
        hora = f.kickoff_utc.astimezone(TZ).strftime("%H:%M") if f.kickoff_utc else "--:--"
        label = f"{estado} | {hora} {f.home_name} vs {f.away_name}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"rf:{f_id}")])

    try:
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
    except Exception:
        pass
    await query.message.reply_text(
        f"{'🔇' if fix.result_only else '📡'} *{fix.home_name} vs {fix.away_name}*\n"
        f"Modo: *{modo}*",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_activos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not tracked:
        await update.message.reply_text("No hay partidos monitoreados.")
        return
    lines = ["Partidos en monitoreo:\n"]
    for fid, fix in tracked.items():
        hora = fix.kickoff_utc.astimezone(TZ).strftime("%H:%M") if fix.kickoff_utc else "--:--"
        xi   = "XI ok" if fix.lineup_sent else "XI pendiente"
        modo = " [RF]" if fix.result_only else ""
        et   = " [ET]" if fix.in_extra_time else ""
        pen  = " [PEN]" if fix.in_penalties else ""
        lines.append(f"- {hora} {fix.home_name} {fix.home_score}-{fix.away_score} {fix.away_name} ({fix.league_name}) [{xi}]{modo}{et}{pen}")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_tabla(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /tabla <liga_slug> [jornada] — Genera y publica la tabla de clasificación como imagen.
    Ejemplo: /tabla esp.1
             /tabla eng.1 30
    Usa el slug de ESPN (ej: esp.1, eng.1, ger.1, ita.1, fra.1, uefa.champions).
    """
    args = ctx.args
    if not args:
        ligas = "\n".join([f"  {slug} → {name}" for name, slug in list(ESPN_LEAGUES.items())[:15]])
        await update.message.reply_text(
            "Uso: /tabla <slug>\n\nSlug de liga (ESPN):\n" + ligas + "\n  ...",
        )
        return

    slug    = args[0].strip()
    jornada = args[1] if len(args) > 1 else None

    msg = await update.message.reply_text(f"Generando tabla de {slug}...")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(_executor, _fetch_standings_data, slug, jornada)
    except Exception as exc:
        await msg.edit_text(f"Error obteniendo tabla: {exc}")
        return

    if not result:
        await msg.edit_text(f"No se pudo obtener la tabla para: {slug}")
        return

    entries, league_name, week_num = result
    await msg.edit_text("Generando imagen...")

    try:
        img_path = await loop.run_in_executor(
            _executor, generate_standings_image, slug, entries, league_name, week_num
        )
    except Exception as exc:
        await msg.edit_text(f"Error generando imagen: {exc}")
        return

    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
    caption = _build_standings_caption(slug, entries, league_name)

    try:
        await app_ref.bot.send_photo(
            chat_id=dest,
            photo=open(img_path, "rb"),
            caption=caption,
            parse_mode="Markdown",
        )
        await msg.edit_text("✅ Tabla publicada en el canal.")
    except Exception as exc:
        await msg.edit_text(f"Error publicando: {exc}")


def _build_standings_caption(slug: str, entries: list, league_name: str) -> str:
    """
    Genera el caption de la tabla con el formato exacto:

    *📊 | TABLA DE CLASIFICACIÓN*

    *🏆 Primer Lugar: Barcelona*
    *🔵 UCL: Real Madrid, Villarreal, Atlético Madrid*
    *🟠 UEL: Real Betis*
    [*⭕️ Play-offs de descenso: Elche*]
    *🔴 Descenso: Levante, Real Oviedo*

    *📲 Suscríbete en t.me/iUniversoFootball*
    """
    n = len(entries)
    lines = ["*📊 | TABLA DE CLASIFICACIÓN*", ""]

    # Ligas con playoff de descenso: Bundesliga y Ligue 1
    HAS_PLAYOFF = {"ger.1", "fra.1"}

    if slug == "eng.1":
        # Premier League: 5 cupos UCL esta temporada
        ucl_spots  = 5
        uel_spots  = 1    # posición 6
        uecl_spots = 1    # posición 7
        rel_spots  = 3
        playoff    = False
    elif slug in ("esp.1", "ger.1", "ita.1", "fra.1"):
        ucl_spots  = 4
        uel_spots  = 1    # posición 5
        uecl_spots = 1    # posición 6
        rel_spots  = 3
        playoff    = slug in HAS_PLAYOFF
    elif slug in ("por.1", "ned.1", "tur.1"):
        ucl_spots  = 1
        uel_spots  = 1
        uecl_spots = 1
        rel_spots  = 2
        playoff    = False
    else:
        ucl_spots = uel_spots = uecl_spots = rel_spots = 0
        playoff = False

    if ucl_spots and n >= ucl_spots:
        lines.append(f"*🏆 Primer Lugar: {entries[0]['name']}*")

        ucl_teams = [entries[i]["name"] for i in range(1, ucl_spots)]
        if ucl_teams:
            lines.append(f"*🔵 UCL: {', '.join(ucl_teams)}*")

        if uel_spots and ucl_spots < n:
            uel_name = entries[ucl_spots]["name"]
            lines.append(f"*🟠 UEL: {uel_name}*")

        if uecl_spots and ucl_spots + uel_spots < n:
            uecl_name = entries[ucl_spots + uel_spots]["name"]
            lines.append(f"*🟢 UECL: {uecl_name}*")

        rel_start = n - rel_spots
        # Playoff: la posición justo antes del descenso directo
        if playoff and rel_start > 0:
            playoff_idx = rel_start - 1
            if playoff_idx < n:
                lines.append(f"*⭕️ Play-offs de descenso: {entries[playoff_idx]['name']}*")

        if rel_spots:
            rel_teams = [entries[i]["name"] for i in range(rel_start, n)]
            lines.append(f"*🔴 Descenso: {', '.join(rel_teams)}*")

        lines.append("")

    lines.append("*📲 Suscríbete en t.me/iUniversoFootball*")
    return "\n".join(lines)


def _fetch_standings_data(slug: str, jornada: str = None) -> Optional[tuple]:
    """
    Consulta la tabla de clasificación de ESPN.
    Devuelve (entries, league_name, week_num) o None si falla.
    """
    url = f"https://site.web.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
    try:
        r = requests.get(url, headers=ESPN_HEADERS, timeout=12)
        if r.status_code != 200:
            logger.warning("ESPN standings HTTP %s para %s", r.status_code, slug)
            return None
        data = r.json()
    except Exception as exc:
        logger.error("ESPN standings error: %s", exc)
        return None

    league_name = (data.get("name") or
                   data.get("abbreviation") or
                   next((n for n, s in ESPN_LEAGUES.items() if s == slug), slug))

    season   = data.get("season", {})
    week_num = jornada or str(season.get("week", {}).get("number", ""))

    entries = []
    groups  = data.get("children", [data])
    for group in groups:
        for entry in group.get("standings", {}).get("entries", []):
            team_name = entry.get("team", {}).get("displayName", "?")
            stats_raw = {s["name"]: s for s in entry.get("stats", [])}

            def sv(key: str, alt: str = "0") -> float:
                s = stats_raw.get(key, {})
                try:
                    return float(s.get("value", s.get("displayValue", alt)) or 0)
                except (ValueError, TypeError):
                    return 0.0

            pts = int(sv("points"))
            pj  = int(sv("gamesPlayed"))
            v   = int(sv("wins"))
            e   = int(sv("ties"))
            d   = int(sv("losses"))
            gf  = int(sv("pointsFor"))
            gc  = int(sv("pointsAgainst"))
            dg  = int(sv("pointDifferential", str(gf - gc)))

            entries.append({
                "name": team_name,
                "pts": pts, "pj": pj,
                "v": v, "e": e, "d": d,
                "gf": gf, "gc": gc, "dg": dg,
            })

    if not entries:
        return None

    entries.sort(key=lambda x: (-x["pts"], -x["dg"], -x["gf"]))
    return entries, league_name, week_num


# ── Resultados del día por liga (imagen) ──────────────────────────────────────

# País/bandera por slug para la imagen de resultados
_SLUG_COUNTRY = {
    "esp.1": ("España", "🇪🇸"), "esp.copa_del_rey": ("España", "🇪🇸"), "esp.super_cup": ("España", "🇪🇸"),
    "eng.1": ("Inglaterra", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"), "eng.fa": ("Inglaterra", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"), "eng.league_cup": ("Inglaterra", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"), "eng.community_shield": ("Inglaterra", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    "ger.1": ("Alemania", "🇩🇪"), "ger.dfb_pokal": ("Alemania", "🇩🇪"), "ger.super_cup": ("Alemania", "🇩🇪"),
    "ita.1": ("Italia", "🇮🇹"), "ita.coppa_italia": ("Italia", "🇮🇹"), "ita.super_cup": ("Italia", "🇮🇹"),
    "fra.1": ("Francia", "🇫🇷"), "fra.coupe_de_france": ("Francia", "🇫🇷"), "fra.trophee_champions": ("Francia", "🇫🇷"),
    "por.1": ("Portugal", "🇵🇹"), "ned.1": ("Países Bajos", "🇳🇱"), "tur.1": ("Turquía", "🇹🇷"),
    "uefa.champions": ("Europa", "🇪🇺"), "uefa.europa": ("Europa", "🇪🇺"), "uefa.europa.conf": ("Europa", "🇪🇺"),
    "uefa.super_cup": ("Europa", "🇪🇺"), "uefa.nations": ("Europa", "🇪🇺"), "uefa.euro": ("Europa", "🇪🇺"),
    "conmebol.libertadores": ("Sudamérica", "🌎"), "conmebol.sudamericana": ("Sudamérica", "🌎"),
    "conmebol.recopa": ("Sudamérica", "🌎"), "conmebol.america": ("América", "🌎"),
    "fifa.conmebol.worldq": ("Sudamérica", "🌎"), "fifa.uefa.worldq": ("Europa", "🇪🇺"),
    "fifa.concacaf.worldq": ("CONCACAF", "🌎"), "fifa.afc.worldq": ("Asia", "🌏"),
    "fifa.caf.worldq": ("África", "🌍"), "fifa.cwc": ("Mundial", "🌐"),
    "fifa.world": ("Mundial", "🌐"), "fifa.friendly": ("Internacional", "🌐"),
    "club.friendly": ("Internacional", "🌐"), "fifa.wcq.ply": ("Internacional", "🌐"),
}


@admin_only
async def cmd_rd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /rd <slug> [fecha] — Resultados del día de una liga específica, en imagen.
    Ejemplo: /rd esp.1
             /rd eng.1 2026-04-06
             /rd ger.1 20260406
    """
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Uso: /rd <slug> [fecha]\n\n"
            "Ejemplos:\n  /rd esp.1\n  /rd eng.1 2026-04-06\n\n"
            "Usa /ligas para ver todos los slugs disponibles."
        )
        return

    slug = args[0].strip()

    # Parsear fecha opcional
    if len(args) > 1:
        raw_date = args[1].strip().replace("-", "")
        if len(raw_date) == 8 and raw_date.isdigit():
            date_str = raw_date
        else:
            await update.message.reply_text(
                "Fecha inválida. Usa formato YYYY-MM-DD o YYYYMMDD.\nEjemplo: /rd esp.1 2026-04-06"
            )
            return
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

    date_display = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    league_display = next((n for n, s in ESPN_LEAGUES.items() if s == slug), slug)
    country_name, country_flag = _SLUG_COUNTRY.get(slug, ("Internacional", "🌐"))

    # Hashtag de la liga
    league_tag = "#" + "".join(w.capitalize() for w in league_display.replace("-", " ").split())

    msg = await update.message.reply_text(
        f"Buscando resultados de {league_display} — {date_display}..."
    )

    loop = asyncio.get_running_loop()

    # ESPN scoreboard con la fecha exacta pedida
    try:
        events = await loop.run_in_executor(_executor, _fetch_scoreboard, slug, date_str)
    except Exception as exc:
        await msg.edit_text(f"Error consultando ESPN: {exc}")
        return

    # Si ESPN no devuelve nada para esa fecha, intentar sin ?dates= (jornada actual)
    if not events:
        try:
            events = await loop.run_in_executor(_executor, _fetch_scoreboard, slug, None)
            # Filtrar solo los de la fecha pedida
            target_date = datetime.strptime(date_str, "%Y%m%d").date()
            filtered = []
            for ev in events:
                raw = ev.get("date", "")
                if raw:
                    try:
                        ev_date = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
                        if ev_date == target_date:
                            filtered.append(ev)
                    except Exception:
                        pass
            events = filtered
        except Exception:
            pass

    if not events:
        await msg.edit_text(
            f"No se encontraron partidos de {league_display} el {date_display}."
        )
        return

    await msg.edit_text("Generando imagen...")

    # Construir lista de resultados para la imagen
    results_data = []
    for ev in events:
        p      = parse_event(ev)
        status = p["status_type"]
        if status in ESPN_FINAL:
            suffix = ""
            if status == "STATUS_FINAL_AET":
                suffix = "AET"
            elif status in ESPN_PENALTIES:
                suffix = "PEN"
            results_data.append({
                "home": p["home_name"], "away": p["away_name"],
                "hs": p["home_score"],  "as_": p["away_score"],
                "state": "final", "suffix": suffix,
                "clock": "",
            })
        elif status in ESPN_LIVE:
            clock = p["clock"] if p["clock"] and p["clock"] != "0" else ""
            results_data.append({
                "home": p["home_name"], "away": p["away_name"],
                "hs": p["home_score"],  "as_": p["away_score"],
                "state": "live", "suffix": "",
                "clock": clock,
            })
        else:
            hora = p["kickoff_str"] or "--:--"
            results_data.append({
                "home": p["home_name"], "away": p["away_name"],
                "hs": None, "as_": None,
                "state": "scheduled", "suffix": "",
                "clock": hora,
            })

    try:
        from results_image_generator import generate_results_image
        img_path = await loop.run_in_executor(
            _executor, generate_results_image,
            results_data, league_display, country_name, country_flag,
            date_display, league_tag,
        )
    except Exception as exc:
        await msg.edit_text(f"Error generando imagen: {exc}")
        return

    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
    caption = (
        f"*🗓 | RESULTADOS*\n"
        f"*{country_flag} | {country_name}*\n\n"
        f"*{league_tag}*\n\n"
        f"*📲 Suscríbete en t.me/iUniversoFootball*"
    )
    try:
        await app_ref.bot.send_photo(
            chat_id=dest,
            photo=open(img_path, "rb"),
            caption=caption,
            parse_mode="Markdown",
        )
        await msg.edit_text("✅ Resultados publicados en el canal.")
    except Exception as exc:
        await msg.edit_text(f"Error publicando: {exc}")


# Variable global para referencia a la app (se asigna en main())
app_ref = None


@admin_only
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text("Uso: /stop <event_id>")
        return
    fid = args[0]
    if fid in tracked:
        tracked.pop(fid)
        await update.message.reply_text(f"Partido {fid} removido.")
    else:
        await update.message.reply_text(f"Partido {fid} no estaba activo.")


@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if args:
        await _run_test(args[0], None, update.message)
        return

    msg = await update.message.reply_text("Buscando partidos finalizados hoy...")
    try:
        all_events = await fetch_all_today()
    except Exception:
        await msg.edit_text("Error consultando ESPN.")
        return

    finished = [e for e in all_events if parse_event(e)["status_type"] in ESPN_FINAL]
    if not finished:
        await msg.edit_text("No hay partidos finalizados hoy.\n\nUsa: /test <event_id>")
        return

    await msg.delete()
    keyboard = []
    for ev in finished:
        p = parse_event(ev)
        label = f"{p['home_name']} {p['home_score']}-{p['away_score']} {p['away_name']} ({p['league']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"tst:{p['id']}:{ev['_slug']}")])

    await update.message.reply_text(
        "Preview del post final - Selecciona un partido:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _run_test(event_id: str, slug: Optional[str], message: Message):
    status_msg = await message.reply_text(f"Obteniendo datos de {event_id}...")

    if not slug:
        try:
            evs = await fetch_all_today()
            match = next((e for e in evs if e["id"] == event_id), None)
            slug = match.get("_slug", "esp.1") if match else "esp.1"
        except Exception:
            slug = "esp.1"

    summary = await fetch_summary(slug, event_id)
    if not summary:
        await status_msg.edit_text("No se encontro el partido en ESPN.")
        return

    header  = summary.get("header", {})
    comp    = (header.get("competitions") or [{}])[0]
    comps   = comp.get("competitors", [])
    home_c  = next((c for c in comps if c.get("homeAway") == "home"), {})
    away_c  = next((c for c in comps if c.get("homeAway") == "away"), {})

    home_name  = home_c.get("team", {}).get("displayName", "?")
    away_name  = away_c.get("team", {}).get("displayName", "?")
    home_logo  = home_c.get("team", {}).get("logo", "")
    away_logo  = away_c.get("team", {}).get("logo", "")
    home_score = int(home_c.get("score", 0) or 0)
    away_score = int(away_c.get("score", 0) or 0)
    league_n   = header.get("league", {}).get("name", slug)
    st_name    = comp.get("status", {}).get("type", {}).get("name", "")
    st_desc    = comp.get("status", {}).get("type", {}).get("description", "")

    warn = f"\n\nAtencion: Estado {st_desc} - el post es preview." if st_name not in ESPN_FINAL else ""
    await status_msg.edit_text(f"{home_name} {home_score}-{away_score} {away_name} | {league_n} - {st_desc}{warn}\n\nGenerando imagen...")

    fd = {
        "fixture": {"id": event_id},
        "league":  {"name": league_n},
        "teams": {
            "home": {"name": home_name, "logo": home_logo},
            "away": {"name": away_name, "logo": away_logo},
        },
        "goals": {"home": home_score, "away": away_score},
    }
    # Sofascore primero; fallback a ESPN
    loop = asyncio.get_running_loop()
    raw_stats = await loop.run_in_executor(
        _executor, sofascore_raw_stats, home_name, away_name, None,
    )
    if raw_stats is None:
        logger.info("Sofascore sin datos en /test, usando ESPN.")
        raw_stats = build_raw_stats_from_espn(summary)
    else:
        logger.info("Stats de /test obtenidas desde Sofascore.")

    img_path = None
    try:
        from image_generator import generate_match_summary
        img_path = await loop.run_in_executor(_executor, generate_match_summary, fd, raw_stats)
    except Exception as exc:
        logger.error("Error imagen test: %s", exc)

    text = (
        "--- PREVIEW POST FINAL ---\n"
        f"Event ID: {event_id}\n"
        "-------------------------\n\n"
        + msg_final(home_name, away_name, home_score, away_score)
    )

    try:
        if img_path and os.path.exists(img_path):
            await message.reply_photo(open(img_path, "rb"), caption=text, parse_mode="Markdown")
        else:
            await message.reply_text(text + "\n\n(Sin imagen)", parse_mode="Markdown")
        await status_msg.delete()
    except Exception as exc:
        await status_msg.edit_text(f"Error: {exc}")


# ── Callbacks inline ───────────────────────────────────────────────────────────

async def cb_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Responder INMEDIATAMENTE — Telegram invalida el query tras 60s
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    parts = query.data.split(":")
    fid   = parts[1]
    slug  = parts[2] if len(parts) > 2 else ""

    if fid in tracked:
        # ── Desactivar — instantáneo, sin HTTP ────────────────────────────
        tracked.pop(fid)
    else:
        # ── Activar — usar cache si existe, ESPN solo si no ───────────────
        raw = _events_cache.get(fid)
        if raw is None:
            # No estaba en cache (raro), consultar ESPN como fallback
            try:
                events = await fetch_scoreboard(slug)
                raw = next((e for e in events if e["id"] == fid), None)
                if raw:
                    _events_cache[fid] = raw
            except Exception as exc:
                logger.error("cb_toggle fetch error: %s", exc)

        if not raw:
            try:
                await query.edit_message_text(
                    "No se encontró el partido. Refresca con /partidos."
                )
            except Exception:
                pass
            return

        raw.setdefault("_slug",   slug)
        raw.setdefault("_league", next((n for n, s in ESPN_LEAGUES.items() if s == slug), slug))
        p = parse_event(raw)

        tracked[fid] = TrackedFixture(
            fixture_id    = fid,
            league_slug   = slug,
            home_name     = p["home_name"],
            away_name     = p["away_name"],
            league_name   = raw["_league"],
            kickoff_utc   = p["kickoff_utc"],
            home_score    = p["home_score"],
            away_score    = p["away_score"],
            status        = p["status_type"],
            _sofascore_id = None,   # se busca en background abajo
        )

        # Sofascore ID se buscará en el primer ciclo del monitor_loop
        # No hacerlo aquí para no ralentizar la respuesta del botón

    # ── Refrescar teclado desde cache — instantáneo, sin HTTP ─────────────
    try:
        # Agrupar los eventos del mismo slug que hay en cache
        slug_events = [ev for ev in _events_cache.values()
                       if ev.get("_slug") == slug]
        if slug_events:
            keyboard = []
            for ev in slug_events:
                p   = parse_event(ev)
                act = "✅ " if p["id"] in tracked else ""
                hora = p["kickoff_str"] or "--:--"
                label = f"{act}{hora} | {p['home_name']} vs {p['away_name']} ({p['home_score']}-{p['away_score']}) {p['status_desc']}"
                keyboard.append([InlineKeyboardButton(label, callback_data=f"tog:{p['id']}:{slug}")])
            await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
    except Exception:
        pass


async def cb_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()   # responder inmediatamente
    if query.from_user.id != ADMIN_ID:
        return
    parts = query.data.split(":")
    await _run_test(parts[1], parts[2] if len(parts) > 2 else None, query.message)



@admin_only
async def cmd_lineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /lineup <event_id> — Fuerza el envío inmediato de las alineaciones
    de un partido activo al canal. Útil si el loop automático no las envió.
    Sin argumentos muestra los partidos activos para seleccionar.
    """
    if ctx.args:
        fid = ctx.args[0]
        fix = tracked.get(fid)
        if not fix:
            await update.message.reply_text(f"Partido {fid} no está en monitoreo.")
            return
        msg = await update.message.reply_text(f"Obteniendo alineaciones de {fix.home_name} vs {fix.away_name}...")
        summary = await fetch_summary(fix.league_slug, fid)
        if not summary:
            await msg.edit_text("No se pudo obtener el summary de ESPN.")
            return
        home_xi, away_xi, home_formation, away_formation, home_logo_url, away_logo_url = parse_lineups(summary)
        if len(home_xi) < 11 or len(away_xi) < 11:
            await msg.edit_text(
                f"Alineaciones incompletas: {fix.home_name} ({len(home_xi)}) vs {fix.away_name} ({len(away_xi)})\n"
                f"ESPN aún no publicó los XI titulares."
            )
            return
        caption = msg_lineup(fix.league_name, fix.home_name, fix.away_name, home_xi, away_xi)
        dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
        await _send_lineup_images(
            ctx.application, dest,
            fix.home_name, fix.away_name,
            home_xi, away_xi,
            home_formation, away_formation,
            home_logo_url, away_logo_url,
            fix.league_name, fid, caption,
        )
        fix.lineup_sent = True
        await msg.edit_text(f"✅ Alineaciones enviadas: {fix.home_name} vs {fix.away_name}")
        return

    # Sin args: mostrar botones con partidos activos
    if not tracked:
        await update.message.reply_text("No hay partidos en monitoreo.")
        return
    keyboard = []
    for fid, fix in tracked.items():
        sent = "✓" if fix.lineup_sent else "⏳"
        label = f"{sent} {fix.home_name} vs {fix.away_name} ({fix.league_name})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"lup:{fid}")])
    await update.message.reply_text(
        "Selecciona partido para forzar alineaciones:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cb_lineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback del botón inline de /lineup."""
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    fid = query.data.split(":")[1]
    fix = tracked.get(fid)
    if not fix:
        try:
            await query.edit_message_text("Partido no encontrado en monitoreo.")
        except Exception:
            pass
        return

    msg = await query.message.reply_text(
        f"Obteniendo alineaciones de {fix.home_name} vs {fix.away_name}..."
    )
    summary = await fetch_summary(fix.league_slug, fid)
    if not summary:
        await msg.edit_text("No se pudo obtener datos de ESPN.")
        return

    home_xi, away_xi, home_f, away_f, home_logo, away_logo = parse_lineups(summary)
    if len(home_xi) < 11 or len(away_xi) < 11:
        await msg.edit_text(
            f"Alineaciones incompletas: {fix.home_name} ({len(home_xi)}) "
            f"vs {fix.away_name} ({len(away_xi)}). ESPN aún no las publicó."
        )
        return

    await msg.edit_text("Generando imágenes...")
    caption = msg_lineup(fix.league_name, fix.home_name, fix.away_name, home_xi, away_xi)
    dest    = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
    await _send_lineup_images(
        ctx.application, dest,
        fix.home_name, fix.away_name,
        home_xi, away_xi, home_f, away_f,
        home_logo, away_logo,
        fix.league_name, fid, caption,
    )
    fix.lineup_sent = True
    await msg.edit_text(f"✅ Alineaciones enviadas al canal.")


@admin_only
async def cmd_testlineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /testlineup — Genera y envía al PRIVADO del admin (no al canal)
    una preview de las imágenes de alineación con datos de ejemplo.
    Útil para verificar el diseño sin publicar nada.
    """
    msg = await update.message.reply_text("Generando imágenes de alineación de prueba...")

    home_xi = ["Ter Stegen", "Kounde", "Araujo", "I. Martinez", "Balde",
               "Pedri", "Casado", "Gavi", "Yamal", "Lewandowski", "Raphinha"]
    away_xi = ["Lunin", "Carvajal", "Militao", "Rudiger", "Mendy",
               "Valverde", "Tchouameni", "Camavinga", "Bellingham",
               "Vinicius Jr.", "Mbappe"]

    caption = msg_lineup("La Liga", "FC Barcelona", "Real Madrid", home_xi, away_xi)

    try:
        await _send_lineup_images(
            ctx.application, update.effective_user.id,
            "FC Barcelona", "Real Madrid",
            home_xi, away_xi,
            "4-3-3", "4-3-3",
            "", "",
            "La Liga", "testlineup",
            caption,
        )
        await msg.delete()
    except Exception as exc:
        await msg.edit_text(f"Error generando preview: {exc}")


@admin_only
async def cmd_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /preview — Envía al canal un mensaje de alineaciones y uno de gol de prueba,
    para verificar que el formato se ve bien antes de un partido real.
    """
    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID

    # ── Datos ficticios ────────────────────────────────────────────────────
    home  = "Real Madrid"
    away  = "FC Barcelona"
    league = "La Liga"

    home_xi = [
        "Lunin", "Carvajal", "Militao", "Rudiger", "Mendy",
        "Valverde", "Camavinga", "Bellingham", "Rodrygo",
        "Vinicius Jr.", "Mbappé",
    ]
    away_xi = [
        "Ter Stegen", "Koundé", "Araujo", "I. Martínez", "Balde",
        "Pedri", "Casadó", "Gavi", "Yamal",
        "Lewandowski", "Raphinha",
    ]

    # ── 1. Alineaciones ────────────────────────────────────────────────────
    text_lineup = msg_lineup(league, home, away, home_xi, away_xi)
    try:
        await ctx.bot.send_message(chat_id=dest, text=text_lineup, parse_mode="Markdown", disable_web_page_preview=True)
        await asyncio.sleep(1)
    except Exception as exc:
        logger.error("cmd_preview error enviando alineaciones: %s", exc)
        await update.message.reply_text(f"Error enviando alineaciones: {exc}")
        return

    # ── 2. Gol ─────────────────────────────────────────────────────────────
    text_goal = msg_goal(
        home, away,
        hs=1, as_=0,
        league=league,
        scorer="Vinicius Jr.",
        assist="Bellingham",
        side="home",
        elapsed="34",
    )
    try:
        await ctx.bot.send_message(chat_id=dest, text=text_goal, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as exc:
        logger.error("cmd_preview error enviando gol: %s", exc)
        await update.message.reply_text(f"Error enviando gol: {exc}")
        return

    destino = "el canal" if CHANNEL_ID else "tu DM"
    await update.message.reply_text(
        f"✅ Preview enviado a {destino}:\n"
        f"• Alineaciones ({home} vs {away})\n"
        f"• Gol de prueba (Vinicius Jr. 34')"
    )


# ══════════════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════

async def post_init(app: Application):
    app.create_task(monitor_loop(app))
    app.create_task(lineup_loop(app))
    logger.info("Loops iniciados.")


def main():
    global app_ref
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app_ref = app
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("partidos", cmd_partidos))
    app.add_handler(CommandHandler("activos",  cmd_activos))
    app.add_handler(CommandHandler("stop",     cmd_stop))
    app.add_handler(CommandHandler("rf",       cmd_rf))
    app.add_handler(CommandHandler("tabla",    cmd_tabla))
    app.add_handler(CommandHandler("rd",       cmd_rd))
    app.add_handler(CommandHandler("ligas",    cmd_ligas))
    app.add_handler(CommandHandler("test",     cmd_test))
    app.add_handler(CommandHandler("preview",     cmd_preview))
    app.add_handler(CommandHandler("lineup",      cmd_lineup))
    app.add_handler(CommandHandler("testlineup",  cmd_testlineup))
    app.add_handler(CallbackQueryHandler(cb_lineup, pattern=r"^lin:"))
    app.add_handler(CommandHandler("lineup",      cmd_lineup))
    app.add_handler(CallbackQueryHandler(cb_lineup, pattern=r"^lup:"))
    app.add_handler(CallbackQueryHandler(cb_rf,    pattern=r"^rf:"))
    app.add_handler(CommandHandler("espn",     cmd_espn))
    app.add_handler(CommandHandler("debug",    cmd_debug))
    app.add_handler(CommandHandler("debug",    cmd_debug))
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^tog:"))
    app.add_handler(CallbackQueryHandler(cb_test,   pattern=r"^tst:"))

    logger.info("Bot iniciado. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
