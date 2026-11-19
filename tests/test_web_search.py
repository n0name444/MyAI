import base64
import unittest
from unittest import mock

from tools.web_search import WebSearch

RSS = """<?xml version="1.0" encoding="utf-8"?><rss><channel>
<item><title>Python 3.13 &amp; новости</title><link>https://www.python.org/downloads/</link><description>Скачать &lt;b&gt;Python&lt;/b&gt;</description></item>
<item><title>Мусор</title><link>https://forum18.example/x</link><description>x</description></item>
<item><title></title><link>https://a.example</link></item>
</channel></rss>"""


class WebSearchTests(unittest.TestCase):
    def test_rss_without_lxml(self):
        r = WebSearch._filter(WebSearch.parse_rss(RSS))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["title"], "Python 3.13 & новости")
        self.assertEqual(r[0]["url"], "https://www.python.org/downloads/")

    def test_bad_rss(self):
        self.assertEqual(WebSearch.parse_rss("<rss><oops"), [])

    def test_unwrap_bing(self):
        target = "https://example.com/page?a=1"
        u = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        self.assertEqual(WebSearch.unwrap_bing(f"https://www.bing.com/ck/a?!&&p=x&u={u}&ntb=1"), target)
        self.assertIsNone(WebSearch.unwrap_bing("https://www.bing.com/ck/a?u=zzz"))
        self.assertEqual(WebSearch.unwrap_bing("https://example.com/"), "https://example.com/")

    def test_unwrap_ddg(self):
        self.assertEqual(WebSearch.unwrap_ddg("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=1"), "https://example.com/a")

    def test_public_url_guard(self):
        for bad in ("http://127.0.0.1:5000/", "http://localhost/", "http://192.168.1.1/", "http://10.0.0.5/", "file:///etc/passwd", "ftp://example.com"):
            self.assertFalse(WebSearch.is_public_url(bad), bad)
        self.assertTrue(WebSearch.is_public_url("http://8.8.8.8/"))

    def test_extract_text(self):
        html = b"<html><body><nav>menu</nav><article><p>" + ("Полезный текст страницы. " * 5).encode() + b"</p><p>short</p></article><script>x()</script></body></html>"
        text = WebSearch.extract_text(html, 500)
        self.assertIn("Полезный текст", text)
        self.assertNotIn("menu", text)
        self.assertNotIn("short", text)

    def test_fetch_blocks_redirect_to_private(self):
        ws = WebSearch()
        resp = mock.Mock(status_code=302, headers={"Location": "http://127.0.0.1:5000/"})
        with mock.patch("tools.web_search.requests.get", return_value=resp) as get, \
             mock.patch.object(WebSearch, "is_public_url", side_effect=lambda u: "127.0.0.1" not in u):
            self.assertEqual(ws.fetch_text("http://example.com/"), "")
            self.assertEqual(get.call_count, 1)  # на приватный адрес запрос не ушёл


if __name__ == "__main__":
    unittest.main()
