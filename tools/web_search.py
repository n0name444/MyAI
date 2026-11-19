"""Веб-поиск: Bing RSS → Bing HTML → DuckDuckGo HTML, плюс чтение текста страниц.

Сниппеты поисковиков слишком короткие для фактических вопросов, поэтому для лучших
результатов дополнительно скачивается основной текст страницы (с защитой от обращения
к локальной сети).
"""
from __future__ import annotations

import base64
import html
import ipaddress
import logging
import re
import socket
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Домены, результаты с которых заведомо мусорные. Дополняйте при необходимости.
BLOCKED_DOMAINS = ("forum18", "safemoonton", "tlauncher")
MAX_PAGE_BYTES = 600_000
MAX_REDIRECTS = 3


class WebSearch:
    def __init__(self, timeout: int = 12):
        self.timeout = timeout
        self.headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }

    # ------------------------- поиск -------------------------
    def search(self, query: str, limit: int = 8) -> List[Dict[str, str]]:
        query = str(query or "").strip()
        if not query:
            return []
        for name, fn in (("Bing RSS", self._bing_rss), ("Bing HTML", self._bing), ("DuckDuckGo", self._ddg)):
            results = self._filter(fn(query, limit * 2))[:limit]
            if results:
                log.info("[WEB] %s: найдено %d", name, len(results))
                return results
        log.warning("[WEB] ни один источник не вернул результатов")
        return []

    @staticmethod
    def _filter(results: List[Dict[str, str]]) -> List[Dict[str, str]]:
        out, seen = [], set()
        for r in results:
            url = r.get("url", "")
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
            if not r.get("title") or not url.startswith(("http://", "https://")):
                continue
            if any(bad in host for bad in BLOCKED_DOMAINS) or url in seen:
                continue
            seen.add(url)
            out.append(r)
        return out

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(text or "")).strip()

    @staticmethod
    def parse_rss(xml_text: str, limit: int = 20) -> List[Dict[str, str]]:
        """Разбор RSS стандартной библиотекой (lxml не нужен)."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            log.warning("[WEB] RSS не разобран: %s", exc)
            return []
        results = []
        for item in root.iter("item"):
            title = WebSearch._clean(item.findtext("title") or "")
            url = (item.findtext("link") or "").strip()
            snippet = WebSearch._clean(item.findtext("description") or "")
            if title and url:
                results.append({"title": title, "snippet": snippet, "url": url})
            if len(results) >= limit:
                break
        return results

    def _bing_rss(self, query, limit):
        try:
            r = requests.get("https://www.bing.com/search", params={"q": query, "format": "rss", "setlang": "ru"},
                             headers={**self.headers, "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
                             timeout=self.timeout)
            if r.status_code != 200:
                log.info("[WEB] Bing RSS HTTP %s", r.status_code)
                return []
            return self.parse_rss(r.text, limit)
        except requests.RequestException as exc:
            log.info("[WEB] Bing RSS: %s", exc)
            return []

    @staticmethod
    def unwrap_bing(url: str) -> Optional[str]:
        """Ссылки bing.com/ck/a?...&u=a1<base64url> → настоящий адрес."""
        if "bing.com/ck/a" not in url:
            return url
        try:
            u = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("u", [""])[0]
            if u.startswith("a1"):
                payload = u[2:]
                payload += "=" * (-len(payload) % 4)
                decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
                if decoded.startswith(("http://", "https://")):
                    return decoded
        except Exception:
            pass
        return None

    @staticmethod
    def unwrap_ddg(url: str) -> str:
        """//duckduckgo.com/l/?uddg=<encoded> → настоящий адрес."""
        if url.startswith("//"):
            url = "https:" + url
        parts = urllib.parse.urlsplit(url)
        if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
            target = urllib.parse.parse_qs(parts.query).get("uddg", [""])[0]
            if target:
                return target
        return url

    def _bing(self, query, limit):
        try:
            r = requests.get("https://www.bing.com/search", params={"q": query, "setlang": "ru"},
                             headers=self.headers, timeout=self.timeout)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for li in soup.select("li.b_algo"):
                a = li.select_one("h2 a")
                if not a:
                    continue
                url = self.unwrap_bing(a.get("href", ""))
                p = li.select_one(".b_caption p")
                if url:
                    results.append({"title": self._clean(a.get_text(" ")), "snippet": self._clean(p.get_text(" ")) if p else "", "url": url})
                if len(results) >= limit:
                    break
            return results
        except Exception as exc:
            log.info("[WEB] Bing HTML: %s", exc)
            return []

    def _ddg(self, query, limit):
        try:
            r = requests.get("https://html.duckduckgo.com/html/", params={"q": query, "kl": "ru-ru"},
                             headers={**self.headers, "Referer": "https://html.duckduckgo.com/"}, timeout=self.timeout)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for node in soup.select(".result"):
                a = node.select_one("a.result__a")
                if not a:
                    continue
                s = node.select_one(".result__snippet")
                results.append({"title": self._clean(a.get_text(" ")), "snippet": self._clean(s.get_text(" ")) if s else "",
                                "url": self.unwrap_ddg(a.get("href", ""))})
                if len(results) >= limit:
                    break
            return results
        except Exception as exc:
            log.info("[WEB] DDG: %s", exc)
            return []

    # ------------------- текст страниц -------------------
    @staticmethod
    def is_public_url(url: str) -> bool:
        """Только http(s) и только адреса из публичного интернета (не localhost/LAN)."""
        try:
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                return False
            infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
            return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
        except Exception:
            return False

    @staticmethod
    def extract_text(raw: bytes, max_chars: int = 1800) -> str:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "iframe"]):
            tag.decompose()
        root = soup.find("article") or soup.find("main") or soup.body or soup
        chunks = []
        for el in root.find_all(["p", "li", "h1", "h2", "h3"]):
            text = re.sub(r"\s+", " ", el.get_text(" ")).strip()
            if len(text) >= 50:
                chunks.append(text)
        return " ".join(chunks)[:max_chars].strip()

    def fetch_text(self, url: str, max_chars: int = 1800) -> str:
        try:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                if not self.is_public_url(current):
                    return ""
                r = requests.get(current, headers=self.headers, timeout=self.timeout, stream=True, allow_redirects=False)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    current = urllib.parse.urljoin(current, r.headers["Location"])
                    r.close()
                    continue
                if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "").lower():
                    r.close()
                    return ""
                raw = b""
                for chunk in r.iter_content(65536):
                    raw += chunk
                    if len(raw) > MAX_PAGE_BYTES:
                        break
                r.close()
                return self.extract_text(raw, max_chars)
        except Exception as exc:
            log.info("[WEB] fetch %s: %s", url, exc)
        return ""

    def enrich(self, results: List[Dict[str, str]], top: int = 2, max_chars: int = 1500, deadline: float = 12.0) -> None:
        """Добавляет поле «text» первым `top` результатам (параллельно, с общим лимитом по времени)."""
        targets = results[:top]
        if not targets:
            return
        pool = ThreadPoolExecutor(max_workers=len(targets))
        try:
            futures = {pool.submit(self.fetch_text, r["url"], max_chars): r for r in targets}
            for fut in as_completed(futures, timeout=deadline):
                futures[fut]["text"] = fut.result() or ""
        except Exception as exc:  # TimeoutError и т.п.: берём то, что успело загрузиться
            log.info("[WEB] enrich: %s", exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


# Совместимость со старым именем.
WebSearchTool = WebSearch
