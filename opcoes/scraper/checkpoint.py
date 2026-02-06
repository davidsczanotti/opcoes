from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from .storage import CSV_FIELDS, _ensure_parent


def default_checkpoint_db_path(output_csv: Path) -> Path:
    return output_csv.with_suffix(".checkpoint.db")


@dataclass(frozen=True)
class CheckpointState:
    processed_symbols: List[str]
    snapshot_rows: List[Dict[str, str]]
    snapshot_date: Optional[str]


class ScrapeCheckpointStore:
    """Checkpoint transacional do scraper por saída e símbolo."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        _ensure_parent(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    def prepare(
        self,
        *,
        output_csv: Path,
        target_symbols: Sequence[str],
        symbols_signature: str,
    ) -> CheckpointState:
        output_resolved = str(output_csv.resolve())
        session_id = _session_id(output_resolved)
        now = _now_iso()
        symbols = _normalize_symbols(target_symbols)
        symbols_json = json.dumps(symbols, ensure_ascii=False, separators=(",", ":"))

        session = self.conn.execute(
            "SELECT * FROM checkpoint_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not session:
            self.conn.execute(
                """
                INSERT INTO checkpoint_sessions (
                    session_id,
                    output_csv,
                    symbols_signature,
                    symbols_json,
                    snapshot_date,
                    last_symbol,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    output_resolved,
                    symbols_signature,
                    symbols_json,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            self.conn.executemany(
                """
                INSERT INTO checkpoint_symbols (
                    session_id, symbol, status, attempts, row_count, last_error, updated_at
                ) VALUES (?, ?, 'pending', 0, 0, NULL, ?)
                """,
                [(session_id, symbol, now) for symbol in symbols],
            )
            self.conn.commit()
            return CheckpointState(processed_symbols=[], snapshot_rows=[], snapshot_date=None)

        if session["output_csv"] != output_resolved:
            # Session id é derivado do output; aqui é apenas proteção de integridade.
            raise RuntimeError("Checkpoint session inconsistente com output_csv.")

        self._reconcile_symbols(
            session_id=session_id,
            target_symbols=symbols,
            symbols_signature=symbols_signature,
            symbols_json=symbols_json,
            updated_at=now,
        )

        done_rows = self.conn.execute(
            """
            SELECT symbol
            FROM checkpoint_symbols
            WHERE session_id = ? AND status = 'done'
            """,
            (session_id,),
        ).fetchall()
        done_set = {str(row["symbol"]) for row in done_rows}
        processed_symbols = [symbol for symbol in symbols if symbol in done_set]
        snapshot_rows = self._load_rows(session_id=session_id, target_symbols=symbols)
        snapshot_date_row = self.conn.execute(
            "SELECT snapshot_date FROM checkpoint_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        snapshot_date = str(snapshot_date_row["snapshot_date"]) if snapshot_date_row and snapshot_date_row["snapshot_date"] else None
        return CheckpointState(
            processed_symbols=processed_symbols,
            snapshot_rows=snapshot_rows,
            snapshot_date=snapshot_date,
        )

    def mark_symbol_running(self, *, output_csv: Path, symbol: str) -> None:
        session_id = _session_id(str(output_csv.resolve()))
        now = _now_iso()
        self.conn.execute(
            """
            UPDATE checkpoint_symbols
            SET status = 'running',
                attempts = attempts + 1,
                updated_at = ?
            WHERE session_id = ? AND symbol = ?
            """,
            (now, session_id, symbol),
        )
        self.conn.execute(
            """
            UPDATE checkpoint_sessions
            SET last_symbol = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (symbol, now, session_id),
        )
        self.conn.commit()

    def mark_symbol_failed(self, *, output_csv: Path, symbol: str, error: str) -> None:
        session_id = _session_id(str(output_csv.resolve()))
        now = _now_iso()
        self.conn.execute(
            """
            UPDATE checkpoint_symbols
            SET status = 'failed',
                last_error = ?,
                updated_at = ?
            WHERE session_id = ? AND symbol = ?
            """,
            ((error or "").strip()[:1500], now, session_id, symbol),
        )
        self.conn.execute(
            """
            UPDATE checkpoint_sessions
            SET last_symbol = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (symbol, now, session_id),
        )
        self.conn.commit()

    def mark_symbol_success(
        self,
        *,
        output_csv: Path,
        symbol: str,
        rows: Sequence[Dict[str, str]],
        snapshot_date: Optional[str],
    ) -> None:
        session_id = _session_id(str(output_csv.resolve()))
        now = _now_iso()
        clean_rows = _normalize_rows(rows)

        self.conn.execute(
            "DELETE FROM checkpoint_rows WHERE session_id = ? AND symbol = ?",
            (session_id, symbol),
        )
        payload = []
        for row in clean_rows:
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            payload.append(
                (
                    session_id,
                    symbol,
                    ticker,
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                    now,
                )
            )
        if payload:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO checkpoint_rows (
                    session_id, symbol, ticker, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                payload,
            )

        self.conn.execute(
            """
            UPDATE checkpoint_symbols
            SET status = 'done',
                row_count = ?,
                last_error = NULL,
                updated_at = ?
            WHERE session_id = ? AND symbol = ?
            """,
            (len(clean_rows), now, session_id, symbol),
        )

        session = self.conn.execute(
            "SELECT snapshot_date FROM checkpoint_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        current_snapshot = str(session["snapshot_date"]) if session and session["snapshot_date"] else None
        merged_snapshot = _max_iso_date(current_snapshot, snapshot_date)
        self.conn.execute(
            """
            UPDATE checkpoint_sessions
            SET snapshot_date = ?, last_symbol = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (merged_snapshot, symbol, now, session_id),
        )
        self.conn.commit()

    def status_counts(self, *, output_csv: Path, target_symbols: Sequence[str]) -> Dict[str, int]:
        session_id = _session_id(str(output_csv.resolve()))
        target_set = set(_normalize_symbols(target_symbols))
        rows = self.conn.execute(
            """
            SELECT symbol, status
            FROM checkpoint_symbols
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
        counts = {"done": 0, "failed": 0, "running": 0, "pending": 0, "total": len(target_set)}
        for row in rows:
            symbol = str(row["symbol"])
            if symbol not in target_set:
                continue
            status = str(row["status"] or "pending").lower()
            if status not in {"done", "failed", "running", "pending"}:
                status = "pending"
            counts[status] += 1
        return counts

    def is_complete(self, *, output_csv: Path, target_symbols: Sequence[str]) -> bool:
        counts = self.status_counts(output_csv=output_csv, target_symbols=target_symbols)
        return counts["total"] > 0 and counts["done"] == counts["total"]

    def clear(self, *, output_csv: Path) -> None:
        session_id = _session_id(str(output_csv.resolve()))
        self.conn.execute("DELETE FROM checkpoint_rows WHERE session_id = ?", (session_id,))
        self.conn.execute("DELETE FROM checkpoint_symbols WHERE session_id = ?", (session_id,))
        self.conn.execute("DELETE FROM checkpoint_sessions WHERE session_id = ?", (session_id,))
        self.conn.commit()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoint_sessions (
                session_id TEXT PRIMARY KEY,
                output_csv TEXT NOT NULL,
                symbols_signature TEXT NOT NULL,
                symbols_json TEXT NOT NULL,
                snapshot_date TEXT,
                last_symbol TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoint_symbols (
                session_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                row_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, symbol)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoint_rows (
                session_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                ticker TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, ticker)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkpoint_symbols_status ON checkpoint_symbols (session_id, status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkpoint_rows_symbol ON checkpoint_rows (session_id, symbol)"
        )
        self.conn.commit()

    def _reconcile_symbols(
        self,
        *,
        session_id: str,
        target_symbols: Sequence[str],
        symbols_signature: str,
        symbols_json: str,
        updated_at: str,
    ) -> None:
        target_set = set(target_symbols)
        existing_rows = self.conn.execute(
            "SELECT symbol FROM checkpoint_symbols WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        existing = {str(row["symbol"]) for row in existing_rows}

        to_remove = sorted(existing - target_set)
        to_add = [sym for sym in target_symbols if sym not in existing]

        if to_remove:
            placeholders = ",".join("?" for _ in to_remove)
            self.conn.execute(
                f"DELETE FROM checkpoint_symbols WHERE session_id = ? AND symbol IN ({placeholders})",
                (session_id, *to_remove),
            )
            self.conn.execute(
                f"DELETE FROM checkpoint_rows WHERE session_id = ? AND symbol IN ({placeholders})",
                (session_id, *to_remove),
            )
        if to_add:
            self.conn.executemany(
                """
                INSERT INTO checkpoint_symbols (
                    session_id, symbol, status, attempts, row_count, last_error, updated_at
                ) VALUES (?, ?, 'pending', 0, 0, NULL, ?)
                """,
                [(session_id, symbol, updated_at) for symbol in to_add],
            )
        self.conn.execute(
            """
            UPDATE checkpoint_sessions
            SET symbols_signature = ?, symbols_json = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (symbols_signature, symbols_json, updated_at, session_id),
        )
        self.conn.commit()

    def _load_rows(self, *, session_id: str, target_symbols: Sequence[str]) -> List[Dict[str, str]]:
        target_set = set(target_symbols)
        payload_rows = self.conn.execute(
            """
            SELECT symbol, payload_json
            FROM checkpoint_rows
            WHERE session_id = ?
            ORDER BY symbol, ticker
            """,
            (session_id,),
        ).fetchall()
        rows: List[Dict[str, str]] = []
        for entry in payload_rows:
            symbol = str(entry["symbol"])
            if symbol not in target_set:
                continue
            try:
                parsed = json.loads(str(entry["payload_json"]))
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            row = {field: str(parsed.get(field) or "") for field in CSV_FIELDS}
            rows.append(row)
        return rows


def _session_id(output_csv_resolved: str) -> str:
    return hashlib.sha1(output_csv_resolved.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _normalize_symbols(symbols: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    for raw in symbols:
        symbol = (raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return normalized


def _normalize_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for row in rows:
        clean = {field: str(row.get(field) or "") for field in CSV_FIELDS}
        normalized.append(clean)
    return normalized


def _max_iso_date(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    if not candidate:
        return current
    if not current:
        return candidate
    try:
        current_dt = dt.date.fromisoformat(current)
        candidate_dt = dt.date.fromisoformat(candidate)
    except ValueError:
        return current
    return max(current_dt, candidate_dt).isoformat()


__all__ = ["CheckpointState", "ScrapeCheckpointStore", "default_checkpoint_db_path"]
