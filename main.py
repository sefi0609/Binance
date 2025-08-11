from utility import *
from binance.client import Client

def main():
    # connect to binance
    client = Client(API_KEY, API_SECRET)
    # cancel existing orders
    cancel_all_orders(client)
    # get coins balances
    balances = get_balances(client)
    # buying missing coins from whitelist
    get_new_balances = buy_missing_whitelist_coins(balances, client)
    # get coins balances after buying whitelist coins
    if get_new_balances:
        balances = get_balances(client)
    # create oco orders (TP + SL)
    create_oco_stop_market_orders(balances, client)

if __name__ == "__main__":
    main()