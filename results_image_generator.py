"""
results_image_generator.py
===========================
Genera una imagen PNG con los resultados del día de una liga.
Formato visual coherente con image_generator.py y standings_image_generator.py.

Uso:
    from results_image_generator import generate_results_image
    path = generate_results_image(results_data, league_name, country_name,
                                   country_flag, date_display, league_tag)
"""

import os
import io
import logging
import requests
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Assets ────────────────────────────────────────────────────────────────────
ASSETS_DIR     = Path(__file__).parent / "assets"
LOGOS_DIR      = ASSETS_DIR / "logos"
FONT_PATH      = os.getenv("FONT_PATH", str(ASSETS_DIR / "font.ttf"))
WATERMARK_PATH = str(ASSETS_DIR / "logo_uf.png")
OUTPUT_DIR     = Path("output_images")
OUTPUT_DIR.mkdir(exist_ok=True)

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UniversoFootballBot/1.0)"}

# ── Paleta ────────────────────────────────────────────────────────────────────
C_BG       = (18, 20, 24)
C_CARD     = (28, 31, 38)
C_HEADER   = (22, 25, 32)
C_ACCENT   = (0, 210, 110)
C_WHITE    = (245, 245, 245)
C_GRAY     = (140, 145, 158)
C_DIVIDER  = (42, 47, 60)
C_ROW_ALT  = (32, 36, 46)
C_LIVE     = (200, 50, 50)
C_SCHED    = (80, 120, 200)
C_FINAL    = (0, 180, 90)
C_SCORE    = (245, 245, 245)


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except (IOError, OSError):
        return ImageFont.load_default()


def _rr(draw: ImageDraw.Draw, xy, r: int, fill, outline=None, ow=0):
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=ow)


def _get_logo(team_name: str, size: int = 36) -> Optional[Image.Image]:
    """Carga logo desde assets/logos/ o TheSportsDB como fallback."""
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


def generate_results_image(
    results:      list[dict],
    league_name:  str,
    country_name: str,
    country_flag: str,
    date_display: str,
    league_tag:   str,
) -> str:
    """
    Genera la imagen de resultados y devuelve la ruta del PNG.

    results: lista de dicts con:
        home, away: str
        hs, as_: int | None  (None si no empezó)
        state: "final" | "live" | "scheduled"
        suffix: "AET" | "PEN" | ""
        clock: str  (minuto si live, hora si scheduled)
    """
    n = len(results)

    # ── Dimensiones dinámicas ──────────────────────────────────────────────────
    LOGO_SZ  = 36
    ROW_H    = 58
    HDR_H    = 90       # cabecera con liga + fecha
    SUBHDR_H = 38       # sub-cabecera con país
    FOOTER_H = 68
    PAD      = 20
    CW       = 1080
    CH       = PAD*2 + HDR_H + SUBHDR_H + n * ROW_H + FOOTER_H + 10

    canvas = Image.new("RGBA", (CW, CH), C_BG)
    draw   = ImageDraw.Draw(canvas)

    f_hdr    = _font(32)
    f_sub    = _font(22)
    f_team   = _font(20)
    f_score  = _font(26)
    f_badge  = _font(18)
    f_brand  = _font(17)

    # ── Tarjeta exterior ──────────────────────────────────────────────────────
    _rr(draw, (PAD, PAD, CW-PAD, CH-PAD), 18, C_CARD)

    # ── Cabecera principal ────────────────────────────────────────────────────
    _rr(draw, (PAD, PAD, CW-PAD, PAD+HDR_H), 18, C_HEADER)

    # Título "🗓 | RESULTADOS"
    title = "🗓 | RESULTADOS"
    tw = draw.textlength(title, font=f_hdr)
    draw.text(((CW - tw) / 2, PAD + 10), title, font=f_hdr, fill=C_ACCENT)

    # Liga + fecha debajo
    sub1 = f"{league_name}  ·  {date_display}"
    sw = draw.textlength(sub1, font=f_sub)
    draw.text(((CW - sw) / 2, PAD + 50), sub1, font=f_sub, fill=C_GRAY)

    # ── Sub-cabecera: país ─────────────────────────────────────────────────────
    SHDR_Y = PAD + HDR_H
    draw.rectangle([PAD+1, SHDR_Y, CW-PAD-1, SHDR_Y+SUBHDR_H], fill=(25, 28, 36))
    country_text = f"{country_flag}  {country_name}"
    ctw = draw.textlength(country_text, font=f_sub)
    draw.text(((CW - ctw) / 2, SHDR_Y + (SUBHDR_H - 22) // 2),
              country_text, font=f_sub, fill=C_WHITE)

    # ── Filas de partidos ─────────────────────────────────────────────────────
    ROW_Y0 = SHDR_Y + SUBHDR_H
    # Columnas X
    LEFT   = PAD + 12
    # Layout: [logo_home][home_name] ... [score/hora] ... [away_name][logo_away]
    HOME_LOGO_X = LEFT
    HOME_NAME_X = LEFT + LOGO_SZ + 10
    AWAY_LOGO_X = CW - PAD - LOGO_SZ - 12
    AWAY_NAME_MAX = AWAY_LOGO_X - 12
    SCORE_CX    = CW // 2          # centro del score

    for i, r in enumerate(results):
        ry     = ROW_Y0 + i * ROW_H
        row_bg = C_ROW_ALT if i % 2 == 0 else C_CARD
        draw.rectangle([PAD+1, ry, CW-PAD-1, ry+ROW_H-1], fill=row_bg)

        cy = ry + (ROW_H - LOGO_SZ) // 2  # top del logo centrado

        # ── Logo local ────────────────────────────────────────────────────────
        home_logo = _get_logo(r["home"], LOGO_SZ)
        if home_logo:
            canvas.paste(home_logo, (HOME_LOGO_X, cy), home_logo)
        else:
            draw.ellipse([HOME_LOGO_X, cy, HOME_LOGO_X+LOGO_SZ, cy+LOGO_SZ], fill=C_DIVIDER)
            init = "".join(w[0].upper() for w in r["home"].split()[:2])
            iw = draw.textlength(init, font=_font(14))
            draw.text((HOME_LOGO_X + LOGO_SZ//2 - iw//2, cy + LOGO_SZ//2 - 9),
                      init, font=_font(14), fill=C_WHITE)

        # ── Nombre local (máx hasta el centro menos score_w/2 - margin) ───────
        home_short = r["home"]
        max_home_px = SCORE_CX - 110
        while draw.textlength(home_short, font=f_team) > (max_home_px - HOME_NAME_X) and len(home_short) > 4:
            home_short = home_short[:-2] + "."
        hn_y = ry + (ROW_H - 20) // 2
        draw.text((HOME_NAME_X, hn_y), home_short, font=f_team, fill=C_WHITE)

        # ── Score / hora central ───────────────────────────────────────────────
        if r["state"] == "final":
            score_str = f"{r['hs']} - {r['as_']}"
            suffix    = f" ({r['suffix']})" if r["suffix"] else ""
            score_col = C_FINAL
            sw2 = draw.textlength(score_str, font=f_score)
            draw.text((SCORE_CX - sw2/2, ry + (ROW_H - 26)//2), score_str,
                      font=f_score, fill=score_col)
            if suffix:
                sf_w = draw.textlength(suffix, font=f_badge)
                draw.text((SCORE_CX - sf_w/2, ry + (ROW_H - 26)//2 + 28), suffix,
                          font=f_badge, fill=C_GRAY)
        elif r["state"] == "live":
            score_str = f"{r['hs']} - {r['as_']}"
            sw2 = draw.textlength(score_str, font=f_score)
            draw.text((SCORE_CX - sw2/2, ry + (ROW_H - 26)//2), score_str,
                      font=f_score, fill=C_LIVE)
            if r["clock"]:
                ck = f"{r['clock']}'"
                ckw = draw.textlength(ck, font=f_badge)
                draw.text((SCORE_CX - ckw/2, ry + (ROW_H - 26)//2 + 28), ck,
                          font=f_badge, fill=C_LIVE)
        else:
            hora = r["clock"] or "--:--"
            hw2 = draw.textlength(hora, font=f_score)
            draw.text((SCORE_CX - hw2/2, ry + (ROW_H - 26)//2), hora,
                      font=f_score, fill=C_SCHED)

        # ── Nombre visitante (derecha, alineado al logo) ───────────────────────
        away_logo = _get_logo(r["away"], LOGO_SZ)
        if away_logo:
            canvas.paste(away_logo, (AWAY_LOGO_X, cy), away_logo)
        else:
            draw.ellipse([AWAY_LOGO_X, cy, AWAY_LOGO_X+LOGO_SZ, cy+LOGO_SZ], fill=C_DIVIDER)
            init = "".join(w[0].upper() for w in r["away"].split()[:2])
            iw = draw.textlength(init, font=_font(14))
            draw.text((AWAY_LOGO_X + LOGO_SZ//2 - iw//2, cy + LOGO_SZ//2 - 9),
                      init, font=_font(14), fill=C_WHITE)

        # Nombre visitante alineado a la derecha del logo
        away_short = r["away"]
        max_away_px = AWAY_LOGO_X - 12
        while draw.textlength(away_short, font=f_team) > (max_away_px - SCORE_CX - 110) and len(away_short) > 4:
            away_short = away_short[:-2] + "."
        an_w = draw.textlength(away_short, font=f_team)
        draw.text((AWAY_LOGO_X - 12 - an_w, hn_y), away_short, font=f_team, fill=C_WHITE)

        # Divisor
        draw.line([(PAD+5, ry+ROW_H-1), (CW-PAD-5, ry+ROW_H-1)], fill=C_DIVIDER, width=1)

    # ── Footer: hashtag + watermark ───────────────────────────────────────────
    FOOT_Y = ROW_Y0 + n * ROW_H + 8

    # Línea decorativa
    draw.line([(PAD+20, FOOT_Y+4), (CW-PAD-20, FOOT_Y+4)], fill=C_DIVIDER, width=1)

    # Hashtag de liga centrado
    ht_w = draw.textlength(league_tag, font=f_brand)
    draw.text(((CW - ht_w) / 2, FOOT_Y + 10), league_tag, font=f_brand, fill=C_ACCENT)

    # Watermark / marca
    WM_Y = FOOT_Y + 34
    if os.path.exists(WATERMARK_PATH):
        try:
            wm    = Image.open(WATERMARK_PATH).convert("RGBA")
            ratio = 32 / wm.height
            wm_w  = int(wm.width * ratio)
            wm    = wm.resize((wm_w, 32), Image.LANCZOS)
            r2, g2, b2, a2 = wm.split()
            a2 = a2.point(lambda p: int(p * 0.85))
            wm.putalpha(a2)
            canvas.alpha_composite(wm, ((CW - wm_w) // 2, WM_Y))
        except Exception as exc:
            logger.warning("Watermark error: %s", exc)
    else:
        brand = "📲 t.me/iUniversoFootball"
        bw = draw.textlength(brand, font=f_brand)
        draw.text(((CW - bw) / 2, WM_Y + 4), brand, font=f_brand, fill=C_ACCENT)

    # ── Guardar ───────────────────────────────────────────────────────────────
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = str(OUTPUT_DIR / f"resultados_{ts}.png")
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    logger.info("Imagen resultados guardada: %s", out_path)
    return out_path
