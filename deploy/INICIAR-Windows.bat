@echo off
REM ============================================================
REM  La Asociacion  ^|  Arranque en 1 clic  ^|  Windows
REM
REM  Doble clic en este archivo. Levanta TODA la obra:
REM    1. BlueMuse      -- puente Bluetooth del Muse + streaming   (BLUEMUSE_AUTO)
REM    2. bridge        -- viz\bridge.py, relay OSC + WebSocket
REM    3. grafica       -- http.server en http://localhost:8000
REM    4. Pure Data     -- abre el patch                 (PD_PATCH)
REM    5. Reaper        -- abre el proyecto (REAPER_PROJECT) + autoplay (AUTOPLAY)
REM    6. productor     -- muse_producer.py, Muse 2 -> OSC   (esta ventana)
REM
REM  Todo se configura en deploy\config.txt.
REM  Cerrar esta ventana detiene todo (incl. Pd y Reaper si CERRAR_TODO=si).
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

REM ---- Program Files (x86): a variable simple para no romper los bloques for ----
set "PF86=%ProgramFiles(x86)%"

REM ---- valores por defecto (se pisan en deploy\config.txt) ----
set "MUSE_NAME="
set "MUSE_STREAM_ARGS=--acc --gyro --ppg"
set "PRODUCER_ARGS="
set "ABRIR_NAVEGADOR=si"
set "BRIDGE_PORT=9001"
set "PD_PORT=9000"
set "WEB_PORT=8000"
set "PD_EXE="
set "REAPER_EXE="
set "PD_PATCH=pureDataPatch-v0.6/subpatches/1-Draft.pd"
set "REAPER_PROJECT=reaperProject.RPP"
set "BLUEMUSE_AUTO=si"
set "CERRAR_TODO=si"
set "AUTOPLAY="
set "AUTOPLAY_DELAY=15"

if exist "deploy\config.txt" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("deploy\config.txt") do (
    if /i "%%A"=="MUSE_NAME"        set "MUSE_NAME=%%B"
    if /i "%%A"=="MUSE_STREAM_ARGS" set "MUSE_STREAM_ARGS=%%B"
    if /i "%%A"=="PRODUCER_ARGS"    set "PRODUCER_ARGS=%%B"
    if /i "%%A"=="ABRIR_NAVEGADOR"  set "ABRIR_NAVEGADOR=%%B"
    if /i "%%A"=="BRIDGE_PORT"      set "BRIDGE_PORT=%%B"
    if /i "%%A"=="PD_PORT"          set "PD_PORT=%%B"
    if /i "%%A"=="WEB_PORT"         set "WEB_PORT=%%B"
    if /i "%%A"=="PD_EXE"           set "PD_EXE=%%B"
    if /i "%%A"=="REAPER_EXE"       set "REAPER_EXE=%%B"
    if /i "%%A"=="PD_PATCH"         set "PD_PATCH=%%B"
    if /i "%%A"=="REAPER_PROJECT"   set "REAPER_PROJECT=%%B"
    if /i "%%A"=="BLUEMUSE_AUTO"    set "BLUEMUSE_AUTO=%%B"
    if /i "%%A"=="CERRAR_TODO"      set "CERRAR_TODO=%%B"
    if /i "%%A"=="AUTOPLAY"         set "AUTOPLAY=%%B"
    if /i "%%A"=="AUTOPLAY_DELAY"   set "AUTOPLAY_DELAY=%%B"
  )
)

REM ---- rutas del config (admiten "/" ) a backslash de Windows ----
set "PD_PATCH_WIN=%PD_PATCH:/=\%"
set "REAPER_PROJECT_WIN=%REAPER_PROJECT:/=\%"
set "AUTOPLAY_WIN=%AUTOPLAY:/=\%"

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

REM ============================================================
REM  Resolver los ejecutables de Pure Data y Reaper
REM  (config con ruta inexistente -> se ignora y se autodetecta)
REM ============================================================
if defined PD_EXE     if not exist "%PD_EXE%"     set "PD_EXE="
if defined REAPER_EXE if not exist "%REAPER_EXE%" set "REAPER_EXE="

if not defined PD_EXE (
  for %%P in (
    "%ProgramFiles%\Pd\bin\pd.exe"
    "%PF86%\Pd\bin\pd.exe"
    "%ProgramFiles%\Purr Data\bin\pd.exe"
    "%PF86%\Purr Data\bin\pd.exe"
  ) do if not defined PD_EXE if exist "%%~P" set "PD_EXE=%%~P"
)
if not defined PD_EXE for /d %%D in ("%ProgramFiles%\Pd*") do if not defined PD_EXE if exist "%%~D\bin\pd.exe" set "PD_EXE=%%~D\bin\pd.exe"
if not defined PD_EXE for /f "delims=" %%P in ('where pd 2^>nul') do if not defined PD_EXE set "PD_EXE=%%P"

if not defined REAPER_EXE (
  for %%P in (
    "%ProgramFiles%\REAPER (x64)\reaper.exe"
    "%ProgramFiles%\REAPER\reaper.exe"
    "%PF86%\REAPER\reaper.exe"
  ) do if not defined REAPER_EXE if exist "%%~P" set "REAPER_EXE=%%~P"
)
if not defined REAPER_EXE for /f "delims=" %%P in ('where reaper 2^>nul') do if not defined REAPER_EXE set "REAPER_EXE=%%P"

REM ============================================================
REM  Muse: BlueMuse automatico, o recordatorio manual
REM ============================================================
if /i "%BLUEMUSE_AUTO%"=="si" goto bluemuse_auto

echo.
echo ============================================================
echo  Puente Bluetooth del Muse (BlueMuse) -- a mano:
echo    1. Prende el Muse 2 y ponetelo.
echo    2. Abri BlueMuse -^> Refresh List -^> tu Muse -^> Start Streaming.
echo       (habilita ACC/GYRO/PPG para movimiento y BPM reales)
echo  Cuando BlueMuse diga "Streaming", volve aca y presiona una tecla.
echo ============================================================
echo.
pause
goto muse_wait

:bluemuse_auto
echo.
echo [run] Abriendo BlueMuse y arrancando el streaming...
echo       Prende el Muse 2 y ponetelo ahora.
start "" "bluemuse://"
timeout /t 6 /nobreak >nul
start "" "bluemuse://startall"
timeout /t 3 /nobreak >nul
start "" "bluemuse://startall"

:muse_wait
echo [wait] Esperando el stream EEG del Muse (hasta 25 s)...
set "MUSE_OK="
for /l %%i in (1,1,25) do (
  if not defined MUSE_OK (
    "%PY%" -c "from pylsl import resolve_byprop; import sys; sys.exit(0 if resolve_byprop('type','EEG',timeout=1) else 1)" >nul 2>nul
    if not errorlevel 1 set "MUSE_OK=1"
  )
)
if not defined MUSE_OK echo [aviso] Todavia no aparece el stream del Muse. El productor va a reintentar solo -- revisa BlueMuse ("Streaming") y la bateria del Muse.

REM ============================================================
REM  Levantar los servicios
REM ============================================================
echo [run] Iniciando bridge OSC/WebSocket...
start "LA-ASOCIACION-BRIDGE" /min "%PY%" viz\bridge.py --listen-port %BRIDGE_PORT% --pd-host 127.0.0.1 --pd-port %PD_PORT%

echo [run] Sirviendo la grafica en http://localhost:%WEB_PORT% ...
start "LA-ASOCIACION-WEB" /min "%PY%" -m http.server %WEB_PORT% --directory viz

REM ---- Pure Data ----
if not defined PD_PATCH goto skip_pd
if "%PD_PATCH%"=="" goto skip_pd
if not defined PD_EXE (
  echo [aviso] No se encontro pd.exe. Pone la ruta en PD_EXE ^(deploy\config.txt^)
  echo         o abri el patch a mano:  %CD%\%PD_PATCH_WIN%
  goto skip_pd
)
if not exist "%CD%\%PD_PATCH_WIN%" (
  echo [aviso] No existe el patch: %PD_PATCH_WIN%  ^(revisa PD_PATCH en config.txt^)
  goto skip_pd
)
echo [run] Abriendo Pure Data: %PD_PATCH_WIN%
start "" "%PD_EXE%" "%CD%\%PD_PATCH_WIN%"
:skip_pd

REM ---- Reaper ----
if not defined REAPER_PROJECT goto skip_reaper
if "%REAPER_PROJECT%"=="" goto skip_reaper
if not defined REAPER_EXE (
  echo [aviso] No se encontro reaper.exe. Pone la ruta en REAPER_EXE ^(deploy\config.txt^)
  echo         o abri el proyecto a mano:  %CD%\%REAPER_PROJECT_WIN%
  goto skip_reaper
)
if not exist "%CD%\%REAPER_PROJECT_WIN%" (
  echo [aviso] No existe el proyecto: %REAPER_PROJECT_WIN%  ^(revisa REAPER_PROJECT^)
  goto skip_reaper
)
echo [run] Abriendo Reaper: %REAPER_PROJECT_WIN%
start "" "%REAPER_EXE%" "%CD%\%REAPER_PROJECT_WIN%"

REM ---- autoplay: correr el ReaScript en la instancia de Reaper ya abierta ----
if not defined AUTOPLAY goto skip_reaper
if "%AUTOPLAY%"=="" goto skip_reaper
if not exist "%CD%\%AUTOPLAY_WIN%" (
  echo [aviso] Autoplay: falta %AUTOPLAY_WIN% ^(lo sube tu companiero^) -- Reaper abre sin autoplay
  goto skip_reaper
)
echo [run] Autoplay de Reaper en %AUTOPLAY_DELAY%s: %AUTOPLAY_WIN%
start "LA-ASOCIACION-AUTOPLAY" /min "%COMSPEC%" /c "timeout /t %AUTOPLAY_DELAY% /nobreak >nul & "%REAPER_EXE%" -nonewinst "%CD%\%AUTOPLAY_WIN%""
:skip_reaper

REM ---- abrir el navegador ----
timeout /t 3 /nobreak >nul
if /i "%ABRIR_NAVEGADOR%"=="si" start "" "http://localhost:%WEB_PORT%/"

echo.
echo ============================================================
echo  Todo arriba. En esta ventana corre el PRODUCTOR del Muse.
echo    kick  = marcar la patada        skip  = saltar calibracion
echo    reset = reiniciar con persona nueva     quit = salir
echo.
echo  Pure Data escucha en el puerto %PD_PORT% (el DSP arranca solo).
echo  Cerrar esta ventana detiene todo.
echo ============================================================
echo.

REM ---- productor en primer plano (Ctrl+C o 'quit' para terminar) ----
"%PY%" producer\muse_producer.py --host 127.0.0.1 --port %BRIDGE_PORT% %PRODUCER_ARGS%

REM ============================================================
REM  Al salir del productor: bajar todo
REM ============================================================
echo.
echo [fin] Deteniendo bridge y servidor de la grafica...
taskkill /f /t /fi "WINDOWTITLE eq LA-ASOCIACION-BRIDGE*"   >nul 2>nul
taskkill /f /t /fi "WINDOWTITLE eq LA-ASOCIACION-WEB*"      >nul 2>nul
taskkill /f /t /fi "WINDOWTITLE eq LA-ASOCIACION-AUTOPLAY*" >nul 2>nul

if /i "%CERRAR_TODO%"=="si" (
  echo [fin] Cerrando Pure Data y Reaper...
  taskkill /f /im pd.exe     >nul 2>nul
  taskkill /f /im reaper.exe >nul 2>nul
  if /i "%BLUEMUSE_AUTO%"=="si" start "" "bluemuse://stopall"
)
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
