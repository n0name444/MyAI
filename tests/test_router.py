import unittest
from collections import Counter

from router import Router
from tests.route_cases import CASES, FOLLOWUP_CASES


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.r = Router()

    def test_reference_phrases(self):
        """Каждая эталонная фраза должна попадать в свой маршрут; при провале видно, какие именно."""
        bad = {t: (exp, self.r.decide(t).route) for t, exp in CASES if self.r.decide(t).route != exp}
        accuracy = 100 * (len(CASES) - len(bad)) / len(CASES)
        self.assertEqual(bad, {}, f"точность {accuracy:.1f}%; (ожидалось, получено): {bad}")

    def test_followups_depend_on_previous_route(self):
        bad = []
        for text, prev, route, followup in FOLLOWUP_CASES:
            d = self.r.decide(text, prev)
            if (d.route, d.followup) != (route, followup):
                bad.append((text, prev, (route, followup), (d.route, d.followup)))
        self.assertEqual(bad, [], f"уточнения: {bad}")

    def test_case_tables_are_sane(self):
        texts = [t for t, _ in CASES]
        self.assertEqual(len(texts), len(set(texts)), "в эталоне есть повторы")
        self.assertGreaterEqual(len(CASES), 100)
        counts = Counter(route for _, route in CASES)
        for route in ("CHAT", "MEMORY", "COMPUTER", "CALCULATOR", "WEATHER", "ANIME", "WEB_SEARCH"):
            self.assertGreaterEqual(counts[route], 5, f"мало примеров для {route}")

    def test_empty_and_noise(self):
        for text in ("", "   ", None, "\n\t"):
            self.assertEqual(self.r.decide(text).route, "CHAT")
        for text in ("???", "...", "ааааааа", "🙂"):
            self.assertEqual(self.r.decide(text).route, "CHAT")

    def test_decision_is_stable_and_explained(self):
        for text, route in CASES[:40]:
            a, b = self.r.decide(text), self.r.decide(text)
            self.assertEqual((a.route, a.reason, a.followup), (b.route, b.reason, b.followup))
            self.assertTrue(a.reason)

    def test_dangerous_targets_never_become_commands(self):
        for text in ("закрой cmd", "открой cmd", "открой regedit", "открой powershell", "запусти cmd"):
            self.assertEqual(self.r.decide(text).route, "CHAT", text)

    def test_polite_and_filler_prefixes(self):
        for text in ("а теперь открой блокнот", "ещё раз посчитай 2+2", "Пожалуйста, посчитай 2+2"):
            self.assertNotEqual(self.r.decide(text).route, "CHAT", text)


    def test_statistics_and_unit_conversion_route_to_calculator(self):
        for text in (
            "Найди сумму чисел 1, 2 и 3",
            "Найди среднее значение 10, 20, 30",
            "минимум из чисел 4, 5, 6",
            "максимум из чисел 4, 5, 6",
            "Переведи 5 км в м",
            "Переведи 2 кг в г",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.r.decide(text).route, "CALCULATOR")

    def test_long_text_is_fast(self):
        import time
        t0 = time.perf_counter()
        for _ in range(200):
            self.r.decide("слово " * 2000)
        self.assertLess(time.perf_counter() - t0, 5.0)       # нет катастрофического отката регулярок


    def test_calculator_natural_language_requests(self):
        from router import Router, Route
        router = Router()
        for query in (
            "Сколько будет 15 процентов от 800?",
            "Увеличь 800 на 15 процентов",
            "Скидка 20% на 1500",
            "Сначала 100 плюс 20, потом умножь на 2",
        ):
            with self.subTest(query=query):
                self.assertEqual(router.route(query), Route.CALCULATOR)


    def test_calculator_history_commands(self):
        from router import Router, Route
        router = Router()
        cases = {
            "покажи историю вычислений": Route.CALC_HISTORY,
            "последний результат": Route.CALC_LAST,
            "повтори последний расчёт": Route.CALC_REPLAY,
            "повтори расчёт 12": Route.CALC_REPLAY,
            "на основе последнего результата плюс 5": Route.CALC_USE_LAST,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(router.route(query), expected)


if __name__ == "__main__":
    unittest.main()
