"""Безопасное управление Windows.

Что умеет: открыть/закрыть приложения из белого списка, открыть сайт (только http/https),
ввести текст и нажать несколько безопасных клавиш в ПРЕДЫДУЩЕМ окне.

Чего не делает: не запускает cmd/PowerShell, не вводит текст в терминалы и в окно MyAI,
не принимает произвольные команды и пути.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import struct
import subprocess
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from textutil import normalize_command

log = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

try:
    import pyautogui

    pyautogui.FAILSAFE = True   # мышь в угол экрана — аварийная остановка
    pyautogui.PAUSE = 0.05
except Exception:  # pyautogui не обязателен для запуска приложений и сайтов
    pyautogui = None

MAX_TYPE_CHARS = 500


@dataclass(frozen=True)
class AppSpec:
    key: str
    title: str
    names: Tuple[str, ...]            # как пользователь может назвать приложение
    exe: str                          # имя exe для поиска в PATH / App Paths
    paths: Tuple[str, ...] = ()       # типовые места установки (с переменными среды)
    args: Tuple[str, ...] = ()
    processes: Tuple[str, ...] = ()   # образы процессов для закрытия
    closable: bool = True


APPS: Tuple[AppSpec, ...] = (
    AppSpec("calc", "Калькулятор", ("калькулятор", "calculator", "calc"), "calc.exe",
            processes=("CalculatorApp.exe", "Calculator.exe", "win32calc.exe", "calc.exe")),
    AppSpec("notepad", "Блокнот", ("блокнот", "notepad"), "notepad.exe", processes=("notepad.exe",)),
    # Проводник управляет рабочим столом и панелью задач — открываем, но не закрываем.
    AppSpec("explorer", "Проводник", ("проводник", "explorer"), "explorer.exe", closable=False),
    AppSpec("paint", "Paint", ("paint", "паинт", "рисование"), "mspaint.exe",
            processes=("mspaint.exe", "PaintStudio.View.exe")),
    AppSpec("taskmgr", "Диспетчер задач", ("диспетчер задач", "task manager", "taskmgr"), "taskmgr.exe",
            processes=("Taskmgr.exe",)),
    AppSpec("chrome", "Google Chrome", ("гугл хром", "google chrome", "chrome", "хром"), "chrome.exe",
            paths=(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                   r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                   r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            processes=("chrome.exe",)),
    AppSpec("discord", "Discord", ("дискорд", "discord"), "Discord.exe",
            paths=(r"%LocalAppData%\Discord\Update.exe",), args=("--processStart", "Discord.exe"),
            processes=("Discord.exe",)),
    AppSpec("telegram", "Telegram", ("телеграм", "telegram"), "Telegram.exe",
            paths=(r"%AppData%\Telegram Desktop\Telegram.exe", r"%ProgramFiles%\Telegram Desktop\Telegram.exe"),
            processes=("Telegram.exe",)),
    AppSpec("steam", "Steam", ("стим", "steam"), "steam.exe",
            paths=(r"%ProgramFiles(x86)%\Steam\steam.exe", r"%ProgramFiles%\Steam\steam.exe"),
            processes=("steam.exe",)),
    AppSpec("vlc", "VLC", ("vlc", "влц"), "vlc.exe",
            paths=(r"%ProgramFiles%\VideoLAN\VLC\vlc.exe", r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"),
            processes=("vlc.exe",)),
)

APP_BY_NAME: Dict[str, AppSpec] = {name: spec for spec in APPS for name in spec.names}
APP_NAMES: Tuple[str, ...] = tuple(sorted(APP_BY_NAME, key=len, reverse=True))

URL_ALIASES: Dict[str, str] = {
    "ютуб": "https://www.youtube.com/", "youtube": "https://www.youtube.com/",
    "гугл": "https://www.google.com/", "google": "https://www.google.com/",
    "вк": "https://vk.com/", "вконтакте": "https://vk.com/",
}
URL_ALIAS_NAMES: Tuple[str, ...] = tuple(sorted(URL_ALIASES, key=len, reverse=True))

KEY_ALIASES: Dict[str, str] = {
    "enter": "enter", "энтер": "enter", "ввод": "enter", "интер": "enter",
    "esc": "esc", "escape": "esc", "эскейп": "esc", "эск": "esc",
    "tab": "tab", "таб": "tab",
    "space": "space", "пробел": "space",
    "backspace": "backspace", "бэкспейс": "backspace", "бекспейс": "backspace",
    "delete": "delete", "делит": "delete",
    "up": "up", "вверх": "up", "down": "down", "вниз": "down",
    "left": "left", "влево": "left", "right": "right", "вправо": "right",
    "home": "home", "хоум": "home", "end": "end", "энд": "end",
}
HOTKEY_LETTERS = "acvxzysf"   # Ctrl+A/C/V/X/Z/Y/S/F
_HOTKEY_RE = re.compile(rf"(?:ctrl|control|контрол)\s*\+?\s*([{HOTKEY_LETTERS}])")
KEY_NAMES: Tuple[str, ...] = tuple(sorted(KEY_ALIASES, key=len, reverse=True))
# Регулярное выражение для роутера: клавиша целиком.
KEY_REGEX = "(?:" + "|".join(re.escape(k) for k in KEY_NAMES) + rf"|(?:ctrl|control|контрол)\s*\+?\s*[{HOTKEY_LETTERS}])"

# Окна, в которые ввод запрещён: терминалы дали бы произвольное выполнение команд.
BLOCKED_EXES = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "windowsterminal.exe",
    "windowsterminalpreview.exe", "wt.exe", "conhost.exe", "openconsole.exe",
    "mintty.exe", "wezterm.exe", "wezterm-gui.exe", "alacritty.exe", "tabby.exe",
    "conemu.exe", "conemu64.exe", "hyper.exe", "terminus.exe", "bash.exe", "wsl.exe",
    # SSH/telnet/RDP: ввод туда — это команды на удалённой машине
    "putty.exe", "kitty.exe", "plink.exe", "ssh.exe", "telnet.exe", "mstsc.exe",
    # файловые менеджеры с командной строкой
    "totalcmd.exe", "totalcmd64.exe", "doublecmd.exe", "far.exe",
    "regedit.exe", "mmc.exe",
})
# IDE: встроенный терминал не отличить от редактора по имени процесса — по умолчанию не вводим.
IDE_EXES = frozenset({
    "code.exe", "code - insiders.exe", "vscodium.exe", "cursor.exe", "windsurf.exe",
    "idea64.exe", "pycharm64.exe", "webstorm64.exe", "goland64.exe", "clion64.exe",
    "rider64.exe", "datagrip64.exe", "phpstorm64.exe", "rubymine64.exe", "studio64.exe",
})
_ALLOW_IDE_INPUT = os.getenv("MYAI_TYPE_IDE", "0").lower() not in {"0", "false", "no"}
BROWSER_EXES = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
                          "vivaldi.exe", "brave.exe", "browser.exe", "iexplore.exe"})
SELF_WINDOW_MARK = "myai"   # в заголовке вкладки MyAI

def _input_blocked(exe: str) -> bool:
    return exe in BLOCKED_EXES or (exe in IDE_EXES and not _ALLOW_IDE_INPUT)

def _is_self_window(title: str, exe: str) -> bool:
    # Окно MyAI — вкладка браузера. Папка «MyAI» в Проводнике — не мы.
    return SELF_WINDOW_MARK in (title or "").lower() and (not exe or exe in BROWSER_EXES)

# ======================= Windows API (ctypes) =======================
def _make_input_types():
    """Структуры SendInput. Размеры заданы явно, чтобы sizeof(INPUT) == 40 на x64."""
    ulong_ptr = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("dwExtraInfo", ulong_ptr)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                    ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ulong_ptr)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]

    class _Union(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_uint32), ("u", _Union)]

    return INPUT, KEYBDINPUT


def _utf16_units(text: str):
    raw = text.encode("utf-16-le")
    return struct.unpack(f"<{len(raw) // 2}H", raw)

def _send_unicode(text: str, guard=None) -> None:
    """Вводит любой Unicode-текст (в том числе кириллицу) через SendInput."""
    INPUT, KEYBDINPUT = _make_input_types()
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, INPUT_KEYBOARD = 0x0002, 0x0004, 1
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    units = _utf16_units(text)
    for start in range(0, len(units), 40):
        if guard is not None:
            guard()          # фокус мог уйти с момента предыдущего блока
        chunk = units[start:start + 40]
        events = []
        for unit in chunk:
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = INPUT(type=INPUT_KEYBOARD)
                inp.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=0)
                events.append(inp)
        array = (INPUT * len(events))(*events)
        sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
        if sent != len(events):
            raise OSError("Windows не принял ввод (окно может быть запущено от имени администратора)")
        time.sleep(0.01)




def _foreground() -> Tuple[int, str, str]:
    """(hwnd, заголовок окна, имя exe в нижнем регистре) активного окна."""
    user32 = ctypes.windll.user32      # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    title_buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title_buf, 512)
    pid = ctypes.c_uint32(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        size = ctypes.c_uint32(1024)
        path_buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
            exe = os.path.basename(path_buf.value).lower()
        kernel32.CloseHandle(handle)
    return int(hwnd or 0), title_buf.value, exe


def _registry_app_path(exe: str) -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _ = winreg.QueryValueEx(key, "")
                value = os.path.expandvars(str(value)).strip('"')
                if os.path.isfile(value):
                    return value
        except OSError:
            continue
    return None


# ============================ Инструмент ============================
class ComputerControl:
    """Белый список приложений и узкий набор действий."""

    def available(self) -> bool:
        return IS_WINDOWS

    def capabilities(self) -> dict:
        return {"windows": IS_WINDOWS, "keyboard": pyautogui is not None,
                "apps": [s.key for s in APPS]}

    # ---------- адреса ----------
    @staticmethod
    def _url(text: str) -> str:
        value = (text or "").strip().strip("<>\"'")
        alias = normalize_command(value)
        if alias in URL_ALIASES:
            return URL_ALIASES[alias]
        if re.match(r"^(?:javascript|data|vbscript|file|mailto|shell|about|ms-[\w-]+):", value, re.I):
            raise ValueError("Разрешены только адреса http и https")
        if re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I) and not re.match(r"^https?://", value, re.I):
            raise ValueError("Разрешены только адреса http и https")
        if not re.match(r"^https?://", value, re.I):
            value = "https://" + value
        try:
            parts = urllib.parse.urlsplit(value)
            host = parts.hostname
            _ = parts.port  # проверяет корректность порта
        except ValueError:
            raise ValueError("Некорректный адрес сайта") from None
        if not host or not re.fullmatch(r"[\w.-]+", host) or not ("." in host or host == "localhost"):
            raise ValueError("Не похоже на адрес сайта")
        return value

    def open_url(self, url: str) -> str:
        url = self._url(url)
        webbrowser.open(url, new=2)
        return f"Открыл {url}"

    # ---------- приложения ----------
    @staticmethod
    def _resolve(spec: AppSpec) -> Optional[str]:
        for candidate in spec.paths:
            path = os.path.expandvars(candidate)
            if os.path.isfile(path):
                return path
        found = shutil.which(spec.exe)
        if found:
            return found
        return _registry_app_path(spec.exe) if IS_WINDOWS else None

    def launch_app(self, spec: AppSpec) -> str:
        if not IS_WINDOWS:
            raise ValueError("Управление приложениями поддерживается только в Windows")
        path = self._resolve(spec)
        if not path:
            raise ValueError(f"{spec.title} не найден на этом компьютере")
        subprocess.Popen([path, *spec.args], shell=False, close_fds=True)
        return f"Открыл {spec.title}."

    @staticmethod
    def _is_running(image: str) -> bool:
        res = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=8)
        return image.lower() in (res.stdout or "").lower()

    def close_app(self, spec: AppSpec, force: bool = False) -> str:
        if not IS_WINDOWS:
            raise ValueError("Управление приложениями поддерживается только в Windows")
        if not spec.closable:
            raise ValueError(f"{spec.title} я не закрываю: он управляет рабочим столом")
        signalled = []
        for image in spec.processes:
            cmd = ["taskkill", "/IM", image, "/T"] + (["/F"] if force else [])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if res.returncode == 0:
                signalled.append(image)
        if not signalled:
            return f"{spec.title} не запущен."
        if force:
            return f"Закрыл {spec.title} принудительно."
        for _ in range(8):   # даём приложению до ~2.5 с закрыться самому
            time.sleep(0.3)
            if not any(self._is_running(image) for image in signalled):
                return f"Закрыл {spec.title}."
        return (f"Попросил {spec.title} закрыться, но он ещё открыт — возможно, спрашивает о сохранении. "
                f"Можно сказать «закрой принудительно {spec.names[0]}» (несохранённое пропадёт).")

    # ---------- клавиатура ----------
    def _focus_target(self) -> Tuple[int, str]:
        """Переключается на предыдущее окно; возвращает (hwnd, заголовок) проверенной цели."""
        if pyautogui is None:
            raise RuntimeError("Для ввода с клавиатуры установите pyautogui")
        if not IS_WINDOWS:
            raise ValueError("Ввод с клавиатуры поддерживается только в Windows")
        before = _foreground()
        current = before
        if _is_self_window(before[1], before[2]):
            pyautogui.hotkey("alt", "tab")
            time.sleep(0.45)
            current = _foreground()
            if current[0] == before[0] or _is_self_window(current[1], current[2]):
                raise ValueError("Не нашёл другое окно для ввода. Сначала откройте нужное приложение (например, «открой блокнот»).")
        self._assert_target(current)
        return current[0], current[1]

    @staticmethod
    def _assert_target(win: Tuple[int, str, str]) -> None:
        exe = win[2]
        if exe in BLOCKED_EXES:
            raise ValueError("В терминалы, PowerShell и системные консоли я ничего не ввожу.")
        if exe in IDE_EXES and not _ALLOW_IDE_INPUT:
            raise ValueError("В окна IDE я по умолчанию не ввожу: там есть встроенный терминал. Разрешить: MYAI_TYPE_IDE=1.")

    def _recheck(self, hwnd: int) -> None:
        """Повторная проверка прямо перед отправкой: фокус мог уйти за время команды."""
        now = _foreground()
        if now[0] != hwnd:
            raise ValueError("Фокус ушёл из целевого окна — ввод остановлен (часть текста могла быть введена). Повторите команду.")
        self._assert_target(now)

    def type_text(self, text: str) -> str:
        text = re.sub(r"[\r\n\t]+", " ", text or "").strip()
        if not text:
            raise ValueError("Что именно написать?")
        if len(text) > MAX_TYPE_CHARS:
            raise ValueError(f"Слишком длинный текст (максимум {MAX_TYPE_CHARS} символов)")
        hwnd, title = self._focus_target()
        _send_unicode(text, guard=lambda: self._recheck(hwnd))
        return f"Ввёл текст ({len(text)} симв.) в окно «{title[:60]}»."

    @staticmethod
    def parse_key(raw: str) -> Tuple[str, ...]:
        key = normalize_command(raw).replace("клавишу ", "").strip()
        if key in KEY_ALIASES:
            return (KEY_ALIASES[key],)
        m = _HOTKEY_RE.fullmatch(key)
        if m:
            return ("ctrl", m.group(1))
        raise ValueError("Эта клавиша не входит в безопасный список")

    def press_key(self, raw: str) -> str:
        keys = self.parse_key(raw)
        hwnd, title = self._focus_target()
        self._recheck(hwnd)
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return f"Нажал {'+'.join(keys)} в окно «{title[:60]}»."

    # ---------- разбор команды ----------
    def execute(self, command: str) -> str:
        text = re.sub(r"^\s*компьютер\s*:\s*", "", command or "", flags=re.I)
        text = re.sub(r"^(?:(?:пожалуйста|пж|ну|слушай|эй|давай|можешь|можно)\s*,?\s+)+", "", text.strip(), flags=re.I).strip()
        if not text:
            return "После команды укажи действие. Например: «Открой Блокнот»."
        try:
            m = re.match(r"^(?:напиши|введи|напечатай)\s+(?:в\s+окне\s*[:—-]?\s*)?(.+)$", text, re.I | re.S)
            if m and not re.fullmatch(r"в\s+окне", m.group(1).strip(), re.I):
                return self.type_text(m.group(1))

            m = re.match(r"^(?:нажми|нажать)\s+(.+?)\s*[.!?]*$", text, re.I)
            if m:
                return self.press_key(m.group(1))

            m = re.match(r"^(?:открой|открыть|запусти|запустить)\s+(.+?)\s*[.!?]*$", text, re.I)
            if m:
                target = m.group(1).strip()
                low = normalize_command(target)
                spec = APP_BY_NAME.get(low)
                if spec:
                    return self.launch_app(spec)
                site = re.match(r"^(?:сайт|страницу|страница|url)\s+(.+)$", target, re.I)
                if site:
                    return self.open_url(site.group(1))
                if low in URL_ALIASES or re.match(r"^(?:https?://|www\.)", target, re.I):
                    return self.open_url(target)
                return "Этого приложения нет в безопасном списке MyAI. Могу открыть: " + ", ".join(s.title for s in APPS) + "."

            m = re.match(r"^(?:закрой|закрыть)\s+(?:(принудительно)\s+)?(.+?)\s*[.!?]*$", text, re.I)
            if m:
                force = bool(m.group(1))
                spec = APP_BY_NAME.get(normalize_command(m.group(2)))
                if not spec:
                    return "Я могу закрывать только приложения из безопасного списка MyAI."
                return self.close_app(spec, force=force)

            m = re.match(r"^(?:перейди|перейти)\s+(?:на\s+)?(.+?)\s*[.!?]*$", text, re.I)
            if m:
                return self.open_url(m.group(1))

            return "Понимаю команды: «открой», «запусти», «закрой», «перейди на», «напиши в окне» и «нажми»."
        except Exception as exc:
            log.warning("computer control: %s", exc)
            return f"Не выполнил действие: {exc}"
