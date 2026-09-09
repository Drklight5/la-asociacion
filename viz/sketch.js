/* ---------------------------------------------------------------------------
 * viz/sketch.js -- visual de proyeccion para "La Asociacion".
 *
 * Grafica de lineas en tiempo real de las 5 bandas EEG (delta/theta/alfa/beta/
 * gamma) + animaciones que acompanan: fondo que reacciona a la banda dominante,
 * campo de particulas segun activacion, pulso al ritmo del BPM, un "glitch"
 * corto cuando llega 'movimiento_abrupto' (la patada).
 *
 * Layout en dos zonas (constante SPLIT):
 *   - Panel izquierdo (~1/3): cubos 3D que rotan con el giroscopio (drawCubes),
 *     el anillo que late con el bpm (drawBeatRing) y el corazon blanco + bpm en
 *     una linea (drawHeart).
 *   - Zona derecha (~2/3): la grafica de ondas y sus animaciones (fondo,
 *     particulas, glow) + una fila "valor icono" por banda (drawWaveHud).
 *   - Arriba a la izquierda: el nombre de la banda predominante en blanco
 *     (drawDomLabel). El HUD (nombre + fila de bandas) se saca con la tecla 'l'.
 *
 * Datos: WebSocket del bridge (viz/bridge.py). Sin datos -> modo demo interno.
 *
 * Parametros de URL:
 *   ?ws=host:puerto   destino del WebSocket   (default localhost:8765)
 *   ?demo             arranca en modo demo
 *   ?lite             menos particulas / sin degradado / sin glow (maquinas justas)
 * ------------------------------------------------------------------------- */

const QS = new URLSearchParams(location.search);
const WS_URL = "ws://" + (QS.get("ws") || "localhost:8765");
const LITE = QS.has("lite");

// Paleta = la misma que el patch de Pd (chart-colors). Orden: freq baja -> alta.
// `icon` es la letra griega de la banda (simbolo estandar en EEG); se usa en
// el HUD de ondas en vez del nombre -- ver drawWaveHud().
const BANDS = [
  { key: "delta", label: "DELTA", icon: "δ", color: "#4ce519" },
  { key: "theta", label: "THETA", icon: "θ", color: "#9800f7" },
  { key: "alfa",  label: "ALFA",  icon: "α", color: "#0e0ef9" },
  { key: "beta",  label: "BETA",  icon: "β", color: "#dbdb1a" },
  { key: "gamma", label: "GAMMA", icon: "γ", color: "#ea415d" },
];

// --- historial (ring buffer) --------------------------------------------------
const SAMPLE_HZ = 20;               // resolucion del historial
let historySeconds = 20;            // ventana visible, ajustable con - / +
let HISTORY_LEN = SAMPLE_HZ * historySeconds;
const MAX_HISTORY_LEN = SAMPLE_HZ * 60;
const hist = {};                    // key -> Float32Array circular
let histHead = 0;
let histAccum = 0;

// --- estado -----------------------------------------------------------------
const target = {
  delta: .5, theta: .5, alfa: .5, beta: .5, gamma: .5, bpm: 72, movement: 0,
  gyro_x: 0, gyro_y: 0, gyro_z: 0, moment: "calibrando",
};
const shown  = Object.assign({}, target);

let sock = null;
let lastLiveRx = -1e9;              // millis() del ultimo frame con upstream vivo
let demo = QS.has("demo");
let showLabels = true;   // barra inferior + nombre de banda visible por defecto (tecla 'l' la saca)

let prevMoment = "calibrando";
let beatPhase = 0, beatFlash = 0;
let glitch = 0;
let bgTint = null;

// --- particulas -----------------------------------------------------------
const PARTICLE_N = LITE ? 45 : 150;
const P = [];

// --- demo -----------------------------------------------------------------
let demoT = 0, demoKickAt = 9 + Math.random() * 12, demoMoment = "calibrando";

// --- cubos 3D (giroscopio) --------------------------------------------------
// El gyro llega CRUDO en grados/s (lo que da el sensor / el simulador) y es
// VELOCIDAD angular, no orientacion -- se integra en el tiempo para dar una
// rotacion acumulada, igual que un giroscopio real. Proyeccion manual (sin
// WEBGL) para dibujar los cubos con las mismas primitivas 2D + blendMode(ADD)
// que el resto del sketch, sobre el mismo canvas.
let cubeRotX = 0.3, cubeRotY = 0.6, cubeRotZ = 0;
const GYRO_GAIN = 0.007;     // grados/s -> rad/s de giro del cubo (~100 deg/s ~ 0.7 rad/s)
const GYRO_DRIFT = 0.06;     // deriva lenta constante -- nunca queda del todo quieto

// --- layout: panel izquierdo (cubo + anillo + corazon) | zona derecha (ondas) --
const SPLIT = 0.34;          // el panel izquierdo ocupa esta fraccion del ancho
const FOCUS_Y = 0.44;        // fraccion de la altura: centro del cubo y del anillo, en el panel
const panelCX = () => width * SPLIT * 0.5;                 // centro X del panel izquierdo
const waveCX  = () => width * (SPLIT + (1 - SPLIT) * 0.5); // centro X de la zona de ondas

const CUBE_VERTS = [
  [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
  [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
];
const CUBE_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 0],
  [4, 5], [5, 6], [6, 7], [7, 4],
  [0, 4], [1, 5], [2, 6], [3, 7],
];
// 3 cubos superpuestos (mismo centro) -- el patron facetado "interpuesto".
// `revealAt` = a partir de que nivel de concentracion aparece cada uno:
//   conc bajo  -> solo el grande (nivel 1)
//   conc medio -> grande + mediano (nivel 2)
//   conc alto  -> los tres (nivel 3)
// Ademas el grosor y la opacidad de TODOS suben con la concentracion.
const CUBES = [
  { size: 1.00, off: [0, 0, 0],            dash: false, alpha: 1.00, revealAt: -1 },
  { size: 0.80, off: [0.55, 1.05, 0.30],   dash: true,  alpha: 0.70, revealAt: 0.35 },
  { size: 0.62, off: [-0.80, 0.35, 0.95],  dash: false, alpha: 0.50, revealAt: 0.62 },
];

function stepGyro(dt) {
  cubeRotX += shown.gyro_x * GYRO_GAIN * dt;
  cubeRotY += (shown.gyro_y * GYRO_GAIN + GYRO_DRIFT) * dt;
  cubeRotZ += shown.gyro_z * GYRO_GAIN * dt;
}

function rotatePoint(p, rx, ry, rz) {
  let [x, y, z] = p;
  let y1 = y * Math.cos(rx) - z * Math.sin(rx);
  let z1 = y * Math.sin(rx) + z * Math.cos(rx);
  let x2 = x * Math.cos(ry) + z1 * Math.sin(ry);
  let z2 = -x * Math.sin(ry) + z1 * Math.cos(ry);
  let x3 = x2 * Math.cos(rz) - y1 * Math.sin(rz);
  let y3 = x2 * Math.sin(rz) + y1 * Math.cos(rz);
  return [x3, y3, z2];
}

function projectPoint(p, cx, cy, scale) {
  const depth = 3.4;
  const f = depth / (depth + p[2]);
  return [cx + p[0] * scale * f, cy + p[1] * scale * f];
}

// concentracion 0..1: sobre todo beta (atencion focalizada), algo de gamma.
function concentration() {
  return constrain(shown.beta * 0.72 + shown.gamma * 0.28, 0, 1);
}

function drawCubes(dom) {
  const cx = panelCX(), cy = height * FOCUS_Y;   // panel izquierdo
  const baseScale = Math.min(width * 0.06, height * 0.11);
  // pulso: laten con el bpm (mismo beatPhase que drawBeatRing) + un salto en la patada
  const pulse = 1 + 0.08 * Math.max(0, Math.sin(beatPhase * Math.PI * 2)) * (0.5 + 0.5 * beatFlash) + 0.4 * glitch;

  const conc = concentration();
  const weight = 0.5 + 1.4 * conc;     // grosor de linea: fino en calma, grueso concentrado
  const gAlpha = 0.32 + 0.68 * conc;   // opacidad global

  const base = color(255);
  const tint = lerpColor(base, color(dom.color), 0.4);

  push();
  blendMode(ADD);
  noFill();
  for (const cu of CUBES) {
    // cada cubo aparece al cruzar su umbral de concentracion (con fade suave)
    const reveal = constrain((conc - cu.revealAt) / 0.16, 0, 1);
    if (reveal <= 0.02) continue;

    const rx = cubeRotX + cu.off[0];
    const ry = cubeRotY + cu.off[1];
    const rz = cubeRotZ + cu.off[2];
    const scale = baseScale * cu.size * pulse;
    const pts = CUBE_VERTS.map((v) => projectPoint(rotatePoint(v, rx, ry, rz), cx, cy, scale));

    strokeWeight(weight);
    stroke(red(tint), green(tint), blue(tint), 210 * cu.alpha * gAlpha * reveal);
    drawingContext.setLineDash(cu.dash ? [5, 5] : []);
    for (const [a, b] of CUBE_EDGES) line(pts[a][0], pts[a][1], pts[b][0], pts[b][1]);
  }
  drawingContext.setLineDash([]);
  blendMode(BLEND);
  pop();
}

// ---------------------------------------------------------------------------
function setup() {
  // fallback por si la pagina carga antes de tener tamano de ventana
  createCanvas(windowWidth || 1280, windowHeight || 720);
  pixelDensity(1);
  frameRate(60);
  colorMode(RGB, 255);
  textFont("ui-monospace, Menlo, Consolas, monospace");

  for (const b of BANDS) {
    const a = new Float32Array(MAX_HISTORY_LEN);
    a.fill(0.5);
    hist[b.key] = a;
  }
  for (let i = 0; i < PARTICLE_N; i++) P.push(newParticle());

  connect();
}

function windowResized() {
  if (windowWidth > 0) resizeCanvas(windowWidth, windowHeight);
}

// red de seguridad: si el evento de resize se perdio (p. ej. la pagina se
// mostro despues de cargar, o cambio la resolucion del proyector) reajusta.
function ensureCanvasSize() {
  if (windowWidth > 0 && (width !== windowWidth || height !== windowHeight)) {
    resizeCanvas(windowWidth, windowHeight);
  }
}

// --- WebSocket -----------------------------------------------------------
function connect() {
  try {
    sock = new WebSocket(WS_URL);
  } catch (e) {
    setTimeout(connect, 2000);
    return;
  }
  sock.onclose = () => setTimeout(connect, 2000);
  sock.onerror = () => { try { sock.close(); } catch (e) {} };
  sock.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.connected) lastLiveRx = millis();     // solo cuenta si el productor esta mandando
    for (const k of ["delta", "theta", "alfa", "beta", "gamma", "movement"]) {
      if (k in d) target[k] = constrain(+d[k], 0, 1);
    }
    for (const k of ["gyro_x", "gyro_y", "gyro_z"]) {   // crudo, grados/s
      if (k in d) target[k] = constrain(+d[k], -2000, 2000);
    }
    if ("bpm" in d) target.bpm = constrain(+d.bpm, 30, 220);
    if ("moment" in d && typeof d.moment === "string") target.moment = d.moment;
  };
}

// ---------------------------------------------------------------------------
function draw() {
  ensureCanvasSize();
  const dt = Math.min(deltaTime, 66) / 1000;
  const live = millis() - lastLiveRx < 4000;
  if (demo || !live) stepDemo(dt);

  // suavizado hacia target (independiente del framerate)
  const kFast = 1 - Math.pow(0.0015, dt);
  const kSlow = 1 - Math.pow(0.03, dt);
  for (const k of ["delta", "theta", "alfa", "beta", "gamma", "movement", "gyro_x", "gyro_y", "gyro_z"]) {
    shown[k] += (target[k] - shown[k]) * kFast;
  }
  shown.bpm += (target.bpm - shown.bpm) * kSlow;
  shown.moment = target.moment;

  if (shown.moment === "movimiento_abrupto" && prevMoment !== "movimiento_abrupto") triggerKick();
  prevMoment = shown.moment;

  // volcar al historial a ritmo fijo
  histAccum += dt;
  const step = 1 / SAMPLE_HZ;
  while (histAccum >= step) {
    histAccum -= step;
    for (const b of BANDS) hist[b.key][histHead] = shown[b.key];
    histHead = (histHead + 1) % MAX_HISTORY_LEN;
  }

  // beat
  beatPhase += dt * (shown.bpm / 60);
  if (beatPhase >= 1) { beatPhase -= 1; beatFlash = 1; }
  beatFlash = Math.max(0, beatFlash - dt * 3.2);
  glitch = Math.max(0, glitch - dt * 0.85);
  stepGyro(dt);

  const calibrating = shown.moment === "calibrando";
  const dom = dominantBand();

  drawBackground(dom, calibrating);
  drawParticles(dt, dom, calibrating);
  drawDivider();
  drawBeatRing(dom);

  push();
  if (glitch > 0.002) translate(random(-1, 1) * 16 * glitch, random(-1, 1) * 11 * glitch);
  drawBandLines(calibrating);
  drawCubes(dom);
  pop();

  if (glitch > 0.002) drawGlitchOverlay();
  if (calibrating) drawCalibrationOverlay();
  drawHeart();
  if (showLabels) { drawDomLabel(dom); drawWaveHud(dom); }
  if (!live) drawSourceTag();
}

// --- capas -----------------------------------------------------------------
function drawBackground(dom, calibrating) {
  background(5, 5, 10);
  const c = color(dom.color);
  if (!bgTint) bgTint = color(c);
  bgTint = lerpColor(bgTint, c, 0.02);
  if (LITE) return;

  const amp = calibrating ? 0.03 : 0.12;
  const gx = waveCX(), gy = height * 0.68;   // el glow, tenue, hacia la zona de ondas
  const g = drawingContext.createRadialGradient(gx, gy, 0, gx, gy, Math.max(width, height) * 0.9);
  g.addColorStop(0, `rgba(${red(bgTint) | 0},${green(bgTint) | 0},${blue(bgTint) | 0},${amp})`);
  g.addColorStop(1, "rgba(0,0,0,0)");
  drawingContext.fillStyle = g;
  drawingContext.fillRect(0, 0, width, height);
}

// divisor sutil entre el panel izquierdo y la zona de ondas
function drawDivider() {
  push();
  stroke(255, 14);
  strokeWeight(1);
  line(width * SPLIT, height * 0.08, width * SPLIT, height * 0.92);
  pop();
}

function drawBandLines(calibrating) {
  const m = Math.min(width, height) * 0.05;
  const x0 = width * SPLIT + m, x1 = width - m;   // solo la zona derecha
  const y0 = height * 0.15, y1 = height * 0.85;
  const H = y1 - y0;
  const n = HISTORY_LEN;
  const start = (histHead - n + MAX_HISTORY_LEN) % MAX_HISTORY_LEN;

  noFill();
  stroke(255, 16);
  strokeWeight(1);
  line(x0, y1 - 0.5 * H, x1, y1 - 0.5 * H);

  blendMode(ADD);
  for (const b of BANDS) {
    const arr = hist[b.key];
    const c = color(b.color);
    const r = red(c), g = green(c), bl = blue(c);
    const v = shown[b.key];

    // relleno MUY sutil bajo la curva: degradado vertical que se desvanece
    // hacia abajo, asi 5 capas encimadas no forman un recuadro plano.
    const dc = drawingContext;
    dc.save();
    dc.beginPath();
    dc.moveTo(x0, y1);
    for (let i = 0; i < n; i++) {
      dc.lineTo(x0 + (x1 - x0) * (i / (n - 1)), y1 - arr[(start + i) % MAX_HISTORY_LEN] * H);
    }
    dc.lineTo(x1, y1);
    dc.closePath();
    const fg = dc.createLinearGradient(0, y0, 0, y1);
    fg.addColorStop(0, `rgba(${r},${g},${bl},${calibrating ? 0.025 : 0.05})`);
    fg.addColorStop(0.55, `rgba(${r},${g},${bl},${calibrating ? 0.012 : 0.025})`);
    fg.addColorStop(1, `rgba(${r},${g},${bl},0)`);
    dc.fillStyle = fg;
    dc.fill();
    dc.restore();

    // glow + linea
    const passes = LITE ? [1] : [0, 1];
    const aBase = calibrating ? 55 : 205;
    noFill();
    for (const pass of passes) {
      stroke(r, g, bl, pass === 0 ? aBase * 0.22 : aBase);
      strokeWeight(pass === 0 ? 6 + 12 * v : 2.2);
      beginShape();
      for (let i = 0; i < n; i++) {
        const x = x0 + (x1 - x0) * (i / (n - 1));
        const y = y1 - arr[(start + i) % MAX_HISTORY_LEN] * H;
        curveVertex(x, y);
        if (i === 0) curveVertex(x, y);
        if (i === n - 1) curveVertex(x, y);
      }
      endShape();
    }

    // punta luminosa en el borde derecho (valor actual)
    const yTip = y1 - v * H;
    noStroke();
    fill(r, g, bl, 235);
    circle(x1, yTip, 6 + 11 * v);
    fill(255, 200 * v);
    circle(x1, yTip, 3);
  }
  blendMode(BLEND);
}

function drawParticles(dt, dom, calibrating) {
  const c = color(dom.color);
  const r = red(c), g = green(c), bl = blue(c);
  const arousal = (shown.beta + shown.gamma) * 0.5;
  const speed = calibrating ? 5 : 14 + 95 * arousal + 120 * glitch;

  const lx = width * SPLIT;   // las particulas viven en la zona de ondas
  blendMode(ADD);
  noStroke();
  for (const p of P) {
    p.a += (noise(p.x * 0.0016, p.y * 0.0016, frameCount * 0.003) - 0.5) * 0.5;
    p.x += Math.cos(p.a) * speed * p.z * dt;
    p.y += Math.sin(p.a) * speed * p.z * dt;
    if (p.x < lx - 20) p.x = width + 20; else if (p.x > width + 20) p.x = lx - 20;
    if (p.y < -20) p.y = height + 20; else if (p.y > height + 20) p.y = -20;
    const s = (calibrating ? 1.1 : 1.5 + 3.2 * arousal) * p.z;
    fill(r, g, bl, (calibrating ? 22 : 55) * p.z);
    circle(p.x, p.y, s);
  }
  blendMode(BLEND);
}

function drawBeatRing(dom) {
  // anillo que sale del centro del panel izquierdo en cada latido y se desvanece
  const decay = Math.max(1 - beatPhase * 1.7, 0);
  const a = decay * 30 + beatFlash * 42;
  if (a < 1) return;
  const c = color(dom.color);
  push();
  blendMode(ADD);
  noFill();
  stroke(red(c), green(c), blue(c), a);
  strokeWeight(2);
  const base = Math.min(width * SPLIT, height) * 0.42;
  circle(panelCX(), height * FOCUS_Y, base * (0.35 + 2.4 * beatPhase));   // mismo centro que el cubo
  noStroke();
  fill(255, 6 * beatFlash);
  rect(0, 0, width * SPLIT, height);   // flash sutil, solo en el panel
  blendMode(BLEND);
  pop();
}

function drawGlitchOverlay() {
  push();
  blendMode(ADD);
  noStroke();
  fill(255, 130 * glitch * glitch);
  rect(0, 0, width, height);
  fill(255, 26 * glitch);
  for (let i = 0; i < 7; i++) {
    const y = random(height);
    rect(0, y, width, random(1, 3));
  }
  blendMode(BLEND);
  pop();
}

function drawCalibrationOverlay() {
  const lx = width * SPLIT;
  push();
  blendMode(ADD);
  noStroke();
  const sweep = (frameCount % 200) / 200;
  fill(255, 9);
  rect(lx + sweep * (width - lx) - 70, 0, 140, height);   // sweep sobre la zona de ondas
  blendMode(BLEND);
  pop();

  fill(255, 210);
  textAlign(CENTER, TOP);
  textSize(Math.min(width, height) * 0.026);
  text("C A L I B R A N D O", waveCX(), height * 0.055);
}

// Nombre de la banda predominante -- arriba a la izquierda, en blanco.
function drawDomLabel(dom) {
  push();
  blendMode(ADD);
  fill(255, 235);
  textAlign(LEFT, TOP);
  textSize(Math.min(width, height) * 0.03);
  text(dom.label.split("").join(" "), width * 0.035, height * 0.045);
  pop();
}

// Corazon + bpm: en el panel izquierdo, debajo del cubo. Corazon blanco, sin
// pulso -- el numero del bpm en la misma linea.
function drawHeart() {
  const unit = Math.min(width, height);
  const cx = panelCX();
  const y = height * 0.9;   // misma altura que la fila de bandas
  const gap = unit * 0.012;
  push();
  blendMode(ADD);
  fill(255, 240);
  textAlign(RIGHT, CENTER);
  textSize(unit * 0.05);
  text("♥", cx - gap, y);
  textAlign(LEFT, CENTER);
  textSize(unit * 0.048);
  text(String(Math.round(shown.bpm)), cx + gap, y);
  pop();
}

// HUD de ondas (zona derecha): una fila con las 5 bandas, cada una como
// "valor icono" (valor a 2 decimales, monoespaciado -> se lee como lectura de
// instrumento). El icono va en el color de la banda, el valor en blanco.
function drawWaveHud(dom) {
  const unit = Math.min(width, height);
  const valSize = unit * 0.028;
  const iconSize = unit * 0.036;
  const y = height * 0.9;
  const n = BANDS.length;
  const step = width * (1 - SPLIT) / (n + 1);
  let x = width * SPLIT + step;

  push();
  blendMode(ADD);
  textAlign(CENTER, CENTER);
  for (const b of BANDS) {
    fill(200, 205, 210, 120);
    textAlign(RIGHT, CENTER);
    textSize(valSize);
    text(shown[b.key].toFixed(2), x - unit * 0.004, y);

    const c = color(b.color);
    fill(red(c), green(c), blue(c), 150);
    textAlign(LEFT, CENTER);
    textSize(iconSize);
    text(b.icon, x + unit * 0.008, y);

    x += step;
  }
  pop();
}

function drawSourceTag() {
  push();
  textAlign(CENTER, BOTTOM);
  textSize(12);
  fill(210, 110, 110, 190);
  text("sin señal del bridge — demo interno   (tecla d)", width / 2, height - 26);
  pop();
}

// --- helpers -----------------------------------------------------------------
function dominantBand() {
  let best = BANDS[0], bv = -1;
  for (const b of BANDS) {
    if (shown[b.key] > bv) { bv = shown[b.key]; best = b; }
  }
  return best;
}

function newParticle() {
  const w = width || 1280;
  return {
    x: w * SPLIT + Math.random() * w * (1 - SPLIT),   // solo en la zona de ondas
    y: Math.random() * (height || 720),
    z: 0.3 + Math.random() * 0.7,
    a: Math.random() * Math.PI * 2,
  };
}

function triggerKick() {
  glitch = 1;
  beatFlash = 1;
  for (let i = 0; i < Math.min(50, P.length); i++) P[i].a = Math.random() * Math.PI * 2;
}

function setHistorySeconds(s) {
  historySeconds = constrain(s, 6, 60);
  HISTORY_LEN = Math.min(SAMPLE_HZ * historySeconds, MAX_HISTORY_LEN);
}

// --- demo (random walk suave + patada periodica) --------------------------
function stepDemo(dt) {
  demoT += dt;
  for (let i = 0; i < BANDS.length; i++) {
    const nv = noise(i * 13.7 + frameCount * 0.006, i * 4.2);
    target[BANDS[i].key] = lerp(target[BANDS[i].key], nv, 0.045);
  }
  target.bpm = 71 + 8 * Math.sin(demoT * 0.2) + (noise(frameCount * 0.01) - 0.5) * 10;

  if (demoT < 6) {
    demoMoment = "calibrando";
  } else if (demoMoment === "movimiento_abrupto") {
    if (demoT - demoKickAt > 1.5) { demoMoment = "operando"; demoKickAt = demoT + 9 + Math.random() * 15; }
  } else {
    demoMoment = (demoT >= demoKickAt) ? "movimiento_abrupto" : "operando";
  }
  target.moment = demoMoment;

  if (demoMoment === "movimiento_abrupto") {
    target.movement = 0.92;
    target.gamma = 0.9;
    target.beta = 0.85;
  } else {
    target.movement = lerp(target.movement, 0.05 + 0.05 * noise(frameCount * 0.02), 0.1);
  }

  // gyro fake (grados/s) para que los cubos se muevan tambien en demo (sin bridge)
  const gEnergy = (0.15 + target.movement * 0.9) * 130;
  target.gyro_x = (noise(100 + frameCount * 0.01) - 0.5) * 2 * gEnergy;
  target.gyro_y = (noise(200 + frameCount * 0.013) - 0.5) * 2 * gEnergy;
  target.gyro_z = (noise(300 + frameCount * 0.008) - 0.5) * 2 * gEnergy;
}

// --- teclado -----------------------------------------------------------------
function keyPressed() {
  if (key === "d" || key === "D") demo = !demo;
  if (key === "l" || key === "L") showLabels = !showLabels;
  if (key === "f" || key === "F") fullscreen(!fullscreen());
  if (key === "-" || key === "_") setHistorySeconds(historySeconds + 4);   // mas segundos = mas "lento"
  if (key === "+" || key === "=") setHistorySeconds(historySeconds - 4);
}
