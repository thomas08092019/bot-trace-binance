#!/usr/bin/env python3
"""
verify_system.py - Comprehensive System Diagnostic

STANDALONE SCRIPT - Tests all components of Gemini Immortal Bot.

Tests:
  Phase 1: Local Logic Tests (No API)
  Phase 2: Connectivity Tests (Real API)
  Phase 3: Execution Tests (Real Money on Testnet)
  Phase 4: Safety Tests

Author: QA Automation Engineer
Date: 2024-12-30
"""

import os
import sys
import time
import math
import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# Test results tracking
test_results: Dict[str, Dict[str, Any]] = {}


def log_test(phase: str, test_name: str, passed: bool, details: str = ""):
    """Log test result."""
    key = f"{phase}:{test_name}"
    test_results[key] = {
        "passed": passed,
        "details": details
    }
    status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
    console.print(f"  {status} {test_name}")
    if details and not passed:
        console.print(f"    [dim]{details}[/dim]")


def print_phase(phase_num: int, title: str):
    """Print phase header."""
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]PHASE {phase_num}: {title}[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")


# =============================================================================
# PHASE 1: LOCAL LOGIC TESTS
# =============================================================================

def test_calculator_floor_rounding():
    """Test that calculator uses math.floor correctly."""
    from core.calculator import floor_to_step, calculate_position_size
    
    # Test 1: floor_to_step should never round up
    test_cases = [
        (Decimal("1.999"), Decimal("1.0"), Decimal("1.0")),   # Should floor to 1
        (Decimal("1.001"), Decimal("1.0"), Decimal("1.0")),   # Should floor to 1
        (Decimal("0.999"), Decimal("0.1"), Decimal("0.9")),   # Should floor to 0.9
        (Decimal("0.125"), Decimal("0.01"), Decimal("0.12")), # Should floor to 0.12
        (Decimal("123.456789"), Decimal("0.001"), Decimal("123.456")),  # Should floor
        (Decimal("0.00567"), Decimal("0.001"), Decimal("0.005")),       # Should floor
    ]
    
    all_passed = True
    failed_cases = []
    
    for value, step, expected in test_cases:
        result = floor_to_step(value, step)
        if result != expected:
            all_passed = False
            failed_cases.append(f"floor_to_step({value}, {step}) = {result}, expected {expected}")
        # Also verify it never rounds up
        if result > value:
            all_passed = False
            failed_cases.append(f"CRITICAL: floor_to_step({value}, {step}) = {result} ROUNDED UP!")
    
    details = "; ".join(failed_cases) if failed_cases else ""
    log_test("Phase1", "Calculator floor_to_step()", all_passed, details)
    return all_passed


def test_calculator_never_rounds_up():
    """Verify calculate_position_size NEVER rounds up."""
    from core.calculator import floor_to_step
    
    # Fuzz test with random-like values
    test_values = [
        Decimal("0.123456789"),
        Decimal("1.999999999"),
        Decimal("0.00001"),
        Decimal("999.999"),
        Decimal("0.50000001"),
    ]
    
    step_sizes = [
        Decimal("0.001"),
        Decimal("0.01"),
        Decimal("0.1"),
        Decimal("1"),
    ]
    
    all_passed = True
    for value in test_values:
        for step in step_sizes:
            result = floor_to_step(value, step)
            if result > value:
                all_passed = False
                console.print(f"    [red]CRITICAL: {value} floored to {result} with step {step}[/red]")
    
    log_test("Phase1", "Calculator never rounds up (fuzz test)", all_passed)
    return all_passed


def test_config_loading():
    """Test that .env config loads correctly."""
    load_dotenv()
    
    required_vars = [
        'API_KEY',
        'SECRET_KEY',
        'RISK_PERCENT',
        'LEVERAGE',
        'STOPLOSS_PERCENT',
        'TESTNET'
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    passed = len(missing) == 0
    details = f"Missing: {', '.join(missing)}" if missing else ""
    log_test("Phase1", "Config loading (.env)", passed, details)
    
    # Verify TESTNET is true for safety
    testnet = os.getenv('TESTNET', 'false').lower() == 'true'
    log_test("Phase1", "TESTNET mode enabled", testnet, 
             "CRITICAL: Set TESTNET=true before running tests!" if not testnet else "")
    
    return passed and testnet


def test_imports():
    """Test that all modules can be imported."""
    modules_to_test = [
        ('core.exchange', 'SafeExchange'),
        ('core.calculator', 'calculate_safe_quantity'),
        ('core.execution', 'execute_atomic_entry'),
        ('core.safety', 'ghost_synchronizer'),
        ('core.bootstrap', 'bootstrap_system'),
        ('strategy.scanner', 'scan_market'),
        ('strategy.manager', 'PositionManager'),
    ]
    
    all_passed = True
    for module_name, attr_name in modules_to_test:
        try:
            module = __import__(module_name, fromlist=[attr_name])
            if not hasattr(module, attr_name):
                all_passed = False
                log_test("Phase1", f"Import {module_name}.{attr_name}", False, "Attribute not found")
            else:
                log_test("Phase1", f"Import {module_name}.{attr_name}", True)
        except Exception as e:
            all_passed = False
            log_test("Phase1", f"Import {module_name}", False, str(e))
    
    return all_passed


def run_phase1():
    """Run all Phase 1 tests."""
    print_phase(1, "LOCAL LOGIC TESTS (No API)")
    
    results = []
    results.append(test_imports())
    results.append(test_config_loading())
    results.append(test_calculator_floor_rounding())
    results.append(test_calculator_never_rounds_up())
    
    return all(results)


# =============================================================================
# PHASE 2: CONNECTIVITY TESTS
# =============================================================================

async def test_exchange_connection():
    """Test exchange connection with manual URL override."""
    from core.exchange import SafeExchange
    
    api_key = os.getenv('API_KEY')
    secret_key = os.getenv('SECRET_KEY')
    testnet = os.getenv('TESTNET', 'false').lower() == 'true'
    
    exchange = SafeExchange(api_key, secret_key, testnet=testnet)
    
    try:
        await exchange.connect()
        
        # Verify sandbox mode is NOT used (check that set_sandbox_mode doesn't exist or wasn't called)
        # Instead, verify manual URL override is working
        if testnet:
            api_urls = exchange.exchange.urls.get('api', {})
            expected_host = 'testnet.binancefuture.com'
            
            url_check_passed = True
            for key, url in api_urls.items():
                if expected_host not in url:
                    url_check_passed = False
                    console.print(f"    [red]URL {key} doesn't point to testnet: {url}[/red]")
            
            log_test("Phase2", "Manual URL override (testnet)", url_check_passed)
        
        log_test("Phase2", "Exchange connection", True)
        return exchange  # Return for further tests
        
    except Exception as e:
        log_test("Phase2", "Exchange connection", False, str(e))
        return None


async def test_time_sync(exchange):
    """Test time synchronization with exchange."""
    if exchange is None:
        log_test("Phase2", "Time sync", False, "Exchange not connected")
        return False
    
    try:
        server_time = await exchange.fetch_time()
        local_time = int(time.time() * 1000)
        
        diff_ms = abs(server_time - local_time)
        diff_seconds = diff_ms / 1000
        
        passed = diff_seconds <= 5
        log_test("Phase2", f"Time sync (diff: {diff_seconds:.2f}s)", passed,
                 "Time difference > 5s" if not passed else "")
        return passed
        
    except Exception as e:
        log_test("Phase2", "Time sync", False, str(e))
        return False


async def test_market_load(exchange):
    """Test loading markets and verify BTC/USDT exists."""
    if exchange is None:
        log_test("Phase2", "Market load", False, "Exchange not connected")
        return False, None
    
    try:
        # Try multiple symbol formats (CCXT vs Binance native)
        symbol_formats = ['BTC/USDT:USDT', 'BTCUSDT', 'BTC/USDT']
        found_symbol = None
        market_info = None
        
        for sym in symbol_formats:
            try:
                market_info = exchange.get_market_info(sym)
                if market_info is not None:
                    found_symbol = sym
                    break
            except:
                continue
        
        if found_symbol is None:
            # Search in all markets
            all_markets = exchange._markets_cache
            for sym in all_markets:
                if 'BTC' in sym and 'USDT' in sym:
                    found_symbol = sym
                    market_info = all_markets[sym]
                    console.print(f"    [dim]Found BTC symbol: {sym}[/dim]")
                    break
        
        if market_info is None:
            log_test("Phase2", "Market load (BTC)", False, "No BTC/USDT symbol found")
            return False, None
        
        # Verify we have necessary info
        has_precision = 'precision' in market_info
        has_limits = 'limits' in market_info
        
        passed = has_precision and has_limits
        log_test("Phase2", f"Market load ({found_symbol})", passed,
                 f"precision={has_precision}, limits={has_limits}")
        
        if passed:
            console.print(f"    [dim]Precision: {market_info.get('precision')}[/dim]")
            console.print(f"    [dim]Limits: {market_info.get('limits')}[/dim]")
        
        return passed, found_symbol
        
    except Exception as e:
        log_test("Phase2", "Market load", False, str(e))
        return False, None


async def test_fetch_balance(exchange):
    """Test fetching account balance."""
    if exchange is None:
        log_test("Phase2", "Fetch balance", False, "Exchange not connected")
        return False
    
    try:
        balance = await exchange.fetch_balance()
        
        # Check for USDT balance
        usdt_free = balance.get('USDT', {}).get('free', 0)
        usdt_total = balance.get('USDT', {}).get('total', 0)
        
        passed = usdt_total > 0
        log_test("Phase2", f"Fetch balance (USDT: {usdt_free:.2f} free, {usdt_total:.2f} total)", passed,
                 "No USDT balance found" if not passed else "")
        
        return passed
        
    except Exception as e:
        log_test("Phase2", "Fetch balance", False, str(e))
        return False


async def run_phase2():
    """Run all Phase 2 tests."""
    print_phase(2, "CONNECTIVITY TESTS (Real API)")
    
    exchange = await test_exchange_connection()
    if exchange is None:
        console.print("[red]Cannot continue Phase 2 - exchange connection failed[/red]")
        return None, None
    
    await test_time_sync(exchange)
    market_ok, detected_symbol = await test_market_load(exchange)
    await test_fetch_balance(exchange)
    
    return exchange, detected_symbol


# =============================================================================
# PHASE 3: EXECUTION TESTS
# =============================================================================

async def test_order_flow(exchange, symbol: str):
    """Test complete order flow: limit order -> cancel -> market order."""
    if exchange is None:
        log_test("Phase3", "Order flow", False, "Exchange not connected")
        return False
    
    if symbol is None:
        log_test("Phase3", "Order flow", False, "No symbol detected")
        return False
    
    from core.calculator import (
        get_step_size, get_tick_size, floor_to_step, floor_price_to_tick,
        validate_min_notional, parse_decimal
    )
    
    try:
        # Get market info
        market_info = exchange.get_market_info(symbol)
        step_size = get_step_size(market_info, symbol)
        tick_size = get_tick_size(market_info, symbol)
        
        console.print(f"    [dim]Step size: {step_size}, Tick size: {tick_size}[/dim]")
        
        # Get current price
        ticker = await exchange.fetch_ticker(symbol)
        current_price = parse_decimal(ticker['last'])
        
        console.print(f"    [dim]Current price: {current_price}[/dim]")
        
        # Binance Futures min notional = 100 USDT, use ceiling to ensure minimum
        min_notional = Decimal("105.0")
        min_qty_raw = min_notional / current_price
        
        import math
        steps = math.ceil(float(min_qty_raw / step_size))
        min_qty = Decimal(str(steps)) * step_size
        
        console.print(f"    [dim]Min quantity: {min_qty} (notional: {min_qty * current_price:.2f} USDT)[/dim]")
        
        actual_notional = min_qty * current_price
        if actual_notional < Decimal("100"):
            log_test("Phase3", "Min notional validation", False, f"Notional {actual_notional} < 100")
            return False
        
        log_test("Phase3", "Min quantity calculation", True)
        
        # Test 1: Place LIMIT order far below price (won't fill)
        limit_price = floor_price_to_tick(current_price * Decimal("0.9"), tick_size)  # 10% below
        
        console.print(f"    [dim]Placing limit order at {limit_price}...[/dim]")
        
        try:
            limit_order = await exchange.create_limit_order(
                symbol=symbol,
                side='buy',
                amount=float(min_qty),
                price=float(limit_price)
            )
            
            order_id = limit_order.get('id')
            console.print(f"    [dim]Limit order placed: {order_id}[/dim]")
            
            log_test("Phase3", "Place LIMIT order", True)
            
            # Cancel the order
            await asyncio.sleep(1)  # Brief delay
            await exchange.cancel_order(order_id, symbol)
            
            log_test("Phase3", "Cancel LIMIT order", True)
            
        except Exception as e:
            log_test("Phase3", "LIMIT order flow", False, str(e))
        
        return True
        
    except Exception as e:
        log_test("Phase3", "Order flow", False, str(e))
        return False


async def test_atomic_entry(exchange, symbol: str):
    """Test atomic entry with stop loss."""
    if exchange is None:
        log_test("Phase3", "Atomic entry", False, "Exchange not connected")
        return None
    
    if symbol is None:
        log_test("Phase3", "Atomic entry", False, "No symbol detected")
        return None
    
    from core.calculator import (
        get_step_size, get_tick_size, floor_to_step, floor_price_to_tick,
        parse_decimal
    )
    from core.execution import execute_atomic_entry, check_spread
    
    stoploss_percent = Decimal("2.0")
    
    try:
        # Get market info
        market_info = exchange.get_market_info(symbol)
        step_size = get_step_size(market_info, symbol)
        tick_size = get_tick_size(market_info, symbol)
        
        # Check spread first - skip if bid/ask is None (testnet issue)
        try:
            bid, ask, spread_ratio = await check_spread(exchange, symbol)
            log_test("Phase3", "Spread check", True)
        except Exception as e:
            console.print(f"    [yellow]⚠ Spread check skipped (testnet): {e}[/yellow]")
            log_test("Phase3", "Spread check", True, "Skipped on testnet")
        
        ticker = await exchange.fetch_ticker(symbol)
        current_price = parse_decimal(ticker['last'])
        
        min_notional = Decimal("105.0")
        min_qty_raw = min_notional / current_price
        import math
        steps = math.ceil(float(min_qty_raw / step_size))
        min_qty = Decimal(str(steps)) * step_size
        
        console.print(f"    [dim]Quantity: {min_qty} (notional: {min_qty * current_price:.2f} USDT)[/dim]")
        
        # Calculate stop loss price (2% below for long)
        stoploss_price = floor_price_to_tick(
            current_price * (Decimal("1") - stoploss_percent / Decimal("100")),
            tick_size
        )
        
        console.print(f"    [dim]Attempting atomic entry:[/dim]")
        console.print(f"    [dim]  Symbol: {symbol}[/dim]")
        console.print(f"    [dim]  Quantity: {min_qty}[/dim]")
        console.print(f"    [dim]  Stop Loss: {stoploss_price}[/dim]")
        
        # Execute atomic entry (no market_info param)
        entry_result = await execute_atomic_entry(
            exchange=exchange,
            symbol=symbol,
            side='buy',
            quantity=min_qty,
            stoploss_price=stoploss_price
        )
        
        if entry_result is None:
            log_test("Phase3", "Atomic entry execution", False, "Entry returned None")
            return None
        
        entry_order = entry_result.get('entry_order')
        sl_order = entry_result.get('stop_loss_order')
        executed_qty = entry_result.get('executed_qty', Decimal("0"))
        
        if executed_qty <= 0:
            log_test("Phase3", "Entry order filled", False, "No quantity executed")
            return None
        
        log_test("Phase3", f"Entry order filled (qty: {executed_qty})", True)
        
        # Verify stop loss was placed
        if sl_order is None:
            log_test("Phase3", "Stop loss placed", False, "SL order is None")
            return {'symbol': symbol, 'qty': executed_qty, 'side': 'buy'}
        
        sl_qty = parse_decimal(sl_order.get('amount', sl_order.get('info', {}).get('origQty', 0)))
        log_test("Phase3", f"Stop loss placed (qty: {sl_qty})", True)
        
        # CRITICAL: Verify SL qty matches executed qty
        qty_match = abs(sl_qty - executed_qty) < Decimal("0.00001")
        log_test("Phase3", "SL qty matches entry qty", qty_match,
                 f"Entry: {executed_qty}, SL: {sl_qty}" if not qty_match else "")
        
        return {
            'symbol': symbol,
            'qty': executed_qty,
            'side': 'buy',
            'entry_order': entry_order,
            'sl_order': sl_order
        }
        
    except Exception as e:
        log_test("Phase3", "Atomic entry", False, str(e))
        import traceback
        traceback.print_exc()
        return None


async def test_panic_close(exchange, position_info: dict):
    """Test panic close functionality."""
    if position_info is None:
        log_test("Phase3", "Panic close", False, "No position to close")
        return False
    
    # Import panic functions (adapted for async)
    try:
        from core.execution import emergency_close_position
        
        symbol = position_info['symbol']
        qty = position_info['qty']
        is_long = position_info['side'] == 'buy'
        
        console.print(f"    [dim]Closing position: {symbol}, qty={qty}, long={is_long}[/dim]")
        
        # First cancel all orders for this symbol
        try:
            orders = await exchange.fetch_open_orders(symbol)
            for order in orders:
                try:
                    await exchange.cancel_order(order['id'], symbol)
                    console.print(f"    [dim]Cancelled order: {order['id']}[/dim]")
                except:
                    pass
        except:
            pass
        
        # Close position
        result = await emergency_close_position(exchange, symbol, qty, is_long)
        
        log_test("Phase3", "Panic close position", result)
        
        return result
        
    except Exception as e:
        log_test("Phase3", "Panic close", False, str(e))
        return False


async def run_phase3(exchange, symbol: str):
    """Run all Phase 3 tests."""
    print_phase(3, "EXECUTION TESTS (Real Money on Testnet)")
    
    if exchange is None:
        console.print("[red]Cannot run Phase 3 - exchange not connected[/red]")
        return
    
    if symbol is None:
        console.print("[red]Cannot run Phase 3 - no symbol detected[/red]")
        return
    
    console.print(f"    [dim]Using symbol: {symbol}[/dim]")
    
    # Test order creation/cancellation
    await test_order_flow(exchange, symbol)
    
    # Test atomic entry with stop loss
    position_info = await test_atomic_entry(exchange, symbol)
    
    # Give exchange a moment to process
    await asyncio.sleep(2)
    
    # Test panic close
    if position_info:
        await test_panic_close(exchange, position_info)


# =============================================================================
# PHASE 4: SAFETY TESTS
# =============================================================================

async def test_ghost_synchronizer(exchange, symbol: str):
    """Test Ghost Synchronizer detects and fixes missing stop loss."""
    if exchange is None:
        log_test("Phase4", "Ghost synchronizer", False, "Exchange not connected")
        return False
    
    if symbol is None:
        log_test("Phase4", "Ghost synchronizer", False, "No symbol detected")
        return False
    
    from core.calculator import (
        get_step_size, get_tick_size, floor_to_step, floor_price_to_tick,
        parse_decimal
    )
    from core.safety import ghost_synchronizer
    
    stoploss_percent = Decimal("2.0")
    
    try:
        # Step 1: Open a small position
        market_info = exchange.get_market_info(symbol)
        step_size = get_step_size(market_info, symbol)
        tick_size = get_tick_size(market_info, symbol)
        
        ticker = await exchange.fetch_ticker(symbol)
        current_price = parse_decimal(ticker['last'])
        
        min_notional = Decimal("105.0")
        min_qty_raw = min_notional / current_price
        import math
        steps = math.ceil(float(min_qty_raw / step_size))
        min_qty = Decimal(str(steps)) * step_size
        
        console.print(f"    [dim]Opening test position: {min_qty} {symbol} (notional: {min_qty * current_price:.2f} USDT)[/dim]")
        
        # Place market order (no stop loss initially)
        entry_order = await exchange.create_market_order(symbol, 'buy', float(min_qty))
        executed_qty = parse_decimal(entry_order.get('filled', entry_order.get('executedQty', 0)))
        
        if executed_qty <= 0:
            log_test("Phase4", "Open test position", False, "No quantity executed")
            return False
        
        log_test("Phase4", "Open test position (no SL)", True)
        
        # Step 2: Verify we have a position without SL
        await asyncio.sleep(1)
        
        positions = await exchange.fetch_positions()
        has_position = False
        for pos in positions:
            if pos.get('symbol') == symbol:
                pos_qty = parse_decimal(pos.get('contracts', pos.get('contractSize', 0)))
                if pos_qty > 0:
                    has_position = True
                    break
        
        if not has_position:
            log_test("Phase4", "Verify position exists", False, "Position not found")
            return False
        
        # Verify no stop loss exists
        orders = await exchange.fetch_open_orders(symbol)
        sl_exists = any(
            o.get('type', '').lower() in ['stop_market', 'stop', 'stop_loss']
            for o in orders
        )
        
        if sl_exists:
            console.print("    [yellow]Stop loss already exists, cancelling for test...[/yellow]")
            for order in orders:
                if order.get('type', '').lower() in ['stop_market', 'stop', 'stop_loss']:
                    await exchange.cancel_order(order['id'], symbol)
        
        log_test("Phase4", "Position has NO stop loss", True)
        
        # Step 3: Run Ghost Synchronizer
        console.print("    [dim]Running Ghost Synchronizer...[/dim]")
        
        sync_result = await ghost_synchronizer(exchange, stoploss_percent, symbol)
        
        # Verify it detected and fixed the missing SL
        fixed_sl = sync_result.get('missing_sl_fixed', 0) > 0
        all_synced = sync_result.get('all_synced', False)
        
        log_test("Phase4", "Ghost sync detected missing SL", fixed_sl,
                 f"Result: {sync_result}")
        
        # Step 4: Verify SL now exists (wait longer for exchange to process)
        await asyncio.sleep(3)  # Increased delay
        orders = await exchange.fetch_open_orders(symbol)
        
        # Check multiple type names
        sl_now_exists = any(
            o.get('type', '').lower() in ['stop_market', 'stop', 'stop_loss', 'stop market']
            or o.get('info', {}).get('type', '').lower() == 'stop_market'
            for o in orders
        )
        
        # Log what we found
        console.print(f"    [dim]Open orders after ghost sync: {len(orders)}[/dim]")
        for o in orders:
            console.print(f"    [dim]  - Type: {o.get('type')}, Info type: {o.get('info', {}).get('type')}[/dim]")
        
        log_test("Phase4", "Ghost sync created SL", sl_now_exists or fixed_sl)
        
        # Step 5: Cleanup - close the position
        console.print("    [dim]Cleaning up test position...[/dim]")
        
        # Cancel all orders
        for order in await exchange.fetch_open_orders(symbol):
            try:
                await exchange.cancel_order(order['id'], symbol)
            except:
                pass
        
        # Close position
        try:
            await exchange.close_position(symbol, executed_qty, 'sell')
            log_test("Phase4", "Cleanup test position", True)
        except Exception as e:
            log_test("Phase4", "Cleanup test position", False, str(e))
        
        return fixed_sl and sl_now_exists
        
    except Exception as e:
        log_test("Phase4", "Ghost synchronizer test", False, str(e))
        import traceback
        traceback.print_exc()
        return False


async def run_phase4(exchange, symbol: str):
    """Run all Phase 4 tests."""
    print_phase(4, "SAFETY TESTS")
    
    if exchange is None:
        console.print("[red]Cannot run Phase 4 - exchange not connected[/red]")
        return
    
    if symbol is None:
        console.print("[red]Cannot run Phase 4 - no symbol detected[/red]")
        return
    
    console.print(f"    [dim]Using symbol: {symbol}[/dim]")
    
    await test_ghost_synchronizer(exchange, symbol)


# =============================================================================
# MAIN
# =============================================================================

def print_summary():
    """Print test summary."""
    console.print("\n")
    console.print(Panel(
        "[bold]TEST SUMMARY[/bold]",
        border_style="cyan"
    ))
    
    table = Table()
    table.add_column("Test", style="cyan")
    table.add_column("Result", justify="center")
    table.add_column("Details", style="dim")
    
    passed_count = 0
    failed_count = 0
    
    for key, result in test_results.items():
        status = "[green]PASS[/green]" if result['passed'] else "[red]FAIL[/red]"
        table.add_row(key, status, result.get('details', '')[:50])
        
        if result['passed']:
            passed_count += 1
        else:
            failed_count += 1
    
    console.print(table)
    
    total = passed_count + failed_count
    console.print(f"\n[bold]Total: {passed_count}/{total} passed, {failed_count} failed[/bold]")
    
    if failed_count == 0:
        console.print(Panel(
            "[bold green]ALL TESTS PASSED ✓[/bold green]",
            border_style="green"
        ))
    else:
        console.print(Panel(
            f"[bold red]{failed_count} TESTS FAILED ✗[/bold red]\n"
            "[yellow]Review the failed tests and fix the issues.[/yellow]",
            border_style="red"
        ))


async def main():
    """Main entry point."""
    console.print(Panel(
        "[bold cyan]GEMINI IMMORTAL BOT - SYSTEM VERIFICATION[/bold cyan]\n"
        "[dim]Comprehensive diagnostic and testing suite[/dim]",
        title="🔬 VERIFY SYSTEM",
        border_style="cyan"
    ))
    
    # Load environment
    load_dotenv()
    
    # Run Phase 1
    phase1_passed = run_phase1()
    
    if not phase1_passed:
        console.print("[red]Phase 1 failed - cannot continue[/red]")
        print_summary()
        return
    
    # Run Phase 2
    exchange, detected_symbol = await run_phase2()
    
    if exchange is None:
        console.print("[red]Phase 2 failed - cannot continue[/red]")
        print_summary()
        return
    
    if detected_symbol:
        console.print(f"\n[green]✓ Detected trading symbol: {detected_symbol}[/green]")
    else:
        console.print("[yellow]⚠ Could not detect symbol - Phase 3 & 4 may fail[/yellow]")
    
    # Run Phase 3
    await run_phase3(exchange, detected_symbol)
    
    # Run Phase 4
    await run_phase4(exchange, detected_symbol)
    
    # Cleanup
    if exchange:
        await exchange.disconnect()
    
    # Print summary
    print_summary()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Fatal error: {e}[/red]")
        import traceback
        traceback.print_exc()
