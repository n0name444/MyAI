"""Отдельная SQLite-история вычислений MyAI."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional


class CalculatorHistory:
    def __init__(self, db_path: str = "calculator_history.sqlite3", limit: int = 500):
        self.db_path = os.path.abspath(db_path)
        self.limit = max(1, int(limit))
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS calculations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    result TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_calculations_created ON calculations(id DESC)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _extract_result(answer: str) -> Optional[str]:
        # Для цепочек приоритетен итог; в остальных ответах берём последнее число.
        text = str(answer or "")
        match = re.search(r"(?:Итог|Сумма|Среднее значение|Минимум|Максимум):\s*([-+]?\d+(?:[.,]\d+)?)", text, re.I)
        if match:
            return match.group(1).replace(",", ".")
        numbers = re.findall(r"(?<![A-Za-zА-Яа-я])[-+]?\d+(?:[.,]\d+)?", text)
        return numbers[-1].replace(",", ".") if numbers else None

    def add(self, expression: str, answer: str) -> int:
        result = self._extract_result(answer)
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO calculations(created_at, expression, answer, result) VALUES(?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), str(expression)[:2000], str(answer)[:5000], result),
            )
            db.execute(
                "DELETE FROM calculations WHERE id NOT IN (SELECT id FROM calculations ORDER BY id DESC LIMIT ?)",
                (self.limit,),
            )
            return int(cur.lastrowid)

    def list(self, limit: int = 20):
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, created_at, expression, answer, result FROM calculations ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _validate_date(value: Optional[str], field: str) -> Optional[str]:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Некорректная дата {field}: используйте ГГГГ-ММ-ДД.") from exc
        if parsed.strftime("%Y-%m-%d") != str(value):
            raise ValueError(f"Некорректная дата {field}: используйте ГГГГ-ММ-ДД.")
        return str(value)

    def search(self, expression: str = "", date_from: Optional[str] = None,
               date_to: Optional[str] = None, limit: int = 500):
        """Фильтрует записи по подстроке выражения и UTC-дате создания."""
        date_from = self._validate_date(date_from, "начала")
        date_to = self._validate_date(date_to, "окончания")
        if date_from and date_to and date_from > date_to:
            raise ValueError("Дата начала не может быть позже даты окончания.")
        clauses = []
        params = []
        expression = str(expression or "").strip()[:200]
        if date_from:
            clauses.append("substr(created_at, 1, 10) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("substr(created_at, 1, 10) <= ?")
            params.append(date_to)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, created_at, expression, answer, result FROM calculations" +
                where + " ORDER BY id DESC LIMIT 500",
                params,
            ).fetchall()
            records = [dict(row) for row in rows]
            if expression:
                needle = expression.casefold()
                records = [row for row in records if needle in row["expression"].casefold()]
            return records[:max(1, min(int(limit), 500))]


    @staticmethod
    def _operation_type(expression: str) -> str:
        """Грубая классификация операции для сводной статистики."""
        text = str(expression or '').casefold()
        if re.search(r'\b(среднее|минимум|максимум|сумма|статистик)', text):
            return 'Статистика'
        if re.search(r'\b(переведи|конвертир|в\s+(?:см|мм|км|метр|кг|грамм|литр|мл|°))', text):
            return 'Конвертация'
        if re.search(r'(скидк|ндс|процент|\b\d+\s*%)', text):
            return 'Проценты и скидки'
        if any(op in text for op in ('+', ' плюс ')):
            return 'Сложение'
        if any(op in text for op in ('-', ' минус ')):
            return 'Вычитание'
        if any(op in text for op in ('*', '×', ' умнож')):
            return 'Умножение'
        if any(op in text for op in ('/', '÷', ' дел')):
            return 'Деление'
        return 'Другие'

    def stats(self, days: int = 14):
        """Сводка по сохранённому журналу и ежедневная активность (UTC)."""
        days = max(1, min(int(days), 90))
        with self._connect() as db:
            rows = db.execute(
                "SELECT created_at, expression FROM calculations ORDER BY id DESC LIMIT ?",
                (self.limit,),
            ).fetchall()
        today = datetime.now(timezone.utc).date()
        start = today.fromordinal(today.toordinal() - days + 1)
        daily_map = {}
        operation_map = {}
        for row in rows:
            day = str(row['created_at'])[:10]
            if day >= start.isoformat():
                daily_map[day] = daily_map.get(day, 0) + 1
            operation = self._operation_type(row['expression'])
            operation_map[operation] = operation_map.get(operation, 0) + 1
        daily = []
        for offset in range(days):
            day = start.fromordinal(start.toordinal() + offset).isoformat()
            daily.append({'date': day, 'count': daily_map.get(day, 0)})
        operations = [
            {'name': name, 'count': count}
            for name, count in sorted(operation_map.items(), key=lambda item: (-item[1], item[0]))
        ]
        return {
            'total': len(rows),
            'days': days,
            'daily': daily,
            'operations': operations,
            'top_operation': operations[0]['name'] if operations else None,
            'active_days': sum(1 for item in daily if item['count']),
        }

    def get(self, calculation_id: int):
        with self._connect() as db:
            row = db.execute("SELECT * FROM calculations WHERE id=?", (int(calculation_id),)).fetchone()
            return dict(row) if row else None

    def last(self):
        rows = self.list(1)
        return rows[0] if rows else None

    def clear(self) -> int:
        """Удалить все записи журнала вычислений, не затрагивая чаты и память."""
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM calculations")
            db.execute("DELETE FROM sqlite_sequence WHERE name='calculations'")
            return int(cur.rowcount)
