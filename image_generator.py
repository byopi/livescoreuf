"""
image_generator.py — Resumen final del partido con Pillow.

Logos: football-logos.cc (con fallback a ESPN CDN)
Stats: posesion, xG, tiros totales, tiros a puerta, corners, tarjetas
"""

import io
import os
import re
import logging
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# ─── Assets ────────────────────────────────────────────────────────────────────
ASSETS_DIR     = Path(__file__).parent / "assets"
FONT_PATH      = os.getenv("FONT_PATH", str(ASSETS_DIR / "font.ttf"))
WATERMARK_PATH = str(ASSETS_DIR / "logo_uf.png")
OUTPUT_DIR     = Path("output_images")

# ─── Paleta ────────────────────────────────────────────────────────────────────
C_BG       = (18, 20, 24)
C_CARD     = (28, 31, 38)
C_HEADER   = (22, 25, 32)
C_ACCENT   = (0, 210, 110)
C_WHITE    = (245, 245, 245)
C_GRAY     = (140, 145, 158)
C_DIVIDER  = (42, 47, 60)
C_BAR_L    = (0, 190, 100)
C_BAR_R    = (210, 55, 55)
C_SCORE_BG = (35, 39, 50)

# ─── Dimensiones ───────────────────────────────────────────────────────────────
W, H         = 1080, 740
LOGO_SIZE    = (130, 130)
BAR_H        = 13
BAR_R        = 6
CARD_M       = 28
CARD_R       = 18

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

STATS_ORDER = [
    ("Posesion",          True,  "Posesión"),
    ("xG",                False, "xG"),
    ("Tiros totales",     False, "Tiros totales"),
    ("Tiros a puerta",    False, "Tiros a puerta"),
    ("Corners",           False, "Córners"),
    ("Tarjetas",          False, "Tarjetas"),
]


# ══════════════════════════════════════════════════════════════════════════════
# BÚSQUEDA DE LOGOS EN FOOTBALL-LOGOS.CC
# ══════════════════════════════════════════════════════════════════════════════

def _slugify(name: str) -> str:
    """Convierte 'Real Madrid CF' → 'real-madrid-cf'"""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name


def _search_football_logos(team_name: str) -> Optional[str]:
    """
    Busca el logo en football-logos.cc usando la búsqueda del sitio.
    Devuelve la URL directa del PNG 256x256 o None si no se encuentra.
    """
    slug = _slugify(team_name)
    # Intentar URL directa primero (patrón más común)
    # El sitio usa: /search/?q=<nombre>
    try:
        search_url = f"https://football-logos.cc/search/?q={requests.utils.quote(team_name)}"
        r = requests.get(search_url, headers=HTTP_HEADERS, timeout=10)
        r.raise_for_status()
        html = r.text

        # Buscar URLs de imágenes PNG en el HTML
        pattern = r'https://assets\.football-logos\.cc/logos/[^"\']+256x256/[^"\']+\.png'
        matches = re.findall(pattern, html)

        if matches:
            logger.info("Logo encontrado en football-logos.cc para: %s", team_name)
            return matches[0]

        # Si no encuentra, intentar con slug directamente
        # Buscar cualquier imagen en los resultados
        pattern2 = r'https://assets\.football-logos\.cc/logos/[^"\']+\.png'
        matches2 = re.findall(pattern2, html)
        if matches2:
            return matches2[0]

    except Exception as exc:
        logger.debug("Error buscando en football-logos.cc '%s': %s", team_name, exc)

    return None


def _get_logo(team_name: str, fallback_url: str = "") -> Optional[Image.Image]:
    """
    Obtiene el logo del equipo:
    1. Busca en football-logos.cc
    2. Fallback a URL de ESPN si no encuentra
    """
    # Intentar football-logos.cc
    logo_url = _search_football_logos(team_name)

    # Fallback a ESPN CDN
    if not logo_url and fallback_url:
        logo_url = fallback_url
        logger.debug("Usando logo de ESPN para: %s", team_name)

    if not logo_url:
        return None

    try:
        r = requests.get(logo_url, headers=HTTP_HEADERS, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        img = img.resize(LOGO_SIZE, Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("Error descargando logo %s: %s", logo_url, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PARSEO DE ESTADÍSTICAS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_stats(raw_stats: list[dict]) -> tuple[dict, dict]:
    """
    Acepta tanto claves en español (del bot) como en inglés (ESPN raw).
    Agrupa tarjetas amarillas + rojas en una sola clave "Tarjetas".
    """
    MAP = {
        # Español
        "Posesion":          "Posesion",
        "Posesión":          "Posesion",
        "xG":                "xG",
        "Tiros totales":     "Tiros totales",
        "Tiros a puerta":    "Tiros a puerta",
        "Corners":           "Corners",
        "Tarjetas amarillas":"Tarjetas",
        "Tarjetas rojas":    "Tarjetas",
        # Inglés ESPN
        "possessionPct":     "Posesion",
        "expectedGoals":     "xG",
        "totalShots":        "Tiros totales",
        "shotsOnTarget":     "Tiros a puerta",
        "corners":           "Corners",
        "yellowCards":       "Tarjetas",
        "redCards":          "Tarjetas",
    }
    home_s: dict = {}
    away_s: dict = {}

    for i, block in enumerate(raw_stats[:2]):
        dest = home_s if i == 0 else away_s
        for stat in block.get("statistics", []):
            key = MAP.get(stat.get("type", ""))
            if not key:
                continue
            raw = stat.get("value", 0)
            if raw is None:
                val = 0.0
            elif isinstance(raw, str):
                val = float(raw.replace("%", "").strip() or 0)
            else:
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    val = 0.0
            # Sumar tarjetas (amarillas + rojas)
            dest[key] = dest.get(key, 0.0) + val

    return home_s, away_s


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE DIBUJO
# ══════════════════════════════════════════════════════════════════════════════

def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except (IOError, OSError):
        return ImageFont.load_default()


def _rr(draw: ImageDraw.Draw, xy, r: int, fill, outline=None, ow=0):
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=ow)


def _centered_text(draw: ImageDraw.Draw, text: str, font, y: int, color,
                   canvas_w: int = W):
    tw = draw.textlength(text, font=font)
    draw.text(((canvas_w - tw) / 2, y), text, font=font, fill=color)


def _paste_logo(canvas: Image.Image, logo: Optional[Image.Image],
                draw: ImageDraw.Draw, x: int, y: int, label: str):
    if logo:
        # Sombra
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        r  = LOGO_SIZE[0] // 2 + 5
        cx = x + LOGO_SIZE[0] // 2
        cy = y + LOGO_SIZE[1] // 2
        sd.ellipse([cx-r+5, cy-r+5, cx+r+5, cy+r+5], fill=(0,0,0,70))
        sh = sh.filter(ImageFilter.GaussianBlur(8))
        canvas.alpha_composite(sh)
        canvas.paste(logo, (x, y), logo)
    else:
        # Placeholder
        r  = min(LOGO_SIZE) // 2
        cx = x + LOGO_SIZE[0] // 2
        cy = y + LOGO_SIZE[1] // 2
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=C_DIVIDER)
        f  = _font(20)
        init = "".join(w[0].upper() for w in label.split()[:2])
        iw = draw.textlength(init, font=f)
        draw.text((cx - iw/2, cy - 13), init, font=f, fill=C_WHITE)


def _draw_bar(draw: ImageDraw.Draw, x: int, y: int, bar_w: int,
              vh: float, va: float, label_es: str, is_pct: bool,
              f_lbl, f_val):
    total = vh + va
    rh = vh / total if total > 0 else 0.5

    hw = max(int(bar_w * rh), 4)
    aw = max(bar_w - hw, 4)

    # Label
    lw = draw.textlength(label_es, font=f_lbl)
    draw.text((x + bar_w/2 - lw/2, y), label_es, font=f_lbl, fill=C_GRAY)
    y += 20

    _rr(draw, (x, y, x + hw - 2, y + BAR_H), BAR_R, C_BAR_L)
    _rr(draw, (x + hw + 2, y, x + bar_w, y + BAR_H), BAR_R, C_BAR_R)

    suf = "%" if is_pct else ""
    vhs = f"{int(vh)}{suf}"
    vas = f"{int(va)}{suf}"
    draw.text((x, y + BAR_H + 5), vhs, font=f_val, fill=C_WHITE)
    vaw = draw.textlength(vas, font=f_val)
    draw.text((x + bar_w - vaw, y + BAR_H + 5), vas, font=f_val, fill=C_WHITE)


# ══════════════════════════════════════════════════════════════════════════════
# GENERADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def generate_match_summary(fixture_data: dict, raw_stats: list[dict]) -> str:
    home_name  = fixture_data["teams"]["home"]["name"]
    away_name  = fixture_data["teams"]["away"]["name"]
    home_score = fixture_data["goals"]["home"] or 0
    away_score = fixture_data["goals"]["away"] or 0
    league     = fixture_data["league"]["name"]
    home_logo_fallback = fixture_data["teams"]["home"].get("logo", "")
    away_logo_fallback = fixture_data["teams"]["away"].get("logo", "")

    home_s, away_s = _parse_stats(raw_stats)

    # ── Logos ──────────────────────────────────────────────────────────────
    home_logo = _get_logo(home_name, home_logo_fallback)
    away_logo = _get_logo(away_name, away_logo_fallback)

    # ── Canvas ─────────────────────────────────────────────────────────────
    canvas = Image.new("RGBA", (W, H), C_BG)
    draw   = ImageDraw.Draw(canvas)

    # ── Fuentes ────────────────────────────────────────────────────────────
    f_league = _font(21)
    f_score  = _font(92)
    f_team   = _font(24)
    f_final  = _font(17)
    f_label  = _font(16)
    f_val    = _font(14)
    f_brand  = _font(14)

    # ── Tarjeta ────────────────────────────────────────────────────────────
    _rr(draw, (CARD_M, CARD_M, W-CARD_M, H-CARD_M), CARD_R, C_CARD)

    # ── Cabecera ───────────────────────────────────────────────────────────
    HDR_H = 52
    _rr(draw, (CARD_M, CARD_M, W-CARD_M, CARD_M+HDR_H), CARD_R, C_HEADER)
    _centered_text(draw, league.upper(), f_league, CARD_M + (HDR_H-21)//2, C_ACCENT)

    # ── Logos y nombres ────────────────────────────────────────────────────
    LOGO_Y   = CARD_M + HDR_H + 18
    HOME_X   = CARD_M + 45
    AWAY_X   = W - CARD_M - 45 - LOGO_SIZE[0]
    HOME_CX  = HOME_X + LOGO_SIZE[0] // 2
    AWAY_CX  = AWAY_X + LOGO_SIZE[0] // 2

    _paste_logo(canvas, home_logo, draw, HOME_X, LOGO_Y, home_name)
    _paste_logo(canvas, away_logo, draw, AWAY_X, LOGO_Y, away_name)

    NAME_Y = LOGO_Y + LOGO_SIZE[1] + 8
    fh = _font(22 if len(home_name) <= 14 else 17)
    fa = _font(22 if len(away_name) <= 14 else 17)

    hw = draw.textlength(home_name, font=fh)
    draw.text((HOME_CX - hw/2, NAME_Y), home_name, font=fh, fill=C_WHITE)
    aw = draw.textlength(away_name, font=fa)
    draw.text((AWAY_CX - aw/2, NAME_Y), away_name, font=fa, fill=C_WHITE)

    # ── Marcador ───────────────────────────────────────────────────────────
    score_str = f"{home_score}   {away_score}"
    sw = draw.textlength(score_str, font=f_score)
    SX = (W - sw) / 2
    SY = LOGO_Y + 8

    _rr(draw, (SX-16, SY-6, SX+sw+16, SY+86), 10, C_SCORE_BG)

    # Guion separador
    gw = draw.textlength("-", font=_font(34))
    draw.text(((W-gw)/2, SY+28), "-", font=_font(34), fill=C_GRAY)

    draw.text((SX, SY), score_str, font=f_score, fill=C_WHITE)

    # FINAL label
    _centered_text(draw, "FINAL", f_final, SY + 90, C_ACCENT)

    # ── Divisor ────────────────────────────────────────────────────────────
    DIV_Y = NAME_Y + 40
    draw.line([(CARD_M+18, DIV_Y), (W-CARD_M-18, DIV_Y)], fill=C_DIVIDER, width=1)

    # ── Estadísticas ───────────────────────────────────────────────────────
    BAR_X   = CARD_M + 55
    BAR_W   = W - (CARD_M + 55) * 2
    STATS_Y = DIV_Y + 16
    ROW_H   = 52

    drawn = 0
    for key, is_pct, label_es in STATS_ORDER:
        vh = home_s.get(key, 0.0)
        va = away_s.get(key, 0.0)
        if vh == 0.0 and va == 0.0:
            continue
        _draw_bar(draw, BAR_X, STATS_Y + drawn * ROW_H, BAR_W,
                  vh, va, label_es, is_pct, f_label, f_val)
        drawn += 1

    # ── Marca de agua ──────────────────────────────────────────────────────
    WM_H = 50
    WM_Y = H - CARD_M - WM_H - 6

    if os.path.exists(WATERMARK_PATH):
        try:
            wm   = Image.open(WATERMARK_PATH).convert("RGBA")
            ratio = WM_H / wm.height
            wm_w  = int(wm.width * ratio)
            wm    = wm.resize((wm_w, WM_H), Image.LANCZOS)
            r, g, b, a = wm.split()
            a = a.point(lambda p: int(p * 0.88))
            wm.putalpha(a)
            canvas.alpha_composite(wm, ((W - wm_w) // 2, WM_Y))
        except Exception as exc:
            logger.warning("Error marca de agua: %s", exc)
    else:
        brand = "t.me/iUniversoFootball"
        bw    = draw.textlength(brand, font=f_brand)
        draw.text(((W-bw)/2, WM_Y+10), brand, font=f_brand, fill=C_ACCENT)

    # ── Guardar ────────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(exist_ok=True)
    fid      = fixture_data["fixture"].get("id", "test")
    out_path = str(OUTPUT_DIR / f"match_{fid}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info("Imagen guardada: %s", out_path)
    return out_path
  
