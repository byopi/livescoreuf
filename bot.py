"""
Livescore Bot — Universo Football
ESPN unofficial API · python-telegram-bot v21+

Flujo:
  1. /partidos   → lista partidos del día (UTC-4) con botones inline
  2. lineup_loop → 1h antes del partido envía alineaciones al canal
  3. monitor_loop→ detecta cambios de marcador → mensaje provisional
  4. resolve_loop→ edita con goleador/asistidor cuando ESPN lo publica
  5. Final       → imagen + mensaje de texto al canal
"""

import os
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import BadRequest

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Zona horaria UTC-4 ────────────────────────────────────────────────────────
TZ_UTC4 = timezone(timedelta(hours=-4))

# ─── Config desde entorno ──────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
ADMIN_ID         = int(os.environ["ADMIN_ID"])
CHANNEL_ID       = os.getenv("CHANNEL_ID", "")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "60"))     # seg entre polls de marcador
RESOLVE_INTERVAL = int(os.getenv("RESOLVE_INTERVAL", "15"))  # seg entre intentos de resolución
RESOLVE_TIMEOUT  = int(os.getenv("RESOLVE_TIMEOUT", "180"))  # seg máx esperando goleador
LINEUP_INTERVAL  = int(os.getenv("LINEUP_INTERVAL", "300"))  # seg entre checks de alineaciones

# ─── ESPN endpoints ────────────────────────────────────────────────────────────
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
ESPN_SUMMARY    = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/summary"
ESPN_HEADERS    = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept":     "application/json",
}

# ─── Ligas ESPN ────────────────────────────────────────────────────────────────
ESPN_LEAGUES = {
    # ── Alemania ───────────────────────────────────────────────────────────────
    "Bundesliga":             "ger.1",
    "DFB-Pokal":              "ger.dfb_pokal",
    # ── España ─────────────────────────────────────────────────────────────────
    "La Liga":                "esp.1",
    "Copa del Rey":           "esp.copa_del_rey",
    "Supercopa de España":    "esp.super_cup",
    # ── Francia ────────────────────────────────────────────────────────────────
    "Ligue 1":                "fra.1",
    "Coupe de France":        "fra.coupe_de_france",
    # ── Inglaterra ─────────────────────────────────────────────────────────────
    "Premier League":         "eng.1",
    "FA Cup":                 "eng.fa",
    "EFL Cup":                "eng.league_cup",
    "FA Community Shield":    "eng.community_shield",
    # ── Italia ─────────────────────────────────────────────────────────────────
    "Serie A":                "ita.1",
    "Coppa Italia":           "ita.coppa_italia",
    # ── UEFA ───────────────────────────────────────────────────────────────────
    "Champions League":       "uefa.champions",
    "Europa League":          "uefa.europa",
    "Conference League":      "uefa.europa.conf",
    "UEFA Nations League":    "uefa.nations",
    # ── Selecciones ────────────────────────────────────────────────────────────
    "FIFA World Cup":         "fifa.world",
    "Eurocopa":               "uefa.euro",
    "Copa America":           "conmebol.america",
    "Eliminatorias CONMEBOL": "conmebol.worldq",
    # ── Sudamerica ─────────────────────────────────────────────────────────────
    "Copa Libertadores":      "conmebol.libertadores",
    "Copa Sudamericana":      "conmebol.sudamericana",
    "Recopa Sudamericana":    "conmebol.recopa",
}

# Estados ESPN
ESPN_FINAL_STATUSES = {"STATUS_FINAL", "STATUS_FULL_TIME"}
ESPN_LIVE_STATUSES  = {"STATUS_IN_PROGRESS", "STATUS_HALFTIME"}


# ══════════════════════════════════════════════════════════════════════════════
# MODELOS DE DATOS
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
    scorer:       str  = "Obteniendo..."
    assist:       str  = "Obteniendo..."
    goal_side:    str  = ""
    resolved:     bool = False
    tg_message:   Optional[Message] = None
    elapsed_secs: float = 0.0


@dataclass
class TrackedFixture:
    fixture_id:   str
    league_slug:  str
    home_name:    str
    away_name:    str
    league_name:  str
    kickoff_utc:  Optional[datetime] = None
    home_score:   int  = 0
    away_score:   int  = 0
    status:       str  = ""
    finished:     bool = False
    lineup_sent:  bool = False


# ─── Estado global ─────────────────────────────────────────────────────────────
tracked:       dict[str, TrackedFixture] = {}
pending_goals: list[PendingGoal]         = []
resolved_kev:  dict[str, set]            = {}


# ══════════════════════════════════════════════════════════════════════════════
# CAPA ESPN API
# ══════════════════════════════════════════════════════════════════════════════

def espn_get(url: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(url, headers=ESPN_HEADERS, params=params, timeout=12)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("ESPN error %s: %s", url, exc)
        return None


def get_scoreboard(league_slug: str) -> list[dict]:
    """Síncrono — usar get_scoreboard_async() desde contextos async."""
    data = espn_get(ESPN_SCOREBOARD.format(league=league_slug))
    return data.get("events", []) if data else []


async def get_scoreboard_async(league_slug: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_scoreboard, league_slug)


def _fetch_all_today_sync() -> list[dict]:
    """Versión síncrona — se llama desde executor para no bloquear el event loop."""
    today_utc4 = datetime.now(TZ_UTC4).date()
    results = []
    for league_name, slug in ESPN_LEAGUES.items():
        for ev in get_scoreboard(slug):
            ev["_league_slug"] = slug
            ev["_league_name"] = league_name
            raw_date = ev.get("date", "")
            if raw_date:
                try:
                    ev_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    if ev_dt.astimezone(TZ_UTC4).date() == today_utc4:
                        results.append(ev)
                except Exception:
                    results.append(ev)
            else:
                results.append(ev)
    return results


async def get_all_today_fixtures() -> list[dict]:
    """Async wrapper — corre las 23 requests HTTP en un thread pool
    para no bloquear el event loop de Telegram."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_all_today_sync)


def get_fixture_summary(league_slug: str, event_id: str) -> Optional[dict]:
    """Síncrono — usar get_fixture_summary_async() desde contextos async."""
    return espn_get(
        ESPN_SUMMARY.format(league=league_slug),
        params={"event": event_id},
    )


async def get_fixture_summary_async(league_slug: str, event_id: str) -> Optional[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_fixture_summary, league_slug, event_id)


def parse_scoreboard_event(event: dict) -> dict:
    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    status = competition.get("status", {})

    raw_date = event.get("date", "")
    kickoff_utc = None
    kickoff_utc4_str = ""
    if raw_date:
        try:
            kickoff_utc = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            kickoff_utc4_str = kickoff_utc.astimezone(TZ_UTC4).strftime("%H:%M")
        except Exception:
            pass

    return {
        "id":           event["id"],
        "home_name":    home.get("team", {}).get("displayName", "?"),
        "away_name":    away.get("team", {}).get("displayName", "?"),
        "home_score":   int(home.get("score", 0) or 0),
        "away_score":   int(away.get("score", 0) or 0),
        "home_logo":    home.get("team", {}).get("logo", ""),
        "away_logo":    away.get("team", {}).get("logo", ""),
        "status_type":  status.get("type", {}).get("name", ""),
        "status_desc":  status.get("type", {}).get("description", ""),
        "clock":        status.get("displayClock", ""),
        "league_slug":  event.get("_league_slug", ""),
        "league_name":  event.get("_league_name", ""),
        "kickoff_utc":  kickoff_utc,
        "kickoff_utc4": kickoff_utc4_str,
    }


# ── Alineaciones ───────────────────────────────────────────────────────────────

def get_lineups(summary: dict) -> tuple[list[str], list[str]]:
    """
    Extrae los 11 titulares de cada equipo desde el summary de ESPN.
    Intenta boxscore.players primero, luego rosters como fallback.
    """
    home_players: list[str] = []
    away_players: list[str] = []

    # Intento 1: boxscore.players
    players_block = summary.get("boxscore", {}).get("players", [])
    for i, team_block in enumerate(players_block[:2]):
        names: list[str] = []
        for stat_group in team_block.get("statistics", []):
            for athlete in stat_group.get("athletes", []):
                if athlete.get("starter") and not athlete.get("didNotPlay", False):
                    name = (
                        athlete.get("athlete", {}).get("shortName")
                        or athlete.get("athlete", {}).get("displayName", "")
                    )
                    if name and name not in names:
                        names.append(name)
                if len(names) == 11:
                    break
            if len(names) == 11:
                break

        # Intento 2: rosters
        if not names:
            side = "home" if i == 0 else "away"
            for roster_team in summary.get("rosters", []):
                if roster_team.get("homeAway") == side:
                    for entry in roster_team.get("roster", []):
                        if entry.get("starter"):
                            name = (
                                entry.get("athlete", {}).get("shortName")
                                or entry.get("athlete", {}).get("displayName", "")
                            )
                            if name and name not in names:
                                names.append(name)
                        if len(names) == 11:
                            break
                    break

        if i == 0:
            home_players = names[:11]
        else:
            away_players = names[:11]

    return home_players, away_players


# ── Goles ──────────────────────────────────────────────────────────────────────

def get_match_key_events(summary: dict) -> list[dict]:
    return [
        ev for ev in summary.get("keyEvents", [])
        if "goal" in (ev.get("type", {}).get("text") or "").lower()
    ]


def parse_goal_event(ev: dict) -> tuple[str, str]:
    scorer = ""
    assist = ""

    for athlete in ev.get("athletes", []):
        role = (athlete.get("type") or "").lower()
        name = athlete.get("displayName") or athlete.get("fullName", "")
        if role in ("scorer", "goal", "goalscorer") and name:
            scorer = name
        elif role in ("assist", "assister") and name:
            assist = name

    raw = ev.get("shortText") or ev.get("text", "")
    if not scorer and raw:
        if re.search(r"own goal|autogol|en propia", raw, re.IGNORECASE):
            scorer = "Autogol"
        else:
            m = re.match(r"^([A-Za-z\u00e0-\u024f\s.\-']+?)\s+\d+[''']", raw)
            if m:
                scorer = m.group(1).strip()

    if not assist and raw:
        m = re.search(r'[Aa]ssist[e]?[:\s]+([A-Za-z\u00e0-\u024f\s.\-\']+?)[\),\n]', raw)
        if m:
            assist = m.group(1).strip()

    return (scorer or ""), (assist or "")


def get_match_stats(summary: dict) -> tuple[dict, dict]:
    STAT_MAP = {
        "possessionPct": "Posesion",
        "shotsOnTarget": "Tiros a puerta",
        "totalShots":    "Tiros totales",
        "corners":       "Corners",
        "fouls":         "Faltas",
        "yellowCards":   "Tarjetas amarillas",
        "redCards":      "Tarjetas rojas",
        "offsides":      "Fuera de juego",
        "saves":         "Paradas",
    }
    home_stats: dict = {}
    away_stats: dict = {}
    teams = summary.get("boxscore", {}).get("teams", [])
    for i, team_block in enumerate(teams[:2]):
        dest = home_stats if i == 0 else away_stats
        for stat in team_block.get("statistics", []):
            key_es = STAT_MAP.get(stat.get("name", ""))
            if key_es:
                raw = (stat.get("displayValue") or "0").replace("%", "").strip()
                try:
                    dest[key_es] = float(raw)
                except ValueError:
                    dest[key_es] = 0.0
    return home_stats, away_stats


# ══════════════════════════════════════════════════════════════════════════════
# FORMATEO DE MENSAJES
# ══════════════════════════════════════════════════════════════════════════════

def format_goal_message(
    home_name: str, away_name: str,
    home_score: int, away_score: int,
    league_name: str,
    scorer: str, assist: str,
    goal_side: str = "",
    elapsed: str = "",
) -> str:
    assist_line = f"*Asistidor:* {assist}" if assist and assist not in ("", "-") else "*Asistidor:* —"
    minute_line = f"\u23f1 {elapsed}'" if elapsed and elapsed not in ("", "0") else ""

    if goal_side == "home":
        score_str = f"[{home_score}]-{away_score}"
    elif goal_side == "away":
        score_str = f"{home_score}-[{away_score}]"
    else:
        score_str = f"[{home_score}-{away_score}]"

    lines = [
        "*🥅 | \u00a1GOOOOOL!*",
        "",
        f"*{home_name} {score_str} {away_name}*",
        "",
    ]
    if minute_line:
        lines.append(minute_line)
    lines += [
        f"⚽ {scorer}",
        f"\U0001f1e6\ufe0f {assist if assist and assist not in ('', '-') else '—'}",
        "",
        "*📲 \u00a1GOOOOOL! S\u00fascribete en t.me/iUniversoFootball*",
    ]
    return "\n".join(lines)


def format_goal_message(
    home_name: str, away_name: str,
    home_score: int, away_score: int,
    league_name: str,
    scorer: str, assist: str,
    goal_side: str = "",
    elapsed: str = "",
) -> str:
    assist_line = f"\U0001f1e6\ufe0f {assist}" if assist and assist not in ("", "-") else "\U0001f1e6\ufe0f —"
    minute_line = f"\u23f1 {elapsed}'" if elapsed and elapsed not in ("", "0") else ""

    if goal_side == "home":
        score_str = f"[{home_score}]-{away_score}"
    elif goal_side == "away":
        score_str = f"{home_score}-[{away_score}]"
    else:
        score_str = f"[{home_score}-{away_score}]"

    lines = [
        "*🥅 | \u00a1GOOOOOL!*",
        "",
        f"*{home_name} {score_str} {away_name}*",
        "",
    ]
    if minute_line:
        lines.append(minute_line)
    lines += [
        f"⚽ {scorer}",
        assist_line,
        "",
        "*📲 Suscr\u00edbete en t.me/iUniversoFootball*",
    ]
    return "\n".join(lines)


def format_final_message(
    home_name: str, away_name: str,
    home_score: int, away_score: int,
) -> str:
    return "\n".join([
        "*📢 | FINAL DEL PARTIDO*",
        "",
        f"\u21aa\ufe0f {home_name} {home_score}-{away_score} {away_name}",
        "",
        "*🎦 Todos los v\u00eddeos de los goles disponibles aqu\u00ed: t.me/ufgoals*",
        "",
        "_⚽ Suscr\u00edbete en t.me/iUniversoFootball_",
    ])


def format_lineup_message(
    home_name: str, away_name: str,
    home_players: list[str], away_players: list[str],
    league_name: str,
) -> str:
    tag     = "".join(w.capitalize() for w in league_name.split())
    home_xi = ", ".join(home_players) if home_players else "No disponible"
    away_xi = ", ".join(away_players) if away_players else "No disponible"
    return "\n".join([
        f"*👥 ALINEACIONES #{tag} | {home_name} vs. {away_name}*",
        "",
        f"*{home_name} XI:* {home_xi}",
        "",
        f"*{away_name} XI:* {away_xi}",
        "",
        "_⚽ Suscr\u00edbete en t.me/iUniversoFootball_",
    ])


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS PARA image_generator
# ══════════════════════════════════════════════════════════════════════════════

def _build_fixture_data(parsed: dict, summary: Optional[dict]) -> dict:
    home_logo = away_logo = ""
    if summary:
        # Logos desde boxscore
        teams = summary.get("boxscore", {}).get("teams", [])
        if len(teams) >= 2:
            home_logo = teams[0].get("team", {}).get("logo", "")
            away_logo = teams[1].get("team", {}).get("logo", "")
        # Fallback: header
        if not home_logo or not away_logo:
            header = summary.get("header", {})
            comp   = (header.get("competitions") or [{}])[0]
            comps  = comp.get("competitors", [])
            home_c = next((c for c in comps if c.get("homeAway") == "home"), {})
            away_c = next((c for c in comps if c.get("homeAway") == "away"), {})
            home_logo = home_logo or home_c.get("team", {}).get("logo", "")
            away_logo = away_logo or away_c.get("team", {}).get("logo", "")
    return {
        "fixture": {"id": parsed.get("id", 0)},
        "league":  {"name": parsed.get("league_name", "")},
        "teams": {
            "home": {"name": parsed["home_name"], "logo": home_logo},
            "away": {"name": parsed["away_name"], "logo": away_logo},
        },
        "goals": {"home": parsed["home_score"], "away": parsed["away_score"]},
    }


def _stats_to_raw(stats: tuple[dict, dict]) -> list[dict]:
    home_s, away_s = stats
    return [
        {"statistics": [{"type": k, "value": v} for k, v in home_s.items()]},
        {"statistics": [{"type": k, "value": v} for k, v in away_s.items()]},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# LOOPS DE BACKGROUND
# ══════════════════════════════════════════════════════════════════════════════

async def lineup_loop(app: Application):
    """
    Cada LINEUP_INTERVAL segundos revisa si algun partido activado
    arranca en <= 60 min. Si ESPN ya tiene las alineaciones, las envia.
    Si no, reintenta hasta que aparezcan (hasta 30 min despues del inicio).
    """
    logger.info("Lineup loop iniciado.")
    while True:
        await asyncio.sleep(LINEUP_INTERVAL)
        now = datetime.now(timezone.utc)

        for fid, fix in list(tracked.items()):
            if fix.lineup_sent or fix.finished:
                continue
            if fix.kickoff_utc is None:
                continue

            mins_to_kickoff = (fix.kickoff_utc - now).total_seconds() / 60

            # Ventana: 60 min antes hasta 30 min despues del inicio
            if not (-30 <= mins_to_kickoff <= 60):
                continue

            summary = await get_fixture_summary_async(fix.league_slug, fid)
            if not summary:
                continue

            home_players, away_players = get_lineups(summary)
            if not home_players and not away_players:
                logger.info("Alineaciones aun no disponibles: %s vs %s", fix.home_name, fix.away_name)
                continue

            msg = format_lineup_message(
                fix.home_name, fix.away_name,
                home_players, away_players,
                fix.league_name,
            )
            destination = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
            try:
                await app.bot.send_message(
                    chat_id=destination,
                    text=msg,
                    parse_mode="Markdown",
                )
                fix.lineup_sent = True
                logger.info("Alineaciones enviadas: %s vs %s", fix.home_name, fix.away_name)
            except Exception as exc:
                logger.error("Error enviando alineaciones: %s", exc)


async def monitor_loop(app: Application):
    """
    Detecta cambios de marcador. Cuando hay gol envia mensaje provisional
    y encola PendingGoal para que resolve_loop lo complete.
    """
    logger.info("Monitor loop iniciado.")
    while True:
        await asyncio.sleep(POLL_INTERVAL)

        for fid, fix in list(tracked.items()):
            if fix.finished:
                continue

            events = await get_scoreboard_async(fix.league_slug)
            raw = next((e for e in events if e["id"] == fid), None)
            if not raw:
                continue

            raw["_league_slug"] = fix.league_slug
            raw["_league_name"] = fix.league_name
            parsed = parse_scoreboard_event(raw)

            new_home = parsed["home_score"]
            new_away = parsed["away_score"]
            status   = parsed["status_type"]
            clock    = parsed["clock"]

            # ── Gol detectado ──────────────────────────────────────────────
            if new_home != fix.home_score or new_away != fix.away_score:
                home_delta = new_home - fix.home_score
                away_delta = new_away - fix.away_score
                n_goals    = home_delta + away_delta

                if home_delta > 0 and away_delta == 0:
                    goal_side = "home"
                elif away_delta > 0 and home_delta == 0:
                    goal_side = "away"
                else:
                    goal_side = ""

                logger.info("Gol: %s vs %s -> %d-%d", fix.home_name, fix.away_name, new_home, new_away)
                fix.home_score = new_home
                fix.away_score = new_away

                destination = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                for _ in range(max(n_goals, 1)):
                    provisional = format_goal_message(
                        fix.home_name, fix.away_name,
                        new_home, new_away,
                        fix.league_name,
                        scorer="⏳ Obteniendo...",
                        assist="⏳ Obteniendo...",
                        goal_side=goal_side,
                        elapsed=clock,
                    )
                    try:
                        sent = await app.bot.send_message(
                            chat_id=destination,
                            text=provisional,
                            parse_mode="Markdown",
                        )
                        pending_goals.append(PendingGoal(
                            fixture_id  = fid,
                            league_slug = fix.league_slug,
                            home_name   = fix.home_name,
                            away_name   = fix.away_name,
                            home_score  = new_home,
                            away_score  = new_away,
                            league_name = fix.league_name,
                            elapsed     = clock,
                            goal_side   = goal_side,
                            tg_message  = sent,
                        ))
                    except Exception as exc:
                        logger.error("Error enviando provisional: %s", exc)

            # ── Final de partido ───────────────────────────────────────────
            if status in ESPN_FINAL_STATUSES and not fix.finished:
                fix.finished = True
                summary = await get_fixture_summary_async(fix.league_slug, fid)
                stats   = get_match_stats(summary) if summary else ({}, {})

                fixture_img = _build_fixture_data(parsed, summary)
                raw_stats   = _stats_to_raw(stats)

                img_path = None
                try:
                    from image_generator import generate_match_summary
                    img_path = generate_match_summary(fixture_img, raw_stats)
                except Exception as exc:
                    logger.error("Error generando imagen final: %s", exc)

                msg_text    = format_final_message(
                    fix.home_name, fix.away_name,
                    fix.home_score, fix.away_score,
                )
                destination = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                try:
                    if img_path and os.path.exists(img_path):
                        with open(img_path, "rb") as f:
                            await app.bot.send_photo(
                                chat_id=destination, photo=f,
                                caption=msg_text, parse_mode="Markdown",
                            )
                    else:
                        await app.bot.send_message(
                            chat_id=destination, text=msg_text, parse_mode="Markdown",
                        )
                except Exception as exc:
                    logger.error("Error enviando final: %s", exc)

                tracked.pop(fid, None)


async def resolve_loop(app: Application):
    """
    Edita los mensajes provisionales de gol con el goleador/asistidor real.
    Reintenta cada RESOLVE_INTERVAL seg hasta RESOLVE_TIMEOUT.
    """
    logger.info("Resolve loop iniciado.")
    while True:
        await asyncio.sleep(RESOLVE_INTERVAL)

        if not pending_goals:
            continue

        by_fixture: dict[str, list[PendingGoal]] = {}
        for pg in pending_goals:
            if not pg.resolved:
                by_fixture.setdefault(pg.fixture_id, []).append(pg)

        for fid, goals in by_fixture.items():
            summary = await get_fixture_summary_async(goals[0].league_slug, fid)
            if not summary:
                for pg in goals:
                    pg.elapsed_secs += RESOLVE_INTERVAL
                continue

            key_events = get_match_key_events(summary)
            seen       = resolved_kev.setdefault(fid, set())
            unresolved = [g for g in goals if not g.resolved]

            for kev in key_events:
                kev_id = str(kev.get("id", ""))
                if kev_id in seen:
                    continue

                scorer, assist = parse_goal_event(kev)
                if not scorer:
                    continue

                if not unresolved:
                    break

                pg = unresolved.pop(0)
                seen.add(kev_id)
                pg.scorer   = scorer
                pg.assist   = assist
                pg.resolved = True

                new_text = format_goal_message(
                    pg.home_name, pg.away_name,
                    pg.home_score, pg.away_score,
                    pg.league_name,
                    scorer=scorer, assist=assist,
                    goal_side=pg.goal_side, elapsed=pg.elapsed,
                )
                if pg.tg_message:
                    try:
                        await pg.tg_message.edit_text(new_text, parse_mode="Markdown")
                        logger.info("Editado -> Gol: %s | Asist: %s", scorer, assist)
                    except BadRequest as exc:
                        logger.warning("BadRequest editando: %s", exc)
                    except Exception as exc:
                        logger.error("Error editando: %s", exc)

            # Timeout
            for pg in goals:
                if not pg.resolved:
                    pg.elapsed_secs += RESOLVE_INTERVAL
                    if pg.elapsed_secs >= RESOLVE_TIMEOUT:
                        pg.resolved = True
                        fallback = format_goal_message(
                            pg.home_name, pg.away_name,
                            pg.home_score, pg.away_score,
                            pg.league_name,
                            scorer="No disponible", assist="—",
                            goal_side=pg.goal_side, elapsed=pg.elapsed,
                        )
                        if pg.tg_message:
                            try:
                                await pg.tg_message.edit_text(fallback, parse_mode="Markdown")
                            except Exception:
                                pass

        pending_goals[:] = [pg for pg in pending_goals if not pg.resolved]


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS DE TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ No tienes permiso.")
            return
        return await func(update, ctx)
    return wrapper


@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Bot Universo Football activo*\n\n"
        "Comandos:\n"
        "/partidos — Partidos del dia (UTC\\-4) y activar monitoreo\n"
        "/activos  — Ver partidos monitoreados\n"
        "/stop     — Detener monitoreo\n"
        "/test     — Preview del post final",
        parse_mode="MarkdownV2",
    )


@admin_only
async def cmd_partidos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Consultando ESPN...")
    all_events = await get_all_today_fixtures()

    if not all_events:
        await msg.edit_text("❌ No hay partidos hoy (UTC-4).")
        return

    await msg.delete()

    by_league: dict[str, list] = {}
    for ev in all_events:
        by_league.setdefault(ev.get("_league_name", "Otra"), []).append(ev)

    today_str = datetime.now(TZ_UTC4).strftime("%d/%m/%Y")
    for league_name, events in by_league.items():
        keyboard = []
        for ev in events:
            p      = parse_scoreboard_event(ev)
            fid    = p["id"]
            slug   = ev.get("_league_slug", "")
            active = "✅ " if fid in tracked else ""
            time_  = p["kickoff_utc4"] or "--:--"
            label  = (
                f"{active}{time_} | {p['home_name']} vs {p['away_name']} "
                f"({p['home_score']}-{p['away_score']}) · {p['status_desc']}"
            )
            keyboard.append([
                InlineKeyboardButton(label, callback_data=f"toggle:{fid}:{slug}")
            ])
        await update.message.reply_text(
            f"📅 *{today_str} — {league_name}*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )


@admin_only
async def cmd_activos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not tracked:
        await update.message.reply_text("ℹ️ No hay partidos monitoreados.")
        return
    lines = ["🟢 *Partidos en monitoreo:*\n"]
    for fid, fix in tracked.items():
        time_str    = fix.kickoff_utc.astimezone(TZ_UTC4).strftime("%H:%M") if fix.kickoff_utc else "--:--"
        lineup_icon = "👥✅" if fix.lineup_sent else "👥⏳"
        lines.append(
            f"\u2022 `{fid}` {time_str} — {fix.home_name} {fix.home_score}-{fix.away_score} "
            f"{fix.away_name} ({fix.league_name}) {lineup_icon}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text("Uso: `/stop <event_id>`", parse_mode="Markdown")
        return
    fid = args[0]
    if fid in tracked:
        tracked.pop(fid)
        await update.message.reply_text(f"⏹️ `{fid}` removido.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ `{fid}` no estaba activo.", parse_mode="Markdown")


@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if args:
        await _run_test(args[0], None, update.message)
        return

    msg = await update.message.reply_text("🔍 Buscando partidos finalizados hoy...")
    all_events = await get_all_today_fixtures()
    finished = [
        e for e in all_events
        if parse_scoreboard_event(e)["status_type"] in ESPN_FINAL_STATUSES
    ]
    if not finished:
        await msg.edit_text(
            "⚠️ No hay partidos finalizados hoy.\n\nPrueba: `/test <event_id>`",
            parse_mode="Markdown",
        )
        return
    await msg.delete()

    keyboard = []
    for ev in finished:
        p = parse_scoreboard_event(ev)
        label = (
            f"🧪 {p['home_name']} {p['home_score']}-{p['away_score']} "
            f"{p['away_name']} ({p['league_name']})"
        )
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"test:{p['id']}:{ev['_league_slug']}")
        ])
    await update.message.reply_text(
        "🧪 *Preview del post final* — Selecciona:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _run_test(event_id: str, league_slug: Optional[str], message: Message):
    status_msg = await message.reply_text(
        f"⏳ Obteniendo datos de `{event_id}`...", parse_mode="Markdown"
    )

    if not league_slug:
        all_events  = await get_all_today_fixtures()
        match       = next((e for e in all_events if e["id"] == event_id), None)
        league_slug = match.get("_league_slug", "esp.1") if match else "esp.1"

    summary = await get_fixture_summary_async(league_slug, event_id)
    if not summary:
        await status_msg.edit_text("❌ No se encontro el partido en ESPN.")
        return

    header      = summary.get("header", {})
    competition = (header.get("competitions") or [{}])[0]
    competitors = competition.get("competitors", [])
    home_c      = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_c      = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_name   = home_c.get("team", {}).get("displayName", "?")
    away_name   = away_c.get("team", {}).get("displayName", "?")
    home_logo   = home_c.get("team", {}).get("logo", "")
    away_logo   = away_c.get("team", {}).get("logo", "")
    home_score  = int(home_c.get("score", 0) or 0)
    away_score  = int(away_c.get("score", 0) or 0)
    league_name = header.get("league", {}).get("name", league_slug)
    status_desc = competition.get("status", {}).get("type", {}).get("description", "")
    short_st    = competition.get("status", {}).get("type", {}).get("name", "")

    warning = ""
    if short_st not in ESPN_FINAL_STATUSES:
        warning = f"\n\n⚠️ _Estado: {status_desc}. Preview._"

    await status_msg.edit_text(
        f"✅ *{home_name} {home_score}-{away_score} {away_name}*\n"
        f"🏆 {league_name} · {status_desc}{warning}\n\n⏳ Generando imagen...",
        parse_mode="Markdown",
    )

    stats        = get_match_stats(summary)
    parsed_fake  = {
        "id": event_id, "home_name": home_name, "away_name": away_name,
        "home_score": home_score, "away_score": away_score,
        "league_name": league_name,
        "home_logo": home_logo, "away_logo": away_logo,
    }
    fixture_data = _build_fixture_data(parsed_fake, summary)
    raw_stats    = _stats_to_raw(stats)

    img_path = None
    try:
        from image_generator import generate_match_summary
        img_path = generate_match_summary(fixture_data, raw_stats)
    except Exception as exc:
        logger.error("Error generando imagen de test: %s", exc)

    msg_text    = format_final_message(home_name, away_name, home_score, away_score)
    header_test = (
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        "🧪 *PREVIEW DEL POST FINAL*\n"
        f"`Event ID: {event_id}`\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    )

    try:
        if img_path and os.path.exists(img_path):
            await message.reply_photo(
                photo=open(img_path, "rb"),
                caption=header_test + msg_text,
                parse_mode="Markdown",
            )
        else:
            await message.reply_text(
                header_test + msg_text + "\n\n_(Sin imagen — verifica los assets)_",
                parse_mode="Markdown",
            )
        await status_msg.delete()
    except Exception as exc:
        await status_msg.edit_text(f"❌ Error: {exc}")


# ── Callbacks inline ───────────────────────────────────────────────────────────

async def callback_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Sin permiso.", show_alert=True)
        return

    parts       = query.data.split(":")
    fid         = parts[1]
    league_slug = parts[2] if len(parts) > 2 else ""

    if fid in tracked:
        tracked.pop(fid)
        await query.answer("⏹️ Partido desactivado.", show_alert=True)
    else:
        events = await get_scoreboard_async(league_slug)
        raw    = next((e for e in events if e["id"] == fid), None)
        if raw:
            raw["_league_slug"] = league_slug
            raw["_league_name"] = next(
                (n for n, s in ESPN_LEAGUES.items() if s == league_slug), league_slug
            )
            p = parse_scoreboard_event(raw)
            tracked[fid] = TrackedFixture(
                fixture_id  = fid,
                league_slug = league_slug,
                home_name   = p["home_name"],
                away_name   = p["away_name"],
                league_name = raw["_league_name"],
                kickoff_utc = p["kickoff_utc"],
                home_score  = p["home_score"],
                away_score  = p["away_score"],
                status      = p["status_type"],
            )
            await query.answer(
                f"✅ Activado: {p['home_name']} vs {p['away_name']}", show_alert=True
            )
        else:
            await query.answer("❌ No se encontro el partido.", show_alert=True)

    # Refrescar teclado
    try:
        events   = await get_scoreboard_async(league_slug)
        keyboard = []
        for ev in events:
            ev["_league_slug"] = league_slug
            ev["_league_name"] = ""
            p2     = parse_scoreboard_event(ev)
            active = "✅ " if p2["id"] in tracked else ""
            time_  = p2["kickoff_utc4"] or "--:--"
            label  = (
                f"{active}{time_} | {p2['home_name']} vs {p2['away_name']} "
                f"({p2['home_score']}-{p2['away_score']}) · {p2['status_desc']}"
            )
            keyboard.append([
                InlineKeyboardButton(label, callback_data=f"toggle:{p2['id']}:{league_slug}")
            ])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        pass


async def callback_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ Generando preview...")

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Sin permiso.", show_alert=True)
        return

    parts       = query.data.split(":")
    event_id    = parts[1]
    league_slug = parts[2] if len(parts) > 2 else None
    await _run_test(event_id, league_slug, query.message)


# ══════════════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════

async def post_init(app: Application):
    app.create_task(lineup_loop(app))
    app.create_task(monitor_loop(app))
    app.create_task(resolve_loop(app))
    logger.info("Loops de lineup, monitoreo y resolucion iniciados.")


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
    app.add_handler(CallbackQueryHandler(callback_toggle, pattern=r"^toggle:"))
    app.add_handler(CallbackQueryHandler(callback_test,   pattern=r"^test:"))

    logger.info("Bot iniciado. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
