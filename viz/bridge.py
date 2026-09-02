#!/usr/bin/env python3
"""
viz/bridge.py -- relay OSC + puente a WebSocket para la visual de proyeccion.

    productor / simulador                bridge.py                     Pure Data
    ---------------------   --OSC:9001-->  (reenvia el datagrama  --OSC:9000-->  (sin
                                            UDP TAL CUAL)                        cambios)
                                               |
                                               +--WebSocket:8765 (JSON)--> viz/index.html

Pure Data NO se entera de que existe este proceso: recibe exactamente los mismos
paquetes UDP (mismas direcciones OSC, mismos tipos, mismo puerto 9000). Lo unico
que cambia respecto a hoy es que el productor / simulador se lanza apuntando al
puerto de este relay (9001) en vez de directo a Pd (9000):

    (productor)   python muse_producer.py --port 9001
    (simulador)   npm start -- --port 9001

Uso:
    pip install -r requirements.txt
    python bridge.py                          # listen :9001 -> Pd 127.0.0.1:9000, ws :8765
    python bridge.py --record sesion.jsonl    # ademas graba cada evento para replay.py
    python bridge.py --no-forward             # NO reenvia a Pd (solo alimenta la visual)

Variables de entorno equivalentes: VIZ_LISTEN_PORT, VIZ_PD_HOST, VIZ_PD_PORT,
VIZ_WS_PORT.
"""

import argparse
import asyncio
import contextlib
import json
import os
import socket
import sys
import time

import websockets
from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_message import OscMessage

BANDS = ("delta", "theta", "beta", "alfa", "gamma")

# Estado actual que se le manda a la visual. Arranca neutro.
STATE = {
    "delta": 0.5, "theta": 0.5, "beta": 0.5, "alfa": 0.5, "gamma": 0.5,
    "bpm": 72.0,
    "movement": 0.0,
    "moment": "calibrando",
    "t": 0.0,          # epoch del ultimo dato OSC recibido
    "connected": False,  # True mientras llegue OSC del productor/simulador
}

CLIENTS: "set[websockets.WebSocketServerProtocol]" = set()


def _apply(address: str, value) -> None:
    """Vuelca un mensaje OSC en STATE. Direcciones segun el contrato del repo:
    /eeg/wave/<banda>, /eeg/bpm, /eeg/movement, /eeg/moment."""
    parts = address.strip("/").split("/")
    if parts[:2] == ["eeg", "wave"] and len(parts) == 3 and parts[2] in BANDS:
        STATE[parts[2]] = float(value)
    elif parts == ["eeg", "bpm"]:
        STATE["bpm"] = float(value)
    elif parts == ["eeg", "movement"]:
        STATE["movement"] = float(value)
    elif parts == ["eeg", "moment"]:
        STATE["moment"] = str(value)
    else:
        return
    STATE["t"] = time.time()
    STATE["connected"] = True


def _decode(data: bytes):
    """Devuelve (address, value) por cada mensaje del datagrama. El productor y
    el simulador mandan mensajes sueltos; se contemplan bundles por las dudas."""
    try:
        if OscBundle.dgram_is_bundle(data):
            bundle = OscBundle(data)
            for i in range(bundle.num_contents):
                content = bundle.content(i)
                if isinstance(content, OscMessage):
                    yield content.address, (content.params[0] if content.params else None)
        else:
            msg = OscMessage(data)
            yield msg.address, (msg.params[0] if msg.params else None)
    except Exception as exc:  # datagrama corrupto / no-OSC -> se ignora
        print(f"[bridge] datagrama no decodificable ({exc})")


class RelayProtocol(asyncio.DatagramProtocol):
    def __init__(self, forward_addr, forward_enabled, recorder):
        self.forward_addr = forward_addr
        self.forward_enabled = forward_enabled
        self.recorder = recorder
        self._fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._warned_forward = False

    def datagram_received(self, data: bytes, addr) -> None:
        # 1) reenviar CRUDO a Pd primero, para no meter latencia al audio
        if self.forward_enabled:
            try:
                self._fwd.sendto(data, self.forward_addr)
            except OSError as exc:
                if not self._warned_forward:
                    print(f"[bridge] no se pudo reenviar a Pd {self.forward_addr}: {exc}")
                    self._warned_forward = True

        # 2) decodificar para la visual (+ grabar si corresponde)
        for address, value in _decode(data):
            if value is None:
                continue
            _apply(address, value)
            if self.recorder is not None:
                self.recorder.write(json.dumps({"t": STATE["t"], "addr": address, "v": value}) + "\n")


async def ws_handler(ws) -> None:
    CLIENTS.add(ws)
    peer = getattr(ws, "remote_address", ("?", 0))
    print(f"[bridge] visual conectada ({peer[0]})  total={len(CLIENTS)}")
    try:
        await ws.send(json.dumps(STATE))
        async for _ in ws:  # la visual no manda nada; solo mantenemos abierto
            pass
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)
        print(f"[bridge] visual desconectada  total={len(CLIENTS)}")


async def broadcaster(hz: float) -> None:
    period = 1.0 / hz
    while True:
        await asyncio.sleep(period)
        if STATE["connected"] and time.time() - STATE["t"] > 3.0:
            STATE["connected"] = False
            print("[bridge] sin datos OSC hace 3s -- la visual pasa a demo")
        if not CLIENTS:
            continue
        msg = json.dumps(STATE)
        for ws in tuple(CLIENTS):  # snapshot: ws_handler puede sacar clientes en un await
            try:
                await ws.send(msg)
            except Exception:
                CLIENTS.discard(ws)


def _int_env(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def parse_args():
    ap = argparse.ArgumentParser(description="Relay OSC + puente WebSocket para la visual.")
    ap.add_argument("--listen-host", default="0.0.0.0",
                    help="interfaz donde el relay escucha OSC (default 0.0.0.0)")
    ap.add_argument("--listen-port", type=int, default=_int_env("VIZ_LISTEN_PORT", 9001),
                    help="puerto donde apuntas el productor/simulador (default 9001)")
    ap.add_argument("--pd-host", default=os.environ.get("VIZ_PD_HOST", "127.0.0.1"),
                    help="host de Pure Data (default 127.0.0.1)")
    ap.add_argument("--pd-port", type=int, default=_int_env("VIZ_PD_PORT", 9000),
                    help="puerto de Pure Data (default 9000)")
    ap.add_argument("--ws-host", default="0.0.0.0", help="interfaz del WebSocket (default 0.0.0.0)")
    ap.add_argument("--ws-port", type=int, default=_int_env("VIZ_WS_PORT", 8765),
                    help="puerto del WebSocket que abre index.html (default 8765)")
    ap.add_argument("--ws-hz", type=float, default=60.0,
                    help="frecuencia de envio del estado a la visual (default 60)")
    ap.add_argument("--forward", action=argparse.BooleanOptionalAction, default=True,
                    help="reenviar el OSC a Pd (default: si). --no-forward para solo la visual")
    ap.add_argument("--record", metavar="ARCHIVO.jsonl",
                    help="graba cada evento OSC (una linea JSON por evento) para replay.py")
    return ap.parse_args()


async def main() -> None:
    args = parse_args()
    recorder = open(args.record, "a", buffering=1, encoding="utf-8") if args.record else None

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: RelayProtocol((args.pd_host, args.pd_port), args.forward, recorder),
        local_addr=(args.listen_host, args.listen_port),
    )
    print(f"[bridge] OSC escuchando en {args.listen_host}:{args.listen_port}")
    if args.forward:
        print(f"[bridge] reenviando intacto a Pd -> {args.pd_host}:{args.pd_port}")
    else:
        print("[bridge] --no-forward: NO se reenvia a Pd")
    if recorder is not None:
        print(f"[bridge] grabando en {args.record}")
    print(f"[bridge] apunta el productor/simulador a  --port {args.listen_port}")

    try:
        async with websockets.serve(ws_handler, args.ws_host, args.ws_port):
            print(f"[bridge] WebSocket en ws://{args.ws_host}:{args.ws_port}  (abri viz/index.html)")
            await broadcaster(args.ws_hz)
    finally:
        transport.close()
        if recorder is not None:
            recorder.close()


if __name__ == "__main__":
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(line_buffering=True)  # logs al vuelo si la salida va a un archivo
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
    print("\n[bridge] detenido.")
