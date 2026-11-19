import unittest
from datetime import date

from tools import anime_query as aq
from tools.anime import AnimeTool

P = lambda text: aq.parse_query(text, date(2026, 11, 9))  # noqa: E731


def media(mid, title, genres, tags=(), year=2024, pop=50000, score=80, fmt="TV", status="FINISHED", desc=""):
    return {"id": mid, "title": {"romaji": title, "english": title, "native": None}, "description": desc,
            "genres": list(genres), "tags": [{"name": n, "rank": r, "isMediaSpoiler": False} for n, r in tags],
            "averageScore": score, "popularity": pop, "episodes": 12, "status": status, "season": "WINTER",
            "seasonYear": year, "startDate": {"year": year}, "format": fmt,
            "siteUrl": f"https://anilist.co/anime/{mid}", "coverImage": {"large": None}}


def page(items, has_next=False):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": has_next}, "media": items}}}


class FakeNet(AnimeTool):
    """AnimeTool без сети: ответы каталогов задаются в тесте, запросы запоминаются."""

    def __init__(self):
        super().__init__()
        self.tags_response = None            # None — каталог тегов недоступен
        self.discover = {1: page([])}        # страница -> ответ
        self.search_media = []
        self.jikan = []
        self.reject_dates = False            # имитация «API не знает аргумента startDate_greater»
        self.posts = []
        self.tag_calls = 0

    def _post(self, url, payload, retries=1):
        query = payload["query"]
        if "MediaTagCollection" in query:
            self.tag_calls += 1
            if self.tags_response is None:
                self._failures += 1
                return None
            return {"data": {"MediaTagCollection": [{"name": n, "isAdult": False} for n in self.tags_response]}}
        if "pageInfo" in query:
            self.posts.append(payload)
            if self.reject_dates and "startDate_greater" in query:
                self._failures += 1
                return None
            return self.discover.get(payload["variables"]["page"], page([]))
        if "$search" in query:
            return {"data": {"Page": {"media": self.search_media}}}
        self._failures += 1
        return None

    def _get(self, url, params=None, headers=None, retries=1):
        if "jikan" in url:
            if url.endswith("/full"):
                return {"data": self.jikan[0]} if self.jikan else None
            return {"data": self.jikan} if self.jikan else None
        self._failures += 1
        return None


class StructuredTests(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()

    def titles(self, result):
        return [i["title"] for i in result.items]

    def test_filtering_collapse_and_reasons(self):
        magic = [("Magic", 80)]
        self.net.discover = {1: page([
            media(1, "Solo Leveling", ["Action", "Fantasy"], magic, 2024, 400000, 83,
                  desc="The weakest hunter grows stronger and stronger, becoming powerful."),
            media(2, "Solo Leveling Season 2", ["Action", "Fantasy"], magic, 2025, 200000),
            media(3, "Frieren", ["Fantasy", "Adventure"], magic, 2023, 300000),
            media(4, "Comedy Show", ["Comedy"], magic, 2024),
            media(5, "Old Fantasy", ["Fantasy"], magic, 2010),
            media(6, "Obscure", ["Fantasy"], magic, 2022, pop=100),
            media(7, "Upcoming", ["Fantasy"], magic, 2026, status="NOT_YET_RELEASED"),
            media(8, "Fantasy Clip", ["Fantasy"], magic, 2024, fmt="MUSIC"),
            media(9, "No Magic Really", ["Fantasy"], [("Magic", 20)], 2022, 90000),
        ])}
        result = self.net.find(P("фэнтези магия 2020-2026"))
        self.assertEqual(result.mode, "structured")
        self.assertEqual(self.titles(result), ["Solo Leveling", "Frieren", "No Magic Really"])
        first = result.items[0]
        self.assertEqual(first["match_level"], 0)
        self.assertIn("жанры: фэнтези", first["reasons"])
        self.assertIn("теги: магия (80%)", first["reasons"])
        self.assertIn("год: 2024", first["reasons"])
        self.assertEqual(result.items[2]["match_level"], 1)
        self.assertTrue(any("без обязательных тегов" in n for n in result.notes))

    def test_api_filters_sent(self):
        self.net.find(P("фэнтези без комедии 2020-2026 сериал"))
        v = self.net.posts[0]["variables"]
        self.assertEqual(v["genre_in"], ["Fantasy"])
        self.assertEqual(v["genre_not_in"], ["Comedy"])
        self.assertEqual(v["format_in"], ["TV", "ONA"])
        self.assertEqual((v["startDate_greater"], v["startDate_lesser"]), (20191231, 20270101))
        self.assertEqual(v["sort"], ["POPULARITY_DESC"])
        self.assertNotIn("tag_in", v)                       # каталог тегов недоступен -> тег не отправляем

    def test_tag_sent_only_if_verified(self):
        self.net.tags_response = ["Magic", "Isekai"]
        self.net.find(P("фэнтези магия"))
        self.assertEqual(self.net.posts[0]["variables"]["tag_in"], ["Magic"])
        self.net2 = FakeNet(); self.net2.tags_response = ["Isekai"]
        self.net2.find(P("фэнтези магия"))
        self.assertNotIn("tag_in", self.net2.posts[0]["variables"])

    def test_enough_strict_results_hide_partial(self):
        items = [media(i, f"Fantasy {chr(65 + i)}", ["Fantasy"], [("Magic", 70)], 2022, 60000 - i * 100) for i in range(1, 6)]
        items.append(media(50, "Partial", ["Fantasy"], [("Magic", 10)], 2022, 90000))
        self.net.discover = {1: page(items)}
        result = self.net.find(P("фэнтези магия 2020-2026"))
        self.assertEqual(len(result.items), 5)
        self.assertTrue(all(i["match_level"] == 0 for i in result.items))
        self.assertFalse(result.notes)

    def test_local_exclusion_and_two_genres(self):
        self.net.discover = {1: page([
            media(1, "Dark Fantasy", ["Fantasy", "Action"], year=2022),
            media(2, "Funny Fantasy", ["Fantasy", "Comedy"], year=2022),
            media(3, "Only Action", ["Action"], year=2022)])}
        result = self.net.find(P("фэнтези экшен без комедии"))
        self.assertEqual(self.titles(result), ["Dark Fantasy", "Only Action"])   # «Funny Fantasy» отсечена исключением
        self.assertEqual([i["match_level"] for i in result.items], [0, 2])       # второй — только часть жанров
        self.assertTrue(any("только с некоторыми" in n for n in result.notes))

    def test_unsupported_api_arguments_fall_back_to_basic_profile(self):
        self.net.reject_dates = True
        self.net.discover = {1: page([media(1, "In Range", ["Fantasy"], year=2022),
                                      media(2, "Too Old", ["Fantasy"], year=2005)])}
        result = self.net.find(P("фэнтези 2020-2026"))
        self.assertEqual(self.titles(result), ["In Range"])        # годы проверены локально
        self.assertFalse(result.offline)

    def test_traits_are_soft_ranking_signal(self):
        self.net.discover = {1: page([
            media(1, "Plain", ["Fantasy"], year=2022, pop=80000, score=80, desc="A quiet journey."),
            media(2, "Growing", ["Fantasy"], year=2022, pop=80000, score=80,
                  desc="A weak boy trains hard and grows stronger every day.")])}
        result = self.net.find(P("фэнтези где герой слабый но постепенно становится сильным"))
        self.assertEqual(self.titles(result), ["Growing", "Plain"])
        self.assertIn("по описанию: герой растёт в силе", result.items[0]["reasons"])
        self.assertTrue(any("приблизительны" in n for n in result.notes))

    def test_pagination_stops_when_enough(self):
        full = [media(i, f"Title {chr(65 + i)}", ["Fantasy"], year=2022, pop=90000 - i) for i in range(1, 9)]
        self.net.discover = {1: page(full, has_next=True), 2: page([media(99, "Never", ["Fantasy"], year=2022)])}
        self.net.find(P("фэнтези"), limit=8)
        self.assertEqual(len(self.net.posts), 1)

    def test_known_tags_cached_and_backoff(self):
        self.net.tags_response = ["Magic"]
        self.net.known_tags(); self.net.known_tags()
        self.assertEqual(self.net.tag_calls, 1)
        failing = FakeNet()
        self.assertIsNone(failing.known_tags()); self.assertIsNone(failing.known_tags())
        self.assertEqual(failing.tag_calls, 1)                      # повтор не чаще раза в 5 минут

    def test_search_compat_returns_list(self):
        self.net.discover = {1: page([media(1, "Sports Show", ["Sports"], [("Football", 90)], 2020)])}
        items = self.net.search("найди аниме про футбол")
        self.assertEqual([i["title"] for i in items], ["Sports Show"])


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()

    def fantasy(self, n, start=1):
        return [media(start + i, f"Fantasy Title {start + i}", ["Fantasy"], [("Magic", 70)], 2022, 90000 - i)
                for i in range(n)]

    def test_twenty_requested_twenty_shown(self):
        self.net.discover = {1: page(self.fantasy(30))}
        result = self.net.find(P("Найди 20 аниме про фэнтези и магию"))
        self.assertEqual(len(result.items), 20)
        self.assertFalse(result.notes)                                   # всё нашлось — лишних слов нет

    def test_more_than_cap_is_reported(self):
        self.net.discover = {1: page(self.fantasy(40))}
        result = self.net.find(P("топ 50 аниме про фэнтези"))
        self.assertEqual(len(result.items), 20)
        self.assertIn("Запрошено 50, но за один раз показываю не больше 20.", result.notes)

    def test_shortfall_is_reported(self):
        self.net.discover = {1: page(self.fantasy(6))}
        result = self.net.find(P("Найди 20 аниме про фэнтези"))
        self.assertEqual(len(result.items), 6)
        self.assertTrue(any(n.startswith("Нашлось 6 из 20 запрошенных") for n in result.notes))

    def test_no_count_note_without_explicit_number(self):
        self.net.discover = {1: page(self.fantasy(3))}
        self.assertFalse(self.net.find(P("аниме про фэнтези")).notes)
        self.net.discover = {1: page(self.fantasy(8))}
        result = self.net.find(P("топ 5 аниме про фэнтези"))
        self.assertEqual((len(result.items), result.notes), (5, []))

    def test_explicit_limit_argument_is_respected_and_capped(self):
        self.net.discover = {1: page(self.fantasy(30))}
        self.assertEqual(len(self.net.find(P("аниме про фэнтези"), limit=3).items), 3)
        self.assertEqual(len(self.net.find(P("аниме про фэнтези"), limit=99).items), 20)

    def test_many_pages_are_fetched_for_twenty(self):
        self.net.discover = {1: page(self.fantasy(8, 100), has_next=True), 2: page(self.fantasy(8, 200), has_next=True),
                             3: page(self.fantasy(8, 300), has_next=True), 4: page(self.fantasy(8, 400))}
        result = self.net.find(P("Найди 20 аниме про фэнтези"))
        self.assertEqual(len(result.items), 20)
        self.assertEqual(len(self.net.posts), 3)                         # не больше MAX_PAGES запросов

    def test_title_path_also_reports_shortfall(self):
        net = FakeNet(); net.jikan = [FallbackTests.JIKAN]
        net.discover = {1: page([])}
        result = net.find(P("найди 15 аниме про машины"))
        self.assertEqual(len(result.items), 1)
        self.assertTrue(any("Нашлось 1 из 15" in n for n in result.notes))


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()
        self.net.search_media = [media(1, "Solo Leveling", ["Action", "Adventure", "Fantasy"],
                                       [("Dungeon", 90), ("Magic", 80), ("Male Protagonist", 55), ("Gore", 30)])]

    def test_similar_excludes_reference_and_its_seasons(self):
        self.net.discover = {1: page([
            media(1, "Solo Leveling", ["Action", "Fantasy"], [("Dungeon", 90), ("Magic", 80)]),
            media(2, "Solo Leveling Season 2", ["Action", "Fantasy"], [("Dungeon", 90), ("Magic", 80)]),
            media(3, "Dungeon Hero", ["Action", "Fantasy"], [("Dungeon", 70), ("Magic", 65)], pop=70000),
            media(4, "Magic Only", ["Action", "Fantasy"], [("Magic", 70)], pop=80000)])}
        result = self.net.find(P("что-нибудь похожее на Solo Leveling"))
        names = [i["title"] for i in result.items]
        self.assertNotIn("Solo Leveling", names)
        self.assertNotIn("Solo Leveling Season 2", names)
        self.assertEqual(names[0], "Dungeon Hero")                  # совпал по всем жанрам и тегам образца
        self.assertTrue(any("Ориентируюсь на «Solo Leveling»" in n for n in result.notes))

    def test_reference_plus_criteria_keeps_user_criteria_hard(self):
        self.net.discover = {1: page([
            media(3, "Fantasy Hit", ["Fantasy"], [("Magic", 70)], 2023, 90000),
            media(4, "Old Hit", ["Fantasy"], [("Magic", 70)], 2005, 90000)])}
        q = aq.merge_followup(P("фэнтези магия 2020-2026"), P("Мне понравилось Solo Leveling, но хочу, чтобы главный герой был ещё сильнее."))
        result = self.net.find(q)
        self.assertEqual([i["title"] for i in result.items], ["Fantasy Hit"])

    def test_reference_not_found_is_reported(self):
        self.net.search_media = []
        result = self.net.find(P("что-нибудь похожее на Zzzz Qqqq"))
        self.assertEqual(result.mode, "empty")
        self.assertTrue(any("Не нашёл «Zzzz Qqqq»" in n for n in result.notes))


class FallbackTests(unittest.TestCase):
    JIKAN = {"mal_id": 16498, "title": "Shingeki no Kyojin", "title_english": "Attack on Titan", "score": 8.5,
             "synopsis": "Humanity fights titans.", "url": "https://myanimelist.net/anime/16498",
             "genres": [{"name": "Action"}], "themes": [{"name": "Gore"}], "aired": {"from": "2013-04-07T00:00:00+00:00"},
             "type": "TV", "episodes": 25, "status": "Finished Airing", "images": {"jpg": {}}}

    def test_title_query_uses_catalog_chain(self):
        net = FakeNet(); net.jikan = [self.JIKAN]
        result = net.find(P("расскажи про аниме Attack on Titan"))
        self.assertEqual(result.mode, "title")
        self.assertEqual(result.items[0]["title"], "Shingeki no Kyojin")
        self.assertEqual(result.items[0]["year"], "2013")
        self.assertIn("MyAnimeList / Jikan", result.sources_used)
        self.assertFalse(result.offline)

    def test_structured_miss_falls_back_to_keywords(self):
        net = FakeNet(); net.jikan = [self.JIKAN]
        net.discover = {1: page([media(1, "Unrelated", ["Drama"], year=2020)])}
        result = net.find(P("найди аниме про машины"))            # тег Cars не подтверждён -> структурный поиск пуст
        self.assertEqual(result.mode, "legacy")
        self.assertEqual(len(result.items), 1)
        self.assertTrue(any("ничего подходящего" in n for n in result.notes))

    def test_offline_is_reported(self):
        result = FakeNet().find(P("аниме про магию"))
        self.assertEqual(result.items, [])
        self.assertTrue(result.offline)

    def test_no_results_online_is_not_offline(self):
        net = FakeNet()
        net.discover = {1: page([])}
        net._failures = 0
        # каталог ответил пусто, а не ошибкой: «offline» быть не должно
        original = net._get
        net._get = lambda *a, **k: ({"data": []} if "jikan" in a[0] else original(*a, **k))
        result = net.find(P("фэнтези"))
        self.assertEqual(result.items, [])
        self.assertFalse(result.offline)


class NormalizeTests(unittest.TestCase):
    def test_kitsu_scale_and_genres(self):
        class K(AnimeTool):
            def _get(self, url, params=None, headers=None, retries=1):
                return {"data": [{"id": "1", "attributes": {"titles": {"en": "Kitsu Show"}, "averageRating": "82.7",
                                                            "startDate": "2021-04-01", "subtype": "TV", "episodeCount": 12,
                                                            "slug": "kitsu-show", "synopsis": "x"},
                                  "relationships": {"genres": {"data": [{"id": "7", "type": "genres"}]}}}],
                        "included": [{"id": "7", "type": "genres", "attributes": {"name": "Science Fiction"}}]}
        item = K()._kitsu_search("x")[0]
        self.assertEqual(item["score"], 8.3)                      # 82.7 / 10
        self.assertEqual(item["genres"], ["Sci-Fi"])
        self.assertEqual(item["year"], "2021")
        self.assertEqual(item["url"], "https://kitsu.io/anime/kitsu-show")

    def test_anilist_spoiler_tags_hidden_and_score_scaled(self):
        raw = media(1, "X", ["Fantasy"], [("Magic", 80)], 2022, score=83)
        raw["tags"].append({"name": "Secret Twist", "rank": 99, "isMediaSpoiler": True})
        item = AnimeTool()._normalize_anilist(raw)
        self.assertEqual(item["score"], 8.3)
        self.assertNotIn("Secret Twist", item["tag_ranks"])
        self.assertEqual(item["themes"], ["Magic"])


if __name__ == "__main__":
    unittest.main()
