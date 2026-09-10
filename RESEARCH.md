# Registro de hipótesis probadas

Todo lo que se ha probado contra datos reales, con su resultado. **Incluye los
fracasos**, que son la mayoría y son el contenido más útil: evitan volver a
gastar tiempo en callejones ya recorridos.

Todos los backtests son walk-forward (el modelo solo ve partidos anteriores al
que predice) y, cuando hay precios, contra **líneas de cierre reales** — el
precio más eficiente que publica el mercado y por tanto la prueba más dura.

**Dato de contexto**: la única fuente gratuita encontrada con odds históricas es
`nflverse` (NFL, 5.295 partidos 2006-2025, con moneyline, hándicap y totales).
Todo lo que involucre precios está probado ahí. Para NBA, MLB y fútbol solo se
pudo medir capacidad predictiva, no rentabilidad.

---

## 1. Modelos propios contra el mercado

| Hipótesis | Resultado | Veredicto |
|---|---|---|
| Elo bate al baseline de tasa base | NBA +0.078, NFL +0.060, EPL +0.074, MLB +0.012 log-loss | ✅ Sí |
| Elo bate al **mercado** | Brier 0.2209 vs 0.2110 del mercado | ❌ **No** |
| La estrategia completa gana dinero | ROI **-1,36%**, bankroll 1000 → 31 | ❌ **No** |
| El modelo aporta info que el mercado no tiene | Ninguna mezcla mejora al mercado; ni al 5% de peso | ❌ **No** |

La última fila es la más concluyente. Si mezclar un 5% de modelo con un 95% de
mercado ya empeora la predicción, el modelo no tiene **nada** que el mercado no
sepa ya.

**Diagnóstico**: donde más discrepan (>20pp) el modelo dice 54,2%, el mercado
49,8% y la realidad es 45,5%. El modelo es *menos* fiable justo donde cree tener
más ventaja — porque apostar selecciona precisamente los partidos donde se
equivoca.

Validación del simulador antes de creerse nada: apostador aleatorio -2,51%
(≈ el margen), siempre-favorito -4,75%, oráculo que conoce el resultado +88,80%.

## 2. Ángulos situacionales (25 probados)

Ninguno con ventaja positiva que sobreviva la corrección por prueba múltiple.
Los dos únicos significativos son **negativos**.

| Ángulo | n | ROI | t |
|---|---|---|---|
| O/U: UNDER con viento >15 | 325 | +6,61% | +1,23 |
| ML: visitante tras semana libre | 678 | +5,25% | +1,03 |
| ML: playoffs, underdog | 230 | +8,93% | +0,89 |
| O/U: OVER si total <42 | 1478 | +1,84% | +0,73 |
| … *(15 ángulos intermedios, todos entre -3% y +2%)* | | | |
| O/U: siempre OVER | 5216 | -3,52% | -2,61 |
| ATS: siempre LOCAL | 5162 | **-4,72%** | **-3,48** |
| ML: siempre favoritos | 5295 | **-3,85%** | **-3,94** |

Con 25 pruebas al 5%, el umbral t corregido es **3,09** (no 1,96), y por azar se
esperarían ~1,2 falsos hallazgos. Ninguno de los positivos se acerca.

## 3. Over/under y hándicap

| Predictor del over/under | Brier |
|---|---|
| Baseline (moneda al aire) | 0.2500 |
| **Mercado** | 0.2501 |
| Modelo de totales | 0.2568 |

**El mercado no predice mejor que una moneda al aire** — y es deliberado: la
línea se coloca donde el resultado es 50/50. En hándicap igual (el local cubre
el 49,0%).

Consecuencia: la ventaja máxima teórica de un modelo en estos mercados es
**cero**. Solo queda el precio, y ahí la diferencia entre casas vale 7 puntos de
ROI (-4,5% a -110 frente a +2,5% a +105).

## 4. Inconsistencia interna del mercado (lo más prometedor, y aun así no)

Hipótesis: el moneyline y el hándicap deberían implicar la misma probabilidad de
victoria; donde divergen, uno está mal.

Discrepancia real: mediana 2,93%, p90 5,17%. Apostando donde diverge más del 5%:
**ROI +10,73%** sobre 642 apuestas.

Parece un hallazgo. No lo es:

| Prueba de robustez | Resultado |
|---|---|
| ¿Estable en el tiempo? | ✅ +9,4% / +6,7% / +17,4% en los tres periodos |
| ¿Estable al mover el umbral? | ❌ **al 4% se vuelve -1,19%** |
| ¿Concentrado en pocas apuestas? | ❌ **5 de 642 aportan el 57% del beneficio** |
| ¿Significativo? | ❌ t=+1,41, hace falta >3,1 con corrección |

Un cambio de signo al mover el umbral un punto porcentual, y más de la mitad del
beneficio en 5 apuestas, son la firma del ruido. Sin esas 5 el ROI cae a +4,65%,
todavía positivo pero indistinguible de cero.

**Queda como hipótesis para validar hacia delante**, no como hallazgo.

## 5. ¿Hay algún segmento menos eficiente? (11 probados)

Si el mercado fuera descuidado en algún rincón —partidos poco vistos, inicio de
temporada con poca información, divisionales— ahí estaría la oportunidad. Se
comparó el Brier del modelo contra el del mercado en 11 segmentos.

**El mercado gana en los once.** Sin excepción.

| Segmento | n | Mercado | Modelo | Diferencia |
|---|---|---|---|---|
| Semanas 1-4 (poca info) | 1120 | 0.22064 | 0.22581 | +0.00517 |
| No divisionales | 3324 | 0.21284 | 0.22041 | +0.00757 |
| Partido parejo | 1047 | 0.24941 | 0.25759 | +0.00818 |
| Playoffs | 230 | 0.21885 | 0.22712 | +0.00827 |
| Semanas 5-13 | 2480 | 0.21042 | 0.22006 | +0.00964 |
| **Todos** | **5166** | **0.21098** | **0.22090** | **+0.00992** |
| 2006-2012 | 1605 | 0.21017 | 0.22083 | +0.01066 |
| Favorito claro | 2668 | 0.17874 | 0.19107 | +0.01234 |
| 2019-2025 | 1960 | 0.21063 | 0.22308 | +0.01245 |
| Semanas 14+ | 1566 | 0.20496 | 0.21872 | +0.01376 |
| Divisionales | 1842 | 0.20761 | 0.22178 | +0.01417 |

La distancia se estrecha al inicio de temporada (+0.005), que es cuando el
mercado tiene menos información — pero nunca se cierra.

**Alcance de esta conclusión**: las líneas de cierre de la NFL son probablemente
el mercado de apuestas más eficiente que existe (volumen enorme, mucho dinero
profesional). Que no haya nada aquí NO demuestra que no lo haya en mercados menos
líquidos —Liga MX, divisiones menores, ligas femeninas— donde el dinero
profesional presta menos atención. Eso queda sin probar únicamente por falta de
odds históricas de esos mercados.

## 6. Ajuste por abridor en MLB

Inferir la calidad del lanzador desde los resultados del equipo: **+0.0004** de
log-loss en holdout. Ruido. Ver `models/pitchers.py` para el detalle y por qué
harían falta proyecciones tipo FIP/SIERA.

## 7. Parlays

No requieren backtest: la matemática es cerrada. Un parlay multiplica el EV por
pata — `EV_parlay = (1 + EV_pata)^n - 1`.

| EV por pata | 4 patas |
|---|---|
| -1,36% (el sistema medido) | **-5,33%** |
| +2,5% (line shopping hipotético) | +10,4% |

Son apalancamiento sobre la ventaja que ya tengas, en la dirección que apunte.
Y las casas añaden margen extra por pata: con un 5% extra, un +3% real por pata
se convierte en **-7,4%** en un parlay de 4.

---

## Lo que queda vivo

**Line shopping** (`ev/lineshop.py`): cobrar la diferencia cuando una casa
blanda tarda en mover su línea. No exige predecir mejor que nadie.

- **No se ha podido backtestear**: haría falta histórico de odds de varias casas
  simultáneas y no existe gratis.
- Es más fuerte en totales y hándicap, precisamente porque ahí no hay nada más
  que capturar.
- Se mide en vivo con CLV. `betbot survey` estima si la oportunidad existe
  gastando 3 créditos.

## Lo que no se pudo probar por falta de datos

| Idea | Qué falta |
|---|---|
| Props de jugadores | Líneas históricas de props (no se venden a particulares) |
| Line shopping | Odds históricas de varias casas simultáneas |
| Apuestas en vivo | Odds históricas in-play |
| Parlays de misma jugada | Precios históricos de SGP y su ajuste por correlación |
| Movimiento de línea | Líneas de apertura (nflverse solo publica cierre) |

## Metodología

Lo que se hizo para no engañarse:

- **Walk-forward estricto** en todos los backtests, con test que lo verifica
  contando actualizaciones por índice.
- **Selección en un periodo, validación en holdout posterior** para cada
  calibración de parámetros.
- **Controles del simulador** (aleatorio, siempre-favorito, oráculo) antes de
  interpretar cualquier resultado.
- **Corrección por prueba múltiple** al evaluar ángulos.
- **Pruebas de robustez** (estabilidad temporal, sensibilidad al umbral,
  concentración) sobre el único candidato prometedor.
