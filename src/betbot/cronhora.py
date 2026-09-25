"""Convierte un bloque de crontab escrito en hora de Ciudad de Mexico a la hora
local de la maquina donde se instala.

POR QUE HACE FALTA. Los horarios del cron estan pensados alrededor de los
kickoffs, en hora de CDMX: el cierre del domingo a las 10:30, antes de los
partidos de las 11:00. Cron usa la hora LOCAL de la maquina. En el Mac de
Mexico coinciden; en un servidor en UTC (lo normal), las 10:30 del crontab
serian las 4:30 de Mexico y el barrido de "cierre" llegaria seis horas antes.
Nada fallaria: simplemente se guardaria un precio que no es el de cierre, y el
CLV mediria otra cosa sin que nadie lo notara.

Al cruzar la medianoche cambia tambien el dia de la semana: las 18:00 del
domingo en CDMX son las 00:00 del LUNES en UTC. Una hora con dos valores que
caen en dias distintos ("8,20" + 6h = 14:00 y 02:00 del dia siguiente) se parte
en dos lineas.

Ciudad de Mexico no tiene horario de verano desde 2022 (UTC-6 fijo). Si la
maquina SI lo tiene, la conversion hecha hoy se desfasa una hora cuando cambie;
se avisa para que el servidor use UTC o se reinstale tras el cambio.

Uso:  python -m betbot.cronhora < bloque  > bloque_convertido
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

CDMX = ZoneInfo("America/Mexico_City")


def desfase_minutos(local_min: int | None = None) -> int:
    """Minutos que hay que SUMAR a una hora de CDMX para tener la hora local."""
    if local_min is None:
        local_min = time.localtime().tm_gmtoff // 60
    cdmx_min = int(datetime.now(CDMX).utcoffset().total_seconds() // 60)
    return local_min - cdmx_min


def tiene_horario_de_verano() -> bool:
    anio = time.localtime().tm_year
    ene = time.localtime(time.mktime((anio, 1, 15, 12, 0, 0, 0, 0, -1))).tm_gmtoff
    jul = time.localtime(time.mktime((anio, 7, 15, 12, 0, 0, 0, 0, -1))).tm_gmtoff
    return ene != jul


def _dias(campo: str) -> list[int] | None:
    """'0,1,4' o '1-5' -> lista de dias; '*' -> None."""
    if campo == "*":
        return None
    dias: list[int] = []
    for parte in campo.split(","):
        if "-" in parte:
            a, b = parte.split("-")
            dias.extend(range(int(a), int(b) + 1))
        else:
            dias.append(int(parte))
    return [d % 7 for d in dias]   # 7 tambien es domingo


def convertir_linea(linea: str, desfase: int) -> list[str]:
    """Una linea de crontab en hora CDMX -> una o varias en hora local."""
    s = linea.strip()
    if desfase == 0 or not s or s.startswith("#") or "=" in s.split(None, 1)[0]:
        return [linea]
    campos = s.split(None, 5)
    if len(campos) < 6:
        return [linea]
    minuto, horas, dom, mes, dow, comando = campos
    # Solo se tocan lineas con minuto y horas fijos; "*/10" o "*" se dejan.
    if not minuto.isdigit() or horas == "*" or dom != "*" or mes != "*":
        return [linea]
    try:
        lista_horas = [int(h) for h in horas.split(",")]
        dias = _dias(dow)
    except ValueError:
        return [linea]

    # (minuto_nuevo, desplazamiento_de_dia) -> horas nuevas
    grupos: dict[tuple[int, int], list[int]] = defaultdict(list)
    for h in lista_horas:
        total = h * 60 + int(minuto) + desfase
        salto = total // 1440          # -1, 0 o +1 dia
        resto = total % 1440
        grupos[(resto % 60, salto)].append(resto // 60)

    salida = []
    for (m, salto), hs in sorted(grupos.items(), key=lambda kv: (kv[0][1], kv[1][0])):
        if dias is None:
            campo_dias = "*"
        else:
            campo_dias = ",".join(str(d) for d in sorted({(d + salto) % 7 for d in dias}))
        campo_horas = ",".join(str(h) for h in sorted(hs))
        salida.append(f"{m} {campo_horas} * * {campo_dias} {comando}")
    return salida


def convertir_bloque(texto: str, desfase: int) -> str:
    lineas = []
    for linea in texto.splitlines():
        lineas.extend(convertir_linea(linea, desfase))
    return "\n".join(lineas)


def main() -> int:
    d = desfase_minutos()
    texto = sys.stdin.read()
    print(convertir_bloque(texto, d))
    zona = time.strftime("%Z")
    if d:
        signo = "+" if d > 0 else "-"
        print(f"Hora de esta maquina: {zona}. Horarios convertidos desde Ciudad de "
              f"Mexico ({signo}{abs(d) // 60}h{abs(d) % 60:02d}).", file=sys.stderr)
    else:
        # Decirlo tambien cuando no se convierte: si no, "no convirtio" y "no
        # hacia falta convertir" se ven exactamente igual en la salida.
        print(f"Hora de esta maquina: {zona}, igual que Ciudad de Mexico. "
              f"No hace falta convertir los horarios.", file=sys.stderr)
    if tiene_horario_de_verano():
        print("AVISO: esta maquina cambia de horario en verano/invierno. La conversion\n"
              "se hizo con la hora de HOY y se desfasara una hora con el cambio. En un\n"
              "servidor, lo mejor es usar UTC; si no, reinstala el cron tras el cambio.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
