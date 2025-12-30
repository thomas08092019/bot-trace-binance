# Notification System Guide

## Overview

The Gemini Immortal Bot includes an optional multi-platform notification system powered by **apprise**. Get instant alerts on Telegram, Discord, Slack, Email, and 80+ other services when your bot executes trades or encounters critical errors.

## Features

✅ **Optional** - Only activates when configured  
✅ **Multi-Platform** - Telegram, Discord, Slack, Email, and more  
✅ **Safe** - Won't crash bot if notification fails  
✅ **Smart** - Different message types with appropriate emojis  
✅ **Comprehensive** - Alerts for entries, exits, stops, and errors  

## Supported Services

Via `apprise`, supports 80+ services including:

- **Telegram** (`tgram://`)
- **Discord** (`discord://`)
- **Slack** (`slack://`)
- **Email** (`mailto://`)
- **Pushover** (`pover://`)
- **Microsoft Teams** (`msteams://`)
- **And many more...**

Full list: https://github.com/caronc/apprise#supported-notifications

## Setup

### 1. Install apprise

```bash
pip install apprise
```

Or update all requirements:

```bash
pip install -r requirements.txt
```

### 2. Get Notification URLs

#### Telegram (Recommended)

1. **Create a bot:**
   - Open Telegram and search for `@BotFather`
   - Send `/newbot` and follow instructions
   - Copy the bot token (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Get your chat ID:**
   - Send a message to your bot
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Find `"chat":{"id":123456789}` in the response
   - Copy the chat ID

3. **Format URL:**
   ```
   tgram://123456789:ABCdefGHIjklMNOpqrsTUVwxyz/123456789
   ```

#### Discord

1. **Create webhook:**
   - Open Discord server settings
   - Go to **Integrations > Webhooks**
   - Click **New Webhook**
   - Customize name and channel
   - Click **Copy Webhook URL**

2. **Extract IDs from URL:**
   ```
   https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN
   ```

3. **Format URL:**
   ```
   discord://WEBHOOK_ID/WEBHOOK_TOKEN
   ```

#### Email

```
mailto://user:password@gmail.com
```

For Gmail, you need to:
- Enable 2-factor authentication
- Create an "App Password" in Google Account settings
- Use the app password instead of your regular password

### 3. Configure .env

Add your notification URL(s) to `.env`:

```env
# Single service
NOTIFICATION_URLS="tgram://123456789:ABCdefGHIjklMNOpqrsTUVwxyz/123456789"

# Multiple services (comma-separated)
NOTIFICATION_URLS="tgram://123456789:ABC/123456789,discord://webhook_id/webhook_token"

# Disabled (leave empty)
NOTIFICATION_URLS=
```

## Notification Types

The bot sends notifications for these events:

### 1. Bot Startup ✅

**When:** Bot starts successfully  
**Contains:** Network (testnet/mainnet), balance, status

**Example:**
```
✅ Bot Started (TESTNET)
Gemini Immortal Bot is now running
Network: TESTNET
Balance: $5,000.00 USDT
Status: Ready to trade
```

### 2. Trade Entry 🚀

**When:** Position opened  
**Contains:** Symbol, side, entry price, quantity, leverage

**Example:**
```
🚀 LONG BTC/USDT Entry
Position opened
Symbol: BTC/USDT
Side: LONG
Entry: $50,000.00
Quantity: 0.1
Leverage: 10x
```

### 3. Stop Loss Placed 🛑

**When:** Stop loss order placed  
**Contains:** Symbol, side, stop price, quantity

**Example:**
```
🛑 Stop Loss Placed - BTC/USDT
Protection order active
Symbol: BTC/USDT
Side: LONG
Stop Price: $49,000.00
Quantity: 0.1
```

### 4. Trade Exit 💰

**When:** Position closed (TP/SL/Manual)  
**Contains:** Symbol, exit price, P&L, reason

**Example (Profit):**
```
💰 LONG BTC/USDT Exit (TP)
Position closed
Symbol: BTC/USDT
Exit: $51,500.00
P&L: +$150.00 (+3.00%)
Reason: TP
```

**Example (Loss):**
```
📉 LONG BTC/USDT Exit (SL)
Position closed
Symbol: BTC/USDT
Exit: $49,000.00
P&L: -$100.00 (-2.00%)
Reason: SL
```

### 5. Critical Alerts 🚨

**When:** Ghost Sync failures, naked positions, errors  
**Contains:** Error details, action required

**Example:**
```
🚨 NAKED POSITION DETECTED
Position without stop loss: BTC/USDT
Details:
Side: LONG
Qty: 0.1
Failed to place SL - MANUAL INTERVENTION NEEDED
```

## Console Output

When notifications are configured:

```
✓ Notifications enabled: 2 service(s) configured
  Services: Telegram, Discord
```

When notification sent:

```
📤 Notification sent: LONG BTC/USDT Entry
```

When notification fails (non-critical):

```
⚠ Failed to send notification: Connection timeout
```

## Testing

After configuring, test notifications manually:

```python
python
>>> from core.notifier import Notifier
>>> import asyncio
>>> 
>>> notifier = Notifier()
>>> async def test():
...     await notifier.send("Test", "Notification system working!", 'success')
>>> 
>>> asyncio.run(test())
```

You should receive a test notification with ✅ emoji.

## Troubleshooting

### "apprise not installed"

```bash
pip install apprise
```

### "Invalid notification URL"

Check URL format:
- Telegram: `tgram://BOT_TOKEN/CHAT_ID`
- Discord: `discord://WEBHOOK_ID/WEBHOOK_TOKEN`
- No spaces, quotes, or extra characters

### "Failed to send notification"

**For Telegram:**
- Ensure you sent at least one message to the bot
- Check bot token is correct
- Check chat ID is numeric (not username)

**For Discord:**
- Ensure webhook URL is complete
- Check webhook hasn't been deleted
- Verify channel permissions

### Notifications not appearing

1. **Check console** - Look for "Notifications enabled" message
2. **Verify URL** - Must be in `.env` file, not `.env.example`
3. **Test manually** - Use Python test script above
4. **Check bot logs** - Look for notification error messages

## Security Best Practices

⚠️ **IMPORTANT:**

1. **Never share your .env file** - Contains sensitive tokens
2. **Use separate bots** - Different bots for testnet and mainnet
3. **Restrict permissions** - Give bot only necessary permissions
4. **Monitor notifications** - Unusual activity could indicate compromise
5. **Rotate tokens** - Change bot tokens periodically

## Advanced Usage

### Custom Message Types

```python
from core.notifier import get_notifier

notifier = get_notifier()
if notifier and notifier.is_enabled():
    await notifier.send(
        title="Custom Alert",
        message="Something happened",
        message_type='warning'  # 'info', 'success', 'warning', 'error'
    )
```

### Check if Enabled

```python
from core.notifier import get_notifier

notifier = get_notifier()
if notifier and notifier.is_enabled():
    # Send notifications
    pass
else:
    # Notifications disabled
    pass
```

### Synchronous Sending (Non-async contexts)

```python
notifier.send_sync(
    title="Sync Alert",
    message="From non-async function",
    message_type='info'
)
```

## Examples

### Minimal Setup (Telegram only)

```env
NOTIFICATION_URLS="tgram://123456789:ABCdefGHIjklMNOpqrsTUVwxyz/987654321"
```

### Production Setup (Telegram + Discord)

```env
NOTIFICATION_URLS="tgram://bot_token/chat_id,discord://webhook_id/webhook_token"
```

### Disabled

```env
NOTIFICATION_URLS=
```

## Performance Impact

- **Negligible** - Notifications are async and non-blocking
- **Safe** - Failures won't stop trading
- **Fast** - Typical send time: 100-300ms
- **Efficient** - Only sends on important events

## Summary

The notification system provides:

✅ **Peace of Mind** - Know what's happening 24/7  
✅ **Quick Response** - React to issues immediately  
✅ **Trade History** - Record of all entries/exits  
✅ **Error Alerts** - Critical issues flagged instantly  
✅ **Flexibility** - Multiple platforms, fully optional  

**Recommendation:** Enable notifications, especially for mainnet trading. Use Telegram for mobile alerts.
