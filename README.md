# AutoBTC - Punto a Punto (Single Screen, Dark)

Proyecto generado el 2025-08-14 para Python 3.11.
UI de una sola pantalla con tema oscuro (ttkbootstrap), integración CCXT (spot),
motor con reglas duras, y cliente LLM "dummy" (reglas heurísticas) que respeta límites.

## Instalación (Windows/Powershell)
```
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python .\main.py
```

## Claves
- TEST (sin claves): Confirm LIVE **desactivado**.
- LIVE: exporta `BINANCE_KEY` y `BINANCE_SECRET`. Activa Confirm LIVE y el switch BOT.

> Este es un esqueleto productivo mínimo: listo para arrancar, con snapshot periódico,
> ranking de pares */BTC, y simulación de órdenes. Amplía websockets/ejecución real
> en `exchange_utils.py` y `engine.py`.

## WebSocket
- Conecta a `depth5@100ms` y `aggTrade` por símbolo mediante `websocket-client`.
- Estima `trade_flow.buy_ratio`, `avg_size` y `streak`, y usa el libro WS para `best_bid/ask`, `spread_bps` y `microprice`.
- Si WS aún no emite, el bot usa REST como respaldo.

## Modos SIM/LIVE
- Selector de modo en cabecera; tamaños USD independientes.
- Botón **Min+ (selección)** calcula el mínimo permitido por Binance (MIN_NOTIONAL) para el símbolo seleccionado y añade un 10%.
- Panel de **Claves API** para Binance y ChatGPT; aplica en caliente.
- Panel **LLM** con modelo (gpt-4o por defecto), temperatura, intervalo y máx. acciones.
- Pestañas de **Órdenes abiertas** y **Órdenes cerradas** con distintivos 🟢/🔴 y ⚡/🔧.
- Pestaña de **Información/Razones** muestra por qué no se abren operaciones.


### Cambios recientes
- Paneles siempre visibles (Mercado, Abiertas, Cerradas).
- Botón **Aplicar tamaño**: fija SIM editable y LIVE al **mínimo permitido por Binance** del símbolo seleccionado (campo LIVE bloqueado).
- LLM: solo **Modelo**.
- Gating LLM: no se llama si no hay órdenes abiertas ni candidatos (score/edge/%).
- Razones más explícitas al no operar.
- Botón **Iniciar Testeos** ejecuta 10 variaciones del umbral de oportunidad (50 compras y 50 ventas cada una),
  envía los resultados al LLM para elegir la más prometedora y permite aplicar la ganadora al modo LIVE.
