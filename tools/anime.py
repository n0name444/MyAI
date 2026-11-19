"""Поиск аниме: структурный (AniList с фильтрами) + запасная цепочка по названию (Jikan -> AniList -> Kitsu).

Поток для запроса «фэнтези, магия, 2020–2026»:
  AnimeQuery (tools/anime_query.py) -> запрос к AniList с фильтрами -> локальная проверка критериев ->
  ранжирование -> схлопывание сезонов -> результат с причинами совпадения.
Если критерии слишком строгие, часть требований ослабляется — и это прямо сообщается в notes.
Если структурный поиск ничего не дал (или запрос — это просто название), работает прежняя цепочка по названию.
Локального списка тайтлов нет: данные всегда берутся из онлайн-каталогов.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests

try:
    from tools import anime_query as aq
except ImportError:  # запуск как скрипта из папки tools
    import anime_query as aq  # type: ignore

log = logging.getLogger(__name__)

MIN_POPULARITY = 3000     # AniList: число пользователей, добавивших тайтл (отсекает совсем неизвестное)
MIN_TAG_RANK = 50         # AniList: «насколько тег подходит тайтлу», %
MAX_PAGES = 3
PER_PAGE = 50
DEFAULT_CACHE_TTL = 3 * 3600   # данные каталогов меняются медленно; 0 — кэш выключен (MYAI_ANIME_CACHE_TTL)
DEFAULT_CACHE_SIZE = 256       # записей в памяти; самые старые вытесняются
JIKAN_MIN_GAP = 0.4            # лимит Jikan: 3 запроса в секунду
_MISS = object()
_ANILIST_FIELDS = (
    "id idMal title { romaji english native } description(asHtml: false) genres tags { name rank isMediaSpoiler } "
    "averageScore popularity episodes status season seasonYear startDate { year } format siteUrl coverImage { large }"
)


@dataclass
class AnimeResult:
    items: List[dict]
    query: "aq.AnimeQuery"
    mode: str = "structured"                 # structured | title | legacy | empty
    notes: List[str] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    offline: bool = False                    # каталоги не ответили вообще


class AnimeTool:
    JIKAN_URL = "https://api.jikan.moe/v4"
    ANILIST_URL = "https://graphql.anilist.co"
    KITSU_URL = "https://kitsu.io/api/edge"

    REQUEST_WORDS = aq.REQUEST_WORDS
    TOPIC_ALIASES = aq.TOPIC_ALIASES

    def __init__(self, web_search=None, timeout=15, cache_ttl: Optional[float] = None, cache_size: int = DEFAULT_CACHE_SIZE):
        self.web_search = web_search
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MyAI-AnimeTool/3.0"})
        if cache_ttl is None:
            try:
                cache_ttl = float(os.getenv("MYAI_ANIME_CACHE_TTL", DEFAULT_CACHE_TTL))
            except ValueError:
                cache_ttl = DEFAULT_CACHE_TTL
        self.cache_ttl, self.cache_size = max(0.0, float(cache_ttl)), max(1, int(cache_size))
        self._cache: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._cache_lock = threading.Lock()
        self._hits = self._misses = 0
        self._clock = time.monotonic        # подменяются в тестах
        self._sleep = time.sleep
        self._jikan_lock = threading.Lock()
        self._last_jikan = 0.0
        self._tags: Optional[Dict[str, str]] = None       # lower -> каноническое имя
        self._tags_failed_at: Optional[float] = None
        self._failures = 0

    # ------------------------------------------------------------------ кэш ответов каталогов
    def _cache_get(self, key):
        if not self.cache_ttl:
            return _MISS
        with self._cache_lock:
            item = self._cache.get(key)
            if item is not None and item[0] > self._clock():
                self._cache.move_to_end(key)
                self._hits += 1
                return copy.deepcopy(item[1])
            if item is not None:
                del self._cache[key]
            self._misses += 1
        return _MISS

    def _cache_put(self, key, value) -> None:
        # неудачи и ответы с ошибками GraphQL не кэшируем: следующий запрос должен попробовать заново
        if not self.cache_ttl or value is None or (isinstance(value, dict) and value.get("errors")):
            return
        with self._cache_lock:
            self._cache[key] = (self._clock() + self.cache_ttl, copy.deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def cache_clear(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._hits = self._misses = 0

    def cache_stats(self) -> Dict[str, int]:
        with self._cache_lock:
            return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    # ------------------------------------------------------------------ HTTP
    def _get(self, url, params=None, headers=None, retries=1):
        key = ("GET", url, tuple(sorted((k, str(v)) for k, v in (params or {}).items())),
               tuple(sorted((headers or {}).items())))
        hit = self._cache_get(key)
        if hit is not _MISS:
            return hit
        data = self._http_get(url, params, headers, retries)
        self._cache_put(key, data)
        return data

    def _post(self, url, payload, retries=1):
        key = ("POST", url, json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
        hit = self._cache_get(key)
        if hit is not _MISS:
            return hit
        data = self._http_post(url, payload, retries)
        self._cache_put(key, data)
        return data

    def _throttle_jikan(self) -> None:
        """Jikan разрешает ~3 запроса в секунду: между настоящими обращениями выдерживаем паузу (кэш её не тратит)."""
        with self._jikan_lock:
            wait = JIKAN_MIN_GAP - (self._clock() - self._last_jikan)
            if wait > 0:
                self._sleep(wait)
            self._last_jikan = self._clock()

    def _http_get(self, url, params=None, headers=None, retries=1):
        if "jikan" in url:
            self._throttle_jikan()
        last_error = None
        for attempt in range(retries + 1):
            try:
                r = self.session.get(url, params=params or {}, headers=headers, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                last_error = f"HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
        self._failures += 1
        log.info("[ANIME] request failed: %s", last_error)
        return None

    def _http_post(self, url, payload, retries=1):
        last_error = None
        for attempt in range(retries + 1):
            try:
                r = self.session.post(url, json=payload, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                last_error = f"HTTP {r.status_code}"
                break
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
        self._failures += 1
        log.info("[ANIME] POST failed: %s", last_error)
        return None

    # ------------------------------------------------------------------ нормализация
    @staticmethod
    def _names(items):
        return [x.get("name") for x in (items or []) if isinstance(x, dict) and x.get("name")]

    @staticmethod
    def _num(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _blank(**kw) -> dict:
        base = {"id": "", "source": "", "title": "Без названия", "title_english": None, "title_japanese": None,
                "synopsis": "", "url": "", "image_url": None, "score": None, "rank": None, "popularity": None,
                "genres": [], "themes": [], "tag_ranks": {}, "demographics": [], "studios": [], "year": None,
                "type": None, "episodes": None, "status": None, "duration": None, "season": None,
                "broadcast": None, "source_material": None, "rating": None}
        base.update(kw)
        return base

    def _normalize_jikan(self, item):
        aired = item.get("aired") or {}
        aired_from = aired.get("from") or ""
        year = str(aired_from)[:4] if aired_from else item.get("year")
        genres = self._names(item.get("genres"))
        themes = self._names(item.get("themes"))
        imgs = ((item.get("images") or {}).get("jpg") or {})
        return self._blank(
            id=f"mal:{item.get('mal_id')}", source="MyAnimeList / Jikan",
            title=item.get("title") or item.get("title_english") or "Без названия",
            title_english=item.get("title_english"), title_japanese=item.get("title_japanese"),
            synopsis=item.get("synopsis") or "", url=item.get("url") or "",
            image_url=imgs.get("large_image_url") or imgs.get("image_url"),
            score=item.get("score"), rank=item.get("rank"), popularity=item.get("popularity"),
            genres=genres, themes=themes, tag_ranks={t: 100 for t in themes},
            demographics=self._names(item.get("demographics")), studios=self._names(item.get("studios")),
            year=year, type=item.get("type"), episodes=item.get("episodes"), status=item.get("status"),
            duration=item.get("duration"), season=item.get("season"),
            broadcast=(item.get("broadcast") or {}).get("string"),
            source_material=item.get("source"), rating=item.get("rating"))

    def _normalize_anilist(self, x):
        title = x.get("title") or {}
        tags = [t for t in (x.get("tags") or []) if isinstance(t, dict) and t.get("name") and not t.get("isMediaSpoiler")]
        tags.sort(key=lambda t: -(t.get("rank") or 0))
        avg = self._num(x.get("averageScore"))
        return self._blank(
            id=f"anilist:{x.get('id')}", source="AniList",
            title=title.get("english") or title.get("romaji") or title.get("native") or "Без названия",
            title_english=title.get("english"), title_japanese=title.get("native"),
            synopsis=re.sub(r"<[^>]+>", "", x.get("description") or "").strip(), url=x.get("siteUrl") or "",
            image_url=(x.get("coverImage") or {}).get("large"),
            score=round(avg / 10, 1) if avg else None, popularity=x.get("popularity"),
            genres=[aq.canon_genre(g) for g in (x.get("genres") or [])],
            themes=[t["name"] for t in tags[:8]], tag_ranks={t["name"]: int(t.get("rank") or 0) for t in tags},
            year=x.get("seasonYear") or (x.get("startDate") or {}).get("year"), type=x.get("format"),
            episodes=x.get("episodes"), status=x.get("status"), season=x.get("season"))

    # ------------------------------------------------------------------ каталоги: поиск по названию
    def _jikan_search(self, query, limit=10):
        data = self._get(f"{self.JIKAN_URL}/anime", {"q": query, "limit": min(limit, 25), "sfw": "true"})
        if not data or not isinstance(data.get("data"), list):
            return []
        return [self._normalize_jikan(raw) for raw in data["data"] if raw.get("mal_id")]

    def _jikan_full(self, item):
        mal_id = item.get("id", "").split(":", 1)[-1]
        if not mal_id.isdigit():
            return item
        data = self._get(f"{self.JIKAN_URL}/anime/{mal_id}/full")
        return self._normalize_jikan(data["data"]) if data and isinstance(data.get("data"), dict) else item

    def _anilist_search(self, query, limit=10):
        gql = ("query ($search: String, $perPage: Int) { Page(perPage: $perPage) { media(search: $search, type: ANIME, "
               "isAdult: false) { " + _ANILIST_FIELDS + " }}}")
        data = self._post(self.ANILIST_URL, {"query": gql, "variables": {"search": query, "perPage": min(limit, 25)}})
        media = (((data or {}).get("data") or {}).get("Page") or {}).get("media") or []
        return [self._normalize_anilist(x) for x in media if isinstance(x, dict)]

    def _kitsu_search(self, query, limit=10):
        data = self._get(f"{self.KITSU_URL}/anime",
                         {"filter[text]": query, "page[limit]": min(limit, 20), "include": "genres"})
        rows = (data or {}).get("data") or []
        genre_names = {i.get("id"): (i.get("attributes") or {}).get("name")
                       for i in ((data or {}).get("included") or []) if i.get("type") == "genres"}
        out = []
        for x in rows:
            a = x.get("attributes") or {}
            titles = a.get("titles") or {}
            title = titles.get("en") or titles.get("en_jp") or titles.get("ja_jp") or a.get("canonicalTitle") or "Без названия"
            gdata = (((x.get("relationships") or {}).get("genres") or {}).get("data")) or []
            genres = [aq.canon_genre(genre_names[g["id"]]) for g in gdata if genre_names.get(g.get("id"))]
            rating = self._num(a.get("averageRating"))          # у Kitsu шкала 0–100, у остальных 0–10
            out.append(self._blank(
                id=f"kitsu:{x.get('id')}", source="Kitsu", title=title, title_english=titles.get("en"),
                title_japanese=titles.get("ja_jp"), synopsis=a.get("synopsis") or "", url=a.get("canonicalUrl")
                or (f"https://kitsu.io/anime/{a.get('slug')}" if a.get("slug") else ""),
                image_url=(a.get("posterImage") or {}).get("large"), score=round(rating / 10, 1) if rating else None,
                popularity=a.get("popularityRank"), genres=genres,
                year=(a.get("startDate") or "")[:4] or None, type=a.get("subtype"), episodes=a.get("episodeCount"),
                status=a.get("status"), duration=a.get("episodeLength")))
        return out

    # ------------------------------------------------------------------ теги AniList
    def known_tags(self) -> Optional[Dict[str, str]]:
        """Список тегов AniList {нижний регистр: имя}. None, если получить не удалось (повтор не чаще раза в 5 минут)."""
        if self._tags is not None:
            return self._tags
        if self._tags_failed_at and time.monotonic() - self._tags_failed_at < 300:
            return None
        data = self._post(self.ANILIST_URL, {"query": "{ MediaTagCollection { name isAdult } }"}, retries=0)
        rows = (((data or {}).get("data") or {}).get("MediaTagCollection")) or []
        names = {r["name"].lower(): r["name"] for r in rows
                 if isinstance(r, dict) and r.get("name") and not r.get("isAdult")}
        if names:
            self._tags = names
            return names
        self._tags_failed_at = time.monotonic()
        return None

    # ------------------------------------------------------------------ AniList: поиск с фильтрами
    def _anilist_discover(self, q, profile: str, page: int, api_tags: Sequence[str]):
        """Возвращает (список | None при ошибке, есть_ли_следующая_страница)."""
        decl = ["$page: Int", "$perPage: Int", "$sort: [MediaSort]"]
        args = ["type: ANIME", "isAdult: false", "sort: $sort"]
        var: dict = {"page": page, "perPage": PER_PAGE, "sort": [q.sort or "POPULARITY_DESC"]}

        def add(name, gtype, value):
            decl.append(f"${name}: {gtype}")
            args.append(f"{name}: ${name}")
            var[name] = value

        if q.genres:
            add("genre_in", "[String]", list(q.genres[:4]))
        if q.formats:
            add("format_in", "[MediaFormat]", list(q.formats))
        if profile == "full":
            if api_tags:
                add("tag_in", "[String]", list(api_tags[:4]))
            if q.exclude_genres:
                add("genre_not_in", "[String]", list(q.exclude_genres))
            if q.year_from:
                add("startDate_greater", "FuzzyDateInt", (q.year_from - 1) * 10000 + 1231)
            if q.year_to:
                add("startDate_lesser", "FuzzyDateInt", (q.year_to + 1) * 10000 + 101)
        gql = (f"query ({', '.join(decl)}) {{ Page(page: $page, perPage: $perPage) {{ pageInfo {{ hasNextPage }} "
               f"media({', '.join(args)}) {{ {_ANILIST_FIELDS} }} }} }}")
        data = self._post(self.ANILIST_URL, {"query": gql, "variables": var})
        page_obj = (((data or {}).get("data") or {}).get("Page"))
        if not isinstance(page_obj, dict):
            return None, False
        media = [self._normalize_anilist(x) for x in (page_obj.get("media") or []) if isinstance(x, dict)]
        return media, bool((page_obj.get("pageInfo") or {}).get("hasNextPage"))

    # ------------------------------------------------------------------ проверка и ранжирование
    def _evaluate(self, item, q, skip: Set[str], ref_key: Optional[str]) -> Tuple[Optional[int], List[str]]:
        """(уровень, причины). Уровень 0 — все критерии; 1 — теги необязательны; 2 — совпала часть жанров; None — не подходит."""
        if item["id"] in skip:
            return None, []
        if ref_key and aq.franchise_key(item["title"]) == ref_key:
            return None, []
        status = str(item.get("status") or "").upper().replace(" ", "_")
        if status in {"NOT_YET_RELEASED", "NOT_YET_AIRED"}:
            return None, []
        fmt = str(item.get("type") or "").upper().replace(" ", "_")
        if q.formats:
            if fmt not in q.formats:
                return None, []
        elif fmt == "MUSIC":
            return None, []
        pop = item.get("popularity")
        if item["source"] == "AniList" and isinstance(pop, int) and pop < MIN_POPULARITY:
            return None, []

        reasons: List[str] = []
        if q.year_from or q.year_to:
            try:
                year = int(str(item.get("year"))[:4])
            except (TypeError, ValueError):
                return None, []
            if (q.year_from and year < q.year_from) or (q.year_to and year > q.year_to):
                return None, []
            reasons.append(f"год: {year}")

        genres = {aq.canon_genre(g) for g in item.get("genres", [])}
        ranks = {k.lower(): v for k, v in (item.get("tag_ranks") or {}).items()}
        if any(g in genres for g in q.exclude_genres):
            return None, []
        if any(ranks.get(t.lower(), 0) >= 40 for t in q.exclude_tags):
            return None, []

        matched_g = [g for g in q.genres if g in genres]
        matched_t = [t for t in q.tags if ranks.get(t.lower(), 0) >= MIN_TAG_RANK]
        all_g, all_t = len(matched_g) == len(q.genres), len(matched_t) == len(q.tags)
        other_hard = bool(q.genres or q.year_from or q.year_to or q.formats)
        if all_g and all_t:
            level = 0
        elif all_g and q.tags and other_hard:
            level = 1
        elif matched_g and (q.tags or len(q.genres) > 1):
            level = 2
        else:
            return None, []

        if matched_g:
            reasons.insert(0, "жанры: " + ", ".join(aq.ru_label(g) for g in matched_g))
        if matched_t:
            reasons.insert(1 if matched_g else 0, "теги: " + ", ".join(
                f"{aq.ru_label(t)} ({ranks[t.lower()]}%)" for t in matched_t))
        for trait in q.traits:
            if aq.trait_hits(item.get("synopsis", ""), trait) >= 2:
                reasons.append("по описанию: " + aq.TRAIT_RU.get(trait, trait))
        return level, reasons

    @staticmethod
    def _rank(item, q, level: int) -> float:
        pop = item.get("popularity")
        score = math.log10(pop + 1) / 6.0 if item["source"] == "AniList" and isinstance(pop, int) else 0.0
        score += (item.get("score") or 0) / 10 * (1.6 if q.sort == "SCORE_DESC" else 0.8)
        genres = {aq.canon_genre(g) for g in item.get("genres", [])}
        if q.genres:
            score += 0.5 * sum(1 for g in q.genres if g in genres) / len(q.genres)
        ranks = {k.lower(): v for k, v in (item.get("tag_ranks") or {}).items()}
        wanted = list(q.tags) + list(q.soft_tags)
        if wanted:
            score += 0.4 * sum(ranks.get(t.lower(), 0) / 100 for t in wanted) / len(wanted)
        blob = f"{item.get('title', '')} {item.get('synopsis', '')}"
        for trait in q.traits:
            score += 0.2 * min(aq.trait_hits(blob, trait), 3)
        low = blob.lower()
        score += 0.1 * min(sum(1 for k in q.keywords if k.lower() in low), 3)
        return score - 0.6 * level

    def _pick(self, raw, q, limit, skip, ref_key) -> Tuple[List[dict], int]:
        scored = []
        for it in raw:
            level, reasons = self._evaluate(it, q, skip, ref_key)
            if level is not None:
                scored.append((it, level, reasons))
        strict_keys = {aq.franchise_key(x[0]["title"]) for x in scored if x[1] == 0}
        chosen = [x for x in scored if x[1] == 0] if len(strict_keys) >= min(limit, 4) else scored
        chosen.sort(key=lambda x: self._rank(x[0], q, x[1]), reverse=True)
        out, seen = [], set()
        for it, level, reasons in chosen:
            key = aq.franchise_key(it["title"])
            if key in seen:
                continue
            seen.add(key)
            card = dict(it)
            card["match_level"], card["reasons"] = level, reasons
            out.append(card)
            if len(out) >= limit:
                break
        return out, sum(1 for c in out if c["match_level"] == 0)

    # ------------------------------------------------------------------ структурный поиск
    def _structured(self, q, limit: int, skip: Set[str], ref_key: Optional[str]) -> Tuple[List[dict], List[str]]:
        notes: List[str] = []
        known = self.known_tags() if q.tags else None
        api_tags = [known[t.lower()] for t in q.tags if known and t.lower() in known]
        raw: List[dict] = []
        seen: Set[str] = set()
        picked: List[dict] = []
        any_ok = False
        for profile in ("full", "basic"):
            for page in range(1, MAX_PAGES + 1):
                got, has_next = self._anilist_discover(q, profile, page, api_tags)
                if got is None:
                    break
                any_ok = True
                for it in got:
                    if it["id"] not in seen:
                        seen.add(it["id"])
                        raw.append(it)
                picked, n_strict = self._pick(raw, q, limit, skip, ref_key)
                if n_strict >= limit or not has_next:
                    break
            picked, n_strict = self._pick(raw, q, limit, skip, ref_key)
            if n_strict >= min(limit, 4):
                break
        if not any_ok:
            return [], notes
        if any(c["match_level"] == 1 for c in picked):
            notes.append("Тайтлов, подходящих по всем критериям, мало, поэтому часть списка — без обязательных тегов.")
        if any(c["match_level"] == 2 for c in picked):
            notes.append("Часть тайтлов совпала только с некоторыми из заданных жанров.")
        if q.traits:
            notes.append("Черты героя («" + "», «".join(aq.TRAIT_RU.get(t, t) for t in q.traits)
                         + "») определяются по описаниям и только приблизительны.")
        return picked, notes

    # ------------------------------------------------------------------ «похоже на …»
    def _resolve_reference(self, title: str) -> Optional[dict]:
        cands = self._anilist_search(title, 3)
        best, best_ratio = None, 0.0
        for c in cands:
            names = [c.get("title"), c.get("title_english"), c.get("title_japanese")]
            ratio = max((SequenceMatcher(None, title.lower(), str(n).lower()).ratio() for n in names if n), default=0)
            if ratio > best_ratio:
                best, best_ratio = c, ratio
        return best if best and best_ratio >= 0.45 else None

    @staticmethod
    def _apply_reference(q, ref: dict):
        q2 = copy.deepcopy(q)
        ref_genres = [g for g in ref.get("genres", []) if g in aq.GENRES][:2]
        ref_tags = [t for t, r in sorted((ref.get("tag_ranks") or {}).items(), key=lambda kv: -kv[1]) if r >= 60][:3]
        if not q.genres and not q.tags:
            q2.genres, q2.tags = ref_genres, ref_tags
        else:
            q2.soft_tags = list(dict.fromkeys(q.soft_tags + ref_tags))
        return q2

    # ------------------------------------------------------------------ запасной путь: по названию
    @staticmethod
    def _relevance(item, query):
        blob = " ".join([item.get("title", ""), item.get("title_english") or "", item.get("synopsis", ""),
                         " ".join(item.get("genres", [])), " ".join(item.get("themes", []))]).lower()
        words = [w for w in re.findall(r"[a-zа-яё0-9]+", query.lower()) if len(w) > 2]
        score = sum(1.0 for w in words if w in blob)
        score += SequenceMatcher(None, query.lower(), item.get("title", "").lower()).ratio() * 0.8
        return score

    @staticmethod
    def _same_title(a, b):
        names = {str(a.get("title", "")).lower(), str(a.get("title_english") or "").lower(), str(a.get("title_japanese") or "").lower()}
        other = {str(b.get("title", "")).lower(), str(b.get("title_english") or "").lower(), str(b.get("title_japanese") or "").lower()}
        return bool((names - {""}) & (other - {""}))

    def _make_queries(self, user_text):
        return aq.legacy_queries(user_text)

    def _legacy_search(self, queries: Sequence[str], limit=8, skip: Set[str] = frozenset()) -> Tuple[List[dict], List[str]]:
        log.info("[ANIME] title-search queries=%r", list(queries))
        candidates: List[dict] = []
        used: List[str] = []
        providers = [("MyAnimeList / Jikan", self._jikan_search), ("AniList", self._anilist_search), ("Kitsu", self._kitsu_search)]
        for source, fn in providers:
            got: List[dict] = []
            for q in queries:
                try:
                    got.extend(fn(q, max(limit * 2, 10)))
                except Exception as exc:  # noqa: BLE001 — каталог не должен ронять поиск
                    log.info("[ANIME] %s: %s", source, exc)
            if got:
                used.append(source)
                candidates.extend(got)
            else:
                log.info("[ANIME] %s: no results, trying next source", source)
        if not candidates:
            return [], used

        merged: List[dict] = []
        for item in candidates:
            existing = next((x for x in merged if self._same_title(x, item)), None)
            if existing:
                existing["genres"] = list(dict.fromkeys(existing.get("genres", []) + item.get("genres", [])))
                existing["themes"] = list(dict.fromkeys(existing.get("themes", []) + item.get("themes", [])))
                if len(item.get("synopsis", "")) > len(existing.get("synopsis", "")):
                    existing["synopsis"] = item["synopsis"]
                existing["sources"] = list(dict.fromkeys(existing.get("sources", [existing.get("source")]) + [item.get("source")]))
                if not existing.get("url") and item.get("url"):
                    existing["url"] = item["url"]
            else:
                item["sources"] = [item.get("source")]
                merged.append(item)
        merged = [m for m in merged if m["id"] not in skip]
        for item in merged:
            item["_relevance"] = max((self._relevance(item, q) for q in queries), default=0)
        ranked = sorted(merged, key=lambda x: x["_relevance"], reverse=True)

        final, full_cards = [], 0
        for item in ranked[:limit]:
            if item.get("source") == "MyAnimeList / Jikan" and full_cards < 3:
                item = self._jikan_full(item)
                full_cards += 1
            item.pop("_relevance", None)
            item.setdefault("reasons", [])
            final.append(item)
        log.info("[ANIME] sources used: %s", used)
        return final, used

    # ------------------------------------------------------------------ публичный API
    @staticmethod
    def _count_notes(q, shown: int, limit: int) -> List[str]:
        """Если пользователь назвал число тайтлов — честно сообщаем, когда показано меньше или упёрлись в потолок."""
        notes: List[str] = []
        if q.requested > aq.MAX_LIMIT:
            notes.append(f"Запрошено {q.requested}, но за один раз показываю не больше {aq.MAX_LIMIT}.")
        target = min(q.requested, limit) if q.requested else 0
        if target and shown < target:
            notes.append(f"Нашлось {shown} из {target} запрошенных. "
                         "Чтобы получить больше, ослабь условия: например, убери год или часть тегов.")
        return notes

    def find(self, query, limit: Optional[int] = None, exclude_ids: Iterable[str] = ()) -> AnimeResult:
        q = copy.deepcopy(query)
        limit = max(1, min(int(limit or q.limit or 8), aq.MAX_LIMIT))
        skip: Set[str] = set(exclude_ids or ())
        notes: List[str] = []
        failures_before = self._failures

        ref_key = None
        work = q
        if q.reference:
            ref = self._resolve_reference(q.reference)
            if ref:
                ref_key = aq.franchise_key(ref["title"])
                skip.add(ref["id"])
                work = self._apply_reference(q, ref)
                notes.append(f"Ориентируюсь на «{ref['title']}».")
            else:
                notes.append(f"Не нашёл «{q.reference}» в каталоге, поэтому ищу только по остальным критериям.")

        if work.has_filters():
            items, extra = self._structured(work, limit, skip, ref_key)
            notes += extra
            if items:
                return AnimeResult(items, q, "structured", notes + self._count_notes(q, len(items), limit), ["AniList"])
            notes.append("По заданным критериям в AniList ничего подходящего не нашлось.")

        if q.title:
            queries = [q.title]
        elif q.keywords:
            queries = list(q.keywords)
        elif not work.has_filters() and not q.reference:
            queries = self._make_queries(q.raw)
        else:
            queries = []
        if queries:
            items, used = self._legacy_search(queries, limit, skip)
            if items:
                return AnimeResult(items, q, "title" if q.title else "legacy",
                                   notes + self._count_notes(q, len(items), limit), used)
        return AnimeResult([], q, "empty", notes, [], offline=self._failures > failures_before)

    def search(self, user_text, limit=8):
        """Совместимость: разбирает текст по правилам и возвращает список тайтлов."""
        q = aq.parse_query(user_text)
        return self.find(q, limit=limit).items

    def web_fallback(self, user_text, limit=5):
        return self.search(user_text, limit)
