# diagnostico/ — chequeo del EEG, solo lectura

Herramienta para **ver los datos crudos del Muse** y decidir con evidencia si el
pipeline de `producer/` está midiendo cerebro o artefactos.

No manda OSC, no toca Pd, no importa nada de `producer/`. Solo lee el mismo
stream LSL. Se puede correr **al mismo tiempo** que el producer.

## Correr

Con el venv del producer, que ya tiene las dependencias:

```bash
producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --guardar sesion.npz
```

Protocolo guiado de ~70 s: 10 s de asentamiento, 30 s de ojos abiertos mirando un
punto fijo, 30 s de ojos cerrados. Al final imprime seis secciones.

Reanalizar una sesión ya grabada, sin volver a ponerse el Muse:

```bash
producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --cargar sesion.npz
```

Capturar **una tarea real** (el Stroop, mirar la pared, lo que sea) sin protocolo,
durante 120 s:

```bash
producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --capturar 120 --guardar stroop.npz
```

Ese modo da un reporte distinto: potencia por canal, parpadeos, y la **matriz de
correlación entre las 5 bandas** del pipeline actual, que muestra si el balancín
del CLR está operando en datos reales.

Tabla continua por canal, sin protocolo, para explorar libremente:

```bash
producer/.venv/Scripts/python.exe diagnostico/eeg_check.py --monitor
```

## Qué contesta

| Sección | Pregunta |
|---|---|
| 1. Señal cruda | ¿En qué unidades viene el stream? ¿Cuánto del pico-a-pico es offset y deriva en vez de señal? |
| 2. Potencia por banda y canal | Potencia absoluta en µV²/Hz, sin normalizar, sin CLR, sin z-score |
| 3. Efecto Berger | ¿Cerrar los ojos sube el alfa en TP9/TP10? **Es el test de aceptación** |
| 4. Parpadeos vs delta/theta | ¿Delta está midiendo ojos o cerebro? |
| 5. Reparto del promedio | ¿Qué canal se lleva el promedio lineal del producer? |
| 6. Simulación del pipeline | ¿Se reproduce con estos datos lo que se ve en Pd? |

## Cómo leerlo

**La sección 3 manda.** Si el ratio de alfa en TP9/TP10 al cerrar los ojos no
llega a ~1.5x, el problema es de adquisición (contacto, pelo, presión), no de
procesamiento — y no tiene sentido tocar el pipeline hasta resolverlo.

Si el Berger aparece pero el gráfico de Pd sigue raro, la sección 6 muestra qué
le hace el pipeline actual a esos mismos datos.

## Interpretación de referencia

| Condición | Qué debería pasar |
|---|---|
| Ojos cerrados | Alfa **sube** fuerte en TP9/TP10 (efecto Berger) |
| Somnolencia / N1 | Theta y delta **suben**, alfa cae |
| Carga cognitiva (Stroop) | Alfa **baja** respecto a mirar una pared |

Si en el gráfico de Pd ves lo contrario, comparalo con las secciones 2 y 4: casi
siempre la diferencia está en que delta/theta siguen la tasa de parpadeo y no la
actividad cerebral.
