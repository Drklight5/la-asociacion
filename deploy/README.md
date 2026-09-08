# deploy/ — arranque en 1 clic

Para quien va a **probar el pipeline completo con el Muse 2 real** sin tocar la
consola. Un doble clic levanta todo:

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
                                                               │                     (lo abrís vos,
                                                               │  WebSocket :8765     no se toca)
                                                               ▼
                                                    http://localhost:8000  (viz/, la gráfica)
```

1. **Comprueba Python** e instala todo en `.venv/` (`producer/requirements.txt`
   + `viz/requirements.txt`). Solo la primera vez tarda.
2. **macOS:** arranca solo el puente `muselsl`.
   **Windows:** te recuerda abrir **BlueMuse** y darle *Start Streaming*
   (BlueMuse es una app aparte, no se automatiza).
3. Arranca **`viz/bridge.py`**: recibe el OSC en `:9001`, lo reenvía **idéntico**
   a Pure Data en `:9000` y además alimenta la gráfica por WebSocket.
4. Sirve **`viz/`** en `http://localhost:8000` y abre el navegador.
5. Deja el **productor del Muse** corriendo en la ventana principal. Ahí escribís
   `kick` / `skip` / `reset` / `quit` + Enter.

**Cerrar la ventana principal detiene todo.**

## Pure Data

No se abre ni se modifica desde acá. Abrí tu patch como siempre: escucha en el
puerto **9000** y recibe los datos a través del bridge sin ningún cambio.

## Configuración

Todo en [`config.txt`](config.txt) (un solo archivo para los dos sistemas):

```
MUSE_NAME=              # solo macOS; vacío si hay un solo Muse encendido
MUSE_STREAM_ARGS=--acc --gyro --ppg   # solo macOS; movimiento y BPM reales
PRODUCER_ARGS=          # extra para el productor, ej: --calibration 45
ABRIR_NAVEGADOR=si      # abrir el navegador con la gráfica al arrancar
BRIDGE_PORT=9001        # el productor manda acá
PD_PORT=9000            # el bridge reenvía acá (tu patch de Pd)
WEB_PORT=8000           # la gráfica
```

## Requisitos previos (una vez por máquina)

- **Windows:** [Python](https://www.python.org/downloads/) con *"Add python.exe to
  PATH"* marcado + [BlueMuse](https://github.com/kowalej/BlueMuse/releases)
  instalado y *Developer Mode* activado.
- **macOS:** `brew install python`.

## Probar sin Muse

Estos lanzadores asumen el Muse 2 real. Para probar solo el patch con datos
falsos está [`simulator/`](../simulator/) (su propio `iniciar.bat` /
`iniciar.command`), o la gráfica sola en modo demo abriendo
`http://localhost:8000/?demo`.

## Si algo no funciona

- **"No se encontró Python"** → instalalo (link arriba) y reabrí el lanzador.
- **No llega nada a Pd** → confirmá que el patch escucha en `netreceive -u -b 9000`
  y que Pd corre en la misma máquina.
- **La gráfica queda en demo / no conecta** → el bridge no está recibiendo OSC;
  revisá que el Muse esté transmitiendo (BlueMuse "Streaming" / `muselsl`).
- **macOS no deja abrir el `.command`** → clic derecho → Abrir → Abrir.
- Detalle del protocolo OSC y de cada componente: [`../README.md`](../README.md),
  [`../producer/README.md`](../producer/README.md), [`../viz/README.md`](../viz/README.md).

---

> `eeg-simulator.service` (en esta misma carpeta) es otra cosa: es para correr el
> **simulador** como servicio `systemd` en un VPS. Ver [`../DEPLOY.md`](../DEPLOY.md).
