import time
import hmac
import hashlib
import requests
from binance.enums import *
from decimal import Decimal, ROUND_DOWN
from config import *

def get_balances(client) -> list:
    print('Owned Coins Balances:')
    account_info = client.get_account()
    balances = account_info['balances']

    # Only show coins with nonzero balance
    nonzero = [b for b in balances if float(b['free']) > 0 or float(b['locked']) > 0]

    for b in nonzero:
        print(f"{b['asset']}: Free={b['free']}, Locked={b['locked']}")

    return nonzero

def cancel_all_orders(client) -> None:
    print('Canceled all orders:')
    open_orders = client.get_open_orders()

    if not open_orders:
        print('No open orders...')
        return

    for order in open_orders:
        symbol = order["symbol"]
        try:
            client.cancel_order(symbol=symbol, orderId=order["orderId"])
            print(f"✅ Canceled order {order['orderId']} for {symbol}")
        except Exception as e:
            print(f"❌ Could not cancel {symbol} order {order['orderId']}: {e}")

def get_price_filter(symbol: str, client) -> tuple:
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'PRICE_FILTER':
            return f['tickSize'], f['minPrice']
    return None, None

def round_price(price: float, tick_size: float) -> Decimal:
    tick_size = Decimal(tick_size)
    price = Decimal(str(price))
    rounded = (price // tick_size) * tick_size
    return rounded.quantize(tick_size, rounding=ROUND_DOWN)

def get_lot_size_filter(symbol: str, client) -> tuple:
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            return f['minQty'], f['maxQty'], f['stepSize']
    return None, None, None

def round_quantity(qty: float, step_size_str: float) -> Decimal:
    step_size = Decimal(step_size_str)
    qty = Decimal(str(qty))
    rounded = (qty // step_size) * step_size
    return rounded.quantize(step_size, rounding=ROUND_DOWN)

def create_oco_stop_market_orders(balances: list, client, take_profit=2, drop_percent=2, limit_offset_percent=0.1) -> None:
    """
    Places STOP-LOSS-LIMIT sell orders for all assets.

    balances: A list of asset balance dictionaries, typically from the Binance API.
    client: The python-binance client instance used to interact with the exchange.
    take_profit: how far above current price to trigger stop
    limit_offset_percent: how far below stop price to set the limit price (to help ensure fill)
    """
    print('Placing OCO Orders:')
    for asset in balances:
        free_amount = float(asset["free"])
        if free_amount > 0 and asset["asset"] != "USDT":
            symbol = asset["asset"] + "USDT"
            try:
                # Get latest market price
                ticker = client.get_symbol_ticker(symbol=symbol)
                price = float(ticker["price"])

                # Calculate stop price and limit price
                tp_price = price * (1 + take_profit / 100)
                sl_price = price * (1 - drop_percent / 100)
                limit_price = tp_price * (1 - limit_offset_percent / 100)

                # Get filters
                tick_size_str, _ = get_price_filter(symbol, client)
                min_qty_str, _, step_size_str = get_lot_size_filter(symbol, client)

                # adjust the prices and the free amount to binance requirements
                if tick_size_str:
                    tp_price_rounded = round_price(tp_price, tick_size_str)
                    sl_price_rounded = round_price(sl_price, tick_size_str)
                    limit_price_rounded = round_price(limit_price, tick_size_str)
                else:
                    tp_price_rounded = Decimal(str(tp_price))
                    sl_price_rounded = Decimal(str(sl_price))
                    limit_price_rounded = Decimal(str(limit_price))

                if step_size_str:
                    quantity_rounded = round_quantity(free_amount, step_size_str)
                else:
                    quantity_rounded = Decimal(str(free_amount))

                # Skip if quantity < minQty
                if Decimal(quantity_rounded) < Decimal(min_qty_str):
                    print(f"⏭ Skipping {symbol}, qty {quantity_rounded} < minQty {min_qty_str}")
                    continue

                print(f"Placing OCO SELL for {symbol}: TP price={tp_price_rounded}, SL price={sl_price_rounded}, limit={limit_price_rounded}, qty={quantity_rounded}")

                order = client.create_oco_order(
                    symbol=symbol,
                    side='SELL',
                    quantity=quantity_rounded,
                    # Take profit leg (limit)
                    aboveType='TAKE_PROFIT_LIMIT',
                    abovePrice=limit_price_rounded,
                    aboveStopPrice=tp_price_rounded,
                    aboveTimeInForce='GTC',
                    # Stop-loss leg (stop-market)
                    belowType='STOP_LOSS',
                    belowStopPrice=sl_price_rounded
                )

                if 'orderListId' in order:
                    print(f"OCO order placed successfully, order list ID: {order['orderListId']}")
                    for o in order['orders']:
                        print(f"Order ID: {o['orderId']} Symbol: {o['symbol']} ClientOrderId: {o['clientOrderId']}")
                else:
                    print("Failed to place OCO order, response:", order)

            except Exception as e:
                print(f"❌ Could not place stop-limit order for {symbol}: {e}")

def buy_missing_whitelist_coins(balances: list, client) -> bool:
    """
    Places limit buy orders for any coins on a predefined whitelist that the user does not currently own.
    It uses a portion of the available USDT to purchase each missing coin, ensuring the purchase
    amount meets the minimum trade size requirements.

    balances: A list of asset balance dictionaries, typically from the Binance API.
    client: The python-binance client instance used to interact with the exchange.
    """
    print('Placing Buy Missing Whitelist Coins:')
    free_usdt = 0

    for b in balances:
        if b["asset"] == "USDT":
            free_usdt = float(b["free"])
            break

    if free_usdt < 5:
        print(f"Not enough USDT to buy missing coins (have {free_usdt} USDT).")
        return False

    # Find owned symbols from whitelist (asset + 'USDT' pair)
    owned_symbols = [b["asset"] + 'USDT' for b in balances if float(b["locked"]) > 0 and b["asset"] != "USDT"]

    # Find missing whitelist coins (those not owned)
    missing_symbols = [sym for sym in WHITELIST if sym not in owned_symbols]

    if not missing_symbols:
        print("You already own all whitelist coins.")
        return False

    # Divide free USDT across missing coins
    per_coin_budget = free_usdt / len(missing_symbols)
    print(f"Free USDT: {free_usdt}, Missing coins: {missing_symbols}, Budget per coin: {per_coin_budget}")

    for symbol in missing_symbols:
        if per_coin_budget < 5:
            print(f"Can't buy coins, budget per coin is: {per_coin_budget}.\nThis is less than 5 USDT minimum.")
            break

        # Get latest price for symbol
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])

        # Calculate quantity to buy (budget / price)
        quantity = per_coin_budget / price

        # Round quantity according to lot size filter
        _, _, step_size_str = get_lot_size_filter(symbol, client)
        if step_size_str:
            quantity_rounded = round_quantity(quantity, step_size_str)
        else:
            quantity_rounded = quantity

        # Skip if quantity < minQty
        min_qty_str, _, _ = get_lot_size_filter(symbol, client)
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
                price=price,
                quantity=quantity_rounded
            )
        except Exception as e:
            print(f"❌ Could not place limit buy for {symbol}: {e}")

    return True
