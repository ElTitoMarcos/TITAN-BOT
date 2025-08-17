from orchestrator.storage import SQLiteStorage


def test_cycle_summaries_include_reason(tmp_path):
    db = tmp_path / "test.db"
    st = SQLiteStorage(db_path=str(db))
    reason = "R" * 300
    st.save_cycle_summary(1, {
        "finished_at": "now",
        "winner_bot_id": 1,
        "winner_reason": reason,
    })
    summaries = st.list_cycle_summaries()
    assert summaries[0]["winner_reason"] == reason
    st.conn.close()
