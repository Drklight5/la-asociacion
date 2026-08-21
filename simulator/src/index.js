import { parseArgs } from "node:util";
import readline from "node:readline";
import { EEGSimulator } from "./dataGenerator.js";
import { OscFrameSender, OSC_ADDRESSES } from "./oscSender.js";

// Los defaults se pueden fijar por variable de entorno (util para systemd/nube,
// donde no se le pasan flags al comando). Un flag explicito siempre gana.
const env = process.env;

const { values: args } = parseArgs({
  options: {
    host: { type: "string", default: env.EEG_SIM_HOST ?? "127.0.0.1" },
    port: { type: "string", default: env.EEG_SIM_PORT ?? "9000" },
    rate: { type: "string", default: env.EEG_SIM_RATE ?? "10" }, // mensajes por segundo
    calibration: { type: "string", default: env.EEG_SIM_CALIBRATION ?? "60" }, // segundos
    "baseline-bpm": { type: "string", default: env.EEG_SIM_BASELINE_bpm ?? "72" },
    // segundos dentro de "operando" antes de patear solo; si no se da, solo manual (o auto-loop decide)
    "auto-kick-after": { type: "string", default: env.EEG_SIM_AUTO_KICK_AFTER },
    "skip-calibration": { type: "boolean", default: env.EEG_SIM_SKIP_CALIBRATION === "true" },
    // ciclo infinito: calibracion -> presencia -> patada -> post-patada -> reset -> repite.
    // pensado para correr sin nadie enfrente (VPS/systemd) y que Pd siempre tenga senal.
    "auto-loop": { type: "boolean", default: env.EEG_SIM_AUTO_LOOP === "true" },
    "post-kick-seconds": { type: "string", default: env.EEG_SIM_POST_KICK_SECONDS ?? "20" },
    help: { type: "boolean", default: false },
  },
});

if (args.help) {
  console.log(`
Simulador de datos EEG/BPM/movimiento -> OSC -> Pure Data

Uso: node src/index.js [opciones]

  --host <ip>              Host de Pure Data (default 127.0.0.1)
  --port <n>                Puerto UDP de Pure Data (default 9000)
  --rate <hz>                Frecuencia de envio en mensajes/seg (default 10)
  --calibration <seg>        Duracion de la fase de calibracion (default 60)
  --baseline-bpm <n>         BPM de reposo aproximado (default 72)
  --auto-kick-after <seg>    Si se da, patea solo despues de N segundos en "operando"
  --skip-calibration         Arranca directo en fase "operando" (util para pruebas)
  --auto-loop                Repite el ciclo completo solo (para correr sin nadie enfrente, p.ej. en la nube)
  --post-kick-seconds <seg>  Con --auto-loop, cuanto dura la fase post-patada antes de reiniciar (default 20)
  --help                     Muestra esta ayuda

Todas las opciones tambien se pueden fijar por variable de entorno (EEG_SIM_HOST,
EEG_SIM_PORT, EEG_SIM_RATE, EEG_SIM_CALIBRATION, EEG_SIM_BASELINE_bpm,
EEG_SIM_AUTO_KICK_AFTER, EEG_SIM_SKIP_CALIBRATION, EEG_SIM_AUTO_LOOP,
EEG_SIM_POST_KICK_SECONDS) - util para systemd/.env en un servidor.

Mientras corre, escribe en la terminal + Enter:
  kick      -> simula la patada (movimiento abrupto)
  skip      -> salta la calibracion
  reset     -> reinicia la simulacion desde calibracion
  quit      -> termina
`);
  process.exit(0);
}

const host = args.host;
const port = Number(args.port);
const rateHz = Number(args.rate);
const calibrationSeconds = Number(args.calibration);
const baselinebpm = Number(args["baseline-bpm"]);
const autoKickAfter = args["auto-kick-after"] !== undefined ? Number(args["auto-kick-after"]) : null;
const autoLoop = args["auto-loop"];
const postKickSeconds = Number(args["post-kick-seconds"]);

// En auto-loop, si no se dio --auto-kick-after, se patea solo en un momento
// aleatorio de la presencia (15-45s) para que el ciclo no dependa de nadie.
const effectiveAutoKickAfter = autoLoop && autoKickAfter === null
  ? 15 + Math.random() * 30
  : autoKickAfter;

const sim = new EEGSimulator({ calibrationSeconds, baselinebpm });
const sender = new OscFrameSender(host, port);

if (args["skip-calibration"]) {
  sim.skipCalibration();
}

let autoKickTimer = null;
function armAutoKick() {
  if (effectiveAutoKickAfter === null) return;
  clearTimeout(autoKickTimer);
  autoKickTimer = setTimeout(() => {
    if (sim.phase === "operando") {
      console.log(`[auto-kick] disparando patada tras ${effectiveAutoKickAfter.toFixed(1)}s en presencia`);
      sim.kick();
    }
  }, effectiveAutoKickAfter * 1000);
}

let autoResetTimer = null;
function armAutoReset() {
  if (!autoLoop) return;
  clearTimeout(autoResetTimer);
  autoResetTimer = setTimeout(() => {
    console.log(`[auto-loop] reiniciando ciclo tras ${postKickSeconds}s post-patada`);
    clearTimeout(autoKickTimer);
    sim.reset();
    lastLoggedPhase = null;
  }, postKickSeconds * 1000);
}

const intervalMs = 1000 / rateHz;
let lastTick = Date.now();
let lastLoggedPhase = null;

const timer = setInterval(() => {
  const now = Date.now();
  const dt = (now - lastTick) / 1000;
  lastTick = now;

  const frame = sim.step(dt);
  sender.send(frame);

  if (frame.moment !== lastLoggedPhase) {
    console.log(`[fase] -> ${frame.moment}`);
    lastLoggedPhase = frame.moment;
    if (frame.moment === "operando" && !sim.hasKicked) {
      armAutoKick();
    } else if (frame.moment === "operando" && sim.hasKicked) {
      // post-patada: si esta en auto-loop, aqui arranca la cuenta para reiniciar
      armAutoReset();
    }
  }
}, intervalMs);

console.log(`Enviando datos EEG simulados a ${host}:${port} (OSC/UDP) a ${rateHz}Hz`);
console.log(`Direcciones OSC: ${Object.values(OSC_ADDRESSES).join(", ")}`);
console.log(`Fase inicial: ${sim.phase} (calibracion ${calibrationSeconds}s)`);
if (autoLoop) {
  console.log(`Auto-loop habilitado: ciclo completo sin intervencion (post-patada ${postKickSeconds}s antes de reiniciar)`);
} else if (autoKickAfter !== null) {
  console.log(`Auto-kick habilitado: ${autoKickAfter}s despues de entrar a "operando"`);
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const cmd = line.trim().toLowerCase();
  if (cmd === "kick") {
    const ok = sim.kick();
    console.log(ok ? "[manual] patada disparada" : "[manual] no se puede patear durante calibracion");
  } else if (cmd === "skip") {
    sim.skipCalibration();
    console.log("[manual] calibracion saltada");
  } else if (cmd === "reset") {
    clearTimeout(autoKickTimer);
    clearTimeout(autoResetTimer);
    sim.reset();
    lastLoggedPhase = null;
    console.log("[manual] simulacion reiniciada");
  } else if (cmd === "quit" || cmd === "exit") {
    shutdown();
  } else if (cmd) {
    console.log(`Comando no reconocido: "${cmd}" (usa kick | skip | reset | quit)`);
  }
});

function shutdown() {
  clearInterval(timer);
  clearTimeout(autoKickTimer);
  clearTimeout(autoResetTimer);
  sender.close();
  rl.close();
  console.log("\nSimulador detenido.");
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown); // systemd manda SIGTERM al hacer stop
