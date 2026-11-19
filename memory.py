"""SQLite-память MyAI: долговременные заметки + журнал реплик."""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Set

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
MAX_TURNS_KEPT = 1000

# Служебные слова не должны сопоставлять заметки («что», «как», «меня»...).
STOPWORDS = frozenset("""
это этот эта эти тот что как так вот или для при над под про без где когда если чтобы тоже тебе тебя
меня мне мной мое моя мои мой моей моим моем наш наша наши ваш ваша ваши его ему ней них там тут
уже еще ещё очень можно нужно надо есть был была были будет будут быть себя свой свои своя
the and for with that this from have has are was were you your
""".split())


def stem(word: str) -> str:
    """Грубая основа: отсекает падежные окончания, чтобы «собаку» совпало с «собака»."""
    w = word.lower().replace("ё", "е")
    n = len(w)
    if n <= 3:
        return w
    if n <= 5:
        return w[:-1]
    return w[:5]


def key_stems(text: str) -> Set[str]:
    words = re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower())
    return {stem(w) for w in words if w.replace("ё", "е") not in STOPWORDS and w not in STOPWORDS}


class Memory:
    """Заметки («запомни …») и последние реплики диалога."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else BASE_DIR / "memory.sqlite3"
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, kind TEXT DEFAULT 'note', created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            con.execute("CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    # ---------- заметки ----------
    def add(self, text: str, kind: str = "note") -> bool:
        """Сохраняет заметку. False — если текст пустой или такая заметка уже есть."""
        text = (text or "").strip()
        if not text:
            return False
        with self._connect() as con:
            # SQLite lower() не знает кириллицу, поэтому сравниваем в Python.
            folded = text.casefold()
            if any(r["text"].casefold() == folded for r in con.execute("SELECT text FROM memories")):
                return False
            con.execute("INSERT INTO memories(text, kind) VALUES (?, ?)", (text, kind))
        return True

    def list_notes(self, limit: int = 30) -> List[str]:
        with self._connect() as con:
            rows = con.execute("SELECT text FROM memories ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r["text"] for r in reversed(rows)]

    def search(self, query: str, limit: int = 6) -> List[str]:
        q = key_stems(query)
        if not q:
            return []
        with self._connect() as con:
            rows = con.execute("SELECT id, text FROM memories ORDER BY id DESC LIMIT 1500").fetchall()
        scored = []
        for row in rows:
            overlap = len(q & key_stems(row["text"]))
            if overlap:
                scored.append((overlap, row["id"], row["text"]))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)  # больше совпадений, затем новее
        return [x[2] for x in scored[:limit]]

    def forget(self, query: str) -> List[str]:
        """Удаляет заметки, в которых есть не менее половины значимых слов запроса. Возвращает удалённые."""
        q = key_stems(query)
        if not q:
            return []
        need = max(1, (len(q) + 1) // 2)
        deleted: List[str] = []
        with self._connect() as con:
            rows = con.execute("SELECT id, text FROM memories").fetchall()
            for row in rows:
                if len(q & key_stems(row["text"])) >= need:
                    con.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
                    deleted.append(row["text"])
        return deleted

    def list_items(self, limit: int = 200) -> List[dict]:
        """Заметки с id и датой, новые первыми (для панели «Память»)."""
        with self._connect() as con:
            rows = con.execute("SELECT id, text, kind, created_at FROM memories ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def forget_id(self, note_id: int) -> Optional[str]:
        """Удаляет ровно одну заметку по id. Возвращает её текст или None, если такой заметки нет."""
        with self._connect() as con:
            row = con.execute("SELECT text FROM memories WHERE id = ?", (int(note_id),)).fetchone()
            if not row:
                return None
            con.execute("DELETE FROM memories WHERE id = ?", (int(note_id),))
            return row["text"]

    def forget_prefix(self, prefix: str) -> int:
        """Удаляет заметки, текст которых начинается с prefix (SQLite LIKE не знает кириллицу, поэтому в Python)."""
        if not prefix:
            return 0
        with self._connect() as con:
            ids = [r["id"] for r in con.execute("SELECT id, text FROM memories") if r["text"].startswith(prefix)]
            for note_id in ids:
                con.execute("DELETE FROM memories WHERE id = ?", (note_id,))
        return len(ids)

    def forget_all(self) -> int:
        with self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            con.execute("DELETE FROM memories")
        return n

    # ---------- журнал реплик ----------
    def add_turn(self, role: str, content: str) -> None:
        if role not in {"user", "assistant"} or not content:
            return
        with self._connect() as con:
            con.execute("INSERT INTO turns(role, content) VALUES (?, ?)", (role, str(content)[:5000]))
            con.execute(
                "DELETE FROM turns WHERE id <= (SELECT MAX(id) FROM turns) - ?", (MAX_TURNS_KEPT,)
            )

    def recent_turns(self, limit: int = 10) -> List[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT role, content FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # ---------- служебное ----------
    def stats(self) -> dict:
        with self._connect() as con:
            notes = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            turns = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        return {"notes": notes, "turns": turns}

    def clear(self) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM memories")
            con.execute("DELETE FROM turns")
