"""
Diagnostico del Muse 2 para "La Asociacion" -- SOLO LECTURA.

No manda OSC, no toca Pd, no importa nada de producer/. Lee el mismo stream LSL
de EEG y muestra los numeros CRUDOS, sin normalizar, sin CLR y sin z-score, para
poder decidir con datos si el pipeline del producer esta midiendo cerebro o
artefactos.

Responde cuatro preguntas concretas:

  1. ?El alfa esta en los datos?  Protocolo ojos abiertos / ojos cerrados. Cerrar
     los ojos tiene que subir el alfa (8-13Hz) en TP9/TP10 -- es el efecto Berger,
     el hallazgo mas reproducible del EEG. Si no aparece, no hay que confiar en
     nada aguas abajo.

  2. ?Delta y theta siguen los parpadeos?  Cuenta parpadeos por condicion y los
     pone al lado de la potencia en delta/theta. AF7/AF8 estan arriba de los ojos:
     un parpadeo son 100-400uV a 0.5-3Hz, o sea justo en delta/theta.

  3. ?Que canal domina el promedio?  El producer promedia la potencia entre
     canales en escala LINEAL, asi que el electrodo de mayor amplitud se lleva el
     promedio (3x amplitud = 9x potencia). Aca se ve el reparto real por canal.

  4. ?En que unidades viene el stream?  Imprime offset de DC, desvio y
     pico-a-pico crudos. Los umbrales en uV del producer asumen que BlueMuse
     entrega uV; si entrega cuentas de ADC, no significan lo que dicen.

Ademas SIMULA el calculo del producer (promedio lineal entre canales -> log10 ->
restar el comun-modo) sobre estos mismos datos, para ver si la inversion que se
ve en Pd se reproduce y de donde sale.

Uso (con el venv del producer, que ya tiene las dependencias):

    producer/.venv/Scripts/python.exe diagnostico/eeg_check.py
    producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --guardar sesion.npz
    producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --cargar sesion.npz
    producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --monitor

Requiere el stream LSL ya corriendo (BlueMuse en Windows, `muselsl stream` en Mac),
igual que el producer. Se puede correr AL MISMO TIEMPO que el producer: LSL
permite varios consumidores del mismo stream.
"""

import argparse
import sys
import time

import numpy as np
from pylsl import StreamInlet, resolve_byprop
from scipy.signal import butter, filtfilt, find_peaks, welch

BANDS = {
    "delta": (1, 4),
    "theta": (4, 8),
    "alfa": (8, 13),
    "beta": (13, 30),
    "gamma": (30, 45),
}
STANDARD_EEG_CHANNELS = ["TP9", "AF7", "AF8", "TP10"]
FRONTALES = ("AF7", "AF8")      # arriba de los ojos -> donde se ven los parpadeos
POSTERIORES = ("TP9", "TP10")   # donde se ve mejor el alfa en un Muse

BLINK_MIN_UV = 60.0             # piso absoluto para llamar "parpadeo" a una excursion
BERGER_MIN_RATIO = 1.5          # subida minima de alfa (cerrados/abiertos) para dar OK


# ---------------------------------------------------------------------------
# Adquisicion
# ---------------------------------------------------------------------------
def leer_nombres(info):
    nombres = []
    ch = info.desc().child("channels").child("channel")
    for _ in range(info.channel_count()):
        nombres.append(ch.child_value("label"))
        ch = ch.next_sibling()
    return nombres


def conectar(timeout=10.0):
    print("Resolviendo stream EEG (BlueMuse / muselsl tiene que estar corriendo)...")
    streams = resolve_byprop("type", "EEG", timeout=timeout)
    if not streams:
        sys.exit("No se encontro stream EEG. Abri BlueMuse y dale 'Start Streaming'.")
    inlet = StreamInlet(streams[0], max_buflen=60)
    info = inlet.info()
    sf = info.nominal_srate() or 256.0
    todos = leer_nombres(info)
    idx = [todos.index(n) for n in STANDARD_EEG_CHANNELS if n in todos]
    if not idx:
        idx = list(range(len(todos)))
    nombres = [todos[i] for i in idx]
    print(f"EEG conectado: {info.name()}  {sf:.0f}Hz  canales={nombres}"
          f"  (descartados: {[n for n in todos if n not in nombres]})\n")
    return inlet, sf, idx, nombres


def grabar(inlet, idx, sf, segundos, etiqueta):
    """Graba `segundos` con cuenta regresiva. Devuelve (n, n_canales) en crudo."""
    muestras = []
    t0 = time.time()
    ultimo_dibujo = 0.0
    while True:
        transcurrido = time.time() - t0
        if transcurrido >= segundos:
            break
        chunk, _ = inlet.pull_chunk(timeout=0.2, max_samples=int(sf))
        if chunk:
            muestras.extend(chunk)
        if transcurrido - ultimo_dibujo > 0.25:
            hechos = int(20 * transcurrido / segundos)
            barra = "#" * hechos + "-" * (20 - hechos)
            print(f"\r  {etiqueta}  [{barra}]  {segundos - transcurrido:4.1f}s ",
                  end="", flush=True)
            ultimo_dibujo = transcurrido
    print(f"\r  {etiqueta}  [{'#' * 20}]  listo   ")
    if not muestras:
        sys.exit("No llegaron muestras del stream EEG.")
    arr = np.asarray(muestras, dtype=float)[:, idx]
    finitos = np.isfinite(arr).all(axis=1)
    descartadas = int((~finitos).sum())
    if descartadas:
        print(f"    [aviso] {descartadas} muestras no finitas (NaN/inf) descartadas "
              f"de {len(arr)} -- perdida de paquetes BLE")
    return arr[finitos]


# ---------------------------------------------------------------------------
# Procesamiento
# ---------------------------------------------------------------------------
def filtrar(x, sf, lo=0.5, hi=45.0):
    """Pasa-banda. Quita el DC y la deriva lenta (que es lo que infla el
    pico-a-pico crudo) y todo lo que este arriba de gamma."""
    ny = sf / 2.0
    b, a = butter(4, [lo / ny, min(hi, ny * 0.95) / ny], btype="band")
    return filtfilt(b, a, x, axis=0)


def potencias(x, sf):
    """Potencia absoluta por banda y POR CANAL, en uV^2/Hz. Sin normalizar."""
    nper = int(min(4 * sf, x.shape[0]))
    freqs, psd = welch(x, fs=sf, nperseg=nper, detrend="linear", axis=0)
    return {n: psd[(freqs >= lo) & (freqs < hi)].mean(axis=0)
            for n, (lo, hi) in BANDS.items()}


def contar_parpadeos(x_frontal, sf):
    """Excursiones grandes en un canal frontal filtrado 0.5-8Hz."""
    f = filtrar(x_frontal[:, None], sf, 0.5, 8.0)[:, 0]
    mad = float(np.median(np.abs(f - np.median(f)))) * 1.4826
    umbral = max(BLINK_MIN_UV, 5.0 * mad)
    picos, _ = find_peaks(np.abs(f), height=umbral, distance=int(0.2 * sf))
    return len(picos), umbral


def forma_producer(x, sf):
    """Replica EXACTA de lo que calcula el producer: promedio de la potencia entre
    canales en escala lineal -> log10 -> restar el comun-modo (CLR). Es la
    'forma' que despues el producer z-scorea contra el baseline."""
    bp = potencias(x, sf)
    lp = {n: float(np.log10(max(float(v.mean()), 1e-12))) for n, v in bp.items()}
    comun = sum(lp.values()) / len(lp)
    return {n: lp[n] - comun for n in BANDS}


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
def tabla(titulo, filas, cabecera):
    print(f"\n{titulo}")
    anchos = [max(len(str(cabecera[i])), max((len(str(f[i])) for f in filas), default=0))
              for i in range(len(cabecera))]
    linea = "  " + "  ".join(str(cabecera[i]).ljust(anchos[i]) for i in range(len(cabecera)))
    print(linea)
    print("  " + "  ".join("-" * a for a in anchos))
    for f in filas:
        print("  " + "  ".join(str(f[i]).ljust(anchos[i]) for i in range(len(cabecera))))


def reporte_crudo(nombres, x, sf):
    print("\n" + "=" * 72)
    print("1. SENAL CRUDA  (?en que unidades viene el stream?)")
    print("=" * 72)
    xf = filtrar(x, sf)
    filas = []
    for i, n in enumerate(nombres):
        filas.append([n, f"{x[:, i].mean():+9.1f}", f"{x[:, i].std():8.1f}",
                      f"{np.ptp(x[:, i]):9.1f}", f"{xf[:, i].std():8.1f}",
                      f"{np.ptp(xf[:, i]):9.1f}"])
    tabla("Por canal (unidades del stream, se asume uV):", filas,
          ["canal", "offset DC", "std cruda", "pp crudo", "std filt", "pp filt"])
    pp_crudo = float(np.ptp(x, axis=0).max())
    pp_filt = float(np.ptp(xf, axis=0).max())
    dc = float(np.abs(x.mean(axis=0)).max())
    print(f"\n  Offset de DC hasta {dc:.0f}. Pico-a-pico CRUDO {pp_crudo:.0f}, "
          f"FILTRADO {pp_filt:.0f} ({pp_crudo / max(pp_filt, 1e-9):.1f}x mas chico).")
    print("  -> Cualquier umbral de artefacto en uV tiene que medirse sobre la senal")
    print("     FILTRADA. Sobre la cruda, el offset y la deriva se comen el margen y")
    print("     el umbral rechaza TODAS las ventanas. (Es lo que rompio el intento")
    print("     anterior: 150uV sobre senal cruda = nada pasa nunca = lineas rectas.)")
    if dc > 300:
        print(f"\n  El offset de DC ({dc:.0f}) confirma que el stream NO viene centrado:")
        print("     hay que filtrar antes de medir amplitudes, no solo antes de Welch.")
    if pp_filt > 1000:
        print(f"\n  [ojo] pico-a-pico filtrado de {pp_filt:.0f} es altisimo para EEG. Si el")
        print("     stream viniera en cuentas de ADC y no en uV, todos los umbrales")
        print("     absolutos del producer estarian mal escalados.")


def reporte_bandas(nombres, abiertos, cerrados, sf):
    print("\n" + "=" * 72)
    print("2. POTENCIA POR BANDA Y POR CANAL  (uV^2/Hz, absoluta, sin normalizar)")
    print("=" * 72)
    pa = potencias(filtrar(abiertos, sf), sf)
    pc = potencias(filtrar(cerrados, sf), sf)
    for cond, p in (("OJOS ABIERTOS", pa), ("OJOS CERRADOS", pc)):
        filas = []
        for i, n in enumerate(nombres):
            filas.append([n] + [f"{p[b][i]:9.2f}" for b in BANDS])
        total = [f"{p[b].mean():9.2f}" for b in BANDS]
        filas.append(["PROMEDIO"] + total)
        tabla(cond + ":", filas, ["canal"] + list(BANDS))
    return pa, pc


def reporte_berger(nombres, pa, pc):
    print("\n" + "=" * 72)
    print("3. EFECTO BERGER  (cerrar los ojos TIENE que subir el alfa)")
    print("=" * 72)
    filas = []
    for i, n in enumerate(nombres):
        r = pc["alfa"][i] / max(pa["alfa"][i], 1e-12)
        marca = "  <-- posterior" if n in POSTERIORES else ""
        filas.append([n, f"{pa['alfa'][i]:9.2f}", f"{pc['alfa'][i]:9.2f}",
                      f"{r:6.2f}x", marca])
    tabla("Alfa (8-13Hz) por canal:", filas,
          ["canal", "abiertos", "cerrados", "ratio", ""])

    post = [i for i, n in enumerate(nombres) if n in POSTERIORES]
    if post:
        ra = float(np.mean([pc["alfa"][i] / max(pa["alfa"][i], 1e-12) for i in post]))
        print(f"\n  Ratio alfa en posteriores (TP9/TP10): {ra:.2f}x")
        if ra >= BERGER_MIN_RATIO:
            print("  -> OK: el alfa ESTA en los datos. El problema es de procesamiento,")
            print("     no de adquisicion. Vale la pena arreglar el pipeline.")
        else:
            print("  -> PROBLEMA: no aparece el efecto Berger. Antes de tocar el")
            print("     pipeline hay que revisar el contacto (pelo, presion, piel")
            print("     limpia) y repetir. Sin esto, nada aguas abajo es confiable.")
    return post


def reporte_parpadeos(nombres, abiertos, cerrados, sf, pa, pc, dur):
    print("\n" + "=" * 72)
    print("4. PARPADEOS vs DELTA/THETA  (?delta esta midiendo ojos o cerebro?)")
    print("=" * 72)
    filas = []
    for cond, datos, p in (("abiertos", abiertos, pa), ("cerrados", cerrados, pc)):
        tot = 0
        for i, n in enumerate(nombres):
            if n in FRONTALES:
                c, _ = contar_parpadeos(datos[:, i], sf)
                tot = max(tot, c)
        filas.append([cond, f"{tot:5d}", f"{60.0 * tot / dur:6.1f}",
                      f"{p['delta'].mean():9.2f}", f"{p['theta'].mean():9.2f}"])
    tabla("Por condicion:", filas,
          ["ojos", "parpad", "por min", "delta", "theta"])

    rd = pc["delta"].mean() / max(pa["delta"].mean(), 1e-12)
    rt = pc["theta"].mean() / max(pa["theta"].mean(), 1e-12)
    print(f"\n  delta cerrados/abiertos = {rd:.2f}x    theta = {rt:.2f}x")
    if rd < 0.7 or rt < 0.7:
        print("  -> CONFIRMADO: al cerrar los ojos delta/theta CAEN. Fisiologicamente")
        print("     deberian subir o quedarse igual. Lo que cae es el artefacto de")
        print("     parpadeo, que es lo que estaba definiendo esas dos bandas.")
    elif rd > 1.3 or rt > 1.3:
        print("  -> delta/theta suben al cerrar los ojos, que es lo fisiologicamente")
        print("     esperable. Los parpadeos no estarian dominando estas bandas.")


def reporte_reparto(nombres, pa):
    print("\n" + "=" * 72)
    print("5. REPARTO DEL PROMEDIO  (?que canal se lleva el promedio lineal?)")
    print("=" * 72)
    filas = []
    for i, n in enumerate(nombres):
        filas.append([n] + [f"{100 * pa[b][i] / max(pa[b].sum(), 1e-12):5.1f}%"
                            for b in BANDS])
    tabla("Aporte de cada canal al promedio del producer (ojos abiertos):",
          filas, ["canal"] + list(BANDS))
    dom = max(range(len(nombres)), key=lambda i: pa["alfa"][i])
    share = 100 * pa["alfa"][dom] / max(pa["alfa"].sum(), 1e-12)
    if share > 45:
        print(f"\n  -> {nombres[dom]} se lleva el {share:.0f}% del alfa del promedio.")
        print("     Promediar potencias en escala lineal deja que un solo electrodo")
        print("     decida por los cuatro. Conviene promediar z por canal.")


def reporte_simulacion(abiertos, cerrados, sf):
    print("\n" + "=" * 72)
    print("6. SIMULACION DEL PIPELINE ACTUAL  (?se reproduce lo que ves en Pd?)")
    print("=" * 72)
    fa = forma_producer(filtrar(abiertos, sf), sf)
    fc = forma_producer(filtrar(cerrados, sf), sf)
    filas = []
    for b in BANDS:
        d = fc[b] - fa[b]
        flecha = "SUBE" if d > 0.05 else ("BAJA" if d < -0.05 else "~igual")
        filas.append([b, f"{fa[b]:+7.3f}", f"{fc[b]:+7.3f}", f"{d:+7.3f}", flecha])
    tabla("'Forma' del producer (log10 tras restar el comun-modo), abiertos -> cerrados:",
          filas, ["banda", "abiertos", "cerrados", "cambio", ""])
    suma_a = sum(fa.values())
    print(f"\n  Suma de las 5 formas: {suma_a:+.6f} (es 0 por construccion).")
    print("  Por eso las bandas se anticorrelacionan: si delta y theta bajan, el")
    print("  comun-modo baja con ellas y beta/gamma SUBEN sin que haya cambiado")
    print("  nada en beta ni en gamma.")
    sube = [b for b in BANDS if fc[b] - fa[b] > 0.05]
    baja = [b for b in BANDS if fc[b] - fa[b] < -0.05]
    if sube or baja:
        print(f"\n  Con TUS datos, cerrar los ojos da:  suben {sube or ['(ninguna)']}"
              f"  /  bajan {baja or ['(ninguna)']}")


def serie_formas(x, sf, ventana=2.0, salto=0.25):
    """Corre el calculo del producer sobre ventanas deslizantes (mismos 2s / 0.25s
    que usa el producer) y devuelve la serie temporal de cada banda."""
    n, h = int(ventana * sf), int(salto * sf)
    xf = filtrar(x, sf)
    out = {b: [] for b in BANDS}
    for i in range(0, len(xf) - n + 1, h):
        f = forma_producer(xf[i:i + n], sf)
        for b in BANDS:
            out[b].append(f[b])
    return {b: np.asarray(v) for b, v in out.items()}


def reporte_bloque(nombres, x, sf):
    """Reporte para una captura continua (una tarea real, sin protocolo)."""
    reporte_crudo(nombres, x, sf)

    print("\n" + "=" * 72)
    print("2. POTENCIA POR BANDA Y POR CANAL  (uV^2/Hz, absoluta, sin normalizar)")
    print("=" * 72)
    p = potencias(filtrar(x, sf), sf)
    filas = [[n] + [f"{p[b][i]:9.2f}" for b in BANDS] for i, n in enumerate(nombres)]
    filas.append(["PROMEDIO"] + [f"{p[b].mean():9.2f}" for b in BANDS])
    tabla("Toda la captura:", filas, ["canal"] + list(BANDS))

    reporte_reparto(nombres, p)

    tot = 0
    for i, n in enumerate(nombres):
        if n in FRONTALES:
            c, _ = contar_parpadeos(x[:, i], sf)
            tot = max(tot, c)
    dur = len(x) / sf
    print(f"\n  Parpadeos detectados: {tot} en {dur:.0f}s = {60.0 * tot / dur:.1f}/min")

    print("\n" + "=" * 72)
    print("3. ANTICORRELACION ENTRE BANDAS  (?el balancin del CLR es real?)")
    print("=" * 72)
    s = serie_formas(x, sf)
    nombres_b = list(BANDS)
    M = np.corrcoef([s[b] for b in nombres_b])
    filas = [[nombres_b[i]] + [f"{M[i][j]:+6.2f}" for j in range(len(nombres_b))]
             for i in range(len(nombres_b))]
    tabla(f"Correlacion entre las 5 bandas del producer ({len(s['delta'])} ventanas):",
          filas, ["banda"] + nombres_b)
    media = float(np.mean([M[i][j] for i in range(5) for j in range(5) if i != j]))
    # La media global engana cuando UNA banda domina: puede dar ~0 mientras esa
    # banda anticorrelaciona -0.9 con las otras cuatro. Lo que importa es si
    # alguna banda empuja al resto, y cual es el par mas negativo.
    por_banda = {b: float(np.mean([M[i][j] for j in range(5) if j != i]))
                 for i, b in enumerate(nombres_b)}
    manda, val_manda = min(por_banda.items(), key=lambda t: t[1])
    pares = [(M[i][j], nombres_b[i], nombres_b[j])
             for i in range(5) for j in range(i + 1, 5)]
    peor, pa_, pb_ = min(pares)

    print(f"\n  Correlacion media global: {media:+.3f}  (poco informativa si una banda domina)")
    print("  Media de cada banda contra las otras cuatro:")
    for b, v in sorted(por_banda.items(), key=lambda t: t[1]):
        print(f"    {b:6s} {v:+.3f}")
    print(f"\n  Par mas anticorrelacionado: {pa_} vs {pb_} = {peor:+.3f}")
    print("  Si las 5 bandas fueran independientes todo esto deberia dar ~0.")
    print("  El piso teorico de la transformada CLR con 5 componentes es -0.250.")
    if val_manda < -0.30 or peor < -0.50:
        print(f"\n  -> BALANCIN CONFIRMADO. '{manda}' empuja al resto ({val_manda:+.3f} de")
        print(f"     media contra las otras cuatro). Cuando {manda} se mueve, las demas")
        print("     se mueven al reves por la normalizacion, no por fisiologia. Eso es")
        print("     exactamente lo que se ve como 'una banda arriba y el resto abajo'.")
    elif media < -0.15:
        print(f"\n  -> Anticorrelacion generalizada ({media:+.3f}): el balancin del CLR")
        print("     esta presente en todas las bandas por igual.")
    else:
        print(f"\n  -> No se ve anticorrelacion fuerte; el balancin no estaria")
        print("     dominando en esta captura.")

    print("\n  Rango de cada banda en la captura (log10 tras el comun-modo):")
    for b in nombres_b:
        v = s[b]
        print(f"    {b:6s} min={v.min():+6.3f}  media={v.mean():+6.3f}  "
              f"max={v.max():+6.3f}  desvio={v.std():.3f}")


# ---------------------------------------------------------------------------
def monitor(inlet, idx, sf, nombres):
    """Modo libre: tabla de potencia por banda y canal, refrescando."""
    print("Monitor continuo (Ctrl+C para salir). Potencia absoluta uV^2/Hz.\n")
    buf = []
    try:
        while True:
            chunk, _ = inlet.pull_chunk(timeout=0.5, max_samples=int(sf))
            if chunk:
                buf.extend(chunk)
            need = int(4 * sf)
            if len(buf) < need:
                continue
            arr = np.asarray(buf[-need:], dtype=float)[:, idx]
            buf = buf[-need:]
            if not np.isfinite(arr).all():
                arr = arr[np.isfinite(arr).all(axis=1)]
                if len(arr) < need // 2:
                    continue
            p = potencias(filtrar(arr, sf), sf)
            cab = "canal ".ljust(8) + "".join(b.rjust(10) for b in BANDS)
            print("\n" + cab)
            for i, n in enumerate(nombres):
                print(n.ljust(8) + "".join(f"{p[b][i]:10.2f}" for b in BANDS))
            print("PROM".ljust(8) + "".join(f"{p[b].mean():10.2f}" for b in BANDS))
    except KeyboardInterrupt:
        print("\nMonitor detenido.")


def main():
    ap = argparse.ArgumentParser(description="Diagnostico de EEG del Muse 2 (solo lectura).")
    ap.add_argument("--segundos", type=float, default=30.0, help="duracion de cada condicion")
    ap.add_argument("--asentar", type=float, default=10.0, help="segundos iniciales que se tiran")
    ap.add_argument("--guardar", metavar="ARCHIVO.npz", help="guarda el crudo para reanalizar")
    ap.add_argument("--cargar", metavar="ARCHIVO.npz", help="reanaliza una sesion guardada")
    ap.add_argument("--monitor", action="store_true", help="tabla continua, sin protocolo")
    ap.add_argument("--capturar", type=float, metavar="SEGUNDOS",
                    help="graba N segundos continuos SIN protocolo, para capturar una tarea "
                         "real (ej. el Stroop). Se analiza con --cargar o al terminar.")
    args = ap.parse_args()

    if args.cargar:
        d = np.load(args.cargar, allow_pickle=False)
        sf = float(d["sf"])
        nombres = [str(s) for s in d["nombres"]]
        print(f"Sesion cargada de {args.cargar}: {nombres} a {sf:.0f}Hz")
        if "bloque" in d:  # captura continua, sin protocolo
            return reporte_bloque(nombres, d["bloque"], sf)
        abiertos, cerrados = d["abiertos"], d["cerrados"]
    else:
        inlet, sf, idx, nombres = conectar()
        if args.monitor:
            return monitor(inlet, idx, sf, nombres)
        if args.capturar:
            print(f"Grabando {args.capturar:.0f}s continuos. Hace la tarea que quieras")
            print("analizar (el Stroop, mirar la pared, cerrar los ojos, lo que sea).\n")
            bloque = grabar(inlet, idx, sf, args.capturar, "grabando      ")
            if args.guardar:
                np.savez_compressed(args.guardar, bloque=bloque, sf=sf,
                                    nombres=np.array(nombres))
                print(f"\n  Crudo guardado en {args.guardar}")
            return reporte_bloque(nombres, bloque, sf)
        print("PROTOCOLO -- segui las instrucciones en voz alta o leelas antes de empezar.\n")
        print(f"  Fase 0 ({args.asentar:.0f}s): quieta, los electrodos se asientan. Se descarta.")
        print(f"  Fase 1 ({args.segundos:.0f}s): OJOS ABIERTOS, mirando un punto fijo, quieta.")
        print(f"  Fase 2 ({args.segundos:.0f}s): OJOS CERRADOS, relajada, sin dormirte.\n")
        input("Enter para empezar...")
        grabar(inlet, idx, sf, args.asentar, "asentando     ")
        print("\n  >>> OJOS ABIERTOS, mira un punto fijo <<<")
        abiertos = grabar(inlet, idx, sf, args.segundos, "ojos ABIERTOS ")
        print("\n  >>> CERRA LOS OJOS ahora <<<")
        time.sleep(1.0)
        cerrados = grabar(inlet, idx, sf, args.segundos, "ojos CERRADOS ")
        print("\n  >>> Podes abrir los ojos <<<")
        if args.guardar:
            np.savez_compressed(args.guardar, abiertos=abiertos, cerrados=cerrados,
                                sf=sf, nombres=np.array(nombres))
            print(f"\n  Crudo guardado en {args.guardar}")

    dur = len(abiertos) / sf
    reporte_crudo(nombres, np.vstack([abiertos, cerrados]), sf)
    pa, pc = reporte_bandas(nombres, abiertos, cerrados, sf)
    reporte_berger(nombres, pa, pc)
    reporte_parpadeos(nombres, abiertos, cerrados, sf, pa, pc, dur)
    reporte_reparto(nombres, pa)
    reporte_simulacion(abiertos, cerrados, sf)
    print("\n" + "=" * 72)
    print("Pasale esta salida completa a Claude para decidir los arreglos.")
    print("=" * 72)


if __name__ == "__main__":
    main()
