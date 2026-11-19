import os, sqlite3, tempfile, unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest import mock
from tools.calculator_scenarios import CalculatorScenarios
from tools.calculator_sharing import CalculatorSharing

class CalculatorSharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=CalculatorScenarios(os.path.join(self.tmp.name,"scenarios.db"))
        self.sharing=CalculatorSharing(os.path.join(self.tmp.name,"sharing.db"))
        self.sid=self.store.save("Test calc","Demo",{"price":"Price","qty":"Quantity"},
            [{"id":"total","name":"Total","expression":"price * qty",
              "variables":{"price":"input.price","qty":"input.qty"}}],"Покупки")
    def tearDown(self): self.tmp.cleanup()
    def test_publish_list_and_install(self):
        pid=self.sharing.publish(self.store,self.sid)
        listed=self.sharing.list("test","Покупки")
        self.assertEqual(listed[0]["id"],pid)
        other=CalculatorScenarios(os.path.join(self.tmp.name,"other.db"))
        self.assertEqual(self.sharing.install(pid,other),1)
        scenario=other.list()[0]
        _,result=other.run(scenario["id"],{"price":12,"qty":3})
        self.assertEqual(result[-1]["result"],"36")
        self.assertEqual(self.sharing.list()[0]["installs"],1)
    def test_invalid_scenario_cannot_publish(self):
        self.store.delete(self.sid)
        with self.assertRaises(KeyError): self.sharing.publish(self.store,self.sid)
    def test_rating_bounds_and_average(self):
        pid=self.sharing.publish(self.store,self.sid)
        self.sharing.rate(pid,5); self.sharing.rate(pid,3)
        item=self.sharing.list()[0]
        self.assertEqual(item["rating_count"],2); self.assertEqual(item["average_rating"],4)
        with self.assertRaises(ValueError): self.sharing.rate(pid,6)
    def test_unpublish_removes_catalog_entry(self):
        pid=self.sharing.publish(self.store,self.sid)
        self.assertTrue(self.sharing.unpublish(pid))
        self.assertEqual(self.sharing.list(),[])
        with self.assertRaises(KeyError): self.sharing.package(pid)
    def test_import_validation_remains_atomic(self):
        pid=self.sharing.publish(self.store,self.sid)
        target=CalculatorScenarios(os.path.join(self.tmp.name,"target.db"))
        self.sharing.install(pid,target)
        with self.assertRaisesRegex(ValueError,"уже существует"): self.sharing.install(pid,target)
        self.assertEqual(len(target.list()),1)

    def test_rating_rejects_non_integers(self):
        pid=self.sharing.publish(self.store,self.sid)
        for bad in (True,False,4.9,4.0,None,"abc","","3.5","-1",0,6,[5]):
            with self.assertRaises(ValueError,msg=repr(bad)): self.sharing.rate(pid,bad)
        self.assertEqual(self.sharing.list()[0]["rating_count"],0)
        self.sharing.rate(pid,"4"); self.sharing.rate(pid," 5 "); self.sharing.rate(pid,3)
        item=self.sharing.list()[0]
        self.assertEqual(item["rating_count"],3); self.assertEqual(item["average_rating"],4)
    def test_publication_id_is_validated(self):
        for bad in (None,True,"x",1.5,-0.0):
            with self.assertRaises(ValueError,msg=repr(bad)): self.sharing.rate(bad,5)
            with self.assertRaises(ValueError,msg=repr(bad)): self.sharing.unpublish(bad)
            with self.assertRaises(ValueError,msg=repr(bad)): self.sharing.package(bad)
    def test_two_ratings_in_same_microsecond_do_not_collide(self):
        class FixedDT:
            @staticmethod
            def now(tz=None): return datetime(2026,1,1,tzinfo=timezone.utc)
        pid=self.sharing.publish(self.store,self.sid)
        with mock.patch("tools.calculator_sharing.datetime",FixedDT):
            self.sharing.rate(pid,5); self.sharing.rate(pid,4)
        self.assertEqual(self.sharing.list()[0]["rating_count"],2)
    def test_old_ratings_table_is_migrated_without_losing_data(self):
        path=os.path.join(self.tmp.name,"old.db")
        db=sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE publications(id INTEGER PRIMARY KEY AUTOINCREMENT, scenario_name TEXT NOT NULL,
                description TEXT NOT NULL, category TEXT NOT NULL, package TEXT NOT NULL,
                published_at TEXT NOT NULL, installs INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE ratings(publication_id INTEGER NOT NULL, rating INTEGER NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(publication_id, created_at),
                FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE);
            INSERT INTO publications(scenario_name,description,category,package,published_at)
                VALUES('Old','d','Покупки','{}','2026-01-01T00:00:00+00:00');
            INSERT INTO ratings VALUES(1,5,'2026-01-02T00:00:00'),(1,3,'2026-01-03T00:00:00');""")
        db.commit(); db.close()
        sharing=CalculatorSharing(path)
        item=sharing.list()[0]
        self.assertEqual((item["rating_count"],item["average_rating"]),(2,4))
        sharing.rate(1,4)
        self.assertEqual(sharing.list()[0]["rating_count"],3)
        CalculatorSharing(path)  # повторное открытие уже мигрированной базы
        self.assertEqual(sharing.list()[0]["rating_count"],3)
    def test_unpublish_removes_ratings(self):
        pid=self.sharing.publish(self.store,self.sid); self.sharing.rate(pid,5)
        self.assertTrue(self.sharing.unpublish(pid)); self.assertFalse(self.sharing.unpublish(pid))
        with closing(sqlite3.connect(self.sharing.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ratings").fetchone()[0],0)

if __name__=="__main__": unittest.main()
