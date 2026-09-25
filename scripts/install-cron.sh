#!/usr/bin/env bash
# Instala las tareas programadas de betbot. Muestra lo que va a hacer y pide
# confirmacion antes de tocar tu crontab.
#
#   bash scripts/install-cron.sh              # deportes por defecto (epl nfl)
#   bash scripts/install-cron.sh epl nfl nba  # los que quieras
#   bash scripts/install-cron.sh --colecta nfl   # + archivado de lineas
#   bash scripts/install-cron.sh --solo-colecta nfl   # SOLO archivado (sin scan)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAP="$REPO/scripts/betbot-cron.sh"
MARCA="# --- betbot ---"
FIN="# --- fin betbot ---"

# CUANTAS REGIONES. La cuota de The Odds API se cobra como
# n_mercados * n_REGIONES, no por llamada. Con regions=us,eu cada barrido de 3
# mercados cuesta 6 creditos, no 3. Calcular el presupuesto ignorando este
# multiplicador es subestimarlo a la mitad, asi que se lee de tu .env.
REGIONES="us"
if [ -f "$REPO/.env" ]; then
    LINEA="$(grep -E '^ODDS_REGIONS=' "$REPO/.env" | tail -1 | cut -d= -f2- | tr -d '\"'"'"' ')"
    [ -n "$LINEA" ] && REGIONES="$LINEA"
fi
N_REG=$(printf '%s' "$REGIONES" | awk -F, '{print NF}')
[ "$N_REG" -lt 1 ] && N_REG=1

COLECTA=0
SOLO_COLECTA=0
DEPORTES=()
for arg in "$@"; do
    case "$arg" in
        --colecta) COLECTA=1 ;;
        --solo-colecta) SOLO_COLECTA=1 ;;
        *) DEPORTES+=("$arg") ;;
    esac
done
[ ${#DEPORTES[@]} -eq 0 ] && DEPORTES=(epl nfl)

# Horario de colecta por deporte. Devuelve lineas de crontab.
#
# POR QUE LA NFL TIENE HORARIO PROPIO. Para medir si un precio era bueno hace
# falta la linea de CIERRE, la de justo antes del kickoff: es la referencia mas
# eficiente del mercado. Dos barridos fijos (8:00 y 20:00) la pierden casi
# siempre: el domingo los partidos empiezan a las 11:00, 14:25 y 18:20 de
# Ciudad de Mexico, y el de las 20:00 llega cuando ya se estan jugando.
#
# Horas en hora de Ciudad de Mexico (UTC-6 todo el ano), que es la de tu Mac.
# Entre septiembre y el 1 de noviembre la costa este va 2 horas por delante;
# despues, 1. Los barridos estan puestos ANTES del kickoff en ambos periodos,
# asi que el cambio de horario de EE.UU. solo les da una hora mas de margen.
colecta_de() {
    local d="$1" m="$2"
    case "$d" in
        nfl)
            echo "# Linea de la semana (apertura y movimiento), cada manana."
            echo "$m 9 * * * $WRAP collect --sport nfl"
            echo "# Cierres del domingo: antes de las ventanas de 11:00, 14:25 y 18:20."
            echo "30 10 * * 0 $WRAP collect --sport nfl"
            echo "0 14 * * 0 $WRAP collect --sport nfl"
            echo "0 18 * * 0 $WRAP collect --sport nfl"
            echo "# Cierres de lunes y jueves por la noche (kickoff ~18:15)."
            echo "0 18 * * 1,4 $WRAP collect --sport nfl"
            echo "# Estadisticas de jugador: nflverse publica con dias de retraso,"
            echo "# asi que se refresca martes y viernes. No gasta creditos."
            echo "30 7 * * 2,5 $WRAP ingest-players --actual"
            ;;
        nba)
            # Partidos a las 19:00 y 22:00 de la costa este: 17:00 y 20:00 de
            # CDMX hasta el 1 de noviembre, 18:00 y 21:00 despues. Los barridos
            # van antes de ambas ventanas en los dos periodos.
            echo "# Linea del dia (apertura), cada manana."
            echo "$m 9 * * * $WRAP collect --sport nba"
            echo "# Cierres: antes de la tanda de las 19:00 ET y de la de las 22:00 ET."
            echo "45 16 * * * $WRAP collect --sport nba"
            echo "45 19 * * * $WRAP collect --sport nba"
            echo "# Estadisticas de jugador, cada manana. No gasta creditos."
            echo "40 7 * * * $WRAP ingest-players --sport nba --actual"
            ;;
        *)
            echo "$m 8,20 * * * $WRAP collect --sport $d"
            ;;
    esac
}

# Barridos al mes por deporte, para el presupuesto.
barridos_mes() {
    case "$1" in
        nfl) echo 52 ;;   # 30 diarios + 5 por semana de cierres x ~4.3
        nba) echo 90 ;;   # 3 diarios
        *)   echo 60 ;;
    esac
}

if [ "$SOLO_COLECTA" -eq 1 ]; then
    BLOQUE="$MARCA
# Generado por scripts/install-cron.sh --solo-colecta el $(date '+%Y-%m-%d').
# SOLO archiva cuotas y estadisticas. No escanea, no alerta, no apuesta."
    MINUTO=5
    COSTE=0
    for d in "${DEPORTES[@]}"; do
        BLOQUE="$BLOQUE
# --- $d ---
$(colecta_de "$d" "$MINUTO")"
        COSTE=$(( COSTE + $(barridos_mes "$d") * 3 * N_REG ))
        MINUTO=$((MINUTO + 3))
    done
    BLOQUE="$BLOQUE
$FIN"

    echo "Se anadiran estas entradas a tu crontab:"
    echo "----------------------------------------"
    echo "$BLOQUE"
    echo "----------------------------------------"
    echo
    echo "Regiones configuradas: $REGIONES ($N_REG) -- la cuota se multiplica por esto."
    echo "Presupuesto estimado: ~${COSTE} creditos al mes (plan gratuito: 500)."
    if [ "$COSTE" -gt 500 ]; then
        echo
        echo "OJO: NO CABE en el plan gratuito. A mitad de mes se agota la cuota y"
        echo "el archivo deja de crecer justo cuando mas partidos hay. Opciones:"
        echo "  - ODDS_REGIONS=us en el .env (la mitad de coste, pero pierdes las"
        echo "    casas europeas y los exchanges, la mejor referencia sin margen);"
        echo "  - instalar menos deportes;"
        echo "  - pasar a un plan de pago."
    fi
    echo
    echo "IMPORTANTE: cron solo corre si el Mac esta ENCENDIDO y DESPIERTO."
    echo "Con la tapa cerrada o en reposo, el barrido de esa hora se pierde y"
    echo "no se repite. Si tienes un servidor que no se apaga, es mejor sitio."
fi

if [ "$SOLO_COLECTA" -eq 0 ]; then

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
# Escaneo por modelo, 3 veces al dia (~90 creditos/mes por deporte)
$MINUTO 9,15,21 * * * $WRAP scan --sport $d
# Escaneo por desacuerdo entre casas, cada 2h. Cubre totales y handicap, que es
# donde esta estrategia es mas fuerte. Cuesta 3 mercados x $N_REG region(es) =
# $((3 * N_REG)) creditos por ejecucion, o sea ~$((360 * N_REG))/mes por deporte.
# Con el plan gratuito de 500, deja SOLO UN deporte y sube el intervalo.
$((MINUTO + 1)) */2 * * * $WRAP scan --sport $d --strategy lineshop
# Reingesta semanal de resultados (martes 6:00). Sin esto los ratings envejecen.
0 6 * * 2 $WRAP ingest --sport $d $F --from \$(date +\\%Y) --to \$(date +\\%Y) --force"
    if [ "$COLECTA" -eq 1 ]; then
        BLOQUE="$BLOQUE
# Archivado de lineas: NO apuesta ni alerta, solo guarda precios para poder
# backtestear mas adelante lo que hoy no tiene historico (props, line shopping).
# Dos barridos al dia x $((3 * N_REG)) creditos = ~$((180 * N_REG))/mes por
# deporte. Si no cabe en tu plan, baja a un solo barrido diario (quita el 20)
# o reduce ODDS_REGIONS a una sola region en el .env.
$((MINUTO + 2)) 8,20 * * * $WRAP collect --sport $d"
    fi
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
SOLO_MODELO=$(( ${#DEPORTES[@]} * 90 * N_REG ))
LINESHOP=$(( ${#DEPORTES[@]} * 360 * N_REG ))
TOTAL=$(( SOLO_MODELO + LINESHOP ))
echo "Regiones configuradas: $REGIONES ($N_REG) -- la cuota se multiplica por esto."
echo "Presupuesto estimado de creditos al mes:"
echo "  escaneo por modelo      ~${SOLO_MODELO}"
echo "  escaneo lineshop        ~${LINESHOP}"
COLECTA_COSTE=0
if [ "$COLECTA" -eq 1 ]; then
    COLECTA_COSTE=$(( ${#DEPORTES[@]} * 180 * N_REG ))
    echo "  archivado de lineas     ~${COLECTA_COSTE}"
fi
echo "  TOTAL                   ~$(( TOTAL + COLECTA_COSTE )) mas los cierres (variable)."
echo
echo "OJO: el plan gratuito son 500/mes. Con lineshop activo NO cabe."
echo "Opciones: quitar la linea de lineshop del crontab, subir su intervalo,"
echo "dejar un solo deporte, reducir ODDS_REGIONS a una sola region en el .env,"
echo "o pasar a un plan de pago. Para solo archivar: --solo-colecta."
fi
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
