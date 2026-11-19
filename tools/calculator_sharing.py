"""Локальный каталог опубликованных сценариев MyAI."""
from __future__ import annotations
import json, os, re, sqlite3, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from tools.calculator_scenarios import CalculatorScenarios

MAX_PACKAGE_BYTES = 65536

_RATINGS_SQL = """CREATE TABLE IF NOT EXISTS ratings(
    id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id INTEGER NOT NULL,
    rating INTEGER NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE)"""


def _strict_int(value, message):
    """Целое число из int или строки из цифр. bool, float, None и мусор отклоняются."""
    if isinstance(value, bool):
        raise ValueError(message)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        return int(value.strip())
    raise ValueError(message)


def _publication_id(value):
    return _strict_int(value, "Некорректный номер публикации.")


class CalculatorSharing:
    """Каталог публикаций на этой установке MyAI; не сетевой сервер."""
    def __init__(self, db_path="calculator_sharing.sqlite3"):
        self.db_path=os.path.abspath(db_path); self._lock=threading.RLock()
        os.makedirs(os.path.dirname(self.db_path),exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS publications(
                id INTEGER PRIMARY KEY AUTOINCREMENT, scenario_name TEXT NOT NULL,
                description TEXT NOT NULL, category TEXT NOT NULL, package TEXT NOT NULL,
                published_at TEXT NOT NULL, installs INTEGER NOT NULL DEFAULT 0)""")
            db.execute(_RATINGS_SQL)
            self._migrate_ratings(db)
    @staticmethod
    def _migrate_ratings(db):
        """Старая таблица имела ключ (publication_id, created_at): две оценки в одну
        микросекунду давали IntegrityError. Переносим оценки в таблицу с собственным id."""
        columns=[r["name"] for r in db.execute("PRAGMA table_info(ratings)")]
        if "id" in columns: return
        db.execute("BEGIN")
        db.execute("ALTER TABLE ratings RENAME TO ratings_old")
        db.execute(_RATINGS_SQL)
        db.execute("""INSERT INTO ratings(publication_id,rating,created_at)
            SELECT publication_id,rating,created_at FROM ratings_old ORDER BY created_at""")
        db.execute("DROP TABLE ratings_old")
    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.db_path,timeout=5); db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try: yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()
    def publish(self, store: CalculatorScenarios, scenario_id):
        scenario=store.get(int(scenario_id))
        if not scenario: raise KeyError("Сценарий не найден.")
        # Повторная валидация перед публикацией: в каталоге нет исполняемого кода.
        name,desc,inputs,steps=store.validate(scenario["name"],scenario["description"],scenario["inputs"],scenario["steps"])
        payload={"format":"myai-calculator-scenarios","version":1,"scenarios":[{
            "name":name,"description":desc,"category":scenario.get("category","Пользовательские"),
            "inputs":inputs,"steps":steps}]}
        package=json.dumps(payload,ensure_ascii=False,separators=(",",":"))
        if len(package.encode("utf-8"))>MAX_PACKAGE_BYTES: raise ValueError("Пакет сценария слишком большой.")
        with self._lock,self._connect() as db:
            cur=db.execute("INSERT INTO publications(scenario_name,description,category,package,published_at) VALUES(?,?,?,?,?)",
                (name,desc,scenario.get("category","Пользовательские"),package,datetime.now(timezone.utc).isoformat(timespec="seconds")))
            return int(cur.lastrowid)
    def list(self, query="", category=""):
        query=str(query or "").strip().casefold(); category=str(category or "").strip()
        with self._connect() as db:
            rows=db.execute("""SELECT p.*, COALESCE(AVG(r.rating),0) average_rating, COUNT(r.rating) rating_count
                FROM publications p LEFT JOIN ratings r ON r.publication_id=p.id
                WHERE (?='' OR p.category=?) GROUP BY p.id ORDER BY p.published_at DESC, p.id DESC""",(category,category)).fetchall()
        out=[]
        for row in rows:
            x=dict(row)
            if query and query not in (x["scenario_name"]+" "+x["description"]).casefold(): continue
            x.pop("package",None); x["average_rating"]=round(float(x["average_rating"]),2)
            out.append(x)
        return out
    def package(self, publication_id):
        pid=_publication_id(publication_id)
        with self._connect() as db: row=db.execute("SELECT package FROM publications WHERE id=?",(pid,)).fetchone()
        if not row: raise KeyError("Публикация не найдена.")
        return json.loads(row["package"])
    def install(self, publication_id, store: CalculatorScenarios):
        pid=_publication_id(publication_id)
        payload=self.package(pid)
        # import_data проверяет весь пакет атомарно, включая AST и связи шагов.
        count=store.import_data(payload)
        with self._connect() as db: db.execute("UPDATE publications SET installs=installs+1 WHERE id=?",(pid,))
        return count
    def rate(self, publication_id, rating):
        pid=_publication_id(publication_id)
        rating=_strict_int(rating,"Оценка должна быть целым числом от 1 до 5.")
        if not 1<=rating<=5: raise ValueError("Оценка должна быть от 1 до 5.")
        with self._lock,self._connect() as db:
            if not db.execute("SELECT 1 FROM publications WHERE id=?",(pid,)).fetchone():
                raise KeyError("Публикация не найдена.")
            db.execute("INSERT INTO ratings(publication_id,rating,created_at) VALUES(?,?,?)",
                (pid,rating,datetime.now(timezone.utc).isoformat(timespec="microseconds")))
        return True
    def unpublish(self, publication_id):
        pid=_publication_id(publication_id)
        with self._lock,self._connect() as db:
            db.execute("DELETE FROM ratings WHERE publication_id=?",(pid,))
            cur=db.execute("DELETE FROM publications WHERE id=?",(pid,))
            return cur.rowcount>0
