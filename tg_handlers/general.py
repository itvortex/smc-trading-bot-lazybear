import os
import time
import logging
from telebot import types
from notifier import notifier, TRADE_DETAILS_CACHE
import bot_state

logger = logging.getLogger("Bot-Core")


# --- ГОЛОВНА КЛАВІАТУРА ---
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    # Снайпери залишаємо як головну окрему кнопку
    markup.add('⚙️ Снайпери')
    # Створюємо наші "папки"
    markup.add('💼 Торгівля', '📊 Дані та Звіти')
    markup.add('🛠 Керування ботом')
    return markup

# --- ПАПКА 1: ТОРГІВЛЯ ---
def get_trade_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('💰 Баланс', '📊 Статус')
    markup.add('📋 Відкриті ордери', '🚫 Скасувати ордери')
    markup.add('🔙 Головне меню')
    return markup

# --- ПАПКА 2: ДАНІ ТА ЗВІТИ ---
def get_data_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('📖 Історія', '🔄 Синхронізація')
    markup.add('📈 Аналітика', '📂 Файли сервера')
    markup.add('🔙 Головне меню')
    return markup

# --- ПАПКА 3: КЕРУВАННЯ ---
def get_settings_keyboard():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('▶️ Продовжити', '⏸ Пауза')
    markup.add('🛑 Вимкнути бота')
    markup.add('🔙 Головне меню')
    return markup

# ==========================================
# ОБРОБНИКИ НАВІГАЦІЇ (ВКЛАДЕНІ МЕНЮ)
# ==========================================

@notifier.bot.message_handler(func=lambda message: message.text == '💼 Торгівля')
def trade_menu(message):
    notifier.bot.send_message(
        message.chat.id,
        "💼 <b>Розділ Торгівлі</b>\nОберіть потрібну дію:",
        parse_mode="HTML",
        reply_markup=get_trade_keyboard()
    )

@notifier.bot.message_handler(func=lambda message: message.text == '📊 Дані та Звіти')
def data_menu(message):
    notifier.bot.send_message(
        message.chat.id,
        "📊 <b>Аналітика та Дані</b>\nОберіть потрібний звіт:",
        parse_mode="HTML",
        reply_markup=get_data_keyboard()
    )

@notifier.bot.message_handler(func=lambda message: message.text == '🛠 Керування ботом')
def settings_menu(message):
    notifier.bot.send_message(
        message.chat.id,
        "🛠 <b>Керування системою</b>\nОберіть команду:",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard()
    )

@notifier.bot.message_handler(func=lambda message: message.text == '🔙 Головне меню')
def back_to_main(message):
    notifier.bot.send_message(
        message.chat.id,
        "🏠 <b>Головне меню</b>\nЩо будемо робити далі?",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )

def get_close_button(text="🔙 Закрити"):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text, callback_data="close_menu"))
    return markup

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'close_menu')
def close_menu_callback(call):
    try:
        notifier.bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception:
        pass

@notifier.bot.message_handler(commands=['start'])
def start_cmd(message):
    mode_text = "🟢 БОЙОВИЙ (ГРОШІ)" if not bot_state.DRY_RUN else "🧪 ХОЛОСТИЙ (БЕЗ РИЗИКУ)"
    text = (
        f"👋 <b>Quant Bot активований.</b>\n\n"
        f"⚙️ <b>Поточний режим:</b> {mode_text}\n\n"
        f"📁 <b>ТОРГІВЛЯ ТА ІНФО:</b>\n"
        f"⚙️ <b>Снайпери</b> — Налаштування та вибір активних монет.\n"
        f"📊 <b>Статус</b> — Перевірка поточних відкритих угод.\n"
        f"💰 <b>Баланс</b> — Твій доступний капітал на OKX.\n"
        f"📖 <b>Історія</b> / 🔄 <b>Синхронізація</b> — Журнал останніх закритих позицій.\n"
        f"📈 <b>Аналітика</b> — Детальний звіт, WinRate та PnL.\n"
        f"🚫 <b>Скасувати ордери</b> — Екстрене видалення всіх ліміток.\n\n"
        f"🎛 <b>КЕРУВАННЯ БОТОМ:</b>\n"
        f"▶️ <b>Продовжити</b> / ⏸ <b>Пауза</b> — Запуск або призупинення сканування.\n"
        f"🛑 <b>Вимкнути бота</b> — Повна зупинка сервера."
    )
    notifier.bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=get_main_keyboard())

@notifier.bot.message_handler(commands=['balance'])
@notifier.bot.message_handler(func=lambda message: message.text == '💰 Баланс')
def balance_cmd(message):
    try:
        balance = bot_state.client.fetch_balance("USDT")
        text = f"📊 <b>Звіт по акаунту:</b>\n💰 Баланс: <code>{balance:.2f} USDT</code>\n"
        text += f"🟢 Активних снайперів: {len(bot_state.active_strategies)}"
        notifier.bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=get_close_button("🔙 Приховати"))
    except Exception as e:
        notifier.bot.send_message(message.chat.id, f"❌ Помилка: <code>{e}</code>", parse_mode="HTML")

@notifier.bot.message_handler(commands=['status'])
@notifier.bot.message_handler(func=lambda message: message.text == '📊 Статус')
def status_cmd(message):
    if not bot_state.active_strategies:
        notifier.bot.send_message(message.chat.id, "📭 Жоден снайпер не запущений.", parse_mode="HTML", reply_markup=get_close_button())
        return

    msg_wait = notifier.bot.send_message(message.chat.id, "⏳ <b>Отримую дані з біржі OKX...</b>", parse_mode="HTML")
    full_text = "📡 <b>СТАТУС СНАЙПЕРІВ:</b>\n━━━━━━━━━━━━━━━\n"

    for s_id, strategy in bot_state.active_strategies.items():
        try:
            positions = bot_state.client.exchange.fetch_positions([strategy.symbol])
            active_positions = [p for p in positions if p.get('contracts', 0) and float(p['contracts']) > 0]

            if not active_positions:
                full_text += f"🔍 <b>{strategy.symbol}</b> ({strategy.timeframe}): Сканує ринок...\n\n"
                continue

            pos = active_positions[0]
            side = str(pos.get('side', 'N/A')).upper()
            entry_price = float(pos.get('entryPrice', 0))
            mark_price = float(pos.get('markPrice', 0))
            pnl = float(pos.get('unrealizedPnl', 0))
            roe = float(pos.get('percentage', 0)) if pos.get('percentage') else float(pos.get('info', {}).get('uplRatio', 0)) * 100

            emoji_side = "🟢 LONG" if side == "LONG" else "🔴 SHORT"
            emoji_pnl = "🟩" if pnl > 0 else "🟥"

            full_text += (
                f"{emoji_side} <b>{strategy.symbol}</b>\n"
                f"🎯 Вхід: <code>{entry_price:.4f}</code> | 🔖 Марк: <code>{mark_price:.4f}</code>\n"
                f"💰 PnL: {emoji_pnl} <b>{pnl:.2f} USDT</b> ({roe:.2f}%)\n"
            )

            if getattr(strategy, 'pending_trade', None):
                t = strategy.pending_trade
                full_text += f"✅ TP: <code>{t.get('tp', 'N/A')}</code> | 🛑 SL: <code>{t.get('sl', 'N/A')}</code>\n"
            full_text += "━━━━━━━━━━━━━━━\n"

        except Exception as e:
            full_text += f"❌ Помилка для {strategy.symbol}: {e}\n━━━━━━━━━━━━━━━\n"

    notifier.bot.edit_message_text(chat_id=message.chat.id, message_id=msg_wait.message_id, text=full_text, parse_mode="HTML", reply_markup=get_close_button())

@notifier.bot.message_handler(func=lambda message: message.text == '📋 Відкриті ордери')
def open_orders_cmd(message):
    if not bot_state.active_strategies:
        notifier.bot.send_message(message.chat.id, "📭 Жоден снайпер не запущений.", parse_mode="HTML", reply_markup=get_close_button())
        return

    msg_wait = notifier.bot.send_message(message.chat.id, "⏳ <b>Збираю дані про ордери...</b>", parse_mode="HTML")
    full_text = "📋 <b>АКТИВНІ ОРДЕРИ:</b>\n━━━━━━━━━━━━━━━\n"

    found_any = False
    for s_id, strategy in bot_state.active_strategies.items():
        try:
            open_orders = bot_state.client.exchange.fetch_open_orders(strategy.symbol)
            try:
                algo_orders = bot_state.client.exchange.fetch_open_orders(strategy.symbol, params={'stop': True})
            except:
                algo_orders = []

            all_orders = open_orders + algo_orders

            if not all_orders: continue

            found_any = True
            full_text += f"💠 <b>{strategy.symbol}:</b>\n"

            for o in all_orders:
                side = o.get('side', '').upper()
                qty = o.get('amount', 0)
                price = o.get('price') or o.get('stopPrice') or o.get('info', {}).get('triggerPx', 'N/A')

                is_reduce = o.get('reduceOnly', False) or o.get('info', {}).get('reduceOnly') == 'true'

                if is_reduce:
                    marker = "🛑 Захист позиції"
                    if strategy.pending_trade:
                        tp_val = float(strategy.pending_trade.get('tp', 0))
                        sl_val = float(strategy.pending_trade.get('sl', 0))
                        pr_val = float(price) if price != 'N/A' else 0

                        if pr_val == tp_val: marker = "🎯 Тейк-Профіт"
                        elif pr_val == sl_val: marker = "🛑 Стоп-Лос"
                        elif pr_val == float(strategy.pending_trade.get('avg_entry', 0)): marker = "🛡️ Безубиток"

                    full_text += f"  └ {marker} | {side} | {qty} контр. | Ціна: {price}\n"
                else:
                    full_text += f"  └ 🟢 Ліміт на Вхід | {side} | {qty} контр. | Ціна: {price}\n"

            full_text += "━━━━━━━━━━━━━━━\n"
        except Exception as e:
            full_text += f"❌ Помилка для {strategy.symbol}: {e}\n━━━━━━━━━━━━━━━\n"

    if not found_any: full_text += "📭 На біржі немає активних ордерів."

    notifier.bot.edit_message_text(chat_id=message.chat.id, message_id=msg_wait.message_id, text=full_text, parse_mode="HTML", reply_markup=get_close_button())

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('info'))
def show_trade_details(call):
    try:
        trade_id = call.data.split('|')[1]
        details = TRADE_DETAILS_CACHE.get(trade_id, "❌ Дані про цю угоду втрачені.")
        notifier.bot.send_message(call.message.chat.id, details, parse_mode='HTML', reply_markup=get_close_button("🔙 Сховати деталі"))
        notifier.bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Помилка обробки кнопки: {e}")

@notifier.bot.message_handler(commands=['cancel'])
@notifier.bot.message_handler(func=lambda message: message.text == '🚫 Скасувати ордери')
def cancel_cmd(message):
    if not bot_state.active_strategies:
        notifier.bot.send_message(message.chat.id, "📭 <b>Жоден снайпер не запущений.</b>\nНемає ордерів для скасування.", parse_mode="HTML", reply_markup=get_close_button())
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for s_id, strategy in bot_state.active_strategies.items():
        markup.add(types.InlineKeyboardButton(f"🚫 Скасувати {strategy.symbol}", callback_data=f"canx_{s_id}"))

    if len(bot_state.active_strategies) > 1:
        markup.add(types.InlineKeyboardButton("🛑 Скасувати ВСІ", callback_data="canx_all"))

    markup.add(types.InlineKeyboardButton("🔙 Закрити", callback_data="close_menu"))
    notifier.bot.send_message(message.chat.id, "❓ <b>Оберіть актив для скасування:</b>", parse_mode="HTML", reply_markup=markup)

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('canx_'))
def callback_cancel_sniper(call):
    action = call.data[len("canx_"):]
    final_text = ""
    try:
        if action == 'all':
            bot_state.stop_all_snipers()
            final_text = "✅ <b>Всі активні снайпери зупинені, ордери видалено.</b>"
        else:
            s_id = action
            if s_id in bot_state.active_strategies:
                try:
                    bot_state.stop_sniper(s_id)  # зберігає стан і скасовує ордери
                    final_text = f"✅ <b>Снайпер {s_id} зупинено.</b>\nВсі ордери видалено з біржі."
                except Exception as e:
                    final_text = f"⚠️ Снайпер зупинений у боті, але виникла помилка на біржі: {e}"
            else:
                notifier.bot.answer_callback_query(call.id, "❌ Вже виконано або снайпер неактивний.")
                return

        notifier.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=final_text, parse_mode="HTML", reply_markup=get_close_button())
    except Exception as e:
        logger.error(f"Critical Cancel Error: {e}")
    finally:
        try: notifier.bot.answer_callback_query(call.id)
        except: pass

@notifier.bot.message_handler(commands=['pause'])
@notifier.bot.message_handler(func=lambda message: message.text == '⏸ Пауза')
def pause_cmd(message):
    bot_state.GLOBAL_RUNNING = False
    notifier.bot.send_message(message.chat.id, "⏸ <b>Сканування ринку ПРИЗУПИНЕНО.</b> Бот перейшов у сплячий режим.", parse_mode="HTML")

@notifier.bot.message_handler(commands=['resume'])
@notifier.bot.message_handler(func=lambda message: message.text == '▶️ Продовжити')
def resume_cmd(message):
    bot_state.GLOBAL_RUNNING = True
    notifier.bot.send_message(message.chat.id, "▶️ <b>Сканування ринку ВІДНОВЛЕНО.</b>", parse_mode="HTML")

@notifier.bot.message_handler(commands=['stop', 'kill'])
@notifier.bot.message_handler(func=lambda message: message.text == '🛑 Вимкнути бота')
def stop_cmd(message):
    try:
        notifier.bot.send_message(message.chat.id, "🛑 <b>Вимикаю сервер...</b>", parse_mode="HTML")
        bot_state.stop_all_snipers()
        # ⚠️ trade_history.csv НЕ видаляємо — це важливі торгові дані!
        # Якщо потрібно очистити вручну — завантажте файл через 📂 Файли сервера
        time.sleep(1)
        os._exit(0)
    except Exception:
        os._exit(1)


# ==========================================
# БЛОК ЗАВАНТАЖЕННЯ ФАЙЛІВ З СЕРВЕРА
# ==========================================

@notifier.bot.message_handler(commands=['files'])
@notifier.bot.message_handler(func=lambda message: message.text == '📂 Файли сервера')
def send_files_menu(message):
    """Відправляє меню з вибором файлів для завантаження"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📜 Логи (bot_activity.log)", callback_data="download_log"),
        types.InlineKeyboardButton("📊 Історія (trade_history.csv)", callback_data="download_csv"),
        types.InlineKeyboardButton("⚙️ Снайпери (snipers.json)", callback_data="download_json"),
        types.InlineKeyboardButton("🔙 Закрити", callback_data="close_menu")
    )
    notifier.bot.send_message(
        message.chat.id,
        "🗄 <b>Доступні файли на сервері:</b>\nОберіть файл, який хочете завантажити:",
        parse_mode="HTML",
        reply_markup=markup
    )


@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('download_'))
def download_file_callback(call):
    """Обробляє натискання на кнопки завантаження файлів"""
    # Словник, який пов'язує кнопку з реальною назвою файлу
    file_map = {
        'download_log': 'bot_activity.log',
        'download_csv': 'trade_history.csv',
        'download_json': 'snipers.json',
        'download_ml': 'ml_training_data.csv'
    }

    file_name = file_map.get(call.data)

    if file_name:
        # Перевіряємо, чи існує файл на сервері
        if os.path.exists(file_name):
            msg_wait = notifier.bot.send_message(call.message.chat.id, f"⏳ Завантажую <b>{file_name}</b>...",
                                                 parse_mode="HTML")
            try:
                # Відкриваємо файл у режимі читання байтів ('rb') і відправляємо
                with open(file_name, 'rb') as f:
                    notifier.bot.send_document(call.message.chat.id, f)
                notifier.bot.delete_message(call.message.chat.id, msg_wait.message_id)
                try: notifier.bot.answer_callback_query(call.id)
                except: pass
            except Exception as e:
                logger.error(f"Помилка відправки файлу {file_name}: {e}")
                notifier.bot.edit_message_text(f"❌ Помилка відправки: {e}", call.message.chat.id, msg_wait.message_id)
        else:
            # Якщо файлу ще не існує (наприклад, бот ще нічого не записав у логи)
            notifier.bot.answer_callback_query(call.id, f"📭 Файл {file_name} ще не створено або порожній!",
                                               show_alert=True)