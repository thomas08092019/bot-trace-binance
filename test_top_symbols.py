#!/usr/bin/env python3
"""Quick test of fetch_top_symbols"""
import asyncio
import os
from dotenv import load_dotenv
from core.exchange import create_exchange
from strategy.scanner import fetch_top_symbols

async def test():
    load_dotenv()
    ex = create_exchange(os.getenv('API_KEY'), os.getenv('SECRET_KEY'), True)
    await ex.connect()
    symbols = await fetch_top_symbols(ex, 15)
    print(f'\nTop 15 symbols: {symbols}')
    await ex.disconnect()

if __name__ == '__main__':
    asyncio.run(test())
