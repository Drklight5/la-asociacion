/* ---------------------------------------------------------------------------
 * viz/sketch.js -- visual de proyeccion para "La Asociacion".
 *
 * Grafica de lineas en tiempo real de las 5 bandas EEG (delta/theta/alfa/beta/
 * gamma) + animaciones que acompanan: fondo que reacciona a la banda dominante,
 * campo de particulas segun activacion, pulso al ritmo del BPM, y un "glitch"
 * corto cuando llega 'movimiento_abrupto' (la patada).
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
const BANDS = [
  { key: "delta", label: "DELTA", color: "#4ce519" },
  { key: "theta", label: "THETA", color: "#9800f7" },
  { key: "alfa",  label: "ALFA",  color: "#0e0ef9" },
  { key: "beta",  label: "BETA",  color: "#dbdb1a" },
  { key: "gamma", label: "GAMMA", color: "#ea415d" },
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
const target = { delta: .5, theta: .5, alfa: .5, beta: .5, gamma: .5, bpm: 72, movement: 0, moment: "calibrando" };
const shown  = Object.assign({}, target);

let sock = null;
let lastLiveRx = -1e9;              // millis() del ultimo frame con upstream vivo
let demo = QS.has("demo");
let showLabels = false;

let prevMoment = "calibrando";
let beatPhase = 0, beatFlash = 0;
let glitch = 0;
let bgTint = null;

// --- particulas -----------------------------------------------------------
const PARTICLE_N = LITE ? 45 : 150;
const P = [];

// --- demo -----------------------------------------------------------------
let demoT = 0, demoKickAt = 9 + Math.random() * 12, demoMoment = "calibrando";

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
  for (const k of ["delta", "theta", "alfa", "beta", "gamma", "movement"]) {
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

  const calibrating = shown.moment === "calibrando";
  const dom = dominantBand();

  drawBackground(dom, calibrating);
  drawParticles(dt, dom, calibrating);
  drawBeatRing(dom);

  push();
  if (glitch > 0.002) translate(random(-1, 1) * 16 * glitch, random(-1, 1) * 11 * glitch);
  drawBandLines(calibrating);
  pop();

  if (glitch > 0.002) drawGlitchOverlay();
  if (calibrating) drawCalibrationOverlay();
  if (showLabels) drawLabels(dom);
  if (!live) drawSourceTag();
}

// --- capas -----------------------------------------------------------------
function drawBackground(dom, calibrating) {
  background(5, 5, 10);
  const c = color(dom.color);
  if (!bgTint) bgTint = color(c);
  bgTint = lerpColor(bgTint, c, 0.02);
  if (LITE) return;

  const amp = calibrating ? 0.04 : 0.17;
  const g = drawingContext.createRadialGradient(
    width * 0.5, height * 0.62, 0,
    width * 0.5, height * 0.62, Math.max(width, height) * 0.8
  );
  g.addColorStop(0, `rgba(${red(bgTint) | 0},${green(bgTint) | 0},${blue(bgTint) | 0},${amp})`);
  g.addColorStop(1, "rgba(0,0,0,0)");
  drawingContext.fillStyle = g;
  drawingContext.fillRect(0, 0, width, height);
}

function drawBandLines(calibrating) {
  const m = Math.min(width, height) * 0.06;
  const x0 = m, x1 = width - m;
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

    // relleno tenue bajo la curva
    noStroke();
    fill(r, g, bl, calibrating ? 7 : 20);
    beginShape();
    vertex(x0, y1);
    for (let i = 0; i < n; i++) {
      const y = y1 - arr[(start + i) % MAX_HISTORY_LEN] * H;
      vertex(x0 + (x1 - x0) * (i / (n - 1)), y);
    }
    vertex(x1, y1);
    endShape(CLOSE);

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

  blendMode(ADD);
  noStroke();
  for (const p of P) {
    p.a += (noise(p.x * 0.0016, p.y * 0.0016, frameCount * 0.003) - 0.5) * 0.5;
    p.x += Math.cos(p.a) * speed * p.z * dt;
    p.y += Math.sin(p.a) * speed * p.z * dt;
    if (p.x < -20) p.x = width + 20; else if (p.x > width + 20) p.x = -20;
    if (p.y < -20) p.y = height + 20; else if (p.y > height + 20) p.y = -20;
    const s = (calibrating ? 1.1 : 1.5 + 3.2 * arousal) * p.z;
    fill(r, g, bl, (calibrating ? 22 : 55) * p.z);
    circle(p.x, p.y, s);
  }
  blendMode(BLEND);
}

function drawBeatRing(dom) {
  // anillo que sale del centro en cada latido y se desvanece
  const decay = Math.max(1 - beatPhase * 1.7, 0);
  const a = decay * 28 + beatFlash * 40;
  if (a < 1) return;
  const c = color(dom.color);
  push();
  blendMode(ADD);
  noFill();
  stroke(red(c), green(c), blue(c), a);
  strokeWeight(2);
  const base = Math.min(width, height);
  circle(width / 2, height * 0.62, base * (0.28 + 0.95 * beatPhase));
  noStroke();
  fill(255, 7 * beatFlash);
  rect(0, 0, width, height);
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
  push();
  blendMode(ADD);
  noStroke();
  const sweep = (frameCount % 200) / 200;
  fill(255, 9);
  rect(sweep * width - 70, 0, 140, height);
  blendMode(BLEND);
  pop();

  fill(255, 210);
  textAlign(CENTER, TOP);
  textSize(Math.min(width, height) * 0.026);
  text("C A L I B R A N D O", width / 2, height * 0.055);
}

function drawLabels(dom) {
  push();
  textAlign(LEFT, CENTER);
  const x = Math.min(width, height) * 0.06;
  let y = height * 0.15;
  const lh = Math.min(width, height) * 0.03;
  textSize(Math.min(width, height) * 0.017);
  for (const b of BANDS) {
    const c = color(b.color);
    fill(red(c), green(c), blue(c), 235);
    text(b.label.padEnd(6) + " " + shown[b.key].toFixed(2), x, y);
    y += lh;
  }
  textAlign(RIGHT, TOP);
  fill(255, 220);
  textSize(Math.min(width, height) * 0.05);
  text(dom.label, width * 0.94, height * 0.10);
  fill(255, 150);
  textSize(Math.min(width, height) * 0.02);
  text(Math.round(shown.bpm) + " bpm   mov " + shown.movement.toFixed(2), width * 0.94, height * 0.17);
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
  return {
    x: Math.random() * (width || 1280),
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
}

// --- teclado -----------------------------------------------------------------
function keyPressed() {
  if (key === "d" || key === "D") demo = !demo;
  if (key === "l" || key === "L") showLabels = !showLabels;
  if (key === "f" || key === "F") fullscreen(!fullscreen());
  if (key === "-" || key === "_") setHistorySeconds(historySeconds + 4);   // mas segundos = mas "lento"
  if (key === "+" || key === "=") setHistorySeconds(historySeconds - 4);
}
