@echo off
REM ============================================================
REM  La Asociacion  ^|  Arranque en 1 clic  ^|  Windows
REM
REM  Doble clic en este archivo. Levanta los 3 servicios:
REM    1. bridge  (viz\bridge.py)   -- relay OSC + WebSocket
REM    2. grafica (http.server)     -- http://localhost:8000
REM    3. productor (muse_producer) -- Muse 2 -> OSC   (esta ventana)
REM
REM  NO toca Pure Data. Abri el patch como siempre; escucha en 9000
REM  y recibe los datos a traves del bridge sin cambiar nada.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

REM ---- valores por defecto (se pisan en deploy\config.txt) ----
set "MUSE_NAME="
set "MUSE_STREAM_ARGS=--acc --gyro --ppg"
set "PRODUCER_ARGS="
set "ABRIR_NAVEGADOR=si"
set "BRIDGE_PORT=9001"
set "PD_PORT=9000"
set "WEB_PORT=8000"

if exist "deploy\config.txt" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("deploy\config.txt") do (
    if /i "%%A"=="MUSE_NAME"        set "MUSE_NAME=%%B"
    if /i "%%A"=="MUSE_STREAM_ARGS" set "MUSE_STREAM_ARGS=%%B"
    if /i "%%A"=="PRODUCER_ARGS"    set "PRODUCER_ARGS=%%B"
    if /i "%%A"=="ABRIR_NAVEGADOR"  set "ABRIR_NAVEGADOR=%%B"
    if /i "%%A"=="BRIDGE_PORT"      set "BRIDGE_PORT=%%B"
    if /i "%%A"=="PD_PORT"          set "PD_PORT=%%B"
    if /i "%%A"=="WEB_PORT"         set "WEB_PORT=%%B"
  )
)

REM ---- Python disponible? ----
where python >nul 2>nul
if errorlevel 1 goto no_python

REM ---- entorno virtual ----
if exist ".venv\Scripts\python.exe" goto have_venv
echo [setup] Creando entorno virtual (una sola vez, puede tardar)...
python -m venv .venv
if errorlevel 1 goto venv_failed

:have_venv
set "PY=.venv\Scripts\python.exe"
echo [setup] Verificando dependencias (primera vez tarda un poco)...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r producer\requirements.txt
if errorlevel 1 goto deps_failed
"%PY%" -m pip install --quiet -r viz\requirements.txt
if errorlevel 1 goto deps_failed

echo.
echo ============================================================
echo  ANTES DE SEGUIR  --  puente Bluetooth del Muse (BlueMuse):
echo    1. Prende el Muse 2 y ponetelo.
echo    2. Abri BlueMuse.
echo    3. Refresh List  -^>  selecciona tu Muse  -^>  Start Streaming.
echo       (para movimiento/BPM reales: habilita ACC/GYRO/PPG en BlueMuse)
echo.
echo  Cuando BlueMuse diga "Streaming", volve aca y presiona una tecla.
echo ============================================================
echo.
pause

REM ---- 1. bridge OSC + WebSocket (ventana minimizada aparte) ----
echo [run] Iniciando bridge OSC/WebSocket...
start "LA-ASOCIACION-BRIDGE" /min "%PY%" viz\bridge.py --listen-port %BRIDGE_PORT% --pd-host 127.0.0.1 --pd-port %PD_PORT%

REM ---- 2. servidor de la grafica (ventana minimizada aparte) ----
echo [run] Sirviendo la grafica en http://localhost:%WEB_PORT% ...
start "LA-ASOCIACION-WEB" /min "%PY%" -m http.server %WEB_PORT% --directory viz

REM ---- 3. abrir el navegador ----
timeout /t 2 /nobreak >nul
if /i "%ABRIR_NAVEGADOR%"=="si" start "" "http://localhost:%WEB_PORT%/"

echo.
echo ============================================================
echo  Todo arriba. En esta ventana corre el PRODUCTOR del Muse.
echo    kick  = marcar la patada        skip  = saltar calibracion
echo    reset = reiniciar con persona nueva     quit = salir
echo.
echo  Pure Data: abrilo aparte, escucha en el puerto %PD_PORT%.
echo  Cerrar esta ventana detiene todo.
echo ============================================================
echo.

REM ---- productor en primer plano (Ctrl+C o 'quit' para terminar) ----
"%PY%" producer\muse_producer.py --host 127.0.0.1 --port %BRIDGE_PORT% %PRODUCER_ARGS%

REM ---- al salir del productor, bajar los servicios de apoyo ----
echo.
echo [fin] Deteniendo bridge y servidor de la grafica...
taskkill /f /t /fi "WINDOWTITLE eq LA-ASOCIACION-BRIDGE*" >nul 2>nul
taskkill /f /t /fi "WINDOWTITLE eq LA-ASOCIACION-WEB*"    >nul 2>nul
echo [fin] Listo.
pause
exit /b 0

:no_python
echo.
echo [ERROR] No se encontro Python.
echo   Instalalo desde https://www.python.org/downloads/
echo   y en el instalador marca "Add python.exe to PATH".
echo.
pause
exit /b 1

:venv_failed
echo.
echo [ERROR] No se pudo crear el entorno virtual.
echo.
pause
exit /b 1

:deps_failed
echo.
echo [ERROR] Fallo la instalacion de dependencias. Revisa tu conexion a internet.
echo.
pause
exit /b 1
