# La Asociación — puente EEG → Pure Data

Contexto completo del proyecto en [ABOUT.md](ABOUT.md).

## Arquitectura

```
Muse 2 (real, futuro)  \
                         >  productor de datos  --OSC/UDP-->  Pure Data
Simulador (hoy)        /
```

Se eligió **OSC sobre UDP** en vez de un webhook HTTP porque:

- Pure Data soporta OSC de forma nativa desde la versión 0.46 (`netreceive -u -b` + `oscparse`), sin externals adicionales.
- Es el protocolo estándar en instalaciones y arte sonoro para mandar datos de sensores en tiempo real (baja latencia, sin overhead de HTTP/TCP handshake por mensaje).
- Es el mismo protocolo que usan las herramientas reales de streaming de Muse (Muse Direct, EEGStreamer), así que cuando se conecte el dispositivo real, **el lado de Pure Data no cambia** — solo se reemplaza el simulador por el productor de datos real.

Un webhook (HTTP) sí tendría sentido si algún día se quisiera notificar a un sistema externo de forma puntual (p. ej. avisar a un backend que "se pateó la pelota"), pero para el stream continuo de EEG/BPM/movimiento no es la herramienta adecuada.

## Esquema de datos (contrato con Pure Data)

Por cada "frame" se mandan estos valores, como mensajes OSC independientes:

| Campo | Dirección OSC | Rango / valores | Descripción |
|---|---|---|---|
| waves.delta | `/eeg/wave/delta` | 0–1 | presencia normalizada de onda delta |
| waves.theta | `/eeg/wave/theta` | 0–1 | presencia normalizada de onda theta |
| waves.beta | `/eeg/wave/beta` | 0–1 | presencia normalizada de onda beta |
| waves.alfa | `/eeg/wave/alfa` | 0–1 | presencia normalizada de onda alfa |
| waves.gamma | `/eeg/wave/gamma` | 0–1 | presencia normalizada de onda gamma |
| bpm | `/eeg/bpm` | ~40–200 | ritmo cardiaco |
| movement | `/eeg/movement` | 0–1 | presencia de movimiento (giroscopio/señal) |
| moment | `/eeg/moment` | `calibrando` \| `operando` \| `movimiento_abrupto` | fase de la intervención |

`oscparse` en Pd vanilla descompone la dirección por "/" en símbolos separados, por lo que `/eeg/wave/delta 0.5` llega a Pd como la lista `eeg wave delta 0.5`. Ver [pd/eeg_receiver_test.pd](pd/eeg_receiver_test.pd) para el patrón de `route` correcto.

## Los 3 momentos

1. **Calibración** (~60s, configurable) — `moment = "calibrando"`.
2. **Presencia** — desde que termina la calibración hasta la patada — `moment = "operando"`.
3. **Post-patada** — el instante de la patada se marca como `moment = "movimiento_abrupto"` (ventana corta, ~1.5s) con picos en movimiento/BPM/beta-gamma simulando el artefacto de movimiento real de un EEG; después vuelve a `"operando"` hasta que se quita el dispositivo.

## Simulador (`simulator/`)

Genera datos sintéticos con esta lógica y los manda por OSC. Ver [simulator/src/dataGenerator.js](simulator/src/dataGenerator.js) para el detalle de cómo se generan las curvas (random walk suavizado por banda, para que no se vea "ruido puro" sino algo con inercia como EEG real).

### Instalar y correr

```bash
cd simulator
npm install
npm start -- --host 127.0.0.1 --port 9000
```

Mientras corre, se puede escribir en la terminal:

- `kick` — simula la patada (movimiento abrupto) en cualquier momento durante la presencia.
- `skip` — salta la calibración (útil para pruebas rápidas).
- `reset` — reinicia toda la simulación desde calibración.
- `quit` — termina.

Ver todas las opciones (duración de calibración, bpm base, auto-kick, etc.):

```bash
npm start -- --help
```

Todas las opciones también se pueden fijar por variable de entorno (`EEG_SIM_HOST`, `EEG_SIM_PORT`, `EEG_SIM_AUTO_LOOP`, etc. — ver [simulator/.env.example](simulator/.env.example)), útil para correrlo como servicio sin pasar flags.

Para dejarlo corriendo solo (sin nadie escribiendo comandos, p. ej. en un servidor), usa `--auto-loop`: repite el ciclo completo — calibración, presencia, patada en un momento aleatorio, post-patada, reinicio — indefinidamente.

### Probar el pipe completo con Pure Data

1. Abre `pd/eeg_receiver_test.pd` en Pure Data.
2. Corre el simulador (`npm start` dentro de `simulator/`).
3. Deberías ver en la consola de Pd los valores llegando (`wave: delta 0.5x`, `bpm: 72`, `movement: 0.0x`, `moment: calibrando`, etc.).
4. Escribe `kick` en la terminal del simulador y confirma que `moment` cambia a `movimiento_abrupto` y que `movement`/`bpm` suben.

## Correrlo en la nube (para que el equipo de producción lo tenga sin depender de ti)

Ver [DEPLOY.md](DEPLOY.md) — VPS chica + Tailscale para que Pd reciba los datos sin abrir puertos en su router, y el patch de Pd nunca tiene que cambiar cuando después se conecte el Muse 2 real.

## Siguiente paso: dispositivo real

Cuando se conecte el Muse 2 real, la ruta natural (documentada por la comunidad) es:

```
Muse 2 --BLE--> muselsl / BrainFlow (Python, LSL) --> productor OSC --> Pure Data
```

Se puede mantener `simulator/src/oscSender.js` como referencia del formato de mensajes esperado y escribir un nuevo productor (probablemente en Python, ya que `muselsl`/`BrainFlow` son las librerías estándar para Muse 2) que calcule las bandas de potencia normalizadas, BPM y movimiento a partir de la señal real, y reutilice exactamente las mismas direcciones OSC — así el patch de Pure Data de producción no tiene que cambiar.
