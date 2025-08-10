from binance.client import Client
from binance.enums import *
from os import getenv
from decimal import Decimal, ROUND_DOWN

WHITELIST = ["LINKUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"]

# Load API keys securely (use environment variables in production)
API_KEY = getenv("API_KEY")
API_SECRET = getenv("API_SECRET")

# Connect to Binance
client = Client(API_KEY, API_SECRET)

def get_balances() -> list:
    print('Owned Coins Balances:')
    account_info = client.get_account()
    balances = account_info['balances']

    # Only show coins with nonzero balance
    nonzero = [b for b in balances if float(b['free']) > 0 or float(b['locked']) > 0]

    for b in nonzero:
        print(f"{b['asset']}: Free={b['free']}, Locked={b['locked']}")

    return nonzero

def cancel_all_orders():
    print('Canceled all orders:')
    open_orders = client.get_open_orders()
    for order in open_orders:
        symbol = order["symbol"]
        try:
            client.cancel_order(symbol=symbol, orderId=order["orderId"])
            print(f"✅ Canceled order {order['orderId']} for {symbol}")
        except Exception as e:
            print(f"❌ Could not cancel {symbol} order {order['orderId']}: {e}")

def get_price_filter(symbol):
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'PRICE_FILTER':
            return f['tickSize'], f['minPrice']
    return None, None

def round_price(price, tick_size):
    tick_size = Decimal(tick_size)
    price = Decimal(str(price))
    rounded = (price // tick_size) * tick_size
    return rounded.quantize(tick_size, rounding=ROUND_DOWN)

def get_lot_size_filter(symbol):
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            return f['minQty'], f['maxQty'], f['stepSize']
    return None, None, None

def round_quantity(qty, step_size_str):
    step_size = Decimal(step_size_str)
    qty = Decimal(str(qty))
    rounded = (qty // step_size) * step_size
    return rounded.quantize(step_size, rounding=ROUND_DOWN)

def create_stop_market_orders(balances, drop_percent=2):
    print('Placing Stop Market Orders:')
    for asset in balances:
        free_amount = float(asset["free"])
        if free_amount > 0 and asset["asset"] != "USDT":
            symbol = asset["asset"] + "USDT"  # Assuming USDT pairs
            try:
                # Get latest market price
                ticker = client.get_symbol_ticker(symbol=symbol)
                price = float(ticker["price"])

                # Calculate stop price (drop_percent% below current price)
                stop_price = price * (1 - drop_percent / 100)

                tick_size_str, _ = get_price_filter(symbol)
                min_qty_str, _, step_size_str = get_lot_size_filter(symbol)

                # adjust the stop price to binance requirements
                if tick_size_str:
                    stop_price_rounded = round_price(stop_price, tick_size_str)
                else:
                    stop_price_rounded = Decimal(str(stop_price))

                # adjust the free amount to binance requirements
                if step_size_str:
                    quantity_rounded = round_quantity(free_amount, step_size_str)
                else:
                    quantity_rounded = Decimal(str(free_amount))

                # Skip if quantity < minQty
                if min_qty_str and quantity_rounded < Decimal(min_qty_str):
                    print(f"⏭ Skipping {symbol} qty {quantity_rounded} < minQty {min_qty_str}")
                    continue

                print(f"Placing STOP-MARKET SELL for {symbol} at stop price {stop_price_rounded} qty {quantity_rounded}")

                client.create_order(
                    symbol=symbol,
                    side=SIDE_SELL,
                    type=ORDER_TYPE_STOP_LOSS,
                    stopPrice=stop_price_rounded,
                    quantity=quantity_rounded
                )
            except Exception as e:
                print(f"❌ Could not place stop order for {symbol}: {e}")

def buy_missing_whitelist_coins(balances):
    free_usdt = 0

    for b in balances:
        if b["asset"] == "USDT":
            free_usdt = float(b["free"])
            break

    if free_usdt < 5:
        print(f"Not enough USDT to buy missing coins (have {free_usdt} USDT).")
        return

    # Find owned symbols from whitelist (asset + 'USDT' pair)
    owned_symbols = [b["asset"] + 'USDT' for b in balances if float(b["free"]) > 0]

    # Find missing whitelist coins (those not owned)
    missing_symbols = [sym for sym in WHITELIST if sym not in owned_symbols]

    if not missing_symbols:
        print("You already own all whitelist coins.")
        return

    # Divide free USDT across missing coins
    per_coin_budget = free_usdt / len(missing_symbols)
    print(f"Free USDT: {free_usdt}, Missing coins: {missing_symbols}, Budget per coin: {per_coin_budget}")

    for symbol in missing_symbols:
        if per_coin_budget < 5:
            print(f"Skipping {symbol}, budget {per_coin_budget} is less than 5 USDT minimum.")
            continue

        # Get latest price for symbol
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])

        # Calculate quantity to buy (budget / price)
        quantity = per_coin_budget / price

        # Round quantity according to lot size filter
        _, _, step_size_str = get_lot_size_filter(symbol)
        if step_size_str:
            quantity_rounded = round_quantity(quantity, step_size_str)
        else:
            quantity_rounded = quantity

        # Skip if quantity < minQty
        min_qty_str, _, _ = get_lot_size_filter(symbol)
        if min_qty_str and quantity_rounded < Decimal(min_qty_str):
            print(f"Skipping {symbol} because quantity {quantity_rounded} < minQty {min_qty_str}")
            continue

        # Place limit buy order at current price (can tweak price if needed)
        try:
            print(f"Placing LIMIT BUY for {symbol} qty {quantity_rounded} at price {price}")
            client.create_order(
                symbol=symbol,
                side=SIDE_BUY,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                price=str(price),
                quantity=str(quantity_rounded)
            )
        except Exception as e:
            print(f"❌ Could not place limit buy for {symbol}: {e}")

if __name__ == "__main__":
    # cancel all open orders
    cancel_all_orders()
    # get coins balances
    balances = get_balances()
    # creat stop loss orders
    create_stop_market_orders(balances)
    # buy missing coins from whitelist
    #buy_missing_whitelist_coins(balances)
