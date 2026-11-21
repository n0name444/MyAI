import ctypes
import unittest
from unittest import mock

from tools import computer_control as cc
from tools.computer_control import ComputerControl


class UrlTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(ComputerControl._url("ютуб"), "https://www.youtube.com/")
        self.assertEqual(ComputerControl._url("example.com"), "https://example.com")
        self.assertEqual(ComputerControl._url("http://example.com:8080/a?b=1"), "http://example.com:8080/a?b=1")

    def test_rejected(self):
        for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "ftp://example.com", "погоды", "ms-settings:", "data:text/html,hi"):
            with self.assertRaises(ValueError, msg=bad):
                ComputerControl._url(bad)


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.cc = ComputerControl()
        p = mock.patch.object(cc, "IS_WINDOWS", True)
        p.start(); self.addCleanup(p.stop)

    def test_cmd_is_not_in_whitelist(self):
        self.assertNotIn("cmd", cc.APP_BY_NAME)
        self.assertIn("безопасном списке", self.cc.execute("открой cmd"))
        self.assertIn("безопасного списка", self.cc.execute("закрой cmd"))

    def test_type_strips_trigger_words(self):
        with mock.patch.object(ComputerControl, "type_text", return_value="ok") as t:
            self.cc.execute("напиши в окне Привет, Мир")
            t.assert_called_once_with("Привет, Мир")

    def test_type_with_colon_does_not_type_the_trigger(self):
        for phrase in ("напиши в окне: привет мир", "напиши в окне:привет мир", "введи в окне — привет мир"):
            with self.subTest(phrase=phrase), mock.patch.object(ComputerControl, "type_text", return_value="ok") as t:
                self.cc.execute(phrase)
                t.assert_called_once_with("привет мир")

    def test_launch_uses_resolved_path(self):
        with mock.patch.object(ComputerControl, "_resolve", return_value=r"C:\x\chrome.exe"), \
             mock.patch.object(cc.subprocess, "Popen") as popen:
            self.assertIn("Открыл", self.cc.execute("открой хром"))
            popen.assert_called_once()
            self.assertEqual(popen.call_args[0][0], [r"C:\x\chrome.exe"])
            self.assertFalse(popen.call_args[1].get("shell"))

    def test_launch_missing_app(self):
        with mock.patch.object(ComputerControl, "_resolve", return_value=None):
            self.assertIn("не найден", self.cc.execute("открой steam"))

    def test_close_reports_not_running(self):
        res = mock.Mock(returncode=128, stdout="", stderr="not found")
        with mock.patch.object(cc.subprocess, "run", return_value=res):
            self.assertIn("не запущен", self.cc.execute("закрой калькулятор"))

    def test_close_graceful_vs_force(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="INFO: No tasks" if cmd[0] == "tasklist" else "", stderr="")

        with mock.patch.object(cc.subprocess, "run", side_effect=fake_run), mock.patch.object(cc.time, "sleep"):
            self.assertIn("Закрыл Блокнот.", self.cc.execute("закрой блокнот"))
            self.assertNotIn("/F", calls[0])
            calls.clear()
            self.assertIn("принудительно", self.cc.execute("закрой принудительно блокнот"))
            self.assertIn("/F", calls[0])

    def test_close_still_running_is_honest(self):
        def fake_run(cmd, **kw):
            return mock.Mock(returncode=0, stdout='"notepad.exe","1"' if cmd[0] == "tasklist" else "", stderr="")
        with mock.patch.object(cc.subprocess, "run", side_effect=fake_run), mock.patch.object(cc.time, "sleep"):
            self.assertIn("ещё открыт", self.cc.execute("закрой блокнот"))

    def test_explorer_not_closable(self):
        self.assertIn("не закрываю", self.cc.execute("закрой проводник"))

    def test_keys(self):
        self.assertEqual(ComputerControl.parse_key("энтер"), ("enter",))
        self.assertEqual(ComputerControl.parse_key("Ctrl+C"), ("ctrl", "c"))
        with self.assertRaises(ValueError):
            ComputerControl.parse_key("alt+f4")

    def test_focus_refuses_terminal_and_self(self):
        fake = mock.Mock()
        with mock.patch.object(cc, "pyautogui", fake), mock.patch.object(cc.time, "sleep"):
            with mock.patch.object(cc, "_foreground", return_value=(1, "Блокнот", "cmd.exe")):
                with self.assertRaises(ValueError):
                    self.cc._focus_target()
            seq = iter([(1, "MyAI — локальный AI - Chrome", "chrome.exe"), (1, "MyAI — локальный AI - Chrome", "chrome.exe")])
            with mock.patch.object(cc, "_foreground", side_effect=lambda: next(seq)):
                with self.assertRaises(ValueError):
                    self.cc._focus_target()
            fake.hotkey.assert_called_with("alt", "tab")

    def test_focus_switches_from_myai_to_notepad(self):
        fake = mock.Mock()
        seq = iter([(1, "MyAI — локальный AI", "chrome.exe"), (2, "Безымянный — Блокнот", "notepad.exe")])
        with mock.patch.object(cc, "pyautogui", fake), mock.patch.object(cc.time, "sleep"), \
             mock.patch.object(cc, "_foreground", side_effect=lambda: next(seq)):
            self.cc._focus_target()
        fake.hotkey.assert_called_once_with("alt", "tab")

    def test_unicode_units_and_struct_size(self):
        self.assertEqual(cc._utf16_units("Пр"), (0x041F, 0x0440))
        self.assertEqual(len(cc._utf16_units("😀")), 2)   # суррогатная пара
        INPUT, _ = cc._make_input_types()
        if ctypes.sizeof(ctypes.c_size_t) == 8:
            self.assertEqual(ctypes.sizeof(INPUT), 40)    # размер, который ждёт SendInput на x64

if __name__ == "__main__":
    unittest.main()



class TestInputSafety(unittest.TestCase):
    def test_terminals_and_ssh_blocked(self):
        for exe in ("mintty.exe", "putty.exe", "kitty.exe", "wezterm-gui.exe",
                    "alacritty.exe", "tabby.exe", "mstsc.exe", "totalcmd64.exe", "wsl.exe"):
            self.assertTrue(cc._input_blocked(exe), exe)

    def test_ide_override(self):
        with mock.patch.object(cc, "_ALLOW_IDE_INPUT", False):
            self.assertTrue(cc._input_blocked("code.exe"))
        with mock.patch.object(cc, "_ALLOW_IDE_INPUT", True):
            self.assertFalse(cc._input_blocked("code.exe"))

    def test_regular_apps_allowed(self):
        for exe in ("notepad.exe", "chrome.exe", "winword.exe", "telegram.exe"):
            self.assertFalse(cc._input_blocked(exe), exe)

    def test_self_window_is_browser_tab_not_folder(self):
        self.assertTrue(cc._is_self_window("MyAI — Google Chrome", "chrome.exe"))
        self.assertFalse(cc._is_self_window("MyAI", "explorer.exe"))

    def test_type_text_aborts_when_focus_changes(self):
        tool = cc.ComputerControl()
        notepad, terminal = (7, "Безымянный — Блокнот", "notepad.exe"), (8, "cmd", "cmd.exe")
        def fake_send(text, guard=None):
            guard()  # имитируем проверку перед первым блоком
        with mock.patch.object(cc, "IS_WINDOWS", True), \
             mock.patch.object(cc, "pyautogui", mock.Mock()), \
             mock.patch.object(cc, "_foreground", side_effect=[notepad, terminal]), \
             mock.patch.object(cc, "_send_unicode", fake_send):
            with self.assertRaises(ValueError):
                tool.type_text("привет")

