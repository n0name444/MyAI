import unittest

from tools.calculator import SafeCalculator as C


class CalculatorTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(C.answer("Посчитай 125 × 48"), "125 × 48 = 6000")
        self.assertEqual(C.solve("2^10")[1], "1024")
        self.assertEqual(C.solve("25 x 4")[1], "100")
        self.assertEqual(C.solve("сколько будет 12 умножить на 3")[1], "36")
        self.assertEqual(C.solve("0.1+0.2")[1], "0.3")
        self.assertEqual(C.solve("2,5*2")[1], "5")
        self.assertEqual(C.solve("7%3")[1], "1")
        self.assertEqual(C.solve("0.1+0.2")[1], "0.3")
        self.assertEqual(C.solve("12 поделить на 3")[1], "4")
        self.assertEqual(C.solve("2 + 3 =")[1], "5")
        self.assertEqual(C.solve("-7//3")[1], "-3")
        self.assertEqual(C.answer("-7//3"), "-7 // 3 = -3")
        self.assertEqual(C.solve("10**100")[1], str(10 ** 100))

    def test_errors_are_friendly(self):
        self.assertIn("ноль", C.answer("10/0"))
        for bad in ("abc", "(-8)**0.5", "9**9**9", "__import__('os')", "2**1000", "1e400*10"):
            self.assertIn("Не удалось", C.answer(bad), bad)

    def test_ambiguous_or_invalid_input_is_not_silently_changed(self):
        for bad in ("2=3", "1,000+1", "1+2=3"):
            with self.subTest(expression=bad):
                self.assertIn("Не удалось", C.answer(bad))

    def test_input_and_result_limits(self):
        for bad in ("9" * 101, "10**101", "1+" * 45 + "1"):
            with self.subTest(expression=bad):
                self.assertIn("Не удалось", C.answer(bad))


    def test_statistics(self):
        self.assertEqual(C.answer("Найди сумму чисел 10, 20, 30"), "Сумма: 60")
        self.assertEqual(C.answer("Найди среднее значение 10, 20, 30"), "Среднее значение: 20")
        self.assertEqual(C.answer("минимум из чисел 10, 20, 30"), "Минимум: 10")
        self.assertEqual(C.answer("максимум из чисел 10, 20, 30"), "Максимум: 30")
        self.assertEqual(C.answer("среднее 1; 2; 2"), "Среднее значение: 1.666666667")

    def test_unit_conversion(self):
        self.assertEqual(C.answer("Переведи 5 км в м"), "5 км = 5000 м")
        self.assertEqual(C.answer("Переведи 250 см в м"), "250 см = 2.5 м")
        self.assertEqual(C.answer("Переведи 2 кг в г"), "2 кг = 2000 г")
        self.assertEqual(C.answer("Переведи 500 мл в л"), "500 мл = 0.5 л")
        self.assertEqual(C.answer("Переведи 0 градусов Цельсия в фаренгейта"), "0 градусов = 32 фаренгейта")
        self.assertEqual(C.answer("Сколько метров в 3 километрах?"), "3 километрах = 3000 метров")

    def test_unit_conversion_rejects_incompatible_units(self):
        self.assertIn("Не удалось", C.answer("Переведи 5 км в кг"))
        self.assertIn("Не удалось", C.answer("Переведи 5 км в кг"))

    def test_natural_language_percentages(self):
        self.assertEqual(C.answer("Сколько будет 15 процентов от 800?"), "15% от 800 = 120")
        self.assertEqual(C.answer("Увеличь 800 на 15 процентов"), "800 + 15% (120) = 920 (увеличение)")
        self.assertEqual(C.answer("Уменьши 800 на 15%"), "800 − 15% (120) = 680 (уменьшение)")
        self.assertEqual(C.answer("Скидка 20% на 1500"), "Цена после скидки 20%: 1500 − 300 = 1200")
        self.assertEqual(C.answer("Добавь НДС 20% к 1000"), "НДС 20%: 1000 + 200 = 1200")
        self.assertEqual(C.answer("Выдели НДС 20% из 1200"), "В сумме 1200 НДС 20% составляет 200; без НДС: 1000")

    def test_multi_step_natural_language(self):
        self.assertEqual(C.answer("Сначала 100 плюс 20, потом умножь на 2"),
                         "Шаг 1: 100+20 = 120\nШаг 2: 120 × 2 = 240\nИтог: 240")
        self.assertEqual(C.answer("Сначала 100 плюс 20, затем вычти 10"),
                         "Шаг 1: 100+20 = 120\nШаг 2: 120 − 10 = 110\nИтог: 110")
        self.assertEqual(C.answer("Сначала 100 плюс 20, потом умножь на 2, затем вычти 5"),
                         "Шаг 1: 100+20 = 120\nШаг 2: 120 × 2 = 240\nШаг 3: 240 − 5 = 235\nИтог: 235")

    def test_no_code_execution(self):
        with self.assertRaises(ValueError):
            C.calculate("__import__('os').system('echo hi')")


if __name__ == "__main__":
    unittest.main()
