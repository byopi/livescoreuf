"""
Livescore Bot — Universo Football
ESPN unofficial API · python-telegram-bot v20+

Flujo de gol:
  1. ESPN detecta cambio de marcador → envía mensaje inmediato con ⏳
  2. Loop de resolución consulta summary cada ~15s → edita el mensaje
     con el nombre real del goleador y asistidor cuando ESPN lo publica
"""

import os
import re
import asyncio
import logging
from datetime import date
from dataclasses import dataclass, field
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

# ─── Config desde entorno ──────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
ADMIN_ID         = int(os.environ["ADMIN_ID"])
CHANNEL_ID       = os.getenv("CHANNEL_ID", "")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "60"))    # segundos entre polls del marcador
RESOLVE_INTERVAL = int(os.getenv("RESOLVE_INTERVAL", "15")) # segundos entre intentos de resolución
RESOLVE_TIMEOUT  = int(os.getenv("RESOLVE_TIMEOUT", "180")) # segundos máx esperando goleador

# ─── ESPN endpoints ────────────────────────────────────────────────────────────
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
ESPN_SUMMARY    = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/{league}/summary"
ESPN_HEADERS    = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept":     "application/json",
}

# Ligas ESPN soportadas — ampliar según necesidad
ESPN_LEAGUES = {
    "Premier League":    "eng.1",
    "La Liga":           "esp.1",
    "Serie A":           "ita.1",
    "Bundesliga":        "ger.1",
    "Ligue 1":           "fra.1",
    "Champions League":  "uefa.champions",
    "Europa League":     "uefa.europa",
    "Liga BetPlay":      "col.1",
    "Liga MX":           "mex.1",
    "MLS":               "usa.1",
    "Copa Libertadores": "conmebol.libertadores",
    "Copa Sudamericana": "conmebol.sudamericana",
}

# Estados ESPN que indican partido finalizado
ESPN_FINAL_STATUSES = {"STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_POSTPONED"}


# ══════════════════════════════════════════════════════════════════════════════
# MODELOS DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingGoal:
    """Gol detectado cuyo goleador/asistidor aún no está confirmado."""
    fixture_id:   str
    league_slug:  str
    home_name:    str
    away_name:    str
    home_score:   int
    away_score:   int
    league_name:  str
    elapsed:      str
    scorer:       str = "⏳ Obteniendo..."
    assist:       str = "⏳ Obteniendo..."
    goal_side:    str = ""      # "home" | "away" | ""
    resolved:     bool = False
    tg_message:   Optional[Message] = None
    elapsed_secs: float = 0.0   # segundos desde creación (para timeout)


@dataclass
class TrackedFixture:
    """Partido activo bajo monitoreo."""
    fixture_id:  str
    league_slug: str
    home_name:   str
    away_name:   str
    league_name: str
    home_score:  int = 0
    away_score:  int = 0
    status:      str = ""
    finished:    bool = False


# ─── Estado global en memoria ──────────────────────────────────────────────────
tracked:       dict[str, TrackedFixture] = {}
pending_goals: list[PendingGoal]         = []
resolved_kev:  dict[str, set]            = {}   # fixture_id → set(kev_id ya usados)


# ══════════════════════════════════════════════════════════════════════════════
# CAPA ESPN API
# ══════════════════════════════════════════════════════════════════════════════

def espn_get(url: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(url, headers=ESPN_HEADERS, params=params, timeout=12)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("ESPN request error %s: %s", url, exc)
        return None


def get_scoreboard(league_slug: str) -> list[dict]:
    data = espn_get(ESPN_SCOREBOARD.format(league=league_slug))
    if not data:
        return []
    return data.get("events", [])


def get_all_today_fixtures() -> list[dict]:
    """Consulta todas las ligas configuradas y devuelve los partidos del día."""
    results = []
    for league_name, slug in ESPN_LEAGUES.items():
        events = get_scoreboard(slug)
        for ev in events:
            ev["_league_slug"] = slug
            ev["_league_name"] = league_name
            results.append(ev)
    return results


def get_fixture_summary(league_slug: str, event_id: str) -> Optional[dict]:
    return espn_get(
        ESPN_SUMMARY.format(league=league_slug),
        params={"event": event_id},
    )


def parse_scoreboard_event(event: dict) -> dict:
    """Extrae campos clave de un evento del scoreboard de ESPN."""
    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    status = competition.get("status", {})

    return {
        "id":          event["id"],
        "home_name":   home.get("team", {}).get("displayName", "?"),
        "away_name":   away.get("team", {}).get("displayName", "?"),
        "home_score":  int(home.get("score", 0) or 0),
        "away_score":  int(away.get("score", 0) or 0),
        "home_logo":   home.get("team", {}).get("logo", ""),
        "away_logo":   away.get("team", {}).get("logo", ""),
        "status_type": status.get("type", {}).get("name", ""),
        "status_desc": status.get("type", {}).get("description", ""),
        "clock":       status.get("displayClock", ""),
        "league_slug": event.get("_league_slug", ""),
        "league_name": event.get("_league_name", ""),
    }


# ── Parseo de goleador / asistidor ─────────────────────────────────────────────

def get_match_key_events(summary: dict) -> list[dict]:
    """Devuelve todos los keyEvents de tipo Goal del summary."""
    goals = []
    for ev in summary.get("keyEvents", []):
        ev_type = (ev.get("type", {}).get("text") or "").lower()
        if "goal" in ev_type:
            goals.append(ev)
    return goals


def parse_goal_event(ev: dict) -> tuple[str, str]:
    """
    Extrae goleador y asistidor de un keyEvent de ESPN.

    Estrategia:
      1. campo athletes[] con type == "scorer" / "assist"
      2. texto libre: "Raphinha 34' (Assist: Pedri)"
      3. Regex sobre shortText / text
    Devuelve ("—", "—") si no hay datos todavía.
    """
    scorer = ""
    assist = ""

    # ── 1. Campo estructurado athletes[] ──────────────────────────────────
    for athlete in ev.get("athletes", []):
        role = (athlete.get("type") or "").lower()
        name = athlete.get("displayName") or athlete.get("fullName", "")
        if role in ("scorer", "goal", "goalscorer") and name:
            scorer = name
        elif role in ("assist", "assister") and name:
            assist = name

    # ── 2. Texto libre ────────────────────────────────────────────────────
    raw = ev.get("shortText") or ev.get("text", "")

    if not scorer and raw:
        if re.search(r"own goal|autogol|en propia", raw, re.IGNORECASE):
            scorer = "Autogol"
        else:
            m = re.match(r"^([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s.\-']+?)\s+\d+[''′]", raw)
            if m:
                scorer = m.group(1).strip()

    if not assist and raw:
        m = re.search(
            r'[Aa]ssist[e]?[:\s]+([A-Za-záéíóúÁÉÍÓÚñÑüÜ\s.\-\']+?)[\),\n]',
            raw,
        )
        if m:
            assist = m.group(1).strip()

    return (scorer or "—"), (assist or "—")


def get_match_stats(summary: dict) -> tuple[dict, dict]:
    """Extrae estadísticas del boxscore del summary."""
    STAT_MAP = {
        "possessionPct": "Posesión",
        "shotsOnTarget": "Tiros a puerta",
        "totalShots":    "Tiros totales",
        "corners":       "Córners",
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
    goal_side: str = "",   # "home" | "away" | "" (desconocido)
    elapsed: str = "",
) -> str:
    """
    Formato del marcador:
      - Gol local     → Real Madrid [1]-0 Barcelona
      - Gol visitante → Real Madrid 0-[1] Barcelona
      - Desconocido   → Real Madrid [1-0] Barcelona
    """
    assist_line = f"🅰️ {assist}" if assist and assist not in ("—", "") else "🅰️ —"
    minute_line = f"⌚ {elapsed}'" if elapsed and elapsed not in ("", "0") else ""

    if goal_side == "home":
        score_str = f"[{home_score}]-{away_score}"
    elif goal_side == "away":
        score_str = f"{home_score}-[{away_score}]"
    else:
        score_str = f"[{home_score}-{away_score}]"

    lines = [
        "*🥅 | ¡GOOOOOL!*",
        "",
        f"*{home_name} [{home_score}-{away_score}] {away_name}*",
        "",
    ]
    if minute_line:
        lines.append(minute_line)
    lines += [
        f"⚽ {scorer}",
        assist_line,
        "",
        "*📲 Suscríbete en t.me/iUniversoFootball*",
    ]
    return "\n".join(lines)


def format_final_message(
    home_name: str, away_name: str,
    home_score: int, away_score: int,
) -> str:
    return "\n".join([
        "*📢 | FINAL DEL PARTIDO*",
        "",
        f"↪️ {home_name} {home_score}-{away_score} {away_name}",
        "",
        "*🎦 Todos los vídeos de los goles disponibles aquí: t.me/ufgoals*",
        "",
        "_⚽ Suscríbete en t.me/iUniversoFootball_",
    ])


# ══════════════════════════════════════════════════════════════════════════════
# LOOPS DE BACKGROUND
# ══════════════════════════════════════════════════════════════════════════════

async def monitor_loop(app: Application):
    """
    Detecta cambios de marcador. Cuando hay gol:
      → Envía mensaje provisional con '⏳ Obteniendo...'
      → Encola PendingGoal para que resolve_loop lo complete
    """
    logger.info("Monitor loop iniciado.")
    while True:
        await asyncio.sleep(POLL_INTERVAL)

        for fid, fix in list(tracked.items()):
            if fix.finished:
                continue

            events = get_scoreboard(fix.league_slug)
            raw = next((e for e in events if e["id"] == fid), None)
            if not raw:
                continue

            raw["_league_slug"] = fix.league_slug
            raw["_league_name"] = fix.league_name
            parsed = parse_scoreboard_event(raw)

            new_home   = parsed["home_score"]
            new_away   = parsed["away_score"]
            status     = parsed["status_type"]
            clock      = parsed["clock"]

            # ── Gol detectado ─────────────────────────────────────────────
            if new_home != fix.home_score or new_away != fix.away_score:
                home_goals_delta = new_home - fix.home_score
                away_goals_delta = new_away - fix.away_score
                n_goals = home_goals_delta + away_goals_delta

                # Determinar qué equipo marcó (para el formato del marcador)
                if home_goals_delta > 0 and away_goals_delta == 0:
                    goal_side = "home"
                elif away_goals_delta > 0 and home_goals_delta == 0:
                    goal_side = "away"
                else:
                    goal_side = ""   # ambos marcaron en el mismo poll (raro)

                logger.info(
                    "⚽ Gol en %s vs %s → %d-%d (side=%s)",
                    fix.home_name, fix.away_name, new_home, new_away, goal_side,
                )
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
                        logger.info("Mensaje provisional enviado (chat=%s).", destination)
                    except Exception as exc:
                        logger.error("Error enviando provisional: %s", exc)

            # ── Final de partido ──────────────────────────────────────────
            if status in ESPN_FINAL_STATUSES and not fix.finished:
                fix.finished = True
                summary = get_fixture_summary(fix.league_slug, fid)
                stats   = get_match_stats(summary) if summary else ({}, {})

                fixture_img = _build_fixture_data(parsed, summary)
                raw_stats   = _stats_to_raw(stats)

                img_path = None
                try:
                    from image_generator import generate_match_summary
                    img_path = generate_match_summary(fixture_img, raw_stats)
                except Exception as exc:
                    logger.error("Error generando imagen final: %s", exc)

                msg      = format_final_message(
                    fix.home_name, fix.away_name,
                    fix.home_score, fix.away_score,
                )
                destination = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
                try:
                    if img_path and os.path.exists(img_path):
                        with open(img_path, "rb") as f:
                            await app.bot.send_photo(
                                chat_id=destination, photo=f, caption=msg,
                                parse_mode="Markdown",
                            )
                    else:
                        await app.bot.send_message(
                            chat_id=destination, text=msg, parse_mode="Markdown",
                        )
                except Exception as exc:
                    logger.error("Error enviando final: %s", exc)

                tracked.pop(fid, None)


async def resolve_loop(app: Application):
    """
    Consulta el summary de ESPN cada RESOLVE_INTERVAL segundos.
    Cuando encuentra el keyEvent del gol, edita el mensaje provisional
    con el goleador y asistidor reales.

    Diagrama del flujo:
    ┌─────────────────────────────────────────────────────────┐
    │  pending_goals (lista de PendingGoal sin resolver)       │
    │                                                         │
    │  Para cada fixture con pendientes:                      │
    │    GET /summary?event=fid                               │
    │      ├─ keyEvents encontrados → parse_goal_event()      │
    │      │     ├─ scorer disponible → edita mensaje ✅      │
    │      │     └─ scorer vacío     → esperar próximo ciclo  │
    │      └─ timeout alcanzado      → edita con "No disp." ⚠️│
    └─────────────────────────────────────────────────────────┘
    """
    logger.info("Resolve loop iniciado.")

    while True:
        await asyncio.sleep(RESOLVE_INTERVAL)

        if not pending_goals:
            continue

        # Agrupar pendientes por fixture (un solo GET de summary por partido)
        by_fixture: dict[str, list[PendingGoal]] = {}
        for pg in pending_goals:
            if not pg.resolved:
                by_fixture.setdefault(pg.fixture_id, []).append(pg)

        for fid, goals in by_fixture.items():
            summary = get_fixture_summary(goals[0].league_slug, fid)
            if not summary:
                # Sumar tiempo de espera igual aunque no haya respuesta
                for pg in goals:
                    pg.elapsed_secs += RESOLVE_INTERVAL
                continue

            key_events = get_match_key_events(summary)
            seen       = resolved_kev.setdefault(fid, set())
            unresolved = [g for g in goals if not g.resolved]

            for kev in key_events:
                kev_id = str(kev.get("id", ""))

                # Ignorar eventos ya usados para resolver otro gol
                if kev_id in seen:
                    continue

                scorer, assist = parse_goal_event(kev)

                # ESPN aún no publicó el goleador → esperar
                if scorer == "—":
                    continue

                # Emparejar con el pending_goal más antiguo sin resolver
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
                    scorer=scorer,
                    assist=assist,
                    goal_side=pg.goal_side,
                    elapsed=pg.elapsed,
                )
                if pg.tg_message:
                    try:
                        await pg.tg_message.edit_text(new_text, parse_mode="Markdown")
                        logger.info(
                            "✅ Mensaje editado — Goleador: %s | Asistidor: %s",
                            scorer, assist,
                        )
                    except BadRequest as exc:
                        logger.warning("BadRequest al editar mensaje de gol: %s", exc)
                    except Exception as exc:
                        logger.error("Error editando mensaje: %s", exc)

            # Sumar tiempo a los pendientes y aplicar timeout
            for pg in goals:
                if not pg.resolved:
                    pg.elapsed_secs += RESOLVE_INTERVAL
                    if pg.elapsed_secs >= RESOLVE_TIMEOUT:
                        pg.resolved = True
                        fallback = format_goal_message(
                            pg.home_name, pg.away_name,
                            pg.home_score, pg.away_score,
                            pg.league_name,
                            scorer="No disponible",
                            assist="—",
                            goal_side=pg.goal_side,
                            elapsed=pg.elapsed,
                        )
                        logger.warning(
                            "⚠️ Timeout resolviendo gol en partido %s. "
                            "Marcando como 'No disponible'.", fid,
                        )
                        if pg.tg_message:
                            try:
                                await pg.tg_message.edit_text(fallback, parse_mode="Markdown")
                            except Exception:
                                pass

        # Limpiar resueltos
        pending_goals[:] = [pg for pg in pending_goals if not pg.resolved]


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS PARA image_generator
# ══════════════════════════════════════════════════════════════════════════════

def _build_fixture_data(parsed: dict, summary: Optional[dict]) -> dict:
    home_logo = away_logo = ""
    if summary:
        teams = summary.get("boxscore", {}).get("teams", [])
        if len(teams) >= 2:
            home_logo = teams[0].get("team", {}).get("logo", "")
            away_logo = teams[1].get("team", {}).get("logo", "")
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
        "👋 *Bot Universo Football activo*\n\n"
        "Comandos:\n"
        "/partidos — Partidos del día (activar monitoreo)\n"
        "/activos  — Ver partidos en monitoreo\n"
        "/stop     — Detener monitoreo de un partido\n"
        "/test     — Preview del post final\n"
        "  `↳ /test` — elegir de finalizados hoy\n"
        "  `↳ /test <event_id>` — cualquier partido ESPN",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_partidos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Consultando ESPN (todas las ligas)…")
    all_events = get_all_today_fixtures()
    if not all_events:
        await msg.edit_text("❌ No se encontraron partidos para hoy en ESPN.")
        return
    await msg.delete()

    # Agrupar por liga
    by_league: dict[str, list] = {}
    for ev in all_events:
        by_league.setdefault(ev.get("_league_name", "Otra"), []).append(ev)

    for league_name, events in by_league.items():
        keyboard = []
        for ev in events:
            p      = parse_scoreboard_event(ev)
            fid    = p["id"]
            slug   = ev.get("_league_slug", "")
            active = "✅ " if fid in tracked else ""
            label  = (
                f"{active}{p['home_name']} vs {p['away_name']} "
                f"({p['home_score']}-{p['away_score']}) · {p['status_desc']}"
            )
            keyboard.append([
                InlineKeyboardButton(label, callback_data=f"toggle:{fid}:{slug}")
            ])
        await update.message.reply_text(
            f"🏆 *{league_name}*",
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
        lines.append(
            f"• `{fid}` — {fix.home_name} {fix.home_score}-{fix.away_score} "
            f"{fix.away_name} ({fix.league_name})"
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
        await update.message.reply_text(f"⏹️ `{fid}` removido del monitoreo.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ `{fid}` no estaba activo.", parse_mode="Markdown")


@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if args:
        await _run_test(args[0], None, update.message)
        return

    msg = await update.message.reply_text("🔍 Buscando partidos finalizados hoy…")
    all_events = get_all_today_fixtures()
    finished = [
        e for e in all_events
        if parse_scoreboard_event(e)["status_type"] in ESPN_FINAL_STATUSES
    ]
    if not finished:
        await msg.edit_text(
            "⚠️ No hay partidos finalizados hoy.\n\nPrueba:\n`/test <event_id>`",
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
            InlineKeyboardButton(
                label, callback_data=f"test:{p['id']}:{ev['_league_slug']}"
            )
        ])
    await update.message.reply_text(
        "🧪 *Preview del post final* — Selecciona un partido:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _run_test(event_id: str, league_slug: Optional[str], message: Message):
    """Genera y envía el preview del post final usando datos reales de ESPN."""
    status_msg = await message.reply_text(
        f"⏳ Obteniendo datos del partido `{event_id}`…", parse_mode="Markdown"
    )

    if not league_slug:
        all_events  = get_all_today_fixtures()
        match       = next((e for e in all_events if e["id"] == event_id), None)
        league_slug = match.get("_league_slug", "esp.1") if match else "esp.1"

    summary = get_fixture_summary(league_slug, event_id)
    if not summary:
        await status_msg.edit_text("❌ No se encontró el partido en ESPN.")
        return

    # Extraer datos
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
        warning = f"\n\n⚠️ _Estado: {status_desc}. El post es una preview._"

    await status_msg.edit_text(
        f"✅ *{home_name} {home_score}-{away_score} {away_name}*\n"
        f"🏆 {league_name} · {status_desc}{warning}\n\n⏳ Generando imagen…",
        parse_mode="Markdown",
    )

    stats        = get_match_stats(summary)
    fixture_data = {
        "fixture": {"id": event_id},
        "league":  {"name": league_name},
        "teams": {
            "home": {"name": home_name, "logo": home_logo},
            "away": {"name": away_name, "logo": away_logo},
        },
        "goals": {"home": home_score, "away": away_score},
    }
    raw_stats = _stats_to_raw(stats)

    img_path = None
    try:
        from image_generator import generate_match_summary
        img_path = generate_match_summary(fixture_data, raw_stats)
    except Exception as exc:
        logger.error("Error generando imagen de test: %s", exc)

    msg_text    = format_final_message(home_name, away_name, home_score, away_score)
    header_test = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧪 *PREVIEW DEL POST FINAL*\n"
        f"`Event ID ESPN: {event_id}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
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
        await status_msg.edit_text(f"❌ Error al enviar el test: {exc}")


# ── Callbacks inline ───────────────────────────────────────────────────────────

async def callback_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Sin permiso.", show_alert=True)
        return

    parts        = query.data.split(":")
    fid          = parts[1]
    league_slug  = parts[2] if len(parts) > 2 else ""

    if fid in tracked:
        tracked.pop(fid)
        await query.answer("⏹️ Partido desactivado.", show_alert=True)
    else:
        events = get_scoreboard(league_slug)
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
                home_score  = p["home_score"],
                away_score  = p["away_score"],
                status      = p["status_type"],
            )
            await query.answer(
                f"✅ Activado: {p['home_name']} vs {p['away_name']}", show_alert=True
            )
        else:
            await query.answer("❌ No se encontró el partido.", show_alert=True)

    # Refrescar teclado
    try:
        events   = get_scoreboard(league_slug)
        keyboard = []
        for ev in events:
            ev["_league_slug"] = league_slug
            ev["_league_name"] = ""
            p2     = parse_scoreboard_event(ev)
            active = "✅ " if p2["id"] in tracked else ""
            label  = (
                f"{active}{p2['home_name']} vs {p2['away_name']} "
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
    await query.answer("⏳ Generando preview…")

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
    asyncio.create_task(monitor_loop(app))
    asyncio.create_task(resolve_loop(app))
    logger.info("Loops de monitoreo y resolución iniciados.")


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

    logger.info("Bot iniciado. Polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
