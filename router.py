"""Роутер намерений: явные правила по приоритету.

Принципы:
* слова-«шум» (сегодня, сейчас, найди) сами по себе маршрут не определяют;
* команды управления ПК и памяти распознаются только в начале фразы;
* формы слов задаются явно («манга/мангу», но не «манго»; «погода», но не «ветеринар»).
Порядок проверки: MEMORY → COMPUTER → CALCULATOR → WEATHER → ANIME → WEB_SEARCH → CHAT.
Уточнения подборки аниме («ещё», «без комедии», «посильнее») распознаются только сразу после ответа ANIME —
поэтому decide() принимает prev_route и без него ведёт себя как раньше.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from textutil import normalize_command

try:  # единый источник имён приложений и клавиш
    from tools.computer_control import APP_NAMES, KEY_REGEX, URL_ALIAS_NAMES
except Exception:  # pragma: no cover
    APP_NAMES, URL_ALIAS_NAMES, KEY_REGEX = (), (), r"(?:enter|esc|tab|space)"


class Route(Enum):
    CHAT = "CHAT"
    CALCULATOR = "CALCULATOR"
    CALC_HISTORY = "CALC_HISTORY"
    CALC_LAST = "CALC_LAST"
    CALC_REPLAY = "CALC_REPLAY"
    CALC_USE_LAST = "CALC_USE_LAST"
    ANIME = "ANIME"
    WEB_SEARCH = "WEB_SEARCH"
    WEATHER = "WEATHER"
    MEMORY = "MEMORY"
    COMPUTER = "COMPUTER"


@dataclass
class RouteDecision:
    route: str
    reason: str
    followup: bool = False      # уточнение предыдущей подборки аниме


def _alt(names) -> str:
    return "(?:" + "|".join(re.escape(n) for n in names) + ")" if names else r"(?!x)x"


_APP = _alt(APP_NAMES)
_ALIAS = _alt(URL_ALIAS_NAMES)
_URL = r"(?:https?://\S+|www\.\S+|[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/\S*)?)"

MEMORY_RE = [
    re.compile(r"^(?:запомни|запомнить)\b"),
    re.compile(r"^(?:забудь|забыть)\b"),
    re.compile(r"^что ты (?:обо мне |про меня )?помнишь\b"),
    re.compile(r"^(?:моя память|покажи (?:мою )?память)\b"),
]

COMPUTER_RE = [
    re.compile(r"^компьютер\s*:"),
    re.compile(rf"^(?:открой|открыть|запусти|запустить)\s+{_APP}$"),
    re.compile(rf"^(?:закрой|закрыть)\s+(?:принудительно\s+)?{_APP}$"),
    re.compile(rf"^(?:открой|открыть)\s+(?:сайт|страницу|страница|url)\s+\S+"),
    re.compile(rf"^(?:открой|открыть)\s+(?:{_ALIAS}$|{_URL}$)"),
    re.compile(rf"^(?:перейди|перейти)\s+(?:на\s+)?(?:{_ALIAS}$|{_URL}$)"),
    re.compile(rf"^(?:нажми|нажать)\s+(?:клавишу\s+)?{KEY_REGEX}$"),
    re.compile(r"^(?:напиши|введи)\s+в\s+окне\s*[:—-]?\s*\S"),
]

CALC_HISTORY_RE = re.compile(r"^(?:покажи\s+)?(?:истори\w*\s+вычислен\w*|мои\s+расч[её]ты|список\s+расч[её]тов)\b")
CALC_LAST_RE = re.compile(r"^(?:покажи\s+)?последн\w*\s+результат\b")
CALC_REPLAY_RE = re.compile(r"^(?:повтори|повторить)\s+(?:последн\w*\s+)?(?:расч[её]т|вычисление)(?:\s+#?\d+)?\b")
CALC_USE_LAST_RE = re.compile(r"^(?:на основе последнего результата|используй(?: последний)? результат|с предыдущим результатом)\b")
CALC_VERB_RE = re.compile(r"^(?:посчитай|вычисли|сколько будет|реши пример|calculate|calc)\b.*\d")
CALC_ADVANCED_RE = re.compile(
    r"\b(?:сумм\w*|средн\w*|минимум|минимальн\w*|максимум|максимальн\w*|переведи|конвертируй|сколько\s+(?:метров|километров|сантиметров|миллиметров|граммов|килограммов|литров|миллилитров)|скидк\w*|ндс|увелич\w*|уменьш\w*|прибавь|добавь|вычти|выдели)\b.*\d"
    r"|\b\d+(?:[.,]\d+)?\s*(?:%|процент\w*)\s*от\s*\d"
    r"|^сначала\b.*\b(?:затем|потом|после этого)\b.*\d"
    r"|\d+(?:[.,]\d+)?\s*(?:мм|см|км|м|мг|кг|г|мл|л|°?[cfk])\s+(?:в|во|to)\s*\w+", re.I)
CALC_PURE_RE = re.compile(r"[\d()+\-*/%.,×÷^]+[?!.]?")
# «12 умножить на 3», «100 разделить на 4» — без глагола «посчитай»
CALC_WORDS_RE = re.compile(
    r"^[\d(][\d\s.,()]*(?:\s+(?:плюс|минус|умножить на|разделить на|делить на|поделить на|в степени)\s+[\d(][\d\s.,()]*)+[?!.]?$")

WEATHER_RE = [
    re.compile(r"\bпогод(?:а|е|у|ы|ой)\b"),
    re.compile(r"\bпрогноз\w*\s+погод"),
    re.compile(r"\bweather\b"),
    re.compile(r"\bтемператур\w*\s+(?:на улице|за окном|воздуха|в\s+\S+|сейчас)"),
    re.compile(r"\b(?:будет|идет|пойдет)\s+ли\s+(?:дождь|снег)\b"),
    re.compile(r"\b(?:на улице|за окном)\s+(?:дождь|снег|холодно|тепло|жарко)\b"),
    re.compile(r"\b(?:нужен|нужна|брать|взять|надевать)\s+ли\b.{0,25}\b(?:зонт\w*|плащ\w*|куртк\w*)"),
]
# Разговор о погоде вообще («что такое погода», «почему меняется погода») — это не запрос прогноза.
WEATHER_GENERAL_RE = re.compile(
    r"что такое погод|как (?:формируется|образуется|возникает|меняется)\s+погод|погод\w*\s+как\s+(?:явление|понятие)"
    r"|почему\s+(?:\w+\s+)?погод")


ANIME_RE = [
    re.compile(r"\bаниме\b"), re.compile(r"\banime\b"),
    re.compile(r"\bманг(?:а|у|е|и|ой)\b"), re.compile(r"\bmanga\b"),
    re.compile(r"исекай|исэкай|isekai"),
    re.compile(r"\bтайтл\w*\b"),
]

# Уточнение подборки: работает только если предыдущий ответ был ANIME.
ANIME_FOLLOWUP_RE = [
    re.compile(r"^(?:а )?(?:еще(?!\s+раз)|другие|другой|другое|другую)(?![а-я])"),
    re.compile(r"^(?:а )?(?:покажи|дай|найди|давай|подбери|предложи)\s+(?:мне\s+)?(?:еще|другие|другое|что-?нибудь\s+(?:еще|другое|новее|посвежее))(?![а-я])"),
    re.compile(r"\bпо(?:сильнее|мощнее|новее|свежее|старее|серьезнее|мрачнее|смешнее|интереснее|популярнее)(?![а-я])"),
    re.compile(r"^(?:а )?(?:без|меньше|больше|поменьше|побольше)\s+\w+"),
    re.compile(r"^(?:а )?(?:только|лучше)\s+(?:сериал\w*|фильм\w*|(?:19|20)\d\d)(?![а-я\d])"),
    re.compile(r"\b(?:понравил\w+|нравится|зашел|зашло)\b.*\b(?:но|а)\b.*\b(?:хочу|хотелось бы|нужн\w+|надо|хочется)\b"),
]

WEB_EXPLICIT_RE = [
    re.compile(r"\b(?:в интернете|в сети интернет|в гугле|в google|загугли|погугли)\b"),
    re.compile(r"\b(?:поищи|найди|проверь) в сети\b"),
    re.compile(r"\bsearch (?:online|the web)\b"),
]
WEB_FRESH_RE = [
    re.compile(r"\bновост(?:и|ей|ям|ях|ь)\b"),
    re.compile(r"\bкурс\s+(?:доллар|евро|рубл|юан|валют|биткоин|bitcoin|btc|eth|usd|eur)\w*"),
    re.compile(r"\bчто нового\b"),
    re.compile(r"\bчто произошло\b"),
    re.compile(r"\b(?:последн\w+|новая|новую|свежая|актуальн\w+)\s+(?:версия|версии|версию|релиз\w*|обновлени\w*|курс|цен\w+)\b"),
    re.compile(r"\bкакая (?:последняя|новая|актуальная) версия\b"),
    re.compile(r"\bдата выхода\b"),
    re.compile(r"\bофициальн\w+ сайт\b"),
    re.compile(r"\bсколько стоит\b"),
    re.compile(r"\bкто сейчас\b"),
    re.compile(r"\bкогда выйдет\b"),
    re.compile(r"\b(?:latest|current) (?:news|version|price)\b|\bnews today\b"),
]
WEB_FIND_RE = re.compile(r"^(?:найди|поищи)\b")
# «Найди ошибку в коде», «найди корни уравнения» — это не поиск в интернете.
NOT_WEB_FIND_RE = re.compile(
    r"\b(?:ошибк\w*|баг\w*|опечатк\w*|корн\w*|производн\w*|интеграл\w*|предел\w*|решени\w*)\b"
    r"|\bв\s+(?:(?:этом|моем|данном|этой|моей)\s+)?(?:коде|тексте|программе|функции|файле|предложении|списке|задаче|уравнении)\b"
)
CODEISH_RE = re.compile(r"```|[{};]|\bdef\b|\bprint\(|\bimport\b|\n")


# Вопросы-определения («что такое аниме», «чем аниме отличается от мультфильмов») — это разговор, а не поиск в каталоге.
ANIME_DEFINITION_RE = re.compile(
    r"^(?:а\s+)?(?:что такое|что значит|что означает|кто такой|кто такие|чем\s.{1,40}отличается|как переводится)\b")


class Router:
    """Правила идут по приоритету; побеждает первое совпавшее."""

    def decide(self, text: str, prev_route: str | None = None) -> RouteDecision:
        raw = text or ""
        t = normalize_command(raw)
        if not t:
            return RouteDecision(Route.CHAT.value, "пустой запрос")

        for rx in MEMORY_RE:
            if rx.search(t):
                return RouteDecision(Route.MEMORY.value, rx.pattern)

        for rx in COMPUTER_RE:
            if rx.search(t):
                return RouteDecision(Route.COMPUTER.value, rx.pattern)

        if CALC_HISTORY_RE.search(t):
            return RouteDecision(Route.CALC_HISTORY.value, "история вычислений")
        if CALC_LAST_RE.search(t):
            return RouteDecision(Route.CALC_LAST.value, "последний результат")
        if CALC_REPLAY_RE.search(t):
            return RouteDecision(Route.CALC_REPLAY.value, "повтор вычисления")
        if CALC_USE_LAST_RE.search(t):
            return RouteDecision(Route.CALC_USE_LAST.value, "использование предыдущего результата")
        compact = re.sub(r"\s+", "", t)
        if CALC_ADVANCED_RE.search(t):
            return RouteDecision(Route.CALCULATOR.value, "статистика или конвертация единиц")
        if CALC_VERB_RE.search(t) or CALC_WORDS_RE.fullmatch(t):
            return RouteDecision(Route.CALCULATOR.value, "команда расчёта")
        if CALC_PURE_RE.fullmatch(compact) and re.search(r"\d", compact) and re.search(r"[+\-*/%×÷^]", compact):
            return RouteDecision(Route.CALCULATOR.value, "математическое выражение")

        if not WEATHER_GENERAL_RE.search(t):
            for rx in WEATHER_RE:
                if rx.search(t):
                    return RouteDecision(Route.WEATHER.value, rx.pattern)

        if prev_route == Route.ANIME.value:
            for rx in ANIME_FOLLOWUP_RE:
                if rx.search(t):
                    return RouteDecision(Route.ANIME.value, "уточнение подборки: " + rx.pattern, followup=True)

        if not ANIME_DEFINITION_RE.search(t):
            for rx in ANIME_RE:
                if rx.search(t):
                    return RouteDecision(Route.ANIME.value, rx.pattern)

        for rx in WEB_EXPLICIT_RE + WEB_FRESH_RE:
            if rx.search(t):
                return RouteDecision(Route.WEB_SEARCH.value, rx.pattern)
        if WEB_FIND_RE.search(t) and not NOT_WEB_FIND_RE.search(t) and not CODEISH_RE.search(raw):
            return RouteDecision(Route.WEB_SEARCH.value, "явная просьба найти")

        return RouteDecision(Route.CHAT.value, "обычный диалог")

    def route(self, text: str, prev_route: str | None = None) -> Route:
        return Route(self.decide(text, prev_route).route)
