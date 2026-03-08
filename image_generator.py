"""
image_generator.py — Genera la imagen de resumen final del partido.

Layout rediseñado:
  ┌──────────────────────────────────────────────────────┐
  │  NOMBRE DE LA LIGA (centrado, acento verde)           │
  ├──────────────────────────────────────────────────────┤
  │  [LOGO LOCAL]   3  —  1   [LOGO VISITANTE]           │
  │  Equipo Local        Equipo Visitante                 │
  │               FINAL                                   │
  ├──────────────────────────────────────────────────────┤
  │  ESTADÍSTICAS (barras comparativas)                   │
  │  Posesión          55%  ████████░░  45%              │
  │  Tiros a puerta     6  ██████░░░░   4                │
  │  …                                                    │
  ├──────────────────────────────────────────────────────┤
  │            [LOGO UNIVERSO FOOTBALL]                   │
  └──────────────────────────────────────────────────────┘
"""

import io
import os
import logging
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

logger = logging.getLogger(__name__)

# ─── Assets ────────────────────────────────────────────────────────────────────
ASSETS_DIR     = Path(__file__).parent / "assets"
FONT_PATH      = os.getenv("FONT_PATH", str(ASSETS_DIR / "font.ttf"))
WATERMARK_PATH = str(ASSETS_DIR / "logo_uf.png")
OUTPUT_DIR     = Path("output_images")

# ─── Paleta ────────────────────────────────────────────────────────────────────
C_BG        = (18, 20, 24)        # fondo muy oscuro
C_CARD      = (28, 31, 38)        # tarjeta central
C_HEADER    = (22, 25, 32)        # cabecera liga
C_ACCENT    = (0, 210, 110)       # verde neón
C_WHITE     = (245, 245, 245)
C_GRAY      = (150, 155, 165)
C_DIVIDER   = (45, 50, 62)
C_BAR_LEFT  = (0, 190, 100)       # barra local (verde)
C_BAR_RIGHT = (210, 55, 55)       # barra visitante (rojo)
C_SCORE_BG  = (35, 39, 48)        # fondo del recuadro de marcador

# ─── Dimensiones ───────────────────────────────────────────────────────────────
W, H         = 1080, 720
LOGO_SIZE    = (140, 140)
BAR_H        = 14
BAR_RADIUS   = 7
CARD_MARGIN  = 30
CARD_RADIUS  = 20

STATS_TO_SHOW = [
    ("Posesión",          True),   # (nombre, es_porcentaje)
    ("Tiros a puerta",    False),
    ("Tiros totales",     False),
    ("Córners",           False),
    ("Faltas",            False),
    ("Tarjetas amarillas",False),
]


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except (IOError, OSError):
        logger.warning("Fuente TTF no encontrada. Usando fuente por defecto.")
        return ImageFont.load_default()


def _download_logo(url: str, size: tuple) -> Optional[Image.Image]:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("Error descargando logo %s: %s", url, exc)
        return None


def _rounded_rect(draw: ImageDraw.Draw, xy, radius: int, fill, outline=None, outline_width=0):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=outline_width)


def _text_centered(draw: ImageDraw.Draw, text: str, font, y: int, color, width: int = W):
    tw = draw.textlength(text, font=font)
    draw.text(((width - tw) / 2, y), text, font=font, fill=color)
    return tw


def _logo_placeholder(draw: ImageDraw.Draw, x: int, y: int, size: tuple, label: str):
    """Círculo de fallback cuando no hay logo disponible."""
    r = min(size) // 2
    cx, cy = x + size[0] // 2, y + size[1] // 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=C_DIVIDER)
    f = _font(18)
    initials = "".join(w[0].upper() for w in label.split()[:2])
    tw = draw.textlength(initials, font=f)
    draw.text((cx - tw / 2, cy - 12), initials, font=f, fill=C_WHITE)


def _paste_logo(canvas: Image.Image, logo: Optional[Image.Image],
                draw: ImageDraw.Draw, x: int, y: int, size: tuple, label: str):
    if logo:
        # Sombra suave detrás del logo
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sh_draw = ImageDraw.Draw(shadow)
        r = min(size) // 2 + 6
        cx, cy = x + size[0] // 2, y + size[1] // 2
        sh_draw.ellipse([cx - r + 4, cy - r + 4, cx + r + 4, cy + r + 4],
                        fill=(0, 0, 0, 80))
        shadow = shadow.filter(ImageFilter.GaussianBlur(8))
        canvas.alpha_composite(shadow)
        canvas.paste(logo, (x, y), logo)
    else:
        _logo_placeholder(draw, x, y, size, label)


def _draw_stat_bar(draw: ImageDraw.Draw,
                   x: int, y: int, bar_w: int,
                   val_h: float, val_a: float,
                   label: str, is_pct: bool,
                   f_label, f_val):
    total = val_h + val_a
    ratio = val_h / total if total > 0 else 0.5

    home_w = max(int(bar_w * ratio), 4)
    away_w = max(bar_w - home_w, 4)

    # Label centrado
    lw = draw.textlength(label, font=f_label)
    draw.text((x + bar_w / 2 - lw / 2, y), label, font=f_label, fill=C_GRAY)
    y += 20

    # Barra local (izquierda → derecha, verde)
    _rounded_rect(draw, (x, y, x + home_w - 2, y + BAR_H), BAR_RADIUS, C_BAR_LEFT)
    # Barra visitante (derecha, rojo)
    _rounded_rect(draw, (x + home_w + 2, y, x + bar_w, y + BAR_H), BAR_RADIUS, C_BAR_RIGHT)

    # Valores
    suffix = "%" if is_pct else ""
    v_h = f"{int(val_h)}{suffix}"
    v_a = f"{int(val_a)}{suffix}"
    draw.text((x, y + BAR_H + 5), v_h, font=f_val, fill=C_WHITE)
    aw = draw.textlength(v_a, font=f_val)
    draw.text((x + bar_w - aw, y + BAR_H + 5), v_a, font=f_val, fill=C_WHITE)


def _parse_stats(raw_stats: list[dict]) -> tuple[dict, dict]:
    NAME_MAP = {
        "Posesión":           "Posesión",
        "Tiros a puerta":     "Tiros a puerta",
        "Tiros totales":      "Tiros totales",
        "Córners":            "Córners",
        "Faltas":             "Faltas",
        "Tarjetas amarillas": "Tarjetas amarillas",
        # También acepta claves en inglés por si viene de image_generator directamente
        "Shots on Goal":      "Tiros a puerta",
        "Total Shots":        "Tiros totales",
        "Corner Kicks":       "Córners",
        "Fouls":              "Faltas",
        "Yellow Cards":       "Tarjetas amarillas",
        "Ball Possession":    "Posesión",
    }
    home_s: dict = {}
    away_s: dict = {}
    for i, block in enumerate(raw_stats[:2]):
        dest = home_s if i == 0 else away_s
        for stat in block.get("statistics", []):
            key = NAME_MAP.get(stat.get("type", ""))
            if not key:
                continue
            raw = stat.get("value", 0)
            if raw is None:
                dest[key] = 0.0
            elif isinstance(raw, str):
                dest[key] = float(raw.replace("%", "").strip() or 0)
            else:
                try:
                    dest[key] = float(raw)
                except (TypeError, ValueError):
                    dest[key] = 0.0
    return home_s, away_s


# ══════════════════════════════════════════════════════════════════════════════
# GENERADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def generate_match_summary(fixture_data: dict, raw_stats: list[dict]) -> str:
    """
    Genera y guarda la imagen de resumen. Devuelve la ruta del PNG.
    """
    home_name  = fixture_data["teams"]["home"]["name"]
    away_name  = fixture_data["teams"]["away"]["name"]
    home_score = fixture_data["goals"]["home"] or 0
    away_score = fixture_data["goals"]["away"] or 0
    league     = fixture_data["league"]["name"]
    home_logo_url = fixture_data["teams"]["home"].get("logo", "")
    away_logo_url = fixture_data["teams"]["away"].get("logo", "")

    home_stats, away_stats = _parse_stats(raw_stats)

    # ── Canvas base ────────────────────────────────────────────────────────
    canvas = Image.new("RGBA", (W, H), C_BG)
    draw   = ImageDraw.Draw(canvas)

    # ── Tarjeta principal ──────────────────────────────────────────────────
    _rounded_rect(draw,
                  (CARD_MARGIN, CARD_MARGIN, W - CARD_MARGIN, H - CARD_MARGIN),
                  CARD_RADIUS, C_CARD)

    # ── Fuentes ────────────────────────────────────────────────────────────
    f_league  = _font(22)
    f_score   = _font(96)
    f_team    = _font(26)
    f_final   = _font(18)
    f_label   = _font(16)
    f_val     = _font(14)
    f_brand   = _font(15)

    # ── Cabecera liga ──────────────────────────────────────────────────────
    header_h = 54
    _rounded_rect(draw,
                  (CARD_MARGIN, CARD_MARGIN,
                   W - CARD_MARGIN, CARD_MARGIN + header_h),
                  CARD_RADIUS, C_HEADER)
    _text_centered(draw, league.upper(), f_league,
                   CARD_MARGIN + (header_h - 22) // 2, C_ACCENT)

    # ── Descargar logos (en paralelo sería ideal, pero requests es sync) ───
    home_logo = _download_logo(home_logo_url, LOGO_SIZE)
    away_logo = _download_logo(away_logo_url, LOGO_SIZE)

    # ── Zona central: logos + marcador ────────────────────────────────────
    zone_top = CARD_MARGIN + header_h + 20
    logo_y   = zone_top

    logo_home_x = CARD_MARGIN + 40
    logo_away_x = W - CARD_MARGIN - 40 - LOGO_SIZE[0]

    _paste_logo(canvas, home_logo, draw, logo_home_x, logo_y, LOGO_SIZE, home_name)
    _paste_logo(canvas, away_logo, draw, logo_away_x, logo_y, LOGO_SIZE, away_name)

    # Nombres de equipo bajo los logos
    name_y = logo_y + LOGO_SIZE[1] + 8
    f_team_home = _font(24 if len(home_name) <= 14 else 18)
    f_team_away = _font(24 if len(away_name) <= 14 else 18)

    home_cx = logo_home_x + LOGO_SIZE[0] // 2
    away_cx = logo_away_x + LOGO_SIZE[0] // 2

    hw = draw.textlength(home_name, font=f_team_home)
    draw.text((home_cx - hw / 2, name_y), home_name, font=f_team_home, fill=C_WHITE)

    aw = draw.textlength(away_name, font=f_team_away)
    draw.text((away_cx - aw / 2, name_y), away_name, font=f_team_away, fill=C_WHITE)

    # ── Marcador central ───────────────────────────────────────────────────
    score_str = f"{home_score}  {away_score}"
    sw = draw.textlength(score_str, font=f_score)
    score_x = (W - sw) / 2
    score_y = logo_y + 10

    # Fondo del marcador
    pad = 18
    _rounded_rect(draw,
                  (score_x - pad, score_y - 8,
                   score_x + sw + pad, score_y + 90),
                  12, C_SCORE_BG)

    # Guión separador entre números
    dash_x = W / 2
    dash_y = score_y + 30
    draw.text((dash_x - 10, dash_y), "—", font=_font(36), fill=C_GRAY)

    # Números del marcador
    draw.text((score_x, score_y), score_str, font=f_score, fill=C_WHITE)

    # Etiqueta FINAL
    final_y = score_y + 94
    _text_centered(draw, "FINAL", f_final, final_y, C_ACCENT)

    # ── Divisor ────────────────────────────────────────────────────────────
    div_y = name_y + 42
    draw.line([(CARD_MARGIN + 20, div_y), (W - CARD_MARGIN - 20, div_y)],
              fill=C_DIVIDER, width=1)

    # ── Estadísticas ───────────────────────────────────────────────────────
    stats_x    = CARD_MARGIN + 60
    bar_total  = W - (CARD_MARGIN + 60) * 2
    stats_top  = div_y + 18
    row_height = 54

    for i, (stat_key, is_pct) in enumerate(STATS_TO_SHOW):
        val_h = home_stats.get(stat_key, 0.0)
        val_a = away_stats.get(stat_key, 0.0)
        if val_h == 0 and val_a == 0:
            continue
        _draw_stat_bar(
            draw, stats_x, stats_top + i * row_height, bar_total,
            val_h, val_a, stat_key, is_pct,
            f_label, f_val,
        )

    # ── Marca de agua (logo Universo Football) ─────────────────────────────
    wm_h   = 52
    wm_y   = H - CARD_MARGIN - wm_h - 8

    if os.path.exists(WATERMARK_PATH):
        try:
            wm = Image.open(WATERMARK_PATH).convert("RGBA")
            ratio = wm_h / wm.height
            wm_w  = int(wm.width * ratio)
            wm    = wm.resize((wm_w, wm_h), Image.LANCZOS)

            # Aumentar contraste y aplicar transparencia
            r, g, b, a = wm.split()
            a = a.point(lambda p: int(p * 0.88))
            wm.putalpha(a)

            wm_x = (W - wm_w) // 2
            canvas.alpha_composite(wm, (wm_x, wm_y))
        except Exception as exc:
            logger.warning("Error pegando marca de agua: %s", exc)
    else:
        brand = "t.me/iUniversoFootball"
        bw    = draw.textlength(brand, font=f_brand)
        draw.text(((W - bw) / 2, wm_y + 10), brand, font=f_brand, fill=C_ACCENT)

    # ── Guardar ────────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(exist_ok=True)
    fid        = fixture_data["fixture"].get("id", "test")
    out_path   = str(OUTPUT_DIR / f"match_{fid}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info("Imagen guardada: %s", out_path)
    return out_path
    
