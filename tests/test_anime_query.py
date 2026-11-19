import unittest
from datetime import date

from tools import anime_query as aq

TODAY = date(2026, 11, 9)
P = lambda text: aq.parse_query(text, TODAY)  # noqa: E731

DOC_EXAMPLE = ("Найди аниме, где главный герой сначала слабый, но постепенно становится очень сильным. "
               "Желательно фэнтези, магия и 2020–2026 годы.")


class ParseTests(unittest.TestCase):
    def test_example_from_spec(self):
        q = P(DOC_EXAMPLE)
        self.assertEqual(q.genres, ["Fantasy"])
        self.assertEqual(q.tags, ["Magic"])
        self.assertEqual((q.year_from, q.year_to), (2020, 2026))
        self.assertEqual(q.traits, ["weak_to_strong"])
        self.assertIsNone(q.title)
        self.assertFalse(aq.needs_llm(q))
        self.assertIn("фэнтези", q.describe())
        self.assertIn("2020–2026", q.describe())

    def test_years(self):
        self.assertEqual((P("аниме с 2015 по 2018").year_from, P("аниме с 2015 по 2018").year_to), (2015, 2018))
        q = P("аниме после 2020"); self.assertEqual((q.year_from, q.year_to), (2020, None))
        q = P("аниме до 2010 года"); self.assertEqual((q.year_from, q.year_to), (None, 2010))
        q = P("аниме 2023 года"); self.assertEqual((q.year_from, q.year_to), (2023, 2023))
        q = P("аниме 90-х"); self.assertEqual((q.year_from, q.year_to), (1990, 1999))
        q = P("новинки про магию"); self.assertEqual((q.year_from, q.year_to), (2024, None))
        q = P("аниме за последние 5 лет"); self.assertEqual(q.year_from, 2021)
        q = P("аниме 2030-2040"); self.assertEqual(q.year_to, 2027)   # не дальше следующего года

    def test_format_count_sort(self):
        q = P("топ 5 фильмов про магию")
        self.assertEqual((q.formats, q.limit, q.sort), (["MOVIE"], 5, "SCORE_DESC"))
        self.assertEqual(P("посоветуй десять аниме про школу").limit, 10)
        self.assertEqual(P("популярные сериалы про самураев").sort, "POPULARITY_DESC")
        self.assertEqual(P("сериал про магию").formats, ["TV", "ONA"])
        self.assertEqual(P("12 серий фэнтези").limit, 8)   # «12 серий» — не количество аниме

    def test_requested_count_and_cap(self):
        q = P("Найди 20 аниме 2020–2026 годов, где главный герой сначала слабый, а затем становится невероятно сильным.")
        self.assertEqual((q.limit, q.requested, q.year_from, q.year_to), (20, 20, 2020, 2026))
        q = P("топ 50 аниме про магию"); self.assertEqual((q.limit, q.requested), (aq.MAX_LIMIT, 50))
        q = P("топ 100 аниме"); self.assertEqual((q.limit, q.requested), (aq.MAX_LIMIT, 100))
        q = P("двадцать аниме про магию"); self.assertEqual((q.limit, q.requested), (20, 20))
        q = P("пятнадцать тайтлов про школу"); self.assertEqual((q.limit, q.requested), (15, 15))
        q = P("аниме про магию"); self.assertEqual((q.limit, q.requested), (8, 0))

    def test_followup_count_replaces_previous(self):
        prev = P("топ 5 аниме про магию")
        self.assertEqual(aq.merge_followup(prev, P("покажи ещё")).limit, 5)
        q = aq.merge_followup(prev, P("а теперь 12 аниме"))
        self.assertEqual((q.limit, q.requested), (12, 12))

    def test_genre_tag_overlap_and_protagonist(self):
        q = P("аниме про футбол")
        self.assertEqual((q.genres, q.tags), (["Sports"], ["Football"]))
        self.assertIn("Male Protagonist", P("аниме с мужским главным героем").tags)
        self.assertIn("Female Protagonist", P("аниме с женской главной героиней").tags)

    def test_excludes(self):
        q = P("топ 5 комедий про школу без романтики")
        self.assertEqual((q.genres, q.tags, q.exclude_genres), (["Comedy"], ["School"], ["Romance"]))
        self.assertNotIn("romance", q.keywords)
        q = P("фэнтези без комедии и экшена, желательно магия")
        self.assertEqual(q.exclude_genres, ["Comedy", "Action"])
        self.assertEqual((q.genres, q.tags), (["Fantasy"], ["Magic"]))

    def test_reference_and_title(self):
        q = P("Посоветуй что-нибудь похожее на Solo Leveling")
        self.assertEqual(q.reference, "Solo Leveling")
        self.assertIsNone(q.title)
        q = P("Мне понравилось Solo Leveling, но хочу, чтобы главный герой был ещё сильнее.")
        self.assertEqual(q.reference, "Solo Leveling")
        self.assertEqual(q.traits, ["overpowered"])
        q = P("расскажи про аниме Attack on Titan")
        self.assertEqual(q.title, "Attack on Titan")
        self.assertFalse(q.has_filters())

    def test_traits(self):
        self.assertEqual(P("аниме где герой непобедим").traits, ["overpowered"])
        self.assertEqual(P("аниме про слабого героя, который прокачивается").traits, ["weak_to_strong"])
        self.assertEqual(P("аниме про магию").traits, [])

    def test_want_more(self):
        self.assertTrue(P("покажи ещё").want_more)
        self.assertTrue(P("ещё варианты").want_more)
        self.assertTrue(P("другие").want_more)
        self.assertFalse(P("ещё сильнее").want_more)       # «ещё» здесь усиливает, а не просит новых

    def test_unknown_russian_needs_llm(self):
        q = P("расскажи про аниме Наруто")
        self.assertTrue(aq.needs_llm(q))
        self.assertFalse(aq.needs_llm(P("найди аниме про футбол")))

    def test_legacy_queries_kept(self):
        self.assertIn("cars", aq.legacy_queries("найди аниме про машины"))
        self.assertEqual(aq.legacy_queries("найди исекай")[0], "isekai")


class FollowupTests(unittest.TestCase):
    def test_example_followup(self):
        prev = P(DOC_EXAMPLE)
        q = aq.merge_followup(prev, P("Мне понравилось Solo Leveling, но хочу, чтобы главный герой был ещё сильнее."))
        self.assertEqual((q.genres, q.tags), (["Fantasy"], ["Magic"]))
        self.assertEqual((q.year_from, q.year_to), (2020, 2026))
        self.assertEqual(q.traits, ["overpowered"])          # «ещё сильнее» заменяет «растёт в силе»
        self.assertEqual(q.reference, "Solo Leveling")
        self.assertTrue(q.followup)

    def test_exclude_removes_from_includes(self):
        prev = P("комедия и фэнтези про магию")
        q = aq.merge_followup(prev, P("без комедии"))
        self.assertEqual(q.genres, ["Fantasy"])
        self.assertEqual(q.exclude_genres, ["Comedy"])

    def test_want_more_and_year_replace(self):
        prev = P("фэнтези 2015-2018")
        q = aq.merge_followup(prev, P("покажи ещё"))
        self.assertTrue(q.want_more)
        self.assertEqual((q.year_from, q.year_to), (2015, 2018))      # прежние критерии сохранены
        q = aq.merge_followup(prev, P("поновее"))
        self.assertEqual(q.year_from, 2023)                          # новое значение заменяет год

    def test_prev_is_not_mutated(self):
        prev = P("фэнтези")
        aq.merge_followup(prev, P("без комедии"))
        self.assertEqual(prev.exclude_genres, [])


class LlmHelpersTests(unittest.TestCase):
    def test_extract_json_from_noise(self):
        self.assertEqual(aq.extract_json('Вот: ```json\n{"genres": ["Fantasy"]}\n``` готово'), {"genres": ["Fantasy"]})
        self.assertIsNone(aq.extract_json("нет json"))
        self.assertEqual(aq.extract_json('{ битый } {"a": 1}'), {"a": 1})

    def test_sanitize_filters_untrusted_values(self):
        data = {"genres": ["fantasy", "Nonexistent", "sci fi", 5], "tags": ["Magic", "Made Up"],
                "year_from": "2030", "year_to": 2024, "formats": ["tv", "BOOK"],
                "title": "Naruto", "reference": "{evil}\n", "keywords_en": ["ninja", "рус", "x" * 50],
                "traits": ["overpowered", "bogus"], "sort": "RANDOM", "exclude_genres": ["Comedy"]}
        q = aq.sanitize_llm_query(data, "текст", today=TODAY)
        self.assertEqual(q.genres, ["Fantasy", "Sci-Fi"])
        self.assertEqual(q.tags, ["Magic"])
        self.assertEqual((q.year_from, q.year_to), (None, 2024))      # 2030 вне допустимого диапазона
        self.assertEqual(q.formats, ["TV"])
        self.assertEqual(q.title, "Naruto")
        self.assertIsNone(q.reference)
        self.assertEqual(q.keywords, ["ninja"])
        self.assertEqual(q.traits, ["overpowered"])
        self.assertIsNone(q.sort)
        self.assertEqual(q.exclude_genres, ["Comedy"])

    def test_sanitize_respects_tag_whitelist(self):
        q = aq.sanitize_llm_query({"tags": ["Magic", "Isekai"]}, "", tag_names=["Isekai"], today=TODAY)
        self.assertEqual(q.tags, ["Isekai"])

    def test_sanitize_rejects_non_dict(self):
        self.assertIsNone(aq.sanitize_llm_query(["x"], "t"))
        self.assertIsNone(aq.sanitize_llm_query(None, "t"))

    def test_combine_rules_take_priority(self):
        base = P("аниме 2020-2022 про магию")
        extra = aq.sanitize_llm_query({"genres": ["Fantasy"], "year_from": 1999, "title": "Naruto"}, "", today=TODAY)
        q = aq.combine_queries(base, extra)
        self.assertEqual((q.year_from, q.year_to), (2020, 2022))
        self.assertEqual(q.genres, ["Fantasy"])
        self.assertEqual(q.tags, ["Magic"])
        self.assertEqual(q.title, "Naruto")
        self.assertIs(aq.combine_queries(base, None), base)

    def test_parse_prompt_lists_allowed_values(self):
        msgs = aq.build_parse_messages("тест", TODAY, tag_names=["Magic"])
        self.assertIn("Fantasy", msgs[0]["content"])
        self.assertIn("Magic", msgs[0]["content"])
        self.assertNotIn("Isekai", msgs[0]["content"])             # нет в списке тегов AniList
        self.assertIn("09.11.2026", msgs[1]["content"])

    def test_parse_reasons(self):
        text = "1. Герой растёт.\n2) **Магия** и фэнтези\nмусор\n7. вне диапазона\n1. дубль"
        self.assertEqual(aq.parse_reasons(text, 3), {1: "Герой растёт.", 2: "Магия и фэнтези"})
        self.assertEqual(aq.parse_reasons("", 3), {})

    def test_franchise_key(self):
        k = aq.franchise_key
        self.assertEqual(k("Solo Leveling Season 2 -Arise from the Shadow-"), k("Solo Leveling"))
        self.assertEqual(k("Overlord IV"), k("Overlord"))
        self.assertEqual(k("Re:Zero 2nd Season"), k("Re:Zero"))
        self.assertNotEqual(k("Naruto"), k("Bleach"))


if __name__ == "__main__":
    unittest.main()
