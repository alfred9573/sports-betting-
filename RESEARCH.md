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

## 2. Ángulos situacionales (36 probados)

Segunda tanda (11 ángulos): cambio de QB titular a favor y en contra (ML y ATS),
árbitros históricamente "de overs"/"de unders", underdog recibiendo +3/+7
exactos y sus vecinos (+2.5/+6.5, +3.5/+7.5), y regresión tras ganar o perder
por 21+. **Ninguno sobrevive.** El único significativo es negativo: apostar
*a favor* de un equipo con QB nuevo pierde un 9,98% (t=-2,63) — el mercado ya lo
castiga, y de sobra.

Primera tanda (25 ángulos):

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

## 4b. Filtro por banda de discrepancia (la lección de overfitting)

Hipótesis: el modelo pierde contra el mercado sobre todo cuando discrepa mucho
(>10pp). ¿Y si solo se apuesta cuando discrepa poco?

Se añadió `max_edge` al motor y se probaron 18 combinaciones de banda y rango
de cuotas. Selección en 2006-2015, validación en 2016-2025:

| Configuración | Train 2006-2015 | **Holdout 2016-2025** |
|---|---|---|
| Banda 6-10%, cuotas 1.5-2.5 (la mejor en train) | **+17,81%** (t=2,45) | **-6,38%** |
| Banda 3-6%, cuotas 1.5-2.5 | -16,47% | -9,00% |
| Sin filtro | — | -7,73% |

**La mejor configuración en train se dio la vuelta en holdout.** Con 18
combinaciones, una brilla por azar; al aplicarla a datos no vistos, pierde. Es
el mecanismo exacto por el que los vendedores de picks enseñan resultados
espectaculares: muestran el train y callan el holdout.

Ninguna banda supera a "sin filtro", y "sin filtro" pierde. El filtro queda en
el motor (desactivado por defecto) como recordatorio medido de que restringir
un modelo sin ventaja no le da ventaja.

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

## 7. Copiar a los tipsters de mayor yield

Hipótesis: si hay rankings de tipsters con yields del 15-25%, copiar su lógica
debería funcionar. Simulado: **5.000 tipsters, todos con EV real de -3%, o sea
ninguno con habilidad**, 300 apuestas cada uno a cuota 2.00.

| Puesto del ranking | Yield |
|---|---|
| #1 | **+20,0%** |
| #10 | +14,7% |
| #50 | +10,0% |
| Mediana | -2,7% |

45 de 5.000 (0,9%) superan el 10% de yield **sin tener ninguna ventaja**.

La prueba definitiva es qué pasa después. El top-100 del primer periodo:

| | Yield |
|---|---|
| Periodo 1 (por el que los elegiste) | **+11,0%** |
| Periodo 2 (los mismos) | **-1,9%** |
| Siguen en positivo | 36/100 |

Regresan a la media exactamente como predice el azar.

### El matiz que lo explica

Un yield del 20% en 300 apuestas *sí* es significativo... **para un tipster
elegido por adelantado** (t = 3,46). Pero elegir al mejor de 5.000 después de
ver los resultados exige corregir por esas 5.000 comparaciones: el umbral sube
de 1,96 a 4,42 sigmas, y el yield necesario pasa de ~11% a **25,5%**.

El #1 de la simulación sacó 20%: por debajo de lo que haría falta. Era el máximo
esperable del azar, no habilidad.

**Regla práctica**: pregúntate a cuántos candidatos viste antes de quedarte con
ese. Un ranking ya hizo la selección por ti, y solo muestra a los que
sobrevivieron — los que quebraron no aparecen.

## 8. Parlays

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

## 9. Line shopping: bloqueado por acceso, no por método

Medido en vivo con `survey` sobre el feed real de The Odds API (28 casas,
regiones `us,eu`):

```
NO APARECEN en el feed: bet365, betcris, caliente, codere
Filtrado a TUS casas: 0 de 1 discrepancias caen donde puedes apostar.
```

Ninguna de las casas accesibles desde México está en el feed. Las 28 que sí
están son estadounidenses reguladas (draftkings, fanduel, betmgm, betrivers),
offshore (bovada, betonlineag, betus, mybookieag), europeas (betsson, tipico_de,
unibet, williamhill), exchanges (betfair, matchbook) y sharps (pinnacle,
onexbet, marathonbet).

**La estrategia requiere dos cosas simultáneas**: un libro sharp de referencia
(hay varios) y un libro blando donde apostar que se quede atrás. Lo segundo es
lo que falta: los blandos del feed son los regulados de EE.UU., inaccesibles
desde México.

No es un fallo de modelado ni de datos. Es que **no se puede cobrar una
diferencia de precio en una casa donde no tienes cuenta.**

## 10. Ventana del ranking de tipsters (1 ano / 2 anos / historico)

Hipotesis del usuario: los rankings "de todos los tiempos" premian a gente que
ya no esta activa o cuyo edge caduco; acortar la ventana a 1-2 anos deberia dar
una lista mas util.

Simulacion: 3.000 tipsters, 90 de ellos (3%) con EV real de +4%, el resto -3%,
cuotas 2.00. Se ordena por yield en cada ventana, se toma el top-10, y se mide
cuantos de esos 10 son de los que de verdad tienen edge.

| Ventana | Apuestas | Yield del top-10 | Aciertos | Precision | EV real a futuro |
|---|---|---|---|---|---|
| 1 ano | 150 | 22,0% | 1/10 | 10% | **-2,30%** |
| 2 anos | 300 | 14,1% | 4/10 | 40% | +0,50% |
| 5 anos | 750 | 10,6% | 10/10 | 100% | +4,00% |
| Historico | 1.500 | 8,8% | 10/10 | 100% | +4,00% |

**Resultado: la hipotesis sale al reves.** Acortar la ventana empeora el
ranking de forma monotona. Con 150 apuestas el ruido domina la senal, asi que
quien encabeza la lista es el que tuvo suerte, no el que tiene edge: copiar al
top-10 de un ranking anual da un EV a futuro de -2,30%, peor que elegir al azar.

El yield mas vistoso (22,0%) sale precisamente de la ventana menos fiable. Un
yield alto no es evidencia de habilidad si la muestra es corta; es la firma
esperada del sesgo de supervivencia.

**LIMITE IMPORTANTE DE ESTE RESULTADO.** El +4,00% de la ultima columna es un
supuesto DE ENTRADA, no una medicion: es el edge que se le asigno por
construccion al 3% de tipsters buenos de la simulacion. La simulacion responde
"si existiera gente con edge real, ¿el ranking la encontraria?" y la respuesta
es "solo con muestras largas". NO responde "¿existe esa gente?", ni cuanto edge
tiene, ni si es copiable con las cuotas y limites que uno consigue de verdad.
Leer ese +4% como un rendimiento alcanzable seria confundir la entrada de la
simulacion con su salida.

## Archivado de lineas (`betbot collect`)

Las cinco filas de la tabla siguiente tienen la misma causa: no existe historico
comprable. La respuesta es fabricarlo. `betbot.collect` archiva cada precio del
feed —todas las casas, todos los mercados, props incluidas— sin apostar ni
alertar, deduplicando por cambio de precio para guardar movimientos en vez de
latidos.

No produce picks y no los producira. Es la condicion previa para que dentro de
varios meses se pueda plantear un backtest de props o de line shopping que hoy
es literalmente imposible. Resultado esperado, con honestidad: puede
perfectamente ser que tampoco haya nada.

## 11. Props de jugador: la via se reabre a medias

El plan de odds SI incluye props (verificado en vivo: 808 precios en un solo
partido, los seis mercados del catalogo). Eso reabre lo que estaba dado por
cerrado, pero solo a medias: la API da el PRECIO, no el historico. Sigue sin
existir archivo de lineas pasadas de props.

Lo que si existe es el rendimiento historico de jugador: nflverse publica
lineas semanales de 1999 a la temporada en curso, mismo esquema de 150 columnas
en las 27 temporadas. Ingeridas 476.669 filas de 11.492 jugadores.

Eso permite responder la pregunta PREVIA —¿describe bien el modelo al
jugador?— aunque no la de fondo —¿se equivoca el mercado?

### Calibracion del modelo (walk-forward estricto, 108.000 predicciones)

| Mercado | Predicciones | Desv. PIT | Brier | Log-loss |
|---|---|---|---|---|
| player_pass_yds | 8.446 | 0,38% | 0,2325 | 0,6576 |
| player_pass_tds | 8.085 | 0,72% | 0,2335 | 0,6600 |
| player_rush_yds | 14.107 | 0,35% | 0,2381 | 0,6689 |
| player_reception_yds | 39.447 | 0,42% | 0,2379 | 0,6686 |
| player_receptions | 38.215 | 0,36% | 0,2369 | 0,6665 |

Los deciles del PIT rondan el 10% en los cinco mercados. La distribucion esta
bien calibrada.

**EL BRIER DE 0,23 NO ES EVIDENCIA DE VENTAJA.** Las lineas sinteticas se
colocan a ±15% de la propia proyeccion del modelo, asi que por construccion el
modelo las separa. Mide consistencia interna, no habilidad contra el mercado.
Es el mismo tipo de numero bonito que ya engano una vez en el modelo de NFL.

### PIT aleatorizado: un defecto de la metrica, no del modelo

La primera corrida acusaba a `player_pass_tds` de estar roto (deciles 0,0 /
0,0 / 27,6). Antes de tocar el modelo se miro el dato: los touchdowns de pase
toman SIETE valores enteros. El PIT solo se reparte uniforme si la variable es
continua; con soporte tan grueso el histograma sale deforme haga lo que haga el
modelo. Aplicada la correccion estandar (Dawid 1984), la desviacion cae de
4,29% a 0,72%.

### Sobreconfianza en la cola alta: real en los conteos, ruido en las yardas

| Mercado | Cubo 60-80% (2010-2017) | Cubo 60-80% (2018-2026) |
|---|---|---|
| player_pass_tds | 67,4% -> 64,2% | 68,1% -> 58,8% |
| player_receptions | 63,2% -> 60,4% | 63,2% -> 57,8% |
| player_pass_yds | 65,0% -> 66,9% | 65,2% -> 64,5% |

Los dos mercados de CONTEO sobreestiman en las dos eras, mismo signo. El de
yardas cambia de signo entre eras: ahi no hay defecto, es ruido, y corregirlo
habria sido sobreajuste. Hay mecanismo que lo explica: los que fallan son los
discretos, y la distribucion empirica de cocientes asume escala continua.

### Correccion: ajustada en 2010-2017, validada en 2018-2026

Un solo parametro en espacio logit, p' = sig(a * logit(p)).

| Mercado | a optima | Holdout log-loss | Corregido | Veredicto |
|---|---|---|---|---|
| player_pass_yds | 1,10 | 0,6587 | 0,6589 | empeora |
| player_pass_tds | 0,84 | 0,6630 | **0,6608** | **mejora** |
| player_rush_yds | 1,00 | 0,6712 | 0,6712 | sin efecto |
| player_reception_yds | 0,96 | 0,6662 | 0,6663 | empeora |
| player_receptions | 0,90 | 0,6653 | 0,6652 | irrelevante |

Los tres mercados de yardas salen en a~1,00: el procedimiento no encuentra nada
donde no habia defecto, que es la mejor senal de que no esta inventando.

Solo se aplica `player_pass_tds`. **Recepciones se deja sin corregir a
proposito**: su mejora es de 0,0001 en log-loss con el Brier identico. El
encogimiento global arregla la cola alta y estropea los cubos medios, que van
en sentido contrario (33,6% -> 35,1%). El defecto tiene forma; no es
sobreconfianza uniforme. Queda marcado como `COLA_ALTA_DUDOSA`, que avisa en
vez de tapar.

### Condicionado a jugar (nota corregida)

Solo hay fila para un jugador si registro alguna estadistica. Una version
anterior de esta nota decia que eso hacia al modelo sistematicamente optimista
porque el libro precia la baja por lesion y el modelo no. Era exagerado: en la
mayoria de casas una prop se ANULA si el jugador no juega, asi que el libro
tampoco cobra ese riesgo y la condicion coincide. El sesgo que queda es mas
estrecho: jugadores que participan sin registrar estadistica pueden no tener
fila, y el modelo ve menos ceros de los reales. Pequeno en titulares, no en
suplentes. Las reglas de anulacion varian por casa.

### Lo que falta para saber si esto gana dinero

Comparar estas probabilidades contra precios reales. Eso empieza ahora, con lo
que `betbot collect` archive. Antes de varios cientos de props ya resueltas
cualquier conclusion es ruido.

## 12. Anytime TD: le gana a la tasa base, pero sobreestima arriba del 15%

Resultado binario (anoto o no), con TDs de carrera y de recepcion; los de pase
no cuentan en este mercado. Modelo: tasa Poisson que mezcla los TDs propios
(media movil encogida) con el uso (acarreos + targets) por la tasa de TD por
toque de la posicion. P(al menos uno) = 1 - exp(-lambda).

### Una base que hacia trampa

La primera comparacion usaba la tasa de TD de toda la plantilla de la posicion,
suplentes incluidos. Pero el modelo solo predice para jugadores con uso real,
que anotan mas. Contra esa base rebajada, parte de la "mejora" era solo "este
jugador si juega". Corregida para medir la base sobre los jugadores ELEGIBLES,
la mejora en entrenamiento bajo de 5,83% a 5,08%.

### Peso elegido en 2010-2017, validado en 2018-2026

| w (peso de los TDs propios) | Mejora 2010-2017 | Mejora 2018-2026 |
|---|---|---|
| 0,00 (solo uso) | 0,64% | 5,15% |
| 0,30 | 3,45% | 6,34% |
| 0,50 | 4,60% | 6,61% |
| **0,70 (elegido)** | **5,08%** | **6,45%** |
| 1,00 (solo TDs propios) | 4,26% | 5,35% |

La eleccion aguanta fuera de muestra: 6,45% frente a un maximo posible de 6,61%.
Por posicion en 2018-2026: RB 10,2%, QB 7,6%, WR 4,9%, TE 2,3%, FB -1,7%.

LO QUE ESTA MEJORA NO ES. Saber que un RB con 20 toques anota mas que uno con
8 no es una ventaja sobre el libro: el libro lo sabe igual. Superar a la tasa
base es condicion necesaria para que valga la pena comparar con precios, no
evidencia de que se les vaya a ganar.

### Sesgo por encima del 15%, y dos correcciones que no pasaron

| Predicho | Observado 2010-2017 | Observado 2018-2026 |
|---|---|---|
| ~25% | 21,8% | 23,6% |
| ~35% | 31,7% | 32,5% |
| ~44% | 38,8% | 42,7% |
| ~54% | 50,2% | 53,8% |

El exceso es real en las dos eras, pero menguo con el tiempo. Por eso:

- **Correccion fija ajustada en 2010-2017**: mejora el entrenamiento y EMPEORA
  el holdout (log-loss 0,50381 -> 0,50390), volteando el sesgo a subestimacion
  (43,8% -> 49,0%). Corrige el pasado, no el presente.
- **Recalibracion movil, cada temporada con las anteriores**: mejora el total
  con ventanas de 2, 3 y 5 temporadas (0,50542 -> ~0,5047), pero solo en 8-9 de
  14 temporadas. No se distingue del azar con claridad.

Ninguna se aplica. Una probabilidad de este modelo por encima del 40% hay que
leerla varios puntos mas baja.

### Un problema propio del mercado

En muchas casas el anytime TD solo se ofrece del lado "si". Sin el lado "no" no
se puede quitar el margen con los metodos de `devig`, asi que la comparacion
tendra que ser directa: EV = p_modelo * cuota - 1, con todo el margen de la casa
como obstaculo. En props de un solo lado ese margen suele ser grande.

## 13. Props de NBA: el mismo modelo, mejor calibrado

Fuente: hoopR (espejo de ESPN), 2002-2026. 848.095 filas, 666.468 partidos
jugados, 2.577 jugadores. A diferencia de NFL, trae a TODA la plantilla con sus
minutos, incluidos los que no jugaron. Se define "jugo" como minutos > 0, que es
cuando una casa no anula la prop: el sesgo de participacion que en NFL no se
puede corregir aqui no existe.

Tres cosas del dato resueltas en el codigo: la temporada se nombra por su ano de
FIN (2026 = 2025-26); la notacion de posiciones cambio con los anos (PG/SG/SF/PF
en 2002, G/F/C desde ~2020) y se unifica a G/F/C; y ~150-200 filas por temporada
no marcadas como DNP pero sin minutos quedan fuera.

### Calibracion (walk-forward desde 2010, 890.000 predicciones)

| Mercado | Predicciones | Desv. PIT | Brier |
|---|---|---|---|
| player_points | 240.374 | 0,13% | 0,2386 |
| player_rebounds | 262.778 | 0,09% | 0,2368 |
| player_assists | 174.677 | 0,11% | 0,2365 |
| player_threes | 211.824 | 0,43% | 0,2309 |

Puntos, rebotes y asistencias salen tres a cinco veces mejor calibrados que
cualquier mercado de NFL (0,35-0,72%). Mas partidos por jugador y un resultado
menos dependiente de jugadas sueltas.

### Estabilidad y correccion (2010-2017 -> 2018-2026)

| Mercado | Cola alta 2010-17 | Cola alta 2018-26 | a | Holdout log-loss | Veredicto |
|---|---|---|---|---|---|
| points | 61,4 -> 60,5 | 61,3 -> 60,8 | 0,98 | 0,66979 -> 0,66980 | sin defecto |
| rebounds | 62,3 -> 60,1 | 62,3 -> 61,3 | 0,98 | 0,66589 -> 0,66589 | sin defecto |
| assists | 63,8 -> 59,4 | 63,8 -> 60,1 | 0,96 | 0,66630 -> 0,66621 | irrelevante |
| **threes** | **65,0 -> 59,8** | **65,7 -> 59,6** | **0,88** | **0,65654 -> 0,65556** | **aplicada** |

Triples reproduce el patron de los touchdowns de pase: un conteo con pocos
valores posibles, con las DOS colas desviadas hacia fuera e identicas en ambas
eras (abajo: 19,7 -> 24,3 y 19,6 -> 24,2). Es sobreconfianza uniforme, justo lo
que corrige un encogimiento global. El mismo mecanismo en dos deportes distintos
es la mejor evidencia de que no es ruido.

Con la correccion aplicada, la cola baja de triples queda limpia (27,2% frente a
26,8% predicho) y el log-loss total baja de 0,6541 a 0,6531, pero la cola alta
sigue unos 4 puntos inflada (64,0% -> 59,9%). Pasa lo mismo que con touchdowns
de pase tras su correccion (66,0% -> 61,5%). Los dos quedan corregidos Y marcados
como `COLA_ALTA_DUDOSA`: corregido no significa fiable por encima del 60%.

Asistencias repite el caso de recepciones en NFL: la cola alta se pasa en las
dos eras, pero los cubos medios van al reves y un parametro global no la
arregla. Marcada como `COLA_ALTA_DUDOSA`.

### Rendimiento

Tres cambios sin efecto en los resultados (NFL verificado identico hasta el
decimo decimal tras cada uno): media movil cortada donde el peso cae por debajo
de 1e-9 (las carreras de NBA superan los 1.500 partidos y el coste era
cuadratico), insercion ordenada en vez de reordenar la lista entera, y la media
del mismo historial reutilizada entre predecir y aprender. Subconjunto de NBA:
60,9s -> 9,9s. Backtest completo: ~2 minutos.

### Lo que falta

Igual que en NFL: comparar contra precios reales. La temporada 2026-27 arranca a
finales de octubre, y archivar sus lineas choca con el presupuesto del plan
gratuito (ver RUNBOOK 5g).

## Lo que queda vivo

Nada, con las casas disponibles desde México.

`ev/lineshop.py` sigue implementado y funcional. Si en algún momento se tiene
cuenta en una casa que el feed cubra, el sistema funciona tal cual: `survey`
mide la oportunidad, `scan --strategy lineshop` emite las señales y `close`
captura los cierres para medir CLV.

El requisito no es técnico, es de acceso.

## Lo que no se pudo probar por falta de datos

| Idea | Qué falta |
|---|---|
| Props de jugadores | Líneas históricas de props. El MODELO ya se valida con nflverse (§11); lo que falta es el precio pasado, que se archiva desde ahora |
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
