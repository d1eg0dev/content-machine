# Content Machine: Eyes of Diego

Una máquina de contenido que corre sola en la nube (GitHub Actions, gratis). Cada día:

1. **Busca un trend.** Gemini, con búsqueda de Google, lee las tendencias del día, la temporada y los eventos próximos. Elige un concepto de ambiente que no repita nada de lo publicado.
2. **Compone la música por código.** Pads, drones, cuencos tibetanos, campanas, lluvia, océano, viento, fuego, pájaros y beats binaurales. Sin samples, sin loops y sin modelos de música IA. Cada minuto es único.
3. **Monta el vídeo.** Usa tu foto (o stock de Pexels, o un paisaje procedural) con zoom lento y partículas, en un loop sin cortes. Genera también la miniatura y 2 Shorts verticales.
4. **Te avisa.** Te llega un email a través de Inkbox con el enlace de descarga, la miniatura, y el título, la descripción y los tags listos para copiar y pegar.

| Día | Qué produce |
|---|---|
| Lun, Mié, Vie, Dom | Vídeo de 1 hora + miniatura + 2 Shorts |
| Mar, Jue, Sáb | 2 Shorts |

Coste: **0 €**. Tu tiempo: **~10–15 min al día** para revisar y subir.

---

## Puesta en marcha (unos 30 minutos, una sola vez)

### 1. Sube el código a GitHub
Crea un repositorio nuevo, por ejemplo `content-machine`, y sube esta carpeta.
**Recomendado: repositorio público.** En los repos públicos los minutos de Actions y el almacenamiento de artefactos son gratis e ilimitados, y las claves (Secrets) siguen siendo privadas. Si lo haces privado tienes 2.000 min/mes y 500 MB de artefactos, lo justo.

### 2. Clave de Gemini (gratis)
- Entra en https://aistudio.google.com/apikey y pulsa "Create API key".
- En GitHub ve a **Settings → Secrets and variables → Actions → New repository secret** y crea `GEMINI_API_KEY`.
- Opcional: en la pestaña *Variables* puedes crear `GEMINI_MODEL` para cambiar de modelo (por defecto es `gemini-2.5-flash`).

Sin esta clave la máquina sigue funcionando con temas de temporada predefinidos, pero sin trends.

### 3. Aviso por email (Inkbox)
Crea estos tres secrets:
- `INKBOX_API_KEY`: tu clave de Inkbox (https://inkbox.ai/console).
- `INKBOX_FROM`: la dirección de tu buzón de Inkbox, que es la que envía el aviso.
- `NOTIFY_TO`: tu email personal, donde lo quieres recibir. Puedes poner varios separados por comas.

### 4. Pexels (opcional, fotos de stock gratis)
https://www.pexels.com/api/ → secret `PEXELS_API_KEY`. Se usa cuando ninguna foto tuya encaja con el tema.

### 5. Tus fotos (lo que más originalidad aporta)
Mete tus fotos en `assets/photos/` con **nombres descriptivos**, porque el scout elige por el nombre:
```
oaxaca-sierra-niebla-amanecer.jpg
playa-tulum-noche-luna.jpg
bosque-pinos-lluvia-michoacan.jpg
```
Conviene que sean horizontales y de al menos 2400 px de ancho. Cuantas más subas, más vídeos serán 100% tuyos.

### 6. Pruébalo
Ve a **Actions → Daily content → Run workflow** y pon `long = true` y `minutes = 3`. En unos 5 minutos te llegará el email y el vídeo de prueba estará en la página de esa ejecución (sección *Artifacts*).

A partir de ahí se ejecuta solo todos los días a las 06:17 (hora de Madrid).

---

## Tu rutina diaria (10–15 min)
1. Abre el email del día y pulsa **"Descargar los vídeos de hoy"**. Abajo del todo, en *Artifacts*, descarga el zip.
2. **Vídeo largo** (4 días por semana): súbelo en YouTube Studio, pega el título, la descripción y los tags del email, pon la miniatura y prográmalo.
3. **Shorts:** súbelos desde YouTube Studio o desde la app, con el título del email.
4. Contesta 2–3 comentarios. Eso cuenta como "aporte humano" y ayuda al algoritmo.

## ¿Por qué no se sube sola a YouTube (todavía)?
YouTube **bloquea en privado** todos los vídeos que se suben por API desde un proyecto que no ha pasado su auditoría de cumplimiento. El código de subida ya está listo (`publish/youtube_upload.py`). Para activarlo:
1. En Google Cloud Console, crea un proyecto, activa *YouTube Data API v3* y crea credenciales OAuth de tipo *Desktop*.
2. Rellena el formulario de auditoría de la API de YouTube. Es gratis y suele tardar unas semanas.
3. Ya aprobado, ejecuta en tu PC `python tools/get_youtube_token.py client_secret.json` y guarda los 3 valores como secrets (`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`).
4. En `config.json` cambia `"upload_mode": "youtube"`. Si quieres que se programen solos, pon `"youtube_publish_delay_hours": 12`.

---

## Ajustes (`config.json`)
| Clave | Qué hace |
|---|---|
| `long_video_days` | Qué días se hace vídeo largo |
| `shorts_per_day` | Número de Shorts por día |
| `short_seconds` | Duración de cada Short |
| `upload_mode` | `artifact` (descargas tú) o `youtube` (tras la auditoría) |
| `description_footer` | Texto fijo al final de cada descripción |

## Probar en tu PC
```bash
pip install -r requirements.txt   # y ffmpeg instalado
python -m engine.music --theme themes/example.json --minutes 2 --out preview.m4a   # solo audio
python -m pipeline.run_daily --long --minutes 3 --no-deliver                        # todo
```

## Estructura
```
scout/trend_scout.py       Trends + Gemini → concepto del día (JSON)
engine/music.py            Motor de música procedural (streaming, aguanta horas)
engine/visuals.py          Imagen → loop animado, miniatura, Shorts
pipeline/run_daily.py      Orquesta todo
publish/deliver.py         Aviso por email (Inkbox) + subida a YouTube
.github/workflows/daily.yml  El cron en la nube
data/history.json          Memoria de lo publicado (para no repetir)
```

---

## Estrategia y reglas de YouTube (léelo una vez)
- **Monetización:** 1.000 suscriptores + 4.000 horas de visualización en 12 meses, o 1.000 suscriptores + 10M de vistas en Shorts en 90 días. Los vídeos de 1 hora de ambiente suman horas muy rápido si alguien los usa para dormir.
- **Política de "inauthentic content":** YouTube penaliza a nivel de canal el contenido hecho en masa con plantillas. Esta máquina lo evita así:
  - cada vídeo tiene tonalidad, modo, capas y paisaje distintos, y el historial impide repeticiones;
  - la música es original y está generada por código;
  - las imágenes son tus fotos;
  - hay un humano revisando antes de publicar.
  - **No subas más de 1 vídeo largo al día.**
- **Divulgación de IA:** no hace falta marcar "contenido alterado o sintético", porque no hay personas ni sucesos realistas generados. Si algún día usas clips de Flow/Veo realistas, márcalo.
- **El canal:** cambia nombre/banner/descripción hacia ambiente + México (por ejemplo "Eyes of Diego — Soundscapes from Mexico"). Tus Shorts antiguos de viajes pueden quedarse; encajan con la estética.
