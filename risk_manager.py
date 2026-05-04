import math
import logging

logger = logging.getLogger("Bot-Core")


class Order:
    def __init__(self, price: float, amount: float):
        self.price = price
        self.amount = amount

    def __repr__(self):
        return f"Order(price={self.price}, amount={self.amount})"


class RiskManager:
    # Fallback-значення якщо біржа недоступна або символ не знайдено
    CONTRACT_MULTIPLIERS = {
        "BTC/USDT:USDT": 0.01,
        "ETH/USDT:USDT": 0.1,
        "SOL/USDT:USDT": 1.0,
        "XRP/USDT:USDT": 100,
        "DOGE/USDT:USDT": 10.0,
        "LINK/USDT:USDT": 10,
    }

    PRICE_PRECISION = {
        "BTC": 1,
        "ETH": 2,
        "XRP": 4,
        "DOGE": 5,
    }
    DEFAULT_PRICE_PRECISION = 3

    def __init__(self, fixed_capital_usd: float, contract_size: float = None, symbol: str = None):
        """
        fixed_capital_usd — капітал у USDT
        contract_size     — реальний розмір контракту з біржі (береться при створенні снайпера)
        symbol            — символ для fallback якщо contract_size не передано
        """
        if fixed_capital_usd <= 0:
            raise ValueError(f"fixed_capital_usd має бути > 0, отримано: {fixed_capital_usd}")
        self.fixed_capital_usd = fixed_capital_usd
        self._symbol = symbol

        # Пріоритет: 1) передано явно → 2) fallback зі статичного словника → 3) 1.0
        if contract_size is not None and contract_size > 0:
            self._contract_size = contract_size
            logger.debug(f"✅ RiskManager: contract_size={contract_size} (з біржі)")
        elif symbol and symbol in self.CONTRACT_MULTIPLIERS:
            self._contract_size = self.CONTRACT_MULTIPLIERS[symbol]
            logger.warning(f"⚠️ RiskManager: contract_size для {symbol} взято з fallback-словника ({self._contract_size}). Рекомендується оновити снайпера.")
        else:
            self._contract_size = 1.0
            logger.warning(f"⚠️ RiskManager: contract_size невідомий для {symbol}, використовується 1.0. Розрахунки можуть бути некоректні!")

    def _get_multiplier(self, symbol: str) -> float:
        """Повертає розмір контракту. Завжди бере з self._contract_size (встановленого при ініціалізації)."""
        return self._contract_size

    def _get_price_precision(self, symbol: str) -> int:
        for key, precision in self.PRICE_PRECISION.items():
            if key in symbol:
                return precision
        return self.DEFAULT_PRICE_PRECISION

    def can_afford(self, client, required_margin_usd: float, buffer_pct: float = 0.05) -> bool:
        try:
            balance = client.fetch_balance()
            needed = required_margin_usd * (1 + buffer_pct)
            if balance < needed:
                logger.warning(
                    f"💸 Недостатньо коштів: баланс={balance:.2f} USDT, "
                    f"потрібно={needed:.2f} USDT (з буфером {buffer_pct*100:.0f}%)"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Помилка перевірки балансу: {e}")
            return False

    def validate_sniper_config(self, symbol: str, current_price: float,
                                capital_usd: float, leverage: int,
                                num_orders: int = 1) -> dict:
        """
        ФІК #6: Валідація конфігурації снайпера при налаштуванні.

        Перевіряє чи вистачить капіталу з обраним плечем для виставлення
        хоча б одного мінімального контракту.

        Повертає dict:
          {
            'ok': bool,           — чи можна запускати снайпер
            'message': str,       — текст для відображення користувачу
            'contracts': int,     — скільки контрактів вийде
            'margin_per_contract': float,  — маржа на 1 контракт
            'total_margin': float,         — загальна маржа
            'min_capital_needed': float,   — мінімально потрібний капітал
          }
        """
        multiplier = self._get_multiplier(symbol)

        # Вартість 1 контракту без плеча
        contract_value_usd = current_price * multiplier

        # Маржа на 1 контракт з урахуванням плеча
        margin_per_contract = contract_value_usd / leverage

        # Мінімально потрібний капітал для 1 контракту (+ 5% буфер на комісії)
        min_capital_needed = margin_per_contract * 1.05

        # Скільки контрактів вийде
        total_contracts = math.floor(capital_usd / margin_per_contract)

        # Загальна маржа яка буде використана
        total_margin = total_contracts * margin_per_contract

        if total_contracts < 1:
            # ФІК #6: формуємо зрозуміле повідомлення про помилку
            min_leverage_needed = math.ceil(contract_value_usd / capital_usd)
            min_capital_for_current_leverage = margin_per_contract * 1.05

            message = (
                f"❌ <b>Недостатньо капіталу для запуску снайпера!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 <b>Монета:</b> {symbol}\n"
                f"💵 <b>Ціна монети:</b> ${current_price:,.4f}\n"
                f"📦 <b>Розмір 1 контракту:</b> ${contract_value_usd:.2f}\n"
                f"⚖️ <b>Обране плече:</b> {leverage}x\n"
                f"💰 <b>Ваш капітал:</b> ${capital_usd:.2f}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔴 <b>Проблема:</b>\n"
                f"Маржа на 1 контракт = ${margin_per_contract:.2f}\n"
                f"(${contract_value_usd:.2f} ÷ {leverage}x плече)\n\n"
                f"Ваш капітал <b>${capital_usd:.2f}</b> менший за маржу "
                f"на мінімальний контракт <b>${min_capital_for_current_leverage:.2f}</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💡 <b>Щоб виправити — оберіть один варіант:</b>\n"
                f"  • Збільшіть капітал до <b>${min_capital_for_current_leverage:.2f}+</b>\n"
                f"  • Або збільшіть плече до <b>{min_leverage_needed}x+</b>"
            )
            return {
                'ok': False,
                'message': message,
                'contracts': 0,
                'margin_per_contract': round(margin_per_contract, 2),
                'total_margin': 0,
                'min_capital_needed': round(min_capital_needed, 2),
            }

        if num_orders > 1 and total_contracts < num_orders:
            # Є контракти але менше ніж потрібно для сітки
            min_capital_for_grid = margin_per_contract * num_orders * 1.05
            message = (
                f"⚠️ <b>Капіталу вистачає але не для повної сітки!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📊 <b>Монета:</b> {symbol}\n"
                f"⚖️ <b>Плече:</b> {leverage}x | 💰 <b>Капітал:</b> ${capital_usd:.2f}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Контрактів доступно: <b>{total_contracts}</b>\n"
                f"Потрібно для сітки ({num_orders} ордерів): <b>{num_orders}</b>\n\n"
                f"💡 Для повної сітки потрібно <b>${min_capital_for_grid:.2f}+</b>\n"
                f"Або зменшіть кількість ордерів до <b>{total_contracts}</b>"
            )
            return {
                'ok': False,
                'message': message,
                'contracts': total_contracts,
                'margin_per_contract': round(margin_per_contract, 2),
                'total_margin': round(total_margin, 2),
                'min_capital_needed': round(min_capital_for_grid, 2),
            }

        # Все ок
        message = (
            f"✅ <b>Конфігурація снайпера коректна</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 {symbol} | ⚖️ {leverage}x\n"
            f"💰 Капітал: ${capital_usd:.2f}\n"
            f"📦 Контрактів: <b>{total_contracts}</b>\n"
            f"💵 Маржа: ~<b>${total_margin:.2f}</b>"
        )
        return {
            'ok': True,
            'message': message,
            'contracts': total_contracts,
            'margin_per_contract': round(margin_per_contract, 2),
            'total_margin': round(total_margin, 2),
            'min_capital_needed': round(min_capital_needed, 2),
        }

    def calculate_requirements(self, symbol: str, current_price: float,
                               margin: float = None, leverage: int = None,
                               min_grid_pct: float = 0.1):
        lot_size = self._get_multiplier(symbol)
        contract_value = current_price * lot_size
        min_total_volume = contract_value / min_grid_pct

        result = {
            "contract_value": round(contract_value, 2),
            "min_total_volume": round(min_total_volume, 2)
        }

        if margin is not None and leverage is None:
            result["required_leverage"] = math.ceil(min_total_volume / margin)
        elif leverage is not None and margin is None:
            result["required_margin"] = round((min_total_volume / leverage) * 1.01, 2)

        return result

    def calculate_exponential_grid(self, symbol: str, current_price: float,
                                   poi_start: float, poi_end: float,
                                   num_orders: int = 3):
        multiplier = self._get_multiplier(symbol)
        precision = self._get_price_precision(symbol)

        min_spread = 20.0 if "BTC" in symbol else 1.0
        if abs(poi_start - poi_end) < min_spread:
            direction = 1 if poi_start > poi_end else -1
            poi_end = poi_start - (min_spread * direction)

        contract_value_usd = current_price * multiplier
        total_contracts = math.floor(self.fixed_capital_usd / contract_value_usd)

        if total_contracts < num_orders:
            logger.warning(
                f"⚠️ Недостатньо капіталу для {num_orders} ордерів у {symbol}. "
                f"Контрактів: {total_contracts}, потрібно мін: {num_orders}"
            )
            return []

        if num_orders == 3:
            weights = [20, 30, 50]
        elif num_orders == 4:
            weights = [10, 20, 30, 40]
        else:
            weights = [2 ** i for i in range(num_orders)]

        total_weight = sum(weights)
        price_step = (poi_end - poi_start) / (num_orders - 1) if num_orders > 1 else 0
        grid = []

        for i in range(num_orders):
            price = poi_start + (i * price_step)
            amount = math.floor(total_contracts * (weights[i] / total_weight))
            grid.append(Order(round(price, precision), amount))

        allocated_contracts = sum(o.amount for o in grid)
        if allocated_contracts < total_contracts and grid:
            grid[-1].amount += (total_contracts - allocated_contracts)

        return grid

    def calculate_single_order(self, symbol: str, current_price: float, entry_price: float):
        multiplier = self._get_multiplier(symbol)
        precision = self._get_price_precision(symbol)

        contract_value_usd = entry_price * multiplier
        total_contracts = math.floor(self.fixed_capital_usd / contract_value_usd)

        if total_contracts < 1:
            logger.warning(f"⚠️ Недостатньо капіталу для 1 контракту {symbol} @ {entry_price}")
            return []

        return [Order(round(entry_price, precision), total_contracts)]