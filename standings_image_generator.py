"""
standings_image_generator.py
=============================
Genera una imagen PNG con la tabla de clasificación de una liga.
Diseño coherente con image_generator.py (misma paleta y fuentes).

Uso:
    from standings_image_generator import generate_standings_image
    path = generate_standings_image(slug, entries, league_name, week_num)
"""

import os
import io
import logging
import requests
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# ── Assets (mismas rutas que image_generator) ─────────────────────────────────
ASSETS_DIR     = Path(__file__).parent / "assets"
LOGOS_DIR      = ASSETS_DIR / "logos"
FONT_PATH      = os.getenv("FONT_PATH", str(ASSETS_DIR / "font.ttf"))
WATERMARK_PATH = str(ASSETS_DIR / "logo_uf.png")
OUTPUT_DIR     = Path("output_images")
OUTPUT_DIR.mkdir(exist_ok=True)

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)"}

# ── Paleta (idéntica a image_generator) ───────────────────────────────────────
C_BG      = (18, 20, 24)
C_CARD    = (28, 31, 38)
C_HEADER  = (22, 25, 32)
C_ACCENT  = (0, 210, 110)
C_WHITE   = (245, 245, 245)
C_GRAY    = (140, 145, 158)
C_DIVIDER = (42, 47, 60)
C_ROW_ALT = (32, 36, 46)          # fila alternada
C_UCL     = (30, 80, 200)         # azul UCL
C_UEL     = (200, 100, 20)        # naranja UEL
C_UECL    = (20, 150, 80)         # verde UECL
C_REL     = (180, 30, 30)         # rojo descenso
C_PLAYOFF = (140, 50, 140)        # morado playoff
C_CHAMP   = (180, 150, 10)        # dorado campeón


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except (IOError, OSError):
        return ImageFont.load_default()


def _rr(draw: ImageDraw.Draw, xy, r: int, fill, outline=None, ow=0):
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=ow)


def _get_logo_small(team_name: str, size: int = 32) -> Optional[Image.Image]:
    """Carga logo del equipo: local primero, luego TheSportsDB."""
    sanitized = team_name
    for ch in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
        sanitized = sanitized.replace(ch, "-")
    local = LOGOS_DIR / f"{sanitized.strip()}.png"
    if local.exists():
        try:
            img = Image.open(str(local)).convert("RGBA")
            return img.resize((size, size), Image.LANCZOS)
        except Exception:
            pass
    # TheSportsDB fallback
    try:
        r = requests.get(
            "https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
            headers=HTTP_HEADERS, params={"t": team_name}, timeout=5,
        )
        if r.status_code == 200:
            teams = r.json().get("teams") or []
            if teams:
                url = teams[0].get("strTeamBadge") or teams[0].get("strTeamLogo")
                if url:
                    resp = requests.get(url, headers=HTTP_HEADERS, timeout=6)
                    if resp.status_code == 200 and len(resp.content) > 200:
                        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                        return img.resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    return None


def _zone_color(pos: int, n: int, slug: str) -> Optional[tuple]:
    """Devuelve el color de la franja lateral según la zona competitiva."""
    HAS_PLAYOFF = {"ger.1", "fra.1"}

    if slug == "eng.1":
        # Premier League: 5 cupos UCL esta temporada
        ucl = 5; uel = 6; uecl = 7
        rel_count = 3
        playoff = False
    elif slug in ("esp.1", "ger.1", "ita.1", "fra.1"):
        ucl = 4; uel = 5; uecl = 6
        rel_count = 3
        playoff = slug in HAS_PLAYOFF
    elif slug in ("por.1", "ned.1", "tur.1"):
        ucl = 1; uel = 2; uecl = 3
        rel_count = 2
        playoff = False
    else:
        return None

    if pos == 1:
        return C_CHAMP
    if 1 < pos <= ucl:
        return C_UCL
    if ucl < pos <= uel:
        return C_UEL
    if uel < pos <= uecl:
        return C_UECL

    rel_start = n - rel_count
    playoff_pos = rel_start - (1 if playoff else 0)

    if playoff and pos == playoff_pos:
        return C_PLAYOFF
    if pos > rel_start:
        return C_REL
    return None


def generate_standings_image(
    slug:        str,
    entries:     list[dict],
    league_name: str,
    week_num:    str = "",
) -> str:
    """
    Genera la imagen de la tabla de clasificación.

    entries: lista de dicts con claves:
        name, pts, pj, v, e, d, gf, gc, dg
    Devuelve la ruta del archivo PNG generado.
    """
    n = len(entries)

    # ── Dimensiones dinámicas ─────────────────────────────────────────────────
    ROW_H    = 44          # altura de cada fila
    HDR_H    = 72          # cabecera liga
    COL_HDR  = 40          # cabecera de columnas
    FOOTER_H = 70          # watermark + suscripción
    LEGEND_H = 30 if slug in ("esp.1","eng.1","ger.1","ita.1","fra.1",
                               "por.1","ned.1","tur.1") else 0
    PAD      = 24          # margen exterior
    CW       = 1080
    CH       = PAD*2 + HDR_H + COL_HDR + n * ROW_H + LEGEND_H + FOOTER_H + 16

    canvas = Image.new("RGBA", (CW, CH), C_BG)
    draw   = ImageDraw.Draw(canvas)

    # Fuentes
    f_hdr   = _font(30)
    f_week  = _font(20)
    f_col   = _font(18)
    f_team  = _font(20)
    f_num   = _font(20)
    f_brand = _font(16)
    f_leg   = _font(15)

    # ── Tarjeta exterior ──────────────────────────────────────────────────────
    _rr(draw, (PAD, PAD, CW-PAD, CH-PAD), 18, C_CARD)

    # ── Cabecera (liga) ───────────────────────────────────────────────────────
    _rr(draw, (PAD, PAD, CW-PAD, PAD+HDR_H), 18, C_HEADER)
    title = league_name.upper()
    if week_num:
        title += f"  ·  JORNADA {week_num}"
    tw = draw.textlength(title, font=f_hdr)
    draw.text(((CW - tw) / 2, PAD + (HDR_H - 30) // 2), title,
              font=f_hdr, fill=C_ACCENT)

    # ── Cabecera de columnas ───────────────────────────────────────────────────
    COL_Y = PAD + HDR_H + 2
    draw.rectangle([PAD+1, COL_Y, CW-PAD-1, COL_Y + COL_HDR], fill=C_HEADER)

    # Anchuras de columnas (px desde el borde izquierdo de la tarjeta)
    LEFT   = PAD + 10
    C_POS  = LEFT        # posición #
    C_LOGO = LEFT + 34   # logo
    C_NAME = LEFT + 74   # nombre
    C_PJ   = CW - 340    # partidos jugados
    C_PTS  = CW - 285    # puntos
    C_V    = CW - 235    # victorias
    C_E    = CW - 190    # empates
    C_D    = CW - 145    # derrotas
    C_DG   = CW - 90     # diferencia de goles
    COL_CY = COL_Y + (COL_HDR - 18) // 2

    for label, cx in [("PJ", C_PJ), ("PTS", C_PTS), ("V", C_V),
                       ("E", C_E),  ("D",   C_D),    ("DG", C_DG)]:
        lw = draw.textlength(label, font=f_col)
        draw.text((cx - lw/2, COL_CY), label, font=f_col, fill=C_GRAY)

    # ── Filas ──────────────────────────────────────────────────────────────────
    ROW_Y0 = COL_Y + COL_HDR

    for i, t in enumerate(entries):
        pos  = i + 1
        ry   = ROW_Y0 + i * ROW_H
        # Fondo alterno
        row_bg = C_ROW_ALT if i % 2 == 0 else C_CARD
        draw.rectangle([PAD+1, ry, CW-PAD-1, ry+ROW_H-1], fill=row_bg)

        # Franja de color de zona (barra lateral izquierda, 4px)
        zone_col = _zone_color(pos, n, slug)
        if zone_col:
            draw.rectangle([PAD+1, ry, PAD+5, ry+ROW_H-1], fill=zone_col)

        cy = ry + (ROW_H - 20) // 2  # centrado vertical del texto

        # Posición
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        pos_str = medals.get(pos, str(pos))
        if pos > 3:
            pw = draw.textlength(pos_str, font=f_num)
            draw.text((C_POS + (28 - pw)/2, cy), pos_str, font=f_num, fill=C_GRAY)
        else:
            # emoji: usa textlength con font por defecto para emojis
            draw.text((C_POS, cy - 2), pos_str, font=f_num, fill=C_WHITE)

        # Logo del equipo
        logo = _get_logo_small(t["name"], size=32)
        if logo:
            ly = ry + (ROW_H - 32) // 2
            canvas.paste(logo, (C_LOGO, ly), logo)
        else:
            # Círculo placeholder con iniciales
            draw.ellipse([C_LOGO, ry+6, C_LOGO+32, ry+ROW_H-6], fill=C_DIVIDER)
            init = "".join(w[0].upper() for w in t["name"].split()[:2])
            iw = draw.textlength(init, font=_font(13))
            draw.text((C_LOGO + 16 - iw/2, ry + ROW_H//2 - 8), init,
                      font=_font(13), fill=C_WHITE)

        # Nombre del equipo
        max_chars = 22
        name_display = t["name"] if len(t["name"]) <= max_chars else t["name"][:max_chars-1] + "."
        draw.text((C_NAME, cy), name_display, font=f_team, fill=C_WHITE)

        # Estadísticas numéricas
        def _num(val, cx_center, highlight=False):
            s = str(val)
            if isinstance(val, int) and val > 0 and highlight:
                s = f"+{val}"
            nw = draw.textlength(s, font=f_num)
            col = C_ACCENT if highlight and isinstance(val, int) and val > 0 else (
                  C_REL if highlight and isinstance(val, int) and val < 0 else C_WHITE)
            draw.text((cx_center - nw/2, cy), s, font=f_num, fill=col)

        _num(t["pj"],  C_PJ)
        _num(t["pts"], C_PTS, highlight=False)
        # Puntos en verde si líder
        if pos == 1:
            pts_s = str(t["pts"])
            pw2 = draw.textlength(pts_s, font=f_num)
            draw.text((C_PTS - pw2/2, cy), pts_s, font=f_num, fill=C_ACCENT)
        else:
            _num(t["pts"], C_PTS)
        _num(t["v"],   C_V)
        _num(t["e"],   C_E)
        _num(t["d"],   C_D)
        _num(t["dg"],  C_DG, highlight=True)

        # Línea divisoria
        draw.line([(PAD+5, ry+ROW_H-1), (CW-PAD-5, ry+ROW_H-1)], fill=C_DIVIDER, width=1)

    # ── Leyenda de zonas ──────────────────────────────────────────────────────
    LEG_Y = ROW_Y0 + n * ROW_H + 8
    if LEGEND_H > 0 and slug in ("esp.1","eng.1","ger.1","ita.1","fra.1",
                                   "por.1","ned.1","tur.1"):
        HAS_PLAYOFF = {"ger.1", "fra.1"}
        zones = [
            (C_UCL,     "Champions"),
            (C_UEL,     "Europa L."),
            (C_UECL,    "Conference"),
        ]
        if slug in HAS_PLAYOFF:
            zones.append((C_PLAYOFF, "Playoff"))
        zones.append((C_REL, "Descenso"))
        lx = PAD + 14
        for color, label in zones:
            draw.rectangle([lx, LEG_Y+6, lx+14, LEG_Y+22], fill=color)
            draw.text((lx+18, LEG_Y+5), label, font=f_leg, fill=C_GRAY)
            lx += draw.textlength(label, font=f_leg) + 38

    # ── Watermark / marca ─────────────────────────────────────────────────────
    WM_Y = CH - PAD - 52
    if os.path.exists(WATERMARK_PATH):
        try:
            wm    = Image.open(WATERMARK_PATH).convert("RGBA")
            ratio = 40 / wm.height
            wm_w  = int(wm.width * ratio)
            wm    = wm.resize((wm_w, 40), Image.LANCZOS)
            r, g, b, a = wm.split()
            a = a.point(lambda p: int(p * 0.85))
            wm.putalpha(a)
            canvas.alpha_composite(wm, ((CW - wm_w) // 2, WM_Y))
        except Exception as exc:
            logger.warning("Watermark error: %s", exc)
    else:
        brand = "📲 t.me/iUniversoFootball"
        bw = draw.textlength(brand, font=f_brand)
        draw.text(((CW - bw) / 2, WM_Y + 6), brand, font=f_brand, fill=C_ACCENT)

    # ── Guardar ───────────────────────────────────────────────────────────────
    out_path = str(OUTPUT_DIR / f"tabla_{slug.replace('.','_')}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info("Tabla imagen guardada: %s", out_path)
    return out_path
