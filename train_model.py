import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
import joblib

# ==========================================
# НАЛАШТУВАННЯ (ЗАПОВНИШ ПОТІМ)
# ==========================================
# Сюди впишеш назву файлу, коли назбираєш 1000+ угод
CSV_FILE = ""


def train_trading_model():
    if not CSV_FILE:
        print("❌ Помилка: Вкажіть шлях до CSV файлу в змінній CSV_FILE.")
        return

    print(f"📥 Завантаження даних з {CSV_FILE}...")
    try:
        df = pd.read_csv(CSV_FILE)
    except Exception as e:
        print(f"❌ Помилка читання файлу: {e}")
        return

    # 1. ОЧИЩЕННЯ ДАНИХ
    # Видаляємо угоди, які ще не закрилися (результат пустий)
    df = df.dropna(subset=['result'])

    print(f"📊 Всього закритих угод для аналізу: {len(df)}")
    if len(df) < 1000:
        print("⚠️ УВАГА: Рекомендується мінімум 1000 угод для якісного навчання!")

    # 2. ПІДГОТОВКА ДАНИХ (Feature Engineering)
    # ML моделі не розуміють текст "LONG" або "SHORT", тільки цифри.
    # Робимо: LONG = 1, SHORT = 0
    df['side_num'] = df['side'].apply(lambda x: 1 if str(x).upper() == 'LONG' else 0)

    # Визначаємо, на які фактори дивитиметься модель (наші фічі з логера)
    features = [
        "side_num", "day_of_week", "hour",
        "rsi_15m", "rsi_4h", "dist_to_ema_pct", "atr_15m", "adx_4h"
    ]

    X = df[features]  # Фактори (Що було на ринку)
    y = df['result']  # Результат (Профіт = 1, Збиток = 0)

    # 3. РОЗБИВКА НА ТРЕНУВАННЯ І ТЕСТ
    # 80% угод йдуть на навчання, 20% - на іспит для моделі
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    print(f"🧠 Вчимося на {len(X_train)} угодах. Тестуємо на {len(X_test)} нових угодах...")

    # 4. СТВОРЕННЯ ТА НАВЧАННЯ МОДЕЛІ (Random Forest)
    model = RandomForestClassifier(
        n_estimators=200,  # Кількість дерев рішень (200 - золота середина)
        max_depth=5,  # Обмежуємо глибину, щоб модель не "зазубрювала" історію
        random_state=42,
        class_weight='balanced'  # Допомагає, якщо збитків більше ніж профітів
    )

    # Сам процес навчання
    model.fit(X_train, y_train)

    # 5. ПЕРЕВІРКА ЯКОСТІ НАВЧАННЯ
    predictions = model.predict(X_test)
    accuracy = accuracy_score(y_test, predictions)

    print("\n==========================================")
    print("📈 РЕЗУЛЬТАТИ ТЕСТУВАННЯ МОДЕЛІ")
    print("==========================================")
    print(f"🎯 Загальна точність (Accuracy): {accuracy * 100:.2f}%\n")
    print(classification_report(y_test, predictions, target_names=["Збиток (0)", "Профіт (1)"]))

    # 6. ЩО НАЙБІЛЬШЕ ВПЛИВАЄ НА ПРОФІТ?
    print("\n🔍 ВАЖЛИВІСТЬ ФАКТОРІВ (Що працює найкраще):")
    importances = model.feature_importances_

    # Сортуємо фактори від найважливішого до найменш важливого
    feat_importances = sorted(zip(features, importances), key=lambda x: x[1], reverse=True)
    for feat, imp in feat_importances:
        print(f" - {feat}: {imp * 100:.2f}%")

    # 7. ЗБЕРЕЖЕННЯ ГОТОВОГО "ШТУЧНОГО ІНТЕЛЕКТУ"
    model_filename = "rf_trading_model.pkl"
    joblib.dump(model, model_filename)
    print("\n✅ НАВЧАННЯ ЗАВЕРШЕНО!")
    print(f"💾 Модель збережена у файл: {model_filename}")
    print("Цей файл потім перекинемо на сервер до бота.")


if __name__ == "__main__":
    train_trading_model()