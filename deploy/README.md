# deploy/ — arranque en 1 clic

Para correr **toda la obra con el Muse 2 real** sin tocar la consola. Un doble
clic abre y conecta todos los programas: el puente del Muse, el bridge, la
gráfica, **Pure Data con el patch** y **Reaper con el proyecto**.

| Sistema | Archivo | Cómo |
|---|---|---|
| **Windows** | `INICIAR-Windows.bat` | doble clic |
| **macOS** (Apple Silicon) | `INICIAR-macOS.command` | doble clic (la 1ª vez: clic derecho → Abrir → Abrir) |

## Qué levanta

```
Muse 2 --BLE-->  muselsl (Mac) / BlueMuse (Win)  --LSL-->  producer/muse_producer.py
                                                               │  OSC :9001
                                                               ▼
                                                        viz/bridge.py ──OSC :9000──►  Pure Data
                                                               │                     (se abre solo
                                                               │  WebSocket :8765     con el patch)
                                                               ▼                            │  audio
                                                    http://localhost:8000  (viz/)      ─────►  Reaper
```

1. **Comprueba Python** e instala todo en `.venv/` (`producer/requirements.txt`
   + `viz/requirements.txt`). Solo la primera vez tarda.
2. **Muse:**
   **Windows** (`BLUEMUSE_AUTO=si`, por defecto): abre **BlueMuse** y arranca el
   streaming solo vía `bluemuse://startall`. Solo prendé el Muse y ponételo.
   (Con `BLUEMUSE_AUTO=no` vuelve a pedirte que le des *Start Streaming* a mano.)
   **macOS:** arranca el puente `muselsl`.
3. Arranca **`viz/bridge.py`**: recibe el OSC en `:9001`, lo reenvía **idéntico**
   a Pure Data en `:9000` y además alimenta la gráfica por WebSocket.
4. Sirve **`viz/`** en `http://localhost:8000` y abre el navegador.
5. Abre **Pure Data** con `PD_PATCH` y **Reaper** con `REAPER_PROJECT`
   (ver *Configuración*). El DSP de Pd arranca solo (el patch trae el
   `loadbang → ; pd dsp 1`). Si `AUTOPLAY` apunta a un ReaScript, lo corre en
   Reaper `AUTOPLAY_DELAY` segundos después (para que el proyecto termine de
   cargar).
6. Deja el **productor del Muse** corriendo en la ventana principal. Ahí escribís
   `kick` / `skip` / `reset` / `quit` + Enter.

**Cerrar la ventana principal detiene todo.** Con `CERRAR_TODO=si` (por defecto)
también cierra Pure Data y Reaper y para el streaming de BlueMuse.

## Pure Data y Reaper

Se abren solos con el patch / proyecto que indiques en `config.txt`:

- **Pure Data** — `PD_PATCH` (por defecto `pureDataPatch-v0.6/subpatches/1-Draft.pd`).
  El patch escucha en el puerto **9000** y recibe los datos a través del bridge
  sin ningún cambio; el DSP se enciende solo.
- **Reaper** — `REAPER_PROJECT` (por defecto `reaperProject.RPP`). Abre el
  proyecto. Si `AUTOPLAY` está seteado (por defecto `deploy/autoplay.lua`), el
  lanzador corre ese ReaScript en Reaper `AUTOPLAY_DELAY` s después de abrir el
  proyecto (Windows: `reaper.exe -nonewinst`; macOS: el binario de REAPER). El
  `autoplay.lua` que viene en el repo va al inicio del proyecto y da *play*
  (no hace nada si ya está sonando). Si el `.lua` no existe, Reaper abre igual
  y solo avisa. Dejá `AUTOPLAY` vacío para dar *play* a mano.

El lanzador busca `pd.exe` / `reaper.exe` en las rutas típicas. Si están
instalados en otro lado, poné la ruta completa en `PD_EXE` / `REAPER_EXE`. Para
**no** abrir alguno, dejá su `PD_PATCH` / `REAPER_PROJECT` vacío.

> El enrutado de audio entre Pd y Reaper (cable virtual / ReaRoute / MIDI
> loopback) y el emparejamiento Bluetooth del Muse son configuración de la
> máquina — el lanzador no los crea, tienen que estar hechos una vez.

## Configuración

Todo en [`config.txt`](config.txt) (un solo archivo para los dos sistemas):

```
MUSE_NAME=              # solo macOS; vacío si hay un solo Muse encendido
MUSE_STREAM_ARGS=--acc --gyro --ppg   # solo macOS; movimiento y BPM reales
PRODUCER_ARGS=          # extra para el productor, ej: --calibration 45
ABRIR_NAVEGADOR=si      # abrir el navegador con la gráfica al arrancar
PD_EXE=                 # ruta a pd.exe; vacío = autodetectar
REAPER_EXE=             # ruta a reaper.exe; vacío = autodetectar
PD_PATCH=pureDataPatch-v0.6/subpatches/1-Draft.pd   # patch que abre; vacío = no abrir Pd
REAPER_PROJECT=reaperProject.RPP                    # proyecto que abre; vacío = no abrir Reaper
AUTOPLAY=deploy/autoplay.lua   # ReaScript que corre en Reaper tras abrir; vacío = play a mano
AUTOPLAY_DELAY=15      # segundos a esperar antes de correr el ReaScript
BLUEMUSE_AUTO=si        # Windows: arrancar BlueMuse + streaming solo
CERRAR_TODO=si          # al cerrar, cerrar también Pd y Reaper
BRIDGE_PORT=9001        # el productor manda acá
PD_PORT=9000            # el bridge reenvía acá (tu patch de Pd)
WEB_PORT=8000           # la gráfica
```

## Requisitos previos (una vez por máquina)

- **Windows:** [Python](https://www.python.org/downloads/) con *"Add python.exe to
  PATH"* marcado + [BlueMuse](https://github.com/kowalej/BlueMuse/releases)
  instalado y *Developer Mode* activado + **Pure Data** y **Reaper** instalados,
  y el Muse ya emparejado por Bluetooth.
- **macOS:** `brew install python` + **Pure Data** y **Reaper** instalados.

## Probar sin Muse

Estos lanzadores asumen el Muse 2 real. Para probar solo el patch con datos
falsos está [`simulator/`](../simulator/) (su propio `iniciar.bat` /
`iniciar.command`), o la gráfica sola en modo demo abriendo
`http://localhost:8000/?demo`.

## Si algo no funciona

- **"No se encontró Python"** → instalalo (link arriba) y reabrí el lanzador.
- **"No se encontró pd.exe / reaper.exe"** → poné la ruta completa al ejecutable
  en `PD_EXE` / `REAPER_EXE` en `config.txt`.
- **No llega nada a Pd** → confirmá que el patch escucha en `netreceive -u -b 9000`
  y que Pd corre en la misma máquina.
- **Pd abre pero no suena** → revisá que sea el patch con el `loadbang → ; pd dsp 1`
  y que la salida de audio de Pd apunte al dispositivo correcto (Media → Audio
  Settings). El enrutado Pd → Reaper es setup de la máquina.
- **BlueMuse no arranca solo** → abrilo una vez a mano para que Windows registre
  el esquema `bluemuse://`, o poné `BLUEMUSE_AUTO=no`.
- **Autoplay no dispara** → subí el `AUTOPLAY_DELAY` si el proyecto tarda en
  cargar; confirmá que `AUTOPLAY` apunta a un `.lua` que existe y que Reaper
  corre ReaScripts.
- **La gráfica queda en demo / no conecta** → el bridge no está recibiendo OSC;
  revisá que el Muse esté transmitiendo (BlueMuse "Streaming" / `muselsl`).
- **macOS no deja abrir el `.command`** → clic derecho → Abrir → Abrir.
- Detalle del protocolo OSC y de cada componente: [`../README.md`](../README.md),
  [`../producer/README.md`](../producer/README.md), [`../viz/README.md`](../viz/README.md).

---

> `eeg-simulator.service` (en esta misma carpeta) es otra cosa: es para correr el
> **simulador** como servicio `systemd` en un VPS. Ver [`../DEPLOY.md`](../DEPLOY.md).
