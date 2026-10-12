#!/usr/bin/env bash
# Actualiza a Cobi en el servidor: baja el código nuevo de GitHub, instala lo que haga falta y lo reinicia.
# Se usa así:  bash ~/cobi/servidor/actualizar.sh
set -e
cd "$(dirname "$0")/.."

echo "📥 Bajando el código nuevo de GitHub..."
git pull --ff-only

echo "📦 Instalando dependencias..."
venv/bin/pip install -q -r requirements.txt

echo "🔄 Reiniciando a Cobi..."
sudo systemctl restart cobi
sleep 5

# Mostramos cómo quedó: tiene que decir "active (running)" y "Cobi conectado como ..."
systemctl --no-pager --lines=8 status cobi
