"""
image_generator.py — Genera la imagen de resumen final del partido.
Diseño: fondo antracita, logos de equipos, marcador, estadísticas con
barras de progreso comparativas y marca de agua de Universo Football.
"""

import os
import io
import logging
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# ─── Rutas de assets ───────────────────────────────────────────────────────────
ASSETS_DIR    = Path(__file__).parent / "assets"
FONT_PATH     = os.getenv("FONT_PATH", str(ASSETS_DIR / "font.ttf"))
WATERMARK_PATH = str(ASSETS_DIR / "logo_uf.png")

# ─── Paleta de colores (tema antracita Universo Football) ──────────────────────
C_BG          = (22, 24, 28)          # fondo principal
C_CARD        = (32, 35, 41)          # tarjeta central
C_ACCENT      = (0, 200, 100)         # verde neón
C_TEXT_WHITE  = (240, 240, 240)
C_TEXT_GRAY   = (160, 165, 175)
C_BAR_HOME    = (0, 180, 90)          # barra equipo local
C_BAR_AWAY    = (220, 60, 60)         # barra equipo visitante
C_DIVIDER     = (50, 55, 65)

# ─── Dimensiones ───────────────────────────────────────────────────────────────
W, H          = 1200, 700
LOGO_SIZE     = (130, 130)
BAR_HEIGHT    = 12
BAR_RADIUS    = 6


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Carga la fuente TTF personalizada o cae en la fuente por defecto."""
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except (IOError, OSError):
        logger.warning("Fuente TTF no encontrada en %s. Usando fuente por defecto.", FONT_PATH)
        return ImageFont.load_default()


def _download_image(url: str, size: tuple[int, int]) -> Optional[Image.Image]:
    """Descarga y redimensiona una imagen desde una URL."""
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("No se pudo descargar imagen %s: %s", url, exc)
        return None


def _draw_rounded_rect(draw: ImageDraw.Draw, xy: tuple, radius: int, fill: tuple):
    """Dibuja un rectángulo con esquinas redondeadas."""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill)


def _draw_progress_bar(
    draw: ImageDraw.Draw,
    x: int, y: int, total_w: int,
    val_home: float, val_away: float,
    label: str,
    font_label: ImageFont.FreeTypeFont,
    font_val: ImageFont.FreeTypeFont,
):
    """
    Dibuja una barra de estadística comparativa (home ←→ away).
    val_home y val_away son valores numéricos (porcentaje, cantidad, etc.).
    """
    total = val_home + val_away
    ratio_h = val_home / total if total > 0 else 0.5
    ratio_a = val_away / total if total > 0 else 0.5

    bar_w = total_w
    home_w = int(bar_w * ratio_h)
    away_w = bar_w - home_w

    # Etiqueta central
    label_w = draw.textlength(label, font=font_label)
    draw.text(
        (x + bar_w // 2 - label_w // 2, y),
        label,
        font=font_label,
        fill=C_TEXT_GRAY,
    )
    y += 22

    # Barra home (izquierda, verde)
    if home_w > 0:
        _draw_rounded_rect(draw, (x, y, x + home_w - 2, y + BAR_HEIGHT), BAR_RADIUS, C_BAR_HOME)

    # Barra away (derecha, roja)
    if away_w > 0:
        _draw_rounded_rect(draw, (x + home_w + 2, y, x + bar_w, y + BAR_HEIGHT), BAR_RADIUS, C_BAR_AWAY)

    # Valores
    val_h_str = f"{int(val_home)}{'%' if val_home <= 100 and label == 'Posesión' else ''}"
    val_a_str = f"{int(val_away)}{'%' if val_away <= 100 and label == 'Posesión' else ''}"

    draw.text((x, y + BAR_HEIGHT + 4), val_h_str, font=font_val, fill=C_TEXT_WHITE)
    val_a_w = draw.textlength(val_a_str, font=font_val)
    draw.text((x + bar_w - val_a_w, y + BAR_HEIGHT + 4), val_a_str, font=font_val, fill=C_TEXT_WHITE)


def _add_noise_texture(img: Image.Image, intensity: int = 8) -> Image.Image:
    """Añade textura de grano sutil al fondo."""
    import random
    noise = Image.new("RGB", img.size)
    pixels = noise.load()
    for i in range(img.width):
        for j in range(img.height):
            v = random.randint(-intensity, intensity)
            r, g, b = img.getpixel((i, j))[:3]
            pixels[i, j] = (
                max(0, min(255, r + v)),
                max(0, min(255, g + v)),
                max(0, min(255, b + v)),
            )
    return Image.blend(img.convert("RGB"), noise, alpha=0.12)


# ══════════════════════════════════════════════════════════════════════════════
# PARSEO DE ESTADÍSTICAS
# ══════════════════════════════════════════════════════════════════════════════

STAT_MAP = {
    "Shots on Goal":           "Tiros a puerta",
    "Shots off Goal":          "Tiros fuera",
    "Total Shots":             "Tiros totales",
    "Blocked Shots":           "Tiros bloqueados",
    "Shots insidebox":         "Tiros (área)",
    "Shots outsidebox":        "Tiros (fuera área)",
    "Fouls":                   "Faltas",
    "Corner Kicks":            "Córners",
    "Offsides":                "Fuera de juego",
    "Ball Possession":         "Posesión",
    "Yellow Cards":            "Tarjetas amarillas",
    "Red Cards":               "Tarjetas rojas",
    "Goalkeeper Saves":        "Paradas",
    "Total passes":            "Pases totales",
    "Passes accurate":         "Pases precisos",
    "Passes %":                "Precisión pases",
}

STATS_TO_SHOW = [
    "Posesión",
    "Tiros a puerta",
    "Tiros totales",
    "Córners",
    "Faltas",
    "Tarjetas amarillas",
]


def _parse_stats(raw_stats: list[dict]) -> tuple[dict, dict]:
    """Convierte la respuesta de la API en dicts home/away de estadísticas."""
    home_stats = {}
    away_stats = {}

    for team_data in raw_stats:
        is_home = team_data.get("is_home", None)
        # La API no garantiza is_home, usamos índice
        stats_list = team_data.get("statistics", [])
        dest = home_stats if raw_stats.index(team_data) == 0 else away_stats

        for stat in stats_list:
            key_es = STAT_MAP.get(stat["type"])
            if key_es:
                raw_val = stat["value"]
                if raw_val is None:
                    dest[key_es] = 0.0
                elif isinstance(raw_val, str) and "%" in raw_val:
                    dest[key_es] = float(raw_val.replace("%", "").strip())
                else:
                    try:
                        dest[key_es] = float(raw_val)
                    except (TypeError, ValueError):
                        dest[key_es] = 0.0

    return home_stats, away_stats


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def generate_match_summary(fixture_data: dict, raw_stats: list[dict]) -> str:
    """
    Genera la imagen de resumen final del partido.
    Devuelve la ruta del archivo PNG generado.
    """
    # ── Datos básicos ──────────────────────────────────────────────────────
    home_name  = fixture_data["teams"]["home"]["name"]
    away_name  = fixture_data["teams"]["away"]["name"]
    home_score = fixture_data["goals"]["home"] or 0
    away_score = fixture_data["goals"]["away"] or 0
    league     = fixture_data["league"]["name"]
    home_logo  = fixture_data["teams"]["home"].get("logo", "")
    away_logo  = fixture_data["teams"]["away"].get("logo", "")

    home_stats, away_stats = _parse_stats(raw_stats)

    # ── Canvas ─────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), C_BG)
    img  = _add_noise_texture(img)
    draw = ImageDraw.Draw(img)

    # ── Fuentes ────────────────────────────────────────────────────────────
    f_score   = _load_font(88)
    f_team    = _load_font(28)
    f_league  = _load_font(20)
    f_label   = _load_font(17)
    f_val     = _load_font(15)
    f_footer  = _load_font(16)

    # ── Tarjeta central ────────────────────────────────────────────────────
    card_margin = 40
    _draw_rounded_rect(
        draw,
        (card_margin, card_margin, W - card_margin, H - card_margin),
        radius=24,
        fill=C_CARD,
    )

    # ── Liga ───────────────────────────────────────────────────────────────
    league_w = draw.textlength(league, font=f_league)
    draw.text(
        ((W - league_w) // 2, 65),
        league,
        font=f_league,
        fill=C_ACCENT,
    )

    # ── Logos de equipos ───────────────────────────────────────────────────
    logo_y = 100
    home_logo_img = _download_image(home_logo, LOGO_SIZE)
    away_logo_img = _download_image(away_logo, LOGO_SIZE)

    logo_home_x = 100
    logo_away_x = W - 100 - LOGO_SIZE[0]

    if home_logo_img:
        img.paste(home_logo_img, (logo_home_x, logo_y), home_logo_img)
    else:
        # Placeholder círculo si no hay logo
        draw.ellipse(
            [logo_home_x, logo_y, logo_home_x + LOGO_SIZE[0], logo_y + LOGO_SIZE[1]],
            fill=C_DIVIDER,
        )

    if away_logo_img:
        img.paste(away_logo_img, (logo_away_x, logo_y), away_logo_img)
    else:
        draw.ellipse(
            [logo_away_x, logo_y, logo_away_x + LOGO_SIZE[0], logo_y + LOGO_SIZE[1]],
            fill=C_DIVIDER,
        )

    # ── Nombres de equipos ─────────────────────────────────────────────────
    name_y = logo_y + LOGO_SIZE[1] + 10

    # Home (centrado bajo su logo)
    home_name_w = draw.textlength(home_name, font=f_team)
    home_center = logo_home_x + LOGO_SIZE[0] // 2
    draw.text(
        (home_center - home_name_w // 2, name_y),
        home_name,
        font=f_team,
        fill=C_TEXT_WHITE,
    )

    # Away (centrado bajo su logo)
    away_name_w = draw.textlength(away_name, font=f_team)
    away_center = logo_away_x + LOGO_SIZE[0] // 2
    draw.text(
        (away_center - away_name_w // 2, name_y),
        away_name,
        font=f_team,
        fill=C_TEXT_WHITE,
    )

    # ── Marcador central ───────────────────────────────────────────────────
    score_str = f"{home_score} - {away_score}"
    score_w   = draw.textlength(score_str, font=f_score)
    score_x   = (W - score_w) // 2
    score_y   = logo_y + 10

    # Sombra del marcador
    draw.text((score_x + 3, score_y + 3), score_str, font=f_score, fill=(0, 0, 0, 120))
    draw.text((score_x, score_y), score_str, font=f_score, fill=C_TEXT_WHITE)

    # Etiqueta "FINAL"
    final_lbl  = "FINAL"
    final_w    = draw.textlength(final_lbl, font=f_label)
    draw.text(
        ((W - final_w) // 2, score_y + 95),
        final_lbl,
        font=f_label,
        fill=C_ACCENT,
    )

    # ── Divisor ────────────────────────────────────────────────────────────
    div_y = name_y + 50
    draw.line([(80, div_y), (W - 80, div_y)], fill=C_DIVIDER, width=1)

    # ── Estadísticas ───────────────────────────────────────────────────────
    stats_top   = div_y + 20
    bar_total_w = W - 300          # ancho total de las barras
    bar_x       = 150              # margen izquierdo
    row_h       = 60               # altura de cada fila de estadística

    for i, stat_key in enumerate(STATS_TO_SHOW):
        val_h = home_stats.get(stat_key, 0.0)
        val_a = away_stats.get(stat_key, 0.0)
        y_pos = stats_top + i * row_h

        _draw_progress_bar(
            draw, bar_x, y_pos, bar_total_w,
            val_h, val_a,
            stat_key,
            f_label, f_val,
        )

    # ── Marca de agua (logo Universo Football) ─────────────────────────────
    wm_path = WATERMARK_PATH
    wm_y    = H - 85

    if os.path.exists(wm_path):
        try:
            wm = Image.open(wm_path).convert("RGBA")
            wm_target_h = 50
            wm_ratio    = wm_target_h / wm.height
            wm_w        = int(wm.width * wm_ratio)
            wm          = wm.resize((wm_w, wm_target_h), Image.LANCZOS)

            # Semitransparente
            r, g, b, a = wm.split()
            a = a.point(lambda p: p * 0.85)
            wm.putalpha(a)

            wm_x = (W - wm_w) // 2
            img.paste(wm, (wm_x, wm_y), wm)
        except Exception as exc:
            logger.warning("Error al pegar marca de agua: %s", exc)
    else:
        # Texto de fallback si no hay imagen de logo
        brand     = "t.me/iUniversoFootball"
        brand_w   = draw.textlength(brand, font=f_footer)
        draw.text(
            ((W - brand_w) // 2, wm_y + 10),
            brand,
            font=f_footer,
            fill=C_ACCENT,
        )

    # ── Guardar ────────────────────────────────────────────────────────────
    output_dir  = Path("output_images")
    output_dir.mkdir(exist_ok=True)
    fid         = fixture_data["fixture"]["id"]
    output_path = str(output_dir / f"match_{fid}.png")
    img.save(output_path, "PNG", optimize=True)
    logger.info("Imagen guardada: %s", output_path)
    return output_path
