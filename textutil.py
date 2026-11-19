"""Общая нормализация текста команд для роутера и управления ПК."""
from __future__ import annotations

import re

_POLITE_PREFIX = re.compile(r"^(?:(?:пожалуйста|пж|ну|слушай|эй|давай|можешь|можно|а ну|а теперь|теперь|и еще раз|и ещё раз|еще раз|ещё раз)\s*,?\s+)+", re.I)
_POLITE_SUFFIX = re.compile(r"(?:\s*,?\s*(?:пожалуйста|пж))+$", re.I)


def strip_polite(text: str) -> str:
    """Убирает «пожалуйста», «ну», «можешь» с краёв, не меняя регистр остального текста."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = _POLITE_PREFIX.sub("", t)
    t = t.strip(" .!?…")
    t = _POLITE_SUFFIX.sub("", t).strip(" .!?,…")
    return t


def normalize_command(text: str) -> str:
    """Нижний регистр, ё→е, без вежливых слов и крайней пунктуации."""
    return strip_polite((text or "").lower().replace("ё", "е"))


# --------------------------------------------------------------------------- недоверенный текст
# Страницы из интернета и описания из каталогов пишут посторонние люди. Для небольшой локальной модели
# фраза «игнорируй предыдущие инструкции» внутри страницы — реальный риск, поэтому такие фрагменты вырезаются,
# а остальное передаётся модели как данные (см. системный промпт веб-поиска).
_SPECIAL_TOKEN_RE = re.compile(r"<\|[A-Za-z0-9_\-]{1,30}\|>|\[\s*/?\s*(?:INST|SYS)\s*\]", re.I)
_WRAPPER_TAG_RE = re.compile(r"<\s*/?\s*(?:источник|карточка|source|system|assistant|user|think)\b[^>]*>", re.I)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_INJECTION_RES = [re.compile(p, re.I) for p in (
    r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|preceding|system)"
    r"\s+(?:instructions?|prompts?|rules?|messages?|directions?)",
    r"(?:игнорируй|игнорируйте|забудь|забудьте|отбрось|отмени)\s+(?:все\s+|любые\s+|свои\s+)?"
    r"(?:предыдущие|прошлые|прежние|вышеуказанные|системные|свои|все)\s+(?:инструкции|указания|правила|команды|промпты?)",
    r"(?:system|системн\w+)\s+(?:prompt|промпт)",
    r"системн\w+\s+инструкци\w+",
    r"you\s+are\s+now\s+(?:a|an|the|in)\b",
    r"(?:new|updated|real)\s+instructions?\s*:",
    r"(?:новые|новая|настоящие)\s+инструкци[ияю]\w*\s*:",
    r"(?:reveal|show|print|repeat)\s+(?:your\s+|the\s+)?(?:system\s+prompt|instructions)",
)]
CUT_MARK = "[вырезано: похоже на команду]"


def neutralize_untrusted(text: str, max_len: int | None = None) -> tuple[str, int]:
    """Очищает чужой текст перед тем, как показать его модели.

    Вырезает служебные токены чата (<|im_start|> и т. п.), теги-обёртки промптов и фразы вида
    «игнорируй предыдущие инструкции». Возвращает (чистый текст, сколько подозрительных фрагментов вырезано).
    Служебные токены и теги убираются молча, но тоже учитываются в счётчике.
    """
    t = _CONTROL_RE.sub("", str(text or ""))
    found = 0
    for rx in (_SPECIAL_TOKEN_RE, _WRAPPER_TAG_RE):
        t, n = rx.subn(" ", t)
        found += n
    for rx in _INJECTION_RES:
        t, n = rx.subn(CUT_MARK, t)
        found += n
    if max_len is not None:
        t = t[:max_len]
    return t, found
