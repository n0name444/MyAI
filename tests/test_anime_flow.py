import unittest

from tests.test_chatbot import Base, FakeModel
from tools import anime_query as aq
from tools.anime import AnimeResult


class ScriptedModel(FakeModel):
    """Отвечает по очереди; когда ответы кончились — повторяет последний."""

    def __init__(self, *answers):
        super().__init__(answers[-1] if answers else "")
        self.answers = list(answers)

    def generate_response(self, prompt, max_new_tokens=100):
        super().generate_response(prompt, max_new_tokens)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def card(i, title="Show", url=True, **kw):
    base = {"id": f"anilist:{i}", "title": f"{title} {i}", "year": 2024, "type": "TV", "episodes": 12, "score": 8.1,
            "genres": ["Fantasy", "Action"], "themes": ["Magic"], "synopsis": "A long synopsis. " * 20,
            "url": f"https://anilist.co/anime/{i}" if url else "", "match_level": 0,
            "reasons": ["жанры: фэнтези", "теги: магия (80%)"]}
    base.update(kw)
    return base


class FakeAnime:
    def __init__(self, pages=None, notes=(), offline=False):
        self.pages = pages or [[card(1), card(2)]]
        self.notes, self.offline = list(notes), offline
        self.queries, self.excluded = [], []

    def find(self, query, limit=None, exclude_ids=()):
        self.queries.append(query)
        self.excluded.append(set(exclude_ids))
        items = self.pages[min(len(self.queries) - 1, len(self.pages) - 1)]
        return AnimeResult(list(items), query, "structured" if items else "empty", list(self.notes), ["AniList"],
                           offline=self.offline and not items)


class LegacyAnime:                       # старый инструмент без find()
    def search(self, text, limit=8):
        return [{"title": "Старый", "year": 2000, "score": 7.0, "genres": ["Action"], "themes": [],
                 "synopsis": "Описание", "url": "https://example.org/old", "source": "X"}]


class AnimeFlowTests(Base):
    def test_cards_reasons_and_sources(self):
        bot = self.make(model=ScriptedModel("1. Герой растёт в силе.\n2. Много магии."), anime=FakeAnime())
        r = bot.reply("Найди аниме про магию и фэнтези")
        text = r["response"]
        self.assertEqual(r["route"], "ANIME")
        self.assertIn("Подборка по запросу (фэнтези, магия):", text)
        self.assertIn("**1. Show 1** · 2024 · сериал · 12 эп. · оценка 8.1", text)
        self.assertIn("Почему подходит: Герой растёт в силе.", text)
        self.assertIn("Почему подходит: Много магии.", text)
        self.assertNotIn("A long synopsis", text)                        # с пояснениями модели синопсис не нужен
        self.assertEqual([s["url"] for s in r["sources"]], ["https://anilist.co/anime/1", "https://anilist.co/anime/2"])
        self.assertTrue(r["model"])                                       # модель писала пояснения -> бейдж модели

    def test_garbage_from_model_falls_back_to_code_reasons(self):
        bot = self.make(model=ScriptedModel("Вот что я думаю про эти тайтлы."), anime=FakeAnime())
        r = bot.reply("Найди аниме про магию")
        self.assertIn("Совпадения: жанры: фэнтези; теги: магия (80%)", r["response"])
        self.assertIn("A long synopsis", r["response"])                  # без пояснений остаётся краткое описание
        self.assertFalse(r["model"])

    def test_model_not_ready_is_not_started(self):
        bot = self.make(anime=FakeAnime())
        bot.model, bot._model_state = None, "idle"                       # модель ещё не загружена
        r = bot.reply("Найди аниме про магию")
        self.assertIn("Совпадения:", r["response"])                      # ответ есть без ожидания загрузки
        self.assertEqual(self.model.prompts, [])

    def test_followup_keeps_criteria_and_skips_shown(self):
        tool = FakeAnime(pages=[[card(1), card(2)], [card(3)]])
        bot = self.make(anime=tool)
        bot.reply("Найди аниме фэнтези про магию после 2020")
        r = bot.reply("покажи ещё")
        self.assertEqual(r["route"], "ANIME")
        q = tool.queries[1]
        self.assertEqual((q.genres, q.tags, q.year_from), (["Fantasy"], ["Magic"], 2020))
        self.assertTrue(q.want_more)
        self.assertEqual(tool.excluded[1], {"anilist:1", "anilist:2"})
        self.assertEqual(tool.excluded[0], set())

    def test_refinement_without_more_does_not_hide_shown(self):
        tool = FakeAnime(pages=[[card(1)], [card(1)]])
        bot = self.make(anime=tool)
        bot.reply("Найди аниме про магию")
        bot.reply("без комедии")
        self.assertEqual(tool.queries[1].exclude_genres, ["Comedy"])
        self.assertEqual(tool.excluded[1], set())

    def test_followup_without_previous_anime_is_chat(self):
        tool = FakeAnime()
        bot = self.make(anime=tool)
        self.assertEqual(bot.reply("покажи ещё")["route"], "CHAT")
        self.assertEqual(tool.queries, [])

    def test_new_chat_forgets_selection(self):
        tool = FakeAnime()
        bot = self.make(anime=tool)
        bot.reply("Найди аниме про магию")
        bot.new_chat()
        self.assertEqual(bot.reply("покажи ещё")["route"], "CHAT")

    def test_llm_parse_used_when_rules_understand_nothing(self):
        tool = FakeAnime()
        model = ScriptedModel('{"title": "Naruto", "genres": ["Action"], "tags": ["Ninja"]}', "1. Классика.\n2. Ниндзя.")
        bot = self.make(model=model, anime=tool)
        bot.reply("расскажи про аниме Наруто")
        q = tool.queries[0]
        self.assertEqual(q.title, "Naruto")
        self.assertEqual(q.genres, ["Action"])
        self.assertEqual(len(model.prompts), 2)                           # разбор + пояснения

    def test_llm_not_called_when_rules_are_enough(self):
        model = ScriptedModel("1. Да.\n2. Да.")
        bot = self.make(model=model, anime=FakeAnime())
        bot.reply("Найди аниме про футбол")
        self.assertEqual(len(model.prompts), 1)                           # только пояснения

    def test_bad_llm_json_is_ignored(self):
        tool = FakeAnime()
        bot = self.make(model=ScriptedModel("совсем не json"), anime=tool)
        r = bot.reply("расскажи про аниме Наруто")
        self.assertEqual(r["route"], "ANIME")
        self.assertIsNone(tool.queries[0].title)

    def test_empty_result_message_includes_criteria_and_notes(self):
        bot = self.make(anime=FakeAnime(pages=[[]], notes=["Не нашёл «X» в каталоге."]))
        text = bot.reply("Найди аниме про магию 2020-2021")["response"]
        self.assertIn("Ничего подходящего не нашёл", text)
        self.assertIn("магия, 2020–2021", text)
        self.assertIn("Не нашёл «X»", text)

    def test_offline_message(self):
        bot = self.make(anime=FakeAnime(pages=[[]], offline=True))
        self.assertIn("интернет", bot.reply("Найди аниме про магию")["response"])

    def test_tool_exception_is_handled(self):
        class Boom(FakeAnime):
            def find(self, *a, **k): raise RuntimeError("boom")
        r = self.make(anime=Boom()).reply("Найди аниме про магию")
        self.assertEqual(r["route"], "ANIME")
        self.assertIn("Не удалось получить данные", r["response"])

    def test_legacy_tool_without_find_still_works(self):
        r = self.make(anime=LegacyAnime()).reply("Найди аниме про магию")
        self.assertEqual(r["route"], "ANIME")
        self.assertIn("Старый", r["response"])

    def test_long_list_reasons_only_for_first_eight(self):
        items = [card(i) for i in range(1, 21)]
        reasons = "\n".join(f"{i}. Причина {i}." for i in range(1, 9))
        model = ScriptedModel(reasons)
        tool = FakeAnime(pages=[items], notes=["Запрошено 50, но за один раз показываю не больше 20."])
        text = self.make(model=model, anime=tool).reply("топ 50 аниме про магию")["response"]
        prompt = model.prompts[-1]
        self.assertIn("[8]", prompt)
        self.assertNotIn("[9]", prompt)                                   # в промпт попали только первые 8
        self.assertEqual(text.count("**"), 40)                            # 20 карточек
        self.assertIn("Почему подходит: Причина 8.", text)
        self.assertNotIn("Причина 9", text)
        self.assertIn("Совпадения: жанры: фэнтези", text)                # у остальных — причины из кода
        self.assertNotIn("A long synopsis", text)
        self.assertIn("Примечание: Запрошено 50", text)

    def test_long_list_without_model_has_no_synopsis_but_short_has(self):
        bot = self.make(anime=FakeAnime(pages=[[card(i) for i in range(1, 11)]]))
        self.assertNotIn("A long synopsis", bot.reply("Найди аниме про магию")["response"])
        bot = self.make(anime=FakeAnime(pages=[[card(i) for i in range(1, 4)]]))
        self.assertIn("A long synopsis", bot.reply("Найди аниме про магию")["response"])

    def test_status_events_order(self):
        bot = self.make(model=ScriptedModel("1. a\n2. b"), anime=FakeAnime())
        statuses = [e["text"] for e in bot.reply_stream("Найди аниме про магию") if e["type"] == "status"]
        self.assertEqual(statuses, ["Разбираю запрос…", "Ищу в каталогах аниме…", "Пишу пояснения…"])


class RouterFollowupTests(unittest.TestCase):
    def setUp(self):
        from router import Router
        self.r = Router()

    def test_followups_only_after_anime(self):
        for text in ("покажи ещё", "ещё сильнее", "без комедии", "посильнее", "больше экшена",
                     "Мне понравилось Solo Leveling, но хочу, чтобы главный герой был ещё сильнее."):
            with self.subTest(text=text):
                d = self.r.decide(text, "ANIME")
                self.assertEqual((d.route, d.followup), ("ANIME", True))
                self.assertEqual(self.r.decide(text).route, "CHAT")           # без контекста — как раньше
                self.assertEqual(self.r.decide(text, "WEATHER").route, "CHAT")

    def test_unrelated_text_after_anime_is_not_followup(self):
        for text in ("Как дела?", "ещё раз посчитай 2+2", "Какая погода в Москве?", "Найди аниме про футбол"):
            with self.subTest(text=text):
                self.assertFalse(self.r.decide(text, "ANIME").followup)
        self.assertEqual(self.r.decide("ещё раз объясни", "ANIME").route, "CHAT")
        self.assertEqual(self.r.decide("посоветуй хороший тайтл").route, "ANIME")


if __name__ == "__main__":
    unittest.main()
