import json
import os
import tempfile
import unittest

from app import create_app
from chat_model import ChatBot
from tools.calculator_history import CalculatorHistory
from tools.calculator_formulas import CalculatorFormulas
from tools.calculator_scenarios import CalculatorScenarios
from tools.calculator_sharing import CalculatorSharing
from memory import Memory
from tests.test_chatbot import FakeModel

PORT = 5000


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        bot = ChatBot(model=FakeModel("Привет из теста"), history_file=os.path.join(self.tmp.name, "h.json"),
                      memory=Memory(os.path.join(self.tmp.name, "m.db")),
                      calculator_history=CalculatorHistory(os.path.join(self.tmp.name, "calc.db")),
                      calculator_formulas=CalculatorFormulas(os.path.join(self.tmp.name, "formulas.db")),
                      calculator_scenarios=CalculatorScenarios(os.path.join(self.tmp.name, "scenarios.db")),
                      tools={"anime": None, "web": None, "weather": None, "computer": None})
        self.bot = bot
        self.app = create_app(bot, port=PORT, preload=False,
                              sharing=CalculatorSharing(os.path.join(self.tmp.name, "sharing.db")))
        self.c = self.app.test_client()
        self.h = {"Host": f"127.0.0.1:{PORT}", "X-MyAI-Token": self.app.config["TOKEN"]}

    def post(self, path, body=None, headers=None):
        return self.c.post(path, data=json.dumps(body or {}), content_type="application/json", headers=headers or self.h)

    def _static(self, name, host=None):
        return self.c.get("/static/" + name, headers={"Host": host or f"127.0.0.1:{PORT}"})

    def test_index_renders_with_token_and_version(self):
        r = self.c.get("/", headers={"Host": f"127.0.0.1:{PORT}"})
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.app.config["TOKEN"], html)
        for tag in ("{% raw %}", "{{", "{%"):
            self.assertNotIn(tag, html)          # Jinja отработал целиком
        # CSS и JS вынесены в static/ и подключаются с версией для сброса кэша
        for name in ("app.css", "calc.css", "app.js", "calc.js", "memory.js"):
            self.assertRegex(html, r'/static/%s\?v=\w+' % name.replace(".", r"\."))
        self.assertNotIn("<style>", html)
        self.assertNotIn("function md(", html)
        # каркас страницы
        for marker in ('id="sidebar"', 'id="menuBtn"', 'aria-controls="sidebar"', 'id="topMenu"', 'id="clearBtn"',
                       'id="reloadMenuBtn"', 'id="capsBtn"', 'id="capsOverlay"', 'id="dialogOverlay"', 'id="jumpBtn"',
                       'id="calcHistoryBtn"', 'role="log"', 'id="srStatus"', 'id="memoryBtn"', 'id="memoryOverlay"',
                       'id="memoryInput"', 'id="memoryList"'):
            self.assertIn(marker, html)
        self.assertNotIn('id="historyBtn"', html)    # кнопка «История» дублировала загрузку чата
        # панель «Вычисления»: три вкладки и редактор
        for marker in ('role="tablist"', 'data-tab="journal"', 'data-tab="calculators"', 'data-tab="exchange"',
                       'scenarioInputRows', 'scenarioStepRows', 'scenarioLibrary', 'scenarioImportFile', 'scenarioCategory'):
            self.assertIn(marker, html)
        self.assertNotIn('formulaVariables', html)
        self.assertNotIn('id="scenarioInputs"', html)

    def test_static_assets_served_and_guarded(self):
        app_js = self._static("app.js")
        self.assertEqual(app_js.status_code, 200)
        self.assertIn("javascript", app_js.content_type)
        js = app_js.get_data(as_text=True)
        self.assertIn("function md(", js)          # безопасный Markdown на месте
        self.assertNotIn("confirm(", js)           # диалог подтверждения свой, не системный
        for marker in ("CAPABILITIES", "askConfirm", "setActions", "closeDrawer", "scrollDown", "hintBusy"):
            self.assertIn(marker, js)
        self.assertNotIn("input.disabled", js)     # поле ввода не блокируется во время ответа
        calc = self._static("calc.js").get_data(as_text=True)
        for marker in ("buildScenarioPayload", "calcShowTab", "openScenarioEditor", "data-run-input",
                       "data-community-unpublish", "/calculator/scenarios/unpublish"):
            self.assertIn(marker, calc)
        mem = self._static("memory.js").get_data(as_text=True)
        for marker in ("loadMemory", "/memory/forget", "/memory/forget_name", "/memory/add", "data-forget"):
            self.assertIn(marker, mem)
        self.assertNotIn("innerHTML=text", mem)
        for name in ("app.css", "calc.css"):
            r = self._static(name)
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/css", r.content_type)
        self.assertEqual(self._static("nope.js").status_code, 404)
        # защита от DNS-rebinding действует и на статические файлы
        self.assertEqual(self._static("app.js", host="evil.example:5000").status_code, 403)


    def test_dns_rebinding_blocked(self):
        for host in ("evil.example", "evil.example:5000", "127.0.0.1:9999"):
            for path in ("/", "/history", "/status"):
                self.assertEqual(self.c.get(path, headers={"Host": host}).status_code, 403, (host, path))
        h = dict(self.h, Host="evil.example:5000")
        self.assertEqual(self.post("/chat", {"text": "привет"}, h).status_code, 403)

    def test_post_requires_token_and_good_origin(self):
        no_token = {"Host": self.h["Host"]}
        self.assertEqual(self.post("/chat", {"text": "x"}, no_token).status_code, 403)
        self.assertEqual(self.post("/chat", {"text": "x"}, dict(self.h, Origin="http://evil.example")).status_code, 403)
        self.assertEqual(self.post("/chat", {"text": "x"}, dict(self.h, Origin=f"http://127.0.0.1:{PORT}")).status_code, 200)

    def test_chat_and_history(self):
        d = self.post("/chat", {"text": "Посчитай 2+2"}).get_json()
        self.assertEqual(d["response"], "2 + 2 = 4"); self.assertEqual(d["route"], "CALCULATOR")
        self.assertEqual(len(self.c.get("/history", headers=self.h).get_json()["history"]), 2)
        self.assertEqual(self.post("/new").status_code, 200)
        self.assertEqual(self.c.get("/history", headers=self.h).get_json()["history"], [])

    def test_stream_is_ndjson(self):
        r = self.post("/chat/stream", {"text": "Расскажи о себе"})
        self.assertEqual(r.mimetype, "application/x-ndjson")
        events = [json.loads(line) for line in r.get_data(as_text=True).splitlines()]
        self.assertEqual(events[0]["type"], "meta"); self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["response"], "Привет из теста")
        self.assertEqual(self.post("/chat/stream", {"text": ""}).status_code, 400)

    def test_calculator_history_api_replay_and_clear(self):
        self.app.config["BOT"]._calculator_answer("2+2")
        history = self.c.get("/calculator/history", headers=self.h)
        self.assertEqual(history.status_code, 200)
        rows = history.get_json()["history"]
        self.assertEqual(len(rows), 1)
        calculation_id = rows[0]["id"]
        replay = self.post("/calculator/replay", {"id": calculation_id})
        self.assertEqual(replay.status_code, 200)
        self.assertIn("4", replay.get_json()["response"])
        self.assertEqual(len(self.c.get("/calculator/history", headers=self.h).get_json()["history"]), 2)
        cleared = self.post("/calculator/clear")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.c.get("/calculator/history", headers=self.h).get_json()["history"], [])

    def test_calculator_search_and_csv_export(self):
        bot = self.app.config["BOT"]
        bot.calculator_history.add("2+2", "2 + 2 = 4")
        bot.calculator_history.add("5*5", "5 * 5 = 25")
        found = self.c.get("/calculator/search?q=2%2B2", headers=self.h)
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.get_json()["count"], 1)
        self.assertEqual(found.get_json()["history"][0]["expression"], "2+2")
        invalid = self.c.get("/calculator/search?date_from=bad", headers=self.h)
        self.assertEqual(invalid.status_code, 400)
        exported = self.c.get("/calculator/export.csv?q=2%2B2", headers=self.h)
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/csv", exported.content_type)
        self.assertIn("2+2", exported.get_data(as_text=True))
        self.assertIn("\ufeff", exported.get_data(as_text=True))

    def test_csv_export_keeps_negative_numbers_and_escapes_formulas(self):
        bot = self.app.config["BOT"]
        bot.calculator_history.add("0 - 5", "0 - 5 = -5")
        bot.calculator_history.add("=cmd|' /C calc'!A0", "Не удалось вычислить")
        text = self.c.get("/calculator/export.csv", headers=self.h).get_data(as_text=True)
        self.assertIn(";-5\r\n", text)      # отрицательное число осталось числом
        self.assertNotIn("'-5", text)
        self.assertIn("'=cmd", text)        # формула по-прежнему экранирована

    def test_calculator_stats_api(self):
        bot = self.app.config["BOT"]
        bot.calculator_history.add("2+2", "2 + 2 = 4")
        bot.calculator_history.add("10*5", "10 * 5 = 50")
        response = self.c.get("/calculator/stats?days=7", headers=self.h)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["daily"]), 7)
        self.assertEqual(sum(x["count"] for x in data["daily"]), 2)
        self.assertEqual(self.c.get("/calculator/stats?days=0", headers=self.h).status_code, 400)
        self.assertEqual(self.c.get("/calculator/stats?days=abc", headers=self.h).status_code, 400)

    def test_calculator_formulas_api(self):
        templates = self.c.get('/calculator/formulas', headers=self.h)
        self.assertEqual(templates.status_code, 200)
        self.assertGreaterEqual(len(templates.get_json()['templates']), 3)
        saved = self.post('/calculator/formulas/save', {
            'name': 'Стоимость', 'expression': 'price * quantity',
            'variables': {'price': 'Цена', 'quantity': 'Количество'}
        })
        self.assertEqual(saved.status_code, 200)
        formula_id = saved.get_json()['id']
        result = self.post('/calculator/formulas/evaluate', {
            'expression': 'price * quantity', 'variables': {'price': 12, 'quantity': 3}
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['result'], '36')
        self.assertEqual(len(self.app.config['BOT'].calculator_history.list()), 1)
        deleted = self.post('/calculator/formulas/delete', {'id': formula_id})
        self.assertEqual(deleted.status_code, 200)

    def test_calculator_formula_api_rejects_unsafe_expression(self):
        result = self.post('/calculator/formulas/evaluate', {
            'expression': '__import__("os").system("whoami")', 'variables': {}
        })
        self.assertEqual(result.status_code, 400)

    def test_calculator_scenarios_api(self):
        payload = {
            "name": "Расчёт заказа", "description": "Цена с налогом",
            "inputs": {"price": "Цена", "quantity": "Количество", "vat": "НДС"},
            "steps": [
                {"id": "subtotal", "name": "Стоимость", "expression": "price * quantity",
                 "variables": {"price": "input.price", "quantity": "input.quantity"}},
                {"id": "tax", "name": "НДС", "expression": "subtotal * vat / 100",
                 "variables": {"subtotal": "step.subtotal", "vat": "input.vat"}},
                {"id": "total", "name": "Итого", "expression": "subtotal + tax",
                 "variables": {"subtotal": "step.subtotal", "tax": "step.tax"}}
            ]
        }
        saved = self.post('/calculator/scenarios/save', payload)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        sid = saved.get_json()['id']
        run = self.post('/calculator/scenarios/run', {'id': sid, 'values': {'price': 100, 'quantity': 3, 'vat': 20}})
        self.assertEqual(run.status_code, 200, run.get_json())
        self.assertEqual(run.get_json()['final_result'], '360')
        self.assertEqual(len(self.app.config['BOT'].calculator_history.list()), 1)
        self.assertEqual(len(self.c.get('/calculator/scenarios', headers=self.h).get_json()['scenarios']), 1)
        self.assertEqual(self.post('/calculator/scenarios/delete', {'id': sid}).status_code, 200)

    def test_calculator_scenario_library_import_export(self):
        library = self.c.get('/calculator/scenarios/library', headers=self.h)
        self.assertEqual(library.status_code, 200)
        self.assertGreaterEqual(len(library.get_json()['templates']), 5)
        template = library.get_json()['templates'][0]
        saved = self.post('/calculator/scenarios/save', template)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        listed = self.c.get('/calculator/scenarios', headers=self.h).get_json()['scenarios']
        self.assertEqual(listed[0]['category'], template['category'])
        exported = self.c.get('/calculator/scenarios/export.json', headers=self.h)
        self.assertEqual(exported.status_code, 200)
        payload = exported.get_json()
        self.assertEqual(payload['format'], 'myai-calculator-scenarios')
        self.assertEqual(len(payload['scenarios']), 1)

    def test_calculator_scenario_import_rejects_invalid_and_duplicates(self):
        bad = self.post('/calculator/scenarios/import', {'format': 'wrong', 'version': 1, 'scenarios': []})
        self.assertEqual(bad.status_code, 400)
        template = self.c.get('/calculator/scenarios/library', headers=self.h).get_json()['templates'][0]
        payload = {'format': 'myai-calculator-scenarios', 'version': 1, 'scenarios': [template]}
        self.assertEqual(self.post('/calculator/scenarios/import', payload).status_code, 200)
        self.assertEqual(self.post('/calculator/scenarios/import', payload).status_code, 400)

    def _save_scenario(self, name="Скидка"):
        payload = {"name": name, "description": "Цена со скидкой", "category": "Покупки",
                   "inputs": {"price": "Цена", "discount": "Скидка, %"},
                   "steps": [{"id": "total", "name": "Итого", "expression": "price - price * discount / 100",
                              "variables": {"price": "input.price", "discount": "input.discount"}}]}
        saved = self.post('/calculator/scenarios/save', payload)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        return saved.get_json()['id']

    def test_calculator_sharing_api_full_cycle(self):
        sid = self._save_scenario()
        self.assertEqual(self.c.get('/calculator/scenarios/community', headers=self.h).get_json()['publications'], [])
        pub = self.post('/calculator/scenarios/publish', {'id': sid})
        self.assertEqual(pub.status_code, 200, pub.get_json())
        pid = pub.get_json()['publication_id']
        listed = self.c.get('/calculator/scenarios/community?q=скид', headers=self.h).get_json()['publications']
        self.assertEqual([x['id'] for x in listed], [pid])
        self.assertNotIn('package', listed[0])
        self.assertEqual(self.c.get('/calculator/scenarios/community?q=нетакого', headers=self.h).get_json()['publications'], [])
        # такое имя уже занято: установка отклоняется целиком
        dup = self.post('/calculator/scenarios/install', {'publication_id': pid})
        self.assertEqual(dup.status_code, 400)
        self.assertEqual(self.post('/calculator/scenarios/delete', {'id': sid}).status_code, 200)
        ok = self.post('/calculator/scenarios/install', {'publication_id': pid})
        self.assertEqual(ok.status_code, 200, ok.get_json()); self.assertEqual(ok.get_json()['installed'], 1)
        installed = self.c.get('/calculator/scenarios', headers=self.h).get_json()['scenarios']
        self.assertEqual(len(installed), 1)
        run = self.post('/calculator/scenarios/run', {'id': installed[0]['id'], 'values': {'price': 200, 'discount': 25}})
        self.assertEqual(run.get_json()['final_result'], '150')
        self.assertEqual(self.post('/calculator/scenarios/rate', {'publication_id': pid, 'rating': '5'}).status_code, 200)
        self.assertEqual(self.post('/calculator/scenarios/rate', {'publication_id': pid, 'rating': 3}).status_code, 200)
        item = self.c.get('/calculator/scenarios/community', headers=self.h).get_json()['publications'][0]
        self.assertEqual((item['rating_count'], item['average_rating'], item['installs']), (2, 4, 1))
        # снятие с публикации не трогает установленную копию
        self.assertEqual(self.post('/calculator/scenarios/unpublish', {'publication_id': pid}).status_code, 200)
        self.assertEqual(self.post('/calculator/scenarios/unpublish', {'publication_id': pid}).status_code, 404)
        self.assertEqual(self.c.get('/calculator/scenarios/community', headers=self.h).get_json()['publications'], [])
        self.assertEqual(len(self.c.get('/calculator/scenarios', headers=self.h).get_json()['scenarios']), 1)

    def test_calculator_sharing_api_rejects_bad_input(self):
        sid = self._save_scenario()
        pid = self.post('/calculator/scenarios/publish', {'id': sid}).get_json()['publication_id']
        for rating in (0, 6, True, 4.5, 'abc', None):
            r = self.post('/calculator/scenarios/rate', {'publication_id': pid, 'rating': rating})
            self.assertEqual(r.status_code, 400, rating)
        self.assertEqual(self.post('/calculator/scenarios/rate', {'publication_id': 999, 'rating': 5}).status_code, 404)
        self.assertEqual(self.post('/calculator/scenarios/install', {'publication_id': 999}).status_code, 404)
        self.assertEqual(self.post('/calculator/scenarios/install', {}).status_code, 400)
        self.assertEqual(self.post('/calculator/scenarios/unpublish', {}).status_code, 400)
        self.assertEqual(self.post('/calculator/scenarios/unpublish', {'publication_id': 'x'}).status_code, 400)
        self.assertEqual(self.post('/calculator/scenarios/publish', {'id': 999}).status_code, 404)
        self.assertEqual(self.post('/calculator/scenarios/publish', {}).status_code, 400)
        for bad in ('abc', 1.5, True, None):
            r = self.post('/calculator/scenarios/publish', {'id': bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.get_json()['error'], 'Некорректный номер сценария.')

    def test_scenario_inputs_keep_author_order(self):
        payload = {"name": "Порядок", "description": "", "category": "Покупки",
                   "inputs": {"zeta": "Первый", "alpha": "Второй", "mid": "Третий"},
                   "steps": [{"id": "total", "name": "Итого", "expression": "zeta + alpha + mid",
                              "variables": {"zeta": "input.zeta", "alpha": "input.alpha", "mid": "input.mid"}}]}
        self.assertEqual(self.post('/calculator/scenarios/save', payload).status_code, 200)
        listed = self.c.get('/calculator/scenarios', headers=self.h).get_json()['scenarios'][0]
        self.assertEqual(list(listed['inputs']), ['zeta', 'alpha', 'mid'])
        exported = self.c.get('/calculator/scenarios/export.json', headers=self.h).get_json()
        self.assertEqual(list(exported['scenarios'][0]['inputs']), ['zeta', 'alpha', 'mid'])

    def test_memory_api_full_cycle(self):
        get = lambda: self.c.get('/memory', headers=self.h).get_json()
        self.assertEqual(get()['notes'], [])
        self.assertEqual(self.post('/memory/add', {'text': 'я люблю игры'}).get_json(), {'status': 'ok', 'added': True})
        self.assertFalse(self.post('/memory/add', {'text': 'Я ЛЮБЛЮ ИГРЫ'}).get_json()['added'])   # дубль без учёта регистра
        self.assertEqual(self.post('/memory/add', {'text': '   '}).status_code, 400)
        self.assertEqual(self.post('/memory/add', {'text': 'я' * 501}).status_code, 400)
        data = get(); self.assertEqual((len(data['notes']), data['total']), (1, 1))
        note = data['notes'][0]
        self.assertEqual((note['text'], isinstance(note['id'], int)), ('я люблю игры', True))
        for bad in (None, True, 'x', 1.5, [1]):
            self.assertEqual(self.post('/memory/forget', {'id': bad}).status_code, 400, bad)
        self.assertEqual(self.post('/memory/forget', {'id': 999}).status_code, 404)
        self.assertEqual(self.post('/memory/forget', {'id': str(note['id'])}).status_code, 200)
        self.assertEqual(get()['notes'], [])
        self.assertEqual(self.post('/memory/forget', {'id': note['id']}).status_code, 404)

    def test_memory_name_is_shown_once_and_can_be_forgotten(self):
        self.assertEqual(self.post('/chat', {'text': 'Меня зовут Иван'}).status_code, 200)
        data = self.c.get('/memory', headers=self.h).get_json()
        self.assertEqual(data['name'], 'Иван')
        self.assertEqual(data['notes'], [])           # заметка с именем дублирует имя и скрыта
        self.assertEqual(data['total'], 1)
        self.post('/memory/add', {'text': 'я люблю игры'})
        self.assertEqual(self.post('/memory/forget_name').get_json(), {'status': 'ok', 'deleted': True})
        data = self.c.get('/memory', headers=self.h).get_json()
        self.assertIsNone(data['name'])
        self.assertEqual([x['text'] for x in data['notes']], ['я люблю игры'])   # прочие заметки не тронуты
        self.assertEqual(data['total'], 1)
        self.assertFalse(self.post('/memory/forget_name').get_json()['deleted'])
        self.assertEqual(self.post('/chat', {'text': 'Как меня зовут?'}).get_json()['response'], 'Ты ещё не говорил мне своё имя.')

    def test_forgetting_name_note_by_id_also_forgets_name(self):
        self.post('/chat', {'text': 'Меня зовут Иван'})
        note = next(x for x in self.bot.memory.list_items() if x['text'].startswith('Пользователя зовут'))
        self.assertTrue(self.bot.forget_note(note['id']))
        self.assertIsNone(self.bot.user_name)
        self.assertFalse(self.bot.forget_note(note['id']))

    def test_memory_api_requires_token(self):
        no_token = {"Host": self.h["Host"]}
        for path in ('add', 'forget', 'forget_name'):
            self.assertEqual(self.post('/memory/' + path, {'text': 'x', 'id': 1}, no_token).status_code, 403, path)

    def test_calculator_sharing_api_requires_token(self):
        no_token = {"Host": self.h["Host"]}
        for path in ('publish', 'install', 'rate', 'unpublish'):
            self.assertEqual(self.post('/calculator/scenarios/' + path, {'id': 1, 'publication_id': 1, 'rating': 5}, no_token).status_code, 403, path)

    def test_calculator_replay_validates_id(self):
        self.assertEqual(self.post("/calculator/replay", {"id": "bad"}).status_code, 400)
        self.assertEqual(self.post("/calculator/replay", {"id": 999}).status_code, 404)

    def test_status_and_health(self):
        s = self.c.get("/status", headers=self.h).get_json()
        self.assertEqual(s["model_state"], "ready"); self.assertIn("version", s)
        self.assertTrue(self.c.get("/health", headers=self.h).get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
