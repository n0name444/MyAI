import os
import tempfile
import unittest

from tools.calculator_history import CalculatorHistory


class CalculatorHistoryTests(unittest.TestCase):
    def test_add_list_last_get_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "calc.sqlite3")
            history = CalculatorHistory(path)
            first_id = history.add("2+2", "2 + 2 = 4")
            history.add("Сначала 10 плюс 5", "Итог: 15")
            self.assertEqual(history.last()["result"], "15")
            self.assertEqual(history.get(first_id)["result"], "4")
            self.assertEqual(len(CalculatorHistory(path).list()), 2)

    def test_limit_keeps_newest_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = CalculatorHistory(os.path.join(tmp, "calc.sqlite3"), limit=2)
            ids = [history.add(f"{n}+1", f"{n} + 1 = {n+1}") for n in range(3)]
            self.assertEqual([r["id"] for r in history.list(10)], list(reversed(ids[1:])))

    def test_sequence_result_is_final_total(self):
        self.assertEqual(CalculatorHistory._extract_result("Шаг 1: 1+2 = 3\nИтог: 3"), "3")


    def test_search_by_expression_and_utc_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = CalculatorHistory(os.path.join(tmp, "calc.sqlite3"))
            first = history.add("Сумма 10 + 20", "Итог: 30")
            second = history.add("100 / 4", "100 / 4 = 25")
            with history._connect() as db:
                db.execute("UPDATE calculations SET created_at=? WHERE id=?", ("2026-11-10T12:00:00+00:00", first))
                db.execute("UPDATE calculations SET created_at=? WHERE id=?", ("2026-11-12T12:00:00+00:00", second))
            self.assertEqual([r["id"] for r in history.search("СУММА")], [first])
            self.assertEqual([r["id"] for r in history.search(date_from="2026-11-11", date_to="2026-11-13")], [second])
            self.assertEqual([r["id"] for r in history.search(date_to="2026-11-10")], [first])

    def test_search_rejects_invalid_and_reversed_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = CalculatorHistory(os.path.join(tmp, "calc.sqlite3"))
            with self.assertRaises(ValueError):
                history.search(date_from="10.11.2026")
            with self.assertRaises(ValueError):
                history.search(date_from="2026-11-12", date_to="2026-11-10")

    def test_stats_daily_and_operation_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = CalculatorHistory(os.path.join(tmp, "calc.sqlite3"))
            history.add("2 + 2", "2 + 2 = 4")
            history.add("10 * 5", "10 * 5 = 50")
            history.add("Сумма 1, 2, 3", "Итог: 6")
            stats = history.stats(7)
            self.assertEqual(stats["total"], 3)
            self.assertEqual(len(stats["daily"]), 7)
            self.assertEqual(sum(x["count"] for x in stats["daily"]), 3)
            counts = {x["name"]: x["count"] for x in stats["operations"]}
            self.assertEqual(counts["Сложение"], 1)
            self.assertEqual(counts["Умножение"], 1)
            self.assertEqual(counts["Статистика"], 1)
            self.assertEqual(stats["top_operation"], "Сложение")

    def test_stats_clamps_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = CalculatorHistory(os.path.join(tmp, "calc.sqlite3"))
            self.assertEqual(len(history.stats(0)["daily"]), 1)
            self.assertEqual(len(history.stats(500)["daily"]), 90)

    def test_clear_removes_records_and_allows_new_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = CalculatorHistory(os.path.join(tmp, "calc.sqlite3"))
            history.add("2+2", "2 + 2 = 4")
            history.add("3+3", "3 + 3 = 6")
            self.assertEqual(history.clear(), 2)
            self.assertEqual(history.list(), [])
            self.assertEqual(history.add("4+4", "4 + 4 = 8"), 1)


if __name__ == "__main__":
    unittest.main()
