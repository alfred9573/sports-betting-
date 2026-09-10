#!/usr/bin/env bash
# Envoltorio para ejecutar betbot desde cron.
#
# POR QUE HACE FALTA UN ENVOLTORIO. Cron no arranca tu shell: no hay PATH, no
# hay variables de entorno, y el directorio de trabajo es tu home, no el repo.
# Un comando que funciona en tu terminal falla en cron por cualquiera de esas
# tres cosas, y falla en silencio salvo que redirijas la salida.
#
# Uso:  scripts/betbot-cron.sh <subcomando> [args...]
# Ej:   scripts/betbot-cron.sh scan --sport epl

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
elif [ -x .venv/Scripts/python.exe ]; then
    PY=".venv/Scripts/python.exe"
else
    echo "ERROR: no encuentro el entorno virtual. Corre: bash scripts/setup.sh" >&2
    exit 1
fi

mkdir -p logs
COMANDO="${1:-}"
[ -z "$COMANDO" ] && { echo "uso: $0 <subcomando> [args]" >&2; exit 2; }

LOG="logs/${COMANDO}.log"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

{
    echo "===== $TS  betbot $* ====="
    "$PY" -m betbot.cli "$@" 2>&1
    ESTADO=$?
    [ $ESTADO -ne 0 ] && echo "[salida con codigo $ESTADO]"
    echo
} >> "$LOG"

# Rotacion simple: si el log pasa de 5 MB, conserva la mitad reciente.
if [ -f "$LOG" ]; then
    TAMANO=$(wc -c < "$LOG" | tr -d ' ')
    if [ "$TAMANO" -gt 5242880 ]; then
        tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
fi
