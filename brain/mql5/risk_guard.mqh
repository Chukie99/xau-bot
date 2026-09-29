//+------------------------------------------------------------------+
//| risk_guard.mqh — Risk constants & guards for XAU hybrid          |
//|                                                                  |
//| PURPOSE: Hard limits the executor (MQL5 EA) can NEVER exceed.    |
//|          These are the FINAL safety net before any order.        |
//| STATUS:  Stage 4 REAL-PREP — dynamic daily loss (% of balance),  |
//|          slippage tolerance, spread guard configurable.          |
//|                                                                  |
//| NOTE: learning mode — mini risk. 0.01 lot, 3 trades/day.         |
//|       Daily loss limit OTOMATIS = 1% dari modal (bukan fixed $10)|
//+------------------------------------------------------------------+
#ifndef RISK_GUARD_MQH
#define RISK_GUARD_MQH

//--- Account / position limits
#define RISK_MAX_LOT          0.01      // max lot per order (Exness min) — JANGAN UBAH
// Fallback only. The live limit is the EA's InpMaxPositions input; this value
// is used when that input is 0 or negative. It was 1 while no code read it at
// all, so it was never tested — and a limit that low strands open positions
// instead of closing them. Set the real number in the EA's properties.
#define RISK_MAX_POSITIONS    4
#define RISK_MAX_LOT_TOTAL    0.01      // max total lot across positions
#define RISK_DAILY_LOSS_PCT   0.01      // 1% dari modal (balance awal hari) — $1000 → $10
#define RISK_MAX_DRAWDOWN_PCT 0.05      // 5% dari modal awal hari — halt kalau kena
#define RISK_MAX_TRADES_DAY   3         // max trades per day (learning)
#define RISK_RR_MIN           1.0       // min risk:reward to accept a signal
#define RISK_SL_MAX_PCT       0.05      // max SL distance as % of price (sanity)

//--- Weekend / liquidity / execution guards
#define RISK_SKIP_WEEKEND     true      // skip entries on low-liquidity hours
#define RISK_MAX_SPREAD_USD   3.0       // default $3 — REAL Cent → set 4.0 (input EA InpMaxSpreadUsd)
#define RISK_MAX_SLIPPAGE_PIPS 20       // max slippage: 20 pips dari harga signal
#define RISK_PIP_SIZE         0.1       // XAUUSD pip size = $0.10 per ounce (point 0.001)

//--- Implemented in xau_bridge.mq5 (stage 4): GuardsPass() + PlaceOrder()
//    - max lot 0.01, max 3 trades/day, daily loss 1% modal, DD 5% modal,
//    - weekend skip, spread guard (input), slippage 20 pips

#endif // RISK_GUARD_MQH