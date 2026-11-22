"""Вход по ключу запуска: без него не открываются ни страница, ни API, ни токен сессии."""
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import app as appmod
from app import MAX_SESSIONS, create_app
from chat_model import ChatBot
from memory import Memory
from tests.test_chatbot import FakeModel
from tools.calculator_formulas import CalculatorFormulas
from tools.calculator_history import CalculatorHistory
from tools.calculator_scenarios import CalculatorScenarios
from tools.calculator_sharing import CalculatorSharing

PORT = 5000
KEY = "launch-key-0123456789abcdefghij"
HOST = {"Host": f"127.0.0.1:{PORT}"}
COOKIE = f"myai_session_{PORT}"
COOKIE_DOMAIN = "127.0.0.1"      # под этим доменом тестовый клиент хранит cookie при Host: 127.0.0.1
GET_PATHS = ("/history", "/status", "/memory", "/calculator/history", "/calculator/export.csv",
             "/calculator/scenarios/export.json", "/calculator/scenarios/community", "/static/app.js")


class AccessKeyBase(unittest.TestCase):
    def make_app(self, port=PORT, access_key=KEY):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bot = ChatBot(model=FakeModel("Привет из теста"), history_file=os.path.join(tmp.name, "h.json"),
                      memory=Memory(os.path.join(tmp.name, "m.db")),
                      calculator_history=CalculatorHistory(os.path.join(tmp.name, "calc.db")),
                      calculator_formulas=CalculatorFormulas(os.path.join(tmp.name, "formulas.db")),
                      calculator_scenarios=CalculatorScenarios(os.path.join(tmp.name, "scenarios.db")),
                      tools={"anime": None, "web": None, "weather": None, "computer": None})
        return create_app(bot, port=port, preload=False, access_key=access_key,
                          sharing=CalculatorSharing(os.path.join(tmp.name, "sharing.db")))

    def setUp(self):
        self.app = self.make_app()
        self.token = self.app.config["TOKEN"]
        self.anon = self.app.test_client()          # посторонний процесс: ни cookie, ни ключа

    def login(self):
        client = self.app.test_client()
        response = client.get(f"/?k={KEY}", headers=HOST)
        self.assertEqual(response.status_code, 302)
        return client

    def post(self, client, path, headers=None, body=None):
        merged = {**HOST, **(headers or {})}
        return client.post(path, data=json.dumps(body or {}), content_type="application/json", headers=merged)


class EntryTests(AccessKeyBase):
    def test_home_without_key_is_denied_and_hides_token(self):
        r = self.anon.get("/", headers=HOST)
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 403)
        self.assertIn("Нужен ключ запуска", html)
        self.assertNotIn(self.token, html)
        self.assertIn("no-store", r.headers.get("Cache-Control", ""))

    def test_wrong_or_empty_key_is_denied_without_cookie(self):
        for query in ("/?k=", "/?k=wrong", f"/?k={KEY[:-1]}", f"/?k={KEY}x"):
            with self.subTest(query=query):
                r = self.anon.get(query, headers=HOST)
                self.assertEqual(r.status_code, 403)
                self.assertNotIn("Set-Cookie", r.headers)
                self.assertNotIn(self.token, r.get_data(as_text=True))

    def test_right_key_sets_cookie_and_hides_key_from_url(self):
        r = self.anon.get(f"/?k={KEY}", headers=HOST)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/")
        self.assertNotIn(KEY, r.headers["Location"])
        cookie = r.headers["Set-Cookie"]
        self.assertIn(f"myai_session_{PORT}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn(KEY, cookie)               # в cookie не сам ключ, а отдельный идентификатор сессии
        page = self.anon.get("/", headers=HOST)     # cookie осталась в клиенте
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.token, page.get_data(as_text=True))
        self.assertIn("no-store", page.headers.get("Cache-Control", ""))

    def test_stale_key_with_valid_session_only_cleans_the_address(self):
        client = self.login()
        r = client.get("/?k=old-key", headers=HOST)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/")
        self.assertEqual(client.get("/", headers=HOST).status_code, 200)

    def test_cookie_name_includes_port(self):
        other = self.make_app(port=5001)
        r = other.test_client().get(f"/?k={KEY}", headers={"Host": "127.0.0.1:5001"})
        self.assertIn("myai_session_5001=", r.headers["Set-Cookie"])

    def test_short_explicit_key_is_replaced_by_random_one(self):
        weak = self.make_app(access_key="short")
        self.assertNotEqual(weak.config["ACCESS_KEY"], "short")
        self.assertGreaterEqual(len(weak.config["ACCESS_KEY"]), 24)
        self.assertEqual(weak.test_client().get("/?k=short", headers=HOST).status_code, 403)
        self.assertNotEqual(self.make_app(access_key=None).config["ACCESS_KEY"],
                            self.make_app(access_key=None).config["ACCESS_KEY"])


class ApiTests(AccessKeyBase):
    def test_get_api_and_static_need_a_session(self):
        for path in GET_PATHS:
            with self.subTest(path=path, who="anon"):
                r = self.anon.get(path, headers=HOST)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.get_json(), {"error": "unauthorized"})
        client = self.login()
        for path in GET_PATHS:
            with self.subTest(path=path, who="session"):
                r = client.get(path, headers=HOST)
                self.assertEqual(r.status_code, 200)
                r.close()

    def test_token_alone_is_not_enough(self):
        # Главное: даже зная токен сессии, без cookie ничего изменить нельзя.
        r = self.post(self.anon, "/memory/add", {"X-MyAI-Token": self.token}, {"text": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.app.config["BOT"].memory.stats()["notes"], 0)
        client = self.login()
        self.assertEqual(self.post(client, "/memory/add", {"X-MyAI-Token": self.token}, {"text": "x"}).status_code, 200)
        self.assertEqual(self.post(client, "/memory/add", None, {"text": "y"}).status_code, 403)   # cookie без токена

    def test_chat_needs_a_session(self):
        r = self.post(self.anon, "/chat", {"X-MyAI-Token": self.token}, {"text": "открой блокнот"})
        self.assertEqual(r.status_code, 401)

    def test_health_stays_open_and_shows_no_secrets(self):
        r = self.anon.get("/health", headers=HOST)
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertNotIn(self.token, body)
        self.assertNotIn(KEY, body)

    def test_foreign_host_is_still_blocked_even_with_session(self):
        client = self.login()
        for who in (self.anon, client):
            r = who.get("/", headers={"Host": "evil.example:5000"})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.get_json(), {"error": "forbidden host"})

    def test_forged_cookie_is_rejected(self):
        self.anon.set_cookie(COOKIE, "x" * 32, domain=COOKIE_DOMAIN)
        self.assertEqual(self.anon.get("/status", headers=HOST).status_code, 401)
        self.assertEqual(self.anon.get("/", headers=HOST).status_code, 403)

    def test_cookie_of_another_run_is_rejected(self):
        sid = self.login().get_cookie(COOKIE, domain=COOKIE_DOMAIN).value
        same_run = self.app.test_client()
        same_run.set_cookie(COOKIE, sid, domain=COOKIE_DOMAIN)
        self.assertEqual(same_run.get("/status", headers=HOST).status_code, 200)     # контроль: cookie действительно уходит
        restarted = self.make_app().test_client()                                    # «перезапуск»: список сессий пуст
        restarted.set_cookie(COOKIE, sid, domain=COOKIE_DOMAIN)
        self.assertEqual(restarted.get("/status", headers=HOST).status_code, 401)

    def test_options_preflight_is_not_open(self):
        r = self.anon.open("/chat", method="OPTIONS", headers=HOST)
        self.assertEqual(r.status_code, 401)

    def test_session_count_is_capped_and_oldest_is_dropped(self):
        first = self.login()
        for _ in range(MAX_SESSIONS):
            self.login()
        self.assertEqual(first.get("/status", headers=HOST).status_code, 401)
        self.assertEqual(self.login().get("/status", headers=HOST).status_code, 200)


class RobustnessTests(AccessKeyBase):
    def test_non_ascii_key_and_token_do_not_crash(self):
        self.assertEqual(self.anon.get("/", query_string={"k": "ключ-é"}, headers=HOST).status_code, 403)
        client = self.login()
        r = self.post(client, "/new", {"X-MyAI-Token": "é"})
        self.assertEqual(r.status_code, 403)       # раньше compare_digest падал с TypeError и отдавал 500
        self.assertEqual(r.get_json(), {"error": "bad token"})

    def test_frontend_reloads_on_401(self):
        path = os.path.join(os.path.dirname(appmod.__file__), "static", "app.js")
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("r.status===401", js)
        self.assertIn("location.reload()", js)


class MainTests(unittest.TestCase):
    def run_main(self, env, make_server=None, opened=True):
        server = mock.Mock()
        fake_app = mock.Mock()
        fake_app.config = {"ACCESS_KEY": "k" * 24}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(appmod, "_setup_logging"), \
             mock.patch.object(appmod, "create_app", return_value=fake_app), \
             mock.patch.object(appmod, "make_server", make_server or mock.Mock(return_value=server)) as ms, \
             mock.patch.object(appmod.webbrowser, "open", return_value=opened) as browser, \
             mock.patch("builtins.print") as printed:
            code = appmod.main()
        return code, ms, browser, printed, server

    def test_start_bat_mode_opens_browser_and_never_prints_the_key(self):
        code, ms, browser, printed, server = self.run_main({"MYAI_OPEN_BROWSER": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(ms.call_args[0][:2], ("127.0.0.1", appmod.PORT))
        url = browser.call_args[0][0]
        self.assertEqual(url, f"http://127.0.0.1:{appmod.PORT}/?k={'k' * 24}")
        printed.assert_not_called()                 # stdout у start.bat уходит в myai_console.log
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()

    def test_manual_mode_prints_url_and_does_not_open_browser(self):
        code, _, browser, printed, _ = self.run_main({"MYAI_OPEN_BROWSER": "0"})
        self.assertEqual(code, 0)
        browser.assert_not_called()
        self.assertIn("k" * 24, " ".join(str(a) for a in printed.call_args[0]))

    def test_browser_failure_falls_back_to_printing_the_url(self):
        _, _, browser, printed, _ = self.run_main({"MYAI_OPEN_BROWSER": "1"}, opened=False)
        browser.assert_called_once()
        self.assertIn("k" * 24, " ".join(str(a) for a in printed.call_args[0]))

    def test_busy_port_exits_without_opening_a_browser(self):
        # werkzeug при занятом порте сам печатает сообщение и вызывает sys.exit(1), а не бросает OSError
        for error in (OSError("busy"), SystemExit(1)):
            with self.subTest(error=repr(error)):
                code, _, browser, _, _ = self.run_main({"MYAI_OPEN_BROWSER": "1"}, make_server=mock.Mock(side_effect=error))
                self.assertEqual(code, 1)
                browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
