# Bot de Trading — Señales + Paper Trading

Bot de señales de compra/venta para **cripto** (spot, Binance) y **forex**
(datos de Yahoo Finance), solo largos. Analiza velas con estrategias
configurables y:

- **Manda señales** por consola y opcionalmente por Telegram.
- **Simula las operaciones** (paper trading) para medir si la estrategia gana
  dinero antes de arriesgar capital real.
- **Backtesting** contra datos históricos para validar cambios de estrategia.

> ⚠️ Ningún bot garantiza ganancias. Este proyecto opera en modo simulado por
> diseño: la ejecución con dinero real solo tiene sentido después de semanas de
> paper trading con resultados consistentemente positivos.

## Estrategias

Dos estrategias disponibles (se elige con `strategy.name` en `config.yaml`):

- **meanrev** (activa): reversión a la media en velas de 1h sobre 8 pares.
  Compra cuando el RSI cae bajo 35 con el precio sobre la EMA 200, y vende
  cuando el RSI recupera 55, con stop y target a 2×ATR. ~3-4 señales por
  semana en total.
- **trend**: cruce alcista de EMA 20/50 con filtro EMA 200 y RSI. Muy pocas
  señales; mejor en 4h.

En ambas: 1% del equity de riesgo por operación, máximo 25% del capital por
posición, comisiones del 0.1% por lado incluidas en la simulación.

Comparación de backtests (2 años, 8 pares, velas 1h salvo indicado):

| Estrategia | Operaciones | Retorno prom. | Nota |
|---|---|---|---|
| meanrev RSI 35/55 | 347 | -1.2% | activa: mucha acción, ~breakeven en mercado que cayó 45% |
| meanrev RSI 30/60 | 99 | -0.9% | más selectiva |
| trend 4h (solo BTC/ETH) | 34 | -1.9% | BTC +2.5%, ETH -6.3% |
| trend 1h | 638 | -7.2% | las comisiones y el ruido la destruyen |

## Forex

`config_forex.yaml` corre la misma estrategia meanrev sobre 6 pares mayores
(EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, NZD/USD) con datos gratuitos de
Yahoo Finance — sin cuenta ni broker para señales/backtest/paper:

```bash
python -m bot.backtest --config config_forex.yaml
python -m bot.main --config config_forex.yaml
```

Backtest 2023-10 → 2026-07 (~2.75 años, 1h): **363 operaciones, win rate
57.3%, retorno promedio +1.42% sin apalancamiento**, drawdown máximo -3.8%.
4 de 6 pares positivos (mejor: AUD/USD +4.1% con 65% de aciertos). La
reversión a la media funciona mejor en forex que en cripto porque los pares
mayores lateralizan la mayor parte del tiempo.

### Portafolio compartido y apalancamiento

`--portfolio` simula un solo capital operando todos los pares (interés
compuesto real). Con USD 500 iniciales, 2023-10 → 2026-07:

| Riesgo/trade | Apalanc. máx/posición | $500 → | Max drawdown | Exposición pico |
|---|---|---|---|---|
| 1% | 5x | $642 (+28%) | -13.2% | 16.7x equity |
| 2% (config activa) | 10x | $788 (+58%) | -25.9% | 33.8x equity |
| 5% | 20x | $1.066 (+113%) | -54.4% | 80x equity (irreal en retail) |

```bash
python -m bot.backtest --config config_forex.yaml --portfolio
```

Limitaciones: el costo se modela como spread (~1 pip); no se modela el swap
nocturno ni el límite de margen del broker (retail suele ser 30:1 — los picos
de exposición por encima de eso no serían ejecutables). Para dinero real hace
falta un broker con API (OANDA tiene demo gratuita); el apalancamiento
multiplica ganancias y pérdidas por igual.

## Multi-mercado (forex + metales + índices)

`config_multi.yaml` suma oro, plata, S&P 500 y Nasdaq a los 6 pares de forex
(todos operables como CFDs en el mismo broker). Backtest de portafolio
compartido con USD 500 (2023-09 → 2026-07, riesgo 2%, hasta 10x): **+88%
($500 → $942), 565 operaciones, win rate 58.1%, max drawdown -34.5%**.
El petróleo se probó y quedó excluido (-16.9%, la reversión a la media no
funciona ahí).

```bash
python -m bot.backtest --config config_multi.yaml --portfolio
python -m bot.main --config config_multi.yaml
```

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
# 1. Validar la estrategia con datos históricos (2 años por defecto)
python -m bot.backtest
python -m bot.backtest --symbol BTC/USDT --days 365

# 2. Correr el bot de señales (paper trading)
python -m bot.main           # loop continuo, revisa cada 15 min
python -m bot.main --once    # una pasada (para cron/launchd)
```

El estado del portafolio simulado se guarda en `paper_state.json` (equity,
posiciones abiertas e historial de operaciones).

## Señales por Telegram

1. Crear un bot con [@BotFather](https://t.me/BotFather) y copiar el token.
2. Escribirle un mensaje al bot y obtener el `chat_id` desde
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Completar `telegram.token` y `telegram.chat_id` en `config.yaml`.

## Estructura

```
bot/
├── data.py        # descarga de velas OHLCV (ccxt, API pública)
├── indicators.py  # EMA, RSI, ATR con pandas
├── strategy.py    # lógica de entrada/salida
├── risk.py        # tamaño de posición por riesgo
├── backtest.py    # simulación histórica con métricas
├── paper.py       # portafolio simulado persistido en JSON
├── notifier.py    # consola + Telegram
└── main.py        # loop en vivo
```

## Próximos pasos

- Dejar correr el paper trading 4–8 semanas y revisar `paper_state.json`.
- Ajustar parámetros solo con evidencia del backtest (ojo con sobreoptimizar).
- Si los resultados acompañan: ejecución real vía API de Binance con las
  mismas reglas de riesgo (empezar con montos mínimos).
