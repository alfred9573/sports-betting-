# betbot — Bot de análisis de apuestas deportivas (señales por EV)

Sistema de **detección de valor esperado**: compara la probabilidad real estimada
por un modelo propio contra la probabilidad implícita en las odds de las casas,
ya descontado el margen. Emite **señales**; las apuestas se ponen a mano.

**No ejecuta apuestas automáticamente y no lo hará.**

## Estado

Pipeline completo (`odds → modelo → EV → alerta`) más capa de ingesta histórica
y backtest walk-forward. **Los cuatro deportes están entrenados y validados con
datos reales**: 71.086 partidos en total (NBA, MLB, NFL y Premier League).

Lo que falta para operar: registrar odds de cierre en vivo y validar con dinero
de papel. Ver *Lo que falta* al final.

## Para ponerlo a funcionar

**[RUNBOOK.md](RUNBOOK.md)** — guía operativa paso a paso: instalación, API key,
validación de adaptadores, cron y dinero de papel. Empieza por ahí si tu objetivo
es operar, no leer código.

Primer comando útil:

```bash
python -m betbot.cli doctor      # config, datos, frescura de modelos y CLV
```

## Arranque rápido

Todo esto se ejecuta en una terminal de tu máquina (ver
[RUNBOOK.md](RUNBOOK.md) si no sabes por dónde empezar).

```bash
git clone <repo> && cd sports-betting-
bash scripts/setup.sh         # Python, venv, dependencias, tests y demo

python -m betbot.cli demo                              # pipeline, datos sintéticos, sin red
python -m betbot.cli ingest --sport nba --from 2000 --to 2015
python -m betbot.cli backtest --sport nba              # walk-forward real
python -m betbot.cli doctor                            # diagnostico
pytest -q                                              # 297 tests
```

El núcleo de modelado corre en stdlib puro. La única dependencia es `certifi`
(el bundle de certificados): la verificación TLS tiene que funcionar nada más
instalar, y el Python de macOS no trae certificados utilizables por defecto.
`pandas` queda en el extra `data`.

## Arquitectura

```
ingest/       Descarga y normalización de resultados históricos
  teams.py      Registro canónico de equipos + alias (la pieza más crítica)
  store.py      SQLite idempotente, reanudable y cronológicamente ordenado
  http.py       Fetcher con caché en disco, rate limit y reintentos
  sources/      Retrosheet (MLB), 538 (NBA), engsoccerdata, ESPN, MLB StatsAPI
odds/         Proveedores de cotizaciones + eliminación de vig (multiplicative/power/shin)
models/       Un módulo por deporte, todos con la misma interfaz ProbabilityModel
  elo.py        Elo genérico con margen de victoria (base de NBA y MLB)
  nba.py        Elo + encogimiento
  nfl.py        Elo con margen de victoria
  pitchers.py   Ratings de abridor MLB (medido: aporta ~nada, ver abajo)
  mlb.py        Elo + Pythagorean + ajuste de abridor + techo de probabilidad
  soccer.py     Poisson bivariado con corrección Dixon-Coles
ev/           Motor de EV, Kelly fraccionado y filtros de riesgo
backtest/     Brier, log-loss, calibración, CLV, ROI con error típico
  walkforward.py  Validación sin fuga temporal (binaria y multiclase)
  multiclass.py   RPS, log-loss y calibración por clase para 1X2
alerts/       Consola y Telegram
storage.py    SQLite: señales, odds de cierre, liquidación
closing.py    Captura de línea de cierre (el job que hace medible el CLV)
cli.py        doctor / validate-source / demo / ingest / backtest /
              scan / close / report
```

El motor de EV no sabe nada de baloncesto, béisbol ni fútbol: solo consume
`ModelProbabilities`. Añadir un deporte es escribir un módulo nuevo en `models/`.

## Resultados medidos

Walk-forward estricto (el modelo solo ve partidos anteriores al que predice),
con selección de parámetros en un periodo temprano y validación en un holdout
posterior nunca visto. Baseline = predecir siempre la frecuencia base.

| Deporte | Partidos | Log-loss | Baseline | Mejora | **Relativa** | Acierto |
|---|---|---|---|---|---|---|
| **NBA** | 20.536 | 0.5979 | 0.6757 | +0.0778 | **11.5%** | 67.2% |
| **NFL** | 7.276 | 0.6254 | 0.6854 | +0.0600 | **8.8%** | 65.6% |
| **Fútbol (EPL)** | 8.360 | 0.9905 | 1.0643 | +0.0739 | **6.9%** | 52.6% |
| **Fútbol (Liga MX)** | 2.049 | 1.0311 | 1.0682 | +0.0371 | **3.5%** | 49.1% |
| **MLB** | 34.914 | 0.6788 | 0.6903 | +0.0116 | **1.7%** | 56.7% |

El fútbol usa log-loss multiclase (tres resultados, baseline ln(3)=1.0986), así
que su columna relativa no es estrictamente comparable con las binarias. Por RPS
—la métrica estándar de 1X2— las dos ligas quedan así:

| Liga | RPS | Baseline | Mejora relativa |
|---|---|---|---|
| Premier League | 0.2012 | 0.2272 | **11,4%** |
| Liga MX | 0.2125 | 0.2257 | **5,9%** |

**Orden por calidad de modelo: NBA > EPL > NFL > Liga MX >> MLB.**

La Liga MX rinde alrededor de la mitad que la Premier. Dos causas plausibles y no
excluyentes: la muestra disponible es mucho menor (2.049 partidos frente a 8.360,
la fuente solo cubre desde 2018-19), y la estructura de torneos cortos —Apertura
y Clausura de 17 jornadas cada uno— deja menos partidos por equipo antes de que
el modelo tenga que predecir.

### Me equivoqué sobre la NFL

Argumenté dos veces que la NFL iba al final porque "17 partidos por temporada es
demasiado poco para separar señal de ruido". Medido, es falso: la NFL da +0.0600
de mejora sobre baseline, **cinco veces la de MLB** y en el mismo rango que NBA.
Se modela bien por las mismas razones que el baloncesto — diferencias de talento
grandes, sin empates, ventaja de local que pesa.

Lo que sí sobrevive del argumento, pero es otra cosa: la NFL ofrece ~285 partidos
por temporada frente a 1.230 de NBA. Eso no degrada la calidad de la predicción,
degrada **la velocidad a la que puedes validar** con CLV y ROI. Son pocas
oportunidades de apuesta, no malas.

### Y el volumen de MLB no compensa su falta de señal

La intuición inicial era empezar por MLB por volumen (2430 partidos/temporada).
La medición dice lo contrario: el béisbol tiene el doble de datos que la NBA y
una séptima parte del edge. En el barrido de MLB, 27 combinaciones de parámetros
caben en un rango de log-loss de 0,0016 — el modelo es casi insensible a su
propia configuración porque apenas hay nada que extraer.

### Lo que encontró la tabla de calibración (NBA)

Los agregados no lo habrían detectado. Con los parámetros iniciales (escritos de
memoria: `k=20`, ventaja de local 60 puntos de Elo), el log-loss salía razonable
—0,6028— pero **los diez deciles de calibración tenían gap negativo**: el modelo
infravaloraba al local en todo el rango, de forma sistemática.

Calibrado contra datos reales (selección en 2000-2010, validación en el holdout
2011-2015, nunca visto durante la selección):

| Configuración | Holdout log-loss | Gap medio |
|---|---|---|
| Inicial (k=20, HFA=60) | 0.6028 | **-3.16%** |
| Calibrada (k=10, HFA=85) | 0.5979 | **+0.89%** |

La ventaja de local en la NBA vale ~85 puntos de Elo, no 60. La mejora de
log-loss es modesta; la de calibración no: un sesgo sistemático de 3 puntos
porcentuales significa apostar siempre al lado equivocado de la misma moneda.

Efecto colateral: el encogimiento hacia 50/50 (`shrink=0.90`) resultó ser
**contraproducente** una vez corregida la ventaja de local. Estaba compensando
el sesgo del HFA mal puesto, no un defecto real del Elo. Ahora es `1.0`.

### El ajuste por abridor en MLB no funciona (medido)

El abridor es el factor que más mueve una línea de béisbol, así que parecía la
mejora obvia. Se implementó estimando el rating de cada abridor por Elo con los
propios game logs de Retrosheet, y se midió con selección en 2010-2020 y holdout
en 2021-2025:

| | Holdout log-loss |
|---|---|
| Sin ajuste de abridor | 0.6773 |
| Con ajuste de abridor | 0.6769 |
| **Ganancia** | **+0.0004** |

Es ruido: ~3% de un edge que ya era pequeño (0.0118 total sobre baseline). Y el
método es frágil — con `k` alto la ganancia se vuelve **negativa** (-0.0060).

Lo curioso es que los ratings sí aprenden algo real: los mejores por ajuste son
Max Fried, Garrett Crochet, Kershaw y Eovaldi, abridores genuinamente buenos. El
problema es que el resultado del partido depende del bullpen y del ataque tanto
como del abridor, así que la señal llega ahogada.

**Conclusión práctica**: para que el abridor aporte hacen falta proyecciones que
midan al lanzador *directamente* (FIP, xFIP, SIERA), no inferirlo del resultado
del equipo. `MLBModel.pitcher_elo` acepta cualquier fuente de ajustes — aliméntalo
con eso y vuelve a medir. No merece la pena refinar más el método por inferencia:
ya está medido y no llega.

### Y en fútbol: el empate estaba mal calibrado

Mismo patrón, otro deporte. El `rho` de Dixon-Coles se escribió como -0.13 (el
valor del paper original sobre datos ingleses de los 90). Medido sobre 8.360
partidos reales, hace falta **más del doble**:

| Configuración | Holdout RPS | \|gap\|max |
|---|---|---|
| Inicial (hfa 1.30, rho -0.13, decay 0.0065) | 0.2039 | 2.29% |
| Calibrada (hfa 1.44, rho -0.28, decay 0.0030) | 0.2012 | 1.87% |

Con rho=-0.13 el empate quedaba infravalorado entre 2,2 y 2,9 puntos porcentuales
en **todas** las configuraciones probadas. A cuota 3.40 ese es exactamente el
rango donde el bot creería ver valor en el empate sin que lo haya.

Señal de que no es overfitting: la misma combinación gana en los dos criterios a
la vez —mejor RPS y mejor calibración por clase (gap máximo 0,08% en train)—, y
generaliza al holdout.

### Los parámetros del modelo son específicos de cada liga

Aplicar a la Liga MX los parámetros calibrados en la Premier produce sesgo
sistemático: sobreestima al local 2,8 puntos porcentuales e infravalora al
visitante 3,0.

| | Holdout RPS | \|gap\|max |
|---|---|---|
| Parámetros de la Premier | 0.2077 | 2,83% |
| Calibrados en Liga MX | 0.2074 | **1,53%** |

Fíjate en lo que cambia y lo que no. **El RPS mejora solo +0.0003 — indistinguible
de ruido con 756 partidos de holdout.** Si me hubiera quedado en la métrica
agregada, la conclusión habría sido "da igual". Pero el error de calibración se
reduce a la mitad, y eso sí importa: un sesgo sistemático de 3pp significa apostar
siempre al lado equivocado de la misma moneda.

Por eso `PoissonSoccerModel.for_league(sport)` carga los parámetros medidos de
cada liga, y avisa por log cuando una liga no tiene preset propio (cae a los de
la Premier, los mejor validados, pero arrastrando el sesgo).

Los valores de Liga MX son **preliminares**: 2.049 partidos es poca muestra y el
barrido entero cabe en un rango de RPS de 0,0037, señal de que hay poco que
afinar con estos datos.

## Las tres decisiones que sostienen el sistema

### 1. Nunca comparar el modelo contra `1/odds`

`1/odds` incluye el margen de la casa. En un moneyline NBA típico las dos patas
suman **1.045**: si comparas tu modelo contra eso, te estás regalando ~2.2 puntos
de "edge" falso por lado. Con un umbral de EV del 3%, eso es exactamente la
diferencia entre un bot con ventaja y uno que apuesta ruido a comisión.

Por eso `devig()` exige que le pases **todas** las patas del mercado, y el default
es Shin. Hay un test dedicado a que el vig por sí solo jamás genere una señal.

### 2. Calibración antes que ROI

El orden en que se valida un modelo no es el intuitivo:

| Métrica | Qué mide | Muestra necesaria |
|---|---|---|
| **Brier / log-loss** | ¿Tus probabilidades son ciertas? | ~200-500 partidos |
| **CLV** | ¿Le ganas al precio de cierre? | ~100-200 apuestas |
| **ROI** | ¿Ganaste dinero? | **~4400 apuestas** |

Ese último número no es retórico. Con un edge real del 3% a cuotas ~2.00, la
desviación típica por apuesta es ≈1.0 unidades; para que el ROI se separe de cero
a dos sigmas hacen falta del orden de 4400 apuestas. **Un backtest de 300 apuestas
con +8% de ROI no es evidencia de nada** — `roi_summary()` te lo dice a la cara con
el campo `significant`.

El CLV es el atajo: si tus precios le ganan sistemáticamente al cierre, tienes
ventaja real aunque la muestra todavía vaya perdiendo. Por eso `storage.py`
guarda odds de cierre desde el primer día.

### 3. Kelly fraccionado y topes duros

Kelly completo maximiza el crecimiento logarítmico **asumiendo que tu probabilidad
es correcta**. No lo es. El default es Kelly 0.25 más un tope duro del 2% del
bankroll por apuesta: reduce la varianza mucho más rápido de lo que reduce el
crecimiento, y sobrevive a un modelo mal calibrado.

Filtros activos por defecto: EV mínimo 3%, edge mínimo 2pp, cuotas entre 1.20 y
10.0 (los longshots tienen EV teórico alto y calibración pésima), y mínimo 3 libros
o presencia de un libro sharp.

## Cuota de The Odds API

El plan gratuito son **500 requests/mes**, y cada llamada cuesta
`n_mercados × n_regiones` créditos, no 1. Pedir `h2h+spreads+totals` en `us+eu`
son 9 créditos por llamada: a 3 deportes cada 30 minutos, el mes se agota en día
y medio. El cliente usa un mercado y una región por defecto, y expone
`credits_remaining` en cada respuesta.

## Fuentes de datos

| Fuente | Deporte | Cobertura | Estado |
|---|---|---|---|
| Retrosheet (espejo Chadwick) | MLB | 1871-2025 | ✅ validada, 0 descartes |
| FiveThirtyEight Elo | NBA | 1946-2015 | ✅ validada, 0 descartes |
| nflverse/nfldata | NFL | 1999-2025 | ✅ validada, 0 descartes |
| engsoccerdata | Fútbol inglés | 1888-2016 | ✅ validada, 0 descartes |
| footballcsv/mexico | Liga MX | 2018-2025 | ✅ validada, 0 descartes |
| MLB StatsAPI | MLB | actual + histórico | ⚠️ sin probar en vivo |
| ESPN scoreboard | NBA/NFL/MLB/fútbol | temporadas recientes | ⚠️ sin probar en vivo |

Las dos marcadas ⚠️ tienen el parseo cubierto por tests contra payloads fijados,
pero **no se han podido ejecutar contra la API real**: el entorno donde se
desarrollaron tiene bloqueado el tráfico saliente a esos hosts. Hacen falta unas
corridas reales antes de fiarse de ellas. Los datasets estáticos de GitHub sí se
descargaron y validaron de verdad.

Nota: Retrosheet no publica gamelog de 2024 (`GL2024.TXT` no existe; esa
temporada solo está como event files). Para 2024 hay que usar StatsAPI o ESPN.

## Medir CLV desde el día uno

```bash
# en cron, cada 10-15 minutos
*/10 * * * * cd /ruta/sports-betting- && python -m betbot.cli close
```

`close` captura el precio de cierre de toda señal cuyo partido arranque en los
próximos 30 minutos. Guarda dos referencias:

- **`closing_odds`** — precio final en el *mismo* libro de la señal. Mide si le
  ganaste a ese libro.
- **`closing_fair_prob`** — consenso sin vig al cierre. Es el benchmark honesto:
  un libro blando puede dejar la línea quieta y hacerte creer que acertaste
  cuando el mercado real se movió en tu contra.

`report` avisa de cuántas señales se quedaron sin cierre capturado. Si ese número
crece, el job no corre con frecuencia suficiente y te estás quedando ciego.

## Lo que falta

1. **Smoke test real de StatsAPI y ESPN** — ver tabla de fuentes.
2. **Proyecciones FIP/SIERA** para alimentar `pitcher_elo` (ver sección del
   abridor: el camino por inferencia ya está medido y descartado).
3. **MLE Dixon-Coles** en fútbol: `fit()` usa estimador de momentos, suficiente
   para validar el pipeline, insuficiente para producción. Y usar xG en vez de
   goles, que predice mejor.
4. **Resto de ligas top-5** (La Liga, Serie A, Bundesliga, Ligue 1, Champions):
   necesitan fuente, tabla de alias y su propio preset calibrado. `engsoccerdata`
   ya sirve España (`spain.csv`), así que esa es la más barata de añadir.
5. **xG en lugar de goles** para el modelo de fútbol: predice mejor que el
   resultado real, que es una muestra pequeñísima de un proceso ruidoso.
6. **Más historia de Liga MX**: la fuente actual arranca en 2018-19. Con más
   temporadas, los presets dejarían de ser preliminares.

## Advertencia

Ningún modelo aquí tiene ventaja demostrada sobre el mercado. El mercado de
apuestas deportivas es eficiente y las casas limitan a los ganadores. Valida
calibración y CLV con dinero de papel durante una temporada antes de arriesgar
capital.
