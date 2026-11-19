import os, tempfile, unittest
from tools.calculator_scenarios import CalculatorScenarios, LIBRARY, CATEGORIES

class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=CalculatorScenarios(os.path.join(self.tmp.name,'scenarios.db'))
    def sample(self):
        return ('Order total','Стоимость + НДС',
          {'price':'Цена за единицу','quantity':'Количество','vat':'НДС, %'},
          [
            {'id':'subtotal','name':'Стоимость','expression':'price * quantity','variables':{'price':'input.price','quantity':'input.quantity'}},
            {'id':'tax','name':'НДС','expression':'subtotal * vat / 100','variables':{'subtotal':'step.subtotal','vat':'input.vat'}},
            {'id':'total','name':'Итого','expression':'subtotal + tax','variables':{'subtotal':'step.subtotal','tax':'step.tax'}}
          ])
    def test_save_run_and_delete(self):
        sid=self.store.save(*self.sample())
        sc,results=self.store.run(sid,{'price':100,'quantity':3,'vat':20})
        self.assertEqual(sc['name'],'Order total')
        self.assertEqual([x['result'] for x in results],['300','60','360'])
        self.assertTrue(self.store.delete(sid)); self.assertFalse(self.store.delete(sid))
    def test_missing_and_extra_inputs_rejected(self):
        sid=self.store.save(*self.sample())
        with self.assertRaisesRegex(ValueError,'входные параметры'):
            self.store.run(sid,{'price':100})
        with self.assertRaisesRegex(ValueError,'должен быть числом'):
            self.store.run(sid,{'price':'x','quantity':2,'vat':20})
    def test_forward_reference_rejected(self):
        name,desc,inputs,steps=self.sample()
        steps[0]['variables']={'price':'step.tax','quantity':'input.quantity'}
        with self.assertRaisesRegex(ValueError,'предыдущий шаг'):
            self.store.save(name,desc,inputs,steps)
    def test_unsafe_formula_rejected(self):
        name,desc,inputs,steps=self.sample(); steps[0]['expression']='__import__("os")'
        with self.assertRaises(ValueError): self.store.save(name,desc,inputs,steps)
    def test_existing_database_gets_default_category(self):
        import sqlite3
        legacy=os.path.join(self.tmp.name,"legacy.db")
        db=sqlite3.connect(legacy)
        db.execute("CREATE TABLE scenarios (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE, description TEXT NOT NULL, inputs TEXT NOT NULL, steps TEXT NOT NULL, created_at TEXT NOT NULL)")
        name,desc,inputs,steps=self.sample()
        import json
        db.execute("INSERT INTO scenarios(name,description,inputs,steps,created_at) VALUES(?,?,?,?,?)",(name,desc,json.dumps(inputs),json.dumps(steps),"2026-01-01T00:00:00+00:00"))
        db.commit(); db.close()
        migrated=CalculatorScenarios(legacy)
        self.assertEqual(migrated.list()[0]["category"],"Пользовательские")

    def test_categories_and_filtering(self):
        name,desc,inputs,steps=self.sample()
        sid=self.store.save(name,desc,inputs,steps,"Покупки")
        self.assertEqual(self.store.get(sid)["category"],"Покупки")
        self.assertEqual(len(self.store.list("Покупки")),1)
        self.assertEqual(self.store.list("Финансы"),[])
        with self.assertRaisesRegex(ValueError,"категория"):
            self.store.save("Other",desc,inputs,steps,"Неизвестно")

    def test_export_import_roundtrip(self):
        self.store.save(*self.sample(),category="Покупки")
        payload=self.store.export_data()
        other=CalculatorScenarios(os.path.join(self.tmp.name,"other.db"))
        self.assertEqual(other.import_data(payload),1)
        imported=other.list()[0]
        self.assertEqual(imported["category"],"Покупки")
        _,results=other.run(imported["id"],{"price":100,"quantity":3,"vat":20})
        self.assertEqual(results[-1]["result"],"360")

    def test_import_is_atomic_on_duplicate(self):
        self.store.save(*self.sample())
        with self.assertRaisesRegex(ValueError,"уже существует"):
            self.store.import_data(self.store.export_data())
        self.assertEqual(len(self.store.list()),1)

    def test_invalid_import_format_rejected(self):
        with self.assertRaisesRegex(ValueError,"формат"):
            self.store.import_data({"format":"other","version":1,"scenarios":[]})

    def test_library_templates_validate_and_execute(self):
        for template in LIBRARY:
            self.store.validate(template["name"],template["description"],template["inputs"],template["steps"])
            sid=self.store.save(template["name"],template["description"],template["inputs"],template["steps"],template["category"])
            values={key:2 for key in template["inputs"]}
            _,results=self.store.run(sid,values)
            self.assertTrue(results)
        self.assertEqual(len(CATEGORIES),5)

    def test_duplicate_name_case_insensitive(self):
        self.store.save(*self.sample())
        name,desc,inputs,steps=self.sample()
        with self.assertRaisesRegex(ValueError,'уже существует'):
            self.store.save(name.upper(),desc,inputs,steps)

    def test_update_creates_version_and_updates_scenario(self):
        sid=self.store.save(*self.sample())
        name,desc,inputs,steps=self.sample(); steps[0]["expression"]="price + quantity"
        version=self.store.update(sid,name,desc,inputs,steps,"Финансы")
        self.assertEqual(version,2)
        current=self.store.get(sid)
        self.assertEqual(current["version"],2)
        self.assertEqual(current["category"],"Финансы")
        self.assertEqual(current["steps"][0]["expression"],"price + quantity")
        versions=self.store.versions(sid)
        self.assertEqual([v["version"] for v in versions],[2,1])
        self.assertTrue(versions[0]["current"])

    def test_duplicate_creates_independent_copy(self):
        sid=self.store.save(*self.sample())
        copy_id=self.store.duplicate(sid)
        self.assertNotEqual(sid,copy_id)
        self.assertEqual(self.store.get(copy_id)["name"],"Order total — копия")
        self.assertEqual(len(self.store.list()),2)

    def test_restore_previous_version_as_new_version(self):
        sid=self.store.save(*self.sample())
        name,desc,inputs,steps=self.sample(); steps[0]["expression"]="price + quantity"
        self.store.update(sid,name,desc,inputs,steps)
        version=self.store.restore_version(sid,1)
        self.assertEqual(version,3)
        current=self.store.get(sid)
        self.assertEqual(current["steps"][0]["expression"],"price * quantity")
        self.assertEqual([v["version"] for v in self.store.versions(sid)],[3,2,1])

    def test_update_rejects_duplicate_name_and_preserves_current(self):
        sid=self.store.save(*self.sample())
        name,desc,inputs,steps=self.sample(); self.store.save("Other",desc,inputs,steps)
        with self.assertRaisesRegex(ValueError,"уже существует"):
            self.store.update(sid,"Other",desc,inputs,steps)
        self.assertEqual(self.store.get(sid)["version"],1)

if __name__=='__main__': unittest.main()
