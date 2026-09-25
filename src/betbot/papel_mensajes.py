"""Mensajes de Telegram de las apuestas en papel.

Van a alguien que va a leerlos en el telefono, asi que: en espanol, con la
hora de Mexico, sin nombres internos de mercado, y con una advertencia que no
se puede pasar por alto. Son apuestas EN PAPEL: el objetivo es ver si el
modelo le gana a la casa antes de poner un peso. Un mensaje que pareciera un
pick invitaria justo a lo que este proceso existe para evitar.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

MX = ZoneInfo("America/Mexico_City")

AVISO = "PAPEL: no son picks, no apostar. Solo mide si el modelo le gana a la casa."

MERCADOS_ES = {
    "player_pass_yds": "yardas de pase",
    "player_pass_tds": "TD de pase",
    "player_rush_yds": "yardas por tierra",
    "player_reception_yds": "yardas por recepcion",
    "player_receptions": "recepciones",
    "player_anytime_td": "anota TD",
    "player_points": "puntos",
    "player_rebounds": "rebotes",
    "player_assists": "asistencias",
    "player_threes": "triples",
}
LADOS_ES = {"Over": "mas de", "Under": "menos de", "Yes": "si", "No": "no"}
DIAS = ("Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom")


def _apuesta(jugador: str, market: str, lado: str, point) -> str:
    mercado = MERCADOS_ES.get(market, market)
    lado_es = LADOS_ES.get(lado, lado)
    if point is None:
        return f"{jugador} {mercado}: {lado_es}"
    return f"{jugador} {mercado} {lado_es} {point:g}"


def _hora(momento) -> str:
    local = momento.astimezone(MX)
    return f"{DIAS[local.weekday()]} {local:%d/%m %H:%M}"


def mensaje_generacion(inf, deporte: str, resumen: dict) -> str:
    """Lo que se apunto en esta corrida, y por que se descarto el resto."""
    lineas = [f"BETBOT {deporte.upper()} - apuestas en papel", AVISO, ""]
    if inf.lineas == 0:
        lineas += [
            "No encontre lineas de props vigentes.",
            "Si esto pasa un sabado, el barrido de las 9:15 no guardo nada:",
            "revisa logs/collect.log en el servidor.",
        ]
        return "\n".join(lineas)

    lineas.append(f"Revise {inf.lineas:,} lineas de props. Nuevas en papel: {inf.apuntadas}.")
    if inf.nuevas:
        por_partido: dict[str, list] = {}
        for linea, p, ev in sorted(inf.nuevas, key=lambda x: (x[0].commence, x[0].partido)):
            clave = f"{_hora(linea.commence)} - {linea.partido}"
            por_partido.setdefault(clave, []).append((linea, p, ev))
        for partido, apuestas in por_partido.items():
            lineas += ["", partido]
            for linea, p, ev in apuestas:
                lineas.append(
                    f"- {_apuesta(linea.jugador, linea.market, linea.lado, linea.point)}"
                    f" @ {linea.precio:.2f} | modelo {p:.0%} | EV {ev:+.1%}"
                )
    else:
        lineas.append("Ninguna tenia valor suficiente. Es un resultado normal.")

    descartes = sorted(inf.descartes.items(), key=lambda kv: -kv[1])[:4]
    if descartes or inf.sin_valor:
        partes = [f"{inf.sin_valor:,} sin valor"] + [f"{n} {m}" for m, n in descartes]
        lineas += ["", "Descartadas: " + "; ".join(partes)]
    lineas += ["", _acumulado(resumen)]
    return "\n".join(lineas)


def mensaje_calificacion(detalle: list, deporte: str, resumen: dict) -> str:
    """Resultado de cada apuesta recien calificada, y el acumulado."""
    # Mismo peso visual para todas: resaltar las ganadas invita a mirar solo esas.
    iconos = {"ganada": "✅", "perdida": "❌", "nula": "➖", "sin_calificar": "❔"}
    lineas = [f"BETBOT {deporte.upper()} - resultados en papel", AVISO, ""]
    for fila, estado, real, clv in detalle:
        apuesta = _apuesta(fila["jugador"], fila["market"], fila["lado"], fila["point"])
        extra = ""
        if real is not None:
            extra += f" -> {real:g}"
        if estado == "nula" and real is None:
            extra += " (no jugo o no registro nada)"
        if clv is not None and estado != "nula":
            extra += f" | CLV {clv:+.1%}"
        lineas.append(f"{iconos.get(estado, estado)} {apuesta}{extra}")
    lineas += ["", _acumulado(resumen)]
    return "\n".join(lineas)


def _acumulado(r: dict) -> str:
    texto = (f"Acumulado: {r['apuntadas']} apuntadas, {r['calificadas']} calificadas "
             f"({r['ganadas']} ganadas), {r['pendientes']} pendientes.")
    if r["calificadas"]:
        texto += f"\nROI {r['roi']:+.1%} a 1 unidad por apuesta."
    if r["n_clv"]:
        texto += (f"\nCLV medio {r['clv_medio']:+.1%}: le gano al cierre en "
                  f"{r['clv_positivo']:.0%} de {r['n_clv']}.")
    if r["calificadas"] < 200:
        texto += "\nCon menos de 200 calificadas esto todavia es ruido."
    return texto
