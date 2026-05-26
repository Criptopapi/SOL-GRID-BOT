# SOL/USDC Grid Bot

Bot automático de grid trading para SOL/USDC en Binance con análisis IA.

## Variables de entorno requeridas en Railway

| Variable | Descripción |
|----------|-------------|
| `BINANCE_API_KEY` | Tu API Key de Binance |
| `BINANCE_API_SECRET` | Tu API Secret de Binance |
| `ANTHROPIC_API_KEY` | Tu API Key de Anthropic (Claude) |
| `CAPITAL_USDC` | Capital en USDC (default: 60) |

## Endpoints

- `GET /` — Estado del servidor
- `GET /state` — Estado completo del bot (precio, órdenes, PNL, log)
- `GET /price` — Precio actual de SOL/USDC
- `POST /analyze` — Analizar mercado con IA y obtener parámetros
- `POST /start` — Iniciar el bot
- `POST /stop` — Detener el bot y cancelar órdenes
