from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional, List, Any, Dict


@dataclass
class Order:
    id: int
    user_id: int
    username: str
    lang: str
    direction: str  # crypto_to_fiat or fiat_to_crypto
    from_asset: str
    to_asset: str
    amount_from: float
    amount_to: float
    rate: float
    fee_pct: float
    status: str  # created, awaiting_proof, processing, done, cancelled
    proof_type: Optional[str] = None  # txid, receipt, reference
    proof_value: Optional[str] = None
    proof_file_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                lang TEXT,
                direction TEXT NOT NULL,
                from_asset TEXT NOT NULL,
                to_asset TEXT NOT NULL,
                amount_from REAL NOT NULL,
                amount_to REAL NOT NULL,
                rate REAL NOT NULL,
                fee_pct REAL NOT NULL,
                status TEXT NOT NULL,
                proof_type TEXT,
                proof_value TEXT,
                proof_file_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        self.conn.commit()

    def create_order(self, **fields) -> int:
        keys = list(fields.keys())
        vals = [fields[k] for k in keys]
        placeholders = ','.join(['?'] * len(keys))
        sql = f"INSERT INTO orders ({','.join(keys)}) VALUES ({placeholders})"
        cur = self.conn.cursor()
        cur.execute(sql, vals)
        self.conn.commit()
        return int(cur.lastrowid)

    def update_order(self, order_id: int, **fields) -> None:
        if not fields:
            return
        keys = list(fields.keys())
        vals = [fields[k] for k in keys]
        sets = ','.join([f"{k}=?" for k in keys])
        sql = f"UPDATE orders SET {sets}, updated_at=datetime('now') WHERE id=?"
        cur = self.conn.cursor()
        cur.execute(sql, vals + [order_id])
        self.conn.commit()

    def get_order(self, order_id: int) -> Optional[Order]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        row = cur.fetchone()
        return self._row_to_order(row) if row else None

    def list_orders(self, limit: int = 20) -> List[Order]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [self._row_to_order(r) for r in rows]

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        return Order(
            id=row['id'],
            user_id=row['user_id'],
            username=row['username'] or '',
            lang=row['lang'] or 'en',
            direction=row['direction'],
            from_asset=row['from_asset'],
            to_asset=row['to_asset'],
            amount_from=row['amount_from'],
            amount_to=row['amount_to'],
            rate=row['rate'],
            fee_pct=row['fee_pct'],
            status=row['status'],
            proof_type=row['proof_type'],
            proof_value=row['proof_value'],
            proof_file_id=row['proof_file_id'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )
