
import time, uuid, os
from typing import Dict, List, Any

class LLMClient:
    """
    Cliente LLM:
    - Si existe OPENAI_API_KEY y un modelo configurado, intenta llamar a OpenAI para proponer acciones.
    - Si falla o no hay clave, usa heurística local ("dummy").
    """

    def __init__(self, model="gpt-4o", temperature_operativo=0.15, temperature_analitico=0.35, api_key:str=""):
        self.model = model
        self.temp_op = temperature_operativo
        self.temp_ana = temperature_analitico
        self.api_key = api_key or os.getenv("OPENAI_API_KEY","")

        # Lazy import para no romper si no está instalado
        self._openai = None
        if self.api_key:
            try:
                from openai import OpenAI  # type: ignore
                self._openai = OpenAI(api_key=self.api_key)
            except Exception:
                self._openai = None

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        try:
            from openai import OpenAI  # type: ignore
            self._openai = OpenAI(api_key=self.api_key)
        except Exception:
            self._openai = None

    def set_model(self, model: str):
        self.model = model

    def propose_actions(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        # Intenta OpenAI primero, si no heurístico
        result: Dict[str, Any] = {}
        if self._openai:
            try:
                result = self._propose_openai(snapshot)
            except Exception:
                result = {}
        if not result:
            result = self._propose_dummy(snapshot)
        try:
            from data_logger import log_event
            log_event({"llm": {"request": snapshot, "response": result}})
        except Exception:
            pass
        return result

    def greet(self, message: str = "hola") -> str:
        reply = ""
        if self._openai:
            try:
                resp = self._openai.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": message}],
                    temperature=self.temp_op,
                    timeout=20,
                )
                reply = resp.choices[0].message.content or ""
            except Exception:
                reply = ""
        try:
            from data_logger import log_event
            log_event({"llm": {"request": message, "response": reply}})
        except Exception:
            pass
        return reply

    def ask(self, message: str) -> str:
        reply = ""
        if self._openai:
            try:
                resp = self._openai.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": message}],
                    temperature=self.temp_ana,
                    timeout=20,
                )
                reply = resp.choices[0].message.content or ""
            except Exception:
                reply = ""
        try:
            from data_logger import log_event
            log_event({"llm": {"request": message, "response": reply}})
        except Exception:
            pass
        return reply

    def ask(self, message: str) -> str:
        if self._openai:
            try:
                resp = self._openai.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": message}],
                    temperature=self.temp_ana,
                    timeout=20,
                )
                return resp.choices[0].message.content or ""
            except Exception:
                return ""
        return ""

    # -------------------- OpenAI --------------------
    def _propose_openai(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        client = self._openai
        cfg = snapshot.get("config", {})
        max_actions = int(cfg.get("max_actions_per_cycle", 6))
        fee_bps = float(cfg.get("opportunity_threshold_percent", 0.2)) * 100.0

        sys_msg = (
            "Eres un asistente de trading cuantitativo. "
            "Decide ÚNICAMENTE con los datos del snapshot. "
            f"Respeta límites de riesgo y un umbral mínimo de oportunidad de {fee_bps:.1f} bps. "
            f"No emitas más de {max_actions} acciones. "
            "Prefiere límites tipo maker si spread lo permite. "
            "Responde en JSON con el campo 'actions'."
        )

        content = {"role":"system","content":sys_msg}
        user_content = {
            "role": "user",
            "content": f"Snapshot:\n{snapshot}\n\n"
                       "Devuelve JSON: {'ts':<ms>,'actions':[{'id':str,'symbol':str,'type':str,"
                       "'price':float,'qty_usd':float,'tif':str,'max_slippage_ticks':int,"
                       "'reason':str,'confidence':float}]} (usa solo campos necesarios)."
        }
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temp_op,
            messages=[content, user_content],
            timeout=20,
        )
        txt = resp.choices[0].message.content or "{}"
        import json
        try:
            data = json.loads(txt)
        except Exception:
            # Fallback: intentar extraer bloque JSON
            start = txt.find("{")
            end = txt.rfind("}")
            data = {}
            if start >= 0 and end > start:
                try:
                    import json as _json
                    data = _json.loads(txt[start:end+1])
                except Exception:
                    data = {}
        if not isinstance(data, dict) or "actions" not in data:
            # fallback vacío
            data = {"ts": int(time.time()*1000), "actions": []}
        return data

    # -------------------- Heurística local --------------------
    def _propose_dummy(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        cfg = snapshot.get("config", {})
        thr = float(cfg.get("opportunity_threshold_percent", 0.2))
        size_usd = float(cfg.get("size_usd", 50.0))
        max_actions = int(cfg.get("max_actions_per_cycle", 6))
        tif = "IOC"
        slp_ticks = 0

        actions: List[Dict[str, Any]] = []
        pairs = snapshot.get("pairs", [])[:10]

        for p in pairs:
            score = float(p.get("score", 0.0))
            edge = float(p.get("edge_est_bps", 0.0))
            sym = p.get("symbol", "")
            best_bid = float(p.get("best_bid", 0.0))

            if score >= 70 and edge >= thr * 100.0:
                actions.append({
                    "id": f"A-{sym.replace('/','')}-{uuid.uuid4().hex[:6].upper()}",
                    "symbol": sym,
                    "type": "PLACE_LIMIT_BUY",
                    "price": best_bid,
                    "qty_usd": size_usd,
                    "tif": tif,
                    "max_slippage_ticks": slp_ticks,
                    "reason": f"Heurística: score {score:.1f} y edge {edge:.1f}≥{thr*100:.1f} bps.",
                    "confidence": 0.6
                })
            if len(actions) >= max_actions:
                break
        return {"ts": int(time.time()*1000), "actions": actions}


    def _load_context_digest(self):
        import os, json
        try:
            path = os.path.join(os.getcwd(), "logs", "llm_context_latest.json")
            if not os.path.isfile(path):
                return {}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Reducir tamaño para prompt: tomar top 10 candidatos y últimas 20 órdenes cerradas
            data["candidates"] = (data.get("candidates") or [])[:10]
            data["orders_closed"] = (data.get("orders_closed") or [])[-20:]
            return data
        except Exception:
            return {}
