# PROJECT SPECIFICATION: GEMINI IMMORTAL TRADING BOT
# VERSION: GOLDEN MASTER (FINAL)

## 1. Safety Philosophy (Triết lý An toàn)
**Core Principle:** "Zero Trust & Fail-Safe" (Không tin cậy & Tự động ngắt khi lỗi).
**Critical Rules (Bất khả xâm phạm):**
1.  **Single Instance:** Sử dụng File Lock để chặn tuyệt đối việc chạy 2 bot cùng lúc.
2.  **Floor Rounding:** Luôn làm tròn xuống (Math Floor) số lượng mua để tránh lỗi thiếu số dư.
3.  **Atomic Defense:** Stoploss phải được đặt dựa trên số lượng *thực tế đã khớp* (Executed Qty), không phải số lượng dự kiến.
4.  **Stale Data Guard:** Dữ liệu giá cũ quá 3 giây -> Hủy lệnh.
5.  **Ghost Synchronizer:** Tự động quét và đồng bộ lại Stoploss nếu phát hiện sai lệch số lượng (do con người can thiệp).
6.  **Panic First:** Script cứu hộ phải giết Process chính trước khi đóng lệnh.

## 2. Directory Structure

```text
bot-trace-binance/
│
├── .env                    # Config (API_KEY, SECRET_KEY, RISK_PERCENT, LEVERAGE)
├── .env.example            # Template for .env
├── .gitignore              # Git ignore file
├── main.py                 # Main Orchestrator (Graceful Exit handler)
├── panic.py                # INDEPENDENT KILL SWITCH (Standalone Script)
├── bot.lock                # Process Lock File (Auto-generated)
├── requirements.txt        # ccxt, python-dotenv, rich, decimal
│
├── core/
│   ├── __init__.py
│   ├── bootstrap.py        # System Check, PID Lock, Time Sync
│   ├── exchange.py         # CCXT Wrapper (Retry, UUID, Stale Check)
│   ├── safety.py           # Ghost Scanner & Logic Synchronizer
│   ├── execution.py        # Atomic Order Sequence (Entry + Protection)
│   └── calculator.py       # Math Floor & Min Notional Validation
│
└── strategy/
    ├── __init__.py
    └── scanner.py          # Market Filter (Volume & Technicals)
```

## 3. Module Specifications

### A. core/bootstrap.py (Khởi động & Khóa)
**PID Lock Logic:**
- Kiểm tra file `bot.lock`. Nếu tồn tại & PID bên trong đang chạy -> CRASH APP ("Instance already running").
- Nếu không -> Tạo file, ghi PID. Đăng ký `atexit` để xóa file khi tắt.

**Sanity Checks:**
- Nếu `RISK_PER_TRADE > 5.0` hoặc `LEVERAGE > 20` -> CRASH APP.
- Check Time Sync với Binance.
- Force Cancel All Open Orders.
- Force Margin Type = ISOLATED.

### B. core/calculator.py (Toán học An toàn)
**Library:** `decimal.Decimal` & `math`.

**Floor Rounding:**
- `RawQty = (Balance * Risk) / Distance`.
- `StepSize` = Lấy từ Exchange Info.
- `SafeQty = math.floor(RawQty / StepSize) * StepSize`.
- **Tuyệt đối không dùng `round()`.**

**Min Notional:** Nếu `SafeQty * Price < 6 USDT` -> Return 0.

### C. core/exchange.py (Kết nối An toàn)
**Config:** `enableRateLimit=True`, `adjustForTimeDifference=True`.

**Stale Data Guard:**
- Trong hàm `fetch_ticker`: Nếu `CurrentTime - TickerTimestamp > 3000ms` -> Raise `StaleDataError`.

**Idempotency:** Hàm `create_order` phải tự động sinh `newClientOrderId` (UUID).

### D. core/execution.py (Thực thi Nguyên tử)
**Logic:**
1. **Check Spread:** Nếu `(Ask - Bid) / Ask > 0.001` (0.1%) -> ABORT.
2. **Entry:** Gửi lệnh Market Buy kèm UUID.
3. **Verify:** Lấy `executedQty` và `averagePrice` từ lệnh vừa khớp.
4. Nếu `executedQty == 0` -> Return.
5. **Protection:** Gửi lệnh `STOP_MARKET` cho đúng `executedQty`.
6. **Fail-Safe:** Retry 5 lần. Nếu lỗi mạng hoặc lỗi API -> MARKET CLOSE IMMEDIATE (Thoát vị thế ngay).

### E. core/safety.py (Ghost Synchronizer)
**Routine:** Chạy đầu mỗi vòng lặp main.

**Logic:**
1. Lấy `Positions` và `OpenOrders`.
2. Duyệt từng Position:
   - Tìm lệnh Stoploss tương ứng.
   - **Case 1 (Không có SL):** Đặt SL mới ngay.
   - **Case 2 (SL sai số lượng):** (Ví dụ Pos=10, SL=5) -> Hủy SL cũ -> Đặt SL mới = 10.

**Mục đích:** Tự sửa lỗi nếu người dùng can thiệp tay.

### F. panic.py (Nút Hủy Diệt)
**Standalone Script:** Dùng `ccxt` (sync).

**Logic:**
1. Đọc PID từ `bot.lock` -> Kill Process chính (`os.kill`).
2. Cancel All Orders.
3. Fetch Positions -> Close All (Market).
4. Xóa file `bot.lock`.
5. In ra màn hình đỏ rực: **"SYSTEM KILLED & FLATTENED"**

## 4. Usage

### Installation
```bash
pip install -r requirements.txt
```

### Configuration
1. Copy `.env.example` to `.env`
2. Fill in your Binance API credentials:
```
API_KEY=your_api_key_here
SECRET_KEY=your_secret_key_here
RISK_PERCENT=1.0
LEVERAGE=10
STOPLOSS_PERCENT=2.0
```

### Running the Bot
```bash
python main.py
```

### Emergency Shutdown
```bash
python panic.py
```

## 5. Safety Checklist

| # | Feature | Status |
|---|---------|--------|
| 1 | PID Lock | ✅ |
| 2 | Math Floor (never round) | ✅ |
| 3 | Stale Data Guard (3s) | ✅ |
| 4 | Atomic SL (executedQty) | ✅ |
| 5 | Ghost Synchronizer | ✅ |
| 6 | Panic First (kill then close) | ✅ |
| 7 | Spread Guard (0.1%) | ✅ |
| 8 | Min Notional Check | ✅ |
| 9 | UUID Idempotency | ✅ |
| 10 | Retry with Backoff | ✅ |
| 11 | Graceful Shutdown | ✅ |
