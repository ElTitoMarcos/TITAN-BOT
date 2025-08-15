from typing import Dict
import math

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _nz(x, eps=1e-12):
    return x if abs(x) > eps else eps

def _norm_trend(pct: float) -> float:
    return 0.5 + 0.5 * _clamp(pct / 20.0, -1.0, 1.0)

def compute_score(features: Dict) -> float:
    """
    Calcula un score (0..100) ponderado según:
    1. Tendencias multi‑marco (semanal, diaria, horaria, minutos).
    2. Presión inmediata del libro de órdenes (top bid vs top ask).
    3. Flujo de órdenes reciente (ratio de compras vs ventas).
    4. Momentum del precio en la ventana configurada.
    5. Profundidad agregada del libro.
    6. Penalización por spread amplio.
    7. Penalización por micro-volatilidad.

    La primera consideración tiene el mayor peso y la última el menor.
    """
    pct = abs(float(features.get("pct_change_window", 0.0)))
    momentum = _clamp(pct / 2.0, 0.0, 1.0)

    depth_buy = float(features.get("depth_buy", 0.0))
    depth_sell = float(features.get("depth_sell", 0.0))
    depth = max(0.0, depth_buy + depth_sell)
    depth_quality = math.log10(1.0 + depth) / 6.0

    best_bid_qty = float(features.get("best_bid_qty", 0.0))
    best_ask_qty = float(features.get("best_ask_qty", 0.0))
    pressure_raw = best_bid_qty / _nz(best_bid_qty + best_ask_qty)
    orderbook_pressure = _clamp(abs(pressure_raw - 0.5) * 2.0, 0.0, 1.0)

    buy_ratio = float(features.get("trade_flow_buy_ratio", 0.5))
    flow_bias = _clamp(abs(buy_ratio - 0.5) * 2.0, 0.0, 1.0)

    micro_vol = float(features.get("micro_volatility", 0.0))
    micro_vol_pen = 1.0 / (1.0 + 50.0 * micro_vol)

    spread = float(features.get("spread_abs", 0.0))
    price = abs(float(features.get("mid", 0.0)))
    tick = max(price * 1e-6, 1e-8)
    spread_penalty = 1.0 / (1.0 + (spread / _nz(tick)))

    trend_w = _norm_trend(float(features.get("trend_w", 0.0)))
    trend_d = _norm_trend(float(features.get("trend_d", 0.0)))
    trend_h = _norm_trend(float(features.get("trend_h", 0.0)))
    trend_m = _norm_trend(float(features.get("trend_m", 0.0)))

    w = features.get("weights") or {
        "trend_w": 25,
        "trend_d": 20,
        "pressure": 15,
        "flow": 12,
        "trend_h": 10,
        "depth": 8,
        "trend_m": 5,
        "momentum": 3,
        "spread": 1,
        "microvol": 1,
    }
    score = (
        trend_w * w.get("trend_w", 0) +
        trend_d * w.get("trend_d", 0) +
        orderbook_pressure * w.get("pressure", 0) +
        flow_bias * w.get("flow", 0) +
        trend_h * w.get("trend_h", 0) +
        depth_quality * w.get("depth", 0) +
        trend_m * w.get("trend_m", 0) +
        momentum * w.get("momentum", 0) +
        spread_penalty * w.get("spread", 0) +
        micro_vol_pen * w.get("microvol", 0)

    )
    return float(_clamp(score, 0.0, 100.0))
