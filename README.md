# ⚽ Universo Football — Livescore Bot

Bot de Telegram para seguimiento de partidos en vivo con notificaciones de goles en tiempo real, goleadores, imágenes de resumen y alineaciones.

---

## 📁 Estructura del proyecto

```
livescoreuf/
├── main.py                   ← Punto de entrada (bot + servidor)
├── bot.py                    ← Lógica principal del bot de Telegram
├── image_generator.py        ← Imagen de resumen final con estadísticas
├── lineup_image_generator.py ← Imágenes de alineaciones (campo + jugadores)
├── espn_goals.py             ← Goleadores en tiempo real desde ESPN
├── thesportsdb.py            ← Listado de partidos del día (TheSportsDB)
├── fotmob_stats.py           ← Goleadores fallback desde FotMob
├── sofascore_stats.py        ← Estadísticas de imagen desde Sofascore
├── server.py                 ← Servidor HTTP para health checks (Render)
├── requirements.txt
├── .env.example              ← Plantilla de variables de entorno
└── assets/
    ├── font.ttf              ← Fuente TTF personalizada (agregar manualmente)
    └── logo_uf.png           ← Logo de Universo Football (agregar manualmente)
```

---

## 🔄 Arquitectura de fuentes de datos

El bot usa múltiples fuentes con fallback automático:

### Listado de partidos del día (`/partidos`)
| Prioridad | Fuente | Motivo |
|-----------|--------|--------|
| 1 | **TheSportsDB** | Gratis, sin auth, sin bloqueos, disponible con días de anticipación |
| 2 | **ESPN** | Fallback — solo muestra jornada actual, puede llegar tarde |

> ⚠️ Sofascore devuelve `403` desde IPs de Render — se eliminó como fuente de listado.

### Livescore en tiempo real (estado del partido)
| Fuente | Uso |
|--------|-----|
| **ESPN Scoreboard** | Estado, marcador y minuto del partido |

### Goleadores (edición del mensaje tras el gol)
| Prioridad | Fuente | Motivo |
|-----------|--------|--------|
| 1 | **ESPN Summary** (`espn_goals.py`) | Busca por nombre de equipo, sin depender del ID, confiable |
| 2 | **FotMob** | Fallback rápido |
| 3 | **Sofascore incidents** | Último recurso |

### Estadísticas para imagen de resumen
| Prioridad | Fuente |
|-----------|--------|
| 1 | **Sofascore statistics** |
| 2 | **ESPN boxscore** (fallback) |

---

## 🚀 Instalación local

```bash
# 1. Clonar el repositorio
git clone https://github.com/byopi/livescoreuf.git
cd livescoreuf

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# 5. Agregar assets
#    - Coloca tu fuente .ttf en assets/font.ttf
#    - Coloca el logo en assets/logo_uf.png

# 6. Ejecutar
python main.py
```

---

## ☁️ Deploy en Render

### Desde el dashboard

1. Crea un **Web Service**.
2. Conecta tu repositorio de GitHub.
3. Configura:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
4. En **Environment Variables** agrega todas las del `.env.example`.
5. El health check apunta a `GET /health`.

### render.yaml

```yaml
services:
  - type: web
    name: uf-livescore-bot
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python main.py
    healthCheckPath: /health
    envVars:
      - key: BOT_TOKEN
        sync: false
      - key: ADMIN_ID
        sync: false
      - key: CHANNEL_ID
        sync: false
      - key: POLL_INTERVAL
        value: "15"
      - key: RESOLVE_INTERVAL
        value: "15"
      - key: RESOLVE_TIMEOUT
        value: "180"
      - key: LINEUP_INTERVAL
        value: "120"
```

---

## 🤖 Comandos del bot

| Comando | Descripción |
|---------|-------------|
| `/start` | Menú de ayuda |
| `/partidos` | Lista partidos del día con botones para activar monitoreo |
| `/activos` | Muestra partidos actualmente monitoreados |
| `/stop <id>` | Detiene el monitoreo de un partido |
| `/lineup [id]` | Envía alineaciones de un partido activo al canal |
| `/testlineup` | Preview privado de imágenes de alineación (sin publicar) |
| `/preview` | Envía al canal un ejemplo de alineaciones + gol de prueba |
| `/test <id>` | Preview del post final con imagen de estadísticas |
| `/espn <slug>` | Test directo de ESPN para un slug (ej: `/espn ita.1`) |
| `/debug` | Diagnóstico completo de ESPN por liga |

---

## 📸 Imagen de resumen (al finalizar el partido)

Generada por `image_generator.py` con Pillow:

- **Fondo:** Gris antracita con textura de grano sutil
- **Logos de equipos:** Descargados automáticamente
- **Marcador:** Tipografía grande con sombra
- **Estadísticas:** Posesión, xG, Tiros a puerta, Tiros totales, Córners, Tarjetas amarillas y rojas, Fuera de juego — con barras de progreso comparativas
- **Marca de agua:** Logo de Universo Football

---

## 👥 Imágenes de alineación

Generadas por `lineup_image_generator.py`:

- Dos imágenes (local y visitante) enviadas como media group
- Campo de fútbol con jugadores posicionados según la formación
- Logo y nombre del equipo
- Alineación en texto adjunta al segundo mensaje
- Se envían automáticamente ~90 min antes del kickoff
- Comando `/lineup` para forzar el envío manualmente

---

## 📢 Mensajes al canal

### Gol
```
🥅 | GOOOOOL!

Home [2]–1 Away

⌚ 34'
⚽ Nombre Goleador
🅰️ Nombre Asistencia

📲 Suscribete en t.me/iUniversoFootball
```

### Final normal
```
📢 | FINAL DEL PARTIDO

↪️ Home 2-1 Away

🎦 Todos los videos de los goles disponibles aqui: t.me/ufgoals
```

### Final en prórroga
```
📢 | FINAL — PRÓRROGA

↪️ Home 1-1 Away (a.e.t.)
```

### Final en penales
```
📢 | FINAL — PENALES

↪️ Home 1-1 Away (90')
🥅 Penales: Home 4-3 Away

  Jugador A ✅   |   Jugador X ✅
  Jugador B ✅   |   Jugador Y ❌
  Jugador C ❌   |   Jugador Z ✅
```

---

## ⚙️ Variables de entorno

| Variable | Requerida | Default | Descripción |
|----------|-----------|---------|-------------|
| `BOT_TOKEN` | ✅ | — | Token de @BotFather |
| `ADMIN_ID` | ✅ | — | Tu ID de Telegram |
| `CHANNEL_ID` | ❌ | — | Canal destino (vacío = mensajes al admin) |
| `POLL_INTERVAL` | ❌ | `15` | Segundos entre polls del livescore |
| `RESOLVE_INTERVAL` | ❌ | `15` | Segundos entre reintentos de goleador |
| `RESOLVE_TIMEOUT` | ❌ | `180` | Tiempo máximo buscando goleador (seg) |
| `LINEUP_INTERVAL` | ❌ | `120` | Segundos entre checks de alineaciones |
| `FONT_PATH` | ❌ | `assets/font.ttf` | Ruta a la fuente TTF |
| `PORT` | ❌ | — | Puerto del servidor (Render lo asigna solo) |

> `API_KEY` ya no es necesaria — el bot usa únicamente APIs públicas gratuitas.

---

## 🌐 APIs utilizadas

| API | Uso | Auth |
|-----|-----|------|
| [ESPN](https://site.api.espn.com) (no oficial) | Livescore, estado del partido, summary, alineaciones | Ninguna |
| [TheSportsDB](https://www.thesportsdb.com/api.php) | Listado de partidos del día | Key pública `123` |
| [FotMob](https://www.fotmob.com) (no oficial) | Goleadores (fallback) | Ninguna |
| [Sofascore](https://www.sofascore.com) (no oficial) | Estadísticas para imagen, goleadores (fallback) | Ninguna |

> ⚠️ Sofascore bloquea con `403` desde IPs de servidores cloud (Render, Railway, etc.). Funciona correctamente desde IPs residenciales o con proxy.

---

## 🏆 Ligas monitoreadas

| Región | Competiciones |
|--------|--------------|
| 🇮🇹 Italia | Serie A, Coppa Italia, Supercopa |
| 🇪🇸 España | La Liga, Copa del Rey, Supercopa |
| 🇫🇷 Francia | Ligue 1, Coupe de France, Trophée des Champions |
| 🇩🇪 Alemania | Bundesliga, DFB-Pokal, Supercopa |
| 🏴󠁧󠁢󠁥󠁮󠁧󠁿 Inglaterra | Premier League, FA Cup, EFL Cup, Community Shield |
| 🇪🇺 UEFA | Champions League, Europa League, Conference League, Supercopa, Nations League |
| 🌎 CONMEBOL | Copa Libertadores, Copa Sudamericana, Recopa, Copa América |
| 🌍 Clasificatorias | UEFA, CONMEBOL, CONCACAF, AFC, CAF |
| 🌐 FIFA | Mundial de Clubes, Mundial 2026 |
