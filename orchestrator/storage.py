"""SQLite-backed persistence layer for the orchestrator."""
from __future__ import annotations

import json
import sqlite3
import threading

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import BotConfig, BotStats, SupervisorEvent

DB_FILENAME = "titanbot.db"

class SQLiteStorage:
    """Persist data from supervisors and runners into SQLite."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or DB_FILENAME
        # allow cross-thread usage (UI thread spawns supervisor thread)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    def _init_db(self) -> None:
        """Create tables if they do not yet exist."""
        schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
        with open(schema_path, "r", encoding="utf-8") as fh:
            self.conn.executescript(fh.read())
        self.conn.commit()

    # ------------------------------------------------------------------
    # Events
    def append_event(self, event: SupervisorEvent) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO events (ts, level, scope, bot_id, cycle_id, message, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.ts.isoformat(),
                    event.level,
                    event.scope,
                    event.bot_id,
                    event.cycle,
                    event.message,
                    json.dumps(event.payload) if event.payload else None,
                ),
            )

    def get_events(self, cycle: Optional[int] = None) -> List[SupervisorEvent]:
        query = "SELECT ts, level, scope, bot_id, cycle_id, message, payload_json FROM events"
        params: List[Any] = []
        if cycle is not None:
            query += " WHERE cycle_id = ?"
            params.append(cycle)
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()

        events = []
        for row in rows:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else None
            events.append(
                SupervisorEvent(
                    ts=datetime.fromisoformat(row["ts"]),
                    level=row["level"],
                    scope=row["scope"],
                    cycle=row["cycle_id"],
                    bot_id=row["bot_id"],
                    message=row["message"],
                    payload=payload,
                )
            )
        return events

    # ------------------------------------------------------------------
    # Bots
    def save_bot(self, bot_config: BotConfig) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO bots (bot_id, cycle_id, name, seed_parent, mutations_json, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bot_id) DO UPDATE SET
                    cycle_id=excluded.cycle_id,
                    name=excluded.name,
                    seed_parent=excluded.seed_parent,
                    mutations_json=excluded.mutations_json
                """,
                (
                    bot_config.id,
                    bot_config.cycle,
                    bot_config.name,
                    bot_config.seed_parent,
                    json.dumps(bot_config.mutations),
                ),
            )

    def get_bot(self, bot_id: int) -> Optional[BotConfig]:
        with self._lock:
            row = self.conn.execute(
                "SELECT bot_id, cycle_id, name, seed_parent, mutations_json FROM bots WHERE bot_id = ?",
                (bot_id,),
            ).fetchone()
        if row is None:
            return None
        return BotConfig(
            id=row["bot_id"],
            cycle=row["cycle_id"],
            name=row["name"],
            mutations=json.loads(row["mutations_json"]) if row["mutations_json"] else {},
            seed_parent=row["seed_parent"],
        )

    # ------------------------------------------------------------------
    # Bot stats
    def save_bot_stats(self, stats: BotStats) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO bot_stats (
                    bot_id, cycle_id, orders, pnl, pnl_pct, runtime_s, wins, losses, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bot_id, cycle_id) DO UPDATE SET
                    orders=excluded.orders,
                    pnl=excluded.pnl,
                    pnl_pct=excluded.pnl_pct,
                    runtime_s=excluded.runtime_s,
                    wins=excluded.wins,
                    losses=excluded.losses,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    stats.bot_id,
                    stats.cycle,
                    stats.orders,
                    stats.pnl,
                    stats.pnl_pct,
                    stats.runtime_s,
                    stats.wins,
                    stats.losses,
                ),
            )

    def get_bot_stats(self, bot_id: int, cycle: Optional[int] = None) -> Optional[BotStats]:
        query = (
            "SELECT bot_id, cycle_id, orders, pnl, pnl_pct, runtime_s, wins, losses "
            "FROM bot_stats WHERE bot_id = ?"
        )
        params: List[Any] = [bot_id]
        if cycle is not None:
            query += " AND cycle_id = ?"
            params.append(cycle)
        query += " ORDER BY cycle_id DESC LIMIT 1"
        with self._lock:
            row = self.conn.execute(query, params).fetchone()

        if row is None:
            return None
        return BotStats(
            bot_id=row["bot_id"],
            cycle=row["cycle_id"],
            orders=row["orders"],
            pnl=row["pnl"],
            pnl_pct=row["pnl_pct"],
            runtime_s=row["runtime_s"],
            wins=row["wins"],
            losses=row["losses"],
        )

    def iter_stats(self, cycle: Optional[int] = None) -> List[BotStats]:
        query = "SELECT bot_id, cycle_id, orders, pnl, pnl_pct, runtime_s, wins, losses FROM bot_stats"
        params: List[Any] = []
        if cycle is not None:
            query += " WHERE cycle_id = ?"
            params.append(cycle)
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return [
            BotStats(
                bot_id=r["bot_id"],
                cycle=r["cycle_id"],
                orders=r["orders"],
                pnl=r["pnl"],
                pnl_pct=r["pnl_pct"],
                runtime_s=r["runtime_s"],
                wins=r["wins"],
                losses=r["losses"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Orders
    _ORDER_COLS = [
        "order_id",
        "bot_id",
        "cycle_id",
        "symbol",
        "side",
        "qty",
        "price",
        "fee_asset",
        "fee_amount",
        "ts",
        "status",
        "pnl",
        "pnl_pct",
        "notes",
        "raw_json",
        "expected_profit_ticks",
        "actual_profit_ticks",
        "spread_ticks",
        "imbalance_pct",
        "top3_depth",
        "book_hash",
        "latency_ms",
        "cancel_replace_count",
        "time_in_force",
        "hold_time_s",
    ]

    def save_order(self, order: Dict[str, Any]) -> None:
        values = [order.get(col) for col in self._ORDER_COLS]
        placeholders = ",".join(["?"] * len(self._ORDER_COLS))
        cols = ",".join(self._ORDER_COLS)
        with self._lock, self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO orders ({cols}) VALUES ({placeholders})",
                values,
            )

    def iter_orders(
        self, cycle: Optional[int] = None, bot_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = f"SELECT {', '.join(self._ORDER_COLS)} FROM orders"
        params: List[Any] = []
        clauses: List[str] = []
        if cycle is not None:
            clauses.append("cycle_id = ?")
            params.append(cycle)
        if bot_id is not None:
            clauses.append("bot_id = ?")
            params.append(bot_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cycles
    def save_cycle_summary(self, cycle: int, summary: Dict[str, Any]) -> None:
        started_at = summary.get("started_at")
        finished_at = summary.get("finished_at")
        winner_bot_id = summary.get("winner_bot_id")
        winner_reason = summary.get("winner_reason")
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO cycles (cycle_id, started_at, finished_at, winner_bot_id, winner_reason)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    started_at=COALESCE(excluded.started_at, cycles.started_at),
                    finished_at=COALESCE(excluded.finished_at, cycles.finished_at),
                    winner_bot_id=excluded.winner_bot_id,
                    winner_reason=excluded.winner_reason
                """,
                (cycle, started_at, finished_at, winner_bot_id, winner_reason),
            )

    def get_cycle_summary(self, cycle: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute(
                "SELECT cycle_id, started_at, finished_at, winner_bot_id, winner_reason FROM cycles WHERE cycle_id = ?",
                (cycle,),
            ).fetchone()

        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self.conn.close()
