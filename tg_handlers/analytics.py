import os
import csv
import logging
from datetime import datetime
from telebot import types
from notifier import notifier
import bot_state
from history_analyzer import HistoryAnalyzer
from tg_handlers.general import get_close_button

logger = logging.getLogger("Bot-Core")


@notifier.bot.message_handler(commands=['history'])
@notifier.bot.message_handler(func=lambda message: message.text == '📖 Історія')
def history_cmd(message):
    if not getattr(bot_state, 'is_synced', False):
        notifier.bot.send_message(
            message.chat.id,
            "⚠️ <b>Дані не синхронізовані!</b>\n\nБудь ласка, спочатку натисніть кнопку <b>🔄 Синхронізація</b>, щоб завантажити актуальні угоди з біржі.",
            parse_mode="HTML"
        )
        return

    try:
        if not os.path.exists('trade_history.csv'):
            notifier.bot.send_message(message.chat.id, "📭 <b>Історія порожня.</b>", parse_mode="HTML")
            return

        with open('trade_history.csv', mode='r', encoding='utf-8') as f:
            reader = list(csv.reader(f))
            valid_trades = [row for row in (reader[1:] if len(reader) > 1 else []) if
                            row and any(field.strip() for field in row)]
            if not valid_trades: return notifier.bot.send_message(message.chat.id,
                                                                  "📭 <b>У журналі ще немає записів.</b>",
                                                                  parse_mode="HTML")

            try:
                valid_trades.sort(key=lambda x: datetime.strptime(x[1], "%Y-%m-%d %H:%M"), reverse=True)
            except:
                pass

            msg = "📖 <b>Останні 5 закритих позицій:</b>\n━━━━━━━━━━━━━━━\n"
            for row in valid_trades[:5]:
                if len(row) < 8: continue
                # Структура: PosId(0), Date(1), Symbol(2), Action(3), Leverage(4), Margin(5), ROE(6), PnL(7), Status(8)
                date, sym, action = row[1], row[2], row[3]
                roe  = row[6].strip() if len(row) > 6 else ""
                pnl  = row[7].replace('$', '').strip() if len(row) > 7 else "0"
                status = row[8].strip() if len(row) > 8 else "Unknown"
                emoji = "✅" if status == "Success" else ("🛡️" if status == "Break-Even" else "🛑")
                msg += f"{emoji} <b>{action} {sym}</b> | 🕒 {date}\n   💰 PnL: <b>{pnl}</b> {roe}\n\n"

            notifier.bot.send_message(message.chat.id, msg, parse_mode="HTML",
                                      reply_markup=get_close_button("🔙 Сховати історію"))
    except Exception as e:
        logger.error(f"Помилка у команді /history: {e}")


@notifier.bot.message_handler(commands=['sync'])
@notifier.bot.message_handler(func=lambda message: message.text == '🔄 Синхронізація')
def sync_history_cmd(message):
    notifier.bot.send_message(message.chat.id, "⏳ Звертаюсь до бази даних OKX...", parse_mode="HTML")
    try:
        history_data = bot_state.client.fetch_position_history()  # Завантажує ВСЮ історію
        if not history_data:
            return notifier.bot.send_message(
                message.chat.id, "📭 На біржі немає закритих позицій.", reply_markup=get_close_button()
            )

        history_data.reverse()

        # --- ДЕДУБЛІКАЦІЯ ПО PosId з підтримкою старого формату CSV ---
        existing_pos_ids = set()
        file_exists = os.path.isfile('trade_history.csv')
        has_pos_id_column = False

        if file_exists:
            with open('trade_history.csv', mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, [])
                has_pos_id_column = 'PosId' in header

                if has_pos_id_column:
                    pos_id_col = header.index('PosId')
                    for r in reader:
                        if len(r) > pos_id_col and r[pos_id_col].strip():
                            existing_pos_ids.add(r[pos_id_col].strip())
                else:
                    # Старий формат без PosId — читаємо всі рядки щоб не загубити
                    # дедублікацію робимо по (Date, Symbol, Action)
                    logger.info("⚠️ Старий формат CSV (без PosId) — міграція при наступному записі.")
                    for r in reader:
                        if len(r) >= 3:
                            existing_pos_ids.add(f"{r[0]}|{r[1]}|{r[2]}")

        added_count = 0

        # Якщо старий формат — спочатку переписуємо файл з новим заголовком
        if file_exists and not has_pos_id_column:
            logger.info("🔄 Міграція CSV: додаємо колонку PosId...")
            rows_backup = []
            with open('trade_history.csv', mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, [])
                for r in reader:
                    if r and any(field.strip() for field in r):
                        rows_backup.append(r)

            with open('trade_history.csv', mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['PosId', 'Date', 'Symbol', 'Action', 'Leverage',
                                 'Margin_USD', 'ROE_Percent', 'PnL_USD', 'Status'])
                for r in rows_backup:
                    writer.writerow([''] + r)  # Додаємо порожній PosId для старих рядків
            has_pos_id_column = True
            logger.info(f"✅ Мігровано {len(rows_backup)} старих записів.")

        with open('trade_history.csv', mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Новий файл — пишемо заголовок
            if not file_exists:
                writer.writerow(['PosId', 'Date', 'Symbol', 'Action', 'Leverage',
                                 'Margin_USD', 'ROE_Percent', 'PnL_USD', 'Status'])

            for t in history_data:
                pos_id = str(t.get('PosId', '')).strip()

                # Перевірка дублікату
                if pos_id and pos_id in existing_pos_ids:
                    continue
                # Для старого формату — перевіряємо по (Date, Symbol, Action)
                if not pos_id:
                    fallback_key = f"{t['Date']}|{t['Symbol']}|{t['Action']}"
                    if fallback_key in existing_pos_ids:
                        continue
                    existing_pos_ids.add(fallback_key)

                writer.writerow([
                    pos_id,
                    t['Date'], t['Symbol'], t['Action'],
                    f"{t['Leverage']}x", round(t['Margin_USD'], 2),
                    f"{round(t['ROE_Percent'], 2)}%", round(t['PnL_USD'], 2),
                    t['Status']
                ])
                if pos_id:
                    existing_pos_ids.add(pos_id)
                added_count += 1

        bot_state.is_synced = True
        notifier.bot.send_message(
            message.chat.id,
            f"✅ Синхронізація успішна!\nДодано нових угод: <b>{added_count}</b>",
            parse_mode="HTML", reply_markup=get_close_button()
        )
    except Exception as e:
        logger.error(f"Помилка синхронізації: {e}")
        notifier.bot.send_message(message.chat.id, "❌ Помилка синхронізації з OKX.", reply_markup=get_close_button())


# =========================================================================
# БЛОК АНАЛІТИКИ ТА ФІЛЬТРІВ
# =========================================================================

def get_global_analytics_menu(period='all'):
    # Мапа періодів у днях
    days_map = {'1d': 1, '7d': 7, '14d': 14, '30d': 30, 'all': None}
    days = days_map.get(period)

    # Ініціалізуємо аналізатор з фільтром по днях
    analyzer = HistoryAnalyzer(days=days)
    stats = analyzer.get_overall_stats()

    period_text = {
        '1d': 'за 24 години',
        '7d': 'за Тиждень',
        '14d': 'за 2 Тижні',
        '30d': 'за Місяць',
        'all': 'за Весь період'
    }[period]

    markup = types.InlineKeyboardMarkup(row_width=3)

    # 1. Створюємо панель вибору часу
    btn_1d = types.InlineKeyboardButton(f"{'✅ ' if period == '1d' else ''}1 Ден", callback_data="stat_time_1d")
    btn_7d = types.InlineKeyboardButton(f"{'✅ ' if period == '7d' else ''}1 Тиж", callback_data="stat_time_7d")
    btn_14d = types.InlineKeyboardButton(f"{'✅ ' if period == '14d' else ''}2 Тиж", callback_data="stat_time_14d")
    btn_30d = types.InlineKeyboardButton(f"{'✅ ' if period == '30d' else ''}1 Міс", callback_data="stat_time_30d")
    btn_all = types.InlineKeyboardButton(f"{'✅ ' if period == 'all' else ''}Весь час", callback_data="stat_time_all")

    markup.add(btn_1d, btn_7d, btn_14d)
    markup.row(btn_30d, btn_all)

    # Якщо за обраний час немає угод
    if stats.get('Усього угод', 0) == 0:
        text = f"📭 <b>Немає закритих угод {period_text}.</b>\nОберіть інший період на панелі вище."
        markup.add(types.InlineKeyboardButton("🔙 Закрити", callback_data="close_menu"))
        return text, markup

    text = f"📈 <b>ГЛОБАЛЬНА АНАЛІТИКА ({period_text})</b>\n━━━━━━━━━━━━━━━\n"
    text += f"📊 <b>Усього угод:</b> {stats['Усього угод']}\n🏆 <b>WinRate:</b> {stats['WinRate %']}%\n"
    text += f"💰 <b>Загальний PnL:</b> {'🟩' if stats['PnL'] > 0 else '🟥'} {stats['PnL']}$\n💵 <b>Середній PnL:</b> {stats['Середній PnL']}$\n"
    text += f"📦 <b>Сер. Маржа:</b> {stats['Сер. Маржа']}$\n⚖️ <b>Сер. Плече:</b> {stats['Сер. Плече']}x\n📈 <b>Сер. ROE:</b> {stats['Сер. ROE %']}%\n\n🔍 <b>Фільтр по монетах:</b>"

    # 2. Кнопки монет (передаємо обраний період у callback)
    coins_report = analyzer.get_grouped_report('Symbol')
    if coins_report is not None and not coins_report.empty:
        coin_buttons = []
        for index, row in coins_report.iterrows():
            clean_sym = str(row['Symbol']).split('/')[0]
            coin_buttons.append(
                types.InlineKeyboardButton(f"🪙 {clean_sym}", callback_data=f"stat_sym_{period}_{clean_sym}"))

        for i in range(0, len(coin_buttons), 2):
            markup.row(*coin_buttons[i:i + 2])

    # 3. Кнопки напрямків (передаємо обраний період)
    markup.row(types.InlineKeyboardButton("🟢 LONG", callback_data=f"stat_act_{period}_LONG"),
               types.InlineKeyboardButton("🔴 SHORT", callback_data=f"stat_act_{period}_SHORT"))
    markup.add(types.InlineKeyboardButton("🔙 Закрити", callback_data="close_menu"))
    return text, markup


@notifier.bot.message_handler(commands=['analytics'])
@notifier.bot.message_handler(func=lambda message: message.text == '📈 Аналітика')
def analytics_cmd(message):
    if not getattr(bot_state, 'is_synced', False):
        notifier.bot.send_message(
            message.chat.id,
            "⚠️ <b>Дані не синхронізовані!</b>\n\nБудь ласка, спочатку натисніть кнопку <b>🔄 Синхронізація</b> для точного аналізу.",
            parse_mode="HTML"
        )
        return

    msg_wait = notifier.bot.send_message(message.chat.id, "⏳ <b>Готую аналітичний звіт...</b>", parse_mode="HTML")
    try:
        # При першому відкритті показуємо "Весь час"
        text, markup = get_global_analytics_menu(period='all')
        notifier.bot.edit_message_text(chat_id=message.chat.id, message_id=msg_wait.message_id, text=text,
                                       parse_mode="HTML", reply_markup=markup)
    except Exception:
        notifier.bot.edit_message_text("❌ Помилка. Перевірте логи.", chat_id=message.chat.id,
                                       message_id=msg_wait.message_id, reply_markup=get_close_button())


# --- ОБРОБНИКИ КНОПОК ---

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('stat_time_'))
def stat_time_callback(call):
    """Обробка натискання на кнопки часу"""
    period = call.data.replace('stat_time_', '')
    text, markup = get_global_analytics_menu(period)
    notifier.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=text,
                                   parse_mode="HTML", reply_markup=markup)


@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('stat_sym_'))
def stat_sym_callback(call):
    """Статистика по конкретній монеті з урахуванням часу"""
    parts = call.data.split('_')  # Наприклад: stat_sym_7d_BTC
    period = parts[2]
    coin = parts[3]

    days_map = {'1d': 1, '7d': 7, '14d': 14, '30d': 30, 'all': None}
    analyzer = HistoryAnalyzer(days=days_map.get(period))
    stats = analyzer.analyze_by_symbol(coin)

    period_text = {'1d': '24 години', '7d': 'Тиждень', '14d': '2 Тижні', '30d': 'Місяць', 'all': 'Весь час'}[period]
    show_analytics(call, stats, f"🪙 <b>АНАЛІТИКА {coin} ({period_text})</b>", period)


@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('stat_act_'))
def stat_act_callback(call):
    """Статистика по LONG/SHORT з урахуванням часу"""
    parts = call.data.split('_')  # Наприклад: stat_act_all_LONG
    period = parts[2]
    action = parts[3]

    days_map = {'1d': 1, '7d': 7, '14d': 14, '30d': 30, 'all': None}
    analyzer = HistoryAnalyzer(days=days_map.get(period))
    stats = analyzer.analyze_by_action(action)

    period_text = {'1d': '24 години', '7d': 'Тиждень', '14d': '2 Тижні', '30d': 'Місяць', 'all': 'Весь час'}[period]
    title = f"{'🟢' if action == 'LONG' else '🔴'} <b>АНАЛІТИКА {action} ({period_text})</b>"
    show_analytics(call, stats, title, period)


def show_analytics(call, stats, title, period):
    """Шаблон виводу результатів з кнопкою повернення у правильний період"""
    if stats['Усього угод'] == 0:
        text = f"{title}\n━━━━━━━━━━━━━━━\n📭 Немає угод за цей період."
    else:
        text = f"{title}\n━━━━━━━━━━━━━━━\n📊 <b>Усього угод:</b> {stats['Усього угод']}\n🏆 <b>WinRate:</b> {stats['WinRate %']}%\n"
        text += f"💰 <b>Загальний PnL:</b> {'🟩' if stats['PnL'] > 0 else '🟥'} {stats['PnL']}$\n💵 <b>Середній PnL:</b> {stats['Середній PnL']}$\n"
        text += f"📦 <b>Сер. Маржа:</b> {stats['Сер. Маржа']}$\n⚖️ <b>Сер. Плече:</b> {stats['Сер. Плече']}x\n📈 <b>Сер. ROE:</b> {stats['Сер. ROE %']}%\n"

    # Кнопка "Назад" запам'ятовує період!
    markup = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("🔙 Назад до Глобальної", callback_data=f"stat_global_{period}")
    )
    notifier.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=text,
                                   parse_mode="HTML", reply_markup=markup)


@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('stat_global_'))
def stat_global_callback(call):
    """Повернення з детальної статистики до глобального меню"""
    period = call.data.replace('stat_global_', '')
    text, markup = get_global_analytics_menu(period)
    notifier.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=text,
                                   parse_mode="HTML", reply_markup=markup)