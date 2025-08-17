import os
import time

from orchestrator.supervisor import Supervisor
from orchestrator.storage import SQLiteStorage
from llm.client import LLMClient


def test_global_scheduler_runs_twice(tmp_path):
    db = tmp_path / "titan.db"
    storage = SQLiteStorage(str(db))
    llm = LLMClient()  # sin API key -> usa fallback determinista
    sup = Supervisor(storage=storage, llm_client=llm)
    events = []
    sup.stream_events(events.append)

    sup.start_global_scheduler(interval_s=1)
    time.sleep(2.5)
    sup.stop_global_scheduler()

    runs = [e for e in events if e.message == "global_insights"]
    assert len(runs) >= 2
