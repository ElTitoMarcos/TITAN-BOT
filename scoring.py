from typing import Dict
import math

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _nz(x, eps=1e-12):
    return x if abs(x) > eps else eps

def compute_score(features: Dict) -> float:
    """
    Nuevo scoring (0..100) con normalización simple:
    - momentum: |pct_change| en ventana (0..2% mapeado a 0..1)
    - depth_quality: log10(1 + (depth_buy+depth_sell))
    - ob_imbalance: favorece 0.6..0.8 compradores o 0.2..0.4 vendedores (campana)
    - micro_volatility: penalización suave por volatilidad excesiva
    - spread_penalty: penaliza spreads amplios frente al tick proxy
    Pesos por defecto: momentum 30, depth 25, imbalance 20, spread 15, microvol 10
    """
    pct = abs(float(features.get("pct_change_window", 0.0)))
    # 0..2% -> 0..1
    momentum = _clamp(pct / 2.0, 0.0, 1.0)

    depth_buy = float(features.get("depth_buy", 0.0))
    depth_sell = float(features.get("depth_sell", 0.0))
    depth = max(0.0, depth_buy + depth_sell)
    depth_quality = math.log10(1.0 + depth) / 6.0  # 0..~1 para rangos comunes

    imb = float(features.get("imbalance", 0.5))
    # campana centrada en 0.7 y 0.3 (dos colinas); pick comprador o vendedor fuerte
    bell = math.exp(-((imb-0.7)**2)/(2*0.07**2)) + math.exp(-((imb-0.3)**2)/(2*0.07**2))
    ob_imbalance = _clamp(bell/2.0, 0.0, 1.0)

    micro_vol = float(features.get("micro_volatility", 0.0))
    # volatilidad deseable moderada: penalización por colas pesadas
    micro_vol_pen = 1.0 / (1.0 + 50.0 * micro_vol)

    spread = float(features.get("spread_abs", 0.0))  # diferencia absoluta
    price = abs(float(features.get("mid", 0.0)))
    tick = max(price * 1e-6, 1e-8)  # proxy
    spread_penalty = 1.0 / (1.0 + (spread / _nz(tick)))

    w = features.get("weights") or {"momentum":30, "depth":25, "imbalance":20, "spread":15, "microvol":10}
    score = (
        momentum * w.get("momentum",30) +
        depth_quality * w.get("depth",25) +
        ob_imbalance * w.get("imbalance",20) +
        spread_penalty * w.get("spread",15) +
        micro_vol_pen * w.get("microvol",10)
    ) / 1.0
    return float(_clamp(score, 0.0, 100.0))
