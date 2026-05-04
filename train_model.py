import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
import joblib
import os

# ==========================================
# НАЛАШТУВАННЯ
# ==========================================
# Файл, у який ваш бот (MLDataLogger) записує результати
CSV_FILE = "ml_training_data.csv"

# Вкажіть таймфрейм, для якого хочете натренувати модель.
# Наприклад: '5m', '15m', '1h'
TARGET_TF = "15m"


def train_trading_model():
    if not os.path.exists(CSV_FILE):
        print(f"❌ Помилка: Файл {CSV_FILE} не знайдено.")
        return

    print(f"📥 Завантаження даних з {CSV_FILE}...")
    try:
        df = pd.read_csv(CSV_FILE)
    except Exception as e:
        print(f"❌ Помилка читання файлу: {e}")
        return

    # 1. ОЧИЩЕННЯ ДАНИХ
    # Видаляємо ордери, які ще відкриті (де result = пусте поле/NaN)
    df = df.dropna(subset=['result']).copy()

    # Фільтруємо дані ТІЛЬКИ для обраного таймфрейму (TARGET_TF)
    df = df[df['base_tf'] == TARGET_TF].copy()

    print(f"📊 Всього закритих угод для ТФ {TARGET_TF}: {len(df)}")

    if len(df) < 50:
        print("❌ Занадто мало даних для навчання. Зберіть хоча б 50-100 угод, а краще 1000+.")
        return
    elif len(df) < 1000:
        print("⚠️ УВАГА: Угод менше 1000. Модель натренується, але для реального трейдингу бажано більше історії!")

    # 2. ПІДГОТОВКА ДАНИХ (Feature Engineering)
    # Перетворюємо текстовий напрямок угоди в цифри.
    # Розуміє як LONG/SHORT, так і BUY/SELL
    df['side_num'] = df['side'].astype(str).str.upper().apply(
        lambda x: 1 if x in ['LONG', 'BUY'] else 0
    )

    # Ці колонки МАЮТЬ ТОЧНО ЗБІГАТИСЯ з тим, що генерує MLDataLogger
    features = [
        "side_num",
        "day_of_week",
        "hour",
        "rsi_base",
        "rsi_context",
        "dist_to_ema_pct",
        "atr_base",
        "adx_context"
    ]

    # Відділяємо фактори (X) від результату (y)
    X = df[features]
    # result: 1.0 (профіт), 0.0 (збиток). Конвертуємо в ціле число на всякий випадок.
    y = df['result'].astype(int)

    # 3. РОЗБИВКА НА ТРЕНУВАННЯ І ТЕСТ
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    print(f"🧠 Вчимося на {len(X_train)} угодах. Тестуємо на {len(X_test)} нових угодах...")

    # 4. СТВОРЕННЯ ТА НАВЧАННЯ МОДЕЛІ
    model = RandomForestClassifier(
        n_estimators=200,  # Кількість дерев рішень
        max_depth=5,  # Обмеження глибини, щоб не було перенавчання
        random_state=42,
        class_weight='balanced'  # Балансує вагу, якщо збитків/профітів різна кількість
    )

    # Процес навчання
    model.fit(X_train, y_train)

    # 5. ПЕРЕВІРКА ЯКОСТІ НАВЧАННЯ
    predictions = model.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)

    print("\n==========================================")
    print(f"📈 РЕЗУЛЬТАТИ ТЕСТУВАННЯ МОДЕЛІ (ТФ: {TARGET_TF})")
    print("==========================================")
    print(f"🎯 Загальна точність (Accuracy): {accuracy * 100:.2f}%\n")

    # Виводимо детальний звіт
    print(classification_report(y_test, predictions, target_names=["Збиток (0)", "Профіт (1)"], zero_division=0))

    # 6. ВАЖЛИВІСТЬ ФАКТОРІВ
    print("\n🔍 ВАЖЛИВІСТЬ ФАКТОРІВ (Що впливало на успіх найбільше):")
    importances = model.feature_importances_
    feat_importances = sorted(zip(features, importances), key=lambda x: x[1], reverse=True)
    for feat, imp in feat_importances:
        print(f" - {feat}: {imp * 100:.2f}%")

    # 7. ЗБЕРЕЖЕННЯ МОДЕЛІ
    # Назва файлу автоматично міститиме таймфрейм
    model_filename = f"rf_trading_model_{TARGET_TF}.pkl"
    joblib.dump(model, model_filename)

    print("\n✅ НАВЧАННЯ ЗАВЕРШЕНО!")
    print(f"💾 Модель збережена у файл: {model_filename}")


if __name__ == "__main__":
    train_trading_model()