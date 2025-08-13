from binance.enums import *
from decimal import Decimal, ROUND_DOWN
import asyncio

# entry point from main script
async def handle_message(msg, client) -> None:
    if msg.get("e") == "executionReport" and msg.get("S") == "SELL" and msg.get("X") == "FILLED":

        symbol = msg.get("s")

        if msg.get("o") == 'STOP_MARKET':
            print(f'Stop Loss Market Order. Stoping trade form {symbol}')
            return

        asset = symbol.replace('USDT', '')
        print(f"Sell detected for {symbol} – triggering re-buy logic")

        old_balance = await get_balance_for_coin(client, asset)
        await buy_coin(symbol, client)

        try:
            # wait for balance to change
            new_balance = await wait_for_new_balance(client, asset, old_balance)
            await create_oco_order(new_balance, symbol, client)
        except TimeoutError as e:
            print(e)
            print('Continuing The Script...')
    else:
        print('Not a Sell order, not action needed')
        return

async def get_balance_for_coin(client, asset: str) -> float:
    """
    Fetches the free balance for a specific asset.
    """
    balance = await client.get_asset_balance(asset)
    return float(balance['free'])

async def wait_for_new_balance(client, asset: str, old_balance: float, timeout=240) -> float:
    """
    Waits for the balance of a specific asset to change.
    """
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        new_balance = await get_balance_for_coin(client, asset)
        if new_balance > old_balance:
            print(f"✅ Balance for {asset} updated: {old_balance} -> {new_balance}")
            return new_balance
        await asyncio.sleep(1)  # Poll every 1 second

    raise TimeoutError(f"❌ Timed out waiting for balance of {asset} to update.")

async def get_price_filter(symbol: str, client) -> tuple:
    info = await client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'PRICE_FILTER':
            return f['tickSize'], f['minPrice']
    return None, None

def round_price(price: float, tick_size: float) -> Decimal:
    tick_size = Decimal(tick_size)
    price = Decimal(str(price))
    rounded = (price // tick_size) * tick_size
    return rounded.quantize(tick_size, rounding=ROUND_DOWN)

async def get_lot_size_filter(symbol: str, client) -> tuple:
    info = await client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            return f['minQty'], f['maxQty'], f['stepSize']
    return None, None, None

def round_quantity(qty: float, step_size_str: float) -> Decimal:
    step_size = Decimal(step_size_str)
    qty = Decimal(str(qty))
    rounded = (qty // step_size) * step_size
    return rounded.quantize(step_size, rounding=ROUND_DOWN)

async def create_oco_order(new_balance: float, symbol: str, client,
                           take_profit=2, drop_percent=4, limit_offset_percent=0.1) -> None:
    """
    Places STOP-LOSS-LIMIT sell orders for all assets.

    balances: A list of asset balance dictionaries, typically from the Binance API.
    client: The python-binance client instance used to interact with the exchange.
    take_profit: how far above current price to trigger stop
    limit_offset_percent: how far below stop price to set the limit price (to help ensure fill)
    """
    print(f'Placing OCO Order For Coin: {symbol}')

    if new_balance == 0:
        return

    try:
        # Get latest market price
        ticker = await client.get_symbol_ticker(symbol=symbol)
        price = float(ticker["price"])

        # Calculate stop price and limit price
        tp_price = price * (1 + take_profit / 100)
        sl_price = price * (1 - drop_percent / 100)
        limit_price = tp_price * (1 - limit_offset_percent / 100)

        # Get filters
        tick_size_str, _ = await get_price_filter(symbol, client)
        min_qty_str, _, step_size_str = await get_lot_size_filter(symbol, client)

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
            quantity_rounded = round_quantity(new_balance, step_size_str)
        else:
            quantity_rounded = Decimal(str(new_balance))

        # Skip if quantity < minQty
        if Decimal(quantity_rounded) < Decimal(min_qty_str):
            print(f"⏭ Skipping {symbol}, qty {quantity_rounded} < minQty {min_qty_str}")
            return

        print(f"Placing OCO SELL for {symbol}: TP price={tp_price_rounded}, SL price={sl_price_rounded}, limit={limit_price_rounded}, qty={quantity_rounded}")

        order = await client.create_oco_order(
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

async def buy_coin(symbol: str, client, limit_offset_percent=0.1) -> None:
    """
    Places limit buy orders for any coins on a predefined whitelist that the user does not currently own.
    It uses a portion of the available USDT to purchase each missing coin, ensuring the purchase
    amount meets the minimum trade size requirements.

    balances: A list of asset balance dictionaries, typically from the Binance API.
    client: The python-binance client instance used to interact with the exchange.
    limit_offset_percent: how far above price to set the limit price (to help ensure fill)
    """
    print(f'Placing Buy Order For Coin: {symbol}')

    free_usdt = await get_balance_for_coin(client, 'USDT')

    if free_usdt < 5:
        print(f"Not enough USDT to buy missing coins (have {free_usdt} USDT).")
        return

    # Get latest price for symbol
    ticker = await client.get_symbol_ticker(symbol=symbol)
    price = float(ticker['price'])
    limit_price = price * (1 + limit_offset_percent / 100)

    # Calculate quantity to buy (budget / price)
    quantity = free_usdt / limit_price

    # Round quantity according to lot size filter
    _, _, step_size_str = await get_lot_size_filter(symbol, client)
    if step_size_str:
        quantity_rounded = round_quantity(quantity, step_size_str)
    else:
        quantity_rounded = Decimal(str(quantity))

    # Skip if quantity < minQty
    min_qty_str, _, _ = await get_lot_size_filter(symbol, client)
    if min_qty_str and quantity_rounded < Decimal(min_qty_str):
        print(f"Skipping {symbol} because quantity {quantity_rounded} < minQty {min_qty_str}")
        return

    # Place limit buy order at current price (can tweak price if needed)
    try:
        print(f"Placing LIMIT BUY for {symbol} qty {quantity_rounded} at price {limit_price}")
        await client.create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            price=limit_price,
            quantity=quantity_rounded
        )
    except Exception as e:
        print(f"❌ Could not place limit buy for {symbol}: {e}")

    return
