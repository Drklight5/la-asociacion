# viz/ — visual de proyección EEG

Gráfica de líneas en tiempo real de las 5 bandas (delta / theta / alfa / beta /
gamma) + animaciones que acompañan (fondo reactivo, partículas, pulso de BPM,
glitch en la patada). Corre en el navegador (p5.js), consumo bajo.

```
productor / simulador                bridge.py                      Pure Data
---------------------  --OSC :9001-->  reenvía el datagrama    --OSC :9000-->  (patch
                                       UDP TAL CUAL  ───────────────────────►  sin tocar)
                                          │
                                          └── WebSocket :8765 (JSON) ──►  index.html (p5.js)
```

**Pure Data no se entera de que esto existe.** El `bridge.py` reenvía cada
paquete UDP idéntico (mismas direcciones OSC, mismos tipos, puerto 9000). Lo
único que cambia respecto a hoy: el productor / simulador se lanza apuntando al
puerto del relay (`9001`) en vez de directo a Pd (`9000`).

Nada fuera de esta carpeta se modifica.

## Instalar (una vez)

```bash
cd viz
pip install -r requirements.txt
curl -L https://cdn.jsdelivr.net/npm/p5@1.9.4/lib/p5.min.js -o p5.min.js
```

> Conviene commitear `p5.min.js` para que la máquina de la instalación no
> dependa de internet el día del montaje.

## Correr

En **tres** terminales (o lanzadores):

1. **El bridge**
   ```bash
   cd viz
   python bridge.py
   ```

2. **La fuente de datos**, apuntada al relay (`--port 9001`):
   ```bash
   # Muse real:
   python producer/muse_producer.py --port 9001
   # o el simulador:
   cd simulator && npm start -- --port 9001
   ```
   (o dejarlo fijo con `EEG_PRODUCER_PORT=9001` / `EEG_SIM_PORT=9001` en el `.env`
   correspondiente — no hace falta tocar código.)

3. **La visual**: abrir `viz/index.html` en el navegador y `f` para pantalla
   completa. Si `file://` diera problemas: `python -m http.server` dentro de
   `viz/` y abrir `http://localhost:8000`.

Pure Data se abre como siempre (escucha 9000); recibe los datos vía el relay.

## Teclas

| Tecla | Acción |
|---|---|
| `d` | demo interno on/off (datos falsos para probar sin fuente) |
| `l` | etiquetas (valores por banda, banda dominante, BPM) |
| `f` | pantalla completa |
| `-` / `+` | ventana del historial más larga / más corta |

## Parámetros de URL

- `?ws=host:puerto` — destino del WebSocket (default `localhost:8765`)
- `?demo` — arranca en modo demo
- `?lite` — menos partículas, sin degradado ni glow (máquinas justas de CPU/GPU)

## Trabajar sin Muse

**Opción rápida:** el simulador contra el bridge (`npm start -- --port 9001`), o
la visual sola en modo demo (`?demo`).

**Con datos reales grabados:**

```bash
python bridge.py --record sesion.jsonl      # graba mientras hay Muse
python replay.py sesion.jsonl --loop        # después, reproduce en bucle hacia el bridge
```

## Opciones del bridge

```
python bridge.py --help
  --listen-port 9001      puerto al que apuntas el productor/simulador
  --pd-host / --pd-port   destino del reenvío (default 127.0.0.1:9000)
  --no-forward            NO reenviar a Pd (solo alimentar la visual)
  --record ARCHIVO.jsonl  grabar cada evento OSC
  --ws-port 8765          puerto del WebSocket
```

Equivalentes por entorno: `VIZ_LISTEN_PORT`, `VIZ_PD_HOST`, `VIZ_PD_PORT`, `VIZ_WS_PORT`.
