#!/bin/bash
# Copia diaria de la base de Cobi (clientes, solicitudes, pólizas, tareas). La corre cron todas las noches.
# Usa el "backup" de SQLite, así la copia sale bien aunque Cobi esté escribiendo en ese momento.
# Guarda las de los últimos 30 días en ~/respaldos/diarios y borra las más viejas.
set -e
CARPETA="$HOME/respaldos/diarios"
mkdir -p "$CARPETA"
DESTINO="$CARPETA/cobi-$(date +%Y-%m-%d).db"

python3 - "$HOME/cobi/cobi.db" "$DESTINO" <<'EOF'
import sqlite3, sys
origen, destino = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with destino:
    origen.backup(destino)
print("ok" if destino.execute("PRAGMA integrity_check").fetchone()[0] == "ok" else "LA COPIA SALIÓ MAL")
EOF

find "$CARPETA" -name "cobi-*.db" -mtime +30 -delete
