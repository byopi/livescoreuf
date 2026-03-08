# ⚽ Universo Football — Livescore Bot

Bot de Telegram para seguimiento de partidos en vivo con generación de
imágenes de resumen y notificaciones de goles en tiempo real.

---

## 📁 Estructura del proyecto

```
livescore_bot/
├── main.py              ← Punto de entrada (bot + servidor)
├── bot.py               ← Lógica del bot de Telegram
├── image_generator.py   ← Generación de imagen con Pillow
├── server.py            ← Servidor FastAPI para health checks
├── requirements.txt
├── .env.example         ← Plantilla de variables de entorno
├── render.yaml          ← Configuración de Render (opcional)
└── assets/
    ├── font.ttf         ← Fuente TTF personalizada (agregar manualmente)
    └── logo_uf.png      ← Logo de Universo Football (agregar manualmente)
```

---

## 🚀 Instalación local

```bash
# 1. Clonar / descargar el proyecto
cd livescore_bot

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# 5. Agregar assets
#    - Coloca tu fuente .ttf como assets/font.ttf
#    - Coloca el logo de Universo Football como assets/logo_uf.png

# 6. Ejecutar
python main.py
```

---

## ☁️ Deploy en Render

### Método A — Desde el dashboard

1. Crea un **Web Service** (tipo Background Worker también funciona).
2. Conecta tu repositorio de GitHub.
3. Configura:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
4. En **Environment Variables** agrega todas las del `.env.example`.
5. El health check de Render debe apuntar a `GET /health`.

### Método B — render.yaml (Infrastructure as Code)

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
      - key: API_KEY
        sync: false
      - key: CHANNEL_ID
        sync: false
      - key: POLL_INTERVAL
        value: "60"
```

---

## 🤖 Comandos del bot

| Comando | Descripción |
|---------|-------------|
| `/start` | Muestra el menú de ayuda |
| `/partidos` | Lista los partidos del día con botones de activación |
| `/activos` | Muestra los partidos actualmente monitoreados |
| `/stop <id>` | Detiene el monitoreo de un partido específico |

---

## 📸 Formato de la imagen de resumen

La imagen generada (`image_generator.py`) incluye:

- **Fondo:** Gris antracita con textura de grano sutil.
- **Logos de equipos:** Descargados automáticamente desde la API.
- **Marcador:** Tipografía grande y destacada con sombra.
- **Estadísticas:** Posesión, Tiros a puerta, Tiros totales, Córners,
  Faltas y Tarjetas amarillas con barras de progreso comparativas.
- **Marca de agua:** Logo de Universo Football centrado en la parte inferior.

### Personalizar la fuente

Coloca cualquier archivo `.ttf` en `assets/font.ttf` o define la ruta
en la variable de entorno `FONT_PATH`. Si no se encuentra, se usa la
fuente de sistema de Pillow como fallback.

---

## 🔑 API utilizada

[API-Football](https://www.api-football.com) (v3) — hasta 100 requests
gratuitas por día en el plan Free.

Endpoints usados:
- `GET /fixtures` — partidos del día y estado en vivo
- `GET /fixtures/events` — eventos de gol
- `GET /fixtures/statistics` — estadísticas del partido

---

## ⚙️ Variables de entorno

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `BOT_TOKEN` | ✅ | Token de @BotFather |
| `ADMIN_ID` | ✅ | Tu ID de Telegram |
| `API_KEY` | ✅ | Key de API-Football |
| `API_HOST` | ❌ | Host de la API (default: `v3.football.api-sports.io`) |
| `CHANNEL_ID` | ❌ | Canal destino (vacío = mensajes al admin) |
| `POLL_INTERVAL` | ❌ | Segundos entre polls (default: `60`) |
| `FONT_PATH` | ❌ | Ruta a la fuente TTF (default: `assets/font.ttf`) |
| `PORT` | ❌ | Puerto del servidor (Render lo asigna solo) |
