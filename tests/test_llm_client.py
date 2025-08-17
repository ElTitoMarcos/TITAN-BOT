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
