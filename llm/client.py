"""Cliente LLM para generar variaciones de estrategia."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional
from .prompts import PROMPT_INICIAL_VARIACIONES, PROMPT_ANALISIS_CICLO

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

    # ------------------------------------------------------------------
    def analyze_cycle_and_pick_winner(self, cycle_summary: Dict[str, object]) -> Dict[str, object]:
        """Analiza un resumen de ciclo y elige un ganador.

        Si la llamada al LLM falla o no hay API key, se usa como
        fallback el bot con mayor PNL.
        """

        if self._client is not None:
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": PROMPT_ANALISIS_CICLO},
                        {"role": "user", "content": json.dumps(cycle_summary)},
                    ],
                    timeout=40,
                )
                txt = resp.choices[0].message.content or "{}"
                data = json.loads(txt)
                if isinstance(data, dict) and "winner_bot_id" in data:
                    return {
                        "winner_bot_id": int(data["winner_bot_id"]),
                        "reason": str(data.get("reason", "")),
                    }
            except Exception:
                pass
        return self._fallback_winner(cycle_summary)

    # ------------------------------------------------------------------
    def _fallback_winner(self, cycle_summary: Dict[str, object]) -> Dict[str, object]:
        """Fallback determinista seleccionando el bot con mayor PnL.

        Se recorre la lista de bots provista en ``cycle_summary`` y se
        identifica el ``bot_id`` con mayor beneficio acumulado. Este camino
        es utilizado cuando la llamada al LLM falla o no se dispone de clave
        de API, evitando que el ciclo quede sin ganador.
        """

        bots = cycle_summary.get("bots", [])
        best_id = None
        best_pnl = float("-inf")
        for bot in bots:
            try:
                pnl = float(bot.get("stats", {}).get("pnl", float("-inf")))
            except Exception:
                pnl = float("-inf")
            if pnl > best_pnl:
                best_pnl = pnl
                best_id = bot.get("bot_id")
        return {
            "winner_bot_id": int(best_id) if best_id is not None else -1,
            "reason": "max_pnl",
        }

