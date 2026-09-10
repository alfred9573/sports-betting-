#!/usr/bin/env bash
# Quita las tareas de betbot del crontab, dejando intacto todo lo demas.
set -uo pipefail
MARCA="# --- betbot ---"
FIN="# --- fin betbot ---"

ACTUAL="$(crontab -l 2>/dev/null || true)"
if ! printf '%s\n' "$ACTUAL" | grep -qF "$MARCA"; then
    echo "No hay tareas de betbot instaladas."
    exit 0
fi

echo "Se quitaran estas entradas:"
printf '%s\n' "$ACTUAL" | sed -n "/^${MARCA}$/,/^${FIN}$/p"
read -r -p "¿Continuar? [s/N] " RESP
case "$RESP" in
    s|S|si|SI|y|Y) ;;
    *) echo "Cancelado."; exit 0 ;;
esac

printf '%s\n' "$ACTUAL" | sed "/^${MARCA}$/,/^${FIN}$/d" | crontab -
echo "Hecho. El resto de tu crontab no se ha tocado."
