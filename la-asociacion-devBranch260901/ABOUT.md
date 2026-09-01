# INTERVENCION A LA ASOCIACION

El objetivo del proyecto es hacer una intervención sonora a partir de las ondas cerebrales para brindar curiosidad y asombro a los visitantes de la obra.

## Sobre la Asociación
La Asociación es una obra mostrada en el Science Gallery en el Expedition del Tecnológico de Monterrey en Monterrey Nuevo Leon. Toda la obra gira entorno a la qué pasa en el cerebro de los artistas cuando trabajan juntos y todas las piezas están encaminadas a neurociencias y comportamiento. La pieza donde haremos la intervención es la cancha, que es una porteria de futbol que en el suelo tiene un cerebro que puede ser pateado por el púlico, que representa como el trabajo de un artista se puede definir en un solo momento que es al patear la pelota.

## Sobre la Propuesta

La propuesta es hacer una intervención sonora unica e irrepetible a partir de los datos EEG, ritmo cardico y movimiento usando un Muse 2, y diseñar toda la propuesta para su implementación

## Sobre el objetivo de la implementación del MUSE

El objetivo de este repositorio es hacer la conexión y una interfaz sencilla con Muse para el procesamiento de los datos y habilitar un webhook o similar un servicio que sirva de conexión con PureData que usan las personas de producción musical para ellos interpretar los datos y generar piezas musicales en vivo.

El archivo que se debe mandar debe tener los siguientes datos.
waves son valores de 0 a 1 normalizados de la presencia de esas ondas en cierto momento.
bpm es el ritmo cardiaco.
movement es un valor de 0 a 1 que es la cantidad de presencia movimiento por el giroscopio o en la señal.
moment es un campo para indicar si se encuentra calibrando, operando o si detecta que hubo un movimiento abrupto que pudo .significar que se pateó la pelota.

- waves
- - delta 
- - theta
- - beta
- - alfa 
- - gamma

- bpm
- movement 
- moment

Esta info les debe servir para conectarlo con el programa Pure Data.

Nuestra intervención tiene 3 momentos.
- calibración que es 1 minuto y durante el tiempo se estaran explicando sobre que es.
- presencia todo el momento antes de que patee la pelota.
- post patear la pelota hasta que se quite el dispositivo.
