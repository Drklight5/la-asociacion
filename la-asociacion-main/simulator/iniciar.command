#!/bin/bash
cd "$(dirname "$0")"

if ! command -v node >/dev/null 2>&1; then
  echo ""
  echo "No se encontro Node.js instalado en esta computadora."
  echo "Descargalo aqui: https://nodejs.org (version LTS) y vuelve a correr este archivo."
  echo ""
  read -p "Presiona Enter para salir..."
  exit 1
fi

if [ ! -d node_modules ]; then
  echo "Instalando dependencias por primera vez, un momento..."
  npm install || { echo "Fallo la instalacion. Revisa el error de arriba."; read -p "Presiona Enter para salir..."; exit 1; }
fi

echo ""
echo "Iniciando simulador EEG..."
echo "  kick  = simular la patada"
echo "  skip  = saltar la calibracion"
echo "  reset = reiniciar desde el principio"
echo "  quit  = salir"
echo ""
npm start

read -p "Presiona Enter para cerrar..."
