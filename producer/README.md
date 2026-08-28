# Productor real (Muse 2 → OSC → Pure Data)

Reemplaza a `simulator/` cuando el Muse 2 real está puesto. Mismo protocolo
OSC, mismo puerto 9000 — el patch de Pd no cambia.

```
Muse 2 --BLE--> BlueMuse (Win) / muselsl (Mac) --LSL--> muse_producer.py --OSC/UDP--> Pure Data
```

## Instalar (una vez)

**macOS**, si falta Python:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"  # si tampoco tenés Homebrew
brew install python
```

```bash
cd producer
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip3 install -r requirements.txt
```

## Correrlo -- son 2 terminales

**Terminal 1 — conectar el Muse (BLE → LSL):**

- Windows: abrir [BlueMuse](https://github.com/kowalej/BlueMuse), conectar el Muse 2, "Start Streaming".
- Mac:
  ```bash
  python3 -m muselsl stream --name "Muse-XXXX" --acc --gyro --ppg
  ```
  (`--acc --gyro --ppg` son opcionales; sin ellos, `movement`/`bpm` quedan en valores fijos.)

Dejar esta terminal corriendo.

**Terminal 2 — mandar los datos a Pd:**

```bash
cd producer && source .venv/bin/activate   # Windows: .venv\Scripts\activate
python3 muse_producer.py --host 127.0.0.1 --port 9000
```

Comandos mientras corre: `kick`, `skip`, `reset`, `quit` + Enter.

## Calibrar `movement` (ejemplo)

```bash
python3 muse_producer.py --debug
```

```
# quieto:           movement=0.08 (mag=4.2)
# patada/sacudida:  movement=1.000 (mag=61.5)
```

Si en reposo ya da alto, o la patada nunca llega a 1.0, subí `--movement-scale`
(default 50) hasta que reposo ≈ 0.1 y patada ≈ 1.0:

```bash
python3 muse_producer.py --movement-scale 60
```

Sin stream GYRO/ACC, `movement` queda fijo y la patada solo se dispara con `kick`.

## Sin pasar flags (`.env`)

Copiá [.env.example](.env.example) a `.env` (mismas variables, prefijo `EEG_PRODUCER_`). Un flag por CLI siempre gana sobre el `.env`.

## Notas

- Protocolo completo (direcciones OSC, rangos, momentos): [README.md](../README.md) del proyecto.
- `bpm` real necesita `--ppg`; sin eso, queda fijo en `--baseline-bpm` (default 72).
