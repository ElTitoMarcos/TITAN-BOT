"""Cliente LLM para generar variaciones de estrategia."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .prompts import PROMPT_INICIAL_VARIACIONES


class LLMClient:
    """Wrapper liviano sobre OpenAI que genera variaciones iniciales.

    Si no hay clave de API o falla la llamada, devuelve un conjunto
    determinista de 10 variaciones válidas.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self._client = None
        if self.api_key:
            try:  # Lazy import para no requerir dependencia siempre
                from openai import OpenAI  # type: ignore

                self._client = OpenAI(api_key=self.api_key)
            except Exception:
                self._client = None

    # ------------------------------------------------------------------
    def _call_openai(self, trading_spec_text: str) -> List[Dict[str, object]]:
        assert self._client is not None
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": PROMPT_INICIAL_VARIACIONES},
                {"role": "user", "content": trading_spec_text},
            ],
            timeout=40,
        )
        txt = resp.choices[0].message.content or "[]"
        data = json.loads(txt)
        if not isinstance(data, list):
            raise ValueError("respuesta no es lista")
        return data

    # ------------------------------------------------------------------
    def _fallback_variations(self) -> List[Dict[str, object]]:
        """Genera 10 variaciones deterministas para modo sin LLM."""
        variations: List[Dict[str, object]] = []
        for i in range(10):
            variations.append(
                {
                    "name": f"var-{i+1:02d}",
                    "mutations": {
                        "order_size_usd": "auto",
                        "buy_level_rule": "accum_bids",
                        "sell_rule": "+1_tick",
                        "imbalance_buy_threshold_pct": 15 + i,
                        "cancel_replace_rules": {
                            "enable": True,
                            "max_moves": i % 5,
                            "min_depth_ratio": 0.5 + (i % 3) * 0.1,
                        },
                        "pair_ranking_window_s": 10 + i,
                        "min_vol_btc_24h": 5 + i,
                        "commission_buffer_ticks": 1,
                        "risk_limits": {
                            "max_open_orders": 1 + (i % 5),
                            "per_pair_exposure_usd": 50 + i * 10,
                        },
                    },
                }
            )
        return variations

    # ------------------------------------------------------------------
    def generate_initial_variations(self, trading_spec_text: str) -> List[Dict[str, object]]:
        """Obtiene 10 variaciones únicas de la estrategia base."""
        raw: List[Dict[str, object]] = []
        if self._client is not None:
            try:
                raw = self._call_openai(trading_spec_text)
            except Exception:
                raw = []
        if not raw:
            raw = self._fallback_variations()

        unique: List[Dict[str, object]] = []
        seen = set()
        for item in raw:
            name = str(item.get("name")) if isinstance(item, dict) else ""
            muts = item.get("mutations") if isinstance(item, dict) else None
            if not name or not isinstance(muts, dict):
                continue
            key = json.dumps(muts, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            unique.append({"name": name, "mutations": muts})
            if len(unique) == 10:
                break

        # Asegurar 10 variaciones
        idx = 1
        while len(unique) < 10:
            extra_name = f"auto-{idx:02d}"
            key = json.dumps({"placeholder": idx})
            if key not in seen:
                unique.append({"name": extra_name, "mutations": {}})
                seen.add(key)
            idx += 1
        return unique
