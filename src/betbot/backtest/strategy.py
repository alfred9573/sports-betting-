"""Simulacion de la estrategia completa contra precios de mercado reales.

LA PREGUNTA QUE RESPONDE ESTE MODULO

Todo lo demas mide si el modelo predice mejor que la tasa base. Eso NO es lo
mismo que ganarle al mercado, y la diferencia lo es todo: la tasa base es un
rival trivial, el mercado incorpora lesiones, alineaciones, clima y el dinero de
gente que se dedica a esto. Un modelo puede batir holgadamente al baseline y aun
asi perder dinero contra un libro.

Aqui se simula el bot entero —modelo, devig, filtros de EV, Kelly, topes— sobre
odds historicas reales, y se reporta lo unico que importa: cuanto habria ganado
o perdido.

ADVERTENCIA SOBRE EL RESULTADO. Las odds historicas disponibles son de CIERRE,
el precio mas eficiente que publica el mercado. Apostar al cierre es la prueba
mas dura que existe:

  - Si el ROI sale positivo contra el cierre, la ventaja es real y probablemente
    MAYOR en la practica, porque apostando temprano se cogen precios peores para
    el libro.
  - Si sale negativo, no significa que el bot sea inutil, pero si que no tiene
    ventaja suficiente para superar el margen a precio de cierre. Y eso es
    exactamente lo que hay que saber ANTES de arriesgar dinero.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from betbot.ev.engine import EVConfig, expected_value, kelly_fraction
from betbot.odds.devig import devig


@dataclass
class Apuesta:
    fecha: str
    seleccion: str
    rival: str
    es_local: bool
    odds: float
    p_modelo: float
    p_mercado: float
    ev: float
    stake: float
    gano: bool
    pnl: float


@dataclass
class StrategyResult:
    n_evaluados: int
    n_apostados: int
    staked: float
    pnl: float
    bankroll_final: float
    bankroll_inicial: float
    roi: float
    stderr: float
    aciertos: int
    odds_media: float
    ev_medio: float
    max_drawdown: float
    apuestas: list[Apuesta] = field(default_factory=list, repr=False)

    @property
    def significativo(self) -> bool:
        """ROI distinguible de cero a dos sigmas."""
        return self.n_apostados > 1 and abs(self.roi) > 2 * self.stderr

    @property
    def tasa_acierto(self) -> float:
        return self.aciertos / self.n_apostados if self.n_apostados else 0.0

    def __str__(self) -> str:
        if not self.n_apostados:
            return (
                f"Ninguna apuesta pasó los filtros sobre {self.n_evaluados} partidos.\n"
                "El modelo nunca vio suficiente valor. Con umbrales realistas y un\n"
                "mercado eficiente, esto es un resultado posible y no un error."
            )
        veredicto = (
            "SIGNIFICATIVO" if self.significativo else "NO significativo (ruido)"
        )
        signo = "ventaja real" if self.roi > 0 else "perdida"
        return (
            f"Apuestas: {self.n_apostados} de {self.n_evaluados} partidos evaluados "
            f"({self.n_apostados / self.n_evaluados:.1%} del calendario)\n"
            f"  Arriesgado    {self.staked:>10.2f} u\n"
            f"  Resultado     {self.pnl:>+10.2f} u\n"
            f"  Bankroll      {self.bankroll_inicial:.0f} -> {self.bankroll_final:.2f}\n"
            f"  ROI           {self.roi:>+10.2%}  (+/- {self.stderr:.2%}) -> "
            f"{veredicto}, {signo}\n"
            f"  Aciertos      {self.tasa_acierto:>10.1%}  | cuota media "
            f"{self.odds_media:.2f} | EV medio {self.ev_medio:+.2%}\n"
            f"  Peor racha    {self.max_drawdown:>10.1%} de caida desde maximo"
        )


def simulate(
    games: list[dict],
    make_model: Callable[[], Any],
    predict_home_prob: Callable[[Any, dict], float | None],
    update: Callable[[Any, dict], None],
    config: EVConfig | None = None,
    devig_method: str = "shin",
) -> StrategyResult:
    """Corre la estrategia sobre partidos con odds reales, en orden cronologico.

    Cada `game` necesita: home, away, home_score, away_score, home_odds,
    away_odds (decimales) y opcionalmente date y new_season.

    Se respeta la regla del walk-forward: se predice ANTES de ver el resultado y
    el modelo solo se actualiza despues.
    """
    cfg = config or EVConfig()
    model = make_model()
    bankroll = cfg.bankroll
    pico = bankroll
    max_dd = 0.0
    apuestas: list[Apuesta] = []
    evaluados = 0
    staked = 0.0

    for g in games:
        p_home = predict_home_prob(model, g)
        odds_home = g.get("home_odds")
        odds_away = g.get("away_odds")

        if p_home is not None and odds_home and odds_away:
            evaluados += 1
            try:
                justas = devig([odds_home, odds_away], devig_method)
            except ValueError:
                justas = None

            if justas:
                # Se evaluan los dos lados; los filtros deciden.
                candidatos = (
                    (g["home"], g["away"], True, p_home, justas[0], odds_home),
                    (g["away"], g["home"], False, 1.0 - p_home, justas[1], odds_away),
                )
                for sel, rival, es_local, p_mod, p_just, odds in candidatos:
                    if not cfg.min_odds <= odds <= cfg.max_odds:
                        continue
                    if p_mod - p_just < cfg.min_edge:
                        continue
                    ev = expected_value(p_mod, odds)
                    if ev < cfg.min_ev:
                        continue

                    frac = min(
                        kelly_fraction(p_mod, odds, cfg.kelly_fraction),
                        cfg.max_stake_pct,
                    )
                    if frac <= 0:
                        continue

                    # Kelly sobre el bankroll VIVO, no sobre el inicial: es como
                    # se apuesta de verdad y es lo que hace que las rachas
                    # compongan en ambos sentidos.
                    stake = frac * bankroll
                    gano = (
                        g["home_score"] > g["away_score"]
                        if es_local
                        else g["away_score"] > g["home_score"]
                    )
                    pnl = stake * (odds - 1.0) if gano else -stake

                    bankroll += pnl
                    staked += stake
                    pico = max(pico, bankroll)
                    if pico > 0:
                        max_dd = max(max_dd, (pico - bankroll) / pico)

                    apuestas.append(
                        Apuesta(
                            fecha=str(g.get("date", "")),
                            seleccion=sel,
                            rival=rival,
                            es_local=es_local,
                            odds=odds,
                            p_modelo=p_mod,
                            p_mercado=p_just,
                            ev=ev,
                            stake=stake,
                            gano=gano,
                            pnl=pnl,
                        )
                    )

        update(model, g)

    n = len(apuestas)
    pnl_total = sum(a.pnl for a in apuestas)
    roi = pnl_total / staked if staked else 0.0

    if n > 1:
        unitarios = [a.pnl / a.stake for a in apuestas if a.stake]
        media = sum(unitarios) / len(unitarios)
        var = sum((x - media) ** 2 for x in unitarios) / (len(unitarios) - 1)
        stderr = math.sqrt(var / len(unitarios))
    else:
        stderr = 0.0

    return StrategyResult(
        n_evaluados=evaluados,
        n_apostados=n,
        staked=staked,
        pnl=pnl_total,
        bankroll_final=bankroll,
        bankroll_inicial=cfg.bankroll,
        roi=roi,
        stderr=stderr,
        aciertos=sum(1 for a in apuestas if a.gano),
        odds_media=sum(a.odds for a in apuestas) / n if n else 0.0,
        ev_medio=sum(a.ev for a in apuestas) / n if n else 0.0,
        max_drawdown=max_dd,
        apuestas=apuestas,
    )
