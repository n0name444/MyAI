"""Безопасные пользовательские формулы и встроенные шаблоны калькулятора."""
from __future__ import annotations

import ast
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


from tools.calculator import SafeCalculator

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,19}$")
_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]{0,19}(?![A-Za-z0-9_])")
_SCIENTIFIC = re.compile(r"(?<![A-Za-z0-9_])(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+(?![A-Za-z0-9_])")
_ALLOWED_NODES = (ast.Expression, ast.Constant, ast.Name, ast.BinOp, ast.UnaryOp,
                  ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
                  ast.UAdd, ast.USub, ast.Load)

TEMPLATES = [
    {"id": "discount", "name": "Цена со скидкой", "expression": "price * (100 - discount) / 100",
     "variables": {"price": "Цена", "discount": "Скидка, %"}},
    {"id": "vat", "name": "Добавить НДС", "expression": "price * (1 + vat / 100)",
     "variables": {"price": "Цена без НДС", "vat": "Ставка НДС, %"}},
    {"id": "purchase", "name": "Стоимость покупки", "expression": "price * quantity",
     "variables": {"price": "Цена за единицу", "quantity": "Количество"}},
    {"id": "profit", "name": "Прибыль", "expression": "revenue - costs",
     "variables": {"revenue": "Выручка", "costs": "Расходы"}},
    {"id": "average", "name": "Среднее значение", "expression": "(a + b + c) / 3",
     "variables": {"a": "Первое число", "b": "Второе число", "c": "Третье число"}},
]


def _expand_scientific(match: re.Match) -> str:
    """Преобразует экспоненциальный литерал в десятичную запись."""
    value = format(Decimal(match.group(0)), "f")
    return value if "." in value else value + ".0"


def evaluate_formula(expression: str, variables: dict) -> Decimal | int | float:
    expression = str(expression or "").strip()
    if not expression or len(expression) > 200:
        raise ValueError("Формула должна содержать от 1 до 200 символов.")
    if not isinstance(variables, dict) or len(variables) > 20:
        raise ValueError("Передайте не более 20 переменных.")
    normalized = {}
    for key, value in variables.items():
        if not isinstance(key, str) or not _NAME.fullmatch(key):
            raise ValueError("Имена переменных: латинская буква и до 19 латинских букв или цифр.")
        try:
            number = Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError):
            raise ValueError(f"Переменная {key}: нужно указать число.") from None
        if not number.is_finite() or abs(number) > Decimal("1e100"):
            raise ValueError(f"Переменная {key}: число вне допустимого диапазона.")
           # Только десятичная запись без экспоненты: SafeCalculator не принимает «e» в литералах.
        normalized[key] = format(number.normalize(), "f")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, MemoryError):
        raise ValueError("Не удалось разобрать формулу.") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > 80 or any(not isinstance(node, _ALLOWED_NODES) for node in nodes):
        raise ValueError("Формула содержит недопустимую операцию.")
    used = {node.id for node in nodes if isinstance(node, ast.Name)}
    missing = sorted(used - normalized.keys())
    extra = sorted(normalized.keys() - used)
    if missing:
        raise ValueError("Не заданы переменные: " + ", ".join(missing))
    if extra:
        raise ValueError("Лишние переменные: " + ", ".join(extra))
    # SafeCalculator принимает десятичные литералы без экспоненциальной записи.
    # Нормализуем экспоненты до подстановки имён, чтобы буква e в 1e5
    # не воспринималась как имя переменной.
    expression = _SCIENTIFIC.sub(_expand_scientific, expression)
    # Подстановка выполняется только после AST-проверки, по токенам имён.
    substituted = _TOKEN.sub(lambda m: f"({normalized[m.group(0)]})", expression)
    return SafeCalculator.calculate(substituted)


class CalculatorFormulas:
    def __init__(self, db_path: str = "calculator_formulas.sqlite3"):
        self.db_path = os.path.abspath(db_path)
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS formulas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                expression TEXT NOT NULL,
                variables TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

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

    def save(self, name: str, expression: str, variables: dict) -> int:
        name = str(name or "").strip()
        if not name or len(name) > 60:
            raise ValueError("Название формулы должно содержать от 1 до 60 символов.")
        if not isinstance(variables, dict):
            raise ValueError("Переменные должны быть объектом.")
        # Проверяем формулу и набор переменных до сохранения.
        evaluate_formula(expression, {k: 1 for k in variables})
        with self._lock, self._connect() as db:
            existing = db.execute("SELECT name FROM formulas").fetchall()
            if any(row["name"].casefold() == name.casefold() for row in existing):
                raise ValueError("Формула с таким названием уже существует.")
            try:
                cur = db.execute("INSERT INTO formulas(name, expression, variables, created_at) VALUES(?,?,?,?)",
                                 (name, expression.strip(), json.dumps(list(variables), ensure_ascii=False),
                                  datetime.now(timezone.utc).isoformat(timespec="seconds")))
            except sqlite3.IntegrityError:
                raise ValueError("Формула с таким названием уже существует.") from None
            return int(cur.lastrowid)

    def list(self):
        with self._connect() as db:
            rows = db.execute("SELECT id,name,expression,variables,created_at FROM formulas ORDER BY name COLLATE NOCASE").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["variables"] = json.loads(item["variables"])
            result.append(item)
        return result

    def get(self, formula_id: int):
        with self._connect() as db:
            row = db.execute("SELECT id,name,expression,variables,created_at FROM formulas WHERE id=?", (int(formula_id),)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["variables"] = json.loads(item["variables"])
        return item

    def delete(self, formula_id: int) -> bool:
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM formulas WHERE id=?", (int(formula_id),))
            return cur.rowcount > 0
