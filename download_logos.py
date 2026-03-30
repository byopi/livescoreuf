"""
download_logos.py
=================
Descarga automáticamente los logos de TODOS los equipos de las ligas
configuradas, consultando directamente la API de ESPN para obtener
los nombres y URLs de logos sin necesidad de hardcodear nada.

Añadir al Build Command de Render:
    pip install -r requirements.txt && python download_logos.py

O ejecutar localmente:
    pip install requests Pillow
    python download_logos.py
"""

import io
import time
import json
import requests
from pathlib import Path
from PIL import Image

OUT_DIR = Path("assets/logos")
IDS_FILE = Path("assets/logo_ids.json")
SIZE    = (210, 210)
DELAY   = 0.3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
session = requests.Session()
session.headers.update(HEADERS)

# ──────────────────────────────────────────────────────────────────────────────
# LIGAS A CUBRIR — ESPN slugs
# Cada entrada: ("Nombre legible", "slug_espn", n_seasons_back)
# ──────────────────────────────────────────────────────────────────────────────
LEAGUES = [
    # ── Francia ───────────────────────────────────────────────────────────────
    ("Ligue 1",                 "fra.1"),
    ("Ligue 2",                 "fra.2"),
    # ── Alemania ──────────────────────────────────────────────────────────────
    ("Bundesliga",              "ger.1"),
    ("2. Bundesliga",           "ger.2"),
    # ── España ────────────────────────────────────────────────────────────────
    ("La Liga",                 "esp.1"),
    ("Segunda División",        "esp.2"),
    ("Primera RFEF",            "esp.3"),
    # ── Inglaterra ────────────────────────────────────────────────────────────
    ("Premier League",          "eng.1"),
    ("Championship",            "eng.2"),
    ("League One",              "eng.3"),
    ("League Two",              "eng.4"),
    # ── Italia ────────────────────────────────────────────────────────────────
    ("Serie A",                 "ita.1"),
    ("Serie B",                 "ita.2"),
    # ── Portugal ──────────────────────────────────────────────────────────────
    ("Primeira Liga",           "por.1"),
    # ── Países Bajos ──────────────────────────────────────────────────────────
    ("Eredivisie",              "ned.1"),
    # ── Turquía ───────────────────────────────────────────────────────────────
    ("Süper Lig",               "tur.1"),
    # ── Venezuela ─────────────────────────────────────────────────────────────
    ("Liga FUTVE",              "ven.1"),
    # ── UEFA ──────────────────────────────────────────────────────────────────
    ("Champions League",        "uefa.champions"),
    ("Europa League",           "uefa.europa"),
    ("Conference League",       "uefa.europa.conf"),
    # ── Copas nacionales ──────────────────────────────────────────────────────
    ("Copa del Rey",            "esp.copa_del_rey"),
    ("FA Cup",                  "eng.fa"),
    ("EFL Cup",                 "eng.league_cup"),
    ("DFB-Pokal",               "ger.dfb_pokal"),
    ("Coppa Italia",            "ita.coppa_italia"),
    ("Coupe de France",         "fra.coupe_de_france"),
    # ── Sudamérica ────────────────────────────────────────────────────────────
    ("Copa Libertadores",       "conmebol.libertadores"),
    ("Copa Sudamericana",       "conmebol.sudamericana"),
    # ── Selecciones ───────────────────────────────────────────────────────────
    ("Nations League",          "uefa.nations"),
    ("Clasificación UEFA",      "uefa.worldq"),
    ("Clasificación CONMEBOL",  "conmebol.worldq"),
    ("Clasificación CONCACAF",  "concacaf.worldq"),
    ("Clasificación AFC",       "afc.worldq"),
    ("Clasificación CAF",       "caf.worldq"),
    ("Clasificación OFC",       "ofc.worldq"),
    ("Copa América",            "conmebol.america"),
    ("Eurocopa",                "uefa.euro"),
    ("Mundial 2026",            "fifa.world"),
    ("Mundial de Clubes",       "fifa.cwc"),
    ("Eliminatorias CONMEBOL",  "conmebol.worldq"),
    # ── Amistosos ─────────────────────────────────────────────────────────────
    ("Amistosos Internacionales", "fifa.friendly"),
    ("Amistosos de Clubes",       "club.friendly"),
    ("FIFA Series",               "fifa.series")
]

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def load_ids() -> dict:
    try:
        if IDS_FILE.exists():
            return json.loads(IDS_FILE.read_text())
    except Exception:
        pass
    return {}

def save_ids(data: dict):
    try:
        IDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"    Aviso: no se pudo guardar logo_ids.json: {e}")

def load_img(data: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        return img.resize(SIZE, Image.LANCZOS)
    except Exception:
        return None

def get_url(url: str) -> Image.Image | None:
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200 and len(r.content) > 500:
            return load_img(r.content)
    except Exception:
        pass
    return None

def from_thesportsdb(name: str) -> Image.Image | None:
    try:
        r = session.get(
            "https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
            params={"t": name}, timeout=8,
        )
        if r.status_code == 200:
            teams = r.json().get("teams") or []
            if teams:
                url = teams[0].get("strTeamBadge") or teams[0].get("strTeamLogo")
                if url:
                    return get_url(url)
    except Exception:
        pass
    return None

def from_apisports(team_id: int) -> Image.Image | None:
    if not team_id:
        return None
    return get_url(f"https://media.api-sports.io/football/teams/{team_id}.png")

def sanitize_filename(name: str) -> str:
    bad = ["/", chr(92), ":", "*", "?", chr(34), "<", ">", "|"]
    for ch in bad:
        name = name.replace(ch, "-")
    return name.strip()

def fetch_espn_teams(slug: str) -> list[dict]:
    """
    Consulta el scoreboard de ESPN y extrae todos los equipos únicos
    con su displayName y URL de logo.
    Retorna lista de {"name": str, "logo": str}
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
    teams = {}
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return []
        for ev in r.json().get("events", []):
            for comp in ev.get("competitions", []):
                for c in comp.get("competitors", []):
                    t = c.get("team", {})
                    name = t.get("displayName", "")
                    logo = t.get("logo", "")
                    if name and name not in teams:
                        teams[name] = logo
    except Exception:
        pass
    return [{"name": k, "logo": v} for k, v in teams.items()]

def fetch_espn_teams_from_standing(slug: str) -> list[dict]:
    """
    Alternativa: usa el endpoint de standings que devuelve todos
    los equipos de la liga aunque no haya partidos hoy.
    """
    url = f"https://site.web.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
    teams = {}
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        for group in data.get("children", [data]):
            for entry in group.get("standings", {}).get("entries", []):
                t = entry.get("team", {})
                name = t.get("displayName", "")
                logo = t.get("logos", [{}])[0].get("href", "") if t.get("logos") else ""
                if name and name not in teams:
                    teams[name] = logo
    except Exception:
        pass
    return [{"name": k, "logo": v} for k, v in teams.items()]

def fetch_all_teams_for_league(slug: str) -> list[dict]:
    """Combina scoreboard + standings para máxima cobertura."""
    seen = {}
    for t in fetch_espn_teams(slug):
        seen[t["name"]] = t["logo"]
    for t in fetch_espn_teams_from_standing(slug):
        if t["name"] not in seen:
            seen[t["name"]] = t["logo"]
    return [{"name": k, "logo": v} for k, v in seen.items()]

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    known_ids = load_ids()
    all_teams: dict[str, str] = {}   # name → espn_logo_url

    # 1. Recolectar todos los equipos de todas las ligas via ESPN
    print("\n" + "="*60)
    print("  FASE 1: Recolectando equipos desde ESPN...")
    print("="*60)

    for league_name, slug in LEAGUES:
        teams = fetch_all_teams_for_league(slug)
        new_count = 0
        for t in teams:
            if t["name"] not in all_teams:
                all_teams[t["name"]] = t["logo"]
                new_count += 1
        print(f"  {league_name:35} → {len(teams):3} equipos  (+{new_count} nuevos)")
        time.sleep(0.4)

    print(f"\n  Total equipos únicos encontrados: {len(all_teams)}")

    # 2. Descargar logos
    print("\n" + "="*60)
    print("  FASE 2: Descargando logos...")
    print("="*60 + "\n")

    total  = len(all_teams)
    ok     = 0
    failed = []

    for i, (name, espn_logo) in enumerate(all_teams.items(), 1):
        out = OUT_DIR / f"{sanitize_filename(name)}.png"
        if out.exists():
            print(f"  [{i:4}/{total}] ✓ existe        {name}")
            ok += 1
            continue

        img, src = None, ""

        # A. Logo directo de ESPN (viene gratis, sin requests extra)
        if espn_logo:
            img = get_url(espn_logo)
            if img:
                src = "espn-cdn"

        # B. api-sports.io por ID conocido
        if not img and name in known_ids:
            img = from_apisports(known_ids[name])
            if img:
                src = "api-sports"

        # C. TheSportsDB como último recurso
        if not img:
            img = from_thesportsdb(name)
            if img:
                src = "thesportsdb"
            time.sleep(0.2)  # solo cuando consultamos TheSportsDB

        if img:
            img.save(str(out), "PNG")
            print(f"  [{i:4}/{total}] ✅ {src:12}  {name}")
            ok += 1
        else:
            print(f"  [{i:4}/{total}] ✗  no encontrado  {name}")
            failed.append(name)

        time.sleep(DELAY)

    # 3. Guardar IDs actualizados
    save_ids(known_ids)

    # 4. Resumen
    print(f"\n{'='*60}")
    print(f"  ✅ Descargados: {ok}/{total}")
    print(f"  ✗  Fallidos:   {len(failed)}")
    if failed:
        print(f"\n  Sin logo (subir manualmente a assets/logos/<nombre>.png):")
        for n in sorted(failed):
            print(f"    · {n}.png")
    print(f"\n  Sube la carpeta assets/logos/ a GitHub y listo.\n")

if __name__ == "__main__":
    main()
