import gzip, json, os, time, threading

_LOG_PATH = os.path.join("logs", "timeline.jsonl.gz")
_lock = threading.Lock()

def log_event(event: dict) -> None:
    """Append an event as JSON line into a compressed log file."""
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        payload = {"ts": int(time.time() * 1000), **event}
        with _lock, gzip.open(_LOG_PATH, "at", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
