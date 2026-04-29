import csv
import os
import json
from datetime import datetime


class MLDataLogger:
    def __init__(self, csv_file="ml_training_data.csv", pending_file="pending_ml_data.json"):
        self.csv_file = csv_file
        self.pending_file = pending_file
        # Заголовки стовпців для нашої майбутньої моделі
        self.headers = [
            "order_id", "symbol", "side", "day_of_week", "hour",
            "rsi_15m", "rsi_4h", "dist_to_ema_pct", "atr_15m", "adx_4h", "result"
        ]
        self._init_csv()

    def _init_csv(self):
        """Створює CSV файл із заголовками, якщо його ще немає."""
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.headers)

    def save_entry_state(self, order_id, symbol, side, indicators):
        """
        Викликається під час ВІДКРИТТЯ ордера.
        Зберігає стан ринку у тимчасовий JSON файл.
        """
        now = datetime.now()
        data = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "day_of_week": now.weekday() + 1,  # 1 (Понеділок) - 7 (Неділя)
            "hour": now.hour,  # 0 - 23
            "rsi_15m": indicators.get('rsi_15m', 0),
            "rsi_4h": indicators.get('rsi_4h', 0),
            "dist_to_ema_pct": indicators.get('dist_to_ema_pct', 0),
            "atr_15m": indicators.get('atr_15m', 0),
            "adx_4h": indicators.get('adx_4h', 0),
            "result": None  # Результат поки невідомий
        }

        pending_data = self._load_pending()
        pending_data[str(order_id)] = data

        with open(self.pending_file, 'w', encoding='utf-8') as f:
            json.dump(pending_data, f, indent=4)

        print(f"[ML Logger] Дані для ордера {order_id} ({symbol}) збережено. Очікуємо результат...")

    def log_result(self, order_id, is_win):
        """
        Викликається під час ЗАКРИТТЯ ордера.
        Знаходить збережені дані, додає результат (1 або 0) і записує в CSV.
        """
        pending_data = self._load_pending()
        order_id_str = str(order_id)

        if order_id_str in pending_data:
            trade_data = pending_data.pop(order_id_str)
            # 1 - Профіт (Take Profit), 0 - Збиток (Stop Loss)
            trade_data["result"] = 1 if is_win else 0

            # Записуємо готовий рядок у CSV для ML
            with open(self.csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.headers)
                writer.writerow(trade_data)

            # Оновлюємо JSON (видаляємо закритий ордер)
            with open(self.pending_file, 'w', encoding='utf-8') as f:
                json.dump(pending_data, f, indent=4)

            print(f"[ML Logger] Ордер {order_id} завершено. Результат {trade_data['result']} додано в датасет.")

    def _load_pending(self):
        """Завантажує тимчасові дані відкритих ордерів."""
        if os.path.exists(self.pending_file):
            with open(self.pending_file, 'r', encoding='utf-8') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}