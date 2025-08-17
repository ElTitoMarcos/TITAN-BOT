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
        """Create tables if they do not yet exist and apply migrations."""
        schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
        with open(schema_path, "r", encoding="utf-8") as fh:
            self.conn.executescript(fh.read())
        # Older databases may miss recently added order columns. Ensure they
        # exist so SELECT statements do not fail.
        self._ensure_order_columns()
        self.conn.commit()

    def _ensure_order_columns(self) -> None:
        """Add any missing columns in the orders table.

        The project evolved and new metrics were appended to the ``orders``
        table over time. Users might still have a database created with an
        older schema lacking some of these fields. This helper inspects the
        current columns and performs ``ALTER TABLE`` operations for any
        missing ones so that reads using ``_ORDER_COLS`` always succeed.
        """

        # Map of required columns and their SQL types.
        required = {
            "resulting_fill_price": "REAL",
            "fee_asset": "TEXT",
            "fee_amount": "REAL",
            "ts": "TEXT",
            "status": "TEXT",
            "pnl": "REAL",
            "pnl_pct": "REAL",
            "notes": "TEXT",
            "raw_json": "TEXT",
            "expected_profit_ticks": "INTEGER",
            "actual_profit_ticks": "INTEGER",
            "spread_ticks": "REAL",
            "imbalance_pct": "REAL",
            "top3_depth": "TEXT",
            "book_hash": "TEXT",
            "latency_ms": "INTEGER",
            "cancel_replace_count": "INTEGER",
            "time_in_force": "TEXT",
            "hold_time_s": "REAL",
        }

        existing = {
            row[1] for row in self.conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        for col, coltype in required.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {coltype}")

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

    def iter_bots(self) -> List[BotConfig]:
        """Return all stored bot configurations."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT bot_id, cycle_id, name, seed_parent, mutations_json FROM bots"
            ).fetchall()
        bots: List[BotConfig] = []
        for r in rows:
            bots.append(
                BotConfig(
                    id=r["bot_id"],
                    cycle=r["cycle_id"],
                    name=r["name"],
                    mutations=json.loads(r["mutations_json"]) if r["mutations_json"] else {},
                    seed_parent=r["seed_parent"],
                )
            )
        return bots

    def get_cycle_winner(self, cycle_id: int) -> Optional[int]:
        """Return winner bot id for a given cycle if stored."""
        with self._lock:
            row = self.conn.execute(
                "SELECT winner_bot_id FROM cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
        if row is None:
            return None
        return row["winner_bot_id"] if row["winner_bot_id"] is not None else None

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
        "resulting_fill_price",
        "fee_asset",
        "fee_amount",
        "ts",
        "status",
        "pnl",
        "pnl_pct",
        "notes",
        "raw_json",  # JSON blob with extended metrics
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
    def list_cycle_summaries(self) -> List[Dict[str, Any]]:
        """Return basic info for all cycles including winner reasons.

        This is used by the UI to repopulate historical data and show the
        full rationale behind each winning bot after restarting the
        application."""

        query = (
            "SELECT c.cycle_id, c.finished_at, c.winner_bot_id, c.winner_reason,"
            "       SUM(bs.pnl) AS total_pnl "
            "FROM cycles c "
            "LEFT JOIN bot_stats bs ON bs.cycle_id = c.cycle_id "
            "GROUP BY c.cycle_id "
            "ORDER BY c.cycle_id"
        )
        with self._lock:
            rows = self.conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    def list_winners(self) -> List[Dict[str, Any]]:
        """Return historical cycle winners with their mutations and stats."""
        query = """
            SELECT c.cycle_id, c.winner_bot_id, b.mutations_json,
                   s.orders, s.pnl, s.pnl_pct, s.runtime_s, s.wins, s.losses
            FROM cycles c
            JOIN bots b ON c.winner_bot_id = b.bot_id
            LEFT JOIN bot_stats s ON s.bot_id = c.winner_bot_id AND s.cycle_id = c.cycle_id
            WHERE c.winner_bot_id IS NOT NULL
            ORDER BY c.cycle_id
        """
        with self._lock:
            rows = self.conn.execute(query).fetchall()
        winners: List[Dict[str, Any]] = []
        for r in rows:
            stats = None
            if r["orders"] is not None:
                stats = {
                    "orders": r["orders"],
                    "pnl": r["pnl"],
                    "pnl_pct": r["pnl_pct"],
                    "runtime_s": r["runtime_s"],
                    "wins": r["wins"],
                    "losses": r["losses"],
                }
            winners.append(
                {
                    "cycle": r["cycle_id"],
                    "bot_id": r["winner_bot_id"],
                    "mutations": json.loads(r["mutations_json"]) if r["mutations_json"] else {},
                    "stats": stats,
                }
            )
        return winners

    # ------------------------------------------------------------------
    def gather_global_summary(self) -> Dict[str, Any]:
        """Aggregate basic metrics across all stored data.

        Returns
        -------
        Dict[str, Any]
            Dictionary containing simple aggregates used for the global
            analysis prompt. The structure intentionally remains lightweight
            so callers are free to extend it without breaking older SQLite
            databases. Values are best-effort; missing tables simply yield
            empty stats instead of raising errors.
        """

        summary: Dict[str, Any] = {
            "mutations": {},
            "trends": [],
            "best_pairs": [],
            "stability": {},
        }

        with self._lock:
            # --- Mutations usage -------------------------------------------------
            try:
                rows = self.conn.execute(
                    "SELECT mutations_json FROM bots WHERE mutations_json IS NOT NULL"
                ).fetchall()
                counts: Dict[str, int] = {}
                for r in rows:
                    try:
                        muts = json.loads(r[0] or "{}")
                    except Exception:
                        muts = {}
                    for k, v in muts.items():
                        key = f"{k}:{v}"
                        counts[key] = counts.get(key, 0) + 1
                summary["mutations"] = counts
            except Exception:
                pass

            # --- PnL trend per cycle -------------------------------------------
            try:
                rows = self.conn.execute(
                    "SELECT cycle_id, SUM(pnl) as pnl FROM bot_stats GROUP BY cycle_id ORDER BY cycle_id"
                ).fetchall()
                summary["trends"] = [
                    {"cycle": int(r["cycle_id"]), "pnl": float(r["pnl"] or 0)}
                    for r in rows
                ]
            except Exception:
                pass

            # --- Best performing symbols --------------------------------------
            try:
                rows = self.conn.execute(
                    "SELECT symbol, SUM(pnl) as pnl FROM orders GROUP BY symbol ORDER BY pnl DESC LIMIT 5"
                ).fetchall()
                summary["best_pairs"] = [
                    {"symbol": r["symbol"], "pnl": float(r["pnl"] or 0)}
                    for r in rows
                ]
            except Exception:
                pass

            # --- Stability metrics --------------------------------------------
            try:
                row = self.conn.execute(
                    "SELECT AVG(cancel_replace_count) AS crc, AVG(latency_ms) AS latency FROM orders"
                ).fetchone()
                summary["stability"] = {
                    "avg_cancel_replace_count": float(row["crc"] or 0),
                    "avg_latency_ms": float(row["latency"] or 0),
                }
            except Exception:
                pass

        return summary

    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self.conn.close()
