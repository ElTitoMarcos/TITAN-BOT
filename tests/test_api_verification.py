import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import exchange_utils.binance_check as binance_check
import requests
from llm.client import LLMClient


class DummyResp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_binance_verify_success(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=0):
        return DummyResp(200)

    monkeypatch.setattr(binance_check.requests, "get", fake_get)
    assert binance_check.verify("k", "s") is True


def test_binance_verify_failure(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=0):
        return DummyResp(401)

    monkeypatch.setattr(binance_check.requests, "get", fake_get)
    assert binance_check.verify("k", "s") is False


def test_llm_check_credentials_success(monkeypatch):
    client = LLMClient(api_key="x")

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(200)

    monkeypatch.setattr(requests, "get", fake_get)
    assert client.check_credentials() is True


def test_llm_check_credentials_failure(monkeypatch):
    client = LLMClient(api_key="x")

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(401)

    monkeypatch.setattr(requests, "get", fake_get)
    assert client.check_credentials() is False


def test_llm_check_credentials_no_key():
    client = LLMClient(api_key="")
    assert client.check_credentials() is False

