#!/usr/bin/env bash
# Instala las tareas programadas de betbot. Muestra lo que va a hacer y pide
# confirmacion antes de tocar tu crontab.
#
#   bash scripts/install-cron.sh              # deportes por defecto (epl nfl)
#   bash scripts/install-cron.sh epl nfl nba  # los que quieras

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAP="$REPO/scripts/betbot-cron.sh"
MARCA="# --- betbot ---"
FIN="# --- fin betbot ---"

DEPORTES=("$@")
[ ${#DEPORTES[@]} -eq 0 ] && DEPORTES=(epl nfl)

# Fuente de ingesta por deporte: la que tiene temporada en curso.
fuente_de() {
    case "$1" in
        nba) echo "--source hoopr" ;;
        epl|laliga|seriea|bundesliga|ligue1) echo "--source openfootball" ;;
        *) echo "" ;;
    esac
}

BLOQUE="$MARCA"
BLOQUE="$BLOQUE
# Generado por scripts/install-cron.sh el $(date '+%Y-%m-%d')."
BLOQUE="$BLOQUE
# Cuota de The Odds API: plan gratuito = 500 requests/mes.
# 'scan' gasta 1 credito por ejecucion y deporte. 'close' solo gasta cuando hay
# senales a punto de empezar, asi que la mayoria de sus disparos son gratis."

MINUTO=7
for d in "${DEPORTES[@]}"; do
    F="$(fuente_de "$d")"
    BLOQUE="$BLOQUE
# --- $d ---
# Escaneo 3 veces al dia (~90 creditos/mes por deporte)
$MINUTO 9,15,21 * * * $WRAP scan --sport $d
# Reingesta semanal de resultados (martes 6:00). Sin esto los ratings envejecen.
0 6 * * 2 $WRAP ingest --sport $d $F --from \$(date +\\%Y) --to \$(date +\\%Y) --force"
    MINUTO=$((MINUTO + 3))
done

BLOQUE="$BLOQUE

# --- comunes ---
# Captura de linea de cierre: NO ESPACIAR. Cada cierre perdido es una apuesta
# que jamas podras evaluar por CLV.
*/10 * * * * $WRAP close
# Reporte diario de CLV y ROI
0 9 * * * $WRAP report
# Diagnostico semanal (lunes): avisa si los datos envejecen o falta cobertura
0 8 * * 1 $WRAP doctor
$FIN"

echo "Se anadiran estas entradas a tu crontab:"
echo "----------------------------------------"
echo "$BLOQUE"
echo "----------------------------------------"
echo
echo "Deportes: ${DEPORTES[*]}"
echo "Presupuesto estimado: ~$(( ${#DEPORTES[@]} * 90 )) creditos/mes en escaneos,"
echo "mas los cierres (variable). Limite del plan gratuito: 500/mes."
echo
read -r -p "¿Instalar? [s/N] " RESP
case "$RESP" in
    s|S|si|SI|y|Y) ;;
    *) echo "Cancelado. No se toco nada."; exit 0 ;;
esac

ACTUAL="$(crontab -l 2>/dev/null || true)"
# Quita cualquier bloque betbot anterior para que reinstalar sea idempotente.
LIMPIO="$(printf '%s\n' "$ACTUAL" | sed "/^${MARCA}$/,/^${FIN}$/d")"

printf '%s\n%s\n' "$LIMPIO" "$BLOQUE" | sed '/^$/N;/^\n$/D' | crontab -
echo
echo "Instalado. Comprueba con:  crontab -l"
echo "Logs en:  $REPO/logs/"
echo
echo "macOS: si las tareas no se ejecutan, dale a cron acceso a disco en"
echo "Ajustes > Privacidad y seguridad > Acceso total al disco > + > /usr/sbin/cron"
