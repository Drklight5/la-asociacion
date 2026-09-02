#!/bin/bash
# ============================================================
#  La Asociacion  |  Arranque en 1 clic  |  macOS (Apple Silicon)
#
#  Doble clic en este archivo. Levanta los 4 procesos:
#    1. muselsl stream           -- puente Bluetooth del Muse 2
#    2. viz/bridge.py            -- relay OSC + WebSocket
#    3. http.server              -- la grafica en http://localhost:8000
#    4. producer/muse_producer   -- Muse 2 -> OSC        (esta ventana)
#
#  NO toca Pure Data. Abri el patch como siempre; escucha en 9000 y
#  recibe los datos a traves del bridge sin cambiar nada.
#
#  La primera vez, si macOS bloquea el archivo:
#    clic derecho -> Abrir -> Abrir.
# ============================================================
set -u
cd "$(dirname "$0")/.."

# ---- valores por defecto (se pisan en deploy/config.txt) ----
MUSE_NAME=""
MUSE_STREAM_ARGS="--acc --gyro --ppg"
PRODUCER_ARGS=""
ABRIR_NAVEGADOR="si"
BRIDGE_PORT=9001
PD_PORT=9000
WEB_PORT=8000

if [ -f deploy/config.txt ]; then
  while IFS='=' read -r key val; do
    case "$key" in
      \#*|"") ;;
      MUSE_NAME)        MUSE_NAME=$val ;;
      MUSE_STREAM_ARGS) MUSE_STREAM_ARGS=$val ;;
      PRODUCER_ARGS)    PRODUCER_ARGS=$val ;;
      ABRIR_NAVEGADOR)  ABRIR_NAVEGADOR=$val ;;
      BRIDGE_PORT)      BRIDGE_PORT=$val ;;
      PD_PORT)          PD_PORT=$val ;;
      WEB_PORT)         WEB_PORT=$val ;;
    esac
  done < deploy/config.txt
fi

pause() { echo; read -n 1 -s -r -p "Enter para cerrar..."; echo; }

# ---- Python disponible? ----
if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo "[ERROR] No se encontro python3."
  echo "  Instalalo con Homebrew:"
  echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo "    brew install python"
  pause
  exit 1
fi

# ---- entorno virtual + dependencias ----
if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] Creando entorno virtual (una sola vez, puede tardar)..."
  python3 -m venv .venv || { echo "[ERROR] No se pudo crear el entorno."; pause; exit 1; }
fi
PY=".venv/bin/python"
echo "[setup] Verificando dependencias (primera vez tarda un poco)..."
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r producer/requirements.txt || { echo "[ERROR] Fallo la instalacion (producer)."; pause; exit 1; }
"$PY" -m pip install --quiet -r viz/requirements.txt      || { echo "[ERROR] Fallo la instalacion (viz)."; pause; exit 1; }

# ---- limpieza: matar todos los procesos hijos al cerrar ----
PIDS=()
cleanup() {
  echo
  echo "[fin] Deteniendo servicios..."
  for pid in "${PIDS[@]}"; do kill "$pid" >/dev/null 2>&1; done
}
trap cleanup EXIT INT TERM

# ---- 1. puente Bluetooth del Muse (muselsl) ----
echo
echo "[1/4] Iniciando el puente Bluetooth del Muse (muselsl)..."
echo "      Si macOS pide permiso de Bluetooth: aceptalo y volve a hacer"
echo "      doble clic en este archivo."
if [ -n "$MUSE_NAME" ]; then
  # shellcheck disable=SC2086
  .venv/bin/muselsl stream --name "$MUSE_NAME" $MUSE_STREAM_ARGS &
else
  # shellcheck disable=SC2086
  .venv/bin/muselsl stream $MUSE_STREAM_ARGS &
fi
PIDS+=($!)

echo "      Esperando el stream del Muse (hasta 25 s)..."
ok=""
for _ in $(seq 1 25); do
  if "$PY" -c "from pylsl import resolve_byprop; import sys; sys.exit(0 if resolve_byprop('type','EEG',timeout=1) else 1)" 2>/dev/null; then
    ok="1"; break
  fi
done
if [ -z "$ok" ]; then
  echo
  echo "[aviso] Todavia no aparece el stream del Muse. El productor va a"
  echo "        seguir reintentando solo. Revisa que el Muse este encendido"
  echo "        y con buena bateria."
fi

# ---- 2. bridge OSC + WebSocket ----
echo "[2/4] Iniciando bridge OSC/WebSocket (relay $BRIDGE_PORT -> Pd $PD_PORT)..."
"$PY" viz/bridge.py --listen-port "$BRIDGE_PORT" --pd-host 127.0.0.1 --pd-port "$PD_PORT" &
PIDS+=($!)

# ---- 3. servidor de la grafica ----
echo "[3/4] Sirviendo la grafica en http://localhost:$WEB_PORT ..."
"$PY" -m http.server "$WEB_PORT" --directory viz >/dev/null 2>&1 &
PIDS+=($!)

sleep 2
if [ "$ABRIR_NAVEGADOR" = "si" ]; then
  open "http://localhost:$WEB_PORT/"
fi

echo
echo "============================================================"
echo " [4/4] En esta ventana corre el PRODUCTOR del Muse."
echo "   kick  = marcar la patada        skip  = saltar calibracion"
echo "   reset = reiniciar con persona nueva     quit = salir"
echo
echo " Pure Data: abrilo aparte, escucha en el puerto $PD_PORT."
echo " Cerrar esta ventana (o Ctrl+C) detiene todo."
echo "============================================================"
echo

# ---- 4. productor en primer plano ----
# shellcheck disable=SC2086
"$PY" producer/muse_producer.py --host 127.0.0.1 --port "$BRIDGE_PORT" $PRODUCER_ARGS

pause
