"""Журнал маршрутов: одна JSON-строка на каждое сообщение пользователя.

Зачем: «роутер ошибается» из обычного лога не видно. Здесь для каждого сообщения записано, куда оно ушло,
почему, сколько заняло и чем закончилось — а `python route_report.py` показывает, где чаще всего бывают отказы и ошибки.

Приватность: файл лежит только на этом компьютере (logs/routes.jsonl). Текст фразы обрезается до 120 символов,
длинные числа и e-mail маскируются. Отключить журнал: переменная окружения MYAI_ROUTE_LOG=0.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Итог: ok — ответ получен; empty — инструмент отказал или ничего не нашёл; error — исключение; stopped — прервал пользователь.
OUTCOMES = ("ok", "empty", "error", "stopped")
# Признаки «инструмент не справился» в готовом ответе. Это эвристика по тексту: ответы инструментов фиксированные.
EMPTY_MARKERS = ("не удалось", "недоступн", "не нашёл", "не нашел", "ничего подходящего", "не буду придумывать",
                 "не найден", "не поддерживается", "не понял", "не могу", "безопасном списке", "безопасного списка",
                 "запрещ", "проверь интернет")
_FREE_TEXT_ROUTES = {"CHAT", "MEMORY"}     # свободный текст модели: слова вроде «не могу» там не означают отказ инструмента

_NUM_RE = re.compile(r"\d{6,}")
_MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def classify_outcome(route: str, answer: str) -> str:
    if route == "ERROR":
        return "error"
    if route in _FREE_TEXT_ROUTES:
        return "ok"
    low = (answer or "").lower()
    return "empty" if any(m in low for m in EMPTY_MARKERS) else "ok"


def _mask(text: str, limit: int) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = _MAIL_RE.sub("***", t)
    t = _NUM_RE.sub("***", t)
    return t[:limit]


class RouteJournal:
    def __init__(self, path, max_bytes: int = 1_000_000, backups: int = 2, max_text: int = 120):
        self.path = Path(path)
        self.max_bytes, self.backups, self.max_text = max_bytes, max(0, backups), max_text
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, base_dir) -> Optional["RouteJournal"]:
        if os.getenv("MYAI_ROUTE_LOG", "1").strip().lower() in {"0", "false", "off", "no"}:
            return None
        return cls(os.getenv("MYAI_ROUTE_LOG_PATH") or Path(base_dir) / "logs" / "routes.jsonl")

    def record(self, *, text: str, route: str, outcome: str, ms: int, reason: Optional[str] = None,
               prev: Optional[str] = None, followup: bool = False, error: Optional[str] = None,
               llm: bool = False) -> None:
        """Дописывает строку. Никогда не бросает исключений: журнал не должен ломать ответ пользователю."""
        entry: Dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="seconds"), "text": _mask(text, self.max_text), "route": route,
            "reason": _mask(reason or "", 80), "prev": prev, "followup": bool(followup), "ms": int(ms),
            "outcome": outcome if outcome in OUTCOMES else "ok", "error": error, "llm": bool(llm)}
        try:
            line = json.dumps(entry, ensure_ascii=False)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate()
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception as exc:  # noqa: BLE001
            log.debug("Журнал маршрутов недоступен: %s", exc)

    def _rotate(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
        except OSError:
            return
        if self.backups == 0:
            self.path.unlink(missing_ok=True)
            return
        for i in range(self.backups, 0, -1):
            src = self.path if i == 1 else self.path.with_name(f"{self.path.name}.{i - 1}")
            dst = self.path.with_name(f"{self.path.name}.{i}")
            if src.exists():
                dst.unlink(missing_ok=True)
                src.rename(dst)

    @staticmethod
    def read_entries(path, include_rotated: bool = True) -> List[dict]:
        """Читает журнал от старых записей к новым; битые строки пропускает."""
        base = Path(path)
        files = []
        if include_rotated:
            files += sorted(base.parent.glob(base.name + ".*"), key=lambda p: p.suffix, reverse=True)
        files.append(base)
        out: List[dict] = []
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(obj, dict) and "route" in obj:
                            out.append(obj)
            except OSError:
                continue
        return out
