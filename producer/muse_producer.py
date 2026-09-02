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
dominaran el 0..1 en cualquier sesion real. Ahora (ver BandPowerTracker para
el detalle): log10 de la potencia por banda -> se resta el comun-modo (la
media de las 5, que se lleva el 1/f y los artefactos de banda ancha) -> queda
la FORMA del espectro -> z-score de esa forma contra el reposo de ESTA
persona, capturado en los primeros `--calibration` segundos (igual haya o no
fase "calibrando" visible -- ver `--skip-calibration`; el comando `reset`
recalibra) -> mapeo tanh suave a 0..1. Mismo enfoque de fondo que muse-lsl /
bci-workshop. El baseline se congela al terminar la calibracion y no se
vuelve a tocar hasta un `reset` o reiniciar el proceso.

Robustez de adquisicion (todo interno, el protocolo OSC no cambia):
  - Salud de canales: se descarta el 5to canal ("Right AUX") siempre, y
    cualquier electrodo que quede plano / sature / haga mal contacto sale del
    promedio (y vuelve si se recupera). Si TP9/TP10 estan mal, queda AF7/AF8.
  - Calibracion: se ignoran los primeros `--calib-settle` s (electrodos secos
    asentandose) y al terminar se imprime un reporte de calidad -- si sale
    "DUDOSA", conviene reacomodar el Muse y escribir 'reset'.
  - Conexion: si el stream EEG se congela se avisa y se mantienen los ultimos
    valores; si sigue caido se reintenta resolver/reconectar solo. Se avisa si
    la tasa de muestreo real se aleja mucho de la nominal (BLE saturado).

MOVIMIENTO: con `--movement-auto` (default), la calibracion mide el reposo y
fija `--movement-scale` y el umbral de patada solos -- ya no hay que ajustarlos
a mano. Con `--no-movement-auto` se usan los valores de los flags tal cual
(ver --debug para el valor de "movement" en reposo vs. patada).
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
    if not indices:
        print(f"[aviso] canales EEG inesperados {names} -- se usan todos (puede incluir AUX/ruido)")
        return list(range(len(names)))
    if len(indices) != len(STANDARD_EEG_CHANNELS):
        print(f"[aviso] faltan canales EEG estandar en {names} -- se usan {[names[i] for i in indices]}")
    return indices


# --- adquisicion / conexion ---
EEG_MAX_BUFLEN = 5           # s de buffer LSL (bajo = siempre señal fresca, no backlog viejo)
STREAM_STALL_S = 2.0         # sin muestras nuevas por mas de esto -> stream "congelado"
EEG_RECONNECT_AFTER_S = 6.0  # congelado tanto tiempo -> reintentar resolve + StreamInlet
RATE_CHECK_S = 10.0          # cada cuanto se estima la tasa real de muestreo
RATE_TOLERANCE = 0.25        # se avisa si la tasa real se aleja > esto de la nominal

# --- salud de canales EEG (en uV; BlueMuse y muselsl entregan uV) ---
CHAN_FLATLINE_STD = 0.5      # std por debajo -> canal muerto / desconectado
CHAN_INSANE_STD = 250.0      # std sostenida por encima -> no es EEG (EMG / mal contacto)
CHAN_RAIL_PTP = 1500.0       # pico-a-pico por encima -> saturacion del ADC
CHAN_DROP_STREAK = 12        # ventanas malas seguidas (~3s @0.25s) -> sacar el canal del promedio
CHAN_RESTORE_STREAK = 20     # ventanas buenas seguidas (~5s) -> reincorporarlo

# --- auto-calibracion de movimiento ---
MOVE_REST_TARGET = 0.12      # a cuanto se mapea el movimiento en reposo tras auto-calibrar
MOVE_KICK_MARGIN = 0.35      # umbral de patada sugerido = MOVE_REST_TARGET + esto


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


BASELINE_MIN_SAMPLES = 6       # muestras minimas de baseline antes de confiar en el z-score
BASELINE_SAMPLE_GAP_S = 2.0    # separacion entre muestras del baseline (= largo de ventana):
                              # ventanas solapadas subestiman el desvio por autocorrelacion
STD_FLOOR_BELS = 0.06         # piso del desvio (~15% de potencia). Una calibracion corta y
                              # quieta da un desvio minusculo y el mapeo se satura enseguida.
Z_SPREAD = 3.0               # cuantos sigmas ocupan ~medio rango del tanh. Mas alto = mas suave.
ARTIFACT_PTP_RATIO = 4.0      # se descarta la ventana si su pico-a-pico supera esto x el de
                              # calibracion (parpadeo / mordida / sacudida de cabeza)


# ---------------------------------------------------------------------------
# Bandas de EEG -> 0..1 por banda. NO es "banda / suma de las 5" (eso hacia
# que delta/theta dominaran siempre por el 1/f del espectro). El pipeline es:
#   1. solo canales SANOS (ver salud de canales abajo); log10 de la potencia
#      por banda ("absolute band power" del SDK de Muse).
#   2. quitar el comun-modo: restar la media de las 5 log-potencias -> queda
#      la FORMA del espectro (que banda esta alta RELATIVO al resto ahora),
#      sin el offset 1/f ni los artefactos de banda ancha que suben las 5
#      bandas juntas.
#   3. z-score de esa forma contra el baseline capturado en calibracion (se
#      saltan los primeros `--calib-settle` s de asentamiento de electrodos),
#      con piso en el desvio, y mapeo tanh suave a 0..1 (0.5 = igual que el
#      baseline).
#   4. gating: ventanas con pico-a-pico muy por encima del de calibracion
#      (parpadeo/movimiento) se descartan enteras.
#   5. durante `movimiento_abrupto` las waves quedan CONGELADAS (la patada es
#      puro artefacto de movimiento) -- el loop principal pasa frozen=True.
# NO suaviza fuerte a proposito: eso lo hace Pd. Aca el foco es que el numero
# sea correcto y confiable, no bonito.
# ---------------------------------------------------------------------------
class BandPowerTracker:
    def __init__(self, inlet, calib_settle_s=10.0, window_s=2.0, update_interval_s=0.25, smoothing=0.25):
        self.inlet = inlet
        self.window_s = window_s
        self.update_interval_s = update_interval_s
        self._last_update = 0.0
        self.smoothing = smoothing
        self.calib_settle_s = calib_settle_s
        self._read_stream_info()
        # --- salud de conexion ---
        self.sample_rate_est = self.sfreq
        self.stalled = False
        self.last_sample_t = None
        self._new_samples = False
        self._rx_count = 0
        self._rx_window_start = 0.0
        self.waves = {name: 0.5 for name in BANDS}
        self._reset_baseline()

    def _read_stream_info(self):
        info = self.inlet.info()
        self.sfreq = info.nominal_srate() or 256.0
        self.buf_len = max(int(self.window_s * self.sfreq), 64)
        self.all_names = read_channel_names(info)
        self.candidate_idx = select_eeg_channels(self.all_names)  # sin AUX
        self.candidate_names = [self.all_names[i] for i in self.candidate_idx]
        self.n_channels = len(self.candidate_idx)

    def replace_inlet(self, inlet):
        """Se llama tras reconectar el stream EEG (BlueMuse reiniciado, Muse
        que se durmio y volvio, etc.). Rearma buffers; el baseline se conserva
        si el layout de canales no cambio."""
        self.inlet = inlet
        prev_names = getattr(self, "candidate_names", None)
        self._read_stream_info()
        self.buffers = [deque(maxlen=self.buf_len) for _ in range(self.n_channels)]
        self.last_sample_t = None
        self._new_samples = False
        self.stalled = False
        self._rx_count = 0
        self._rx_window_start = 0.0
        if prev_names != self.candidate_names:
            print("[conexion] el layout de canales cambio tras reconectar -- recalibra ('reset')")
            self._reset_baseline()
        else:  # mismo layout: se conserva el baseline, se revalua la salud de canales
            self._bad_streak = [0] * self.n_channels
            self._good_streak = [0] * self.n_channels

    def _reset_baseline(self):
        """Vuelve al estado 'sin calibrar'. Lo llama tambien el comando `reset`
        del loop principal para que las bandas se recalibren con la persona
        nueva -- sino el baseline queda pegado a la persona anterior."""
        self._calib_start = None          # se fija al entrar en 'calibrando'
        self._was_calibrating = False
        self._last_baseline_sample = 0.0
        self._baseline = {name: _RunningStats() for name in BANDS}
        self._ptp_ref = _RunningStats()   # pico-a-pico tipico en calibracion (para el gating)
        # bootstrap: juntar el baseline sobre la marcha en 'operando'. Arranca en
        # True para cubrir --skip-calibration (nunca hay fase 'calibrando'); se
        # apaga en cuanto empieza una calibracion de verdad.
        self._bootstrap = True
        self._calib_windows = 0
        self._calib_rejected = 0
        self._calib_report_done = False
        # salud de canales: posiciones (en buffers) actualmente sanas
        self._active_pos = list(range(self.n_channels))
        self._bad_streak = [0] * self.n_channels
        self._good_streak = [0] * self.n_channels
        self.buffers = [deque(maxlen=self.buf_len) for _ in range(self.n_channels)]
        for name in BANDS:
            self.waves[name] = 0.5

    def reset(self):
        self._reset_baseline()

    # -- salud de canales -----------------------------------------------------
    def _update_channel_health(self, window):
        for pos in range(self.n_channels):
            col = window[:, pos]
            std = float(col.std())
            healthy = (CHAN_FLATLINE_STD < std < CHAN_INSANE_STD) and float(np.ptp(col)) < CHAN_RAIL_PTP
            if healthy:
                self._good_streak[pos] += 1
                self._bad_streak[pos] = 0
            else:
                self._bad_streak[pos] += 1
                self._good_streak[pos] = 0
        new_active = []
        for pos in range(self.n_channels):
            active = pos in self._active_pos
            if active and self._bad_streak[pos] >= CHAN_DROP_STREAK:
                print(f"[canal] {self.candidate_names[pos]} sin señal usable -- fuera del promedio")
            elif not active and self._good_streak[pos] >= CHAN_RESTORE_STREAK:
                print(f"[canal] {self.candidate_names[pos]} recuperado -- vuelve al promedio")
                new_active.append(pos)
            elif active:
                new_active.append(pos)
        self._active_pos = new_active

    # -- reporte de calidad de calibracion ---------------------------------
    def _finalize_calibration(self):
        if self._calib_report_done:
            return
        n = self._baseline["delta"].n
        if n < BASELINE_MIN_SAMPLES:
            if not self._bootstrap:
                self._bootstrap = True
                print(f"[calibracion] pocas muestras limpias ({n}) -- completando el baseline en 'operando'")
            return
        self._bootstrap = False
        self._calib_report_done = True
        rej = self._calib_rejected / max(self._calib_windows, 1)
        used = list(self.candidate_names[p] for p in self._active_pos)
        max_std = max((s.std for s in self._baseline.values()), default=0.0)
        dudosa = n < BASELINE_MIN_SAMPLES + 3 or not used or rej > 0.5 or max_std > 0.5
        if dudosa:
            print("[calibracion] !!! CALIDAD DUDOSA -- reacomoda el Muse (contacto/pelo) y escribi 'reset'")
            print(f"[calibracion]     muestras={n} rechazo_artefacto={rej * 100:.0f}% "
                  f"canales={used or 'NINGUNO'} desvio_max={max_std:.2f}")
        else:
            print(f"[calibracion] baseline OK: muestras={n} canales={used} "
                  f"rechazo_artefacto={rej * 100:.0f}%")

    def poll(self, now, calibrating, frozen=False):
        samples, _ = self.inlet.pull_chunk(timeout=0.0, max_samples=int(self.sfreq * 2))
        if samples:
            arr = np.asarray(samples, dtype=float)
            for buf_i, ch_i in enumerate(self.candidate_idx):
                self.buffers[buf_i].extend(arr[:, ch_i])
            self.last_sample_t = now
            self._new_samples = True
            self._rx_count += len(samples)

        # --- stall / reconexion la maneja el loop principal mirando .stalled ---
        if self.last_sample_t is None:
            return
        if now - self.last_sample_t > STREAM_STALL_S:
            if not self.stalled:
                print(f"[conexion] EEG sin datos hace {now - self.last_sample_t:.1f}s -- "
                      f"manteniendo ultimos valores")
            self.stalled = True
            return
        if self.stalled:
            print("[conexion] EEG reanudado")
            self.stalled = False
            self._rx_count = 0
            self._rx_window_start = now  # no promediar la tasa a traves del corte

        # --- tasa de muestreo real (BLE saturado / interferencia) ---
        if self._rx_window_start == 0.0:
            self._rx_window_start = now
        elif now - self._rx_window_start >= RATE_CHECK_S:
            self.sample_rate_est = self._rx_count / (now - self._rx_window_start)
            if abs(self.sample_rate_est - self.sfreq) > RATE_TOLERANCE * self.sfreq:
                print(f"[conexion] tasa EEG real ~{self.sample_rate_est:.0f}Hz vs nominal "
                      f"{self.sfreq:.0f}Hz (BLE saturado?)")
            self._rx_count = 0
            self._rx_window_start = now

        if now - self._last_update < self.update_interval_s:
            return
        if len(self.buffers[0]) < self.buf_len:
            return
        if not self._new_samples:
            return  # nada nuevo en el buffer (stream lento/cortado) -> waves quietas
        self._new_samples = False
        self._last_update = now

        # bookkeeping de la fase de calibracion
        if calibrating and not self._was_calibrating:
            self._calib_start = now
            self._bootstrap = False  # arranca calibracion de verdad -> manda esa
        if self._was_calibrating and not calibrating:
            self._finalize_calibration()
        self._was_calibrating = calibrating

        if frozen:
            return  # movimiento_abrupto: waves congeladas tal cual

        window = np.array(self.buffers, dtype=float).T  # (buf_len, n_channels)
        self._update_channel_health(window)
        if not self._active_pos:
            return  # ningun canal sano -> mantener ultimos valores
        w = window[:, self._active_pos]

        settled = self._calib_start is not None and (now - self._calib_start) >= self.calib_settle_s
        collecting = (calibrating and settled) or self._bootstrap
        take_sample = collecting and (now - self._last_baseline_sample >= BASELINE_SAMPLE_GAP_S)
        if calibrating and settled:
            self._calib_windows += 1

        # --- gating de artefactos (parpadeo / mordida / sacudida) ---
        ptp = float(np.ptp(w, axis=0).max())
        ptp_known = self._ptp_ref.n >= BASELINE_MIN_SAMPLES
        if ptp_known and ptp > ARTIFACT_PTP_RATIO * self._ptp_ref.mean:
            if calibrating and settled:
                self._calib_rejected += 1
            return  # ventana sucia
        if collecting:
            self._ptp_ref.update(ptp)

        # fs = la nominal del stream (cada muestra representa 1/sfreq s, aunque el
        # BLE pierda paquetes). sample_rate_est es solo diagnostico.
        freqs, psd = welch(w, fs=self.sfreq, nperseg=min(256, w.shape[0]), detrend="linear", axis=0)
        log_power = {}
        for name, (lo, hi) in BANDS.items():
            mask = (freqs >= lo) & (freqs < hi)
            if mask.any():
                band_power = float(psd[mask].mean(axis=0).mean())  # promedio entre canales sanos
            else:  # resolucion espectral demasiado gruesa para la banda -> bin mas cercano
                band_power = float(psd[np.argmin(np.abs(freqs - 0.5 * (lo + hi)))].mean())
            log_power[name] = np.log10(max(band_power, 1e-12))

        common = sum(log_power.values()) / len(log_power)
        shape = {name: log_power[name] - common for name in BANDS}

        if take_sample:
            for name in BANDS:
                self._baseline[name].update(shape[name])
            self._last_baseline_sample = now
        if self._bootstrap and self._baseline["delta"].n >= BASELINE_MIN_SAMPLES + 3:
            self._finalize_calibration()

        for name in BANDS:
            stats = self._baseline[name]
            if stats.n >= BASELINE_MIN_SAMPLES:
                std_eff = (stats.std ** 2 + STD_FLOOR_BELS ** 2) ** 0.5
                z = (shape[name] - stats.mean) / std_eff
                target = 0.5 + 0.5 * np.tanh(z / Z_SPREAD)  # z=0 (=baseline) -> 0.5
            else:
                target = 0.5  # todavia sin baseline confiable
            # EMA suave nomas (el suavizado "de verdad" lo hace Pd). float() para
            # mandar un float de Python por OSC, no un np.float64.
            self.waves[name] = float(clamp(self.waves[name] + (target - self.waves[name]) * self.smoothing))


# ---------------------------------------------------------------------------
# ACC/GYRO -> movement 0..1. Preferimos GYRO (mas sensible a sacudidas de la
# cabeza), si no hay usamos ACC. Se le resta un bias lento por eje, asi sirve
# igual para GYRO (bias ~0) que para ACC (bias = gravedad) -- en ambos casos
# mide "cuanto movimiento dinamico hay".
#   - self.value    : suavizado, es lo que sale por OSC (para graficar/animar).
#   - self.instant  : casi crudo, es lo que dispara la deteccion de patada.
# Con --movement-auto, la calibracion mide el reposo y fija `scale` sola.
# ---------------------------------------------------------------------------
class MovementTracker:
    def __init__(self, inlet, scale, smoothing=0.3, auto=True):
        self.inlet = inlet
        self.scale = max(scale, 1e-6)
        self.smoothing = smoothing
        self.auto = auto and inlet is not None
        self.value = 0.05
        self.instant = 0.05
        self._last_magnitude = 0.0
        self._bias = None
        self._rest = _RunningStats()
        self.calibrated = False

    def poll(self, now=None, calibrating=False):
        if self.inlet is None:
            return
        samples, _ = self.inlet.pull_chunk(timeout=0.0, max_samples=64)
        for sample in samples:
            vec = np.asarray(sample, dtype=float)
            if self._bias is None:
                self._bias = vec.copy()
            else:
                self._bias += (vec - self._bias) * 0.01  # bias lento (gravedad / offset)
            dyn = vec - self._bias
            magnitude = float(np.sqrt(np.dot(dyn, dyn)))
            self._last_magnitude = magnitude
            self.instant = clamp(magnitude / self.scale)
            self.value += (self.instant - self.value) * self.smoothing
            if calibrating:
                self._rest.update(magnitude)

    def finalize_calibration(self):
        """Fija `scale` para que el reposo medido caiga en ~MOVE_REST_TARGET.
        Devuelve un umbral de patada sugerido, o None si no se pudo."""
        if not self.auto or self._rest.n < 20:
            return None
        rest_high = self._rest.mean + 2.0 * self._rest.std
        if rest_high <= 1e-6:
            return None
        self.scale = rest_high / MOVE_REST_TARGET
        self.calibrated = True
        kick_thr = clamp(MOVE_REST_TARGET + MOVE_KICK_MARGIN)
        print(f"[calibracion] movimiento: reposo mag~{self._rest.mean:.2f} "
              f"-> scale={self.scale:.2f}, umbral patada={kick_thr:.2f}")
        return kick_thr

    def reset(self):
        self._rest = _RunningStats()
        self.calibrated = False


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

    def set_kick_threshold(self, value):
        self.kick_threshold = value

    def step(self, dt, movement_value):
        # movement_value = MovementTracker.instant (casi crudo), no el suavizado
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
    return StreamInlet(streams[0], max_buflen=EEG_MAX_BUFLEN)


def resolve_eeg_inlet(timeout=2.0):
    """Un intento de resolver + abrir el stream EEG. Devuelve el inlet o None.
    max_buflen chico a proposito: si el stream se congela y vuelve, no queremos
    procesar 60s de backlog viejo, queremos señal fresca."""
    streams = resolve_byprop("type", "EEG", timeout=timeout)
    if not streams:
        return None
    return StreamInlet(streams[0], max_buflen=EEG_MAX_BUFLEN)


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
    parser.add_argument("--calib-settle", type=float, default=float(env.get("EEG_PRODUCER_CALIB_SETTLE", "10")), help="segundos iniciales de la calibracion que se ignoran (electrodos secos asentandose)")
    parser.add_argument("--movement-auto", action=argparse.BooleanOptionalAction, default=_env_bool("EEG_PRODUCER_MOVEMENT_AUTO", True), help="mide el reposo en la calibracion y fija --movement-scale / umbral de patada solo")
    parser.add_argument("--movement-scale", type=float, default=float(env.get("EEG_PRODUCER_MOVEMENT_SCALE", "50")), help="Magnitud de gyro/accel que equivale a movement=1.0 (fallback si --no-movement-auto)")
    parser.add_argument("--kick-threshold", type=float, default=float(env.get("EEG_PRODUCER_KICK_THRESHOLD", "0.6")), help="movement >= esto en 'operando' dispara la patada (fallback si --no-movement-auto)")
    parser.add_argument("--kick-refractory", type=float, default=float(env.get("EEG_PRODUCER_KICK_REFRACTORY", "5")), help="segundos minimos entre patadas auto-detectadas")
    parser.add_argument("--stream-timeout", type=float, default=float(env.get("EEG_PRODUCER_STREAM_TIMEOUT", "5")), help="segundos a esperar por cada stream LSL opcional (ACC/GYRO/PPG)")
    parser.add_argument("--debug", action="store_true", default=_env_bool("EEG_PRODUCER_DEBUG"), help="imprime movement/bpm en cada actualizacion, para calibrar --movement-scale")
    args = parser.parse_args()

    client = udp_client.SimpleUDPClient(args.host, args.port)
    print(f"Mandando datos EEG reales a {args.host}:{args.port} (OSC/UDP) a {args.rate}Hz")
    print(f"Direcciones OSC: {', '.join(OSC_ADDRESSES.values())}")

    print("Resolviendo stream EEG (asegurate que BlueMuse / muselsl stream ya esta corriendo)...")
    eeg_inlet = None
    for intento in range(6):  # ~60s reintentando antes de rendirse
        eeg_inlet = resolve_eeg_inlet(timeout=10)
        if eeg_inlet is not None:
            break
        print(f"[conexion] sin stream EEG todavia (intento {intento + 1}/6)...")
    if eeg_inlet is None:
        raise RuntimeError(
            "No se encontro stream EEG. En Windows: abre BlueMuse, conecta el Muse 2 y "
            "dale 'Start Streaming'. En Mac: corre 'muselsl stream --name Muse-XXXX' en otra terminal."
        )
    print(f"EEG conectado: {eeg_inlet.info().name()}")

    gyro_inlet = resolve_optional("GYRO", args.stream_timeout, "movement quedara en 0 (agrega '--gyro' a 'muselsl stream')")
    acc_inlet = None
    if gyro_inlet is None:
        acc_inlet = resolve_optional("ACC", args.stream_timeout, "movement quedara en 0 (agrega '--acc' a 'muselsl stream')")
    ppg_inlet = resolve_optional("PPG", args.stream_timeout, f"bpm quedara fijo en {args.baseline_bpm} (agrega '--ppg' a 'muselsl stream')")

    bands = BandPowerTracker(eeg_inlet, calib_settle_s=args.calib_settle)
    movement = MovementTracker(gyro_inlet or acc_inlet, scale=args.movement_scale, auto=args.movement_auto)
    bpm = BpmTracker(ppg_inlet, baseline_bpm=args.baseline_bpm) if ppg_inlet else None
    phase = MomentPhase(
        calibration_s=args.calibration,
        kick_threshold=args.kick_threshold,
        refractory_s=args.kick_refractory,
    )
    if args.skip_calibration:
        phase.skip_calibration()

    commands = start_stdin_reader()
    default_kick_threshold = args.kick_threshold
    print(f"Fase inicial: {phase.phase} (calibracion {args.calibration}s, se ignoran los primeros {args.calib_settle}s)")
    print("Comandos: kick | skip | reset | quit + Enter")

    tick_s = 1.0 / args.rate
    last_tick = time.time()
    last_logged_phase = None
    was_calibrating = phase.phase == MOMENT["CALIBRANDO"]
    stall_since = None

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
                        phase.set_kick_threshold(default_kick_threshold)
                        bands.reset()      # recalibra el baseline de las bandas
                        movement.reset()   # vuelve a medir el reposo de movimiento
                        last_logged_phase = None
                        was_calibrating = True
                        print("[manual] reiniciado -- recalibrando bandas y movimiento con la persona nueva")
                    elif cmd in ("quit", "exit"):
                        print("\nProductor detenido.")
                        return
                    else:
                        print(f"Comando no reconocido: '{cmd}' (usa kick | skip | reset | quit)")
            except queue.Empty:
                pass

            calibrating = phase.phase == MOMENT["CALIBRANDO"]
            frozen = phase.phase == MOMENT["MOVIMIENTO_ABRUPTO"]

            bands.poll(now, calibrating=calibrating, frozen=frozen)
            movement.poll(now, calibrating=calibrating)
            if bpm is not None:
                bpm.poll(now)

            # --- reconexion del stream EEG si se congelo ---
            if bands.stalled:
                if stall_since is None:
                    stall_since = now
                elif now - stall_since >= EEG_RECONNECT_AFTER_S:
                    print("[conexion] intentando reconectar el stream EEG...")
                    new_inlet = resolve_eeg_inlet(timeout=2.0)
                    if new_inlet is not None:
                        bands.replace_inlet(new_inlet)
                        print("[conexion] EEG reconectado")
                    stall_since = now  # backoff hasta el proximo intento
            else:
                stall_since = None

            current_phase = phase.step(dt, movement.instant)
            if current_phase != last_logged_phase:
                print(f"[fase] -> {current_phase}")
                last_logged_phase = current_phase

            # fin de la calibracion -> auto-calibrar el movimiento
            if was_calibrating and current_phase != MOMENT["CALIBRANDO"]:
                thr = movement.finalize_calibration()
                if thr is not None:
                    phase.set_kick_threshold(thr)
            was_calibrating = current_phase == MOMENT["CALIBRANDO"]

            client.send_message(OSC_ADDRESSES["delta"], bands.waves["delta"])
            client.send_message(OSC_ADDRESSES["theta"], bands.waves["theta"])
            client.send_message(OSC_ADDRESSES["beta"], bands.waves["beta"])
            client.send_message(OSC_ADDRESSES["alfa"], bands.waves["alfa"])
            client.send_message(OSC_ADDRESSES["gamma"], bands.waves["gamma"])
            client.send_message(OSC_ADDRESSES["bpm"], round(bpm.bpm if bpm else args.baseline_bpm))
            client.send_message(OSC_ADDRESSES["movement"], round(movement.value, 3))
            client.send_message(OSC_ADDRESSES["moment"], current_phase)

            if args.debug:
                health = "OK" if not bands.stalled else "SIN DATOS"
                chans = [bands.candidate_names[p] for p in bands._active_pos]
                print(f"movement={movement.value:.3f}/inst={movement.instant:.3f} "
                      f"(mag={movement._last_magnitude:.1f}) bpm={(bpm.bpm if bpm else args.baseline_bpm):.0f} "
                      f"eeg={health} canales={chans} tasa={bands.sample_rate_est:.0f}Hz")
    except KeyboardInterrupt:
        print("\nProductor detenido.")


if __name__ == "__main__":
    main()
