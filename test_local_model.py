"""Ручная проверка загрузки модели без веб-интерфейса: python test_local_model.py"""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from chat_model import ChatBot

bot = ChatBot()
start = time.time()
print("Загружаю модель…")
if not bot._ensure_model():
    raise SystemExit(f"Не удалось загрузить модель: {bot._model_error}")
print(f"Готово за {time.time() - start:.1f} с: {bot._loaded_model_name}")
for text in ("Привет! Ответь одним предложением.", "Сколько будет 17*23?"):
    t0 = time.time()
    result = bot.reply(text)
    print(f"\n> {text}\n[{result['route']}, {time.time() - t0:.1f} с] {result['response']}")
