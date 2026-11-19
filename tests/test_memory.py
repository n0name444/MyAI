import os
import tempfile
import unittest

from memory import Memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = Memory(os.path.join(self.tmp.name, "m.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_morphology(self):
        self.m.add("у меня есть собака по имени Рекс")
        self.assertTrue(self.m.search("расскажи про мою собаку"))
        self.assertTrue(self.m.search("собаку"))

    def test_stopwords_do_not_match(self):
        self.m.add("я люблю игры")
        self.assertEqual(self.m.search("что как меня"), [])

    def test_dedupe_cyrillic_case(self):
        self.assertTrue(self.m.add("я люблю игры"))
        self.assertFalse(self.m.add("Я ЛЮБЛЮ ИГРЫ"))

    def test_list_forget(self):
        self.m.add("я люблю игры"); self.m.add("мой кот Барсик")
        self.assertEqual(len(self.m.list_notes()), 2)
        self.assertEqual(self.m.forget("про игры"), ["я люблю игры"])
        self.assertEqual(self.m.list_notes(), ["мой кот Барсик"])
        self.assertEqual(self.m.forget_all(), 1)

    def test_list_items_and_forget_id(self):
        self.m.add("первая"); self.m.add("вторая")
        items = self.m.list_items()
        self.assertEqual([x["text"] for x in items], ["вторая", "первая"])      # новые первыми
        self.assertEqual(set(items[0]), {"id", "text", "kind", "created_at"})
        self.assertEqual(self.m.forget_id(items[1]["id"]), "первая")
        self.assertIsNone(self.m.forget_id(items[1]["id"]))                    # повторно: уже нет
        self.assertEqual([x["text"] for x in self.m.list_items()], ["вторая"])
        self.assertEqual(len(self.m.list_items(limit=0)), 0)

    def test_forget_id_removes_exactly_one_note(self):
        self.m.add("я люблю игры"); self.m.add("я люблю игры про космос")
        target = next(x for x in self.m.list_items() if x["text"] == "я люблю игры")
        self.m.forget_id(target["id"])
        self.assertEqual([x["text"] for x in self.m.list_items()], ["я люблю игры про космос"])
        # для сравнения: забывание по словам удаляет всё похожее
        self.m.add("я люблю игры"); self.assertEqual(len(self.m.forget("игры")), 2)

    def test_forget_prefix_with_cyrillic(self):
        self.m.add("Пользователя зовут Иван"); self.m.add("я люблю игры")
        self.assertEqual(self.m.forget_prefix("Пользователя зовут "), 1)
        self.assertEqual([x["text"] for x in self.m.list_items()], ["я люблю игры"])
        self.assertEqual(self.m.forget_prefix(""), 0)
        self.assertEqual(self.m.forget_prefix("нет такого"), 0)

    def test_turn_pruning(self):
        import memory
        old = memory.MAX_TURNS_KEPT
        memory.MAX_TURNS_KEPT = 5
        try:
            for i in range(12):
                self.m.add_turn("user", f"msg {i}")
            self.assertLessEqual(self.m.stats()["turns"], 6)
        finally:
            memory.MAX_TURNS_KEPT = old


if __name__ == "__main__":
    unittest.main()
