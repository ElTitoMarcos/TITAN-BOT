import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode


def verify(api_key: str, api_secret: str, timeout: float = 5.0) -> bool:
    """Return True if Binance API keys are valid.

    A lightweight ``/api/v3/account`` request is performed to ensure both the
    key and secret are correct. Any network or authentication error results in
    ``False`` so callers can safely gate UI elements based on the result.
    The call uses a short timeout so interactive flows remain responsive.
    """
    if not api_key or not api_secret:
        return False
    try:
        ts = int(time.time() * 1000)
        query = urlencode({"timestamp": ts})
        sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params = {"timestamp": ts, "signature": sig}
        headers = {"X-MBX-APIKEY": api_key}
        resp = requests.get(
            "https://api.binance.com/api/v3/account",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception:
        return False
