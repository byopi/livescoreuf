"""
image_generator.py — Resumen final del partido con Pillow.

Logos: ESPN CDN (primario) + TheSportsDB (fallback)
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
    ("Fuera de juego",    False, "Fuera de juego"),
]


# ══════════════════════════════════════════════════════════════════════════════
# BÚSQUEDA DE LOGOS EN FOOTBALL-LOGOS.CC
# ══════════════════════════════════════════════════════════════════════════════

# ─── Cache de logos en memoria ────────────────────────────────────────────────
_logo_cache: dict[str, Optional[str]] = {}


def _slugify(name: str) -> str:
    """'Real Madrid CF' → 'real-madrid-cf'"""
    name = name.lower().strip()
    for k, v in {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}.items():
        name = name.replace(k, v)
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name.strip("-")


# Aliases para nombres que TheSportsDB conoce diferente
_NAME_ALIASES = {
    "internazionale":    "Inter Milan",
    "inter":             "Inter Milan",
    "fc internazionale": "Inter Milan",
    "paris fc":          "Paris FC",
    "ac milan":          "AC Milan",
    "atletico madrid":   "Atletico Madrid",
    "atletico de madrid":"Atletico Madrid",
    "paris saint-germain":"Paris Saint-Germain",
    "psg":               "Paris Saint-Germain",
    "manchester united": "Manchester United",
    "man united":        "Manchester United",
    "manchester city":   "Manchester City",
    "man city":          "Manchester City",
    "tottenham hotspur": "Tottenham Hotspur",
    "spurs":             "Tottenham Hotspur",
    "olympique lyonnais":"Olympique Lyonnais",
    "lyon":              "Olympique Lyonnais",
    "olympique de marseille": "Olympique Marseille",
    "bayer leverkusen":  "Bayer Leverkusen",
    "rb leipzig":        "RB Leipzig",
    "borussia dortmund": "Borussia Dortmund",
    "bvb":               "Borussia Dortmund",
}


def _fetch_logo_thesportsdb(team_name: str) -> Optional[str]:
    """
    Busca el logo en TheSportsDB (API pública, sin key).
    Intenta el nombre original y aliases conocidos.
    """
    names_to_try = [team_name]
    alias = _NAME_ALIASES.get(team_name.lower().strip())
    if alias and alias != team_name:
        names_to_try.append(alias)

    for name in names_to_try:
        try:
            url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={requests.utils.quote(name)}"
            r = requests.get(url, headers=HTTP_HEADERS, timeout=8)
            if r.status_code == 200:
                data = r.json()
                teams = data.get("teams") or []
                if teams:
                    logo = teams[0].get("strTeamBadge") or teams[0].get("strTeamLogo")
                    if logo:
                        logger.info("Logo TheSportsDB para '%s': %s", name, logo)
                        return logo
        except Exception as exc:
            logger.debug("TheSportsDB error '%s': %s", name, exc)

    return None


def _get_logo(team_name: str, fallback_url: str = "") -> Optional[Image.Image]:
    """
    Obtiene el logo del equipo en este orden:
      1. URL de ESPN CDN (ya viene del scoreboard, es la más fiable)
      2. TheSportsDB (API pública, sin key)
    """
    if team_name in _logo_cache:
        logo_url = _logo_cache[team_name]
    else:
        # Primero ESPN (fallback_url), luego TheSportsDB
        logo_url = fallback_url or None
        if not logo_url:
            logo_url = _fetch_logo_thesportsdb(team_name)
        _logo_cache[team_name] = logo_url

    if not logo_url:
        logger.warning("No se encontró logo para: %s", team_name)
        return None

    try:
        r = requests.get(logo_url, headers=HTTP_HEADERS, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        img = img.resize(LOGO_SIZE, Image.LANCZOS)
        return img
    except Exception as exc:
        # Si ESPN falla, intentar TheSportsDB como último recurso
        if logo_url == fallback_url and fallback_url:
            logger.warning("ESPN logo falló, intentando TheSportsDB para: %s", team_name)
            alt_url = _fetch_logo_thesportsdb(team_name)
            if alt_url:
                try:
                    r2 = requests.get(alt_url, headers=HTTP_HEADERS, timeout=10)
                    r2.raise_for_status()
                    img = Image.open(io.BytesIO(r2.content)).convert("RGBA")
                    img = img.resize(LOGO_SIZE, Image.LANCZOS)
                    _logo_cache[team_name] = alt_url
                    return img
                except Exception:
                    pass
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
        "Fuera de juego":    "Fuera de juego",
        # Inglés ESPN
        "possessionPct":     "Posesion",
        "expectedGoals":     "xG",
        "totalShots":        "Tiros totales",
        "shotsOnTarget":     "Tiros a puerta",
        "corners":           "Corners",
        "yellowCards":       "Tarjetas",
        "redCards":          "Tarjetas",
        "offsides":          "Fuera de juego",
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

def generate_match_summary(fixture_data: dict, raw_stats: list[dict] = None) -> str:
    home_name  = fixture_data["teams"]["home"]["name"]
    away_name  = fixture_data["teams"]["away"]["name"]
    home_score = fixture_data["goals"]["home"] or 0
    away_score = fixture_data["goals"]["away"] or 0
    league     = fixture_data["league"]["name"]
    home_logo_fallback = fixture_data["teams"]["home"].get("logo", "")
    away_logo_fallback = fixture_data["teams"]["away"].get("logo", "")

    # ── Logos ──────────────────────────────────────────────────────────────
    home_logo = _get_logo(home_name, home_logo_fallback)
    away_logo = _get_logo(away_name, away_logo_fallback)

    # ── Canvas (formato cuadrado, limpio) ───────────────────────────────────
    CW, CH = 1080, 1080
    canvas = Image.new("RGBA", (CW, CH), C_BG)
    draw   = ImageDraw.Draw(canvas)

    # ── Fuentes ────────────────────────────────────────────────────────────
    f_league = _font(26)
    f_score  = _font(120)
    f_final  = _font(20)
    f_brand  = _font(15)
    f_vs     = _font(38)

    # ── Tarjeta interior ───────────────────────────────────────────────────
    _rr(draw, (CARD_M, CARD_M, CW-CARD_M, CH-CARD_M), CARD_R, C_CARD)

    # ── Cabecera con nombre de liga ─────────────────────────────────────────
    HDR_H = 60
    _rr(draw, (CARD_M, CARD_M, CW-CARD_M, CARD_M+HDR_H), CARD_R, C_HEADER)
    lw = draw.textlength(league.upper(), font=f_league)
    draw.text(((CW - lw) / 2, CARD_M + (HDR_H - 26) // 2), league.upper(),
              font=f_league, fill=C_ACCENT)

    # ── Área central: logos + marcador ─────────────────────────────────────
    # Dividimos el espacio entre cabecera y watermark en tres columnas:
    # [logo local] [marcador] [logo visitante]

    LOGO_SIZE_BIG = (210, 210)
    CONTENT_TOP = CARD_M + HDR_H + 40
    CONTENT_BOT = CH - CARD_M - 80   # reserva para watermark
    CENTER_Y    = (CONTENT_TOP + CONTENT_BOT) // 2

    # Posiciones X de cada columna
    HOME_CX = CARD_M + 40 + LOGO_SIZE_BIG[0] // 2        # centro logo local
    AWAY_CX = CW - CARD_M - 40 - LOGO_SIZE_BIG[0] // 2   # centro logo visit.
    HOME_LX = HOME_CX - LOGO_SIZE_BIG[0] // 2
    AWAY_LX = AWAY_CX - LOGO_SIZE_BIG[0] // 2
    LOGO_Y  = CENTER_Y - LOGO_SIZE_BIG[1] // 2 - 30       # un poco arriba del centro

    # Logos
    def _paste_big(logo, cx, name):
        lx = cx - LOGO_SIZE_BIG[0] // 2
        ly = LOGO_Y
        if logo:
            logo_big = logo.resize(LOGO_SIZE_BIG, Image.LANCZOS)
            # Sombra suave
            sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            sd = ImageDraw.Draw(sh)
            r  = LOGO_SIZE_BIG[0] // 2 + 6
            sd.ellipse([cx-r+6, ly+LOGO_SIZE_BIG[1]//2-r+6,
                        cx+r+6, ly+LOGO_SIZE_BIG[1]//2+r+6], fill=(0,0,0,80))
            sh = sh.filter(ImageFilter.GaussianBlur(10))
            canvas.alpha_composite(sh)
            canvas.paste(logo_big, (lx, ly), logo_big)
        else:
            r  = LOGO_SIZE_BIG[0] // 2
            draw.ellipse([lx, ly, lx+LOGO_SIZE_BIG[0], ly+LOGO_SIZE_BIG[1]],
                         fill=C_DIVIDER)
            fi = _font(28)
            init = "".join(w[0].upper() for w in name.split()[:2])
            iw = draw.textlength(init, font=fi)
            draw.text((cx - iw/2, ly + LOGO_SIZE_BIG[1]//2 - 18), init,
                      font=fi, fill=C_WHITE)

    _paste_big(home_logo, HOME_CX, home_name)
    _paste_big(away_logo, AWAY_CX, away_name)

    # Nombres de equipos bajo los logos
    NAME_Y = LOGO_Y + LOGO_SIZE_BIG[1] + 14
    fh = _font(26 if len(home_name) <= 14 else 20)
    fa = _font(26 if len(away_name) <= 14 else 20)
    hw = draw.textlength(home_name, font=fh)
    draw.text((HOME_CX - hw/2, NAME_Y), home_name, font=fh, fill=C_WHITE)
    aw = draw.textlength(away_name, font=fa)
    draw.text((AWAY_CX - aw/2, NAME_Y), away_name, font=fa, fill=C_WHITE)

    # ── Marcador central ───────────────────────────────────────────────────
    score_str = f"{home_score} - {away_score}"
    sw = draw.textlength(score_str, font=f_score)
    SX = (CW - sw) / 2
    SY = LOGO_Y + LOGO_SIZE_BIG[1] // 2 - 70   # centrado verticalmente con logos

    # Caja del marcador
    PAD_X, PAD_Y = 24, 10
    _rr(draw,
        (SX - PAD_X, SY - PAD_Y, SX + sw + PAD_X, SY + 120 + PAD_Y),
        14, C_SCORE_BG)

    draw.text((SX, SY), score_str, font=f_score, fill=C_WHITE)

    # Etiqueta FINAL
    _centered_text(draw, "FINAL", f_final, SY + 122, C_ACCENT, CW)

    # ── Marca de agua ──────────────────────────────────────────────────────
    WM_H = 56
    WM_Y = CH - CARD_M - WM_H - 10

    if os.path.exists(WATERMARK_PATH):
        try:
            wm    = Image.open(WATERMARK_PATH).convert("RGBA")
            ratio = WM_H / wm.height
            wm_w  = int(wm.width * ratio)
            wm    = wm.resize((wm_w, WM_H), Image.LANCZOS)
            r, g, b, a = wm.split()
            a = a.point(lambda p: int(p * 0.90))
            wm.putalpha(a)
            canvas.alpha_composite(wm, ((CW - wm_w) // 2, WM_Y))
        except Exception as exc:
            logger.warning("Error marca de agua: %s", exc)
    else:
        brand = "t.me/iUniversoFootball"
        bw    = draw.textlength(brand, font=f_brand)
        draw.text(((CW - bw) / 2, WM_Y + 14), brand, font=f_brand, fill=C_ACCENT)

    # ── Guardar ────────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(exist_ok=True)
    fid      = fixture_data["fixture"].get("id", "test")
    out_path = str(OUTPUT_DIR / f"match_{fid}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info("Imagen guardada: %s", out_path)
    return out_path
  
