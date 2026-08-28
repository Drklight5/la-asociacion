# Productor real (Muse 2 → OSC → Pure Data)

Reemplaza a `simulator/` cuando el Muse 2 real está puesto. Manda **exactamente
el mismo protocolo OSC** que el simulador (mismas direcciones, mismo puerto
9000 por default) — el patch de Pd no se toca para nada, solo se apaga el
simulador y se prende esto.

```
Muse 2 --BLE--> BlueMuse (Win) / muselsl (Mac) --LSL--> muse_producer.py --OSC/UDP--> Pure Data
```

## 1. Conectar el Muse 2 (BLE → LSL)

**Windows** — usar [BlueMuse](https://github.com/kowalej/BlueMuse): conectar el
Muse 2 ahí y darle "Start Streaming". Si querés `movement`/`bpm` reales,
habilitar ACC/GYRO/PPG en la configuración de BlueMuse antes de streamear.

**macOS** (Apple Silicon, sin apps extra):
```bash
muselsl stream --name "Muse-XXXX" --acc --gyro --ppg
```
(`--acc --gyro --ppg` son opcionales — sin ellos, el productor igual manda el
protocolo completo, con `movement=0` y `bpm` fijo. Ver [3. Correrlo](#3-correrlo).)

## 2. Instalar

```bash
cd producer
python -m venv .venv
# Windows: .venv\Scripts\activate | Mac: source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Correrlo

Con el stream LSL del paso 1 activo:

```bash
python muse_producer.py --host 127.0.0.1 --port 9000
```

Mismos comandos que el simulador mientras corre (`kick`, `skip`, `reset`, `quit` + Enter).

### Calibrar `movement` con el dispositivo real

`--movement-scale` y `--kick-threshold` no se pueden adivinar sin probar con
el Muse puesto — dependen de la sensibilidad real del gyro y de qué tan fuerte
se sacude la cabeza al patear. Pasos:

1. Correr con `--debug` y el Muse puesto: `python muse_producer.py --debug`.
2. Ver el valor de `movement` en reposo (parado, quieto) — debería rondar 0.05–0.15.
3. Simular la patada (sacudir la cabeza fuerte, o patear de verdad) y ver a
   qué valor de `movement` llega.
4. Ajustar `--movement-scale` hasta que el reposo dé ~0.1 y el movimiento
   fuerte se acerque a 1.0. Si el kick automático no dispara o dispara con
   cualquier movimiento, ajustar `--kick-threshold` (default 0.6).

Si no hay stream GYRO/ACC (no se pasó `--gyro`/`--acc` a `muselsl stream`, o
BlueMuse no los tiene habilitados), `movement` queda fijo en un valor bajo y
la patada solo se dispara con el comando manual `kick`.

### Correrlo sin pasar flags (`.env`)

Igual que `simulator/`: copiá [.env.example](.env.example) a `.env` y ajustá
los valores — útil para dejarlo configurado una vez (por ejemplo en el
`EnvironmentFile=` de un servicio systemd) sin tener que escribir los flags
cada vez. Un flag por línea de comandos siempre gana sobre el `.env`.

## Notas

- El detalle completo del protocolo (direcciones OSC, rangos, los 3 momentos)
  está en el [README.md](../README.md) del proyecto — es el mismo contrato
  que usa `simulator/`, no se repite acá.
- `bpm` real requiere el canal PPG del Muse 2 (`--ppg`); se calcula con
  detección de picos sobre la señal filtrada. Es una estimación básica —
  esperá ruido durante movimiento fuerte (igual que le pasaría a cualquier
  sensor óptico de pulso puesto en la frente de alguien que se está moviendo).
- Sin PPG, `bpm` se manda fijo en `--baseline-bpm` (default 72).
