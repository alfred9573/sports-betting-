# Runbook: de repo a señales con dinero de papel

Guía operativa para poner el bot a funcionar en tu máquina. Todo lo que sigue
requiere red abierta, así que no se pudo ejecutar en el entorno donde se
desarrolló — es lo que te toca a ti.

**Nada de esto implica apostar dinero real.** El objetivo de este documento es
llegar a tener CLV medido en papel, que es la única evidencia que justifica
arriesgar capital después.

---

## 0. El bloqueo que tienes que resolver primero

Los modelos están validados, pero **los datos de NBA terminan en 2015**. El
dataset de FiveThirtyEight, que es con el que se midió el edge del 11,5%, no se
actualiza desde entonces.

```
$ python -m betbot.cli doctor
  NBA    ultimo 2015-06-16 (4103d, OBSOLETO) | modelo OK
```

"modelo OK" significa que carga y predice. **No significa que sirva**: los
ratings describen plantillas de hace una década. Escanear partidos de hoy con
eso produce señales sin ningún fundamento, y no da error.

Para operar NBA en vivo necesitas datos recientes, y la única vía implementada
es el adaptador de ESPN — que **nunca se ha ejecutado contra la API real**
(el entorno de desarrollo tenía bloqueado ese host). Validarlo es el paso 3.

Ligas sin este problema: **MLB** (Retrosheet llega a 2025), **Liga MX**
(footballcsv llega a 2024-25), **NFL** (nflverse llega a 2025). Pero MLB tiene
el peor edge medido (1,7%), así que no es donde quieres empezar.

---

## 1. Instalación (10 minutos)

### Dónde se ejecuta todo esto

En **una terminal de tu propia computadora**, no en un chat ni en un navegador.

- **macOS**: Cmd+Espacio, escribe `Terminal`, Enter.
- **Windows**: tecla Windows, escribe `PowerShell`, Enter. (Necesitas Python
  instalado desde python.org marcando "Add to PATH".)
- **Linux**: Ctrl+Alt+T.

El bot tiene que vivir en tu máquina porque necesita red abierta hacia las APIs
de odds y porque el cron debe correr todos los días acumulando historial. Un
entorno temporal no sirve: al destruirse se pierde el CLV acumulado, que es
justamente lo que estás midiendo.

### Instalación

```bash
git clone https://github.com/alfred9573/sports-betting-
cd sports-betting-
git checkout claude/sports-betting-analysis-bot-abfii9
bash scripts/setup.sh
```

`setup.sh` comprueba la versión de Python, crea el entorno virtual, instala
dependencias, corre los tests y ejecuta el demo. Si algo falta, te dice qué.

Si `demo` imprime una señal con su EV y su stake, el pipeline funciona.

### Cómo llamar al bot a partir de ahora

El entorno virtual mantiene las dependencias del bot separadas del Python del
sistema. Desde la carpeta del repo:

```bash
.venv/bin/python -m betbot.cli doctor      # macOS / Linux
.venv\Scripts\python -m betbot.cli doctor  # Windows
```

En el resto de este documento verás `python -m betbot.cli ...`; sustitúyelo por
la ruta de arriba, o activa el entorno una vez por sesión de terminal con
`source .venv/bin/activate` (macOS/Linux) y entonces `python` ya es el correcto.

---

## 2. API key y configuración

1. Regístrate en https://the-odds-api.com (plan gratuito: 500 requests/mes).
2. Copia la key.

```bash
cp .env.example .env
```

Edita `.env`:

```ini
ODDS_API_KEY=tu_key_aqui
BANKROLL=1000          # unidades de papel, no dinero real todavía
KELLY_FRACTION=0.25
MIN_EV=0.03
MAX_STAKE_PCT=0.02
```

Comprueba que lo lee y prueba la conexión (cuesta **1 crédito** de los 500):

```bash
python -m betbot.cli doctor --api
```

Qué mirar en la salida:

- `conexion OK | N eventos NBA` → la key funciona.
- `cuota restante: 499` → confirma el consumo real por llamada.
- **`nombres de equipo en el feed:`** → esto es lo más importante de todo el
  paso. Compara esos nombres con los canónicos de `src/betbot/ingest/teams.py`.
  Si el feed dice `"LA Clippers"` y tu tabla espera `"Los Angeles Clippers"`,
  el modelo devolverá `None` para todos los eventos y **el bot callará sin dar
  un solo error**. Es el fallo más caro del sistema y por eso el `doctor` te
  pone los nombres delante.

Si aparece algún nombre sin alias, añádelo a `teams.py` y vuelve a correr los
tests.

### Cuidado con la cuota

Cada llamada cuesta `n_mercados × n_regiones` créditos, **no 1**. Con
`h2h+spreads+totals` en `us+eu` son 9 créditos por llamada: a 3 deportes cada 30
minutos agotas el mes en día y medio. Los defaults (`h2h`, región `us`) están
puestos para no quemarla.

---

## 3. Qué puedes operar HOY

No todas las fuentes están al día, y el modelo no puede predecir una temporada
de la que no tiene datos (la barrera de cobertura lo bloquea). Estado real:

| Liga | Fuente con temporada en curso | ¿Operable hoy? |
|---|---|---|
| **Premier League** | `openfootball` | ✅ sí |
| La Liga, Serie A, Bundesliga, Ligue 1 | `openfootball` | ⚠️ sin preset calibrado |
| NBA | `hoopr` (hasta jun 2026) | ⛔ hasta que arranque y se juegue |
| NFL | `nflverse` | ⛔ hasta que se jueguen partidos |
| **Liga MX** | ninguna encontrada | ⛔ ver abajo |
| MLB | Retrosheet llega a 2025 | ⛔ falta 2026 |

### Liga MX: sin fuente actual

`footballcsv/mexico` se quedó en 2024-25 y no hay ningún espejo en GitHub con la
temporada en curso. Las alternativas son de pago (API-Football cubre Liga MX) o
la API de ESPN, que devuelve 403 desde muchas redes. Hasta resolverlo, la Liga MX
sirve para validar metodología pero no para operar.

## 3b. Premier League (lo que sí funciona ahora)

```bash
python -m betbot.cli ingest --sport epl --source openfootball --from 2016 --to 2026
python -m betbot.cli doctor
python -m betbot.cli backtest --sport epl
```

✅ **Bien si:** `doctor` dice `al dia` y el backtest da RPS ≈ 0.201 frente a un
baseline de 0.233.

**No mezcles fuentes de fútbol.** Si ya ingeriste `engsoccerdata`, sus temporadas
se solapan con `openfootball` y los partidos entrarían dos veces — cada resultado
contaría el doble y el modelo exageraría las diferencias entre equipos. `ingest`
te avisa si lo detecta; lo más limpio es borrar la BD y reingerir con una sola.

## 3c. Datos recientes de NBA

```bash
python -m betbot.cli ingest --sport nba --source hoopr --from 2016 --to 2026
```

**El `--source hoopr` es obligatorio.** Sin él se usa el dataset de
FiveThirtyEight, que se congeló en 2015 y produciría un modelo con ratings de
plantillas de hace una década — sin dar ningún error.

`hoopr` es el espejo en GitHub de los datos de ESPN: los mismos partidos, pero
en un solo CSV en vez de una petición por día. Tarda segundos, no minutos.

Verifica:

```bash
python -m betbot.cli doctor
python -m betbot.cli backtest --sport nba
```

✅ **Bien si:** `doctor` dice `al dia` (o `fuera de temporada` si es verano) y el
backtest bate al baseline: log-loss ≈ 0.619 frente a 0.684.

### Sobre ESPN en vivo

El adaptador `--source espn` consulta la API de ESPN día a día. **Devuelve 403
desde muchas redes**, incluso con cabeceras de navegador. Se conserva porque
cubre deportes que `hoopr` no, pero para NBA usa `hoopr`: son los mismos datos
sin el bloqueo.

## 4. Primer escaneo real

```bash
python -m betbot.cli scan --sport nba
```

`scan` entrena el modelo con lo que haya en `data/games.db` y lo cruza contra las
odds en vivo. Resultados posibles:

- **Ninguna señal** → es lo normal y lo esperable. Con `MIN_EV=3%` la mayoría de
  escaneos no encuentra nada. El mercado es eficiente; si encontraras valor en
  cada partido, el bug estaría en tu modelo.
- **Una o dos señales** → míralas con lupa. ¿La cuota es real? ¿El nombre del
  equipo es correcto? ¿El EV es creíble o absurdo (>20% casi siempre es un bug)?
- **Muchas señales** → **casi seguro es un bug.** Revisa que el devig esté
  actuando y que los nombres de equipo casen. Un modelo bien calibrado contra un
  mercado eficiente encuentra valor raramente.

### La trampa de la pretemporada

El caso real que motivó la barrera de cobertura: con datos hasta junio y
partidos de octubre, el bot produjo **16 señales con EV de hasta +79,8%**, todas
con el modelo más confiado que el mercado.

No se parecía a un fallo. Se parecía exactamente a lo que uno querría ver si el
bot funcionara.

La causa: un Elo entrenado hasta junio no sabe **nada** del verano — draft,
traspasos, agencia libre, lesiones. El mercado sí, y ya lo tiene en el precio.
El modelo sigue creyendo que las plantillas son las de la final, y cuando su
visión choca de frente con la del mercado, interpreta esa diferencia como valor.

`scan` ahora bloquea esos partidos y te dice qué ingerir. La señal de alarma que
debes memorizar: **muchas señales, EV enorme, todas en la misma dirección.**

### Y aunque tengas datos de la temporada: espera

Medido sobre 14.168 partidos, la ventaja del modelo según lo avanzada que esté
la temporada:

| Partidos jugados | Mejora sobre baseline |
|---|---|
| Primeros 100 | +0.0345 |
| 101-300 | +0.0566 |
| A partir de 301 | +0.0657 |

**El modelo tiene la mitad de ventaja en las primeras semanas.** Los ratings
todavía arrastran la temporada anterior y las plantillas han cambiado. Si vas a
apostar de verdad, deja pasar el primer mes.

Las señales se guardan en `data/betbot.db` y no se repiten en escaneos
sucesivos.

---

## 5. Cron: escaneo y captura de cierre

La captura de cierre es **lo que hace medible el CLV**, y el CLV es lo único que
te dirá si tienes ventaja antes de que pasen dos temporadas.

```bash
crontab -e
```

```cron
# Escaneo cada 2 horas (ajusta a tu cuota)
0 */2 * * * cd /ruta/sports-betting- && .venv/bin/python -m betbot.cli scan --sport nba >> logs/scan.log 2>&1

# Captura de línea de cierre cada 10 minutos — NO lo espacies más
*/10 * * * * cd /ruta/sports-betting- && .venv/bin/python -m betbot.cli close >> logs/close.log 2>&1

# Reporte diario
0 9 * * * cd /ruta/sports-betting- && .venv/bin/python -m betbot.cli report >> logs/report.log 2>&1
```

```bash
mkdir -p logs
```

**Presupuesto de cuota**: escaneo cada 2h = 12 llamadas/día = 360/mes. Más la
captura de cierre, que solo llama cuando hay señales pendientes. Con 500
requests/mes vas justo con un solo deporte. Si te quedas corto, espacia el
escaneo a cada 3-4 horas; **nunca espacies `close`**, porque cada cierre perdido
es una apuesta que jamás podrás evaluar.

Vigila esto en el reporte:

```
N senales se quedaron sin cierre capturado (no evaluables por CLV).
```

Si ese número crece, el cron no corre con suficiente frecuencia y te estás
quedando ciego sin enterarte.

---

## 6. Dinero de papel: qué mirar y cuándo decidir

Apunta las señales en papel. No muevas dinero real todavía.

```bash
python -m betbot.cli report
```

### La regla de decisión

Mira **CLV, no ROI**. Con un edge del 3% y cuotas ~2.00, el ROI necesita del
orden de 4400 apuestas para separarse de cero a dos sigmas — varias temporadas.
El CLV da señal con 100-200.

Cuando llegues a **100-200 señales con cierre capturado**:

| Resultado | Qué significa | Qué hacer |
|---|---|---|
| CLV medio **positivo** y `significant` | Le ganas al precio de cierre. Es la mejor evidencia disponible de ventaja real. | Considera capital real, empezando pequeño. |
| CLV medio **negativo** | El edge sobre baseline era real pero insuficiente frente al vig. | **Para.** Mejor saberlo en papel. |
| CLV **no concluyente** | Muestra insuficiente. | Sigue acumulando. No interpretes el ROI. |

El campo `veredicto` del reporte ya aplica el test a dos sigmas por ti.

### Trampa a evitar

Un ROI positivo con CLV negativo es **suerte, no ventaja**, y se revierte. Es
exactamente el escenario en el que un bot parece funcionar durante dos meses y
luego devuelve todo. Si las dos métricas se contradicen, hazle caso al CLV.

---

## 7. Mantenimiento

```bash
# Semanal: refrescar resultados para que los ratings no envejezcan
python -m betbot.cli ingest --sport nba --from 2024 --to 2025 --force

# Antes de cualquier decisión sobre dinero real
python -m betbot.cli doctor
python -m betbot.cli backtest --sport nba
```

`doctor` avisa cuando los datos llevan más de 30 días sin actualizarse y marca
como OBSOLETO cualquier cosa por encima de un año.

---

## Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `scan` nunca da señales | Nombres de equipo que no casan | `doctor --api` y compara con `teams.py` |
| Datos de NBA obsoletos tras ingerir | Olvidaste `--source espn` | Sin él se usa el dataset que acaba en 2015 |
| `No hay datos historicos de X` | Falta ingerir | `betbot ingest --sport X` |
| EV absurdos (>20%) | Devig roto o nombres mal casados | Revisa que el mercado traiga todas sus patas |
| `cuota agotada` | Presupuesto mensual consumido | Espacia el escaneo; nunca `close` |
| `CERTIFICATE_VERIFY_FAILED` | Python en macOS sin certificados | `.venv/bin/pip install certifi` |
| `HTTP 403` en una fuente | Filtro por User-Agent o bloqueo regional | Ver paso 3; prueba la URL en el navegador |
| CLV vacío en el reporte | `close` no está en cron | Añádelo cada 10 minutos |
| Muchas señales de golpe | Casi seguro un bug | No apuestes; investiga primero |

---

## Lo que este runbook no cubre

- **Límites de casa.** Las casas limitan a los ganadores. Si el bot funciona,
  ese es el siguiente problema, y es un problema de gestión de cuentas, no de
  software.
- **Impuestos y legalidad** en tu jurisdicción.
- **Gestión emocional de la varianza.** Con Kelly 0.25 y cuotas ~2.00, rachas
  perdedoras de 10 apuestas seguidas son estadísticamente normales.
