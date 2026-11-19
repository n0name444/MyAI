import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import route_report
from route_journal import RouteJournal, classify_outcome
from tests.test_anime_flow import FakeAnime
from tests.test_chatbot import Base, FakeModel, FakeWeather


def rec(j, **kw):
    base = dict(text="тест", route="CHAT", outcome="ok", ms=5)
    base.update(kw)
    j.record(**base)


class JournalFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "logs" / "routes.jsonl"

    def test_record_and_read_back(self):
        j = RouteJournal(self.path)
        rec(j, text="Какая погода в Москве?", route="WEATHER", reason="погод", prev="ANIME", followup=True, ms=123, llm=False)
        rec(j, outcome="bogus")
        rows = RouteJournal.read_entries(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["route"], rows[0]["prev"], rows[0]["followup"], rows[0]["ms"]), ("WEATHER", "ANIME", True, 123))
        self.assertEqual(rows[1]["outcome"], "ok")                      # неизвестный итог заменяется на ok
        self.assertRegex(rows[0]["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    def test_privacy_masking_and_truncation(self):
        j = RouteJournal(self.path, max_text=50)
        rec(j, text="Мой номер 89161234567, почта ivan@example.com " + "ы" * 100)
        text = RouteJournal.read_entries(self.path)[0]["text"]
        self.assertNotIn("89161234567", text)
        self.assertNotIn("ivan@example.com", text)
        self.assertIn("***", text)
        self.assertLessEqual(len(text), 50)

    def test_rotation_keeps_limited_files_and_order(self):
        j = RouteJournal(self.path, max_bytes=400, backups=2)
        for i in range(40):
            rec(j, text=f"запись {i:02d}")
        names = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(names, ["routes.jsonl", "routes.jsonl.1", "routes.jsonl.2"])
        texts = [r["text"] for r in RouteJournal.read_entries(self.path)]
        self.assertEqual(texts, sorted(texts))                          # от старых к новым
        self.assertEqual(texts[-1], "запись 39")
        self.assertLess(len(texts), 40)                                 # самые старые удалены ротацией

    def test_record_never_raises(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        j = RouteJournal(blocker / "sub" / "routes.jsonl")             # родитель — файл, создать каталог нельзя
        rec(j)                                                           # не должно бросить исключение

    def test_bad_lines_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"route": "CHAT", "ts": "t"}\nмусор\n[1,2]\n{"нет": "route"}\n', encoding="utf-8")
        self.assertEqual(len(RouteJournal.read_entries(self.path)), 1)
        self.assertEqual(RouteJournal.read_entries(Path(self.tmp.name) / "none.jsonl"), [])

    def test_from_env(self):
        with mock.patch.dict(os.environ, {"MYAI_ROUTE_LOG": "0"}):
            self.assertIsNone(RouteJournal.from_env(self.tmp.name))
        with mock.patch.dict(os.environ, {"MYAI_ROUTE_LOG": "1", "MYAI_ROUTE_LOG_PATH": str(self.path)}):
            self.assertEqual(RouteJournal.from_env(self.tmp.name).path, self.path)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MYAI_ROUTE_LOG", None); os.environ.pop("MYAI_ROUTE_LOG_PATH", None)
            self.assertEqual(RouteJournal.from_env(self.tmp.name).path, Path(self.tmp.name) / "logs" / "routes.jsonl")


class ClassifyTests(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(classify_outcome("WEATHER", "Не удалось найти город «Ы»."), "empty")
        self.assertEqual(classify_outcome("WEATHER", "Москва: +5°C"), "ok")
        self.assertEqual(classify_outcome("COMPUTER", "Приложения cmd нет в безопасном списке."), "empty")
        self.assertEqual(classify_outcome("WEB_SEARCH", "Не удалось найти достаточно релевантных источников."), "empty")
        self.assertEqual(classify_outcome("ANIME", "Ничего подходящего не нашёл (критерии: магия)."), "empty")
        self.assertEqual(classify_outcome("CALCULATOR", "2 + 2 = 4"), "ok")
        self.assertEqual(classify_outcome("CHAT", "Не могу точно сказать, но думаю, что…"), "ok")   # свободный текст модели
        self.assertEqual(classify_outcome("ERROR", "что угодно"), "error")


class BotIntegrationTests(Base):
    def make_bot(self, model=None, **tools):
        bot = self.make(model=model, **tools)
        self.jpath = Path(self.tmp.name) / "routes.jsonl"
        bot.journal = RouteJournal(self.jpath)
        return bot

    def rows(self):
        return RouteJournal.read_entries(self.jpath)

    def test_normal_answer_is_recorded(self):
        bot = self.make_bot()
        bot.reply("Посчитай 2+2")
        (row,) = self.rows()
        self.assertEqual((row["route"], row["outcome"], row["prev"], row["followup"], row["error"]),
                         ("CALCULATOR", "ok", None, False, None))
        self.assertEqual(row["reason"], "команда расчёта")
        self.assertIsInstance(row["ms"], int)

    def test_llm_flag_and_prev_route(self):
        bot = self.make_bot(model=FakeModel("Привет!"))
        bot.reply("Посчитай 2+2")
        bot.reply("Расскажи что-нибудь интересное")
        a, b = self.rows()
        self.assertFalse(a["llm"]); self.assertTrue(b["llm"])
        self.assertEqual((b["route"], b["prev"]), ("CHAT", "CALCULATOR"))

    def test_tool_refusal_is_marked_empty(self):
        class Refusing(FakeWeather):
            def get(self, text): return "Не удалось найти такой город."
        bot = self.make_bot(weather=Refusing())
        bot.reply("Какая погода в Ыыыы?")
        self.assertEqual(self.rows()[0]["outcome"], "empty")

    def test_exception_is_recorded_with_real_route(self):
        class Boom(FakeWeather):
            def get(self, text): raise RuntimeError("boom")
        bot = self.make_bot(weather=Boom())
        r = bot.reply("Какая погода в Москве?")
        self.assertEqual(r["route"], "ERROR")                           # пользователь видит ошибку, как раньше
        row = self.rows()[0]
        self.assertEqual((row["route"], row["outcome"], row["error"]), ("WEATHER", "error", "RuntimeError"))

    def test_stop_button_is_recorded(self):
        bot = self.make_bot()
        gen = bot.reply_stream("Посчитай 2+2")
        next(gen)                                                        # получили только первое событие
        gen.close()                                                      # клиент отключился
        self.assertEqual(self.rows()[0]["outcome"], "stopped")

    def test_followup_flag_is_recorded(self):
        bot = self.make_bot(anime=FakeAnime())
        bot.reply("Найди аниме про магию")
        bot.reply("покажи ещё")
        first, second = self.rows()
        self.assertEqual((first["route"], first["followup"]), ("ANIME", False))
        self.assertEqual((second["route"], second["prev"], second["followup"]), ("ANIME", "ANIME", True))

    def test_early_replies_and_empty_input(self):
        bot = self.make_bot()
        bot.reply("Меня зовут Иван")
        bot.reply("   ")
        rows = self.rows()
        self.assertEqual(len(rows), 1)                                   # пустое сообщение не записывается
        self.assertEqual((rows[0]["route"], rows[0]["reason"]), ("CHAT", ""))

    def test_broken_journal_does_not_break_reply(self):
        bot = self.make_bot()
        bot.journal = mock.Mock(); bot.journal.record.side_effect = OSError("disk full")
        self.assertEqual(bot.reply("Посчитай 2+2")["response"], "2 + 2 = 4")

    def test_no_journal_by_default(self):
        bot = self.make()
        self.assertIsNone(bot.journal)
        self.assertEqual(bot.reply("Посчитай 2+2")["response"], "2 + 2 = 4")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "routes.jsonl"
        j = RouteJournal(self.path)
        for ms in (100, 200, 300, 400):
            rec(j, route="WEATHER", ms=ms, reason="погод")
        rec(j, route="WEATHER", outcome="empty", text="погода в Ыыы", ms=900, reason="погод")
        rec(j, route="COMPUTER", outcome="error", error="OSError", text="открой блокнот", ms=50)
        rec(j, route="ANIME", followup=True, ms=7000, text="покажи ещё", reason="уточнение")
        rec(j, route="CHAT", outcome="stopped", ms=10)

    def test_summary_content(self):
        out = route_report.summarize(RouteJournal.read_entries(self.path))
        self.assertIn("Записей: 8", out)
        weather = next(l for l in out.splitlines() if l.startswith("WEATHER"))
        self.assertEqual(weather.split()[:5], ["WEATHER", "5", "4", "1", "0"])      # всего, ок, отказ, ошибки
        self.assertIn("Отказов и ошибок: 2 из 8 (25%)", out)
        self.assertIn("погода в Ыыы", out)
        self.assertIn("[OSError]", out)
        self.assertIn("уточнений: 1", out)
        self.assertIn("7000 мс", out)                                             # самый долгий запрос

    def test_empty_and_cli(self):
        self.assertIn("пуст", route_report.summarize([]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = route_report.main(["--path", str(Path(self.tmp.name) / "нет.jsonl")])
        self.assertEqual(code, 0)
        self.assertIn("не найден", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            route_report.main(["--path", str(self.path), "--last", "2"])
        self.assertIn("Записей: 2", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
