"""Погода через Open-Meteo (геокодирование + прогноз), без поисковой выдачи."""
from __future__ import annotations

import logging
import re
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

WEATHER_CODES = {
    0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность", 3: "Пасмурно",
    45: "Туман", 48: "Изморозь/туман", 51: "Лёгкая морось", 53: "Морось", 55: "Сильная морось",
    56: "Ледяная морось", 57: "Сильная ледяная морось", 61: "Небольшой дождь", 63: "Дождь", 65: "Сильный дождь",
    66: "Ледяной дождь", 67: "Сильный ледяной дождь", 71: "Небольшой снег", 73: "Снег", 75: "Сильный снег",
    77: "Снежные зёрна", 80: "Ливни", 81: "Ливень", 82: "Сильный ливень", 85: "Снегопад",
    86: "Сильный снегопад", 95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом",
}

_CITY_PATTERNS = [
    r"(?:погода|температура|прогноз(?:\s+погоды)?|дожд[ья])(?:\s+(?:сейчас|сегодня|завтра))?\s+(?:в|во|для)\s+([^,?.!]+)",
    r"(?:погода|температура|прогноз(?:\s+погоды)?)\s+([^,?.!]+?)(?:\s+(?:сейчас|сегодня|завтра))?$",
    r"(?:в|во)\s+([^,?.!]+)\s+(?:сейчас|сегодня|завтра)",
]
_TIME_WORDS = re.compile(r"\s+(?:сейчас|сегодня|завтра|послезавтра)$", re.I)


class WeatherTool:
    GEO = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MyAI-Weather/1.4"})

    def _get_json(self, url, params):
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---------- разбор запроса ----------
    @staticmethod
    def extract_city(text: str) -> Optional[str]:
        t = (text or "").strip()
        for p in _CITY_PATTERNS:
            m = re.search(p, t, re.I)
            if m:
                city = _TIME_WORDS.sub("", m.group(1).strip())
                city = re.sub(r"^(?:сейчас|сегодня|завтра|послезавтра)\s+(?:(?:в|во|для)\s+)?", "", city, flags=re.I)
                city = re.sub(r"\s+(?:сейчас|сегодня|завтра)\b.*$", "", city, flags=re.I)
                city = city.strip(" ,.-")
                if city:
                    return city
        return None

    @staticmethod
    def city_candidates(city: str) -> List[str]:
        """«Архангельске» → Архангельск, «Москву» → Москва, «Казани» → Казань."""
        c = city.strip()
        out = [c]
        low = c.lower()
        if low.endswith(("е", "и")) and len(c) > 3:
            out += [c[:-1], c[:-1] + "а", c[:-1] + "ь"]
        elif low.endswith("у") and len(c) > 3:
            out += [c[:-1] + "а", c[:-1]]
        elif low.endswith("ой") and len(c) > 4:
            out += [c[:-2] + "а"]
        elif low.endswith("ом") and len(c) > 4:
            out += [c[:-2]]
        return list(dict.fromkeys(out))

    def _find_place(self, city: str):
        for candidate in self.city_candidates(city):
            geo = self._get_json(self.GEO, {"name": candidate, "count": 5, "language": "ru", "format": "json"})
            places = geo.get("results") or []
            if places:
                exact = next((x for x in places if str(x.get("name", "")).lower() == candidate.lower()), None)
                return exact or places[0]
        return None

    # ---------- ответ ----------
    def get(self, text: str) -> str:
        city = self.extract_city(text)
        if not city:
            return "Укажи город, например: «погода в Архангельске»."
        tomorrow = bool(re.search(r"\bзавтра\b", text or "", re.I))
        try:
            place = self._find_place(city)
            if not place:
                return f"Не удалось определить город «{city}»."
            data = self._get_json(self.FORECAST, {
                "latitude": place["latitude"], "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 2,
            })
            return self.format(place, data, tomorrow)
        except Exception as exc:
            log.warning("[WEATHER] %s", exc)
            return "Не удалось получить актуальную погоду. Проверь подключение к интернету и название города."

    @staticmethod
    def _describe(code) -> str:
        return WEATHER_CODES.get(code, "Неизвестные погодные условия")

    @classmethod
    def format(cls, place: dict, data: dict, tomorrow: bool = False) -> str:
        name = place.get("name", "")
        region = place.get("admin1")
        where = f"{name}, {region}" if region and region.lower() != name.lower() else name
        daily = data.get("daily", {})
        du = data.get("daily_units", {})
        idx = 1 if tomorrow else 0

        def day(key):
            values = daily.get(key) or []
            return values[idx] if len(values) > idx else None

        tmax, tmin, rain = day("temperature_2m_max"), day("temperature_2m_min"), day("precipitation_probability_max")
        unit = du.get("temperature_2m_max", "°C")
        if tomorrow:
            lines = [f"Погода в **{where}** завтра:", f"- {cls._describe(day('weather_code'))}"]
            if tmin is not None and tmax is not None:
                lines.append(f"- Температура: от **{tmin}** до **{tmax} {unit}**")
            if rain is not None:
                lines.append(f"- Вероятность осадков: **{rain}%**")
            return "\n".join(lines)

        cur, cu = data.get("current", {}), data.get("current_units", {})
        lines = [f"Погода в **{where}** сейчас:", f"- {cls._describe(cur.get('weather_code'))}",
                 f"- Температура: **{cur.get('temperature_2m')} {cu.get('temperature_2m', '°C')}**",
                 f"- Ощущается как: **{cur.get('apparent_temperature')} {cu.get('apparent_temperature', '°C')}**",
                 f"- Влажность: **{cur.get('relative_humidity_2m')}%**",
                 f"- Ветер: **{cur.get('wind_speed_10m')} {cu.get('wind_speed_10m', 'км/ч')}**"]
        if tmin is not None and tmax is not None:
            lines.append(f"- Сегодня: от **{tmin}** до **{tmax} {unit}**" + (f", осадки {rain}%" if rain is not None else ""))
        return "\n".join(lines)
