#!/usr/bin/env bash
# Corre `survey` a intervalos durante un rato y acumula los resultados.
#
# El objetivo es distinguir una oportunidad ESTRUCTURAL de una casualidad: una
# sola medicion no dice nada porque las lineas desfasadas duran minutos.
#
#   bash scripts/survey-dia.sh nfl 12 30    # 12 mediciones cada 30 min
set -uo pipefail

DEPORTE="${1:-nfl}"
VECES="${2:-12}"
CADA_MIN="${3:-30}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
[ -x .venv/bin/python ] && PY=".venv/bin/python" || PY=".venv/Scripts/python.exe"

CREDITOS=$(( VECES * 3 ))
echo "Deporte: $DEPORTE | $VECES mediciones, una cada $CADA_MIN min"
echo "Coste estimado: ~$CREDITOS creditos de The Odds API"
echo "Duracion: ~$(( VECES * CADA_MIN / 60 )) horas"
echo
read -r -p "¿Empezar? [s/N] " R
case "$R" in s|S|si|SI|y|Y) ;; *) echo "Cancelado."; exit 0 ;; esac

mkdir -p data logs
for i in $(seq 1 "$VECES"); do
    echo "[$(date '+%H:%M:%S')] medicion $i/$VECES"
    "$PY" -m betbot.cli survey --sport "$DEPORTE" --log data/survey.csv \
        >> logs/survey.log 2>&1
    tail -3 logs/survey.log | grep -E "Historico|oportunidades" || true
    [ "$i" -lt "$VECES" ] && sleep $(( CADA_MIN * 60 ))
done

echo
echo "=== RESUMEN ==="
"$PY" - <<'PYEOF'
import csv
from pathlib import Path
f = Path("data/survey.csv")
if not f.exists():
    print("Sin datos.")
    raise SystemExit
filas = list(csv.DictReader(f.open()))
if not filas:
    print("Sin datos.")
    raise SystemExit
op = [int(r["oportunidades"]) for r in filas]
sharp = [int(r["con_sharp"]) for r in filas]
print(f"Mediciones: {len(filas)}")
print(f"Con libro sharp cotizando: {sum(1 for s in sharp if s > 0)}/{len(filas)}")
print(f"Con al menos una oportunidad: {sum(1 for x in op if x > 0)}/{len(filas)}")
print(f"Oportunidades por escaneo: media {sum(op)/len(op):.1f}, maximo {max(op)}")
print()
if not any(sharp):
    print("SIN LIBRO SHARP: la estrategia no se puede evaluar. Prueba")
    print("ODDS_REGIONS=eu en .env (es donde suele estar Pinnacle).")
elif not any(op):
    print("CERO oportunidades en todas las mediciones.")
    print("No hay nada que capturar y pagar mas cuota no lo cambia.")
elif sum(1 for x in op if x > 0) / len(op) > 0.5:
    print("Aparecen oportunidades en mas de la mitad de las mediciones.")
    print("Eso apunta a algo estructural: pagar por escanear mas seguido")
    print("empieza a tener sentido. Confirmalo con CLV en vivo antes de")
    print("mover dinero.")
else:
    print("Oportunidades intermitentes. Puede ser ruido de sincronizacion")
    print("entre casas o una ventana real pero estrecha. Acumula mas")
    print("mediciones antes de decidir.")
PYEOF
