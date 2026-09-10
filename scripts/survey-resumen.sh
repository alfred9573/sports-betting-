#!/usr/bin/env bash
# Resume las mediciones acumuladas de survey. Se puede correr en cualquier
# momento, tambien mientras la serie sigue en marcha.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
elif [ -x .venv/Scripts/python.exe ]; then
    PY=".venv/Scripts/python.exe"
else
    echo "ERROR: no encuentro el entorno virtual." >&2
    echo "Corre primero:  bash scripts/setup.sh" >&2
    exit 1
fi

"$PY" - <<'PYEOF'
import csv
from pathlib import Path

f = Path("data/survey.csv")
if not f.exists():
    print("Aun no hay mediciones. Corre: bash scripts/survey-dia.sh nfl 12 30 --fondo")
    raise SystemExit

filas = list(csv.DictReader(f.open()))
if not filas:
    print("El fichero esta vacio.")
    raise SystemExit

op = [int(r["oportunidades"]) for r in filas]
sharp = [int(r["con_sharp"]) for r in filas]
libros = [int(r["libros"]) for r in filas]
mejores = [float(r["mejor_ev"]) for r in filas]

print(f"Mediciones acumuladas: {len(filas)}")
print(f"  primera: {filas[0]['momento']}")
print(f"  ultima:  {filas[-1]['momento']}")
print()
print(f"Con libro sharp cotizando:    {sum(1 for s in sharp if s > 0)}/{len(filas)}")
print(f"Con alguna oportunidad:       {sum(1 for x in op if x > 0)}/{len(filas)}")
print(f"Oportunidades por escaneo:    media {sum(op)/len(op):.1f}, maximo {max(op)}")
print(f"Casas vistas por escaneo:     media {sum(libros)/len(libros):.1f}")
if any(m > 0 for m in mejores):
    print(f"Mejor EV visto:               {max(mejores):+.2%}")
print()

con_op = sum(1 for x in op if x > 0) / len(op)
if not any(sharp):
    print("VEREDICTO: no has medido nada.")
    print("No aparece ningun libro sharp, asi que no hay referencia contra la")
    print("que comparar. Pon ODDS_REGIONS=eu en .env y repite.")
elif not any(op):
    print("VEREDICTO: cero oportunidades en todas las mediciones.")
    print("No hay nada que capturar. Pagar mas cuota no lo cambia.")
elif len(filas) < 8:
    print("VEREDICTO: aun es pronto.")
    print(f"Con {len(filas)} mediciones no se distingue senal de casualidad.")
    print("Acumula al menos 8-12 antes de concluir.")
elif con_op > 0.5:
    print("VEREDICTO: aparecen oportunidades de forma consistente.")
    print(f"En el {con_op:.0%} de las mediciones. Eso apunta a algo estructural")
    print("y no a ruido. Pagar por escanear mas seguido empieza a justificarse,")
    print("pero confirmalo con CLV en vivo antes de mover dinero real.")
else:
    print("VEREDICTO: oportunidades intermitentes.")
    print(f"Solo en el {con_op:.0%} de las mediciones. Puede ser ruido de")
    print("sincronizacion entre casas, o una ventana real pero muy estrecha.")
    print("Acumula mas mediciones antes de decidir.")
PYEOF
