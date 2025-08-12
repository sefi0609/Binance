from utility import *
from config import *
from binance import AsyncClient, BinanceSocketManager

async def main():
    client = await AsyncClient.create(api_key=API_KEY, api_secret=API_SECRET)
    try:
        bsm = BinanceSocketManager(client)
        print('Waiting for orders...')
        async with bsm.user_socket() as stream:
            while True:
                msg = await stream.recv()
                await handle_message(msg, client)
    finally:
        await client.close_connection()

if __name__ == "__main__":
    asyncio.run(main())