@echo off
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo No se encontro Node.js instalado en esta computadora.
  echo Descargalo aqui: https://nodejs.org  ^(version LTS^) y vuelve a correr este archivo.
  echo.
  pause
  exit /b 1
)

if not exist node_modules (
  echo Instalando dependencias por primera vez, un momento...
  call npm install
  if errorlevel 1 (
    echo.
    echo Fallo la instalacion. Revisa el error de arriba.
    pause
    exit /b 1
  )
)

echo.
echo Iniciando simulador EEG...
echo   kick  = simular la patada
echo   skip  = saltar la calibracion
echo   reset = reiniciar desde el principio
echo   quit  = salir
echo.
call npm start

pause
