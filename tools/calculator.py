"""Безопасный калькулятор на AST (без eval/exec). Единственная реализация в проекте."""
from __future__ import annotations

import ast
import math
import operator as op
import re
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction
from typing import Tuple

MAX_POWER = 100
MAX_RESULT = 10 ** 100
MAX_EXPR_LEN = 200
MAX_LITERAL_DIGITS = 100
MAX_AST_NODES = 80
MAX_AST_DEPTH = 20

_PREFIX = re.compile(r"^\s*(?:посчитай|вычисли|сколько будет|реши пример|calculate|calc)\b[:\s]*", re.I)
_WORD_OPS = (
    (re.compile(r"\s*умножить\s+на\s*", re.I), "*"),
    (re.compile(r"\s*(?:разделить|делить|поделить)\s+на\s*", re.I), "/"),
    (re.compile(r"\s*в\s+степени\s*", re.I), "**"),
    (re.compile(r"\s+плюс\s+", re.I), "+"),
    (re.compile(r"\s+минус\s+", re.I), "-"),
)
_ALLOWED = re.compile(r"[0-9+\-*/%().\s]+")
_THOUSANDS_COMMA = re.compile(r"(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)")

_NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?")

# Коэффициенты приведены к базовой единице каждой размерности.
# Температура обрабатывается отдельно, поскольку её шкалы имеют смещение.
_UNIT_ALIASES = {
    "мм": ("length", Decimal("0.001")), "миллиметр": ("length", Decimal("0.001")),
    "миллиметра": ("length", Decimal("0.001")), "миллиметров": ("length", Decimal("0.001")),
    "см": ("length", Decimal("0.01")), "сантиметр": ("length", Decimal("0.01")),
    "сантиметра": ("length", Decimal("0.01")), "сантиметров": ("length", Decimal("0.01")), "сантиметрах": ("length", Decimal("0.01")),
    "м": ("length", Decimal("1")), "метр": ("length", Decimal("1")),
    "метра": ("length", Decimal("1")), "метров": ("length", Decimal("1")), "метрах": ("length", Decimal("1")),
    "км": ("length", Decimal("1000")), "километр": ("length", Decimal("1000")),
    "километра": ("length", Decimal("1000")), "километров": ("length", Decimal("1000")), "километрах": ("length", Decimal("1000")),
    "мг": ("mass", Decimal("0.001")), "миллиграмм": ("mass", Decimal("0.001")),
    "миллиграмма": ("mass", Decimal("0.001")), "миллиграммов": ("mass", Decimal("0.001")),
    "г": ("mass", Decimal("1")), "гр": ("mass", Decimal("1")), "грамм": ("mass", Decimal("1")),
    "грамма": ("mass", Decimal("1")), "граммов": ("mass", Decimal("1")), "граммах": ("mass", Decimal("1")),
    "кг": ("mass", Decimal("1000")), "килограмм": ("mass", Decimal("1000")),
    "килограмма": ("mass", Decimal("1000")), "килограммов": ("mass", Decimal("1000")), "килограммах": ("mass", Decimal("1000")),
    "т": ("mass", Decimal("1000000")), "тонна": ("mass", Decimal("1000000")),
    "тонны": ("mass", Decimal("1000000")), "тонн": ("mass", Decimal("1000000")),
    "мл": ("volume", Decimal("0.001")), "миллилитр": ("volume", Decimal("0.001")),
    "миллилитра": ("volume", Decimal("0.001")), "миллилитров": ("volume", Decimal("0.001")),
    "л": ("volume", Decimal("1")), "литр": ("volume", Decimal("1")),
    "литра": ("volume", Decimal("1")), "литров": ("volume", Decimal("1")), "литрах": ("volume", Decimal("1")),
}
_TEMPERATURE_UNITS = {
    "c": "c", "°c": "c", "с": "c", "цельсия": "c", "градусов": "c", "градуса": "c", "градус": "c",
    "градусов цельсия": "c", "градуса цельсия": "c", "градус цельсия": "c",
    "f": "f", "°f": "f", "фаренгейта": "f", "фаренгейт": "f",
    "k": "k", "к": "k", "кельвин": "k", "кельвина": "k", "кельвинов": "k",
}


class SafeCalculator:
    BIN_OPS = {
        ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
        ast.Div: op.truediv, ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod, ast.Pow: op.pow,
    }
    UNARY_OPS = {ast.UAdd: op.pos, ast.USub: op.neg}

    @staticmethod
    def extract_expression(text: str) -> str:
        """Достаёт математическое выражение из короткой команды на русском/английском."""
        t = (text or "").strip().lower()
        t = _PREFIX.sub("", t)
        for pattern, repl in _WORD_OPS:
            t = pattern.sub(repl, t)
        t = t.replace("×", "*").replace("÷", "/")
        t = re.sub(r"(?<=[\d)])\s*[xх]\s*(?=[\d(])", "*", t)
        t = t.replace("^", "**")
        # Разрешаем один завершающий знак равенства (например, «2 + 3 =»),
        # но не удаляем '=' внутри выражения: «2=3» должно быть ошибкой.
        t = re.sub(r"\s*=\s*$", "", t)
        # Десятичная запятая допустима, но запись вида 1,000 неоднозначна:
        # это может быть и тысяча, и десятичное число в русской локали.
        if _THOUSANDS_COMMA.search(t):
            raise ValueError("Неоднозначная запись числа с запятой")
        t = t.replace(",", ".")
        return t.strip(" ?!.\t")

    @classmethod
    def calculate(cls, text: str):
        expr = cls.extract_expression(text)
        if not expr:
            raise ValueError("Пустое выражение")
        if len(expr) > MAX_EXPR_LEN:
            raise ValueError("Слишком длинное выражение")
        if not _ALLOWED.fullmatch(expr):
            raise ValueError("Недопустимые символы")
        if any(len(token) > MAX_LITERAL_DIGITS for token in _NUMBER_TOKEN.findall(expr)):
            raise ValueError("Слишком длинное число")

        try:
            tree = ast.parse(expr, mode="eval")
        except (SyntaxError, ValueError, MemoryError):
            raise ValueError("Не удалось разобрать выражение") from None
        cls._check_tree(tree)
        # Decimal даёт точные десятичные операции (например, 0.1 + 0.2).
        # Контекст локальный: настройки точности не меняют поведение других модулей.
        with localcontext() as context:
            context.prec = 120
            try:
                return cls._eval(tree.body)
            except (DecimalException, OverflowError, ArithmeticError) as exc:
                if isinstance(exc, ZeroDivisionError):
                    raise
                raise ValueError("Слишком сложное или большое вычисление") from None

    @classmethod
    def _check_tree(cls, tree) -> None:
        """Ограничивает размер и глубину AST до начала вычислений."""
        stack = [(tree, 1)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
                raise ValueError("Слишком сложное выражение")
            stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))

    @classmethod
    def _eval(cls, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if isinstance(node.value, int):
                value = node.value
            else:
                value = Decimal(str(node.value))
            if abs(value) > MAX_RESULT:
                raise ValueError("Слишком большое число")
            return value

        if isinstance(node, ast.BinOp) and type(node.op) in cls.BIN_OPS:
            left, right = cls._eval(node.left), cls._eval(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > MAX_POWER:
                    raise ValueError("Слишком большая степень")
                # Для дробной степени используем math.pow, но комплексные
                # результаты и нечисловые значения не допускаем.
                if isinstance(right, Decimal) and right != right.to_integral_value():
                    result = math.pow(float(left), float(right))
                else:
                    exponent = int(right)
                    result = cls.BIN_OPS[type(node.op)](left, exponent)
            else:
                if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                    raise ZeroDivisionError("Деление на ноль")
                if isinstance(node.op, ast.Div):
                    result = Decimal(left) / Decimal(right)
                elif isinstance(node.op, ast.FloorDiv):
                    # Fraction сохраняет точность и повторяет семантику Python //.
                    result = Fraction(left) // Fraction(right)
                elif isinstance(node.op, ast.Mod):
                    result = Decimal(left) % Decimal(right)
                else:
                    result = cls.BIN_OPS[type(node.op)](left, right)
            if isinstance(result, complex) or not math.isfinite(float(result)):
                raise ValueError("Результат не является конечным вещественным числом")
            if abs(result) > MAX_RESULT:
                raise ValueError("Слишком большое число")
            return result

        if isinstance(node, ast.UnaryOp) and type(node.op) in cls.UNARY_OPS:
            result = cls.UNARY_OPS[type(node.op)](cls._eval(node.operand))
            if abs(result) > MAX_RESULT:
                raise ValueError("Слишком большое число")
            return result
        raise ValueError("Не удалось разобрать пример")

    @staticmethod
    def format_number(value) -> str:
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return format(value.to_integral_value(), "f")
            return format(value.normalize(), ".10g")
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Некорректный результат")
            if value.is_integer() and abs(value) < 1e15:
                return str(int(value))
            return f"{value:.10g}"
        return str(value)

    @staticmethod
    def _parse_decimal(value: str) -> Decimal:
        return Decimal(value.replace(",", "."))

    @classmethod
    def statistics(cls, operation: str, values) -> Decimal:
        """Вычисляет статистику по конечному непустому набору чисел."""
        if operation not in {"sum", "mean", "min", "max"}:
            raise ValueError("Неизвестная статистическая операция")
        if not isinstance(values, (list, tuple)) or not values or len(values) > 1000:
            raise ValueError("Нужно передать от 1 до 1000 чисел")
        nums = [v if isinstance(v, Decimal) else Decimal(str(v)) for v in values]
        if any(not v.is_finite() or abs(v) > MAX_RESULT for v in nums):
            raise ValueError("Числа должны быть конечными и не слишком большими")
        with localcontext() as context:
            context.prec = 120
            if operation == "sum":
                result = sum(nums, Decimal(0))
            elif operation == "mean":
                result = sum(nums, Decimal(0)) / Decimal(len(nums))
            elif operation == "min":
                result = min(nums)
            else:
                result = max(nums)
        if not result.is_finite() or abs(result) > MAX_RESULT:
            raise ValueError("Слишком большой результат")
        return result

    @classmethod
    def convert_units(cls, value, from_unit: str, to_unit: str) -> Decimal:
        """Конвертирует поддерживаемые единицы длины, массы, объёма и температуры."""
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if not amount.is_finite() or abs(amount) > MAX_RESULT:
            raise ValueError("Некорректное или слишком большое значение")
        source = (from_unit or "").strip().lower().replace("ё", "е")
        target = (to_unit or "").strip().lower().replace("ё", "е")
        if source in _TEMPERATURE_UNITS and target in _TEMPERATURE_UNITS:
            a, b = _TEMPERATURE_UNITS[source], _TEMPERATURE_UNITS[target]
            # Сначала переводим в Цельсии, затем в целевую шкалу.
            celsius = amount if a == "c" else (amount - Decimal(32)) * Decimal(5) / Decimal(9) if a == "f" else amount - Decimal("273.15")
            result = celsius if b == "c" else celsius * Decimal(9) / Decimal(5) + Decimal(32) if b == "f" else celsius + Decimal("273.15")
            if b == "k" and result < 0:
                raise ValueError("Температура в кельвинах не может быть ниже абсолютного нуля")
            return result
        if source not in _UNIT_ALIASES or target not in _UNIT_ALIASES:
            raise ValueError("Неизвестная единица измерения")
        source_type, source_factor = _UNIT_ALIASES[source]
        target_type, target_factor = _UNIT_ALIASES[target]
        if source_type != target_type:
            raise ValueError("Нельзя переводить разные типы величин")
        result = amount * source_factor / target_factor
        if not result.is_finite() or abs(result) > MAX_RESULT:
            raise ValueError("Слишком большой результат")
        return result

    @classmethod
    def _percentage_answer(cls, text: str):
        """Распознаёт частые процентные задачи в естественной русской формулировке."""
        t = (text or "").strip().lower().replace("ё", "е")
        number = r"([-+]?\d+(?:[.,]\d+)?)"
        percent = r"(?:%|процент(?:а|ов)?)"
        def dec(raw):
            value = cls._parse_decimal(raw)
            if not value.is_finite() or abs(value) > MAX_RESULT:
                raise ValueError("Некорректное или слишком большое число")
            return value
        def fmt(value):
            if not value.is_finite() or abs(value) > MAX_RESULT:
                raise ValueError("Слишком большой результат")
            return cls.format_number(value)

        # «Сколько будет 15 процентов от 800?» / «15% от 800».
        m = re.search(rf"{number}\s*{percent}\s*от\s*{number}", t)
        if m:
            rate, base = map(dec, m.groups())
            result = base * rate / Decimal(100)
            return f"{fmt(rate)}% от {fmt(base)} = {fmt(result)}"

        # «Увеличь 800 на 15 процентов» / «уменьши 800 на 15%».
        m = re.search(rf"\b(увелич\w*|уменьш\w*|прибавь|добавь|вычти|снизь)\s+(?:сумму\s+)?{number}\s+(?:на|к)\s+{number}\s*{percent}", t)
        if m:
            action, base_raw, rate_raw = m.groups()
            base, rate = dec(base_raw), dec(rate_raw)
            delta = base * rate / Decimal(100)
            is_decrease = action.startswith(("уменьш", "вычти", "снизь"))
            result = base - delta if is_decrease else base + delta
            verb = "уменьшение" if is_decrease else "увеличение"
            return f"{fmt(base)} {'−' if is_decrease else '+'} {fmt(rate)}% ({fmt(delta)}) = {fmt(result)} ({verb})"

        # «Скидка 15% на 800» / «товар 800 рублей, скидка 15%».
        m = re.search(rf"скидк\w*\s*{number}\s*{percent}\s*(?:на|от)?\s*(?:товар\s*)?{number}", t)
        if not m:
            m = re.search(rf"(?:товар|цена|стоимость)\s*(?:равна|составляет)?\s*{number}[^.!?]*скидк\w*\s*{number}\s*{percent}", t)
            if m:
                base_raw, rate_raw = m.groups()
            else:
                base_raw = rate_raw = None
        else:
            rate_raw, base_raw = m.groups()
        if base_raw is not None:
            base, rate = dec(base_raw), dec(rate_raw)
            discount = base * rate / Decimal(100)
            return f"Цена после скидки {fmt(rate)}%: {fmt(base)} − {fmt(discount)} = {fmt(base - discount)}"

        # «Добавь НДС 20% к 1000» и «выдели НДС 20% из 1200».
        m = re.search(rf"\b(?:добавь|начисли)\s+ндс\s*{number}\s*{percent}\s*(?:к|на)\s*{number}", t)
        if m:
            rate, base = map(dec, m.groups())
            tax = base * rate / Decimal(100)
            return f"НДС {fmt(rate)}%: {fmt(base)} + {fmt(tax)} = {fmt(base + tax)}"
        m = re.search(rf"\b(?:выдели|извлеки)\s+ндс\s*{number}\s*{percent}\s*из\s*{number}", t)
        if m:
            rate, gross = map(dec, m.groups())
            base = gross * Decimal(100) / (Decimal(100) + rate)
            tax = gross - base
            return f"В сумме {fmt(gross)} НДС {fmt(rate)}% составляет {fmt(tax)}; без НДС: {fmt(base)}"
        return None

    @classmethod
    def _sequence_answer(cls, text: str):
        """Выполняет короткую последовательность явно заданных арифметических шагов."""
        t = (text or "").strip().lower().replace("ё", "е")
        match = re.match(r"^\s*сначала\s+(.+?)[,;]?\s+(?:затем|потом|после этого)\s+(.+)$", t)
        if not match:
            return None
        first_text, tail = match.groups()
        first_text = re.sub(r"^(?:посчитай|вычисли)\s+", "", first_text)
        first_expr = cls.extract_expression(first_text)
        value = Decimal(cls.calculate(first_expr))
        if not value.is_finite() or abs(value) > MAX_RESULT:
            raise ValueError("Слишком большой результат")
        steps = [f"Шаг 1: {first_expr} = {cls.format_number(value)}"]
        # Разделяем оставшиеся явно обозначенные действия, чтобы поддержать цепочку из нескольких шагов.
        actions = re.split(r"\s*(?:,\s*)?(?:а\s+)?(?:затем|потом|после этого)\s+", tail)
        while actions:
            action = actions.pop(0).strip(" ,;")
            if not action:
                continue
            m = re.search(r"(?:умножь|умножить|умножаем)\s+(?:результат\s+)?на\s+(\d+(?:[.,]\d+)?)", action)
            if m:
                operand = cls._parse_decimal(m.group(1)); old = value; value = old * operand
                if not value.is_finite() or abs(value) > MAX_RESULT: raise ValueError("Слишком большой результат")
                steps.append(f"Шаг {len(steps)+1}: {cls.format_number(old)} × {cls.format_number(operand)} = {cls.format_number(value)}")
                continue
            m = re.search(r"(?:раздели|разделить)\s+(?:результат\s+)?на\s+(\d+(?:[.,]\d+)?)", action)
            if m:
                operand = cls._parse_decimal(m.group(1))
                if operand == 0: raise ZeroDivisionError
                old = value; value = Decimal(old) / operand
                if not value.is_finite() or abs(value) > MAX_RESULT: raise ValueError("Слишком большой результат")
                steps.append(f"Шаг {len(steps)+1}: {cls.format_number(old)} ÷ {cls.format_number(operand)} = {cls.format_number(value)}")
                continue
            m = re.search(r"(?:прибавь|добавь|прибавить)\s+(\d+(?:[.,]\d+)?)", action)
            if m:
                operand = cls._parse_decimal(m.group(1)); old = value; value = old + operand
                if not value.is_finite() or abs(value) > MAX_RESULT: raise ValueError("Слишком большой результат")
                steps.append(f"Шаг {len(steps)+1}: {cls.format_number(old)} + {cls.format_number(operand)} = {cls.format_number(value)}")
                continue
            m = re.search(r"(?:вычти|вычесть|отними)\s+(\d+(?:[.,]\d+)?)", action)
            if m:
                operand = cls._parse_decimal(m.group(1)); old = value; value = old - operand
                if not value.is_finite() or abs(value) > MAX_RESULT: raise ValueError("Слишком большой результат")
                steps.append(f"Шаг {len(steps)+1}: {cls.format_number(old)} − {cls.format_number(operand)} = {cls.format_number(value)}")
                continue
            raise ValueError("Не удалось распознать следующий шаг")
        if len(steps) < 2:
            raise ValueError("Нужно указать хотя бы два шага")
        if not value.is_finite() or abs(value) > MAX_RESULT:
            raise ValueError("Слишком большой результат")
        return "\n".join(steps + [f"Итог: {cls.format_number(value)}"])

    @classmethod
    def _advanced_answer(cls, text: str):
        """Распознаёт ограниченный набор явных статистических и конвертационных запросов."""
        t = (text or "").strip().lower().replace("ё", "е")
        # Статистика: «среднее 10, 20, 30», «найди сумму чисел 1; 2; 3».
        stat_match = re.search(r"\b(сумм\w*|средн\w*|минимум|минимальн\w*|максимум|максимальн\w*)\b", t)
        if stat_match:
            key = stat_match.group(1)
            operation = "sum" if key.startswith("сумм") else "mean" if key.startswith("средн") else "min" if key.startswith("миним") else "max"
            tail = t[stat_match.end():]
            raw_numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", tail)
            if not raw_numbers:
                raise ValueError("Не нашёл числа для статистического расчёта")
            # Разделитель запятая трактуется как разделитель списка, если после неё есть пробел.
            values = [cls._parse_decimal(v) for v in raw_numbers]
            result = cls.statistics(operation, values)
            labels = {"sum": "Сумма", "mean": "Среднее значение", "min": "Минимум", "max": "Максимум"}
            return f"{labels[operation]}: {cls.format_number(result)}"

        # Конвертация: «переведи 5 км в м» / «сколько метров в 3 километрах».
        units = sorted(set(_UNIT_ALIASES) | set(_TEMPERATURE_UNITS), key=len, reverse=True)
        unit_pattern = "(?:" + "|".join(re.escape(u) for u in units) + ")"
        match = re.search(rf"([-+]?\d+(?:[.,]\d+)?)\s*({unit_pattern})\s+(?:в|во|to)\s+({unit_pattern})\b", t)
        if not match:
            match = re.search(rf"(?:сколько\s+)?({unit_pattern})\s+в\s+([-+]?\d+(?:[.,]\d+)?)\s*({unit_pattern})\b", t)
            if match:
                # Форма «сколько метров в 3 километрах» — меняем направление.
                target_unit, amount, source_unit = match.groups()
                result = cls.convert_units(cls._parse_decimal(amount), source_unit, target_unit)
                source_label = "градусов" if source_unit.startswith("градус") else source_unit
                return f"{cls.format_number(cls._parse_decimal(amount))} {source_label} = {cls.format_number(result)} {target_unit}"
        if match:
            amount, source_unit, target_unit = match.groups()
            result = cls.convert_units(cls._parse_decimal(amount), source_unit, target_unit)
            source_label = "градусов" if source_unit.startswith("градус") else source_unit
            return f"{cls.format_number(cls._parse_decimal(amount))} {source_label} = {cls.format_number(result)} {target_unit}"
        return None

    @classmethod
    def solve(cls, text: str) -> Tuple[str, str]:
        """Возвращает (выражение, результат) либо бросает ValueError/ZeroDivisionError."""
        expr = cls.extract_expression(text)
        value = cls.calculate(text)
        return expr, cls.format_number(value)

    @classmethod
    def answer(cls, text: str) -> str:
        try:
            percentage = cls._percentage_answer(text)
            if percentage is not None:
                return percentage
            sequence = cls._sequence_answer(text)
            if sequence is not None:
                return sequence
            advanced = cls._advanced_answer(text)
            if advanced is not None:
                return advanced
            expr, result = cls.solve(text)
            pretty = re.sub(r"\s+", "", expr).replace("**", "^")
            pretty = pretty.replace("//", " // ").replace("*", " × ")
            pretty = re.sub(r"(?<!/)/(?!/)", " ÷ ", pretty)
            pretty = re.sub(r"(?<=[\d)])([+\-])(?=[\d(])", r" \1 ", pretty)
            return f"{pretty} = {result}"
        except ZeroDivisionError:
            return "Нельзя делить на ноль."
        except (ValueError, SyntaxError, RecursionError):
            return "Не удалось разобрать пример. Напиши, например: 25 × 4."
