import time
import math
import logging
from telebot import types
from notifier import notifier
import bot_state
from risk_manager import RiskManager
from strategies.smc_exponential import SMCExponentialStrategy
from strategies.smc_single import SMCSingleStrategy

logger = logging.getLogger("Bot-Core")

@notifier.bot.message_handler(regexp="⚙️ Снайпери")
def snipers_menu(message):
    show_main_snipers_menu(message.chat.id)

def show_main_snipers_menu(chat_id, message_id=None):
    markup = types.InlineKeyboardMarkup(row_width=1)
    if bot_state.snipers_data["list"]:
        for s_id, cfg in bot_state.snipers_data["list"].items():
            is_active = s_id in bot_state.active_strategies
            prefix = "🟢" if is_active else "🔴"
            rr_val = cfg.get('rr', 2.0)
            tf_val = cfg.get('timeframe', '1m')
            strat_icon = "📶 Сітка" if cfg.get('strat_type', 'exponential') == 'exponential' else "🎯 1 Вхід"
            btn_text = f"{prefix} {cfg['symbol']} (${cfg['position']} | {cfg['leverage']}x | 1:{rr_val} | {tf_val} | {strat_icon})"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"run_sniper_{s_id}"))

    controls = [types.InlineKeyboardButton("➕ Створити", callback_data="build_sniper")]
    if bot_state.snipers_data["list"]:
        controls.append(types.InlineKeyboardButton("✏️ Змінити", callback_data="menu_edit_sniper"))
        markup.row(*controls)
        markup.add(types.InlineKeyboardButton("🗑 Видалити", callback_data="menu_del_sniper"))
    else:
        markup.row(*controls)

    if bot_state.active_strategies:
        markup.add(types.InlineKeyboardButton("🛑 ЗУПИНИТИ ВСІХ", callback_data="stop_all_snipers"))

    markup.add(types.InlineKeyboardButton("🔙 Закрити", callback_data="close_menu"))
    text = "⚙️ <b>Список Снайперів</b>\n🟢 - Працює | 🔴 - Зупинений\nНатисніть на снайпера для запуску/зупинки:"

    try:
        if message_id:
            notifier.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML", reply_markup=markup)
        else:
            notifier.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        pass

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('run_sniper_'))
def callback_run_sniper(call):
    sniper_id = call.data.split('_')[2]
    cfg = bot_state.snipers_data["list"][sniper_id]

    if sniper_id in bot_state.active_strategies:
        strategy = bot_state.active_strategies.pop(sniper_id)
        try: bot_state.safe_cancel_all_orders(strategy.symbol)
        except: pass
        notifier.bot.answer_callback_query(call.id, f"🛑 {cfg['symbol']} зупинено!")
    else:
        total_purchasing_power = cfg["position"] * cfg["leverage"]
        risk_mgr = RiskManager(fixed_capital_usd=total_purchasing_power)
        bot_state.risk_managers[sniper_id] = risk_mgr

        strat_type = cfg.get('strat_type', 'exponential')
        if strat_type == 'single':
            new_strategy = SMCSingleStrategy(client=bot_state.client, risk_manager=risk_mgr, symbol=cfg["symbol"], timeframe=cfg.get("timeframe", "1m"), rr=cfg.get("rr", 2.0), dry_run=bot_state.DRY_RUN)
        elif strat_type == 'bos':
            new_strategy = SMCBOSStrategy(client=bot_state.client, risk_manager=risk_mgr, symbol=cfg["symbol"], timeframe=cfg.get("timeframe", "15m"), rr=cfg.get("rr", 2.0), leverage=cfg["leverage"], dry_run=bot_state.DRY_RUN)
        else:
            new_strategy = SMCExponentialStrategy(client=bot_state.client, risk_manager=risk_mgr, symbol=cfg["symbol"], timeframe=cfg.get("timeframe", "1m"), dry_run=bot_state.DRY_RUN)
            new_strategy.rr = cfg.get("rr", 2.0)

        new_strategy.leverage = cfg["leverage"]
        bot_state.active_strategies[sniper_id] = new_strategy
        notifier.bot.answer_callback_query(call.id, f"▶️ {cfg['symbol']} запущено!")

    show_main_snipers_menu(call.message.chat.id, call.message.message_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'stop_all_snipers')
def stop_all_callback(call):
    bot_state.stop_all_snipers()
    notifier.bot.answer_callback_query(call.id, "🛑 Всі снайпери вимкнені!")
    show_main_snipers_menu(call.message.chat.id, call.message.message_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'build_sniper')
def open_builder_create(call):
    chat_id = call.message.chat.id
    bot_state.user_builder[chat_id] = {'msg_id': call.message.message_id, 'mode': 'create', 'symbol': None, 'position': None, 'leverage': None, 'rr': None, 'timeframe': '1m', 'strat_type': 'exponential'}
    update_builder_menu(chat_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'menu_edit_sniper')
def menu_edit_sniper(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for s_id, cfg in bot_state.snipers_data["list"].items():
        markup.add(types.InlineKeyboardButton(f"✏️ {cfg['symbol']} (${cfg['position']})", callback_data=f"edit_sn_{s_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_snipers"))
    notifier.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="✏️ <b>Оберіть снайпера:</b>", parse_mode="HTML", reply_markup=markup)

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('edit_sn_'))
def callback_edit_sniper(call):
    sniper_id = call.data.split('_')[2]
    cfg = bot_state.snipers_data["list"][sniper_id]
    chat_id = call.message.chat.id
    bot_state.user_builder[chat_id] = {'msg_id': call.message.message_id, 'mode': 'edit', 'sniper_id': sniper_id, 'symbol': cfg['symbol'], 'position': cfg['position'], 'leverage': cfg['leverage'], 'rr': cfg.get('rr', 2.0), 'timeframe': cfg.get('timeframe', '1m'), 'strat_type': cfg.get('strat_type', 'exponential')}
    update_builder_menu(chat_id)

def update_builder_menu(chat_id):
    data = bot_state.user_builder.get(chat_id)
    if not data: return
    markup = types.InlineKeyboardMarkup(row_width=1)

    markup.add(types.InlineKeyboardButton(f"✅ Актив: {data['symbol']}" if data.get('symbol') else "❌ Встановити Актив", callback_data="bld_sym"))
    markup.add(types.InlineKeyboardButton(f"✅ Маржа: ${data['position']}" if data.get('position') else "❌ Встановити Суму", callback_data="bld_pos"))
    markup.add(types.InlineKeyboardButton(f"✅ Плече: {data['leverage']}x" if data.get('leverage') else "❌ Встановити Плече", callback_data="bld_lev"))
    markup.add(types.InlineKeyboardButton(f"✅ R:R = 1:{data['rr']}" if data.get('rr') else "❌ Встановити Risk:Reward", callback_data="bld_rr"))
    markup.add(types.InlineKeyboardButton(f"⏱ Таймфрейм: {data.get('timeframe', '1m')}", callback_data="bld_tf"))
    markup.add(types.InlineKeyboardButton(f"⚙️ Стратегія: {'Сітка (4 ордери)' if data.get('strat_type') == 'exponential' else ('Один вхід' if data.get('strat_type') == 'single' else 'BOS/CHOCH SMC')}", callback_data="bld_strat"))

    if data.get('symbol') and data.get('position') and data.get('leverage') and data.get('rr'):
        markup.add(types.InlineKeyboardButton("💾 Зберегти Снайпера", callback_data="bld_save"))
    markup.add(types.InlineKeyboardButton("🔙 Скасувати", callback_data="bld_cancel"))

    text = f"🛠 <b>Конструктор ({'Створення нового' if data['mode'] == 'create' else 'Редагування'})</b>\n\n"
    if data.get('symbol'):
        try:
            df = bot_state.client.fetch_ohlcv(data['symbol'], '1m', limit=1)
            text += f"📊 <b>Актив:</b> <code>{data['symbol']}</code> (~${float(df.iloc[-1]['close']):.2f})\n"
        except: pass
    text += "\nЗаповніть всі параметри для збереження."
    try: notifier.bot.edit_message_text(chat_id=chat_id, message_id=data['msg_id'], text=text, parse_mode="HTML", reply_markup=markup)
    except: pass

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_strat')
def bld_strat(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("📶 Експоненціальна сітка", callback_data="set_strat_exponential"))
    markup.add(types.InlineKeyboardButton("🎯 Снайперський вхід", callback_data="set_strat_single"))
    markup.add(types.InlineKeyboardButton("🧠 BOS/CHOCH (повний SMC)", callback_data="set_strat_bos"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="bld_strat_back"))
    notifier.bot.edit_message_text("⚙️ <b>Оберіть тип стратегії:</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('set_strat_'))
def process_bld_strat(call):
    bot_state.user_builder[call.message.chat.id]['strat_type'] = call.data.split('_')[2]
    update_builder_menu(call.message.chat.id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_strat_back')
def bld_strat_back(call): update_builder_menu(call.message.chat.id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_sym')
def bld_sym(call):
    msg = notifier.bot.send_message(call.message.chat.id, "Введіть тикер (наприклад: <code>ETH/USDT:USDT</code>):", parse_mode="HTML")
    notifier.bot.register_next_step_handler(msg, lambda m: process_bld_input(m, msg.message_id, 'symbol', lambda x: x.strip().upper()))

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_pos')
def bld_pos(call):
    msg = notifier.bot.send_message(call.message.chat.id, "Введіть суму ВЛАСНОЇ МАРЖІ в доларах (напр. 50):", parse_mode="HTML")
    notifier.bot.register_next_step_handler(msg, lambda m: process_bld_input(m, msg.message_id, 'position', float))

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_lev')
def bld_lev(call):
    msg = notifier.bot.send_message(call.message.chat.id, "Введіть кредитне плече (наприклад: 10):", parse_mode="HTML")
    notifier.bot.register_next_step_handler(msg, lambda m: process_bld_input(m, msg.message_id, 'leverage', int))

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_rr')
def bld_rr(call):
    msg = notifier.bot.send_message(call.message.chat.id, "Введіть значення Risk/Reward (наприклад: <code>2</code>):", parse_mode="HTML")
    notifier.bot.register_next_step_handler(msg, lambda m: process_bld_input(m, msg.message_id, 'rr', float))

def process_bld_input(message, prompt_id, key, cast_func):
    chat_id = message.chat.id
    if chat_id in bot_state.user_builder:
        try: bot_state.user_builder[chat_id][key] = cast_func(message.text)
        except: pass
    try:
        notifier.bot.delete_message(chat_id, message.message_id)
        notifier.bot.delete_message(chat_id, prompt_id)
    except: pass
    update_builder_menu(chat_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_tf')
def bld_tf(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for text, cb in [("🚀 1m -> 15m", "set_bld_tf_1m"), ("📈 5m -> 1h", "set_bld_tf_5m"), ("📊 15m -> 4h", "set_bld_tf_15m"), ("🔋 1h -> 1d", "set_bld_tf_1h")]:
        markup.add(types.InlineKeyboardButton(text, callback_data=cb))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="bld_tf_back"))
    notifier.bot.edit_message_text("⏱ <b>Оберіть таймфрейм:</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('set_bld_tf_'))
def process_bld_tf(call):
    bot_state.user_builder[call.message.chat.id]['timeframe'] = call.data.split('_')[-1]
    update_builder_menu(call.message.chat.id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_tf_back')
def bld_tf_back(call): update_builder_menu(call.message.chat.id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_cancel')
def bld_cancel(call):
    chat_id = call.message.chat.id
    if chat_id in bot_state.user_builder: del bot_state.user_builder[chat_id]
    show_main_snipers_menu(chat_id, call.message.message_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'bld_save')
def bld_save(call):
    chat_id = call.message.chat.id
    data = bot_state.user_builder.get(chat_id)
    if not data: return

    s_data = {"name": f"{data['symbol'].split('/')[0]} Sniper", "symbol": data['symbol'], "position": data['position'], "leverage": data['leverage'], "rr": data['rr'], "timeframe": data.get('timeframe', '1m'), "strat_type": data.get('strat_type', 'exponential')}

    if data['mode'] == 'create':
        bot_state.snipers_data["list"][str(int(time.time()))] = s_data
    else:
        bot_state.snipers_data["list"][data['sniper_id']].update(s_data)
        if data['sniper_id'] in bot_state.active_strategies:
            try: bot_state.safe_cancel_all_orders(bot_state.active_strategies.pop(data['sniper_id']).symbol)
            except: pass

    bot_state.save_snipers(bot_state.snipers_data)
    del bot_state.user_builder[chat_id]
    try: notifier.bot.answer_callback_query(call.id, "✅ Збережено успішно!", show_alert=True)
    except: pass
    show_main_snipers_menu(chat_id, call.message.message_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'back_to_snipers')
def back_to_snipers(call): show_main_snipers_menu(call.message.chat.id, call.message.message_id)

@notifier.bot.callback_query_handler(func=lambda call: call.data == 'menu_del_sniper')
def delete_sniper_list(call):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for s_id, cfg in bot_state.snipers_data["list"].items():
        markup.add(types.InlineKeyboardButton(f"🗑 Видалити {cfg['symbol']}", callback_data=f"del_sniper_{s_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_snipers"))
    notifier.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="❌ <b>Оберіть снайпера для видалення:</b>", parse_mode="HTML", reply_markup=markup)

@notifier.bot.callback_query_handler(func=lambda call: call.data.startswith('del_sniper_'))
def callback_del_sniper(call):
    sniper_id = call.data.split('_')[2]
    if sniper_id in bot_state.active_strategies:
        notifier.bot.answer_callback_query(call.id, "❌ Зупиніть снайпера перед видаленням!", show_alert=True)
        return
    if sniper_id in bot_state.snipers_data["list"]:
        del bot_state.snipers_data["list"][sniper_id]
        bot_state.save_snipers(bot_state.snipers_data)
        notifier.bot.answer_callback_query(call.id, "✅ Видалено!")
        show_main_snipers_menu(call.message.chat.id, call.message.message_id)