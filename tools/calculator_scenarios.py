"""Безопасные последовательные сценарии вычислений."""
from __future__ import annotations
import json, os, re, sqlite3, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from tools.calculator_formulas import evaluate_formula
from tools.calculator import SafeCalculator

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,29}$")
_REF = re.compile(r"^(input|step)\.([A-Za-z][A-Za-z0-9_]{0,29})$")
CATEGORIES = ("Финансы", "Покупки", "Строительство", "Повседневные", "Пользовательские")
LIBRARY = [
    {"name":"Цена со скидкой", "description":"Рассчитывает цену после скидки.", "category":"Покупки", "inputs":{"price":"Исходная цена","discount":"Скидка, %"}, "steps":[{"id":"discountamount","name":"Размер скидки","expression":"price * discount / 100","variables":{"price":"input.price","discount":"input.discount"}},{"id":"final_price","name":"Цена со скидкой","expression":"price - discountamount","variables":{"price":"input.price","discountamount":"step.discountamount"}}]},
    {"name":"Стоимость заказа с НДС", "description":"Стоимость количества товаров с добавлением НДС.", "category":"Покупки", "inputs":{"price":"Цена за единицу","quantity":"Количество","vat":"НДС, %"}, "steps":[{"id":"subtotal","name":"Стоимость без НДС","expression":"price * quantity","variables":{"price":"input.price","quantity":"input.quantity"}},{"id":"tax","name":"Сумма НДС","expression":"subtotal * vat / 100","variables":{"subtotal":"step.subtotal","vat":"input.vat"}},{"id":"total","name":"Итого","expression":"subtotal + tax","variables":{"subtotal":"step.subtotal","tax":"step.tax"}}]},
    {"name":"Простой процент по вкладу", "description":"Оценка процентов за период без капитализации.", "category":"Финансы", "inputs":{"amount":"Сумма","rate":"Годовая ставка, %","months":"Срок, месяцев"}, "steps":[{"id":"interest","name":"Начисленные проценты","expression":"amount * rate / 100 * months / 12","variables":{"amount":"input.amount","rate":"input.rate","months":"input.months"}},{"id":"total","name":"Сумма с процентами","expression":"amount + interest","variables":{"amount":"input.amount","interest":"step.interest"}}]},
    {"name":"Площадь комнаты", "description":"Площадь прямоугольного помещения.", "category":"Строительство", "inputs":{"length":"Длина, м","width":"Ширина, м"}, "steps":[{"id":"area","name":"Площадь, м²","expression":"length * width","variables":{"length":"input.length","width":"input.width"}}]},
    {"name":"Расход топлива", "description":"Расход топлива на 100 км и стоимость поездки.", "category":"Повседневные", "inputs":{"distance":"Расстояние, км","consumption":"Расход, л/100 км","fuelprice":"Цена топлива за литр"}, "steps":[{"id":"liters","name":"Топливо, литров","expression":"distance * consumption / 100","variables":{"distance":"input.distance","consumption":"input.consumption"}},{"id":"cost","name":"Стоимость поездки","expression":"liters * fuelprice","variables":{"liters":"step.liters","fuelprice":"input.fuelprice"}}]}
]

class CalculatorScenarios:
    def __init__(self, db_path="calculator_scenarios.sqlite3"):
        self.db_path=os.path.abspath(db_path); self._lock=threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL, inputs TEXT NOT NULL, steps TEXT NOT NULL,
                created_at TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Пользовательские')""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(scenarios)").fetchall()}
            if "category" not in columns:
                db.execute("ALTER TABLE scenarios ADD COLUMN category TEXT NOT NULL DEFAULT 'Пользовательские'")
            if "version" not in columns:
                db.execute("ALTER TABLE scenarios ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            db.execute("""CREATE TABLE IF NOT EXISTS scenario_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                inputs TEXT NOT NULL,
                steps TEXT NOT NULL,
                category TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                UNIQUE(scenario_id, version)
            )""")

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.db_path, timeout=5); db.row_factory=sqlite3.Row
        try: yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()

    @staticmethod
    def validate(name, description, inputs, steps):
        name=str(name or '').strip(); description=str(description or '').strip()
        if not name or len(name)>60: raise ValueError('Название сценария: от 1 до 60 символов.')
        if len(description)>240: raise ValueError('Описание не должно превышать 240 символов.')
        if not isinstance(inputs,dict) or not 1<=len(inputs)<=20: raise ValueError('Нужно указать от 1 до 20 входных параметров.')
        for key,label in inputs.items():
            if not isinstance(key,str) or not _ID.fullmatch(key): raise ValueError('Некорректное имя входного параметра: '+str(key))
            if not isinstance(label,str) or not label.strip() or len(label)>80: raise ValueError('Для каждого параметра нужна подпись до 80 символов.')
        if not isinstance(steps,list) or not 1<=len(steps)<=20: raise ValueError('Сценарий должен содержать от 1 до 20 шагов.')
        prior=set()
        for i,step in enumerate(steps):
            if not isinstance(step,dict): raise ValueError(f'Шаг {i+1}: ожидается объект.')
            sid=step.get('id'); label=step.get('name'); expr=step.get('expression'); bindings=step.get('variables')
            if not isinstance(sid,str) or not _ID.fullmatch(sid) or sid in prior: raise ValueError(f'Шаг {i+1}: некорректный или повторяющийся id.')
            if not isinstance(label,str) or not label.strip() or len(label)>80: raise ValueError(f'Шаг {i+1}: укажите название до 80 символов.')
            if not isinstance(bindings,dict) or not bindings: raise ValueError(f'Шаг {label}: укажите привязки переменных.')
            dummy={}
            for var,source in bindings.items():
                if not isinstance(var,str) or not _ID.fullmatch(var): raise ValueError(f'Шаг {label}: некорректная переменная.')
                if isinstance(source,(int,float)) and not isinstance(source,bool): dummy[var]=source
                elif isinstance(source,str):
                    m=_REF.fullmatch(source)
                    if not m: raise ValueError(f'Шаг {label}: источник {source!r} должен иметь вид input.имя или step.id.')
                    kind,ref=m.groups()
                    if kind=='input' and ref not in inputs: raise ValueError(f'Шаг {label}: неизвестный входной параметр {ref}.')
                    if kind=='step' and ref not in prior: raise ValueError(f'Шаг {label}: ссылка на шаг {ref} должна указывать на предыдущий шаг.')
                    dummy[var]=1
                else: raise ValueError(f'Шаг {label}: недопустимый источник переменной {var}.')
            evaluate_formula(expr,dummy)
            prior.add(sid)
        return name,description,inputs,steps

    def save(self,name,description,inputs,steps,category="Пользовательские"):
        name,description,inputs,steps=self.validate(name,description,inputs,steps)
        category = str(category or "Пользовательские").strip()
        if category not in CATEGORIES: raise ValueError("Неизвестная категория сценария.")
        with self._lock,self._connect() as db:
            try:
                cur=db.execute('INSERT INTO scenarios(name,description,inputs,steps,created_at,category) VALUES(?,?,?,?,?,?)',
                    (name,description,json.dumps(inputs,ensure_ascii=False),json.dumps(steps,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds'),category))
            except sqlite3.IntegrityError: raise ValueError('Сценарий с таким названием уже существует.') from None
            return int(cur.lastrowid)

    @staticmethod
    def _decode(row):
        if not row:return None
        x=dict(row); x['inputs']=json.loads(x['inputs']); x['steps']=json.loads(x['steps']); return x
    def list(self, category=None):
        with self._connect() as db:
            if category and category in CATEGORIES:
                rows=db.execute('SELECT * FROM scenarios WHERE category=? ORDER BY name COLLATE NOCASE',(category,)).fetchall()
            else:
                rows=db.execute('SELECT * FROM scenarios ORDER BY category,name COLLATE NOCASE').fetchall()
        return [self._decode(r) for r in rows]
    def get(self,scenario_id):
        with self._connect() as db: row=db.execute('SELECT * FROM scenarios WHERE id=?',(int(scenario_id),)).fetchone()
        return self._decode(row)
    def update(self, scenario_id, name, description, inputs, steps, category="Пользовательские"):
        name,description,inputs,steps=self.validate(name,description,inputs,steps)
        category=str(category or "Пользовательские").strip()
        if category not in CATEGORIES: raise ValueError("Неизвестная категория сценария.")
        scenario_id=int(scenario_id)
        with self._lock,self._connect() as db:
            current=db.execute("SELECT * FROM scenarios WHERE id=?",(scenario_id,)).fetchone()
            if not current: raise KeyError("Сценарий не найден.")
            duplicate=db.execute("SELECT id FROM scenarios WHERE name=? COLLATE NOCASE AND id<>?",(name,scenario_id)).fetchone()
            if duplicate: raise ValueError("Сценарий с таким названием уже существует.")
            next_version=int(current["version"] or 1)+1
            # Сохраняем предыдущую версию до изменения записи.
            db.execute("""INSERT OR IGNORE INTO scenario_versions
                (scenario_id,version,name,description,inputs,steps,category,saved_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (scenario_id,int(current["version"] or 1),current["name"],current["description"],
                 current["inputs"],current["steps"],current["category"],
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
            db.execute("""UPDATE scenarios SET name=?,description=?,inputs=?,steps=?,category=?,version=?
                WHERE id=?""",(name,description,json.dumps(inputs,ensure_ascii=False),
                json.dumps(steps,ensure_ascii=False),category,next_version,scenario_id))
        return next_version

    def duplicate(self, scenario_id, new_name=None):
        source=self.get(scenario_id)
        if not source: raise KeyError("Сценарий не найден.")
        base=str(new_name or (source["name"]+" — копия")).strip()
        candidate=base; suffix=2
        while True:
            try:
                return self.save(candidate,source["description"],source["inputs"],source["steps"],source.get("category","Пользовательские"))
            except ValueError as exc:
                if "уже существует" not in str(exc): raise
                candidate=f"{base} ({suffix})"; suffix+=1
                if suffix>1000: raise ValueError("Не удалось подобрать уникальное название копии.")

    def versions(self, scenario_id):
        scenario_id=int(scenario_id)
        with self._connect() as db:
            rows=db.execute("SELECT version,name,description,category,saved_at FROM scenario_versions WHERE scenario_id=? ORDER BY version DESC",(scenario_id,)).fetchall()
            current=db.execute("SELECT version,name,description,category,created_at FROM scenarios WHERE id=?",(scenario_id,)).fetchone()
        if not current: raise KeyError("Сценарий не найден.")
        return [{"version":int(current["version"] or 1),"name":current["name"],"description":current["description"],"category":current["category"],"saved_at":current["created_at"],"current":True}] + [dict(r, current=False) for r in rows]

    def restore_version(self, scenario_id, version):
        scenario_id=int(scenario_id); version=int(version)
        with self._lock,self._connect() as db:
            current=db.execute("SELECT * FROM scenarios WHERE id=?",(scenario_id,)).fetchone()
            if not current: raise KeyError("Сценарий не найден.")
            if int(current["version"] or 1)==version: return int(current["version"] or 1)
            old=db.execute("SELECT * FROM scenario_versions WHERE scenario_id=? AND version=?",(scenario_id,version)).fetchone()
            if not old: raise KeyError("Версия не найдена.")
            duplicate=db.execute("SELECT id FROM scenarios WHERE name=? COLLATE NOCASE AND id<>?",(old["name"],scenario_id)).fetchone()
            if duplicate: raise ValueError("Нельзя восстановить версию: её название уже занято другим сценарием.")
            current_version=int(current["version"] or 1)
            db.execute("""INSERT OR IGNORE INTO scenario_versions
                (scenario_id,version,name,description,inputs,steps,category,saved_at)
                VALUES(?,?,?,?,?,?,?,?)""",(scenario_id,current_version,current["name"],current["description"],current["inputs"],current["steps"],current["category"],datetime.now(timezone.utc).isoformat(timespec='seconds')))
            next_version=current_version+1
            db.execute("""UPDATE scenarios SET name=?,description=?,inputs=?,steps=?,category=?,version=? WHERE id=?""",
                (old["name"],old["description"],old["inputs"],old["steps"],old["category"],next_version,scenario_id))
        return next_version

    def delete(self,scenario_id):
        scenario_id=int(scenario_id)
        with self._lock,self._connect() as db:
            cur=db.execute('DELETE FROM scenarios WHERE id=?',(scenario_id,))
            if cur.rowcount: db.execute('DELETE FROM scenario_versions WHERE scenario_id=?',(scenario_id,))
            return cur.rowcount>0

    def export_data(self, scenario_ids=None):
        selected = self.list()
        if scenario_ids is not None:
            wanted = {int(x) for x in scenario_ids}
            selected = [x for x in selected if x["id"] in wanted]
            if len(selected) != len(wanted): raise ValueError("Один или несколько сценариев не найдены.")
        return {"format":"myai-calculator-scenarios", "version":1, "scenarios":[
            {"name":x["name"],"description":x["description"],"category":x.get("category","Пользовательские"),"inputs":x["inputs"],"steps":x["steps"]} for x in selected]}

    def import_data(self, payload):
        if not isinstance(payload,dict) or payload.get("format") != "myai-calculator-scenarios" or payload.get("version") != 1:
            raise ValueError("Неподдерживаемый формат файла сценариев.")
        items=payload.get("scenarios")
        if not isinstance(items,list) or not 1 <= len(items) <= 100:
            raise ValueError("Файл должен содержать от 1 до 100 сценариев.")
        prepared=[]; names=set()
        for item in items:
            if not isinstance(item,dict): raise ValueError("Некорректная запись сценария.")
            name,description,inputs,steps=self.validate(item.get("name"),item.get("description",""),item.get("inputs"),item.get("steps"))
            category=str(item.get("category") or "Пользовательские")
            if category not in CATEGORIES: raise ValueError("Неизвестная категория сценария: "+category)
            key=name.casefold()
            if key in names: raise ValueError("В файле есть повторяющиеся названия сценариев.")
            names.add(key); prepared.append((name,description,inputs,steps,category))
        with self._lock,self._connect() as db:
            existing={r[0].casefold() for r in db.execute("SELECT name FROM scenarios").fetchall()}
            duplicate=next((x[0] for x in prepared if x[0].casefold() in existing),None)
            if duplicate: raise ValueError("Сценарий уже существует: "+duplicate)
            now=datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.executemany("INSERT INTO scenarios(name,description,inputs,steps,created_at,category) VALUES(?,?,?,?,?,?)",[
                (n,d,json.dumps(i,ensure_ascii=False),json.dumps(st,ensure_ascii=False),now,c) for n,d,i,st,c in prepared])
        return len(prepared)

    def run(self,scenario_id,values):
        scenario=self.get(scenario_id)
        if not scenario: raise KeyError('Сценарий не найден.')
        if not isinstance(values,dict) or set(values)!=set(scenario['inputs']):
            missing=set(scenario['inputs'])-(set(values) if isinstance(values,dict) else set())
            extra=(set(values) if isinstance(values,dict) else set())-set(scenario['inputs'])
            details=[]
            if missing: details.append('не заданы: '+', '.join(sorted(missing)))
            if extra: details.append('лишние: '+', '.join(sorted(extra)))
            raise ValueError('Проверьте входные параметры ('+'; '.join(details)+').')
        normalized={}
        for k,v in values.items():
            try: n=Decimal(str(v).replace(',','.'))
            except Exception: raise ValueError(f'Параметр {scenario["inputs"][k]} должен быть числом.') from None
            if not n.is_finite() or abs(n)>Decimal('1e100'): raise ValueError(f'Параметр {scenario["inputs"][k]} вне допустимого диапазона.')
            normalized[k]=n
        results={}; output=[]
        for step in scenario['steps']:
            bindings={}
            for var,source in step['variables'].items():
                if isinstance(source,(int,float)) and not isinstance(source,bool): bindings[var]=source
                else:
                    kind,ref=_REF.fullmatch(source).groups()
                    bindings[var]=normalized[ref] if kind=='input' else results[ref]
            value=evaluate_formula(step['expression'],bindings); results[step['id']]=value
            output.append({'id':step['id'],'name':step['name'],'expression':step['expression'],'result':SafeCalculator.format_number(value)})
        return scenario,output
