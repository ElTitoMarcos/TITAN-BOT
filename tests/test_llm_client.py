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
