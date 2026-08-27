# Desplegar el simulador en la nube (para pruebas remotas del equipo de producción)

## Por qué esto y no solo "subirlo a un servidor"

OSC va sobre UDP, pensado para red local. Si el simulador corre en un VPS público y Pure Data corre en la laptop del equipo de producción, esa laptop casi seguro está detrás del router de su estudio/casa sin puertos abiertos — un paquete UDP desde internet nunca va a llegar ahí, sin importar qué tan bien configurado esté el servidor.

La solución sin pelearse con el router: meter la VPS y la laptop de Pd en la misma red privada con **[Tailscale](https://tailscale.com)** (gratis para este tamaño de equipo, une hasta 100 dispositivos). Tailscale les da una IP privada estable (`100.x.x.x`) a cada dispositivo que se une, y el tráfico llega sin que nadie toque configuración de NAT/firewall.

Cuando llegue el Muse 2 real, lo más probable es que el productor de datos real corra físicamente en el lugar del evento (por el alcance de Bluetooth del dispositivo) — ahí ya no va a hacer falta nube ni Tailscale, todo será local. Este montaje es específicamente para que el equipo pueda probar **ahora**, sin ti presente.

## Qué NO cambia en Pure Data

El patch de Pd (`pd/eeg_receiver_test.pd` o el que usen en producción) siempre escucha en `netreceive -u -b <puerto>` en su propia máquina. Da igual si el remitente es el simulador en la nube o, después, el productor real corriendo local — Pd nunca necesita reconfigurarse. Lo único que cambia entre "simulación" y "real" es qué proceso corre y hacia dónde manda los paquetes, y eso vive del lado del que envía.

## Paso 1 — Tailscale en la laptop de Pd

En la máquina donde va a correr Pure Data:

1. Instalar Tailscale: https://tailscale.com/download (Windows/Mac, instalador normal, un clic).
2. Iniciar sesión con una cuenta (puede ser la tuya — invita al equipo de producción como miembro de la misma red/"tailnet" desde https://login.tailscale.com/admin/users, o comparte tu login si es un equipo chico).
3. Correr `tailscale up` (o queda conectado automáticamente tras iniciar sesión).
4. Anotar su IP de Tailscale: en la app de Tailscale aparece, o corriendo `tailscale ip -4` en una terminal. Se ve como `100.x.x.x` y **no cambia** mientras el dispositivo siga en la red.

## Paso 2 — Provisionar una VPS pequeña

Cualquier VPS Ubuntu de ~$4-6/mes sirve (DigitalOcean, Hetzner, Linode, Vultr), o el free tier de Oracle Cloud si quieres $0. Solo necesita:

- Ubuntu 22.04+ con acceso SSH.
- No necesita puertos abiertos entrantes para esto (el simulador solo *manda* UDP hacia afuera, hacia la IP de Tailscale de la laptop de Pd).

```bash
# en la VPS, recien provisionada
sudo apt update && sudo apt install -y nodejs npm git

# instalar y conectar Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up   # abre un link, inicia sesion con la MISMA cuenta/tailnet que la laptop de Pd
```

## Paso 3 — Subir y correr el simulador

```bash
sudo mkdir -p /opt/la-asociacion
sudo chown $USER /opt/la-asociacion
git clone <url-del-repo> /opt/la-asociacion   # o scp del contenido de la carpeta simulator/
cd /opt/la-asociacion/simulator
npm install

cp .env.example .env
nano .env   # EEG_SIM_HOST = IP de Tailscale de la laptop de Pd (paso 1), revisar el puerto
```

Probar antes de dejarlo como servicio:

```bash
node src/index.js   # deberia decir "Enviando datos EEG simulados a 100.x.x.x:9000..."
```

Con Pd abierto y `pd/eeg_receiver_test.pd` cargado en la laptop del equipo de producción, ya deberían ver los valores llegando en la consola de Pd.

## Paso 4 — Dejarlo corriendo siempre (systemd)

```bash
sudo useradd -r -s /usr/sbin/nologin eegsim
sudo chown -R eegsim /opt/la-asociacion
sudo cp /opt/la-asociacion/deploy/eeg-simulator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eeg-simulator
sudo systemctl status eeg-simulator   # confirma "active (running)"
journalctl -u eeg-simulator -f        # logs en vivo
```

Con `EEG_SIM_AUTO_LOOP=true` en el `.env` (ya viene así en `.env.example`), el simulador corre el ciclo completo solo — calibración, presencia, patada en un momento aleatorio, post-patada, y se reinicia — sin que nadie tenga que escribir comandos. Así el equipo de producción puede dejarlo prendido y probar su patch de Pd en cualquier momento.

## Cuando llegue el Muse 2 real

1. `sudo systemctl stop eeg-simulator` (o `disable` si ya no se va a volver a usar la simulación).
2. Correr el productor real (a escribir después, típicamente Python con `muselsl`/BrainFlow) en la máquina física donde esté el Muse 2 — probablemente la misma laptop/máquina del evento, mandando a `127.0.0.1` si Pd corre ahí también.
3. Pd sigue escuchando exactamente igual — no se toca el patch.
