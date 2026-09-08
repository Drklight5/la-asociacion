// Genera datos sinteticos de EEG/BPM/movimiento que imitan lo que mandaria
// un Muse 2 real, siguiendo el esquema descrito en ABOUT.md:
//   waves: { delta, theta, beta, alfa, gamma }  -> 0..1
//   bpm:   ritmo cardiaco (bpm)
//   movement: 0..1
//   moment: "calibrando" | "operando" | "movimiento_abrupto"
//   gyro:  { x, y, z }  -> grados/s crudos, como el stream GYRO del Muse
//   accel: { x, y, z }  -> g crudos, como el stream ACC del Muse (un eje ~1 = gravedad)

const BANDS = ["delta", "theta", "beta", "alfa", "gamma"];

// Que tan "nervioso"/rapido se mueve cada banda y que tanto persigue su
// objetivo aleatorio. Delta se mueve lento y suave, gamma es mas nerviosa,
// como en EEG real.
const BAND_PROFILE = {
  delta: { targetVolatility: 0.05, smoothing: 0.35, baseline: 0.55 },
  theta: { targetVolatility: 0.08, smoothing: 0.45, baseline: 0.45 },
  beta: { targetVolatility: 0.12, smoothing: 0.6, baseline: 0.4 },
  alfa: { targetVolatility: 0.08, smoothing: 0.5, baseline: 0.5 },
  gamma: { targetVolatility: 0.15, smoothing: 0.7, baseline: 0.3 },
};

const MOMENT = {
  CALIBRANDO: "calibrando",
  OPERANDO: "operando",
  MOVIMIENTO_ABRUPTO: "movimiento_abrupto",
};

function clamp(value, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value));
}

// Ruido gaussiano via Box-Muller, para que el random walk se sienta organico
// en vez de un diente de sierra uniforme.
function randNormal() {
  const u = 1 - Math.random();
  const v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

// ---------------------------------------------------------------------------
// Ejes CRUDOS de giroscopio y acelerometro (x/y/z cada uno), en el mismo rango
// que da un Muse real -- para viz/ (los cubos 3D) y para que el equipo de Pd
// los mapee a la musica. NO forma parte del contrato historico con Pd (que solo
// tiene la magnitud agregada en `movement`); son direcciones aditivas, ver
// oscSender.js. Cada eje oscila a su propio ritmo (fase distinta) y su amplitud
// sigue la energia de movimiento, asi que en calibracion casi no se mueven y en
// la patada los 3 ejes se disparan juntos, como una sacudida real.
// ---------------------------------------------------------------------------
class GyroAxis {
  // grados/segundo. Reposo ~0 (+- unos pocos de ruido), giro de cabeza
  // +-50..150, patada hasta ~+-260.
  constructor(freq, phase) {
    this.freq = freq;
    this.phase = phase;
    this.value = 0;
  }

  step(dt, t, energy) {
    const wobble = Math.sin(t * this.freq + this.phase) * 0.5 + randNormal() * 0.5;
    const target = wobble * (4 + energy * 260);
    this.value += (target - this.value) * clamp(dt * 6, 0, 1);
    return Number(this.value.toFixed(2));
  }
}

class AccelAxis {
  // g (1g ~ 9.81 m/s2). Un eje lleva la gravedad (~+-1), los otros ~0; encima
  // va la dinamica del movimiento. En la patada el eje puede irse a +-3.
  constructor(freq, phase, gravity) {
    this.freq = freq;
    this.phase = phase;
    this.gravity = gravity;
    this.value = gravity;
  }

  step(dt, t, energy) {
    const wobble = Math.sin(t * this.freq + this.phase) * 0.4 + randNormal() * 0.35;
    const target = this.gravity + wobble * (0.04 + energy * 2.2);
    this.value += (target - this.value) * clamp(dt * 6, 0, 1);
    return Number(this.value.toFixed(3));
  }
}

class Band {
  constructor(profile) {
    this.value = profile.baseline;
    this.target = profile.baseline;
    this.profile = profile;
  }

  step(dt, kickBoost = 0) {
    const { targetVolatility, smoothing } = this.profile;
    this.target = clamp(this.target + randNormal() * targetVolatility * dt);
    this.value += (this.target - this.value) * smoothing * dt;
    if (kickBoost > 0) {
      this.value = clamp(this.value + kickBoost * randNormal() * 0.5 + kickBoost * 0.3);
    }
    this.value = clamp(this.value);
    return this.value;
  }
}

export class EEGSimulator {
  /**
   * @param {object} opts
   * @param {number} opts.calibrationSeconds duracion de la fase de calibracion
   * @param {number} opts.baselinebpm bpm de reposo aproximado
   */
  constructor({ calibrationSeconds = 60, baselinebpm = 72 } = {}) {
    this.calibrationSeconds = calibrationSeconds;
    this.baselinebpm = baselinebpm;

    this.bands = Object.fromEntries(
      BANDS.map((name) => [name, new Band(BAND_PROFILE[name])])
    );

    this.bpm = baselinebpm;
    this.bpmTarget = baselinebpm;
    this.movement = 0.05;
    this.movementTarget = 0.05;

    this.gyro = {
      x: new GyroAxis(0.7, 0),
      y: new GyroAxis(0.5, 2.1),
      z: new GyroAxis(0.9, 4.4),
    };
    // gravedad repartida como si el Muse estuviera puesto derecho: casi todo
    // en un eje, un resto chico en los otros.
    this.accel = {
      x: new AccelAxis(0.6, 1.3, 0.06),
      y: new AccelAxis(0.8, 3.7, -0.12),
      z: new AccelAxis(0.4, 5.5, 0.98),
    };

    this.elapsed = 0; // segundos desde que arranco la fase actual
    this.totalElapsed = 0;
    this.phase = MOMENT.CALIBRANDO;

    // Ventana de "movimiento abrupto" (patada): se activa un instante y
    // decae. kickEnergy en 1 = pico del evento, decae exponencialmente.
    this.kickEnergy = 0;
    this.kickWindowSeconds = 1.5; // cuanto dura el moment "movimiento_abrupto"
    this.kickElapsed = 0;
    this.hasKicked = false;
  }

  // Fuerza el paso de calibracion -> presencia sin esperar el timer.
  skipCalibration() {
    if (this.phase === MOMENT.CALIBRANDO) {
      this._enterPhase(MOMENT.OPERANDO);
    }
  }

  // Simula la patada: dispara el evento de movimiento abrupto.
  kick() {
    if (this.phase === MOMENT.CALIBRANDO) return false;
    this.hasKicked = true;
    this.kickEnergy = 1;
    this.kickElapsed = 0;
    this._enterPhase(MOMENT.MOVIMIENTO_ABRUPTO);
    this.movementTarget = 1;
    this.bpmTarget = clamp(this.baselinebpm + 35 + randNormal() * 8, 40, 200);
    return true;
  }

  // Reinicia toda la simulacion (nueva persona con el dispositivo).
  reset() {
    this.hasKicked = false;
    this.kickEnergy = 0;
    this.movement = 0.05;
    this.movementTarget = 0.05;
    this.bpm = this.baselinebpm;
    this.bpmTarget = this.baselinebpm;
    for (const band of Object.values(this.bands)) {
      band.value = band.profile.baseline;
      band.target = band.profile.baseline;
    }
    for (const axis of Object.values(this.gyro)) axis.value = 0;
    for (const axis of Object.values(this.accel)) axis.value = axis.gravity;
    this._enterPhase(MOMENT.CALIBRANDO);
  }

  _enterPhase(phase) {
    this.phase = phase;
    this.elapsed = 0;
  }

  /**
   * Avanza la simulacion `dt` segundos y regresa el frame actual.
   */
  step(dt) {
    this.elapsed += dt;
    this.totalElapsed += dt;

    if (this.phase === MOMENT.CALIBRANDO && this.elapsed >= this.calibrationSeconds) {
      this._enterPhase(MOMENT.OPERANDO);
    }

    if (this.phase === MOMENT.MOVIMIENTO_ABRUPTO) {
      this.kickElapsed += dt;
      if (this.kickElapsed >= this.kickWindowSeconds) {
        // Despues del instante de la patada, seguimos en "post patada"
        // (sigue siendo presencia/operando hasta que se quite el dispositivo).
        this._enterPhase(MOMENT.OPERANDO);
      }
    }

    // Decaimiento del pico de energia de la patada (afecta bandas/bpm/movimiento).
    this.kickEnergy = Math.max(0, this.kickEnergy - dt / 2.5);

    // Movimiento: durante calibracion casi no hay; en operando hay chispas
    // pequeñas (la gente se mueve poco parada esperando); al patear se va a 1
    // y decae.
    if (this.phase === MOMENT.CALIBRANDO) {
      this.movementTarget = clamp(0.03 + Math.random() * 0.05);
    } else if (this.kickEnergy > 0.02) {
      this.movementTarget = clamp(this.kickEnergy);
    } else {
      this.movementTarget = clamp(0.05 + Math.random() * 0.15);
    }
    this.movement += (this.movementTarget - this.movement) * clamp(dt * 3, 0, 1);
    this.movement = clamp(this.movement);

    // Ejes crudos de giro y acelerometro: misma energia que empuja `movement`,
    // mas el pico de la patada, para que los 3 ejes se disparen juntos en
    // movimiento_abrupto.
    const motionEnergy = clamp(this.movement * 0.6 + this.kickEnergy * 0.6);
    const gyro = {
      x: this.gyro.x.step(dt, this.totalElapsed, motionEnergy),
      y: this.gyro.y.step(dt, this.totalElapsed, motionEnergy),
      z: this.gyro.z.step(dt, this.totalElapsed, motionEnergy),
    };
    const accel = {
      x: this.accel.x.step(dt, this.totalElapsed, motionEnergy),
      y: this.accel.y.step(dt, this.totalElapsed, motionEnergy),
      z: this.accel.z.step(dt, this.totalElapsed, motionEnergy),
    };

    // BPM: deriva lenta en reposo, sube con la patada y baja de vuelta.
    if (this.kickEnergy <= 0.02) {
      this.bpmTarget = clamp(
        this.baselinebpm + randNormal() * 3,
        this.baselinebpm - 10,
        this.baselinebpm + 15
      );
    }
    this.bpm += (this.bpmTarget - this.bpm) * clamp(dt * 0.8, 0, 1);

    // Bandas: la patada mete un "artefacto de movimiento" tipico de EEG real
    // (ruido de alta frecuencia en beta/gamma).
    const motionArtifact = this.kickEnergy;
    const waves = {};
    for (const name of BANDS) {
      const boost = (name === "beta" || name === "gamma") ? motionArtifact : motionArtifact * 0.3;
      waves[name] = this.bands[name].step(dt, boost);
    }

    return {
      waves,
      bpm: Math.round(this.bpm),
      movement: Number(this.movement.toFixed(3)),
      moment: this.phase,
      gyro,
      accel,
    };
  }
}

export { MOMENT };
