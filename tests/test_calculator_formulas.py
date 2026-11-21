import os
import tempfile
import unittest
from decimal import Decimal

from tools.calculator_formulas import CalculatorFormulas, evaluate_formula, TEMPLATES


class FormulaEvaluationTests(unittest.TestCase):
    def test_evaluate_variables(self):
        self.assertEqual(str(evaluate_formula('price * quantity', {'price': '12.5', 'quantity': 4})), '50.0')

    def test_discount_template(self):
        result = evaluate_formula('price * (100 - discount) / 100', {'price': 200, 'discount': 15})
        self.assertEqual(str(result), '170')

    def test_missing_extra_and_invalid_variables(self):
        with self.assertRaisesRegex(ValueError, 'Не заданы'):
            evaluate_formula('a+b', {'a': 1})
        with self.assertRaisesRegex(ValueError, 'Лишние'):
            evaluate_formula('a+b', {'a': 1, 'b': 2, 'c': 3})
        with self.assertRaises(ValueError):
            evaluate_formula('a+1', {'__import__': 1})

    def test_rejects_calls_attributes_and_non_numeric(self):
        for expr in ('__import__("os")', 'a.real', '[a][0]', 'open(1)'):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                evaluate_formula(expr, {'a': 1})
        with self.assertRaises(ValueError):
            evaluate_formula('a+1', {'a': 'nan'})

    def test_division_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            evaluate_formula('a / b', {'a': 1, 'b': 0})


class FormulaStorageTests(unittest.TestCase):
    def test_save_list_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CalculatorFormulas(os.path.join(tmp, 'formulas.db'))
            formula_id = store.save('Стоимость', 'price * quantity', {'price': 'Цена', 'quantity': 'Количество'})
            row = store.get(formula_id)
            self.assertEqual(row['expression'], 'price * quantity')
            self.assertEqual(row['variables'], ['price', 'quantity'])
            with self.assertRaisesRegex(ValueError, 'уже существует'):
                store.save('стоимость', 'a+b', {'a': 'A', 'b': 'B'})
            self.assertTrue(store.delete(formula_id))
            self.assertFalse(store.delete(formula_id))
            self.assertEqual(store.list(), [])

    def test_templates_have_unique_ids(self):
        self.assertEqual(len({t['id'] for t in TEMPLATES}), len(TEMPLATES))


if __name__ == '__main__':
    unittest.main()
class TestFormulaExtremeValues(unittest.TestCase):
    def test_extreme_fractional_values(self):
        # Очень малое и очень большое нецелое: до патча подстановка давала «1.5e-20»
        # с буквой «e», и SafeCalculator отказывал («Недопустимые символы»).
        self.assertEqual(evaluate_formula("x*2", {"x": "1.5e-20"}), Decimal("3.0E-20"))
        self.assertGreater(evaluate_formula("x+1", {"x": "12345678901234567890.5"}), Decimal("1e19"))

    def test_regular_cases_unchanged(self):
        # Обычный кейс (скидка) патч не сломал.
        self.assertEqual(evaluate_formula("price*(100-discount)/100", {"price": "1000", "discount": "15"}), 850)
