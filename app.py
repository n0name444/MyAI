"""Веб-сервер MyAI (только localhost)."""
from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import os
import secrets
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from chat_model import ChatBot
from route_journal import RouteJournal
from version import __version__
from tools.calculator_formulas import TEMPLATES, evaluate_formula
from tools.calculator_scenarios import CalculatorScenarios, CATEGORIES, LIBRARY
from tools.calculator_sharing import CalculatorSharing
from tools.calculator import SafeCalculator

BASE_DIR = Path(__file__).resolve().parent
PORT = int(os.getenv("MYAI_PORT", "5000"))


def _setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        file_handler = RotatingFileHandler(BASE_DIR / "myai.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

MAX_NOTE_CHARS = 500


def _strict_id(value) -> int:
    """Целый id из числа или строки из цифр; bool, дроби и мусор отклоняются."""
    if isinstance(value, bool):
        raise ValueError("Некорректный номер.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    raise ValueError("Некорректный номер.")


def _asset_version() -> str:
    """Версия статических файлов для ?v=…: после обновления браузер не берёт старый app.js из кэша."""
    try:
        return str(int(max(p.stat().st_mtime for p in (BASE_DIR / "static").glob("*") if p.is_file())))
    except (OSError, ValueError):
        return __version__


def create_app(chatbot: ChatBot | None = None, port: int = PORT, preload: bool = True,
               sharing: CalculatorSharing | None = None) -> Flask:
    app = Flask(__name__)
    # Порядок параметров сценария задаёт автор; по умолчанию Flask сортирует ключи JSON по алфавиту.
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    log = logging.getLogger("myai.app")

    bot = chatbot or ChatBot(journal=RouteJournal.from_env(BASE_DIR))
    sharing = sharing or CalculatorSharing(str(BASE_DIR / "calculator_sharing.sqlite3"))
    token = secrets.token_urlsafe(24)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    @app.before_request
    def guard():
        # Защита от DNS-rebinding: принимаем только запросы, адресованные localhost.
        if request.host.lower() not in allowed_hosts:
            return jsonify({"error": "forbidden host"}), 403
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin not in allowed_origins:
                return jsonify({"error": "forbidden origin"}), 403
            if not hmac.compare_digest(request.headers.get("X-MyAI-Token", ""), token):
                return jsonify({"error": "bad token"}), 403
        return None

    def _text() -> str:
        data = request.get_json(silent=True) or {}
        return str(data.get("text", "") or "").strip()

    @app.get("/")
    def home():
        return render_template("index.html", token=token, version=__version__, asset_v=_asset_version())

    @app.post("/chat")
    def chat():
        text = _text()
        if not text:
            return jsonify({"response": "Напиши сообщение.", "route": "CHAT", "model": "", "sources": []})
        try:
            return jsonify(bot.reply(text))
        except Exception:
            log.exception("/chat")
            return jsonify({"response": "Произошла ошибка. Подробности — в myai.log.", "route": "ERROR", "sources": []}), 500

    @app.post("/chat/stream")
    def chat_stream():
        text = _text()
        if not text:
            return jsonify({"error": "empty"}), 400

        def generate():
            try:
                for event in bot.reply_stream(text):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except Exception:
                log.exception("/chat/stream")
                yield json.dumps({"type": "done", "response": "Произошла ошибка. Подробности — в myai.log.",
                                  "route": "ERROR", "model": "", "sources": []}, ensure_ascii=False) + "\n"

        return Response(generate(), mimetype="application/x-ndjson",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/new")
    def new_chat():
        bot.new_chat()
        return jsonify({"status": "ok"})

    @app.post("/clear")
    def clear():
        try:
            bot.clear_history()
            return jsonify({"status": "ok"})
        except Exception:
            log.exception("/clear")
            return jsonify({"status": "error"}), 500

    @app.post("/reload_model")
    def reload_model():
        return jsonify({"status": "started" if bot.reload_model() else "busy"})

    @app.get("/memory")
    def memory_overview():
        try:
            return jsonify(bot.memory_overview())
        except Exception:
            log.exception("/memory")
            return jsonify({"error": "Не удалось загрузить память."}), 500

    @app.post("/memory/add")
    def memory_add():
        text = _text()
        if not text:
            return jsonify({"error": "Введите текст заметки."}), 400
        if len(text) > MAX_NOTE_CHARS:
            return jsonify({"error": f"Заметка слишком длинная: не больше {MAX_NOTE_CHARS} символов."}), 400
        try:
            return jsonify({"status": "ok", "added": bool(bot.memory.add(text))})
        except Exception:
            log.exception("/memory/add")
            return jsonify({"error": "Не удалось сохранить заметку."}), 500

    @app.post("/memory/forget")
    def memory_forget():
        data = request.get_json(silent=True) or {}
        try:
            note_id = _strict_id(data.get("id"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            if not bot.forget_note(note_id):
                return jsonify({"error": "Такой заметки уже нет."}), 404
            return jsonify({"status": "ok"})
        except Exception:
            log.exception("/memory/forget")
            return jsonify({"error": "Не удалось забыть заметку."}), 500

    @app.post("/memory/forget_name")
    def memory_forget_name():
        try:
            return jsonify({"status": "ok", "deleted": bot.forget_name()})
        except Exception:
            log.exception("/memory/forget_name")
            return jsonify({"error": "Не удалось забыть имя."}), 500

    @app.get("/history")
    def history():
        return jsonify({"history": bot.get_history()})

    @app.get("/calculator/history")
    def calculator_history():
        try:
            return jsonify({"history": bot.calculator_history.list(100)})
        except Exception:
            log.exception("/calculator/history")
            return jsonify({"error": "Не удалось загрузить историю вычислений."}), 500

    def _calculator_filters():
        return (request.args.get("q", ""), request.args.get("date_from"),
                request.args.get("date_to"))

    @app.get("/calculator/stats")
    def calculator_stats():
        try:
            days = request.args.get("days", "14")
            try:
                days = int(days)
            except (TypeError, ValueError):
                return jsonify({"error": "Период должен быть числом дней."}), 400
            if not 1 <= days <= 90:
                return jsonify({"error": "Период должен быть от 1 до 90 дней."}), 400
            return jsonify(bot.calculator_history.stats(days))
        except Exception:
            log.exception("/calculator/stats")
            return jsonify({"error": "Не удалось построить статистику вычислений."}), 500

    @app.get("/calculator/search")
    def calculator_search():
        try:
            query, date_from, date_to = _calculator_filters()
            rows = bot.calculator_history.search(query, date_from, date_to)
            return jsonify({"history": rows, "count": len(rows)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            log.exception("/calculator/search")
            return jsonify({"error": "Не удалось выполнить поиск по истории."}), 500

    @app.get("/calculator/export.csv")
    def calculator_export():
        try:
            query, date_from, date_to = _calculator_filters()
            rows = bot.calculator_history.search(query, date_from, date_to, limit=500)
            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
            writer.writerow(["ID", "Дата (UTC)", "Выражение", "Ответ", "Результат"])
            def safe_cell(value):
                value = "" if value is None else str(value)
                if not value.startswith(("=", "+", "-", "@", "\t", "\r")):
                    return value
                try:
                    float(value.replace(",", "."))   # обычное число, в т.ч. отрицательное
                except ValueError:
                    return "'" + value               # потенциальная формула — экранируем
                return value
            for row in rows:
                writer.writerow([row["id"], row["created_at"], safe_cell(row["expression"]),
                                 safe_cell(row["answer"]), safe_cell(row["result"])])
            csv_data = "\ufeff" + output.getvalue()
            return Response(csv_data, mimetype="text/csv; charset=utf-8",
                            headers={"Content-Disposition": "attachment; filename=MyAI_calculations.csv"})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            log.exception("/calculator/export.csv")
            return jsonify({"error": "Не удалось экспортировать историю."}), 500

    @app.get("/calculator/formulas")
    def calculator_formulas():
        try:
            return jsonify({"formulas": bot.calculator_formulas.list(), "templates": TEMPLATES})
        except Exception:
            log.exception("/calculator/formulas")
            return jsonify({"error": "Не удалось загрузить формулы."}), 500

    @app.post("/calculator/formulas/save")
    def calculator_formula_save():
        data = request.get_json(silent=True) or {}
        try:
            formula_id = bot.calculator_formulas.save(data.get("name", ""), data.get("expression", ""),
                                                       data.get("variables", {}))
            return jsonify({"status": "ok", "id": formula_id})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            log.exception("/calculator/formulas/save")
            return jsonify({"error": "Не удалось сохранить формулу."}), 500

    @app.post("/calculator/formulas/evaluate")
    def calculator_formula_evaluate():
        data = request.get_json(silent=True) or {}
        try:
            expression = str(data.get("expression", ""))
            variables = data.get("variables", {})
            result = evaluate_formula(expression, variables)
            answer = SafeCalculator.format_number(result)
            history_expression = "Формула: " + expression + " | " + json.dumps(variables, ensure_ascii=False)
            bot.calculator_history.add(history_expression, "Результат: " + answer)
            return jsonify({"result": answer, "expression": expression})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except ZeroDivisionError:
            return jsonify({"error": "Деление на ноль."}), 400
        except Exception:
            log.exception("/calculator/formulas/evaluate")
            return jsonify({"error": "Не удалось вычислить формулу."}), 500

    @app.post("/calculator/formulas/delete")
    def calculator_formula_delete():
        data = request.get_json(silent=True) or {}
        try:
            formula_id = _strict_id(data.get("id"))
            if formula_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректный номер формулы."}), 400
        try:
            if not bot.calculator_formulas.delete(formula_id):
                return jsonify({"error": "Формула не найдена."}), 404
            return jsonify({"status": "ok"})
        except Exception:
            log.exception("/calculator/formulas/delete")
            return jsonify({"error": "Не удалось удалить формулу."}), 500

    @app.get("/calculator/scenarios")
    def calculator_scenarios():
        try:
            return jsonify({"scenarios": bot.calculator_scenarios.list(), "categories": CATEGORIES})
        except Exception:
            log.exception("/calculator/scenarios")
            return jsonify({"error": "Не удалось загрузить сценарии."}), 500

    @app.post("/calculator/scenarios/save")
    def calculator_scenario_save():
        data = request.get_json(silent=True) or {}
        try:
            scenario_id = bot.calculator_scenarios.save(data.get("name", ""), data.get("description", ""),
                                                        data.get("inputs", {}), data.get("steps", []), data.get("category", "Пользовательские"))
            return jsonify({"status": "ok", "id": scenario_id})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            log.exception("/calculator/scenarios/save")
            return jsonify({"error": "Не удалось сохранить сценарий."}), 500

    @app.post("/calculator/scenarios/update")
    def calculator_scenario_update():
        data = request.get_json(silent=True) or {}
        try:
            scenario_id = _strict_id(data.get("id"))
            if scenario_id <= 0: raise ValueError
            version = bot.calculator_scenarios.update(scenario_id, data.get("name", ""), data.get("description", ""),
                data.get("inputs", {}), data.get("steps", []), data.get("category", "Пользовательские"))
            return jsonify({"status": "ok", "id": scenario_id, "version": version})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc) or "Некорректный сценарий."}), 400
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'")}), 404
        except Exception:
            log.exception("/calculator/scenarios/update")
            return jsonify({"error": "Не удалось обновить сценарий."}), 500

    @app.post("/calculator/scenarios/copy")
    def calculator_scenario_copy():
        data = request.get_json(silent=True) or {}
        try:
            scenario_id = _strict_id(data.get("id"))
            if scenario_id <= 0: raise ValueError
            new_id = bot.calculator_scenarios.duplicate(scenario_id, data.get("name"))
            return jsonify({"status": "ok", "id": new_id})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc) or "Некорректный номер сценария."}), 400
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'")}), 404
        except Exception:
            log.exception("/calculator/scenarios/copy")
            return jsonify({"error": "Не удалось скопировать сценарий."}), 500

    @app.get("/calculator/scenarios/<int:scenario_id>/versions")
    def calculator_scenario_versions(scenario_id):
        try:
            return jsonify({"versions": bot.calculator_scenarios.versions(scenario_id)})
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'")}), 404
        except Exception:
            log.exception("/calculator/scenarios/versions")
            return jsonify({"error": "Не удалось загрузить версии сценария."}), 500

    @app.post("/calculator/scenarios/restore")
    def calculator_scenario_restore():
        data = request.get_json(silent=True) or {}
        try:
            scenario_id = _strict_id(data.get("id")); version = _strict_id(data.get("version"))
            if scenario_id <= 0 or version <= 0: raise ValueError
            new_version = bot.calculator_scenarios.restore_version(scenario_id, version)
            return jsonify({"status": "ok", "version": new_version})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc) or "Некорректная версия."}), 400
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'")}), 404
        except Exception:
            log.exception("/calculator/scenarios/restore")
            return jsonify({"error": "Не удалось восстановить версию."}), 500

    @app.get("/calculator/scenarios/library")
    def calculator_scenario_library():
        return jsonify({"templates": LIBRARY, "categories": CATEGORIES})

    @app.get("/calculator/scenarios/export.json")
    def calculator_scenario_export():
        try:
            payload = bot.calculator_scenarios.export_data()
            data = json.dumps(payload, ensure_ascii=False, indent=2)
            return Response(data, mimetype="application/json; charset=utf-8",
                            headers={"Content-Disposition": "attachment; filename=MyAI_scenarios.json"})
        except Exception:
            log.exception("/calculator/scenarios/export.json")
            return jsonify({"error": "Не удалось экспортировать сценарии."}), 500

    @app.post("/calculator/scenarios/import")
    def calculator_scenario_import():
        data = request.get_json(silent=True)
        try:
            count = bot.calculator_scenarios.import_data(data)
            return jsonify({"status": "ok", "imported": count})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            log.exception("/calculator/scenarios/import")
            return jsonify({"error": "Не удалось импортировать сценарии."}), 500

    @app.post("/calculator/scenarios/run")
    def calculator_scenario_run():
        data = request.get_json(silent=True) or {}
        try:
            scenario, results = bot.calculator_scenarios.run(_strict_id(data.get("id")), data.get("values", {}))
            lines = [f"{item['name']}: {item['result']}" for item in results]
            answer = "\n".join(lines)
            expression = "Сценарий: " + scenario["name"] + " | " + json.dumps(data.get("values", {}), ensure_ascii=False)
            bot.calculator_history.add(expression, answer)
            return jsonify({"scenario": scenario["name"], "results": results, "final_result": results[-1]["result"]})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc) or "Некорректный номер сценария."}), 400
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'")}), 404
        except ZeroDivisionError:
            return jsonify({"error": "Деление на ноль в одном из шагов."}), 400
        except Exception:
            log.exception("/calculator/scenarios/run")
            return jsonify({"error": "Не удалось выполнить сценарий."}), 500

    @app.post("/calculator/scenarios/delete")
    def calculator_scenario_delete():
        data = request.get_json(silent=True) or {}
        try:
            scenario_id = _strict_id(data.get("id"))
            if scenario_id <= 0: raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректный номер сценария."}), 400
        try:
            if not bot.calculator_scenarios.delete(scenario_id):
                return jsonify({"error": "Сценарий не найден."}), 404
            return jsonify({"status": "ok"})
        except Exception:
            log.exception("/calculator/scenarios/delete")
            return jsonify({"error": "Не удалось удалить сценарий."}), 500

    @app.get("/calculator/scenarios/community")
    def calculator_scenario_community():
        try:
            return jsonify({"publications": sharing.list(request.args.get("q",""), request.args.get("category",""))})
        except Exception:
            log.exception("/calculator/scenarios/community")
            return jsonify({"error":"Не удалось загрузить локальный каталог."}),500

    @app.post("/calculator/scenarios/publish")
    def calculator_scenario_publish():
        data=request.get_json(silent=True) or {}
        try:
            publication_id=sharing.publish(bot.calculator_scenarios,data.get("id"))
            return jsonify({"status":"ok","publication_id":publication_id})
        except (TypeError,ValueError) as exc: return jsonify({"error":str(exc)}),400
        except KeyError as exc: return jsonify({"error":str(exc).strip("'")}),404
        except Exception:
            log.exception("/calculator/scenarios/publish")
            return jsonify({"error":"Не удалось опубликовать сценарий."}),500

    @app.post("/calculator/scenarios/install")
    def calculator_scenario_install():
        data=request.get_json(silent=True) or {}
        try:
            count=sharing.install(data.get("publication_id"),bot.calculator_scenarios)
            return jsonify({"status":"ok","installed":count})
        except (TypeError,ValueError) as exc: return jsonify({"error":str(exc)}),400
        except KeyError as exc: return jsonify({"error":str(exc).strip("'")}),404
        except Exception:
            log.exception("/calculator/scenarios/install")
            return jsonify({"error":"Не удалось установить сценарий."}),500

    @app.post("/calculator/scenarios/rate")
    def calculator_scenario_rate():
        data=request.get_json(silent=True) or {}
        try:
            sharing.rate(data.get("publication_id"),data.get("rating"))
            return jsonify({"status":"ok"})
        except (TypeError,ValueError) as exc: return jsonify({"error":str(exc)}),400
        except KeyError as exc: return jsonify({"error":str(exc).strip("'")}),404
        except Exception:
            log.exception("/calculator/scenarios/rate")
            return jsonify({"error":"Не удалось сохранить оценку."}),500

    @app.post("/calculator/scenarios/unpublish")
    def calculator_scenario_unpublish():
        data=request.get_json(silent=True) or {}
        try:
            if not sharing.unpublish(data.get("publication_id")): return jsonify({"error":"Публикация не найдена."}),404
            return jsonify({"status":"ok"})
        except (TypeError,ValueError): return jsonify({"error":"Некорректный номер публикации."}),400
        except Exception:
            log.exception("/calculator/scenarios/unpublish")
            return jsonify({"error":"Не удалось снять публикацию."}),500

    @app.post("/calculator/replay")
    def calculator_replay():
        data = request.get_json(silent=True) or {}
        try:
            calculation_id = _strict_id(data.get("id"))
            if calculation_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректный номер расчёта."}), 400
        try:
            row = bot.calculator_history.get(calculation_id)
            if row is None:
                return jsonify({"error": "Расчёт не найден."}), 404
            answer = bot.replay_calculation(calculation_id)
            return jsonify({"id": calculation_id, "expression": row["expression"], "response": answer})
        except Exception:
            log.exception("/calculator/replay")
            return jsonify({"error": "Не удалось повторить расчёт."}), 500

    @app.post("/calculator/clear")
    def calculator_clear():
        try:
            deleted = bot.calculator_history.clear()
            return jsonify({"status": "ok", "deleted": deleted})
        except Exception:
            log.exception("/calculator/clear")
            return jsonify({"error": "Не удалось очистить историю вычислений."}), 500

    @app.get("/status")
    def status():
        return jsonify(bot.status())

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "service": "MyAI", "version": __version__})

    app.config["BOT"] = bot
    app.config["TOKEN"] = token
    if preload and os.getenv("MYAI_PRELOAD", "1").lower() not in {"0", "false", "no"}:
        bot.preload_async()
    return app


if __name__ == "__main__":
    _setup_logging()
    application = create_app()
    logging.getLogger("myai").info("MyAI v%s запущен: http://127.0.0.1:%s", __version__, PORT)
    application.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
