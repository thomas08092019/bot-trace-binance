"""
core/notifier.py - Multi-Platform Notification System

Implements:
- Telegram, Discord, Slack, Email notifications via apprise
- Optional activation (only if NOTIFICATION_URLS configured)
- Graceful error handling (won't crash bot if notification fails)
- Different message types: info, success, warning, error
- Async notification sending

Based on binance-trade-bot notification system.
"""

import os
import asyncio
from typing import Optional
from rich.console import Console

try:
    import apprise
    APPRISE_AVAILABLE = True
except ImportError:
    APPRISE_AVAILABLE = False

console = Console()


class Notifier:
    """
    Multi-platform notification sender using apprise.
    
    Supports:
    - Telegram: tgram://BOT_TOKEN/CHAT_ID
    - Discord: discord://WEBHOOK_ID/WEBHOOK_TOKEN
    - Slack: slack://TOKEN_A/TOKEN_B/TOKEN_C
    - Email: mailto://user:pass@gmail.com
    - And 80+ other services via apprise
    
    Example .env:
        NOTIFICATION_URLS="tgram://123456:ABC-DEF/123456789,discord://webhook_id/webhook_token"
    """
    
    def __init__(self, notification_urls: Optional[str] = None):
        """
        Initialize notification system.
        
        Args:
            notification_urls: Comma-separated apprise URLs, or None to disable
        """
        self.enabled = False
        self.apprise_instance = None
        self.urls = []
        
        # Check if apprise is installed
        if not APPRISE_AVAILABLE:
            console.print("[yellow]⚠ apprise not installed - notifications disabled[/yellow]")
            console.print("[dim]  Install with: pip install apprise[/dim]")
            return
        
        # Load URLs from parameter or environment
        if notification_urls is None:
            notification_urls = os.getenv('NOTIFICATION_URLS', '').strip()
        
        if not notification_urls:
            console.print("[dim]ℹ Notifications disabled (no NOTIFICATION_URLS configured)[/dim]")
            return
        
        # Parse comma-separated URLs
        self.urls = [url.strip() for url in notification_urls.split(',') if url.strip()]
        
        if not self.urls:
            console.print("[dim]ℹ Notifications disabled (empty NOTIFICATION_URLS)[/dim]")
            return
        
        # Initialize apprise
        try:
            self.apprise_instance = apprise.Apprise()
            
            # Add all notification URLs
            added_count = 0
            for url in self.urls:
                if self.apprise_instance.add(url):
                    added_count += 1
                else:
                    console.print(f"[yellow]⚠ Invalid notification URL (skipped): {url[:20]}...[/yellow]")
            
            if added_count > 0:
                self.enabled = True
                console.print(f"[green]✓ Notifications enabled: {added_count} service(s) configured[/green]")
                
                # Show service types (safely)
                services = []
                for asset in self.apprise_instance:
                    service_name = asset.service_name if hasattr(asset, 'service_name') else 'Unknown'
                    if service_name not in services:
                        services.append(service_name)
                
                if services:
                    console.print(f"[dim]  Services: {', '.join(services)}[/dim]")
            else:
                console.print("[yellow]⚠ No valid notification URLs - notifications disabled[/yellow]")
                
        except Exception as e:
            console.print(f"[yellow]⚠ Failed to initialize notifications: {e}[/yellow]")
            console.print("[dim]  Bot will continue without notifications[/dim]")
    
    async def send(
        self,
        title: str,
        message: str,
        message_type: str = 'info',
        body_format: str = None
    ) -> bool:
        """
        Send notification to all configured services.
        
        Args:
            title: Notification title
            message: Notification message body
            message_type: 'info', 'success', 'warning', 'error' (affects emoji/color)
            body_format: apprise body format (default: auto-detect)
            
        Returns:
            True if at least one notification sent successfully
        """
        if not self.enabled or not self.apprise_instance:
            return False
        
        # Add emoji prefix based on type
        emoji_map = {
            'info': 'ℹ️',
            'success': '✅',
            'warning': '⚠️',
            'error': '🚨',
            'entry': '🚀',
            'exit': '💰',
            'stop': '🛑'
        }
        
        emoji = emoji_map.get(message_type, 'ℹ️')
        formatted_title = f"{emoji} {title}"
        
        # Determine apprise notify type
        notify_type_map = {
            'info': apprise.NotifyType.INFO,
            'success': apprise.NotifyType.SUCCESS,
            'warning': apprise.NotifyType.WARNING,
            'error': apprise.NotifyType.FAILURE,
            'entry': apprise.NotifyType.SUCCESS,
            'exit': apprise.NotifyType.SUCCESS,
            'stop': apprise.NotifyType.WARNING
        }
        
        notify_type = notify_type_map.get(message_type, apprise.NotifyType.INFO)
        
        try:
            # Send notification asynchronously
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                lambda: self.apprise_instance.notify(
                    title=formatted_title,
                    body=message,
                    notify_type=notify_type,
                    body_format=body_format or apprise.NotifyFormat.TEXT
                )
            )
            
            if success:
                console.print(f"[dim]📤 Notification sent: {title}[/dim]")
            else:
                console.print(f"[yellow]⚠ Failed to send notification: {title}[/yellow]")
            
            return success
            
        except Exception as e:
            console.print(f"[yellow]⚠ Notification error: {e}[/yellow]")
            console.print(f"[dim]  Title: {title}[/dim]")
            return False
    
    def send_sync(
        self,
        title: str,
        message: str,
        message_type: str = 'info'
    ) -> bool:
        """
        Synchronous version of send() for non-async contexts.
        
        Args:
            title: Notification title
            message: Notification message body
            message_type: 'info', 'success', 'warning', 'error'
            
        Returns:
            True if notification sent successfully
        """
        if not self.enabled or not self.apprise_instance:
            return False
        
        # Add emoji prefix based on type
        emoji_map = {
            'info': 'ℹ️',
            'success': '✅',
            'warning': '⚠️',
            'error': '🚨',
            'entry': '🚀',
            'exit': '💰',
            'stop': '🛑'
        }
        
        emoji = emoji_map.get(message_type, 'ℹ️')
        formatted_title = f"{emoji} {title}"
        
        # Determine apprise notify type
        notify_type_map = {
            'info': apprise.NotifyType.INFO,
            'success': apprise.NotifyType.SUCCESS,
            'warning': apprise.NotifyType.WARNING,
            'error': apprise.NotifyType.FAILURE,
            'entry': apprise.NotifyType.SUCCESS,
            'exit': apprise.NotifyType.SUCCESS,
            'stop': apprise.NotifyType.WARNING
        }
        
        notify_type = notify_type_map.get(message_type, apprise.NotifyType.INFO)
        
        try:
            success = self.apprise_instance.notify(
                title=formatted_title,
                body=message,
                notify_type=notify_type
            )
            
            if success:
                console.print(f"[dim]📤 Notification sent: {title}[/dim]")
            else:
                console.print(f"[yellow]⚠ Failed to send notification: {title}[/yellow]")
            
            return success
            
        except Exception as e:
            console.print(f"[yellow]⚠ Notification error: {e}[/yellow]")
            return False
    
    def is_enabled(self) -> bool:
        """Check if notifications are enabled."""
        return self.enabled
    
    async def send_startup(self, balance: float, testnet: bool = False) -> bool:
        """
        Send bot startup notification.
        
        Args:
            balance: Account balance in USDT
            testnet: Whether running on testnet
            
        Returns:
            True if notification sent successfully
        """
        network = "TESTNET" if testnet else "MAINNET"
        title = f"Bot Started ({network})"
        message = (
            f"Gemini Immortal Bot is now running\n"
            f"Network: {network}\n"
            f"Balance: ${balance:,.2f} USDT\n"
            f"Status: Ready to trade"
        )
        return await self.send(title, message, 'success')
    
    async def send_entry(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int
    ) -> bool:
        """
        Send trade entry notification.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
            side: 'LONG' or 'SHORT'
            entry_price: Entry price
            quantity: Position quantity
            leverage: Leverage used
            
        Returns:
            True if notification sent successfully
        """
        title = f"{side} {symbol} Entry"
        message = (
            f"Position opened\n"
            f"Symbol: {symbol}\n"
            f"Side: {side}\n"
            f"Entry: ${entry_price:,.2f}\n"
            f"Quantity: {quantity}\n"
            f"Leverage: {leverage}x"
        )
        return await self.send(title, message, 'entry')
    
    async def send_exit(
        self,
        symbol: str,
        side: str,
        exit_price: float,
        pnl: float,
        pnl_percent: float,
        reason: str = "TP"
    ) -> bool:
        """
        Send trade exit notification.
        
        Args:
            symbol: Trading symbol
            side: 'LONG' or 'SHORT'
            exit_price: Exit price
            pnl: Profit/Loss in USDT
            pnl_percent: P&L percentage
            reason: Exit reason ('TP', 'SL', 'Manual', etc.)
            
        Returns:
            True if notification sent successfully
        """
        pnl_emoji = "💰" if pnl > 0 else "📉"
        title = f"{pnl_emoji} {side} {symbol} Exit ({reason})"
        message = (
            f"Position closed\n"
            f"Symbol: {symbol}\n"
            f"Exit: ${exit_price:,.2f}\n"
            f"P&L: ${pnl:+,.2f} ({pnl_percent:+.2f}%)\n"
            f"Reason: {reason}"
        )
        msg_type = 'success' if pnl > 0 else 'warning'
        return await self.send(title, message, msg_type)
    
    async def send_stop_placed(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float
    ) -> bool:
        """
        Send stop loss placed notification.
        
        Args:
            symbol: Trading symbol
            side: 'LONG' or 'SHORT'
            stop_price: Stop loss price
            quantity: Protected quantity
            
        Returns:
            True if notification sent successfully
        """
        title = f"Stop Loss Placed - {symbol}"
        message = (
            f"Protection order active\n"
            f"Symbol: {symbol}\n"
            f"Side: {side}\n"
            f"Stop Price: ${stop_price:,.2f}\n"
            f"Quantity: {quantity}"
        )
        return await self.send(title, message, 'stop')
    
    async def send_critical_alert(
        self,
        title: str,
        message: str,
        details: str = None
    ) -> bool:
        """
        Send critical error alert.
        
        Args:
            title: Alert title
            message: Alert message
            details: Additional details (optional)
            
        Returns:
            True if notification sent successfully
        """
        full_message = message
        if details:
            full_message += f"\n\nDetails:\n{details}"
        
        return await self.send(title, full_message, 'error')


# Global notifier instance (initialized in main.py)
_notifier: Optional[Notifier] = None


def get_notifier() -> Optional[Notifier]:
    """Get global notifier instance."""
    return _notifier


def set_notifier(notifier: Notifier) -> None:
    """Set global notifier instance."""
    global _notifier
    _notifier = notifier
