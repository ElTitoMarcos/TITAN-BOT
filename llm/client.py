"""Cliente LLM para generar variaciones de estrategia."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Callable, Any
import hashlib

from .prompts import (
    PROMPT_INICIAL_VARIACIONES,
    PROMPT_ANALISIS_CICLO,
    PROMPT_NUEVA_GENERACION_DESDE_GANADOR,
    PROMPT_P0,
    PROMPT_META_GANADOR,
)

# Pesos por defecto para el análisis local de ciclos
DEFAULT_METRIC_WEIGHTS: Dict[str, float] = {
    "pnl": 0.35,
    "timeouts": 0.25,
    "slippage": 0.2,
    "win_rate": 0.1,
    "avg_hold_s": 0.06,
    "cancel_replace_count": 0.04,
}

class LLMClient:
    """Wrapper liviano sobre OpenAI que genera variaciones iniciales.

    Si no hay clave de API o falla la llamada, devuelve un conjunto
    determinista de 10 variaciones válidas.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        on_log: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self.on_log = on_log
        self._client = None
        if self.api_key:
            try:  # Lazy import para no requerir dependencia siempre
                from openai import OpenAI  # type: ignore

                self._client = OpenAI(api_key=self.api_key)
            except Exception:
                self._client = None

    # ------------------------------------------------------------------
    def set_api_key(self, api_key: str) -> None:
        """Actualiza la clave de API y reconfigura el cliente interno."""
        self.api_key = api_key or ""
        if self.api_key:
            try:
                from openai import OpenAI  # type: ignore

                self._client = OpenAI(api_key=self.api_key)
            except Exception:
                self._client = None
        else:
            self._client = None

    # ------------------------------------------------------------------
    def check_credentials(self) -> bool:
        """Verifies that the configured API key is valid.

        It performs a minimal request to the OpenAI API. Any network or
        authentication error results in ``False`` so callers can decide how to
        handle unavailable credentials without raising exceptions.
        """
        if not self.api_key:
            self._client = None

            return False
        try:
            import requests

            url = "https://api.openai.com/v1/models"
            self._log("request", {"url": url})
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            self._log("response", {"status": resp.status_code})
            if resp.status_code == 200:
                return True
            self._client = None
            return False
        except Exception as e:
            self._client = None
            self._log("response", {"error": str(e)})

            return False

    # ------------------------------------------------------------------
    def _log(self, tag: str, payload: Any, label: Optional[str] = None) -> None:
        if self.on_log:
            try:
                self.on_log(tag, payload, label)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _extract_json(self, txt: str) -> Optional[Any]:
        """Intenta decodificar un JSON embebido en ``txt``.

        Primero se intenta decodificar el texto completo. Si falla, se busca
        un bloque JSON delimitado por ``[]`` o ``{}`` dentro del texto.
        """
        txt = txt.strip()
        try:
            return json.loads(txt)
        except Exception:
            pass

        start = txt.find("[")
        end = txt.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(txt[start : end + 1])
            except Exception:
                pass

        start = txt.find("{")
        end = txt.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(txt[start : end + 1])
            except Exception:
                pass

        return None

    # ------------------------------------------------------------------
    def _call_openai(
        self, trading_spec_text: str, label: Optional[str] = None
    ) -> List[Dict[str, object]]:
        assert self._client is not None
        messages = [
            {"role": "system", "content": PROMPT_P0},
            {"role": "system", "content": PROMPT_INICIAL_VARIACIONES},
            {"role": "user", "content": trading_spec_text},
        ]
        self._log(
            "request", {"model": self.model, "messages": messages}, label
        )
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=messages,
                timeout=40,
            )
            raw_txt = resp.choices[0].message.content or ""
            self._log("response", raw_txt)
            data = self._extract_json(raw_txt)
            if not isinstance(data, list):
                self._log("response", {"error": "no json array", "raw": raw_txt})
                return []
            return data

        except Exception as e:
            self._log("response", {"error": str(e)})
            return []

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
        if self._client is not None and self.check_credentials():
            try:
                raw = self._call_openai(
                    trading_spec_text, label="Variaciones Iniciales"
                )
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
    def _fingerprint(self, mutations: Dict[str, object]) -> str:
        """Genera un hash determinista para un conjunto de mutations."""
        return hashlib.sha256(json.dumps(mutations, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------
    def _fallback_new_generation(
        self,
        winner_mutations: Dict[str, object],
        history_fingerprints: List[str],
    ) -> List[Dict[str, object]]:
        """Crea 10 variaciones simples basadas en el ganador.

        Cada variación modifica ligeramente ``imbalance_buy_threshold_pct`` y
        ``max_open_orders`` respetando los fingerprints históricos.
        """

        base = winner_mutations.copy()
        variations: List[Dict[str, object]] = []
        seen = set(history_fingerprints)
        for i in range(1, 21):  # margen para asegurar 10
            muts = json.loads(json.dumps(base))  # deep copy
            thresh = muts.get("imbalance_buy_threshold_pct", 20)
            if isinstance(thresh, (int, float)):
                muts["imbalance_buy_threshold_pct"] = int(thresh) + i
            rl = muts.get("risk_limits", {})
            if isinstance(rl, dict):
                moo = rl.get("max_open_orders", 1)
                if isinstance(moo, int):
                    rl["max_open_orders"] = max(1, moo + (i % 3))
                rl.setdefault("per_pair_exposure_usd", 50)
                muts["risk_limits"] = rl
            fp = self._fingerprint(muts)
            if fp in seen:
                continue
            seen.add(fp)
            variations.append({"name": f"child-{i:02d}", "mutations": muts})
            if len(variations) == 10:
                break
        return variations

    # ------------------------------------------------------------------
    def new_generation_from_winner(
        self,
        winner_mutations: Dict[str, object],
        history_fingerprints: List[str],
    ) -> List[Dict[str, object]]:
        """Genera 10 variaciones nuevas basadas en el ganador anterior.

        ``history_fingerprints`` contiene hashes de mutaciones previas para
        evitar duplicados históricos.
        """

        raw: List[Dict[str, object]] = []
        if self._client is not None:
            prompt = PROMPT_NUEVA_GENERACION_DESDE_GANADOR.replace(
                "<PEGAR_JSON_WINNER>", json.dumps(winner_mutations, ensure_ascii=False)
            )
            messages = [
                {"role": "system", "content": PROMPT_P0},
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps({"history_fingerprints": history_fingerprints}),
                },
            ]
            self._log(
                "request", {"model": self.model, "messages": messages}, label="Nueva Generación"
            )
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0.2,
                    messages=messages,
                    timeout=40,
                )
                txt = resp.choices[0].message.content or "[]"
                self._log("response", txt)
                data = self._extract_json(txt)
                if isinstance(data, list):
                    raw = data
                else:
                    self._log("response", {"error": "no json array", "raw": txt})
                    raw = []
            except Exception as e:
                self._log("response", {"error": str(e)})
                raw = []
        if not raw:
            raw = self._fallback_new_generation(winner_mutations, history_fingerprints)

        unique: List[Dict[str, object]] = []
        seen = set(history_fingerprints)
        for item in raw:
            name = str(item.get("name")) if isinstance(item, dict) else ""
            muts = item.get("mutations") if isinstance(item, dict) else None
            if not name or not isinstance(muts, dict):
                continue
            fp = self._fingerprint(muts)
            if fp in seen:
                continue
            seen.add(fp)
            unique.append({"name": name, "mutations": muts})
            if len(unique) == 10:
                break

        # rellenar si faltan
        idx = 1
        while len(unique) < 10:
            base = json.loads(json.dumps(winner_mutations))
            base["placeholder"] = idx
            fp = self._fingerprint(base)
            if fp not in seen:
                unique.append({"name": f"auto-{idx:02d}", "mutations": base})
                seen.add(fp)
            idx += 1
        return unique

    # ------------------------------------------------------------------
    def analyze_cycle_and_pick_winner(
        self,
        cycle_summary: Dict[str, object],
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, object]:
        """Analiza un resumen de ciclo y elige un ganador.

        Si la llamada al LLM falla o no hay API key, se recurre a un
        cálculo local basado en un score ponderado de múltiples métricas.
        """

        if self._client is not None:
            messages = [
                {"role": "system", "content": PROMPT_P0},
                {"role": "system", "content": PROMPT_ANALISIS_CICLO},
                {"role": "user", "content": json.dumps(cycle_summary)},
            ]
            self._log(
                "request", {"model": self.model, "messages": messages}, label="Análisis de Ciclo"
            )
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=messages,
                    timeout=40,
                )
                raw_txt = resp.choices[0].message.content or "{}"
                self._log("response", raw_txt)
                data = self._extract_json(raw_txt)
                if isinstance(data, dict) and "winner_bot_id" in data:
                    return {
                        "winner_bot_id": int(data["winner_bot_id"]),
                        "reason": str(data.get("reason", "")),
                    }
                self._log("response", {"error": "no json object", "raw": raw_txt})
            except Exception as e:
                self._log("response", {"error": str(e)})
        return self.pick_winner_local(cycle_summary, weights)

    # ------------------------------------------------------------------
    def pick_meta_winner(self, winners: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Elige un meta-ganador entre ganadores históricos."""
        if self._client is not None:
            messages = [
                {"role": "system", "content": PROMPT_P0},
                {"role": "system", "content": PROMPT_META_GANADOR},
                {"role": "user", "content": json.dumps(winners)},
            ]
            self._log(
                "request", {"model": self.model, "messages": messages}, label="Meta-ganador"
            )
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=messages,
                    timeout=40,
                )
                raw_txt = resp.choices[0].message.content or "{}"
                self._log("response", raw_txt)
                data = self._extract_json(raw_txt)
                if isinstance(data, dict) and "bot_id" in data:
                    return {
                        "bot_id": int(data.get("bot_id", -1)),
                        "reason": str(data.get("reason", "")),
                    }
                self._log("response", {"error": "no json object", "raw": raw_txt})
            except Exception as e:
                self._log("response", {"error": str(e)})
        return self._fallback_meta_winner(winners)

    def _fallback_meta_winner(self, winners: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Fallback determinista: selecciona el ganador con mayor PnL."""
        best = None
        best_pnl = float("-inf")
        for w in winners:
            try:
                pnl = float(w.get("stats", {}).get("pnl", float("-inf")))
            except Exception:
                pnl = float("-inf")
            if pnl > best_pnl:
                best_pnl = pnl
                best = w
        if best:
            return {
                "bot_id": int(best.get("bot_id", -1)),
                "reason": "max_pnl",
            }
        return {"bot_id": -1, "reason": "no_winners"}

    # ------------------------------------------------------------------
    def pick_winner_local(
        self,
        cycle_summary: Dict[str, object],
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, object]:
        """Selecciona un ganador mediante score ponderado.

        ``weights`` permite ajustar la importancia relativa de cada métrica.
        Los pesos que no se provean se completan con valores por defecto
        ``DEFAULT_METRIC_WEIGHTS``. Se prioriza PnL, luego estabilidad
        (timeouts y slippage), seguido de win rate, menor tiempo de hold y
        menor cancelación/reemplazo. Los empates se resuelven de manera
        determinista siguiendo el mismo orden de métricas.
        """

        bots = cycle_summary.get("bots", [])
        if not bots:
            return {"winner_bot_id": -1, "reason": "no_bots"}

        w = DEFAULT_METRIC_WEIGHTS.copy()
        if weights:
            w.update(weights)

        # recopilar valores por métrica
        pnl_vals = [float(b.get("stats", {}).get("pnl", 0.0)) for b in bots]
        timeout_vals = [float(b.get("stats", {}).get("timeouts", 0.0)) for b in bots]
        slippage_vals = [float(b.get("stats", {}).get("avg_slippage_ticks", 0.0)) for b in bots]
        win_vals = [float(b.get("stats", {}).get("win_rate", 0.0)) for b in bots]
        hold_vals = [float(b.get("stats", {}).get("avg_hold_s", 0.0)) for b in bots]
        crc_vals = [
            float(b.get("stats", {}).get("cancel_replace_count", 0.0)) for b in bots
        ]

        def norm(val: float, vals: List[float], invert: bool = False) -> float:
            mn = min(vals)
            mx = max(vals)
            if mx == mn:
                res = 0.0
            else:
                res = (val - mn) / (mx - mn)
            return 1 - res if invert else res

        scored: List[Dict[str, float]] = []
        for idx, bot in enumerate(bots):
            score = (
                w["pnl"] * norm(pnl_vals[idx], pnl_vals)
                + w["timeouts"] * norm(timeout_vals[idx], timeout_vals, invert=True)
                + w["slippage"] * norm(slippage_vals[idx], slippage_vals, invert=True)
                + w["win_rate"] * norm(win_vals[idx], win_vals)
                + w["avg_hold_s"] * norm(hold_vals[idx], hold_vals, invert=True)
                + w["cancel_replace_count"]
                * norm(crc_vals[idx], crc_vals, invert=True)
            )
            scored.append(
                {
                    "bot_id": int(bot.get("bot_id", -1)),
                    "score": score,
                    "pnl": pnl_vals[idx],
                    "timeouts": timeout_vals[idx],
                    "slippage": slippage_vals[idx],
                    "win_rate": win_vals[idx],
                    "avg_hold_s": hold_vals[idx],
                    "cancel_replace_count": crc_vals[idx],
                }
            )

        scored.sort(
            key=lambda b: (
                b["score"],
                b["pnl"],
                -b["timeouts"],
                -b["slippage"],
                b["win_rate"],
                -b["avg_hold_s"],
                -b["cancel_replace_count"],
                -b["bot_id"],
            ),
            reverse=True,
        )

        winner = scored[0]
        return {"winner_bot_id": winner["bot_id"], "reason": "weighted_score"}

    # ------------------------------------------------------------------
    def analyze_global(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Solicita al LLM recomendaciones globales.

        Parameters
        ----------
        summary: Dict[str, Any]
            Resumen global recopilado por el supervisor.

        Returns
        -------
        Dict[str, Any]
            Diccionario con la clave ``changes`` que contiene una lista de
            sugerencias accionables.
        """

        if self._client is not None:
            from .prompts import PROMPT_ANALISIS_GLOBAL

            messages = [
                {"role": "system", "content": PROMPT_P0},
                {"role": "system", "content": PROMPT_ANALISIS_GLOBAL},
                {"role": "user", "content": json.dumps(summary)},
            ]
            self._log(
                "request", {"model": self.model, "messages": messages}, label="Análisis Global"
            )
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=messages,
                    timeout=40,
                )
                raw_txt = resp.choices[0].message.content or "{}"
                self._log("response", raw_txt)
                data = self._extract_json(raw_txt)
                if isinstance(data, dict) and data.get("changes"):
                    return {"changes": list(data.get("changes", []))}
                self._log("response", {"error": "no json object", "raw": raw_txt})
            except Exception as e:
                self._log("response", {"error": str(e)})

        # Fallback simple con recomendaciones deterministas
        return {"changes": ["aumentar_timeout", "revisar_slippage", "ajustar_pesos"]}

    # ------------------------------------------------------------------
    def propose_patch(self, changes: Dict[str, Any]) -> str:
        """Obtiene un patch unificado basado en cambios sugeridos.

        Si el LLM no está disponible, devuelve una cadena vacía.
        """

        if self._client is not None:
            from .prompts import PROMPT_PATCH_FROM_CHANGES

            messages = [
                {"role": "system", "content": PROMPT_P0},
                {"role": "system", "content": PROMPT_PATCH_FROM_CHANGES},
                {"role": "user", "content": json.dumps(changes)},
            ]
            self._log("request", {"model": self.model, "messages": messages})
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=messages,
                    timeout=40,
                )
                diff = resp.choices[0].message.content or ""
                self._log("response", diff)
                return diff
            except Exception as e:
                self._log("response", {"error": str(e)})
                return ""

        return ""
