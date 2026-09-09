# betbot — Bot de análisis de apuestas deportivas (señales por EV)

Sistema de **detección de valor esperado**: compara la probabilidad real estimada
por un modelo propio contra la probabilidad implícita en las odds de las casas,
ya descontado el margen. Emite **señales**; las apuestas se ponen a mano.

**No ejecuta apuestas automáticamente y no lo hará.**

## Estado

Pipeline completo y probado extremo a extremo (`odds → modelo → EV → alerta`),
con modelos NBA, MLB y fútbol implementados. **Ningún modelo está entrenado con
datos reales todavía** — falta la capa de ingesta histórica. Hasta entonces
`predict()` devuelve `None` por diseño: no se apuesta con un modelo sin entrenar.

## Arranque rápido

```bash
git clone <repo> && cd sports-betting-
cp .env.example .env          # rellena ODDS_API_KEY si vas a escanear en vivo
pip install -e ".[dev]"

python -m betbot.cli demo     # pipeline completo, datos sintéticos, sin red
pytest -q                     # 92 tests
```

El núcleo no tiene dependencias: solo stdlib. `pandas`/`requests` quedan en el
extra `data`, para la ingesta histórica.

## Arquitectura

```
odds/         Proveedores de cotizaciones + eliminación de vig (multiplicative/power/shin)
models/       Un módulo por deporte, todos con la misma interfaz ProbabilityModel
  elo.py        Elo genérico con margen de victoria (base de NBA y MLB)
  nba.py        Elo + encogimiento
  mlb.py        Elo + Pythagorean + ajuste de abridor + techo de probabilidad
  soccer.py     Poisson bivariado con corrección Dixon-Coles
ev/           Motor de EV, Kelly fraccionado y filtros de riesgo
backtest/     Brier, log-loss, calibración, CLV, ROI con error típico
alerts/       Consola y Telegram
storage.py    SQLite: señales, odds de cierre, liquidación
cli.py        demo / scan / report
```

El motor de EV no sabe nada de baloncesto, béisbol ni fútbol: solo consume
`ModelProbabilities`. Añadir un deporte es escribir un módulo nuevo en `models/`.

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

## Lo que falta

1. **Ingesta histórica** (bloqueante para todo lo demás): sin esto no hay
   entrenamiento ni backtest. NBA/MLB vía `nba_api` y Retrosheet/pybaseball;
   fútbol vía FBref/Understat para xG.
2. **Backtest walk-forward** con las métricas ya implementadas — entrenar solo con
   datos anteriores a cada partido, nunca con la temporada completa.
3. **Registro automático de odds de cierre** (un job que corra al inicio de cada
   partido) para que el CLV sea medible.
4. **MLE Dixon-Coles** en fútbol: `fit()` usa estimador de momentos, suficiente
   para validar el pipeline, insuficiente para producción.
5. **NFL**: deliberadamente al final. 17 partidos por temporada es demasiado poco
   para separar señal de ruido con estos métodos.

## Orden recomendado

NBA o MLB primero: mucha data, sin empates, modelos probados. MLB da el mejor
banco de pruebas por volumen (2430 partidos/temporada) a costa de que el abridor
domina la línea. Fútbol después, que añade el empate y baja anotación. NFL al
final, si acaso.

## Advertencia

Ningún modelo aquí tiene ventaja demostrada sobre el mercado. El mercado de
apuestas deportivas es eficiente y las casas limitan a los ganadores. Valida
calibración y CLV con dinero de papel durante una temporada antes de arriesgar
capital.
