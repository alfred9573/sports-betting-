"""Mensajes de Telegram de las apuestas en papel.

Van a alguien que va a leerlos en el telefono, asi que: en espanol, con la
hora de Mexico, sin nombres internos de mercado, y con una advertencia que no
se puede pasar por alto. Son apuestas EN PAPEL: el objetivo es ver si el
modelo le gana a la casa antes de poner un peso. Un mensaje que pareciera un
pick invitaria justo a lo que este proceso existe para evitar.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from betbot.papel import unidades_de

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


def _pesos(unidades: float, pesos_por_unidad: float | None) -> str:
    return f" (${unidades * pesos_por_unidad:,.0f})" if pesos_por_unidad else ""


def mensaje_apuesta(a, deporte: str, pesos_por_unidad: float | None = None) -> str:
    """Una apuesta, con todo lo necesario para ejecutarla en cualquier casa.

    La cuota minima es la pieza clave: el precio del aviso es la mediana de las
    casas del feed, y la casa donde se apueste casi nunca paga exactamente eso.
    "Solo si paga al menos X" funciona en cualquiera, siempre que la linea sea
    la misma.
    """
    ln = a.linea
    return "\n".join([
        f"PAPEL {deporte.upper()} - no apostar",
        f"{_hora(ln.commence)} - {ln.partido}",
        "",
        _apuesta(ln.jugador, ln.market, ln.lado, ln.point),
        f"Cuota {ln.precio:.2f} (mediana de {ln.n_casas} casas)",
        f"Minima: {a.cuota_minima:.2f} - por debajo ya no tiene valor",
        f"Modelo {a.p:.0%} | EV {a.ev:+.1%}",
        f"Stake: {a.unidades:g} u{_pesos(a.unidades, pesos_por_unidad)}",
        "",
        "Solo vale con la MISMA linea en tu casa.",
    ])


def mensaje_generacion(inf, deporte: str, resumen: dict, enviadas_aparte: int = 0) -> str:
    """Resumen de la corrida: cuanto se reviso, que se apunto y que se descarto.

    Las apuestas que ya se mandaron una por una (`enviadas_aparte`) no se
    repiten aqui; las que no cupieron como mensaje propio si se listan.
    """
    lineas = [f"BETBOT {deporte.upper()} - apuestas en papel", AVISO, ""]
    if inf.lineas == 0:
        lineas += [
            "No encontre lineas de props vigentes.",
            "Si esto pasa un sabado, el barrido de las 9:15 no guardo nada:",
            "revisa logs/collect.log en el servidor.",
        ]
        return "\n".join(lineas)

    total_u = sum(a.unidades for a in inf.nuevas)
    lineas.append(f"Revise {inf.lineas:,} lineas de props. Nuevas en papel: {inf.apuntadas}"
                  + (f" ({total_u:g} u en total)." if inf.nuevas else "."))
    restantes = sorted(inf.nuevas, key=lambda a: (a.linea.commence, a.linea.partido))
    restantes = restantes[enviadas_aparte:]
    if enviadas_aparte:
        lineas.append(f"({enviadas_aparte} ya enviadas arriba, una por mensaje.)")
    if restantes:
        por_partido: dict[str, list] = {}
        for a in restantes:
            clave = f"{_hora(a.linea.commence)} - {a.linea.partido}"
            por_partido.setdefault(clave, []).append(a)
        for partido, apuestas in por_partido.items():
            lineas += ["", partido]
            for a in apuestas:
                ln = a.linea
                lineas.append(
                    f"- {_apuesta(ln.jugador, ln.market, ln.lado, ln.point)}"
                    f" @ {ln.precio:.2f} (min {a.cuota_minima:.2f}) | {a.unidades:g} u"
                    f" | EV {a.ev:+.1%}"
                )
    elif not inf.nuevas:
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
        if estado in ("ganada", "perdida"):
            u = unidades_de(fila)
            neto = u * (fila["precio"] - 1.0) if estado == "ganada" else -u
            extra += f" | {neto:+.2f} u"
        if clv is not None and estado != "nula":
            extra += f" | CLV {clv:+.1%}"
        lineas.append(f"{iconos.get(estado, estado)} {apuesta}{extra}")
    lineas += ["", _acumulado(resumen)]
    return "\n".join(lineas)


def _acumulado(r: dict) -> str:
    texto = (f"Acumulado: {r['apuntadas']} apuntadas, {r['calificadas']} calificadas "
             f"({r['ganadas']} ganadas), {r['pendientes']} pendientes.")
    if r["calificadas"]:
        texto += (f"\nEn unidades: {r['ganancia_u']:+.2f} u sobre {r['unidades_arriesgadas']:g}"
                  f" arriesgadas (ROI {r['roi_u']:+.1%}).")
        texto += f"\nA stake plano: ROI {r['roi']:+.1%}."
    if r["n_clv"]:
        texto += (f"\nCLV medio {r['clv_medio']:+.1%}: le gano al cierre en "
                  f"{r['clv_positivo']:.0%} de {r['n_clv']}.")
    if r["calificadas"] < 200:
        texto += "\nCon menos de 200 calificadas esto todavia es ruido."
    return texto
