# Simulador EEG 

Esto genera datos falsos de EEG/BPM/movimiento (como los que va a mandar el Muse 2 real) y se los manda a Pure Data por la red. Sirve para probar el patch sin tener el dispositivo puesto.

## Requisitos (una sola vez)

1. **Git** — para bajar el proyecto. Si no lo tienes: https://git-scm.com/downloads
2. **Node.js** — para correr el simulador. Si no lo tienes: https://nodejs.org (elige la versión **LTS**)

Si no quieres instalar Git, también puedes bajar el proyecto como ZIP desde la página del repositorio (botón "Code" -> "Download ZIP") y descomprimirlo.

## Descargar el proyecto

```bash
git clone https://github.com/Drklight5/la-asociacion.git
```

## Correrlo

**Windows:** entra a la carpeta `la-asociacion/simulator` y haz doble clic en `iniciar.bat`.

**Mac:** entra a la carpeta `la-asociacion/simulator` y haz doble clic en `iniciar.command`. (Si macOS se queja de que es de un desarrollador no identificado: click derecho -> Abrir, y confirmar.)

Eso instala lo necesario la primera vez y arranca el simulador. Vas a ver algo como:

```
Enviando datos EEG simulados a 127.0.0.1:9000 (OSC/UDP) a 10Hz
Fase inicial: calibrando (calibracion 60s)
```

### Si prefieres usar la terminal en vez de doble clic

```bash
cd la-asociacion/simulator
npm install
npm start
```

## Mientras está corriendo

Escribe cualquiera de estos y presiona Enter:

| Comando | Qué hace |
|---|---|
| `kick` | Simula la patada (movimiento abrupto) — no hace falta esperar los 60s de calibración de nuevo, ni patear de verdad |
| `skip` | Salta directo la fase de calibración |
| `reset` | Reinicia todo desde el principio |
| `quit` | Lo apaga |

## Ver los datos llegando en Pure Data

1. Abre `pd/eeg_receiver_test.pd` (está en la carpeta principal del proyecto, no dentro de `simulator`) con Pure Data.
2. Con el simulador corriendo, deberías ver en la consola de Pd los valores de `delta`, `theta`, `beta`, `alfa`, `gamma`, `bpm`, `movement` y `moment` actualizándose solos.
3. Para su propio patch de producción: escuchen en `netreceive -u -b 9000` (mismo puerto que usa el simulador) y usen el patrón de `route` de `eeg_receiver_test.pd` como referencia — el detalle del formato de datos está en [README.md](README.md).

## Si algo no funciona

- **"No se encontro Node.js"** -> instala Node.js (link arriba) y vuelve a intentar.
- **No llega nada a Pd** -> confirma que el simulador y Pd corren en la **misma computadora** (o misma red), y que el puerto en Pd (`netreceive -u -b 9000`) coincide con el del simulador.
- **Quieren correr el simulador en una máquina y Pd en otra** -> eso necesita un paso extra de red (ver [DEPLOY.md](DEPLOY.md)), avísenle a Valeria.
