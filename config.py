import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Завантажуємо змінні з файлу .env
load_dotenv()

@dataclass
class ExchangeConfig:
    api_key: str = os.getenv("OKX_API_KEY", "")
    api_secret: str = os.getenv("OKX_API_SECRET", "")
    api_password: str = os.getenv("OKX_API_PASSWORD", "")
    testnet: bool = os.getenv("USE_TESTNET", "True").lower() == "true"

@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = 1.0  # Ризик на одну угоду
    max_open_positions: int = 10
    base_currency: str = "USDT"

@dataclass
class AppConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    log_level: str = "INFO"

# Створюємо єдиний екземпляр конфігурації
config = AppConfig()