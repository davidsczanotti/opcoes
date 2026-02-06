from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from urllib import parse, request
import http.cookiejar

from .config import get_db_path
from .scraper.storage import _ensure_parent
from .utils import parse_ptbr_number

SEARCH_URL = "https://www.fundamentus.com.br/buscaavancada.php"
RESULT_URL = "https://www.fundamentus.com.br/resultado.php"

RESULT_FIELDS = [
    "papel",
    "cotacao",
    "pl",
    "pvp",
    "psr",
    "div_yield",
    "p_ativo",
    "p_cap_giro",
    "p_ebit",
    "p_ativo_circ_liq",
    "ev_ebit",
    "ev_ebitda",
    "margem_ebit",
    "margem_liquida",
    "liquidez_corrente",
    "roic",
    "roe",
    "liquidez_2m",
    "patrimonio_liq",
    "div_bruta_patrim",
    "cresc_rec_5a",
]

NUMERIC_FIELDS = [f for f in RESULT_FIELDS if f != "papel"]


@dataclass(frozen=True)
class FundamentusFilterConfig:
    liq_2m_min: float = 1_000_000.0
    div_bruta_patrim_max: float = 2.0
    cresc_rec_5a_min: float = 0.0
    div_yield_min: float = 6.0
    roe_min: float = 15.0
    margem_liquida_min: float = 10.0
    margem_liquida_allow_zero: bool = True


class _FundamentusTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_thead = False
        self._in_tbody = False
        self._in_row = False
        self._in_cell = False
        self._cell_parts: List[str] = []
        self.headers: List[str] = []
        self.rows: List[List[str]] = []
        self._current_row: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "table":
            for key, value in attrs:
                if key == "id" and value == "resultado":
                    self._in_table = True
                    break
        if not self._in_table:
            return
        if tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr":
            self._in_row = True
            self._current_row = []
        elif tag in ("th", "td"):
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if self._in_cell and tag in ("th", "td"):
            text = " ".join("".join(self._cell_parts).split())
            if self._in_thead and tag == "th":
                self.headers.append(text)
            elif self._in_tbody and tag == "td":
                self._current_row.append(text)
            self._in_cell = False
        elif tag == "tr":
            if self._in_tbody and self._current_row:
                self.rows.append(self._current_row)
            self._in_row = False
        elif tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def parse_result_table(html: str) -> List[Dict[str, str]]:
    parser = _FundamentusTableParser()
    parser.feed(html)
    results: List[Dict[str, str]] = []
    for row in parser.rows:
        if len(row) < len(RESULT_FIELDS):
            continue
        payload = dict(zip(RESULT_FIELDS, row[: len(RESULT_FIELDS)]))
        results.append(payload)
    return results


def normalize_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, object]]:
    normalized: List[Dict[str, object]] = []
    for row in rows:
        papel = (row.get("papel") or "").strip().upper()
        if not papel:
            continue
        entry: Dict[str, object] = {"papel": papel}
        for field in NUMERIC_FIELDS:
            entry[field] = parse_ptbr_number(row.get(field))
        normalized.append(entry)
    return normalized


def evaluate_row(row: Dict[str, object], cfg: FundamentusFilterConfig) -> Dict[str, object]:
    papel = (row.get("papel") or "").strip().upper()

    def reject(step: int, rule: str, value: Optional[float], reason: str) -> Dict[str, object]:
        return {
            "papel": papel,
            "status": "rejected",
            "failed_step": step,
            "failed_rule": rule,
            "failed_value": value,
            "reason": reason,
        }

    liq_2m = row.get("liquidez_2m")
    if liq_2m is None:
        return reject(1, "liq_2m_min", None, "missing_liquidez_2m")
    if liq_2m < cfg.liq_2m_min:
        return reject(1, "liq_2m_min", float(liq_2m), "liquidez_2m_below_min")

    div_bruta = row.get("div_bruta_patrim")
    if div_bruta is None:
        return reject(2, "div_bruta_patrim_max", None, "missing_div_bruta_patrim")
    if div_bruta > cfg.div_bruta_patrim_max:
        return reject(2, "div_bruta_patrim_max", float(div_bruta), "div_bruta_patrim_above_max")

    cresc_rec = row.get("cresc_rec_5a")
    if cresc_rec is None:
        return reject(3, "cresc_rec_5a_min", None, "missing_cresc_rec_5a")
    if cresc_rec < cfg.cresc_rec_5a_min:
        return reject(3, "cresc_rec_5a_min", float(cresc_rec), "cresc_rec_5a_below_min")

    div_yield = row.get("div_yield")
    if div_yield is None:
        return reject(4, "div_yield_min", None, "missing_div_yield")
    if div_yield < cfg.div_yield_min:
        return reject(4, "div_yield_min", float(div_yield), "div_yield_below_min")

    roe = row.get("roe")
    if roe is None:
        return reject(5, "roe_min", None, "missing_roe")
    if roe < cfg.roe_min:
        return reject(5, "roe_min", float(roe), "roe_below_min")

    margem = row.get("margem_liquida")
    if margem is None:
        return reject(6, "margem_liquida_rule", None, "missing_margem_liquida")
    if not (margem >= cfg.margem_liquida_min or (cfg.margem_liquida_allow_zero and margem == 0)):
        return reject(6, "margem_liquida_rule", float(margem), "margem_liquida_out_of_rule")

    return {
        "papel": papel,
        "status": "approved",
        "failed_step": None,
        "failed_rule": None,
        "failed_value": None,
        "reason": "approved",
    }


def evaluate_rows(rows: Sequence[Dict[str, object]], cfg: Optional[FundamentusFilterConfig] = None) -> List[Dict[str, object]]:
    cfg = cfg or FundamentusFilterConfig()
    results: List[Dict[str, object]] = []
    for row in rows:
        papel = (row.get("papel") or "").strip()
        if not papel:
            continue
        results.append(evaluate_row(row, cfg))
    return results


def fetch_fundamentus_results(
    *,
    pl_min: float = 0.0,
    patrim_min: float = 0.0,
    timeout: float = 30.0,
) -> List[Dict[str, str]]:
    params = {
        "pl_min": f"{pl_min:.2f}".rstrip("0").rstrip("."),
        "patrim_min": f"{patrim_min:.2f}".rstrip("0").rstrip("."),
        "negociada": "ON",
        "ordem": "1",
    }
    payload = parse.urlencode(params).encode("ascii")

    jar = http.cookiejar.CookieJar()
    opener = request.build_opener(request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
        ("Referer", SEARCH_URL),
    ]

    opener.open(SEARCH_URL, timeout=timeout)
    req = request.Request(
        RESULT_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with opener.open(req, timeout=timeout) as resp:
        html = resp.read().decode("iso-8859-1", errors="replace")
    return parse_result_table(html)


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    _ensure_parent(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentus_snapshots (
            snapshot_date TEXT NOT NULL,
            papel TEXT NOT NULL,
            cotacao REAL,
            pl REAL,
            pvp REAL,
            psr REAL,
            div_yield REAL,
            p_ativo REAL,
            p_cap_giro REAL,
            p_ebit REAL,
            p_ativo_circ_liq REAL,
            ev_ebit REAL,
            ev_ebitda REAL,
            margem_ebit REAL,
            margem_liquida REAL,
            liquidez_corrente REAL,
            roic REAL,
            roe REAL,
            liquidez_2m REAL,
            patrimonio_liq REAL,
            div_bruta_patrim REAL,
            cresc_rec_5a REAL,
            PRIMARY KEY (snapshot_date, papel)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentus_runs (
            snapshot_date TEXT PRIMARY KEY,
            pl_min REAL,
            patrim_min REAL,
            negociada INTEGER,
            source_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentus_signals (
            snapshot_date TEXT NOT NULL,
            papel TEXT NOT NULL,
            status TEXT NOT NULL,
            failed_step INTEGER,
            failed_rule TEXT,
            failed_value REAL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (snapshot_date, papel)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentus_filter_runs (
            snapshot_date TEXT PRIMARY KEY,
            liq_2m_min REAL,
            div_bruta_patrim_max REAL,
            cresc_rec_5a_min REAL,
            div_yield_min REAL,
            roe_min REAL,
            margem_liquida_min REAL,
            margem_liquida_allow_zero INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def save_snapshot(
    rows: Sequence[Dict[str, str]],
    *,
    snapshot_date: Optional[str] = None,
    pl_min: float = 0.0,
    patrim_min: float = 0.0,
    negociada: bool = True,
    db_path: Optional[Path] = None,
) -> int:
    snapshot_date = snapshot_date or dt.date.today().isoformat()
    normalized = normalize_rows(rows)
    payload = [
        (
            snapshot_date,
            row.get("papel"),
            row.get("cotacao"),
            row.get("pl"),
            row.get("pvp"),
            row.get("psr"),
            row.get("div_yield"),
            row.get("p_ativo"),
            row.get("p_cap_giro"),
            row.get("p_ebit"),
            row.get("p_ativo_circ_liq"),
            row.get("ev_ebit"),
            row.get("ev_ebitda"),
            row.get("margem_ebit"),
            row.get("margem_liquida"),
            row.get("liquidez_corrente"),
            row.get("roic"),
            row.get("roe"),
            row.get("liquidez_2m"),
            row.get("patrimonio_liq"),
            row.get("div_bruta_patrim"),
            row.get("cresc_rec_5a"),
        )
        for row in normalized
    ]

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO fundamentus_runs
            (snapshot_date, pl_min, patrim_min, negociada, source_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (snapshot_date, float(pl_min), float(patrim_min), 1 if negociada else 0, RESULT_URL),
        )
        if payload:
            conn.executemany(
                """
                INSERT OR REPLACE INTO fundamentus_snapshots (
                    snapshot_date, papel, cotacao, pl, pvp, psr, div_yield, p_ativo, p_cap_giro,
                    p_ebit, p_ativo_circ_liq, ev_ebit, ev_ebitda, margem_ebit, margem_liquida,
                    liquidez_corrente, roic, roe, liquidez_2m, patrimonio_liq, div_bruta_patrim,
                    cresc_rec_5a
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        conn.commit()
    finally:
        conn.close()
    return len(payload)


def save_signals(
    signals: Sequence[Dict[str, object]],
    *,
    snapshot_date: str,
    cfg: Optional[FundamentusFilterConfig] = None,
    db_path: Optional[Path] = None,
) -> int:
    cfg = cfg or FundamentusFilterConfig()
    payload = [
        (
            snapshot_date,
            s.get("papel"),
            s.get("status"),
            s.get("failed_step"),
            s.get("failed_rule"),
            s.get("failed_value"),
            s.get("reason"),
        )
        for s in signals
        if s.get("papel")
    ]
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO fundamentus_filter_runs (
                snapshot_date, liq_2m_min, div_bruta_patrim_max, cresc_rec_5a_min, div_yield_min,
                roe_min, margem_liquida_min, margem_liquida_allow_zero
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                float(cfg.liq_2m_min),
                float(cfg.div_bruta_patrim_max),
                float(cfg.cresc_rec_5a_min),
                float(cfg.div_yield_min),
                float(cfg.roe_min),
                float(cfg.margem_liquida_min),
                1 if cfg.margem_liquida_allow_zero else 0,
            ),
        )
        if payload:
            conn.executemany(
                """
                INSERT OR REPLACE INTO fundamentus_signals (
                    snapshot_date, papel, status, failed_step, failed_rule, failed_value, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        conn.commit()
    finally:
        conn.close()
    return len(payload)


def scrape_and_store(
    *,
    pl_min: float = 0.0,
    patrim_min: float = 0.0,
    timeout: float = 30.0,
    snapshot_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    rows = fetch_fundamentus_results(pl_min=pl_min, patrim_min=patrim_min, timeout=timeout)
    return save_snapshot(
        rows,
        snapshot_date=snapshot_date,
        pl_min=pl_min,
        patrim_min=patrim_min,
        negociada=True,
        db_path=db_path,
    )


def apply_filters(
    *,
    snapshot_date: Optional[str] = None,
    cfg: Optional[FundamentusFilterConfig] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    cfg = cfg or FundamentusFilterConfig()
    snap = snapshot_date or latest_snapshot_date(db_path=db_path)
    if not snap:
        return {"total": 0, "approved": 0, "rejected": 0}
    rows = fetch_snapshot(snapshot_date=snap, db_path=db_path)
    signals = evaluate_rows(rows, cfg)
    save_signals(signals, snapshot_date=snap, cfg=cfg, db_path=db_path)
    approved = sum(1 for s in signals if s.get("status") == "approved")
    rejected = sum(1 for s in signals if s.get("status") == "rejected")
    return {"total": len(signals), "approved": approved, "rejected": rejected}


def latest_snapshot_date(db_path: Optional[Path] = None) -> Optional[str]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(snapshot_date) AS d FROM fundamentus_snapshots").fetchone()
        return row["d"] if row else None
    finally:
        conn.close()


def fetch_snapshot(
    *,
    snapshot_date: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, object]]:
    conn = _connect(db_path)
    try:
        snap = snapshot_date or latest_snapshot_date(db_path=db_path)
        if not snap:
            return []
        query = "SELECT * FROM fundamentus_snapshots WHERE snapshot_date = ?"
        params: list[object] = [snap]
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_signals(
    *,
    snapshot_date: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, object]]:
    conn = _connect(db_path)
    try:
        snap = snapshot_date or latest_snapshot_date(db_path=db_path)
        if not snap:
            return []
        query = "SELECT * FROM fundamentus_signals WHERE snapshot_date = ?"
        params: list[object] = [snap]
        if status:
            query += " AND status = ?"
            params.append(status)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_filter_run(
    *,
    snapshot_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, object]]:
    conn = _connect(db_path)
    try:
        snap = snapshot_date or latest_snapshot_date(db_path=db_path)
        if not snap:
            return None
        row = conn.execute(
            """
            SELECT *
            FROM fundamentus_filter_runs
            WHERE snapshot_date <= ?
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            (snap,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_signal_dates(
    *,
    end_date: Optional[str] = None,
    limit: int = 2,
    db_path: Optional[Path] = None,
) -> List[str]:
    conn = _connect(db_path)
    try:
        query = "SELECT DISTINCT snapshot_date FROM fundamentus_signals"
        params: list[object] = []
        if end_date:
            query += " WHERE snapshot_date <= ?"
            params.append(end_date)
        query += " ORDER BY snapshot_date DESC"
        if limit and limit > 0:
            query += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(query, params).fetchall()
        return [row["snapshot_date"] for row in rows if row and row["snapshot_date"]]
    finally:
        conn.close()


def fetch_approved_ranking(
    *,
    window_days: Optional[int] = None,
    snapshot_date: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> Dict[str, object]:
    conn = _connect(db_path)
    try:
        end_date = snapshot_date or latest_snapshot_date(db_path=db_path)
        if not end_date:
            return {"rows": [], "start_date": None, "end_date": None, "window_days": window_days}
        start_date: Optional[str] = None
        if window_days and window_days > 0:
            try:
                end_dt = dt.date.fromisoformat(end_date)
                start_dt = end_dt - dt.timedelta(days=max(window_days - 1, 0))
                start_date = start_dt.isoformat()
            except ValueError:
                start_date = None

        query = "SELECT papel, COUNT(*) AS approvals FROM fundamentus_signals WHERE status = 'approved'"
        params: list[object] = []
        if start_date:
            query += " AND snapshot_date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif snapshot_date:
            query += " AND snapshot_date <= ?"
            params.append(end_date)
        query += " GROUP BY papel ORDER BY approvals DESC, papel ASC"
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(query, params).fetchall()
        return {
            "rows": [{"papel": r["papel"], "approvals": r["approvals"]} for r in rows],
            "start_date": start_date,
            "end_date": end_date,
            "window_days": window_days,
        }
    finally:
        conn.close()


__all__ = [
    "FundamentusFilterConfig",
    "fetch_fundamentus_results",
    "parse_result_table",
    "normalize_rows",
    "evaluate_row",
    "evaluate_rows",
    "save_snapshot",
    "save_signals",
    "scrape_and_store",
    "apply_filters",
    "latest_snapshot_date",
    "fetch_snapshot",
    "fetch_signals",
    "fetch_filter_run",
    "fetch_signal_dates",
    "fetch_approved_ranking",
]
