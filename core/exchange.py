"""
core/exchange.py - Safe Exchange Connection Wrapper

Implements:
- CCXT async wrapper with proper configuration
- Stale Data Guard (reject data older than 3 seconds)
- Idempotent orders with UUID client order IDs
- Automatic retry logic with exponential backoff
"""

import os
import asyncio
import time
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any, List

import ccxt.async_support as ccxt
from rich.console import Console

console = Console()

# Load config from environment (with defaults)
STALE_DATA_THRESHOLD_MS = int(os.getenv('STALE_DATA_THRESHOLD_MS', '10000'))

# Retry configuration
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '5'))
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0


class StaleDataError(Exception):
    """
    Raised when market data is too old to be reliable.
    
    CRITICAL: Never trade on stale data - it can lead to catastrophic losses.
    """
    pass


class ExchangeError(Exception):
    """Generic exchange operation error."""
    pass


class SafeExchange:
    """
    Thread-safe, fail-safe wrapper around CCXT exchange.
    
    Features:
    - Automatic time difference adjustment
    - Rate limiting
    - Stale data detection
    - UUID-based order idempotency
    - Exponential backoff retry
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        """
        Initialize safe exchange wrapper.
        
        Args:
            api_key: Binance API key
            secret_key: Binance secret key
            testnet: Use testnet if True
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.testnet = testnet
        self.exchange: Optional[ccxt.binanceusdm] = None
        self._markets_cache: Dict[str, Any] = {}
        self._last_markets_load: float = 0
        self._markets_cache_ttl: float = 3600  # 1 hour
    
    async def connect(self) -> None:
        """
        Initialize and connect to the exchange.
        
        Configures:
        - Rate limiting (enabled)
        - Time difference adjustment (enabled)
        - Sandbox mode for testnet
        """
        config = {
            'apiKey': self.api_key,
            'secret': self.secret_key,
            'enableRateLimit': True,
            'adjustForTimeDifference': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True,
            }
        }
        
        # Testnet không hỗ trợ sapi endpoints (fetch_currencies)
        # Phải disable trước khi init exchange
        if self.testnet:
            config['options'] = config.get('options', {})
            config['options']['fetchCurrencies'] = False
            # Suppress warning khi fetch all open orders (cần thiết cho Ghost Synchronizer)
            config['options']['warnOnFetchOpenOrdersWithoutSymbol'] = False
        
        self.exchange = ccxt.binanceusdm(config)
        
        if self.testnet:
            # KHÔNG DÙNG set_sandbox_mode(True) NỮA - Binance đã thay đổi cơ chế
            # Thủ công set URL cho Testnet Futures (bao gồm tất cả API versions)
            self.exchange.urls['api'] = {
                'fapiPublic': 'https://testnet.binancefuture.com/fapi/v1',
                'fapiPrivate': 'https://testnet.binancefuture.com/fapi/v1',
                'fapiPublicV2': 'https://testnet.binancefuture.com/fapi/v2',
                'fapiPrivateV2': 'https://testnet.binancefuture.com/fapi/v2',
                'fapiPublicV3': 'https://testnet.binancefuture.com/fapi/v3',
                'fapiPrivateV3': 'https://testnet.binancefuture.com/fapi/v3',
                'public': 'https://testnet.binancefuture.com/fapi/v1',
                'private': 'https://testnet.binancefuture.com/fapi/v1',
            }
            console.print("[yellow]⚠ TESTNET MODE ENABLED (Manual URL Override)[/yellow]")
        
        # Load markets
        await self._load_markets()
        
        console.print("[green]✓ Exchange connected[/green]")
    
    async def disconnect(self) -> None:
        """Close the exchange connection."""
        if self.exchange:
            await self.exchange.close()
            self.exchange = None
            console.print("[green]✓ Exchange disconnected[/green]")
    
    async def _load_markets(self) -> None:
        """Load and cache market information."""
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        current_time = time.time()
        if (current_time - self._last_markets_load) > self._markets_cache_ttl:
            self._markets_cache = await self.exchange.load_markets()
            self._last_markets_load = current_time
            console.print(f"[dim]Loaded {len(self._markets_cache)} markets[/dim]")
    
    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get market information for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
            
        Returns:
            Market info dictionary
            
        Raises:
            ExchangeError: If symbol not found
        """
        if symbol not in self._markets_cache:
            raise ExchangeError(f"Symbol {symbol} not found in markets")
        return self._markets_cache[symbol]
    
    async def _retry_async(self, operation, *args, **kwargs):
        """
        Execute an async operation with exponential backoff retry.
        
        Args:
            operation: Async function to call
            *args, **kwargs: Arguments to pass to operation
            
        Returns:
            Result of operation
            
        Raises:
            Last exception if all retries fail
        """
        last_exception = None
        
        for attempt in range(MAX_RETRIES):
            try:
                return await operation(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                last_exception = e
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                console.print(f"[yellow]⚠ Retry {attempt + 1}/{MAX_RETRIES} after {delay}s: {e}[/yellow]")
                await asyncio.sleep(delay)
            except ccxt.ExchangeError as e:
                # Don't retry on exchange errors (e.g., insufficient balance)
                raise ExchangeError(f"Exchange error: {e}")
        
        raise ExchangeError(f"All {MAX_RETRIES} retries failed: {last_exception}")
    
    def _generate_client_order_id(self) -> str:
        """
        Generate a unique client order ID for idempotency.
        
        Returns:
            UUID-based order ID
        """
        # Use 'GEM_' prefix + UUID (max 36 chars for Binance)
        return f"GEM_{uuid.uuid4().hex[:28]}"
    
    def _check_data_freshness(self, timestamp_ms: int) -> None:
        """
        Check if data is fresh enough to use.
        
        Args:
            timestamp_ms: Data timestamp in milliseconds
            
        Raises:
            StaleDataError: If data is older than threshold
        """
        current_time_ms = int(time.time() * 1000)
        age_ms = current_time_ms - timestamp_ms
        
        if age_ms > STALE_DATA_THRESHOLD_MS:
            raise StaleDataError(
                f"Data is {age_ms}ms old (threshold: {STALE_DATA_THRESHOLD_MS}ms). "
                "Refusing to trade on stale data."
            )
    
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch ticker with stale data guard.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Ticker data
            
        Raises:
            StaleDataError: If ticker is too old
            ExchangeError: If fetch fails
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        ticker = await self._retry_async(self.exchange.fetch_ticker, symbol)
        
        # CRITICAL: Check data freshness
        if 'timestamp' in ticker and ticker['timestamp']:
            self._check_data_freshness(ticker['timestamp'])
        else:
            # If no timestamp, log warning but continue
            console.print("[yellow]⚠ Ticker has no timestamp - cannot verify freshness[/yellow]")
        
        return ticker
    
    async def fetch_tickers(self) -> Dict[str, Any]:
        """
        Fetch all tickers (for volume ranking).
        
        Returns:
            Dictionary of tickers keyed by symbol
            
        Raises:
            ExchangeError: If fetch fails
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        return await self._retry_async(self.exchange.fetch_tickers)
    
    async def fetch_balance(self) -> Dict[str, Any]:
        """
        Fetch account balance.
        
        Returns:
            Balance data
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        return await self._retry_async(self.exchange.fetch_balance)
    
    async def fetch_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch open positions.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of positions
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        positions = await self._retry_async(self.exchange.fetch_positions, symbol)
        
        # Filter to only positions with non-zero quantity
        return [p for p in positions if float(p.get('contracts', 0)) != 0]
    
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch open orders.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of open orders
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        if symbol:
            return await self._retry_async(self.exchange.fetch_open_orders, symbol)
        else:
            return await self._retry_async(self.exchange.fetch_open_orders)
    
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Fetch a specific order by ID.
        
        Args:
            order_id: Order ID
            symbol: Trading symbol
            
        Returns:
            Order data
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        return await self._retry_async(self.exchange.fetch_order, order_id, symbol)
    
    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a market order with idempotency protection.
        
        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            amount: Order amount
            params: Additional parameters
            
        Returns:
            Order result
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        if params is None:
            params = {}
        
        # CRITICAL: Add UUID for idempotency
        params['newClientOrderId'] = self._generate_client_order_id()
        
        console.print(f"[cyan]→ Creating market {side} order: {amount} {symbol}[/cyan]")
        console.print(f"[dim]  Client Order ID: {params['newClientOrderId']}[/dim]")
        
        order = await self._retry_async(
            self.exchange.create_order,
            symbol, 'market', side, float(amount), None, params
        )
        
        console.print(f"[green]✓ Order created: {order['id']}[/green]")
        return order
    
    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a limit order.
        
        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            amount: Order amount
            price: Limit price
            params: Additional parameters
            
        Returns:
            Order result
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        if params is None:
            params = {}
        
        # CRITICAL: Add UUID for idempotency
        params['newClientOrderId'] = self._generate_client_order_id()
        
        console.print(f"[cyan]→ Creating limit {side} order: {amount} {symbol} @ {price}[/cyan]")
        console.print(f"[dim]  Client Order ID: {params['newClientOrderId']}[/dim]")
        
        order = await self._retry_async(
            self.exchange.create_order,
            symbol, 'limit', side, amount, price, params
        )
        
        console.print(f"[green]✓ Limit order created: {order['id']}[/green]")
        return order
    
    async def create_stop_market_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        stop_price: Decimal,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a stop market order (for stop loss).
        
        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell' (opposite of position)
            amount: Order amount
            stop_price: Stop trigger price
            params: Additional parameters
            
        Returns:
            Order result
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        if params is None:
            params = {}
        
        # CRITICAL: Add UUID for idempotency
        params['newClientOrderId'] = self._generate_client_order_id()
        params['stopPrice'] = float(stop_price)
        params['type'] = 'STOP_MARKET'
        params['reduceOnly'] = True  # Important: SL should only reduce position
        
        console.print(f"[cyan]→ Creating stop market order: {side} {amount} {symbol} @ {stop_price}[/cyan]")
        console.print(f"[dim]  Client Order ID: {params['newClientOrderId']}[/dim]")
        
        order = await self._retry_async(
            self.exchange.create_order,
            symbol, 'STOP_MARKET', side, float(amount), float(stop_price), params
        )
        
        console.print(f"[green]✓ Stop order created: {order['id']}[/green]")
        return order
    
    async def create_take_profit_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        stop_price: Decimal,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a take profit market order.
        
        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell' (opposite of position)
            amount: Order amount
            stop_price: Take profit trigger price
            params: Additional parameters
            
        Returns:
            Order result
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        if params is None:
            params = {}
        
        # CRITICAL: Add UUID for idempotency
        params['newClientOrderId'] = self._generate_client_order_id()
        params['stopPrice'] = float(stop_price)
        params['type'] = 'TAKE_PROFIT_MARKET'
        params['reduceOnly'] = True  # Important: TP should only reduce position
        
        console.print(f"[cyan]→ Creating take profit order: {side} {amount} {symbol} @ {stop_price}[/cyan]")
        console.print(f"[dim]  Client Order ID: {params['newClientOrderId']}[/dim]")
        
        order = await self._retry_async(
            self.exchange.create_order,
            symbol, 'TAKE_PROFIT_MARKET', side, float(amount), float(stop_price), params
        )
        
        console.print(f"[green]✓ Take profit order created: {order['id']}[/green]")
        return order
    
    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Cancel an order.
        
        Args:
            order_id: Order ID to cancel
            symbol: Trading symbol
            
        Returns:
            Cancellation result
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        console.print(f"[yellow]→ Cancelling order: {order_id}[/yellow]")
        
        result = await self._retry_async(self.exchange.cancel_order, order_id, symbol)
        
        console.print(f"[green]✓ Order cancelled: {order_id}[/green]")
        return result
    
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all open orders.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            Number of orders cancelled
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        orders = await self.fetch_open_orders(symbol)
        cancelled = 0
        
        for order in orders:
            try:
                await self.cancel_order(order['id'], order['symbol'])
                cancelled += 1
            except Exception as e:
                console.print(f"[red]✗ Failed to cancel {order['id']}: {e}[/red]")
        
        return cancelled
    
    async def close_position(self, symbol: str, amount: Decimal, side: str) -> Dict[str, Any]:
        """
        Close a position with a market order.
        
        Args:
            symbol: Trading symbol
            amount: Position amount to close
            side: 'buy' to close short, 'sell' to close long
            
        Returns:
            Order result
        """
        params = {'reduceOnly': True}
        return await self.create_market_order(symbol, side, amount, params)
    
    async def set_leverage(self, leverage: int, symbol: str) -> None:
        """
        Set leverage for a symbol.
        
        Args:
            leverage: Leverage value
            symbol: Trading symbol
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        await self._retry_async(self.exchange.set_leverage, leverage, symbol)
    
    async def set_margin_mode(self, margin_mode: str, symbol: str) -> None:
        """
        Set margin mode for a symbol.
        
        Args:
            margin_mode: 'isolated' or 'cross'
            symbol: Trading symbol
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        await self._retry_async(self.exchange.set_margin_mode, margin_mode, symbol)
    
    async def fetch_time(self) -> int:
        """
        Fetch exchange server time.
        
        Returns:
            Server time in milliseconds
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        return await self._retry_async(self.exchange.fetch_time)
    
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1h',
        limit: int = 100
    ) -> List[List]:
        """
        Fetch OHLCV candlestick data.
        
        Args:
            symbol: Trading symbol
            timeframe: Candle timeframe (e.g., '1m', '5m', '1h')
            limit: Number of candles to fetch
            
        Returns:
            List of OHLCV data
        """
        if self.exchange is None:
            raise ExchangeError("Exchange not connected")
        
        return await self._retry_async(
            self.exchange.fetch_ohlcv, symbol, timeframe, None, limit
        )


# Factory function for creating exchange instance
def create_exchange(api_key: str, secret_key: str, testnet: bool = False) -> SafeExchange:
    """
    Factory function to create a SafeExchange instance.
    
    Args:
        api_key: Binance API key
        secret_key: Binance secret key
        testnet: Use testnet if True
        
    Returns:
        SafeExchange instance
    """
    return SafeExchange(api_key, secret_key, testnet)
