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
from sofascore_stats import sofascore_raw_stats
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

# ─── Zona horaria ──────────────────────────────────────────────────────────────
TZ = timezone(timedelta(hours=-4))   # UTC-4

# ─── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
ADMIN_ID         = int(os.environ["ADMIN_ID"])
CHANNEL_ID       = os.getenv("CHANNEL_ID", "")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL",    "60"))
RESOLVE_INTERVAL = int(os.getenv("RESOLVE_INTERVAL", "15"))
RESOLVE_TIMEOUT  = int(os.getenv("RESOLVE_TIMEOUT",  "180"))
LINEUP_INTERVAL  = int(os.getenv("LINEUP_INTERVAL",  "300"))

# ─── ESPN ──────────────────────────────────────────────────────────────────────
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
ESPN_SUMMARY    = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/summary"
ESPN_HEADERS    = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept": "application/json",
}

ESPN_LEAGUES = {
    # Alemania
    "Bundesliga":             "ger.1",
    "DFB-Pokal":              "ger.dfb_pokal",
    # España
    "La Liga":                "esp.1",
    "Copa del Rey":           "esp.copa_del_rey",
    "Supercopa de Espana":    "esp.super_cup",
    # Francia
    "Ligue 1":                "fra.1",
    "Coupe de France":        "fra.coupe_de_france",
    # Inglaterra
    "Premier League":         "eng.1",
    "FA Cup":                 "eng.fa",
    "EFL Cup":                "eng.league_cup",
    "Community Shield":       "eng.community_shield",
    # Italia
    "Serie A":                "ita.1",
    "Coppa Italia":           "ita.coppa_italia",
    # UEFA
    "Champions League":       "uefa.champions",
    "Europa League":          "uefa.europa",
    "Conference League":      "uefa.europa.conf",
    "Nations League":         "uefa.nations",
    # Selecciones
    "Mundial FIFA":           "fifa.world",
    "Eurocopa":               "uefa.euro",
    "Copa America":           "conmebol.america",
    "Eliminatorias CONMEBOL": "conmebol.worldq",
    # Sudamerica
    "Copa Libertadores":      "conmebol.libertadores",
    "Copa Sudamericana":      "conmebol.sudamericana",
    "Recopa Sudamericana":    "conmebol.recopa",
}

ESPN_FINAL  = {"STATUS_FINAL", "STATUS_FULL_TIME"}
ESPN_LIVE   = {"STATUS_IN_PROGRESS", "STATUS_HALFTIME"}

# Thread pool para requests HTTP (no bloquean el event loop)
_executor = ThreadPoolExecutor(max_workers=8)


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
    lineup_tries: int  = 0


# ─── Estado global ─────────────────────────────────────────────────────────────
tracked:       dict[str, TrackedFixture] = {}
pending_goals: list[PendingGoal]         = []
resolved_kev:  dict[str, set]            = {}


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


def _fetch_scoreboard(slug: str) -> list[dict]:
    data = _espn_get(ESPN_SCOREBOARD.format(league=slug))
    return data.get("events", []) if data else []


def _fetch_summary(slug: str, event_id: str) -> Optional[dict]:
    return _espn_get(ESPN_SUMMARY.format(league=slug), params={"event": event_id})


def _fetch_all_today() -> list[dict]:
    """Consulta todas las ligas y devuelve partidos del dia en UTC-4."""
    today = datetime.now(TZ).date()
    results = []
    for league_name, slug in ESPN_LEAGUES.items():
        for ev in _fetch_scoreboard(slug):
            raw = ev.get("date", "")
            include = True
            if raw:
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    include = dt.astimezone(TZ).date() == today
                except Exception:
                    pass
            if include:
                ev["_slug"]   = slug
                ev["_league"] = league_name
                results.append(ev)
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


def parse_lineups(summary: dict) -> tuple[list[str], list[str]]:
    home_xi: list[str] = []
    away_xi: list[str] = []
    for i, roster in enumerate(summary.get("rosters", [])[:2]):
        dest = home_xi if i == 0 else away_xi
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
    return home_xi, away_xi


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
             side: str = "", elapsed: str = "") -> str:
    assist_line = f"🅰️ {assist}" if assist and assist != "-" else "🅰️ -"
    minute = f"⌚ {elapsed}'" if elapsed and elapsed != "0" else ""

    if side == "home":
        score = f"[{hs}]-{as_}"
    elif side == "away":
        score = f"{hs}-[{as_}]"
    else:
        score = f"[{hs}-{as_}]"

    lines = [
        "*🥅 | GOOOOOL!*",
        "",
        f"*{home} {score} {away}*",
        "",
    ]
    if minute:
        lines.append(minute)
    lines += [f"⚽ {scorer}", assist_line, "", "*📲 Suscribete en t.me/iUniversoFootball*"]
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

async def monitor_loop(app: Application):
    logger.info("monitor_loop iniciado")
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        for fid, fix in list(tracked.items()):
            if fix.finished:
                continue
            try:
                events = await fetch_scoreboard(fix.league_slug)
                raw = next((e for e in events if e["id"] == fid), None)
                if not raw:
                    continue
                raw["_slug"]   = fix.league_slug
                raw["_league"] = fix.league_name
                p = parse_event(raw)

                new_h  = p["home_score"]
                new_a  = p["away_score"]
                status = p["status_type"]
                clock  = p["clock"]

                # Gol detectado
                if new_h != fix.home_score or new_a != fix.away_score:
                    dh = new_h - fix.home_score
                    da = new_a - fix.away_score
                    side = "home" if dh > 0 and da == 0 else "away" if da > 0 and dh == 0 else ""
                    fix.home_score = new_h
                    fix.away_score = new_a

                    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                    for _ in range(max(dh + da, 1)):
                        text = msg_goal(fix.home_name, fix.away_name, new_h, new_a,
                                        fix.league_name, "⏳ Obteniendo...", "⏳ Obteniendo...",
                                        side, clock)
                        try:
                            sent = await app.bot.send_message(dest, text, parse_mode="Markdown", disable_web_page_preview=True)
                            pending_goals.append(PendingGoal(
                                fixture_id=fid, league_slug=fix.league_slug,
                                home_name=fix.home_name, away_name=fix.away_name,
                                home_score=new_h, away_score=new_a,
                                league_name=fix.league_name, elapsed=clock,
                                goal_side=side, tg_message=sent,
                            ))
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
                            raw_stats = await loop.run_in_executor(
                                _executor, sofascore_raw_stats,
                                fix.home_name, fix.away_name, None,
                            )
                            if raw_stats is None:
                                logger.info("Sofascore sin datos, usando ESPN para stats de imagen.")
                                raw_stats = build_raw_stats_from_espn(summary)
                            else:
                                logger.info("Stats de imagen obtenidas desde Sofascore.")
                            loop = asyncio.get_running_loop()
                            from image_generator import generate_match_summary
                            img_path = await loop.run_in_executor(
                                _executor, generate_match_summary, fd, raw_stats
                            )
                        except Exception as exc:
                            logger.error("Error generando imagen: %s", exc)

                    text = msg_final(fix.home_name, fix.away_name, fix.home_score, fix.away_score)
                    dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                    try:
                        if img_path and os.path.exists(img_path):
                            with open(img_path, "rb") as f:
                                await app.bot.send_photo(dest, f, caption=text, parse_mode="Markdown")
                        else:
                            await app.bot.send_message(dest, text, parse_mode="Markdown", disable_web_page_preview=True)
                    except Exception as exc:
                        logger.error("Error enviando final: %s", exc)

                    tracked.pop(fid, None)

            except Exception as exc:
                logger.error("monitor_loop error en %s: %s", fid, exc)


async def resolve_loop(app: Application):
    logger.info("resolve_loop iniciado")
    while True:
        await asyncio.sleep(RESOLVE_INTERVAL)
        if not pending_goals:
            continue

        by_fix: dict[str, list[PendingGoal]] = {}
        for pg in pending_goals:
            if not pg.resolved:
                by_fix.setdefault(pg.fixture_id, []).append(pg)

        for fid, goals in by_fix.items():
            try:
                summary = await fetch_summary(goals[0].league_slug, fid)
                if not summary:
                    for pg in goals:
                        pg.elapsed_secs += RESOLVE_INTERVAL
                    continue

                key_evs    = parse_key_events(summary)
                seen       = resolved_kev.setdefault(fid, set())
                unresolved = [g for g in goals if not g.resolved]

                for kev in key_evs:
                    kid = str(kev.get("id", ""))
                    if kid in seen:
                        continue
                    scorer, assist = parse_goal_event(kev)
                    if not scorer:
                        continue
                    if not unresolved:
                        break

                    pg = unresolved.pop(0)
                    seen.add(kid)
                    pg.scorer   = scorer
                    pg.assist   = assist
                    pg.resolved = True

                    text = msg_goal(pg.home_name, pg.away_name, pg.home_score, pg.away_score,
                                    pg.league_name, scorer, assist, pg.goal_side, pg.elapsed)
                    if pg.tg_message:
                        try:
                            await pg.tg_message.edit_text(text, parse_mode="Markdown")
                        except BadRequest:
                            pass
                        except Exception as exc:
                            logger.error("Error editando gol: %s", exc)

                for pg in goals:
                    if not pg.resolved:
                        pg.elapsed_secs += RESOLVE_INTERVAL
                        if pg.elapsed_secs >= RESOLVE_TIMEOUT:
                            pg.resolved = True
                            text = msg_goal(pg.home_name, pg.away_name, pg.home_score, pg.away_score,
                                            pg.league_name, "No disponible", "-",
                                            pg.goal_side, pg.elapsed)
                            if pg.tg_message:
                                try:
                                    await pg.tg_message.edit_text(text, parse_mode="Markdown")
                                except Exception:
                                    pass
            except Exception as exc:
                logger.error("resolve_loop error: %s", exc)

        pending_goals[:] = [pg for pg in pending_goals if not pg.resolved]


async def lineup_loop(app: Application):
    logger.info("lineup_loop iniciado")
    while True:
        await asyncio.sleep(LINEUP_INTERVAL)
        now = datetime.now(timezone.utc)

        for fid, fix in list(tracked.items()):
            if fix.lineup_sent or fix.finished or fix.kickoff_utc is None:
                continue
            mins = (fix.kickoff_utc - now).total_seconds() / 60
            if not (-15 <= mins <= 60):
                continue
            try:
                summary = await fetch_summary(fix.league_slug, fid)
                if not summary:
                    continue
                home_xi, away_xi = parse_lineups(summary)
                if len(home_xi) < 11 or len(away_xi) < 11:
                    fix.lineup_tries += 1
                    logger.info("Alineaciones incompletas %s vs %s (intento %d)",
                                fix.home_name, fix.away_name, fix.lineup_tries)
                    continue

                text = msg_lineup(fix.league_name, fix.home_name, fix.away_name, home_xi, away_xi)
                dest = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                await app.bot.send_message(dest, text, parse_mode="Markdown", disable_web_page_preview=True)
                fix.lineup_sent = True
                logger.info("Alineaciones enviadas: %s vs %s", fix.home_name, fix.away_name)
            except Exception as exc:
                logger.error("lineup_loop error %s: %s", fid, exc)


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
        "/preview  - Enviar al canal un ejemplo de alineaciones y gol"
    )
    await update.message.reply_text(text)


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
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    parts = query.data.split(":")
    fid   = parts[1]
    slug  = parts[2] if len(parts) > 2 else ""

    if fid in tracked:
        tracked.pop(fid)
        await query.answer("Partido desactivado.", show_alert=True)
    else:
        try:
            events = await fetch_scoreboard(slug)
            raw = next((e for e in events if e["id"] == fid), None)
            if raw:
                raw["_slug"]   = slug
                raw["_league"] = next((n for n, s in ESPN_LEAGUES.items() if s == slug), slug)
                p = parse_event(raw)
                tracked[fid] = TrackedFixture(
                    fixture_id  = fid,
                    league_slug = slug,
                    home_name   = p["home_name"],
                    away_name   = p["away_name"],
                    league_name = raw["_league"],
                    kickoff_utc = p["kickoff_utc"],
                    home_score  = p["home_score"],
                    away_score  = p["away_score"],
                    status      = p["status_type"],
                )
                await query.answer(f"Activado: {p['home_name']} vs {p['away_name']}", show_alert=True)
            else:
                await query.answer("No se encontro el partido.", show_alert=True)
                return
        except Exception as exc:
            logger.error("cb_toggle error: %s", exc)
            await query.answer("Error al activar.", show_alert=True)
            return

    # Refrescar teclado de la liga
    try:
        events = await fetch_scoreboard(slug)
        keyboard = []
        for ev in events:
            ev["_slug"]   = slug
            ev["_league"] = ""
            p   = parse_event(ev)
            act = "OK " if p["id"] in tracked else ""
            hora = p["kickoff_str"] or "--:--"
            label = f"{act}{hora} | {p['home_name']} vs {p['away_name']} ({p['home_score']}-{p['away_score']}) {p['status_desc']}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"tog:{p['id']}:{slug}")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
    except Exception:
        pass


async def cb_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Generando preview...")
    if query.from_user.id != ADMIN_ID:
        return
    parts = query.data.split(":")
    await _run_test(parts[1], parts[2] if len(parts) > 2 else None, query.message)



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
        await ctx.bot.send_message(dest, text_lineup, parse_mode="Markdown", disable_web_page_preview=True)
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
        await ctx.bot.send_message(dest, text_goal, parse_mode="Markdown", disable_web_page_preview=True)
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
    app.create_task(resolve_loop(app))
    app.create_task(lineup_loop(app))
    logger.info("Loops iniciados.")


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
    app.add_handler(CommandHandler("preview",  cmd_preview))
    app.add_handler(CallbackQueryHandler(cb_toggle, pattern=r"^tog:"))
    app.add_handler(CallbackQueryHandler(cb_test,   pattern=r"^tst:"))

    logger.info("Bot iniciado. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
