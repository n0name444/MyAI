import copy
import os
import unittest
from unittest import mock

from tools.anime import DEFAULT_CACHE_TTL, JIKAN_MIN_GAP, AnimeTool
from tools import anime_query as aq
from tests.test_anime import FakeNet, media, page
from datetime import date

P = lambda text: aq.parse_query(text, date(2026, 11, 9))  # noqa: E731


class Counting(AnimeTool):
    """Сеть подменена на нижнем уровне: кэш и ограничитель частоты — настоящие. Время управляется тестом."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls, self.sleeps, self.now = [], [], 1000.0
        self._clock = lambda: self.now
        self._sleep = self._fake_sleep
        self.get_result = {"data": [1, 2]}
        self.post_result = {"data": {"x": 1}}

    def _fake_sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.now += s

    def _http_get(self, url, params=None, headers=None, retries=1):
        if "jikan" in url:
            self._throttle_jikan()
        self.calls.append(("GET", url, params))
        return copy.deepcopy(self.get_result)

    def _http_post(self, url, payload, retries=1):
        self.calls.append(("POST", url, payload))
        return copy.deepcopy(self.post_result)


class CacheTests(unittest.TestCase):
    def test_get_is_cached_regardless_of_param_order(self):
        t = Counting()
        t._get("https://x/api", {"a": 1, "b": 2})
        t._get("https://x/api", {"b": 2, "a": 1})
        self.assertEqual(len(t.calls), 1)
        t._get("https://x/api", {"a": 1, "b": 3})
        t._get("https://x/other", {"a": 1, "b": 2})
        self.assertEqual(len(t.calls), 3)

    def test_post_is_cached_regardless_of_key_order(self):
        t = Counting()
        t._post("https://x/g", {"query": "q", "variables": {"a": 1, "b": 2}})
        t._post("https://x/g", {"variables": {"b": 2, "a": 1}, "query": "q"})
        self.assertEqual(len(t.calls), 1)
        t._post("https://x/g", {"query": "q", "variables": {"a": 9}})
        self.assertEqual(len(t.calls), 2)

    def test_entries_expire_after_ttl(self):
        t = Counting(cache_ttl=100)
        t._get("https://x/a")
        t.now += 99
        t._get("https://x/a")
        self.assertEqual(len(t.calls), 1)
        t.now += 2
        t._get("https://x/a")
        self.assertEqual(len(t.calls), 2)

    def test_failures_and_graphql_errors_are_not_cached(self):
        t = Counting()
        t.get_result = None
        self.assertIsNone(t._get("https://x/a"))
        t.get_result = {"data": [1]}
        self.assertEqual(t._get("https://x/a"), {"data": [1]})          # после сбоя запрос повторяется
        t.post_result = {"errors": [{"message": "bad"}]}
        t._post("https://x/g", {"q": 1}); t._post("https://x/g", {"q": 1})
        self.assertEqual(len([c for c in t.calls if c[0] == "POST"]), 2)

    def test_lru_eviction(self):
        t = Counting(cache_size=2)
        for name in ("a", "b"):
            t._get(f"https://x/{name}")
        t._get("https://x/a")                                            # «a» использован недавно
        t._get("https://x/c")                                            # вытесняется «b»
        n = len(t.calls)
        t._get("https://x/a")
        self.assertEqual(len(t.calls), n)                                # «a» остался в кэше
        t._get("https://x/b")
        self.assertEqual(len(t.calls), n + 1)                            # «b» вытеснен
        self.assertLessEqual(t.cache_stats()["size"], 2)

    def test_mutating_results_cannot_corrupt_cache(self):
        t = Counting()
        first = t._get("https://x/a")
        first["data"].append("испорчено")
        second = t._get("https://x/a")
        self.assertEqual(second, {"data": [1, 2]})
        second["data"].clear()
        self.assertEqual(t._get("https://x/a"), {"data": [1, 2]})

    def test_zero_ttl_disables_cache(self):
        t = Counting(cache_ttl=0)
        t._get("https://x/a"); t._get("https://x/a")
        self.assertEqual(len(t.calls), 2)
        self.assertEqual(t.cache_stats()["size"], 0)

    def test_env_ttl(self):
        with mock.patch.dict(os.environ, {"MYAI_ANIME_CACHE_TTL": "60"}):
            self.assertEqual(AnimeTool().cache_ttl, 60)
        with mock.patch.dict(os.environ, {"MYAI_ANIME_CACHE_TTL": "0"}):
            self.assertEqual(AnimeTool().cache_ttl, 0)
        with mock.patch.dict(os.environ, {"MYAI_ANIME_CACHE_TTL": "не число"}):
            self.assertEqual(AnimeTool().cache_ttl, DEFAULT_CACHE_TTL)
        self.assertEqual(AnimeTool(cache_ttl=5).cache_ttl, 5)

    def test_stats_and_clear(self):
        t = Counting()
        t._get("https://x/a"); t._get("https://x/a"); t._get("https://x/b")
        self.assertEqual(t.cache_stats(), {"hits": 1, "misses": 2, "size": 2})
        t.cache_clear()
        self.assertEqual(t.cache_stats(), {"hits": 0, "misses": 0, "size": 0})


class ThrottleTests(unittest.TestCase):
    def test_jikan_calls_are_spaced_and_cache_hits_are_free(self):
        t = Counting()
        for i in range(3):
            t._get(f"https://api.jikan.moe/v4/anime/{i}")
        self.assertEqual(t.sleeps, [JIKAN_MIN_GAP, JIKAN_MIN_GAP])       # первый запрос — без ожидания
        t._get("https://api.jikan.moe/v4/anime/1")                       # из кэша: ни запроса, ни паузы
        self.assertEqual(len(t.sleeps), 2)
        self.assertEqual(len(t.calls), 3)

    def test_no_wait_when_enough_time_passed_and_other_hosts_unthrottled(self):
        t = Counting()
        t._get("https://api.jikan.moe/v4/anime/1")
        t.now += 5
        t._get("https://api.jikan.moe/v4/anime/2")
        for i in range(3):
            t._get(f"https://kitsu.io/api/edge/anime/{i}")
        self.assertEqual(t.sleeps, [])


class HttpNet(FakeNet):
    """FakeNet, но подмена сети на нижнем уровне — настоящие _get/_post с кэшем работают."""
    _get = AnimeTool._get
    _post = AnimeTool._post

    def _http_get(self, url, params=None, headers=None, retries=1):
        return FakeNet._get(self, url, params, headers, retries)

    def _http_post(self, url, payload, retries=1):
        return FakeNet._post(self, url, payload, retries)


class FindUsesCacheTests(unittest.TestCase):
    def test_refinement_does_not_hit_the_network_again(self):
        net = HttpNet()
        net.discover = {1: page([media(1, "Hit", ["Fantasy"], [("Magic", 70)], 2022, 90000)])}
        q = P("фэнтези магия 2020-2026")
        first = net.find(q)
        posts_after_first = len(net.posts)
        second = net.find(q)
        self.assertGreater(posts_after_first, 0)
        self.assertEqual(len(net.posts), posts_after_first)             # второй поиск целиком из кэша
        self.assertEqual([i["title"] for i in first.items], [i["title"] for i in second.items])
        self.assertGreater(net.cache_stats()["hits"], 0)

    def test_different_criteria_still_query_the_catalog(self):
        net = HttpNet()
        net.find(P("фэнтези 2020-2026"))
        n = len(net.posts)
        net.find(P("фэнтези без комедии 2020-2026"))
        self.assertGreater(len(net.posts), n)


if __name__ == "__main__":
    unittest.main()
