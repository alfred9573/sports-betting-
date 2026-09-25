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
| **NFL** | `nflverse` | ⚠️ desde la semana 2 (ver 3d) |
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

## 3d. NFL: arranca en la semana 2

`nflverse` se actualiza en cuanto terminan los partidos, así que la NFL es
operable casi desde el principio de temporada. La secuencia:

```bash
# ahora: base historica
python -m betbot.cli ingest --sport nfl --from 2010 --to 2026

# cada martes, tras la jornada
python -m betbot.cli ingest --sport nfl --from 2026 --to 2026 --force
python -m betbot.cli scan --sport nfl
```

La barrera de cobertura bloquea la semana 1 —el modelo no tiene ni un partido de
la temporada nueva— y **se levanta sola** en cuanto ingieres los primeros
resultados. No hay que tocar nada.

### La NFL aguanta mejor el arranque que la NBA

Medido sobre 4.363 partidos (2010-2026):

| Tramo | NFL | NBA (comparación) |
|---|---|---|
| Inicio de temporada | **+0.0456** (74% de su ventaja) | +0.0345 (52%) |
| Media temporada | +0.0481 | +0.0566 |
| Tramo final | +0.0616 | +0.0657 |

La NFL pierde bastante menos ventaja al principio, y su sesgo de calibración en
las primeras jornadas es de solo +0,63%. La razón probable: las plantillas de
fútbol americano son más estables de un año a otro en relación a su impacto, y
la regresión a la media del 33% ya absorbe buena parte del cambio.

**Conclusión práctica: en NFL no hace falta esperar al mes de temporada** como
recomendé para la NBA. Desde la semana 2 el modelo ya rinde a tres cuartos de su
capacidad.

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

```bash
bash scripts/install-cron.sh epl nfl
```

Te enseña exactamente qué va a añadir y pide confirmación antes de tocar nada.
Reinstalarlo reemplaza el bloque anterior sin duplicarlo, y respeta el resto de
tu crontab. Para quitarlo: `bash scripts/uninstall-cron.sh`.

Qué instala:

| Tarea | Frecuencia | Coste en cuota |
|---|---|---|
| `scan` por deporte | 3 veces al día | ~90 créditos/mes por deporte |
| `close` | cada 10 min | solo cuando hay señales por empezar |
| `report` | diario 9:00 | 0 |
| `ingest` semanal | martes 6:00 | 0 (GitHub, no la API) |
| `doctor` | lunes 8:00 | 0 |

Con dos deportes son ~180 créditos de escaneo más los cierres: cabe en los 500
del plan gratuito con margen.

### La reserva de cuota

Si quedan menos de 60 créditos, **`scan` se abstiene solo** y deja pasar únicamente
la captura de cierres. La prioridad es deliberada:

> Una señal que no encuentras es una oportunidad perdida.
> Un cierre que no capturas es una apuesta que **nunca** podrás evaluar.

Quedarse sin cuota a mitad de mes con señales abiertas y sin cierres es el peor
resultado posible: acumulas apuestas que no sirven ni para aprender. Ajustable
con `QUOTA_RESERVE` en `.env`.

### Si las tareas no se ejecutan (macOS)

macOS restringe el acceso a disco de los procesos en segundo plano. Si `crontab -l`
muestra las tareas pero `logs/` sigue vacío:

**Ajustes → Privacidad y seguridad → Acceso total al disco → + → `/usr/sbin/cron`**

(Cmd+Shift+G en el diálogo para escribir la ruta.) Después, comprueba:

```bash
tail -f logs/scan.log
```

### Por qué un envoltorio y no el comando directo

Cron no arranca tu shell: no hay `PATH`, no hay variables de entorno y el
directorio de trabajo es tu home, no el repo. Un comando que funciona en tu
terminal falla en cron por cualquiera de esas tres cosas — y falla en silencio
si no rediriges la salida. `scripts/betbot-cron.sh` se encarga de eso, añade
marca de tiempo a cada ejecución y rota los logs a los 5 MB.

## 5b. Telegram

```bash
python -m betbot.cli test-telegram
```

Si falta configuración, el comando te dice exactamente qué hacer. Resumen:

1. En Telegram, habla con **@BotFather** → `/newbot` → te da un token
2. Pégalo en `.env` como `TELEGRAM_BOT_TOKEN=`
3. **Manda cualquier mensaje a tu bot** (si no, no puede escribirte)
4. Abre `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y busca
   `"chat":{"id":NUMERO`
5. Pégalo como `TELEGRAM_CHAT_ID=`
6. Vuelve a correr `test-telegram`

Con `lineshop` las alertas dejan de ser un lujo: la ventana de una línea
desfasada se cierra en minutos.

## 5c. ¿Merece la pena pagar más cuota? Mídelo antes

### Antes de correrlo: comprueba que hay un libro sharp

La estrategia compara casas blandas contra una **sharp** (Pinnacle, Betfair,
Circa). Sin esa referencia no hay nada que medir: comparar dos blandas entre sí
no dice cuál tiene razón.

Pinnacle suele estar en la región `eu`, no en `us`. En `.env`:

```ini
ODDS_REGIONS=eu
```

O `us,eu` para ver también las casas de cara al público estadounidense — **pero
ojo: cada región multiplica el coste**. Con 3 mercados y 2 regiones son 6
créditos por escaneo en vez de 3.

### Una medición no dice nada. Corre la serie

```bash
bash scripts/survey-dia.sh nfl 12 30 --fondo
```

Un comando y te olvidas: arranca en segundo plano, sobrevive a que cierres la
terminal y en macOS impide que el equipo se duerma a mitad — que es la causa
habitual de que una serie larga aparezca truncada sin explicación.

12 mediciones cada 30 minutos (~36 créditos, ~6 horas). Puedes consultar el
avance en cualquier momento, incluso mientras sigue corriendo:

```bash
bash scripts/survey-resumen.sh
```

Acumula todo en `data/survey.csv` y da el veredicto:

- **Sin libro sharp en ninguna** → revisa `ODDS_REGIONS`, no has medido nada
- **Cero oportunidades en todas** → no hay nada que capturar, ahórrate la cuota
- **Oportunidades en más de la mitad** → apunta a algo estructural
- **Intermitentes** → ruido de sincronización, o ventana real pero estrecha

Para una sola medición suelta:

```bash
python -m betbot.cli survey --sport nfl --log data/survey.csv
```

Cuesta **3 créditos por región configurada** (con `us,eu` son 6) y responde con datos si existe la oportunidad que
`lineshop` busca: cuántas casas cotizan, si hay alguna sharp de referencia,
cuántas discrepancias hay ahora mismo y de qué tamaño.

Cómo interpretarlo:

- **Cero oportunidades por encima del 2%, repetidamente** → no hay nada que
  capturar y ningún plan lo arregla. Ahórrate el dinero.
- **Aparecen y se mantienen a lo largo del día** → la oportunidad es estructural
  y escanear más seguido tiene sentido.
- **Aparecen y desaparecen sin patrón** → estás viendo ruido de sincronización
  entre casas, no una ventaja explotable.

**Es una foto, no una película.** De ahí el script de arriba.

### Dos filtros que evitan diagnósticos falsos

**Partidos lejanos.** Un feed de NFL en septiembre trae la temporada entera. Las
casas publican líneas a semanas vista con márgenes anchos y límites mínimos: ahí
*siempre* parecerá que hay valor, y no lo hay. `survey` solo cuenta lo que empieza
en las próximas 48h (`--horas` lo ajusta).

**Casas inaccesibles.** Pasa la lista de donde puedes apostar de verdad:

```bash
python -m betbot.cli survey --sport nfl --casas bet365,betmgm --log data/survey.csv
```

El survey marca cuáles de tus casas aparecen en el feed, avisa de las que
faltan, y **cuenta solo las oportunidades que caen donde puedes actuar**. Sin
esto el diagnóstico sale inflado justo en la dirección que lleva a pagar una
suscripción.

Si sale repetidamente «NINGUNA discrepancia cae en tus casas», la estrategia no
te sirve — y no por los modelos, sino por dónde puedes apostar. Es un motivo
perfectamente válido para cerrar el proyecto.

### Y una comprobación que decide si algo de esto te sirve

Mira la lista de **casas presentes** que imprime el survey. Si las casas donde
tú puedes apostar de verdad no están ahí, encontrarás valor en sitios que no
puedes usar. The Odds API cubre regiones `us`, `us2`, `uk`, `eu` y `au`; si
apuestas en casas locales de otro mercado, puede que no aparezcan.

Compruébalo antes de pagar nada: es la diferencia entre una estrategia
accionable y un ejercicio teórico.

## 5d. La cuenta de la cuota

`lineshop` cubre tres mercados (totales, hándicap, moneyline) y **The Odds API
cobra por mercado**: 3 créditos por escaneo en vez de 1.

| Configuración | Créditos/mes |
|---|---|
| Solo modelo, 1 deporte | ~90 |
| Solo modelo, 2 deportes | ~180 |
| + lineshop cada 2h, 1 deporte | **~1.170** |

**El plan gratuito son 500/mes: con lineshop activo no cabe.** Opciones:

- Subir el intervalo de lineshop (cada 6h ≈ 360/mes)
- Un solo deporte
- Pasar a un plan de pago

Y hay una tensión real que conviene ver: `lineshop` **necesita frecuencia** —
una línea desfasada dura minutos— así que espaciarlo mucho le quita justo lo que
lo hace funcionar. Si la estrategia te convence, el plan de pago es parte del
coste de probarla en serio.

## 5e. Archivar líneas: construir el histórico que nadie vende

Dos ideas se quedaron sin probar por la misma razón —no existen datos
históricos que comprar—: las props de jugador (nadie publica las líneas
pasadas) y el line shopping (los mirrors de odds multi-casa tienen las columnas
de cuotas recortadas). `collect` resuelve eso por la vía lenta: guarda cada
precio que ve, todos los días, hasta que haya muestra.

```bash
# Un barrido: 3 mercados x N regiones. Con ODDS_REGIONS=us,eu son 6 créditos,
# no 3: la API multiplica por mercados Y por regiones.
.venv/bin/python -m betbot.cli collect --sport nfl

# Ver qué llevas acumulado (gratis, no toca la API)
.venv/bin/python -m betbot.cli collect --stats

# Props: estima el coste ANTES de gastar
.venv/bin/python -m betbot.cli collect --props --dry-run --sport nfl
```

`collect` **no apuesta, no alerta y no genera picks**. Solo archiva. Si esperas
que salga un pick de aquí, no va a pasar; lo que hace es permitir que dentro de
unos meses se pueda hacer una pregunta que hoy no tiene respuesta posible.

**Props y tu plan.** Las props no vienen en el feed de liga: hay que pedirlas
partido a partido, y el plan gratuito normalmente no las incluye. Corre primero
el `--dry-run`: te dice cuántos créditos costaría. Si al quitar `--dry-run`
recibes un 422, ese es el plan diciendo que no las cubre, y el comando corta
ahí en vez de quemar cuota repitiendo el mismo error diez veces.

**Automatizarlo.** Dos barridos al día por deporte son ~180 créditos/mes *por
región*: con `us,eu` son ~360, y eso ya no entra en el plan gratuito de 500 si
corres cualquier otra cosa. El instalador lee tu `ODDS_REGIONS` y te imprime el
total antes de tocar nada:

```bash
bash scripts/install-cron.sh --colecta nfl
```

Si no cabe: baja a un barrido diario, o reduce `ODDS_REGIONS` a `us` en el
`.env`. Ojo con lo segundo — quitar `eu` te quita casas del archivo, incluidos
los exchanges, que son la mejor referencia de precio sin vig que hay en el feed.

**Cuándo mirar los datos.** No antes de varios cientos de eventos ya jugados
con línea de apertura y de cierre. En NFL eso son meses. Mirar antes es leer
ruido y convencerse de algo falso, que es exactamente el error que este
proyecto lleva evitando desde el principio.

## 5f. Props: estadísticas de jugador y validación del modelo

```bash
# 27 temporadas de estadísticas semanales de jugador (~500k líneas, ~1 min)
.venv/bin/python -m betbot.cli ingest-players --from 1999 --to 2026

# estado de la base
.venv/bin/python -m betbot.cli ingest-players --stats

# validar el modelo (tarda ~1-2 min, no toca la API)
.venv/bin/python -m betbot.cli backtest-props --calibracion

# anytime TD: compara contra la tasa base de los titulares de su posicion
.venv/bin/python -m betbot.cli backtest-anytime-td
```

Durante la temporada, refrescar solo el año en curso es suficiente y es
idempotente:

```bash
.venv/bin/python -m betbot.cli ingest-players --from 2026 --to 2026
```

**Cómo leer la salida, que es donde está la trampa.** `backtest-props` mide si
el modelo describe bien al jugador: si dice 30%, ¿pasa el 30% de las veces? Los
deciles del PIT deben rondar 10.0. **No** mide si le ganas al mercado — para eso
harían falta líneas históricas de props, que no existen.

El Brier de ~0.23 frente al 0.25 de una moneda **no es señal de ventaja**: las
líneas sintéticas se colocan a ±15% de la propia proyección del modelo, así que
por construcción las separa bien. Mide consistencia interna. Si algún día lees
ese número como rendimiento esperado, te estarás engañando.

**Lo que sí es real y conviene recordar:** el modelo está condicionado a que el
jugador juegue (solo hay datos de quien registró alguna estadística). En la
mayoría de casas una prop se anula si el jugador no juega, así que eso coincide
con cómo se liquida — revisa la regla de tu casa. El sesgo que queda es más
chico: un suplente que entra sin registrar nada puede no tener fila, y el modelo
ve menos ceros de los reales, lo que infla los overs de jugadores de poco uso.
Y `player_receptions` sobreestima
en la cola alta (dice 63%, pasa 58%) de forma estable en dos eras distintas: el
código lo marca como `COLA_ALTA_DUDOSA` en vez de taparlo con una corrección que
no funciona.

## 5g. NBA: estadísticas de jugador y props

Mismo modelo que las props de NFL, otra fuente (hoopR, 2002-actual). Los
archivos vienen en formato parquet, así que hace falta una librería extra, una
sola vez:

```bash
.venv/bin/pip install pyarrow
```

Luego:

```bash
# 25 temporadas, ~850k filas, ~1 minuto
betbot ingest-players --sport nba

# validar el modelo (~2-3 minutos, no toca la API)
betbot backtest-props --sport nba --calibracion
```

**Cómo se nombran las temporadas, porque confunde:** NBA usa el año en que
TERMINA la temporada. La 2026 es la 2025-26, y la que arranca en octubre es la
**2027**. NFL usa el año en que empieza. `--actual` resuelve eso solo: siempre
pide la temporada en curso de cada liga.

**Qué tiene NBA que NFL no:** el archivo trae a toda la plantilla, incluidos los
que no jugaron, con sus minutos. El modelo usa "jugó = minutos > 0", que es
exactamente cuando una casa NO anula la prop. En NFL eso no se puede.

**Recolección de líneas de NBA y el presupuesto.** El instalador ya tiene el
horario de NBA (apertura a las 9:00 y cierres antes de las tandas de 19:00 y
22:00 de la costa este), pero NFL + NBA no caben juntos en el plan gratuito con
dos regiones:

| Configuración | Créditos/mes |
|---|---|
| NFL, `us,eu` | ~312 |
| NFL + NBA, `us,eu` | ~852 (no cabe) |
| NFL + NBA, solo `us` | ~426 |

Quitar `eu` te deja sin casas europeas ni exchanges, que son la mejor referencia
de precio sin margen. Es una decisión tuya; el instalador te muestra la cuenta
antes de tocar nada:

```bash
bash scripts/install-cron.sh --solo-colecta nfl nba
```

## 5h. Apuestas en papel sobre props

Es el paso que separa "el modelo está calibrado" de "el modelo le gana a la
casa". Cada sábado toma las props reales de unos pocos partidos, calcula la
probabilidad del modelo, apunta las que tendrían valor esperado positivo **sin
apostar nada**, y después del partido las califica con el resultado real y con
el precio de cierre.

```bash
# instalar (reemplaza el bloque de cron anterior; muestra el costo antes)
bash scripts/install-cron.sh --solo-colecta --props=3 nfl

# ver cómo va, en cualquier momento (no gasta créditos)
betbot papel --sport nfl --resumen
```

Con `--props=3` son ~466 créditos al mes contando la colecta normal: cabe en
el plan gratuito, pero justo. NBA con props no cabe sin un plan de pago.

**Cómo está hecho para no engañarte:**

- El precio que se "toma" es la **mediana entre casas**, no el mejor. El mejor
  de 28 casas casi siempre es de una casa donde no tienes cuenta.
- Se decide el sábado y se compara contra el precio **justo antes del
  partido**. Esa diferencia es el CLV.
- Si el jugador no juega, la apuesta queda **nula**, como en la casa.
- No se apuntan líneas donde se sabe que el modelo se pasa (anytime TD sobre
  40%, recepciones/asistencias/triples/TD de pase sobre 60%), ni EV mayores a
  25%: casi siempre son un error de nombre o una línea vieja.
- Si a las estadísticas les falta más de una semana, no se apunta nada.

**Avisos por Telegram.** Con Telegram configurado en el `.env`, el cron manda
un mensaje cada sábado con lo que apuntó (o con que no encontró líneas, que es
la señal de que falló el barrido) y otro cada vez que califica. Todos llevan
"PAPEL: no son picks, no apostar". Si un sábado no llega nada, algo se rompió:
revisa `logs/papel.log`. Para comprobar la configuración:

```bash
betbot test-telegram
```

**Qué mirar y cuándo:** primero el **CLV medio**. Si durante las primeras
~100 apuestas es negativo, el modelo no le gana al cierre y el ROI no importa.
Si es positivo y se mantiene hasta ~200-300 apuestas, ahí se puede hablar de
dinero real con montos chicos. **El ROI de 30 o 50 apuestas es casi todo
suerte**: el comando te lo recuerda.

## 5i. Correrlo en un servidor en vez del Mac

Con el Mac, cada barrido que coincide con la tapa cerrada se pierde, y el de
cierre del domingo es justo el que más importa. Un servidor que no se apaga lo
resuelve. Lo que hay que saber antes:

- **Las horas se convierten solas.** Los horarios están escritos en hora de
  Ciudad de México y el instalador los pasa a la hora del servidor (en UTC, +6
  horas, con el cambio de día cuando cruza la medianoche). Si el servidor tiene
  horario de verano, avisa: mejor que esté en UTC.
- **No toca lo que ya corre ahí.** El instalador y el desinstalador solo
  reemplazan el bloque `# --- betbot ---` del crontab.
- **Consumo:** entrenar los modelos de NFL ocupa ~700 MB de memoria durante
  ~20 segundos, un par de veces por semana, con prioridad baja (`nice`). Revisa
  antes con `free -h` que haya al menos 1.5 GB disponibles.
- **Nunca los dos a la vez.** Si el cron sigue en el Mac y también en el
  servidor, los créditos se gastan dos veces y la cuota se acaba a mitad de mes.

En el servidor:

```bash
python3 --version        # tiene que ser 3.11 o más
free -h                  # columna "available": al menos 1.5G
cd ~ && git clone https://github.com/alfred9573/sports-betting-.git
cd ~/sports-betting- && bash scripts/setup.sh
```

Desde el Mac, copia tu configuración y el archivo de líneas (cambia la IP):

```bash
scp ~/sports-betting-/.env root@IP_DEL_SERVIDOR:~/sports-betting-/.env
scp ~/sports-betting-/data/odds_archive.db root@IP_DEL_SERVIDOR:~/sports-betting-/data/
```

En el servidor otra vez:

```bash
echo 'betbot() { (cd ~/sports-betting- && .venv/bin/python -m betbot.cli "$@") }' >> ~/.bashrc
source ~/.bashrc
betbot ingest-players
bash scripts/install-cron.sh --solo-colecta --props=3 nfl
```

Y al final, en el Mac, quita su cron:

```bash
cd ~/sports-betting- && bash scripts/uninstall-cron.sh
```

Para ver resultados: `ssh` al servidor y `betbot papel --sport nfl --resumen`.

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
