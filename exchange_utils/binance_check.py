import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode


def verify(api_key: str, api_secret: str, timeout: float = 5.0) -> bool:
    """Return True if Binance API keys are valid.

    Performs a signed request to ``/api/v3/account``. Any network or
    authentication error results in ``False``. The call uses a short timeout
    so UI callers do not block for long periods.
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
