import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, db_path: str = "data/stock_agent.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker      TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    open        REAL,
                    high        REAL,
                    low         REAL,
                    close       REAL,
                    volume      REAL,
                    UNIQUE(ticker, date)
                );

                CREATE TABLE IF NOT EXISTS news (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker       TEXT NOT NULL,
                    fetched_at   TEXT NOT NULL,
                    title        TEXT,
                    description  TEXT,
                    published_at TEXT,
                    source       TEXT
                );

                CREATE TABLE IF NOT EXISTS ai_suggestions (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker              TEXT NOT NULL,
                    date                TEXT NOT NULL,
                    recommendation      TEXT,
                    confidence          REAL,
                    key_levels          TEXT,
                    risk_factors        TEXT,
                    feasibility         TEXT,
                    sentiment_score     REAL,
                    key_events          TEXT,
                    stale_news          INTEGER,
                    price_at_suggestion REAL,
                    raw_response        TEXT,
                    actual_return       REAL,
                    accurate            INTEGER,
                    evaluated_at        TEXT
                );

                CREATE TABLE IF NOT EXISTS backtest_results (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at          TEXT NOT NULL,
                    ticker          TEXT NOT NULL,
                    strategy        TEXT,
                    annual_return   REAL,
                    sharpe_ratio    REAL,
                    max_drawdown    REAL,
                    win_rate        REAL
                );

                CREATE TABLE IF NOT EXISTS data_quality_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker     TEXT NOT NULL,
                    logged_at  TEXT NOT NULL,
                    warning    TEXT NOT NULL
                );
            """)

    # ── Price History ────────────────────────────────────────────────────────

    def save_price_history(self, ticker: str, df: pd.DataFrame):
        rows = []
        for dt, row in df.iterrows():
            rows.append((
                ticker,
                str(dt.date()),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row["Volume"]),
            ))
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO price_history (ticker,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
        logger.debug(f"{ticker}: saved {len(rows)} price rows")

    def load_price_history(self, ticker: str, days: int = 365) -> Optional[pd.DataFrame]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT date,open,high,low,close,volume FROM price_history "
                "WHERE ticker=? ORDER BY date DESC LIMIT ?",
                (ticker, days),
            ).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["date", "Open", "High", "Low", "Close", "Volume"])
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").set_index("date")

    # ── News ─────────────────────────────────────────────────────────────────

    def save_news(self, ticker: str, articles: list[dict]):
        now = datetime.utcnow().isoformat()
        rows = [
            (ticker, now, a.get("title"), a.get("description"), a.get("published_at"), a.get("source"))
            for a in articles
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO news (ticker,fetched_at,title,description,published_at,source) VALUES (?,?,?,?,?,?)",
                rows,
            )

    # ── AI Suggestions ───────────────────────────────────────────────────────

    def save_suggestion(self, ticker: str, suggestion: dict, price: Optional[float], raw: str):
        import json
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO ai_suggestions
                   (ticker,date,recommendation,confidence,key_levels,risk_factors,
                    feasibility,sentiment_score,key_events,stale_news,
                    price_at_suggestion,raw_response)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ticker, today,
                    suggestion.get("recommendation"),
                    suggestion.get("confidence"),
                    json.dumps(suggestion.get("key_levels")),
                    json.dumps(suggestion.get("risk_factors")),
                    suggestion.get("feasibility"),
                    suggestion.get("sentiment_score"),
                    json.dumps(suggestion.get("key_events")),
                    int(bool(suggestion.get("stale_news"))),
                    price,
                    raw,
                ),
            )

    def load_recent_suggestions(self, ticker: str, days: int = 3) -> list[dict]:
        """Load last N days of suggestions for a ticker (used for consecutive-buy check)."""
        from datetime import timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_suggestions WHERE ticker=? AND date>=? ORDER BY date DESC",
                (ticker, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unevaluated_suggestions(self, days_ago: int = 30) -> list[dict]:
        """Return suggestions that are ~30 days old and not yet evaluated."""
        from datetime import timedelta
        target_date = (date.today() - timedelta(days=days_ago)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_suggestions WHERE date<=? AND accurate IS NULL",
                (target_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_suggestion_accuracy(self, suggestion_id: int, actual_return: float, accurate: bool):
        with self._conn() as conn:
            conn.execute(
                "UPDATE ai_suggestions SET actual_return=?, accurate=?, evaluated_at=? WHERE id=?",
                (actual_return, int(accurate), datetime.utcnow().isoformat(), suggestion_id),
            )

    # ── Backtest Results ─────────────────────────────────────────────────────

    def save_backtest_result(self, ticker: str, strategy: str, metrics: dict):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO backtest_results
                   (run_at,ticker,strategy,annual_return,sharpe_ratio,max_drawdown,win_rate)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    datetime.utcnow().isoformat(), ticker, strategy,
                    metrics.get("annual_return"), metrics.get("sharpe_ratio"),
                    metrics.get("max_drawdown"), metrics.get("win_rate"),
                ),
            )

    # ── Data Quality Log ─────────────────────────────────────────────────────

    def log_data_warning(self, ticker: str, warning: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO data_quality_log (ticker,logged_at,warning) VALUES (?,?,?)",
                (ticker, datetime.utcnow().isoformat(), warning),
            )

    # ── Accuracy Report ──────────────────────────────────────────────────────

    def get_accuracy_report(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT recommendation,
                          COUNT(*) as total,
                          SUM(accurate) as correct
                   FROM ai_suggestions
                   WHERE accurate IS NOT NULL
                   GROUP BY recommendation"""
            ).fetchall()
        result = {}
        for r in rows:
            result[r["recommendation"]] = {
                "total": r["total"],
                "correct": r["correct"] or 0,
                "accuracy": (r["correct"] or 0) / r["total"] if r["total"] else 0,
            }
        return result
