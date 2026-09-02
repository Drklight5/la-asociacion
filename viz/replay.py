#!/usr/bin/env python3
"""
viz/replay.py -- reproduce una sesion grabada por  bridge.py --record  mandando
OSC con el timing original. Sirve para trabajar la visual sin Muse ni simulador.

    python replay.py sesion.jsonl                     # -> 127.0.0.1:9001 (el bridge)
    python replay.py sesion.jsonl --loop              # en bucle infinito
    python replay.py sesion.jsonl --port 9000         # directo a Pd
    python replay.py sesion.jsonl --speed 2           # al doble de velocidad

El formato es una linea JSON por evento: {"t": <epoch>, "addr": "/eeg/...", "v": <valor>}
"""

import argparse
import json
import time

from pythonosc.udp_client import SimpleUDPClient


def load(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["t"])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="archivo .jsonl grabado por bridge.py --record")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9001, help="destino OSC (default 9001 = el bridge)")
    ap.add_argument("--loop", action="store_true", help="repetir al terminar")
    ap.add_argument("--speed", type=float, default=1.0, help="multiplicador de velocidad (default 1.0)")
    args = ap.parse_args()

    rows = load(args.file)
    if not rows:
        raise SystemExit("archivo vacio o ilegible")

    client = SimpleUDPClient(args.host, args.port)
    span = rows[-1]["t"] - rows[0]["t"]
    print(f"[replay] {len(rows)} eventos / {span:.1f}s -> {args.host}:{args.port} "
          f"(speed {args.speed}x{', loop' if args.loop else ''})")

    while True:
        t0 = rows[0]["t"]
        start = time.perf_counter()
        for row in rows:
            target = (row["t"] - t0) / args.speed
            drift = target - (time.perf_counter() - start)
            if drift > 0:
                time.sleep(drift)
            client.send_message(row["addr"], row["v"])
        if not args.loop:
            break
        print("[replay] loop")

    print("[replay] fin")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[replay] detenido.")
