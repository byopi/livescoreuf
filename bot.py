"""
Livescore Bot — Universo Football
ESPN unofficial API · python-telegram-bot v21+ · Python 3.12+
Integración con TheSportsDB para rescatar partidos en Fecha FIFA.
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
from sofascore_stats import sofascore_raw_stats, _get as sofascore_get
from fotmob_stats import (
    get_scorer_assist, find_fotmob_match_id,
    get_fotmob_livescore, fotmob_raw_stats,
)
from lineup_image_generator import generate_lineup_images
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
    # ── Selecciones ────────────────────────────────────────────────────────
    "Nations League":           "uefa.nations",
    "Eurocopa":                 "uefa.euro",
    "Copa América":             "conmebol.america",
    # ── Mundial 2026 (clasificatorias + torneo) ────────────────────────────
    "Clasificación UEFA":       "fifa.uefa.worldq",
    "Clasificación CONMEBOL":   "fifa.worldq.conmebol",
    "Clasificación CONCACAF":   "concacaf.worldq",
    "Clasificación AFC":        "afc.worldq",
    "Clasificación CAF":        "caf.worldq",
    "Mundial de Clubes FIFA":   "fifa.cwc",
    "Mundial FIFA 2026":        "fifa.world",
    "Amistosos Internacionales":"fifa.friendly",
}

ESPN_FINAL  = {"STATUS_FINAL", "STATUS_FULL_TIME"}
ESPN_LIVE   = {"STATUS_IN_PROGRESS", "STATUS_HALFTIME"}

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
    _fotmob_id:     Optional[int] = None


# ─── Estado global ─────────────────────────────────────────────────────────────
tracked:       dict[str, TrackedFixture] = {}
pending_goals: list[PendingGoal]         = []
resolved_kev:  dict[str, set]            = {}

# Cache de eventos del día
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
    params = {}
    if date:
        params["dates"] = date
    data = _espn_get(ESPN_SCOREBOARD.format(league=slug), params=params or None)
    return data.get("events", []) if data else []


def _fetch_summary(slug: str, event_id: str) -> Optional[dict]:
    return _espn_get(ESPN_SUMMARY.format(league=slug), params={"event": event_id})


def _fetch_all_today() -> list[dict]:
    now_utc   = datetime.now(timezone.utc)
    # Consultamos un rango de 3 días para capturar partidos por zona horaria
    dates_to_query = [
        (now_utc - timedelta(days=1)).strftime("%Y%m%d"),
        now_utc.strftime("%Y%m%d"),
        (now_utc + timedelta(days=1)).strftime("%Y%m%d"),
    ]

    results = []
    seen    = set()

    for league_name, slug in ESPN_LEAGUES.items():
        for date_str in dates_to_query:
            events = _fetch_scoreboard(slug, date=date_str)
            for ev in events:
                if ev["id"] not in seen:
                    seen.add(ev["id"])
                    ev["_slug"]   = slug
                    ev["_league"] = league_name
                    results.append(ev)
    
    logger.info(f"ESPN: {len(results)} partidos encontrados en total.")
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

    # Manejo SEGURO de fechas
    raw_date = ev.get("date", "")
    kickoff_utc = None
    kickoff_str = "--:--"
    
    if raw_date:
        try:
            # Convertimos el string de ESPN a objeto datetime
            kickoff_utc = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
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
    
    # ESPN normal
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


def parse_goal_event(ev: dict) -> tuple[str, str]:
    scorer = assist = ""
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
            scorer = "Autogol"
        else:
            m = re.match(r"^([\w\s.\-'áéíóúñÁÉÍÓÚÑ]+?)\s+\d+[''']", raw)
            if m:
                scorer = m.group(1).strip()
    if not assist and raw:
        m = re.search(r"[Aa]ssist[e]?[:\s]+([\w\s.\-'áéíóúñÁÉÍÓÚÑ]+?)[\),\n]", raw)
        if m:
            assist = m.group(1).strip()

    return scorer or "", assist or ""


def parse_key_events(summary: dict) -> list[dict]:
    return [
        ev for ev in summary.get("keyEvents", [])
        if "goal" in (ev.get("type", {}).get("text") or "").lower()
    ]


def parse_lineups(summary: dict) -> tuple[list[str], list[str], str, str, str, str]:
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

        formation = roster.get("formation", "")
        if formation:
            if i == 0:
                home_formation = formation
            else:
                away_formation = formation

        team_data = roster.get("team", {})
        logo = team_data.get("logo", "")
        if not logo:
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
             side: str = "", elapsed: str = "") -> str:
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

    lines.append(f"⚽ {scorer}")

    if assist and assist != "-":
        lines.append(f"🅰️ {assist}")

    lines += ["", "*📲 Suscribete en t.me/iUniversoFootball*"]
    return "\n".join(lines)


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

_resolving: set[str] = set()


async def _resolve_goal(app: Application, pg: PendingGoal):
    loop     = asyncio.get_running_loop()
    seen     = resolved_kev.setdefault(pg.fixture_id, set())
    elapsed  = 0
    interval = RESOLVE_INTERVAL

    logger.info("Resolviendo gol: %s vs %s min %s",
                pg.home_name, pg.away_name, pg.elapsed)

    while elapsed < RESOLVE_TIMEOUT and not pg.resolved:
        try:
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
                    logger.info("FotMob resolvio: '%s' asiste '%s'", scorer, assist or "-")
                    break
            except Exception as exc:
                logger.debug("FotMob error en resolver gol: %s", exc)

            if not pg.resolved:
                try:
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
                except Exception as exc:
                    logger.debug("ESPN error en resolver gol: %s", exc)

        except Exception as exc:
            logger.warning("_resolve_goal error: %s", exc)

        if pg.resolved:
            break
        await asyncio.sleep(interval)
        elapsed += interval

    if pg.resolved and pg.scorer and pg.scorer not in ("-", "Obteniendo..."):
        text = msg_goal(
            pg.home_name, pg.away_name,
            pg.home_score, pg.away_score,
            pg.league_name, pg.scorer, pg.assist,
            pg.goal_side, pg.elapsed,
        )
        if pg.tg_message:
            try:
                await pg.tg_message.edit_text(
                    text, parse_mode="Markdown",
                    link_preview_options=_NO_PREVIEW,
                )
            except BadRequest:
                pass
            except Exception as exc:
                logger.error("Error editando mensaje: %s", exc)
    else:
        pg.resolved = True

    _resolving.discard(pg.fixture_id + pg.elapsed)


async def monitor_loop(app: Application):
    logger.info("monitor_loop iniciado (poll cada %ds)", POLL_INTERVAL)
    loop = asyncio.get_running_loop()
    
    # Configuración de Auto-Ping para Render
    port = os.getenv("PORT", "8000")
    health_url = f"http://localhost:{port}"

    while True:
        try:
            # ─── SISTEMA DE INMORTALIDAD (SELF-PING) ───
            # Engañamos a Render generando tráfico interno al server.py
            # Esto evita que la instancia entre en "Sleep Mode"
            try:
                # Usamos run_in_executor para no bloquear el loop con requests
                await loop.run_in_executor(None, requests.get, health_url, {"timeout": 5})
                logger.debug("Self-ping exitoso a %s", health_url)
            except Exception as e:
                logger.debug("Error en self-ping: %s", e)

            # Espera el intervalo de sondeo
            await asyncio.sleep(POLL_INTERVAL)

            # ─── MONITOREO DE PARTIDOS ───
            for fid, fix in list(tracked.items()):
                if fix.finished:
                    continue
                
                try:
                    raw = None
                    # 1. Intentar con FotMob (Prioridad)
                    try:
                        raw = await loop.run_in_executor(
                            _executor, get_fotmob_livescore,
                            fix.home_name, fix.away_name, None,
                        )
                        if raw:
                            raw["_slug"]   = fix.league_slug
                            raw["_league"] = fix.league_name
                    except Exception as exc:
                        logger.debug("FotMob livescore error: %s", exc)

                    # 2. Fallback a ESPN Scoreboard
                    if not raw:
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

                    # Detección de cambios de marcador
                    if new_h != fix.home_score or new_a != fix.away_score:
                        dh = new_h - fix.home_score
                        da = new_a - fix.away_score
                        side = "home" if dh > 0 and da == 0 else "away" if da > 0 and dh == 0 else ""
                        
                        fix.home_score = new_h
                        fix.away_score = new_a

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
                                asyncio.create_task(_resolve_goal(app, pg))
                            except Exception as exc:
                                logger.error("Error enviando gol: %s", exc)

                    # Detección de Final del Partido
                    if status in ESPN_FINAL and not fix.finished:
                        fix.finished = True
                        summary = await fetch_summary(fix.league_slug, fid)

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
                                
                                fm_id = await loop.run_in_executor(_executor, find_fotmob_match_id, fix.home_name, fix.away_name)
                                raw_stats = await loop.run_in_executor(_executor, fotmob_raw_stats, fix.home_name, fix.away_name, fm_id)
                                
                                if raw_stats is None:
                                    raw_stats = build_raw_stats_from_espn(summary)
                                    
                                from image_generator import generate_match_summary
                                img_path = await loop.run_in_executor(_executor, generate_match_summary, fd, raw_stats)
                            except Exception as exc:
                                logger.error("Error generando imagen final: %s", exc)

                        text = msg_final(fix.home_name, fix.away_name, fix.home_score, fix.away_score)
                        dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                        try:
                            if img_path and os.path.exists(img_path):
                                with open(img_path, "rb") as f:
                                    await app.bot.send_photo(chat_id=dest, photo=f, caption=text, parse_mode="Markdown")
                            else:
                                await app.bot.send_message(chat_id=dest, text=text, parse_mode="Markdown", disable_web_page_preview=True)
                        except Exception as exc:
                            logger.error("Error enviando mensaje final: %s", exc)

                        tracked.pop(fid, None)

                except Exception as exc:
                    logger.error("Error en proceso de fixture %s: %s", fid, exc)

        except Exception as global_exc:
            # ─── BLINDAJE TOTAL ───
            # Si hay un error de red masivo, el loop no se rompe
            logger.critical("FALLO GLOBAL EN MONITOR_LOOP: %s. Reintentando ciclo...", global_exc)
            await asyncio.sleep(10)


async def lineup_loop(app: Application):
    logger.info("lineup_loop iniciado")
    while True:
        await asyncio.sleep(LINEUP_INTERVAL)
        now = datetime.now(timezone.utc)

        for fid, fix in list(tracked.items()):
            if fix.lineup_sent or fix.finished:
                continue
            if fix.kickoff_utc is not None:
                mins = (fix.kickoff_utc - now).total_seconds() / 60
                if not (-30 <= mins <= 90):
                    continue
            else:
                if fix.status not in ESPN_LIVE:
                    continue

            try:
                summary = await fetch_summary(fix.league_slug, fid)
                if not summary:
                    continue
                home_xi, away_xi, home_formation, away_formation, home_logo_url, away_logo_url = parse_lineups(summary)
                if len(home_xi) < 11 or len(away_xi) < 11:
                    fix.lineup_tries += 1
                    if fix.lineup_tries >= 20:
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
    from telegram import InputMediaPhoto
    loop = asyncio.get_running_loop()
    try:
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
        with open(path_home, "rb") as f_home, open(path_away, "rb") as f_away:
            media = [
                InputMediaPhoto(media=f_home.read()),
                InputMediaPhoto(media=f_away.read(), caption=caption_text, parse_mode="Markdown"),
            ]
        await app.bot.send_media_group(chat_id=dest, media=media)
    except Exception as exc:
        logger.error("Error enviando imágenes de lineup: %s", exc)
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
        "/partidos - Partidos del dia y activar monitoreo\n"
        "/activos  - Ver partidos monitoreados\n"
        "/stop     - Detener monitoreo de un partido\n"
        "/test     - Preview del post final\n"
        "/preview     - Enviar al canal un ejemplo de alineaciones y gol\n"
        "/lineup      - Enviar alineaciones manualmente al canal\n"
        "/testlineup  - Preview privado de imágenes de alineación\n"
        "/debug    - Diagnóstico de ESPN por liga\n"
        "/espn     - Test directo: /espn <slug>\n"
    )
    await update.message.reply_text(text)


@admin_only
async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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

    full_text = "\n".join(lines)
    chunks = [full_text[i:i+3900] for i in range(0, len(full_text), 3900)]
    await msg.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await update.message.reply_text(chunk)


@admin_only
async def cmd_espn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
    msg = await update.message.reply_text("Consultando ESPN y TheSportsDB...")
    try:
        all_events = await fetch_all_today()
    except Exception as exc:
        logger.error("Error fetch_all_today: %s", exc)
        await msg.edit_text("Error consultando las APIs. Intenta de nuevo.")
        return

    if not all_events:
        today_str = datetime.now(TZ).strftime("%d/%m/%Y")
        await msg.edit_text(f"No hay partidos hoy {today_str} en las ligas configuradas.")
        return

    await msg.delete()

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
            act  = "✅ " if fid in tracked else ""
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
async def cmd_activos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not tracked:
        await update.message.reply_text("No hay partidos monitoreados.")
        return
    lines = ["Partidos en monitoreo:\n"]
    for fid, fix in tracked.items():
        hora = fix.kickoff_utc.astimezone(TZ).strftime("%H:%M") if fix.kickoff_utc else "--:--"
        xi   = "XI ok" if fix.lineup_sent else "XI pendiente"
        lines.append(f"- {hora} {fix.home_name} {fix.home_score}-{fix.away_score} {fix.away_name} ({fix.league_name}) [{xi}]")
    await update.message.reply_text("\n".join(lines))


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

    msg = await update.message.reply_text("Buscando partidos...")
    try:
        all_events = await fetch_all_today()
    except Exception:
        await msg.edit_text("Error consultando.")
        return

    # Si no hay de ESPN de hoy, permitimos testear cualquiera disponible en el caché
    events_to_show = all_events if all_events else list(_events_cache.values())
    if not events_to_show:
        await msg.edit_text("No hay partidos hoy.\n\nUsa: /test <event_id>")
        return

    await msg.delete()
    keyboard = []
    for ev in events_to_show[:10]:
        p = parse_event(ev)
        label = f"{p['home_name']} {p['home_score']}-{p['away_score']} {p['away_name']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"tst:{p['id']}:{ev.get('_slug', '')}")])

    await update.message.reply_text(
        "Preview del post final - Selecciona un partido:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _run_test(event_id: str, slug: Optional[str], message: Message):
    status_msg = await message.reply_text(f"Obteniendo datos de {event_id}...")

    if not slug:
        slug = "esp.1"

    summary = await fetch_summary(slug, event_id)
    if not summary:
        # Intenta sacarlo del caché local si vino por TheSportsDB
        cached = _events_cache.get(event_id)
        if cached:
            p = parse_event(cached)
            await status_msg.edit_text(f"Post final sin summary (TheSportsDB):\n{p['home_name']} {p['home_score']}-{p['away_score']} {p['away_name']}")
            text = msg_final(p['home_name'], p['away_name'], p['home_score'], p['away_score'])
            await message.reply_text(text, parse_mode="Markdown")
            return
        await status_msg.edit_text("No se encontró el partido en ESPN.")
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

    await status_msg.edit_text(f"Generando imagen para {home_name} vs {away_name}...")

    fd = {
        "fixture": {"id": event_id},
        "league":  {"name": league_n},
        "teams": {
            "home": {"name": home_name, "logo": home_logo},
            "away": {"name": away_name, "logo": away_logo},
        },
        "goals": {"home": home_score, "away": away_score},
    }
    
    loop = asyncio.get_running_loop()
    raw_stats = await loop.run_in_executor(
        _executor, sofascore_raw_stats, home_name, away_name, None,
    )
    if raw_stats is None:
        raw_stats = build_raw_stats_from_espn(summary)

    img_path = None
    try:
        from image_generator import generate_match_summary
        img_path = await loop.run_in_executor(_executor, generate_match_summary, fd, raw_stats)
    except Exception as exc:
        logger.error("Error imagen test: %s", exc)

    text = (
        "--- PREVIEW POST FINAL ---\n\n"
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


async def cb_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    parts = query.data.split(":")
    fid   = parts[1]
    slug  = parts[2] if len(parts) > 2 else ""

    if fid in tracked:
        tracked.pop(fid)
    else:
        raw = _events_cache.get(fid)
        if not raw:
            try:
                await query.edit_message_text("No se encontró el partido.")
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
        )

    try:
        slug_events = [ev for ev in _events_cache.values() if ev.get("_slug") == slug]
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
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    parts = query.data.split(":")
    await _run_test(parts[1], parts[2] if len(parts) > 2 else None, query.message)


@admin_only
async def cmd_lineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args:
        fid = ctx.args[0]
        fix = tracked.get(fid)
        if not fix:
            await update.message.reply_text(f"Partido {fid} no está en monitoreo.")
            return
        msg = await update.message.reply_text(f"Obteniendo alineaciones...")
        summary = await fetch_summary(fix.league_slug, fid)
        if not summary:
            await msg.edit_text("No se pudo obtener el summary.")
            return
        home_xi, away_xi, home_formation, away_formation, home_logo_url, away_logo_url = parse_lineups(summary)
        if len(home_xi) < 11 or len(away_xi) < 11:
            await msg.edit_text("Alineaciones incompletas en ESPN.")
            return
        caption = msg_lineup(fix.league_name, fix.home_name, fix.away_name, home_xi, away_xi)
        dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
        await _send_lineup_images(
            ctx.application, dest, fix.home_name, fix.away_name,
            home_xi, away_xi, home_formation, away_formation,
            home_logo_url, away_logo_url, fix.league_name, fid, caption,
        )
        fix.lineup_sent = True
        await msg.edit_text(f"✅ Alineaciones enviadas.")
        return

    if not tracked:
        await update.message.reply_text("No hay partidos en monitoreo.")
        return
    keyboard = []
    for fid, fix in tracked.items():
        sent = "✓" if fix.lineup_sent else "⏳"
        label = f"{sent} {fix.home_name} vs {fix.away_name}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"lup:{fid}")])
    await update.message.reply_text(
        "Selecciona para forzar alineación:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cb_lineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    fid = query.data.split(":")[1]
    fix = tracked.get(fid)
    if not fix:
        return

    msg = await query.message.reply_text(f"Alineaciones de {fix.home_name}...")
    summary = await fetch_summary(fix.league_slug, fid)
    if not summary:
        await msg.edit_text("No se pudo obtener datos.")
        return

    home_xi, away_xi, home_f, away_f, home_logo, away_logo = parse_lineups(summary)
    if len(home_xi) < 11 or len(away_xi) < 11:
        await msg.edit_text("Alineaciones incompletas en ESPN.")
        return

    caption = msg_lineup(fix.league_name, fix.home_name, fix.away_name, home_xi, away_xi)
    dest    = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
    await _send_lineup_images(
        ctx.application, dest, fix.home_name, fix.away_name,
        home_xi, away_xi, home_f, away_f,
        home_logo, away_logo, fix.league_name, fid, caption,
    )
    fix.lineup_sent = True
    await msg.edit_text(f"✅ Alineaciones enviadas.")


@admin_only
async def cmd_testlineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Generando preview de alineación...")
    home_xi = ["Glove", "Defender 1", "Defender 2", "Defender 3", "Defender 4",
               "Mid 1", "Mid 2", "Mid 3", "Forward 1", "Forward 2", "Forward 3"]
    away_xi = home_xi
    caption = msg_lineup("Test League", "Home FC", "Away FC", home_xi, away_xi)
    try:
        await _send_lineup_images(
            ctx.application, update.effective_user.id, "Home FC", "Away FC",
            home_xi, away_xi, "4-3-3", "4-3-3", "", "", "Test League", "testlineup", caption,
        )
        await msg.delete()
    except Exception as exc:
        await msg.edit_text(f"Error: {exc}")


@admin_only
async def cmd_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
    home  = "Equipo A"
    away  = "Equipo B"
    league = "Liga Test"

    text_goal = msg_goal(home, away, 1, 0, league, "Goleador", "Asistidor", "home", "34")
    try:
        await ctx.bot.send_message(chat_id=dest, text=text_goal, parse_mode="Markdown")
        await update.message.reply_text("✅ Preview enviado.")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════

async def post_init(app: Application):
    # Tus tareas actuales
    app.create_task(monitor_loop(app))
    app.create_task(lineup_loop(app))
    
    # LA NUEVA TAREA DE SUPERVIVENCIA
    app.create_task(self_ping_loop())
    
    logger.info("¡Sistema Inmortal de Universo Football activado!")

async def self_ping_loop():
    """Evita que Render suspenda la instancia por inactividad."""
    port = os.getenv("PORT", "8000")
    # Intentamos conectar a localhost o a la URL de Render si la tienes en variables
    url = f"http://localhost:{port}"
    while True:
        try:
            # Hacemos una petición interna al servidor de server.py
            requests.get(url, timeout=5)
            logger.debug("Self-ping: Bot manteniéndose despierto.")
        except Exception as e:
            logger.debug(f"Self-ping local: {e}")
        
        # Esperar 10 minutos (Render apaga a los 15 min de inactividad)
        await asyncio.sleep(600)

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("partidos", cmd_partidos))
    app.add_handler(CommandHandler("activos",  cmd_activos))
    app.add_handler(CommandHandler("stop",     cmd_stop))
    app.add_handler(CommandHandler("test",     cmd_test))
    app.add_handler(CommandHandler("preview",     cmd_preview))
    app.add_handler(CommandHandler("lineup",      cmd_lineup))
    app.add_handler(CommandHandler("testlineup",  cmd_testlineup))
    app.add_handler(CallbackQueryHandler(cb_lineup, pattern=r"^lup:"))
    app.add_handler(CommandHandler("espn",     cmd_espn))
    app.add_handler(CommandHandler("debug",    cmd_debug))
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^tog:"))
    app.add_handler(CallbackQueryHandler(cb_test,   pattern=r"^tst:"))

    logger.info("Bot iniciado. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
