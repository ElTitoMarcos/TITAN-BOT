"""Prompts estáticos usados por el cliente LLM."""

# Cabecera global que se antepone a todos los prompts enviados al LLM.
PROMPT_P0 = (
    "Proyecto TITAN-BOT. Módulo 'Testeos Masivos' con 10 bots concurrentes. "
    "Usa datos de mercado en tiempo real (snapshot REST + diffs WS), simula fills "
    "contra el order book, respeta rate limits y muestra logs del LLM en la UI. "
    "Código claro, modular, testeable y con manejo de errores."
)

PROMPT_INICIAL_VARIACIONES = """
SISTEMA: Eres experto en microestructura y market-making spot en Binance (pares XXXBTC). Tarea: generar 10 variaciones de una estrategia base que compra en nivel con acumulación de bids y vende +1 tick, con filtros: beneficio > comisiones (compra+venta), volumen ≥ 5 BTC/24h y monitoreo del libro para mover/cancelar órdenes ante cambios.
REQUISITOS:
- 10 variaciones distintas entre sí (sin duplicados lógicos).
- Cambia exactamente 1–3 elementos por variación: umbrales de desequilibrio, reglas de entrada/salida, ventana de ranking, límites de exposición, tamaño de orden, cancel/replace, timeout de venta, criterio de venta al precio de compra ante caída del 15%, etc.
- Mantén el espíritu del método original (venta +1 tick) aunque se permitan “+k_ticks con max_wait_s”.
FORMATO DE SALIDA:
Devuelve un JSON array con 10 objetos, cada uno con:
{
  "name": "var-<corto-unico>",
  "mutations": {
    "order_size_usd": "auto|fijo|%balance",
    "buy_level_rule": "accum_bids|best_ask_if_imbalance",
    "sell_rule": "+1_tick|+k_ticks|max_wait_s",
    "imbalance_buy_threshold_pct": <15-40>,
    "cancel_replace_rules": {"enable": true, "max_moves": 0-5, "min_depth_ratio": 0.4-0.9},
    "pair_ranking_window_s": <10-120>,
    "min_vol_btc_24h": <5-50>,
    "commission_buffer_ticks": <1-3>,
    "risk_limits": {"max_open_orders": 1-5, "per_pair_exposure_usd": 10-500}
  }
}
CONSTRICCIONES:
- Sin dos variaciones con el mismo set efectivo de mutations.
- Sin ML.
- Todas aplicables a cualquier XXXBTC independientemente del precio (usar increments del exchange si aplica).
Valida que el JSON sea parseable.
"""

PROMPT_ANALISIS_CICLO = """
Te paso un resumen del ciclo con 10 bots. Para cada bot: mutations, stats (orders, pnl,
pnl_pct, win_rate, avg_hold_s, avg_slippage_ticks, timeouts, cancel_replace_count),
top-3 pares por PnL y distribución de resultados por hora.
Tarea: Elige UN ganador priorizando PNL, luego estabilidad (menos timeouts y slippage),
después win_rate, luego menor avg_hold_s y finalmente menor cancel_replace_count. Puedes
elegir un bot que despunte claramente en un aspecto clave aunque no tenga el mayor PNL.
Devuelve JSON:
{ "winner_bot_id": <int>, "reason": "<breve explicación>" }
El JSON debe ser parseable. Nada más.
"""

PROMPT_NUEVA_GENERACION_DESDE_GANADOR = """
Base (JSON mutations) del bot ganador:
<PEGAR_JSON_WINNER>
Genera 10 NUEVAS variaciones cercanas (mutaciones locales pequeñas), todas distintas entre sí y distintas a cualquier variación previa (te paso fingerprints si los hay). Respeta:
- Sin ML.
- Par BTC.
- Regla central de venta +1 tick puede extenderse a +k_ticks con max_wait_s, siempre cubriendo comisiones.
Formato: igual que el prompt inicial (name + mutations). Devuelve JSON parseable.
Evita duplicados: usa fingerprints (hashes) de conjuntos de parámetros que te paso.
"""

PROMPT_META_GANADOR = """
Te paso una lista de ganadores históricos. Cada elemento incluye cycle, bot_id,
mutations y stats (orders, pnl, pnl_pct, wins, losses, runtime_s).
Elige UN meta-ganador con criterio multi-métrica: prioriza pnl y pnl_pct, pero
también win_rate alto y estabilidad. Penaliza pérdidas o comportamientos
inconsistentes.
Devuelve JSON: { "bot_id": <int>, "reason": "<breve explicación>" }.
El JSON debe ser parseable. Nada más.
"""

PROMPT_ANALISIS_GLOBAL = """
Te paso un resumen global con métricas por mutación, tendencias,
mejores pares y estabilidad general. Propón 3-5 cambios accionables
para mejorar el sistema de testeos y trading. Devuelve JSON:
{
  "changes": ["<breve cambio 1>", "<breve cambio 2>", ...]
}
El JSON debe ser parseable. Nada más.
"""

PROMPT_PATCH_FROM_CHANGES = """
Te paso una lista de cambios accionables en formato JSON. Genera un patch
unificado (diff) que implemente de manera aproximada esas sugerencias.
Devuelve solo el diff en texto plano sin explicación adicional.
"""
