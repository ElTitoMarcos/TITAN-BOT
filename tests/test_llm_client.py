import types
from llm.client import LLMClient

class DummyResp:
    def __init__(self, content:str):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]

class DummyCompletions:
    def __init__(self, content:str):
        self._content = content
    def create(self, **kwargs):
        return DummyResp(self._content)

class DummyChat:
    def __init__(self, content:str):
        self.completions = DummyCompletions(content)

class DummyClient:
    def __init__(self, content:str):
        self.chat = DummyChat(content)


def test_generate_initial_variations_extracts_json(monkeypatch):
    content = "noise before\n[{\"name\":\"foo\",\"mutations\":{}}]\nnoise after"
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    monkeypatch.setattr(LLMClient, "check_credentials", lambda self: True)
    res = client.generate_initial_variations("spec")
    assert res[0]["name"] == "foo"
    assert len(res) == 10


def test_generate_initial_variations_parses_yaml(monkeypatch):
    content = "bots:\n  - name: foo\n    mutations: {}\n"
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    monkeypatch.setattr(LLMClient, "check_credentials", lambda self: True)
    res = client.generate_initial_variations("spec")
    assert res[0]["name"] == "foo"
    assert len(res) == 10


def test_generate_initial_variations_handles_trailing_commas_and_alias(monkeypatch):
    content = (
        "bots:\n  - name: bot_1,\n    mutation:\n      order_size_usd: 12.7,\n"
    )
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    monkeypatch.setattr(LLMClient, "check_credentials", lambda self: True)
    res = client.generate_initial_variations("spec")
    assert res[0]["name"] == "bot_1"
    assert res[0]["mutations"]["order_size_usd"] == 12.7
    assert len(res) == 10

def test_analyze_cycle_extracts_json_from_code_block():
    content = "```json\n{\"winner_bot_id\": 7, \"reason\": \"ok\"}\n```"
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    cycle = {"bots": [{"bot_id": 1, "stats": {"pnl": 0}}]}
    res = client.analyze_cycle_and_pick_winner(cycle)
    assert res["winner_bot_id"] == 7

def test_new_generation_extracts_json_from_code_block():
    content = "noise```json\n[{\"name\":\"foo\",\"mutations\":{}}]\n```more"
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    res = client.new_generation_from_winner({}, [])
    assert res[0]["name"] == "foo"
    assert len(res) == 10

def test_new_generation_parses_yaml():
    content = "bots:\n  - name: foo\n    mutations: {}\n"
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    res = client.new_generation_from_winner({}, [])
    assert res[0]["name"] == "foo"
    assert len(res) == 10


def test_new_generation_handles_trailing_commas_and_alias():
    content = (
        "bots:\n  - name: bot_1,\n    mutation:\n      order_size_usd: 12.7,\n"
    )
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    res = client.new_generation_from_winner({}, [])
    assert res[0]["name"] == "bot_1"
    assert res[0]["mutations"]["order_size_usd"] == 12.7
    assert len(res) == 10

def test_pick_meta_winner_extracts_json():
    content = "```json\n{\"bot_id\": 2, \"reason\": \"best pnl\"}\n```"
    client = LLMClient(api_key="")
    client._client = DummyClient(content)
    winners = [
        {"cycle": 1, "bot_id": 1, "mutations": {}, "stats": {"pnl": 1}},
        {"cycle": 2, "bot_id": 2, "mutations": {}, "stats": {"pnl": 2}},
    ]
    res = client.pick_meta_winner(winners)
    assert res["bot_id"] == 2

def test_local_weighted_fallback_prefers_stability():
    # Without API client, analyze_cycle_and_pick_winner debe usar fallback local
    client = LLMClient(api_key="")
    client._client = None
    summary = {
        "bots": [
            {
                "bot_id": 1,
                "stats": {
                    "pnl": 100,
                    "win_rate": 0.5,
                    "avg_hold_s": 10,
                    "avg_slippage_ticks": 5,
                    "timeouts": 10,
                    "cancel_replace_count": 5,
                },
            },
            {
                "bot_id": 2,
                "stats": {
                    "pnl": 90,
                    "win_rate": 0.5,
                    "avg_hold_s": 10,
                    "avg_slippage_ticks": 0,
                    "timeouts": 0,
                    "cancel_replace_count": 1,
                },
            },
        ]
    }
    decision = client.analyze_cycle_and_pick_winner(summary)
    assert decision["winner_bot_id"] == 2
