# Productor real (Muse 2 → OSC → Pure Data)

Reemplaza a `simulator/` cuando el Muse 2 real está puesto. Mismo protocolo
OSC, mismo puerto 9000 — el patch de Pd no cambia.

```
Muse 2 --BLE--> BlueMuse (Win) / muselsl (Mac) --LSL--> muse_producer.py --OSC/UDP--> Pure Data
```

## 1. Instalar
> Usar python3 y pip3 como alternativa si los comandos no funcionan 

**macOS** — si no tenés Python 3 instalado (`python3 --version` para chequear):

```bash
# si tampoco tienes Homebrew:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install python
```

```bash
cd producer
python -m venv .venv
# Windows: .venv\Scripts\activate | Mac: source .venv/bin/activate
pip install -r requirements.txt
```
> Editar requirements.txt con python-osc>=1.8.3 si la version 1.9 no es soportada

## 2. Conectar el Muse 2 (BLE → LSL)

**Windows** — usar [BlueMuse](https://github.com/kowalej/BlueMuse): conectar el
Muse 2 ahí y darle "Start Streaming". Si querés `movement`/`bpm` reales,
habilitar ACC/GYRO/PPG en la configuración de BlueMuse antes de streamear.

**macOS** (Apple Silicon, sin apps extra):
```bash
muselsl stream --name "Muse-XXXX" --acc --gyro --ppg
```
> Nuestro dispositivo es 3745
> agregar python3 -m al comando si no funciona 

(`--acc --gyro --ppg` son opcionales — sin ellos, el productor igual manda el
protocolo completo, con `movement=0` y `bpm` fijo. Ver [3. Correrlo](#3-correrlo).)

## 3. Correrlo

Con el stream LSL del paso 1 activo:

```bash
python muse_producer.py --host 127.0.0.1 --port 9000
```

Mismos comandos que el simulador mientras corre (`kick`, `skip`, `reset`, `quit` + Enter).

### Flujo por persona (obra)

1. La persona se pone el Muse. El operador escribe `reset` + Enter.
2. Arranca la calibración (`--calibration`, default 60 s). Se ignoran los
   primeros `--calib-settle` s (default 10) mientras los electrodos secos se
   asientan. La persona escucha la explicación **quieta** — esto importa: si se
   mueve mientras calibra, el baseline queda inflado en delta/theta y después
   todo se ve invertido (delta/theta abajo, beta/gamma arriba). Medido: calibrar
   quieta elimina la inversión sin costar rango.
3. Al terminar, la consola imprime un reporte: `baseline OK: ...` o
   `!!! CALIDAD DUDOSA ...`. Si sale dudosa → reacomodar el Muse y `reset`.
4. `moment` pasa a `operando`. La persona patea → `movimiento_abrupto` (las
   waves se congelan durante el artefacto) → vuelve a `operando`.
5. La persona se saca el Muse, lo deja en la mesa. Siguiente persona → paso 1.

### Movimiento / detección de patada

Con `--movement-auto` (default) la calibración **mide sola** el reposo del
giro/acelerómetro y fija la escala y el umbral de patada — no hay que ajustar
nada a mano. Verlo con `--debug` (`movement=... / inst=...`).

Con `--no-movement-auto` se usan `--movement-scale` y `--kick-threshold` tal
cual (para forzar valores conocidos). Sin stream GYRO/ACC (no se pasó
`--gyro`/`--acc`, o BlueMuse no los tiene habilitados), `movement` queda bajo
y fijo y la patada solo se dispara con el comando manual `kick`.

### Robustez de conexión

- Descarta el canal `Right AUX` del Muse 2 y cualquier electrodo que quede
  plano / sature / haga mal contacto (y lo reincorpora si se recupera). Si
  TP9/TP10 fallan, sigue con AF7/AF8.
- Si el stream EEG se corta, avisa y **mantiene los últimos valores**; si
  sigue caído, reintenta reconectar solo.
- Avisa si la tasa de muestreo real se aleja mucho de la nominal (BLE saturado).

## Sin pasar flags (`.env`)

Copiá [.env.example](.env.example) a `.env` (mismas variables, prefijo `EEG_PRODUCER_`). Un flag por CLI siempre gana sobre el `.env`.

## Notas

- Protocolo completo (direcciones OSC, rangos, momentos): [README.md](../README.md) del proyecto.
- `bpm` real necesita `--ppg`; sin eso, queda fijo en `--baseline-bpm` (default 72).
