import pandas as pd
import os
from datetime import datetime, timedelta


class HistoryAnalyzer:
    def __init__(self, file_path='trade_history.csv', days=None):
        self.file_path = file_path
        self.days = days  # Зберігаємо переданий період (кількість днів)

        # Одразу завантажуємо дані при створенні об'єкта
        self.df = self.load_data()

    def load_data(self):
        """Завантажує та готує дані з CSV файлу з урахуванням фільтру часу"""
        if not os.path.exists(self.file_path):
            print(f"❌ Файл {self.file_path} не знайдено!")
            return pd.DataFrame()

        try:
            df = pd.read_csv(self.file_path)
            if df.empty:
                return df

            # 1. Перетворюємо колонку Date у правильний формат часу
            if 'Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Date'])

                # === ФІЛЬТРАЦІЯ ПО ЧАСУ ===
                if self.days is not None:
                    cutoff_date = datetime.now() - timedelta(days=self.days)
                    df = df[df['Date'] >= cutoff_date]

            # 2. Очищаємо ROE_Percent від знаку '%' та перетворюємо у числа
            if 'ROE_Percent' in df.columns:
                df['ROE_Percent'] = df['ROE_Percent'].astype(str).str.replace('%', '')
                df['ROE_Percent'] = pd.to_numeric(df['ROE_Percent'], errors='coerce').fillna(0)

            # 3. Гарантуємо, що фінансові колонки є числами
            if 'PnL_USD' in df.columns:
                df['PnL_USD'] = pd.to_numeric(df['PnL_USD'], errors='coerce').fillna(0)
            if 'Margin_USD' in df.columns:
                df['Margin_USD'] = pd.to_numeric(df['Margin_USD'], errors='coerce').fillna(0)

            # 4. Очищаємо плече від 'x' (наприклад '10.0x' -> 10.0)
            if 'Leverage' in df.columns:
                df['Leverage'] = df['Leverage'].astype(str).str.replace('x', '', case=False)
                df['Leverage'] = pd.to_numeric(df['Leverage'], errors='coerce').fillna(0)

            return df

        except Exception as e:
            print(f"❌ Помилка читання історії: {e}")
            return pd.DataFrame()

    def _calculate_metrics(self, df):
        """Внутрішня функція для підрахунку метрик"""
        if df is None or df.empty:
            return {
                "Усього угод": 0,
                "PnL": 0.0,
                "Середній PnL": 0.0,
                "Сер. Маржа": 0.0,
                "Сер. Плече": 0.0,
                "Сер. ROE %": 0.0,
                "WinRate %": 0.0
            }

        total_trades = len(df)
        total_pnl = df['PnL_USD'].sum()
        avg_pnl = df['PnL_USD'].mean()

        # Нові метрики
        avg_margin = df['Margin_USD'].mean() if 'Margin_USD' in df.columns else 0.0
        avg_leverage = df['Leverage'].mean() if 'Leverage' in df.columns else 0.0
        avg_roe = df['ROE_Percent'].mean() if 'ROE_Percent' in df.columns else 0.0

        # Підрахунок WinRate (відсоток успішних угод)
        if 'Status' in df.columns:
            winning_trades = len(df[df['Status'] == 'Success'])
            win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
        else:
            win_rate = 0.0

        return {
            "Усього угод": total_trades,
            "PnL": round(total_pnl, 2),
            "Середній PnL": round(avg_pnl, 2),
            "Сер. Маржа": round(avg_margin, 2),
            "Сер. Плече": round(avg_leverage, 1),
            "Сер. ROE %": round(avg_roe, 2),
            "WinRate %": round(win_rate, 2)
        }

    def get_overall_stats(self):
        """1. Загальна статистика за весь час"""
        return self._calculate_metrics(self.df)

    def analyze_by_symbol(self, symbol):
        """2. Статистика по конкретному активу (напр. BTC/USDT)"""
        df = self.df
        if df is not None and not df.empty and 'Symbol' in df.columns:
            df = df[df['Symbol'].str.contains(symbol, case=False, na=False)]
        return self._calculate_metrics(df)

    def analyze_by_action(self, action):
        """3. Статистика по напрямку (LONG або SHORT)"""
        df = self.df
        if df is not None and not df.empty and 'Action' in df.columns:
            df = df[df['Action'].str.upper() == action.upper()]
        return self._calculate_metrics(df)

    def get_grouped_report(self, group_by_column='Symbol'):
        """4. Зведений звіт із підрахунком WinRate для кожної групи"""
        df = self.df
        if df is None or df.empty or group_by_column not in df.columns:
            return None

        # Словник агрегації
        agg_dict = {
            'PnL_USD': ['count', 'sum', 'mean'],
            'Margin_USD': 'mean',
            'ROE_Percent': 'mean'
        }

        # Якщо є колонка Status, додаємо розрахунок WinRate
        if 'Status' in df.columns:
            agg_dict['Status'] = lambda x: (x == 'Success').sum() / len(x) * 100

        report = df.groupby(group_by_column).agg(agg_dict).reset_index()

        # Перейменовуємо колонки для красивого виводу
        if 'Status' in df.columns:
            report.columns = [group_by_column, 'Угоди', 'PnL', 'Сер_PnL', 'Сер_Маржа', 'Сер_ROE', 'Вінрейт_%']
            report['Вінрейт_%'] = report['Вінрейт_%'].round(1)
        else:
            report.columns = [group_by_column, 'Угоди', 'PnL', 'Сер_PnL', 'Сер_Маржа', 'Сер_ROE']

        # Округлення
        report['PnL'] = report['PnL'].round(2)
        report['Сер_PnL'] = report['Сер_PnL'].round(2)
        report['Сер_Маржа'] = report['Сер_Маржа'].round(2)
        report['Сер_ROE'] = report['Сер_ROE'].round(2)

        return report


# --- ТЕСТОВИЙ БЛОК ДЛЯ КОНСОЛІ ---
if __name__ == "__main__":
    analyzer = HistoryAnalyzer()

    print("\n📊 --- ЗАГАЛЬНА СТАТИСТИКА ---")
    stats = analyzer.get_overall_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")

    print("\n📋 --- АНАЛІЗ ПО ВСІХ АКТИВАХ (МОНЕТАХ) ---")
    coins_report = analyzer.get_grouped_report('Symbol')
    if coins_report is not None:
        print(coins_report.to_string(index=False))

    print("\n⚖️ --- АНАЛІЗ ЛОНГІВ ТА ШОРТІВ ---")
    actions_report = analyzer.get_grouped_report('Action')
    if actions_report is not None:
        print(actions_report.to_string(index=False))
    print("\n")