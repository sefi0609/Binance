import os

WHITELIST = ["LINKUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"]
BASE_URL = 'https://api.binance.com'

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")