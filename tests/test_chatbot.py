import json
import os
import tempfile
import threading
import time
import unittest

import chat_model
from chat_model import ChatBot
from memory import Memory
from tools.calculator_history import CalculatorHistory


class FakeModel:
    def __init__(self, text="Ответ модели"):
        self.text, self.prompts, self.active, self.max_active = text, [], 0, 0

    def generate_response(self, prompt, max_new_tokens=100):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        self.prompts.append(prompt)
        self.active -= 1
        return self.text


class FakeWeb:
    def __init__(self, results): self.results, self.queries = results, []
    def search(self, q, limit=8):
        self.queries.append(q); return [dict(r) for r in self.results]


class FakeWeather:
    def get(self, text): return "Погода: тест"


class FakeComputer:
    def __init__(self): self.commands = []
    def execute(self, cmd): self.commands.append(cmd); return "Открыл Блокнот."


SOURCES = [{"title": "Python.org", "snippet": "Python 3.13 вышла", "url": "https://www.python.org/"},
           {"title": "Blog", "snippet": "что-то", "url": "https://blog.example/py"}]


class Base(unittest.TestCase):
    def make(self, model=None, **tools):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.model = model or FakeModel()
        tools.setdefault("anime", None); tools.setdefault("web", None)
        tools.setdefault("weather", None); tools.setdefault("computer", None)
        return ChatBot(model=self.model, history_file=os.path.join(self.tmp.name, "h.json"),
                       memory=Memory(os.path.join(self.tmp.name, "m.db")), tools=tools,
                       calculator_history=CalculatorHistory(os.path.join(self.tmp.name, "calculator.sqlite3")))


class RouteTests(Base):
    def test_calc_and_name(self):
        b = self.make()
        self.assertEqual(b.reply("Посчитай 125 × 48")["response"], "125 × 48 = 6000")

    def test_calculator_history_and_reuse_last_result(self):
        b = self.make()
        self.assertEqual(b.reply("Посчитай 10 + 5")["response"], "10 + 5 = 15")
        self.assertIn("#1", b.reply("покажи историю вычислений")["response"])
        self.assertIn("Последний результат: 15", b.reply("последний результат")["response"])
        self.assertEqual(b.reply("на основе последнего результата плюс 7")["response"], "15 + 7 = 22")
        self.assertIn("Повтор расчёта #2", b.reply("повтори последний расчёт")["response"])
        self.assertIn("Иван", b.reply("Меня зовут Иван")["response"])
        self.assertEqual(b.reply("как меня зовут?")["response"], "Тебя зовут Иван.")
        
    def test_calc_last_result_is_two_lines(self):
        b = self.make()
        b.reply("Посчитай 10 + 5")
        answer = b.reply("последний результат")["response"]
        self.assertNotIn("\\n", answer)        # литерального «\n» быть не должно
        self.assertIn("15\nРасчёт:", answer)   # настоящий перенос — должен

    def test_name_false_positive(self):
        b = self.make()
        b.reply("меня зовут так же как тебя")
        self.assertIsNone(b.user_name)
        b.reply("меня зовут иван")
        self.assertEqual(b.user_name, "Иван")

    def test_memory_commands_roundtrip(self):
        b = self.make()
        self.assertIn("запомнил", b.reply("запомни: я люблю игры")["response"])
        self.assertIn("я люблю игры", b.reply("что ты помнишь")["response"])   # раньше: «Пока ничего нет»
        self.assertIn("Забыл", b.reply("забудь про игры")["response"])          # раньше: уходило в чат
        self.assertIn("пуста", b.reply("что ты помнишь")["response"] + "пуста")
        self.assertNotIn("игры", b.reply("что ты помнишь")["response"])
        self.assertIn("Не нашёл", b.reply("забудь про космос")["response"])

    def test_computer_prefix_stripped(self):
        comp = FakeComputer(); b = self.make(computer=comp)
        r = b.reply("компьютер: открой блокнот")
        self.assertEqual(r["route"], "COMPUTER"); self.assertEqual(comp.commands, ["открой блокнот"])
        b.reply("открой ютуб"); self.assertEqual(comp.commands[-1], "открой ютуб")

    def test_weather(self):
        b = self.make(weather=FakeWeather())
        self.assertEqual(b.reply("погода в Москве")["response"], "Погода: тест")

    def test_new_chat_vs_clear(self):
        b = self.make()
        b.reply("запомни: я люблю игры"); b.reply("привет")
        self.assertTrue(b.get_history())
        b.new_chat()
        self.assertEqual(b.get_history(), []); self.assertEqual(b.memory.list_notes(), ["я люблю игры"])
        b.clear_history()
        self.assertEqual(b.memory.list_notes(), [])

    def test_history_persists_atomically(self):
        b = self.make(); b.reply("привет")
        with open(b.history_file, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["history"]), 2)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if f.endswith(".tmp")], [])
        b2 = ChatBot(model=self.model, history_file=b.history_file, memory=b.memory, tools={"anime": None, "web": None, "weather": None, "computer": None})
        self.assertEqual(len(b2.get_history()), 2)


class StreamTests(Base):
    def test_event_order_and_shape(self):
        b = self.make()
        events = list(b.reply_stream("Расскажи о себе"))
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "meta"); self.assertEqual(types[-1], "done")
        self.assertIn("delta", types)
        self.assertEqual(events[-1]["response"], "Ответ модели"); self.assertEqual(events[-1]["route"], "CHAT")

    def test_chat_prompt_contains_history_and_notes(self):
        b = self.make()
        b.reply("запомни: у меня есть собака Рекс")
        b.reply("привет")
        b.reply("как зовут мою собаку?")
        self.assertIn("собака Рекс", self.model.prompts[-1])

    def test_generation_is_serialized(self):
        b = self.make()
        threads = [threading.Thread(target=b.reply, args=(f"вопрос {i}",)) for i in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(self.model.max_active, 1)

    def test_think_blocks_removed(self):
        self.assertEqual(ChatBot._clean("<think>размышляю</think>Привет"), "Привет")
        self.assertEqual(ChatBot._clean("Привет<think>обрыв без конца"), "Привет")


class WebTests(Base):
    def test_web_answer_has_separate_sources_and_no_truncation(self):
        long = "Итог. " * 300   # ~1800 символов; раньше источники обрезались общим лимитом
        b = self.make(model=FakeModel(long), web=FakeWeb(SOURCES))
        r = b.reply("Новости про Python")
        self.assertEqual(r["route"], "WEB_SEARCH")
        self.assertEqual([s["url"] for s in r["sources"]][0], "https://www.python.org/")
        self.assertNotIn("Источники", r["response"])
        self.assertIn("Вопрос:", self.model.prompts[-1]); self.assertIn("[1] Python.org", self.model.prompts[-1])

    def test_query_cleaned(self):
        web = FakeWeb(SOURCES); b = self.make(web=web)
        b.reply("найди в интернете рецепт борща")
        self.assertEqual(web.queries, ["рецепт борща"])

    def test_no_results_is_honest(self):
        b = self.make(web=FakeWeb([]))
        self.assertIn("не буду придумывать", b.reply("Курс доллара")["response"])

    def test_empty_model_answer_falls_back_to_listing(self):
        b = self.make(model=FakeModel("<think>x</think>"), web=FakeWeb(SOURCES))
        self.assertIn("Python.org", b.reply("Новости про Python")["response"])


class FakeTokenizer:
    """Токен = слово; шаблон чата = перечисление ролей."""
    truncation_side = "left"
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True, enable_thinking=False):
        return " ".join(f"<{m['role']}> {m['content']}" for m in messages) + " <assistant>"
    def __call__(self, text, add_special_tokens=False, **kw):
        return {"input_ids": text.split()}


class FitTests(unittest.TestCase):
    def setUp(self):
        self.bot = ChatBot.__new__(ChatBot)
        self.bot.tokenizer = FakeTokenizer()
        self.old = chat_model.MAX_INPUT_TOKENS
        chat_model.MAX_INPUT_TOKENS = 120
        self.addCleanup(lambda: setattr(chat_model, "MAX_INPUT_TOKENS", self.old))

    def test_old_history_dropped_question_kept(self):
        msgs = [{"role": "system", "content": "СИСТЕМА инструкция"}]
        for i in range(10):
            msgs += [{"role": "user", "content": " ".join(f"в{i}" for _ in range(20))},
                     {"role": "assistant", "content": " ".join(f"о{i}" for _ in range(20))}]
        msgs.append({"role": "user", "content": "ПОСЛЕДНИЙ вопрос пользователя"})
        out = self.bot._fit_messages(msgs)
        self.assertLessEqual(self.bot._count_tokens(out), 120)
        self.assertEqual(out[0]["content"], "СИСТЕМА инструкция")
        self.assertEqual(out[-1]["content"], "ПОСЛЕДНИЙ вопрос пользователя")
        self.assertEqual(out[1]["role"], "user")          # нет «осиротевшего» ответа ассистента
        self.assertTrue(any("в9" in m["content"] for m in out[1:-1]))   # свежее осталось
        self.assertFalse(any("в0" in m["content"] for m in out))        # старое выброшено

    def test_huge_last_message_is_shortened_not_dropped(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "вопрос " + "слово " * 500}]
        out = self.bot._fit_messages(msgs)
        self.assertLessEqual(self.bot._count_tokens(out), 120)
        self.assertTrue(out[-1]["content"].startswith("вопрос"))


if __name__ == "__main__":
    unittest.main()
