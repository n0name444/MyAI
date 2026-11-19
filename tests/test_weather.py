import unittest

from tools.weather import WeatherTool


class WeatherTests(unittest.TestCase):
    def test_extract_city(self):
        cases = {
            "Какая погода в Архангельске сейчас?": "Архангельске",
            "погода в Москве": "Москве",
            "погода Казань": "Казань",
            "прогноз погоды завтра в Санкт-Петербурге": "Санкт-Петербурге",
            "температура в Сочи сегодня": "Сочи",
            "погода": None,
        }
        for text, city in cases.items():
            self.assertEqual(WeatherTool.extract_city(text), city, text)

    def test_candidates(self):
        self.assertIn("Архангельск", WeatherTool.city_candidates("Архангельске"))
        self.assertIn("Москва", WeatherTool.city_candidates("Москву"))
        self.assertIn("Казань", WeatherTool.city_candidates("Казани"))

    def test_get_with_fake_api_and_tomorrow(self):
        w = WeatherTool()
        calls = []

        def fake(url, params):
            calls.append(url)
            if "geocoding" in url:
                return {"results": [] if params["name"] == "Архангельске" else [{"name": "Архангельск", "admin1": "Архангельская область", "latitude": 64.5, "longitude": 40.5}]}
            return {"current": {"temperature_2m": 3.1, "apparent_temperature": 0.5, "relative_humidity_2m": 80, "weather_code": 61, "wind_speed_10m": 12},
                    "current_units": {"temperature_2m": "°C", "apparent_temperature": "°C", "wind_speed_10m": "km/h"},
                    "daily": {"temperature_2m_max": [5, 7], "temperature_2m_min": [1, 2], "precipitation_probability_max": [60, 10], "weather_code": [61, 0]},
                    "daily_units": {"temperature_2m_max": "°C"}}
        w._get_json = fake
        now = w.get("Какая погода в Архангельске сейчас?")
        self.assertIn("Архангельск", now); self.assertIn("3.1", now); self.assertIn("Небольшой дождь", now)
        tmr = w.get("погода в Архангельске завтра")
        self.assertIn("завтра", tmr); self.assertIn("от **2** до **7", tmr); self.assertIn("Ясно", tmr)
        self.assertIn("Укажи город", w.get("погода"))

    def test_api_failure_message(self):
        w = WeatherTool()
        def boom(url, params): raise RuntimeError("net")
        w._get_json = boom
        self.assertIn("Не удалось получить", w.get("погода в Москве"))


if __name__ == "__main__":
    unittest.main()
