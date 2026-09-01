"""
Productor de datos EEG REALES para "La Asociación" -- Muse 2 -> OSC -> Pure Data.

Reemplaza a simulator/ (Node.js) cuando el Muse 2 real esta puesto. Usa
EXACTAMENTE el mismo protocolo OSC que simulator/src/oscSender.js, asi el
patch de Pd no cambia nada -- solo se apaga el simulador y se prende esto.

Requisito previo: un stream LSL de tipo EEG ya corriendo.
  Windows: BlueMuse (ver ../../README.md del proyecto Muse)
  macOS:   muselsl stream --name "Muse-XXXX" [--acc --gyro --ppg]

Streams opcionales (si no estan disponibles, el productor sigue mandando el
protocolo completo con valores de respaldo, para no romper el contrato con Pd):
  - ACC/GYRO -> `movement` real (si no hay ninguno, movement queda en 0 y se
    avisa una vez por consola).
  - PPG      -> `bpm` real via deteccion de picos (si no hay, se manda
    --baseline-bpm fijo).

Protocolo OSC (mismas direcciones/rangos que simulator/src/oscSender.js -- ver
README.md del proyecto para la tabla completa; la SEMANTICA de las waves
cambio, ver nota de calibracion de bandas mas abajo):
  /eeg/wave/delta   float 0-1  (que tan arriba del reposo de ESTA persona
  /eeg/wave/theta   float 0-1   esta esta banda ahora mismo -- 0.5 = igual
  /eeg/wave/beta    float 0-1   que su baseline, 1.0 = muy por encima, 0.0 =
  /eeg/wave/alfa    float 0-1   muy por debajo. Las 5 son independientes y
  /eeg/wave/gamma   float 0-1   YA NO suman ~1 entre si -- ver BandPowerTracker)
  /eeg/bpm          ~40-200
  /eeg/movement     float 0-1
  /eeg/moment       "calibrando" | "operando" | "movimiento_abrupto"

NOTA sobre calibracion de bandas (por que delta/theta ya no dominan todo el
tiempo): el espectro EEG cae ~1/f con la frecuencia -- delta (1-4Hz) tiene
ordenes de magnitud mas potencia absoluta que gamma (30-45Hz) SIEMPRE, sin
importar el estado mental de la persona. Antes esta clase dividia cada banda
entre la suma de las 5 ("potencia relativa"), lo que hacia que delta/theta
dominaran el 0..1 en cualquier sesion real. Ahora cada banda se compara
contra SU PROPIO reposo, capturado durante los primeros `--calibration`
segundos de la sesion (igual haya o no fase "calibrando" visible -- ver
`--skip-calibration`), siguiendo el mismo patron que usan muse-lsl/
bci-workshop (log10 de la potencia + z-score contra mean/std de calibracion).

IMPORTANTE: `--movement-scale` y `--kick-threshold` son valores que dependen
del dispositivo/persona real y NO se pueden adivinar sin probar con el Muse
puesto. Correr primero con --debug, ver los valores de "movement" impresos
en reposo vs. moviendose fuerte, y ajustar --movement-scale para que el
reposo de ~0.1-0.2 y un movimiento fuerte se acerque a 1.0.
"""

import argparse
import os
import queue
import sys
import threading
import time
from collections import deque

import numpy as np
from pylsl import StreamInlet, resolve_byprop
from pythonosc import udp_client
from scipy.signal import butter, filtfilt, find_peaks, welch

# ---------------------------------------------------------------------------
# Protocolo OSC -- debe quedar identico a simulator/src/oscSender.js
# ---------------------------------------------------------------------------
OSC_ADDRESSES = {
    "delta": "/eeg/wave/delta",
    "theta": "/eeg/wave/theta",
    "beta": "/eeg/wave/beta",
    "alfa": "/eeg/wave/alfa",
    "gamma": "/eeg/wave/gamma",
    "bpm": "/eeg/bpm",
    "movement": "/eeg/movement",
    "moment": "/eeg/moment",
}

BANDS = {
    "delta": (1, 4),
    "theta": (4, 8),
    "alfa": (8, 13),
    "beta": (13, 30),
    "gamma": (30, 45),
}

MOMENT = {
    "CALIBRANDO": "calibrando",
    "OPERANDO": "operando",
    "MOVIMIENTO_ABRUPTO": "movimiento_abrupto",
}


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Nombres de canal del stream EEG -> nos quedamos solo con los 4 electrodos
# reales (TP9/AF7/AF8/TP10). El Muse 2 manda un 5to canal ("Right AUX") sin
# electrodo util conectado -- promediarlo junto con los otros 4 solo mete
# ruido/deriva de baja frecuencia (justo lo que infla delta). El pipeline
# hermano de este (C:\V\Muse\muse_osc.py) ya descarta este canal asi; misma
# logica aca para que ambos midan lo mismo.
# ---------------------------------------------------------------------------
STANDARD_EEG_CHANNELS = ["TP9", "AF7", "AF8", "TP10"]


def read_channel_names(info):
    n_channels = info.channel_count()
    names = []
    ch = info.desc().child("channels").child("channel")
    for _ in range(n_channels):
        names.append(ch.child_value("label"))
        ch = ch.next_sibling()
    return names


def select_eeg_channels(names):
    indices = [names.index(n) for n in STANDARD_EEG_CHANNELS if n in names]
    if len(indices) != len(STANDARD_EEG_CHANNELS):
        print(f"[aviso] canales EEG inesperados {names} -- se usan todos (puede incluir AUX/ruido)")
        return list(range(len(names)))
    return indices


class _RunningStats:
    """Media/desvio estandar online (Welford) -- usado para el baseline de
    calibracion de cada banda. No hace falta guardar todas las muestras."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0

    def update(self, x):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def std(self):
        return (self._m2 / (self.n - 1)) ** 0.5 if self.n >= 2 else 0.0


# Muestras minimas de baseline antes de confiar en el z-score (a
# update_interval_s=0.5 son ~3s) -- con menos que esto el desvio estandar es
# demasiado ruidoso y una sola muestra rara dispararia el z-score.
BASELINE_MIN_SAMPLES = 6


# ---------------------------------------------------------------------------
# Bandas de EEG -> 0..1 por banda, cada una comparada contra SU PROPIO
# reposo (ver nota de calibracion de bandas en el docstring del modulo).
# ---------------------------------------------------------------------------
class BandPowerTracker:
    def __init__(self, inlet, calibration_s, window_s=2.0, update_interval_s=0.5, smoothing=0.25):
        info = inlet.info()
        self.inlet = inlet
        self.sfreq = info.nominal_srate()
        self.channel_indices = select_eeg_channels(read_channel_names(info))
        self.n_channels = len(self.channel_indices)
        self.buf_len = max(int(window_s * self.sfreq), 64)
        self.buffers = [deque(maxlen=self.buf_len) for _ in range(self.n_channels)]
        self.update_interval_s = update_interval_s
        self._last_update = 0.0
        self.smoothing = smoothing
        self.calibration_s = calibration_s
        self._start = None  # se fija en el primer poll(), no en __init__
        self._baseline = {name: _RunningStats() for name in BANDS}
        self.waves = {name: 0.5 for name in BANDS}  # arranca neutro hasta tener baseline

    def poll(self, now):
        samples, _ = self.inlet.pull_chunk(timeout=0.0, max_samples=int(self.sfreq))
        for sample in samples:
            for buf_i, ch_i in enumerate(self.channel_indices):
                self.buffers[buf_i].append(sample[ch_i])

        if now - self._last_update < self.update_interval_s:
            return
        if len(self.buffers[0]) < self.buf_len:
            return
        self._last_update = now

        if self._start is None:
            self._start = now
        collecting_baseline = (now - self._start) < self.calibration_s

        window = np.array(self.buffers).T  # (n_samples, n_channels)
        freqs, psd = welch(
            window, fs=self.sfreq, nperseg=min(256, window.shape[0]), detrend="linear", axis=0
        )
        for name, (lo, hi) in BANDS.items():
            mask = (freqs >= lo) & (freqs < hi)
            band_power = float(psd[mask].mean(axis=0).mean())  # promedio entre los 4 canales
            log_power = np.log10(max(band_power, 1e-12))  # log10 = "absolute band power" del SDK de Muse

            stats = self._baseline[name]
            if collecting_baseline:
                stats.update(log_power)

            if stats.n >= BASELINE_MIN_SAMPLES:
                z = (log_power - stats.mean) / (stats.std or 1.0)
                target = 1.0 / (1.0 + np.exp(-z))  # sigmoide: z=0 (=baseline) -> 0.5
            else:
                target = 0.5  # todavia sin baseline confiable

            # Suavizado (EMA) para no "escalonar" entre actualizaciones de 0.5s,
            # igual que MovementTracker -- sino se oye como pasos discretos en Pd.
            self.waves[name] = clamp(self.waves[name] + (target - self.waves[name]) * self.smoothing)


# ---------------------------------------------------------------------------
# ACC/GYRO -> movement 0..1. Preferimos GYRO (mas sensible a sacudidas de la
# cabeza), si no hay usamos ACC. Requiere --movement-scale calibrado in situ.
# ---------------------------------------------------------------------------
class MovementTracker:
    def __init__(self, inlet, scale, smoothing=0.3):
        self.inlet = inlet
        self.scale = scale
        self.smoothing = smoothing
        self.value = 0.05
        self._last_magnitude = 0.0

    def poll(self):
        if self.inlet is None:
            return
        samples, _ = self.inlet.pull_chunk(timeout=0.0, max_samples=64)
        for sample in samples:
            magnitude = float(np.sqrt(sum(v * v for v in sample)))
            self._last_magnitude = magnitude
            target = clamp(magnitude / self.scale)
            self.value += (target - self.value) * self.smoothing


# ---------------------------------------------------------------------------
# PPG -> bpm real via deteccion de picos. Requiere ventana de varios segundos
# para ser estable; con pocos datos o senal ruidosa, mantiene el ultimo valor
# valido (o el baseline si nunca hubo uno).
# ---------------------------------------------------------------------------
class BpmTracker:
    def __init__(self, inlet, baseline_bpm, window_s=8.0, update_interval_s=2.0):
        info = inlet.info()
        self.inlet = inlet
        self.sfreq = info.nominal_srate() or 64.0
        self.buf_len = int(window_s * self.sfreq)
        self.buffer = deque(maxlen=self.buf_len)
        self.update_interval_s = update_interval_s
        self._last_update = 0.0
        self.bpm = baseline_bpm

    def poll(self, now):
        if self.inlet is None:
            return
        samples, _ = self.inlet.pull_chunk(timeout=0.0, max_samples=int(self.sfreq))
        for sample in samples:
            # canal 1 = PPG2 (infrarrojo) -- el que sirve para pulso. PPG1 (indice 0)
            # es luz ambiente, PPG3 (indice 2) es rojo; ver muselsl/constants.py.
            self.buffer.append(sample[1] if len(sample) > 1 else sample[0])

        if now - self._last_update < self.update_interval_s:
            return
        if len(self.buffer) < self.buf_len:
            return
        self._last_update = now

        sig = np.array(self.buffer)
        nyq = self.sfreq / 2.0
        try:
            b, a = butter(3, [0.7 / nyq, 3.5 / nyq], btype="band")
            filtered = filtfilt(b, a, sig)
        except ValueError:
            return  # sfreq muy baja para este filtro, se mantiene el ultimo valor

        min_distance = max(int(self.sfreq * 60 / 200), 1)  # tope 200 bpm
        peaks, _ = find_peaks(filtered, distance=min_distance, prominence=filtered.std() * 0.5)
        if len(peaks) < 2:
            return

        intervals_s = np.diff(peaks) / self.sfreq
        bpm = 60.0 / np.median(intervals_s)
        if 40 <= bpm <= 200:
            self.bpm = 0.5 * self.bpm + 0.5 * bpm  # suavizado


# ---------------------------------------------------------------------------
# Maquina de estados de los 3 momentos -- misma logica que
# simulator/src/dataGenerator.js (mismos nombres/duraciones por defecto).
# ---------------------------------------------------------------------------
class MomentPhase:
    def __init__(self, calibration_s=60.0, kick_window_s=1.5, kick_threshold=0.6, refractory_s=5.0):
        self.calibration_s = calibration_s
        self.kick_window_s = kick_window_s
        self.kick_threshold = kick_threshold
        self.refractory_s = refractory_s
        self.phase = MOMENT["CALIBRANDO"]
        self.elapsed = 0.0
        self._refractory = 0.0

    def _enter(self, phase):
        self.phase = phase
        self.elapsed = 0.0

    def skip_calibration(self):
        if self.phase == MOMENT["CALIBRANDO"]:
            self._enter(MOMENT["OPERANDO"])

    def kick(self):
        if self.phase == MOMENT["CALIBRANDO"]:
            return False
        self._enter(MOMENT["MOVIMIENTO_ABRUPTO"])
        self._refractory = self.refractory_s
        return True

    def reset(self):
        self._enter(MOMENT["CALIBRANDO"])
        self._refractory = 0.0

    def step(self, dt, movement_value):
        self.elapsed += dt
        self._refractory = max(0.0, self._refractory - dt)

        if self.phase == MOMENT["CALIBRANDO"] and self.elapsed >= self.calibration_s:
            self._enter(MOMENT["OPERANDO"])
        elif self.phase == MOMENT["MOVIMIENTO_ABRUPTO"] and self.elapsed >= self.kick_window_s:
            self._enter(MOMENT["OPERANDO"])
        elif (
            self.phase == MOMENT["OPERANDO"]
            and self._refractory <= 0.0
            and movement_value >= self.kick_threshold
        ):
            self.kick()

        return self.phase


# ---------------------------------------------------------------------------
# Lectura de comandos por stdin (kick/skip/reset/quit) sin bloquear el loop
# principal -- mismos comandos que el simulador de Node, para paridad.
# ---------------------------------------------------------------------------
def start_stdin_reader():
    q = queue.Queue()

    def _reader():
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd:
                q.put(cmd)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return q


def resolve_optional(stream_type, timeout, label):
    streams = resolve_byprop("type", stream_type, timeout=timeout)
    if not streams:
        print(f"[aviso] no se encontro stream LSL '{stream_type}' -- {label}")
        return None
    return StreamInlet(streams[0], max_buflen=60)


def _env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() == "true"


def main():
    # Mismo patron que simulator/src/index.js: variable de entorno como default,
    # un flag explicito por linea de comandos siempre gana. Prefijo EEG_PRODUCER_
    # (distinto de EEG_SIM_* del simulador) para poder tener ambos .env sin choque.
    env = os.environ
    parser = argparse.ArgumentParser(description="Productor real Muse 2 -> OSC -> Pure Data (protocolo La Asociacion).")
    parser.add_argument("--host", default=env.get("EEG_PRODUCER_HOST", "127.0.0.1"), help="Host de Pure Data")
    parser.add_argument("--port", type=int, default=int(env.get("EEG_PRODUCER_PORT", "9000")), help="Puerto UDP de Pure Data")
    parser.add_argument("--rate", type=float, default=float(env.get("EEG_PRODUCER_RATE", "10")), help="Mensajes por segundo (default 10, igual que el simulador)")
    parser.add_argument("--calibration", type=float, default=float(env.get("EEG_PRODUCER_CALIBRATION", "60")), help="Duracion de calibracion en segundos")
    parser.add_argument("--baseline-bpm", type=float, default=float(env.get("EEG_PRODUCER_BASELINE_BPM", "72")), help="bpm de respaldo si no hay stream PPG")
    parser.add_argument("--skip-calibration", action="store_true", default=_env_bool("EEG_PRODUCER_SKIP_CALIBRATION"), help="Arranca directo en 'operando'")
    parser.add_argument("--movement-scale", type=float, default=float(env.get("EEG_PRODUCER_MOVEMENT_SCALE", "50")), help="Magnitud de gyro/accel que equivale a movement=1.0 (AJUSTAR in situ, ver --debug)")
    parser.add_argument("--kick-threshold", type=float, default=float(env.get("EEG_PRODUCER_KICK_THRESHOLD", "0.6")), help="movement >= esto en 'operando' dispara la patada automaticamente")
    parser.add_argument("--kick-refractory", type=float, default=float(env.get("EEG_PRODUCER_KICK_REFRACTORY", "5")), help="segundos minimos entre patadas auto-detectadas")
    parser.add_argument("--stream-timeout", type=float, default=float(env.get("EEG_PRODUCER_STREAM_TIMEOUT", "5")), help="segundos a esperar por cada stream LSL opcional (ACC/GYRO/PPG)")
    parser.add_argument("--debug", action="store_true", default=_env_bool("EEG_PRODUCER_DEBUG"), help="imprime movement/bpm en cada actualizacion, para calibrar --movement-scale")
    args = parser.parse_args()

    client = udp_client.SimpleUDPClient(args.host, args.port)
    print(f"Mandando datos EEG reales a {args.host}:{args.port} (OSC/UDP) a {args.rate}Hz")
    print(f"Direcciones OSC: {', '.join(OSC_ADDRESSES.values())}")

    print("Resolviendo stream EEG (asegurate que BlueMuse / muselsl stream ya esta corriendo)...")
    eeg_streams = resolve_byprop("type", "EEG", timeout=10)
    if not eeg_streams:
        raise RuntimeError(
            "No se encontro stream EEG. En Windows: abre BlueMuse, conecta el Muse 2 y "
            "dale 'Start Streaming'. En Mac: corre 'muselsl stream --name Muse-XXXX' en otra terminal."
        )
    eeg_inlet = StreamInlet(eeg_streams[0], max_buflen=60)
    print(f"EEG conectado: {eeg_inlet.info().name()}")

    gyro_inlet = resolve_optional("GYRO", args.stream_timeout, "movement quedara en 0 (agrega '--gyro' a 'muselsl stream')")
    acc_inlet = None
    if gyro_inlet is None:
        acc_inlet = resolve_optional("ACC", args.stream_timeout, "movement quedara en 0 (agrega '--acc' a 'muselsl stream')")
    ppg_inlet = resolve_optional("PPG", args.stream_timeout, f"bpm quedara fijo en {args.baseline_bpm} (agrega '--ppg' a 'muselsl stream')")

    bands = BandPowerTracker(eeg_inlet, calibration_s=args.calibration)
    movement = MovementTracker(gyro_inlet or acc_inlet, scale=args.movement_scale)
    bpm = BpmTracker(ppg_inlet, baseline_bpm=args.baseline_bpm) if ppg_inlet else None
    phase = MomentPhase(
        calibration_s=args.calibration,
        kick_threshold=args.kick_threshold,
        refractory_s=args.kick_refractory,
    )
    if args.skip_calibration:
        phase.skip_calibration()

    commands = start_stdin_reader()
    print(f"Fase inicial: {phase.phase} (calibracion {args.calibration}s)")
    print("Comandos: kick | skip | reset | quit + Enter")

    tick_s = 1.0 / args.rate
    last_tick = time.time()
    last_logged_phase = None

    try:
        while True:
            now = time.time()
            dt = now - last_tick
            if dt < tick_s:
                time.sleep(tick_s - dt)
                now = time.time()
                dt = now - last_tick
            last_tick = now

            try:
                while True:
                    cmd = commands.get_nowait()
                    if cmd == "kick":
                        ok = phase.kick()
                        print("[manual] patada disparada" if ok else "[manual] no se puede patear durante calibracion")
                    elif cmd == "skip":
                        phase.skip_calibration()
                        print("[manual] calibracion saltada")
                    elif cmd == "reset":
                        phase.reset()
                        last_logged_phase = None
                        print("[manual] simulacion reiniciada")
                    elif cmd in ("quit", "exit"):
                        print("\nProductor detenido.")
                        return
                    else:
                        print(f"Comando no reconocido: '{cmd}' (usa kick | skip | reset | quit)")
            except queue.Empty:
                pass

            bands.poll(now)
            movement.poll()
            if bpm is not None:
                bpm.poll(now)

            current_phase = phase.step(dt, movement.value)
            if current_phase != last_logged_phase:
                print(f"[fase] -> {current_phase}")
                last_logged_phase = current_phase

            client.send_message(OSC_ADDRESSES["delta"], bands.waves["delta"])
            client.send_message(OSC_ADDRESSES["theta"], bands.waves["theta"])
            client.send_message(OSC_ADDRESSES["beta"], bands.waves["beta"])
            client.send_message(OSC_ADDRESSES["alfa"], bands.waves["alfa"])
            client.send_message(OSC_ADDRESSES["gamma"], bands.waves["gamma"])
            client.send_message(OSC_ADDRESSES["bpm"], round(bpm.bpm if bpm else args.baseline_bpm))
            client.send_message(OSC_ADDRESSES["movement"], round(movement.value, 3))
            client.send_message(OSC_ADDRESSES["moment"], current_phase)

            if args.debug:
                print(f"movement={movement.value:.3f} (mag={movement._last_magnitude:.1f}) bpm={(bpm.bpm if bpm else args.baseline_bpm):.0f}")
    except KeyboardInterrupt:
        print("\nProductor detenido.")


if __name__ == "__main__":
    main()
