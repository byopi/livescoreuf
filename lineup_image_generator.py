"""
lineup_image_generator.py
=========================
Genera 2 imágenes de alineación (una por equipo) mostrando
el campo de fútbol con los jugadores posicionados según la formación.

  - Imagen 1 (local):  campo + jugadores + logo + nombre equipo
  - Imagen 2 (visita): campo + jugadores + logo + nombre equipo
                       + caption con el texto de alineaciones

Las dos se envían como media group en un solo mensaje de Telegram.
"""

import io
import os
import logging
import requests
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# ── Assets ─────────────────────────────────────────────────────────────────────
ASSETS_DIR     = Path(__file__).parent / "assets"
LOGOS_DIR      = ASSETS_DIR / "logos"
FONT_PATH      = os.getenv("FONT_PATH", str(ASSETS_DIR / "font.ttf"))
OUTPUT_DIR     = Path("output_images")
OUTPUT_DIR.mkdir(exist_ok=True)

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)"}

# ── Paleta ─────────────────────────────────────────────────────────────────────
C_GRASS_DARK  = (34,  139, 60)
C_GRASS_LIGHT = (40,  160, 70)
C_LINE        = (255, 255, 255)
C_PLAYER_HOME = (255, 220,  50)   # amarillo/dorado para local
C_PLAYER_AWAY = (220,  70,  70)   # rojo para visitante
C_PLAYER_TEXT = (20,   20,  20)
C_CARD_BG     = (15,   15,  20, 210)
C_WHITE       = (245, 245, 245)
C_GRAY        = (160, 160, 160)
C_ACCENT      = (0,   210, 110)

# ── Dimensiones canvas ─────────────────────────────────────────────────────────
CW, CH = 1080, 1080


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except (IOError, OSError):
        return ImageFont.load_default()


def _download_img(url: str, size: tuple) -> Optional[Image.Image]:
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=8)
        if r.status_code == 200 and len(r.content) > 500:
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            return img.resize(size, Image.LANCZOS)
    except Exception:
        pass
    return None


def _get_logo(team_name: str, espn_url: str = "") -> Optional[Image.Image]:
    """Carga el logo: local → ESPN CDN → api-sports CDN."""
    size = (90, 90)
    # 1. Local
    local = LOGOS_DIR / f"{team_name}.png"
    if local.exists():
        try:
            img = Image.open(str(local)).convert("RGBA")
            return img.resize(size, Image.LANCZOS)
        except Exception:
            pass
    # 2. ESPN CDN
    if espn_url:
        img = _download_img(espn_url, size)
        if img:
            return img
    # 3. TheSportsDB
    try:
        r = requests.get(
            "https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
            headers=HTTP_HEADERS, params={"t": team_name}, timeout=6,
        )
        if r.status_code == 200:
            teams = r.json().get("teams") or []
            if teams:
                url = teams[0].get("strTeamBadge") or teams[0].get("strTeamLogo")
                if url:
                    img = _download_img(url, size)
                    if img:
                        return img
    except Exception:
        pass
    return None


# ── Parseo de formación ────────────────────────────────────────────────────────

def parse_formation(formation_str: str) -> list[int]:
    """
    "4-3-3" → [4, 3, 3]
    "4-2-3-1" → [4, 2, 3, 1]
    Siempre excluye el portero (se agrega automáticamente).
    """
    try:
        parts = [int(x) for x in formation_str.strip().split("-") if x.isdigit()]
        if parts and sum(parts) in range(9, 12):
            return parts
    except Exception:
        pass
    return [4, 3, 3]  # fallback


def assign_players_to_lines(players: list[str], formation: list[int]) -> list[list[str]]:
    """
    Asigna los 11 jugadores a las líneas de la formación.
    Retorna lista de listas: [[GK], [DEF...], [MID...], [FWD...]]
    El primer jugador siempre es el portero.
    """
    lines = [[players[0]]]   # portero
    idx = 1
    for count in formation:
        line = []
        for _ in range(count):
            if idx < len(players):
                line.append(players[idx])
                idx += 1
        lines.append(line)
    return lines


# ── Dibujado del campo ─────────────────────────────────────────────────────────

def _draw_pitch(draw: ImageDraw.Draw, x0: int, y0: int, x1: int, y1: int):
    """Dibuja el campo de fútbol con rayas y líneas reglamentarias."""
    pw = x1 - x0
    ph = y1 - y0

    # Rayas de hierba alternadas
    stripe_w = pw // 10
    for i in range(10):
        color = C_GRASS_DARK if i % 2 == 0 else C_GRASS_LIGHT
        draw.rectangle([x0 + i*stripe_w, y0, x0 + (i+1)*stripe_w, y1], fill=color)

    lw = 3  # grosor líneas

    # Borde
    draw.rectangle([x0, y0, x1, y1], outline=C_LINE, width=lw)

    # Línea del medio
    mid_y = y0 + ph // 2
    draw.line([(x0, mid_y), (x1, mid_y)], fill=C_LINE, width=lw)

    # Círculo central
    r = int(pw * 0.1)
    cx, cy = x0 + pw//2, mid_y
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=C_LINE, width=lw)
    draw.ellipse([cx-4, cy-4, cx+4, cy+4], fill=C_LINE)

    # Área grande local (arriba)
    bw = int(pw * 0.55)
    bh = int(ph * 0.15)
    bx = x0 + (pw - bw) // 2
    draw.rectangle([bx, y0, bx+bw, y0+bh], outline=C_LINE, width=lw)

    # Área pequeña local (arriba)
    sw = int(pw * 0.28)
    sh = int(ph * 0.07)
    sx = x0 + (pw - sw) // 2
    draw.rectangle([sx, y0, sx+sw, y0+sh], outline=C_LINE, width=lw)

    # Punto penal local
    draw.ellipse([cx-4, y0+int(ph*0.1)-4, cx+4, y0+int(ph*0.1)+4], fill=C_LINE)

    # Área grande visitante (abajo)
    draw.rectangle([bx, y1-bh, bx+bw, y1], outline=C_LINE, width=lw)

    # Área pequeña visitante (abajo)
    draw.rectangle([sx, y1-sh, sx+sw, y1], outline=C_LINE, width=lw)

    # Punto penal visitante
    draw.ellipse([cx-4, y1-int(ph*0.1)-4, cx+4, y1-int(ph*0.1)+4], fill=C_LINE)

    # Semicírculo área local
    ar = int(pw * 0.1)
    draw.arc([cx-ar, y0+bh-ar, cx+ar, y0+bh+ar], start=0, end=180, fill=C_LINE, width=lw)

    # Semicírculo área visitante
    draw.arc([cx-ar, y1-bh-ar, cx+ar, y1-bh+ar], start=180, end=360, fill=C_LINE, width=lw)


def _draw_player(draw: ImageDraw.Draw, cx: int, cy: int,
                 name: str, color: tuple, font_name, font_num):
    """Dibuja un círculo con el nombre del jugador."""
    r = 22
    # Sombra
    draw.ellipse([cx-r+3, cy-r+3, cx+r+3, cy+r+3], fill=(0, 0, 0, 100))
    # Círculo
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color, outline=C_LINE, width=2)

    # Nombre abreviado (apellido o primer nombre corto)
    short = name.split()[-1] if name.split() else name
    if len(short) > 10:
        short = short[:9] + "."
    tw = draw.textlength(short, font=font_name)
    # Texto debajo del círculo con sombra
    draw.text((cx - tw/2 + 1, cy + r + 4 + 1), short, font=font_name, fill=(0, 0, 0, 180))
    draw.text((cx - tw/2, cy + r + 4), short, font=font_name, fill=C_WHITE)


# ── Generador principal ────────────────────────────────────────────────────────

def generate_lineup_images(
    home_name:    str,
    away_name:    str,
    home_xi:      list[str],
    away_xi:      list[str],
    home_formation: str = "4-3-3",
    away_formation: str = "4-3-3",
    home_logo_url: str = "",
    away_logo_url: str = "",
    league_name:  str  = "",
    match_id:     str  = "test",
) -> tuple[str, str]:
    """
    Genera dos imágenes PNG y devuelve (path_home, path_away).

    - path_home: imagen del equipo local (sin caption)
    - path_away: imagen del equipo visitante (usada como caption con texto)
    """
    home_logo = _get_logo(home_name, home_logo_url)
    away_logo = _get_logo(away_name, away_logo_url)

    path_home = _build_image(
        team_name  = home_name,
        players    = home_xi,
        formation  = home_formation,
        logo       = home_logo,
        player_color = C_PLAYER_HOME,
        is_home    = True,
        league     = league_name,
        match_id   = match_id,
        suffix     = "home",
    )
    path_away = _build_image(
        team_name  = away_name,
        players    = away_xi,
        formation  = away_formation,
        logo       = away_logo,
        player_color = C_PLAYER_AWAY,
        is_home    = False,
        league     = league_name,
        match_id   = match_id,
        suffix     = "away",
    )
    return path_home, path_away


def _build_image(
    team_name:    str,
    players:      list[str],
    formation:    str,
    logo:         Optional[Image.Image],
    player_color: tuple,
    is_home:      bool,
    league:       str,
    match_id:     str,
    suffix:       str,
) -> str:
    canvas = Image.new("RGBA", (CW, CH), (15, 15, 20, 255))
    draw   = ImageDraw.Draw(canvas)

    f_title  = _font(38)
    f_form   = _font(28)
    f_league = _font(22)
    f_player = _font(18)
    f_num    = _font(16)

    # ── Header: logo + nombre equipo + liga ───────────────────────────────────
    HDR_H = 110
    draw.rectangle([0, 0, CW, HDR_H], fill=(22, 25, 32, 255))

    logo_x, logo_y = 24, (HDR_H - 90) // 2
    if logo:
        canvas.paste(logo, (logo_x, logo_y), logo)
    else:
        draw.ellipse([logo_x, logo_y, logo_x+90, logo_y+90], fill=(50, 55, 70))

    text_x = logo_x + 90 + 18
    team_w = draw.textlength(team_name, font=f_title)
    draw.text((text_x, 14), team_name, font=f_title, fill=C_WHITE)

    form_label = f"Formación: {formation}"
    draw.text((text_x, 14 + 44), form_label, font=f_form, fill=C_ACCENT)

    if league:
        lw = draw.textlength(league.upper(), font=f_league)
        draw.text((CW - lw - 20, (HDR_H - 22) // 2), league.upper(),
                  font=f_league, fill=C_GRAY)

    # ── Campo ─────────────────────────────────────────────────────────────────
    PAD_X = 60
    PITCH_TOP    = HDR_H + 20
    PITCH_BOTTOM = CH - 30
    PITCH_LEFT   = PAD_X
    PITCH_RIGHT  = CW - PAD_X

    _draw_pitch(draw, PITCH_LEFT, PITCH_TOP, PITCH_RIGHT, PITCH_BOTTOM)

    # ── Posicionar jugadores ───────────────────────────────────────────────────
    form_parts = parse_formation(formation)
    lines      = assign_players_to_lines(players, form_parts)

    pitch_w = PITCH_RIGHT - PITCH_LEFT
    pitch_h = PITCH_BOTTOM - PITCH_TOP

    # Para equipo local: GK abajo, delanteros arriba
    # Para equipo visitante: GK arriba, delanteros abajo
    # Dividimos el campo en N+1 filas (N = líneas incluyendo GK)
    n_lines = len(lines)   # incluye GK

    for line_idx, line_players in enumerate(lines):
        if not line_players:
            continue

        # Fracción Y dentro del campo (0 = top, 1 = bottom)
        # local: GK en y=0.88, adelante en y=0.12
        # away:  GK en y=0.12, adelante en y=0.88
        frac = (line_idx + 0.5) / n_lines   # 0→1 de GK a delanteros

        if is_home:
            y_frac = 1.0 - frac * 0.88   # GK cerca del fondo
        else:
            y_frac = frac * 0.88 + 0.06  # GK cerca de arriba

        cy = PITCH_TOP + int(pitch_h * y_frac)

        n_players = len(line_players)
        for p_idx, player in enumerate(line_players):
            x_frac = (p_idx + 1) / (n_players + 1)
            cx = PITCH_LEFT + int(pitch_w * x_frac)
            _draw_player(draw, cx, cy, player, player_color, f_player, f_num)

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_path = str(OUTPUT_DIR / f"lineup_{match_id}_{suffix}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info("Imagen lineup guardada: %s", out_path)
    return out_path
