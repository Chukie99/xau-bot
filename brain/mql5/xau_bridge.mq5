//+------------------------------------------------------------------+
//| xau_bridge.mq5 — XAUUSD hybrid: executor side (MQL5 EA)          |
//|                                                                  |
//| STAGE 3: LEARNING MODE — OrderSend enabled with HARD GUARDS.     |
//|   - Master: input InpEnabled (default true — LEARNING MODE GO).  |
//|   - Guards: max lot 0.01, max 3 trades/day, daily loss $10.      |
//|   - SL/TP wajib dari signal.json (skip kalau missing).           |
//|   - Weekend skip. Spread > $3 skip. Drawdown 5% ($50) halt.      |
//|                                                                  |
//| DESIGN: Hermes (Python) = brain writes signals/signal.json.      |
//|         This EA = tangan — reads it, logs it, places order.      |
//+------------------------------------------------------------------+
#property copyright "SOPIAN XAU Hybrid"
#property version   "0.3.0"
#property strict

#include <json_parser.mqh>    // JSON parser (flat schema)
#include <risk_guard.mqh>     // risk constants & checks

input string InpSignalPath = "signals\\signal.json";  // relative to MQL5\Files\
input int    InpPollSec    = 10;                      // timer interval (seconds)
input bool   InpEnabled    = true;                   // LEARNING MODE: ORDER ENABLED
input double InpMaxSpreadUsd = 3.0;                  // max spread (USD) — REAL Cent → 4.0
input int    InpMaxPositions = 4;                    // max concurrent positions this EA may hold

//: Stamped on every order this EA sends, and used to recognise its own open
//: positions. POSITION_MAGIC is the proper identifier, but this EA predates
//: magic numbers and every position it has ever placed carries magic 0 — so
//: switching the identity to magic would orphan those four. The comment is
//: what is actually there, and matching on it is what actually works.
#define ORDER_COMMENT "xau_bridge learning"

string g_lastTimestamp = "";      // last processed signal timestamp
string g_lastSignalAction = "";   // dedupe: only trade on BUY/SELL
string g_lastOrderTicket = "";    // track filled order
datetime g_dayAnchor = 0;         // when current daily counters started
int    g_dayTrades = 0;           // trades today
double g_dayStartBalance = 0.0;   // BALANCE at day anchor — see CheckDayRollover
double g_dayStartEquity = 0.0;    // equity at day anchor (logging only)

//+------------------------------------------------------------------+
//| Read whole text file (binary mode, share-read for Python writer) |
//+------------------------------------------------------------------+
bool FileReadAllText(const string fname, string &content)
  {
   int h = FileOpen(fname, FILE_READ | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return(false);
   ulong sz = FileSize(h);
   if(sz == 0) { FileClose(h); return(false); }
   if(sz > 100000) sz = 100000;
   uchar arr[];
   if(!ArrayResize(arr, (int)sz)) { FileClose(h); return(false); }
   if(FileReadArray(h, arr, 0, (int)sz) != (int)sz) { FileClose(h); return(false); }
   FileClose(h);
   content = CharArrayToString(arr, 0, WHOLE_ARRAY, CP_UTF8);
   return(true);
  }

//+------------------------------------------------------------------+
//| Day rollover: reset daily counters at 00:00 server time          |
//+------------------------------------------------------------------+
void CheckDayRollover()
  {
   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);
   datetime dayStart = dt.year * 100000000L + dt.mon * 1000000L + dt.day * 10000L;
   // simpler: build midnight timestamp
   MqlDateTime mid; mid.year = dt.year; mid.mon = dt.mon; mid.day = dt.day;
   mid.hour = 0; mid.min = 0; mid.sec = 0; mid.day_of_week = dt.day_of_week; mid.day_of_year = dt.day_of_year;
   datetime midnight = StructToTime(mid);
   if(g_dayAnchor == 0)
     {
      g_dayAnchor = midnight;
      g_dayTrades = 0;
      g_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      g_dayStartEquity  = AccountInfoDouble(ACCOUNT_EQUITY);
     }
   else if(midnight != g_dayAnchor)
     {
      g_dayAnchor = midnight;
      g_dayTrades = 0;
      g_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      g_dayStartEquity  = AccountInfoDouble(ACCOUNT_EQUITY);
      Print("Daily counters reset (new day). balance anchor=", g_dayStartBalance,
            " equity anchor=", g_dayStartEquity);
     }
  }

//+------------------------------------------------------------------+
//| Guard checks — return false (block) if any limit exceeded        |
//+------------------------------------------------------------------+
bool GuardsPass(const string symbol, const string action, const double entry, const double sl, const double tp, const double lot)
  {
   // 1. Enabled flag
   if(!InpEnabled)
     {
      Print("GUARD: InpEnabled=false — block order.");
      return(false);
     }

   // 2. Weekend skip (server time: Sat/Sun)
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week == 0 || dt.day_of_week == 6)
     {
      Print("GUARD: weekend — skip.");
      return(false);
     }

   // 3. SL/TP wajib
   if(sl == 0 || tp == 0 || entry == 0)
     {
      Print("GUARD: SL/TP/entry missing — skip signal.");
      return(false);
     }

   // 4. Lot guard — validasi; jangan assign (param by-value = constant)
   if(lot <= 0 || lot > 0.01)
     {
      Print("GUARD: lot invalid — ", lot, " (harus 0.01). Skip.");
      return(false);
     }

   // 5. Spread guard — pakai input InpMaxSpreadUsd (default $3, REAL Cent → 4.0)
   long spr = SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   double spreadUsd = spr * SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(spreadUsd > InpMaxSpreadUsd)
     {
      Print("GUARD: spread too wide — ", spreadUsd, " (max $", InpMaxSpreadUsd, "). Skip.");
      return(false);
     }

   // 5b. Slippage tolerance — harga aktual vs signal entry max 20 pips (XAU: 1 pip = $0.10)
   //
   //    Direction-aware, and this was a real bug: the deviation was measured
   //    from ASK regardless of side. A SELL fills at BID, so as the spread
   //    widens ASK climbs and the guard rejects a SELL whose entry is still
   //    sitting right on the signal price. On 2026-09-28 that blocked two of
   //    four signals, both SELL, for no reason other than the spread being
   //    ordinary at 20:00 WIB. Comparing against the side's own fill price
   //    means the guard answers the question it is actually being asked: did
   //    the market move away from where we decided to enter?
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ref = (action == "SELL") ? bid : ask;
   double px  = (entry > 0) ? entry : ((ask + bid) / 2.0);
   double devPips = MathAbs(ref - px) / 0.1;   // 20 pips = $2.0 (XAU point 0.001 → pip 0.1)
   if(devPips > RISK_MAX_SLIPPAGE_PIPS)
     {
      Print("GUARD: slippage too high — ", action, " dev ", DoubleToString(devPips,1),
            " pips (max ", RISK_MAX_SLIPPAGE_PIPS, "). Skip.");
      return(false);
     }

   // 6. Daily loss limit — 1% dari modal (balance), diukur dari anchor hari ini
   //
   //    Measured against the day's BALANCE anchor rather than the day's equity
   //    anchor. The limit is a percentage of balance, so comparing it against a
   //    figure that already contains unrealised P/L makes the guard measure
   //    itself against a moving target: open profit inflates the anchor, which
   //    widens the tolerance, which is precisely backwards — the account looks
   //    safest exactly when it is most exposed. Anchor on balance and the
   //    number is the one the policy actually names.
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double eqNow   = AccountInfoDouble(ACCOUNT_EQUITY);
   double dayLoss = g_dayStartBalance - eqNow;
   double dayLimit = balance * RISK_DAILY_LOSS_PCT;
   if(dayLoss > dayLimit)
     {
      Print("GUARD: daily loss exceeded — $", DoubleToString(dayLoss,2), " (limit $",
            DoubleToString(dayLimit,2), " = 1% modal). Stop today.");
      return(false);
     }

   // 7. Max drawdown — 5% dari balance anchor
   double dd = (g_dayStartBalance > 0) ? (g_dayStartBalance - eqNow) : 0.0;
   double ddLimit = balance * RISK_MAX_DRAWDOWN_PCT;
   if(dd > ddLimit)
     {
      Print("GUARD: drawdown exceeded — $", DoubleToString(dd,2), " (limit $",
            DoubleToString(ddLimit,2), " = 5% modal). Halt.");
      return(false);
     }

   // 8. Max 3 trades/day
   if(g_dayTrades >= 3)
     {
      Print("GUARD: max 3 trades/day reached.");
      return(false);
     }

   // 9. Max concurrent positions
   //
   //    RISK_MAX_POSITIONS was declared in risk_guard.mqh but nothing ever read
   //    it, so the account accumulated four open positions while the header
   //    claimed a limit of one. Counting only this EA's positions is
   //    deliberate: the account may legitimately hold other symbols, and
   //    blocking those would mean one EA vetoing trades it does not own. The
   //    comment is this EA's own, which is also the de-facto magic number.
   //
   //    The limit is an input, not the header constant. The header shipped a
   //    value of 1 that no code consulted, so any figure there was untested —
   //    and a limit too low silently strands open positions rather than closing
   //    them. InpMaxPositions defaults to 4 because that is what this account
   //    actually ran; a new user with fresh settings should raise or lower it
   //    deliberately in the EA's properties, where they can see it.
   int maxPos = (InpMaxPositions > 0) ? InpMaxPositions : RISK_MAX_POSITIONS;
   int own = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_COMMENT) == ORDER_COMMENT)
         own++;
     }
   if(own >= maxPos)
     {
      Print("GUARD: max ", maxPos,
            " open position(s) for this EA already — have ", own, ". Skip.");
      return(false);
     }

   return(true);
  }

//+------------------------------------------------------------------+
//| Place a market order (learning mode — 0.01 lot, SL/TP attached)  |
//+------------------------------------------------------------------+
bool PlaceOrder(const string symbol, const string action, const double entry, const double sl, const double tp, const double lot)
  {
   ENUM_ORDER_TYPE otype = (action == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   MqlTradeRequest req = {};
   MqlTradeResult  res = {};
   req.action    = TRADE_ACTION_DEAL;
   req.symbol    = symbol;
   req.volume    = lot;
   req.type      = otype;
   req.price     = SymbolInfoDouble(symbol, (otype == ORDER_TYPE_BUY) ? SYMBOL_ASK : SYMBOL_BID);
   req.sl        = (sl > 0) ? sl : 0;
   req.tp        = (tp > 0) ? tp : 0;
   req.deviation = 20;
   req.comment   = ORDER_COMMENT;
   if(!OrderSend(req, res))
     {
      Print("ORDER FAILED: retcode=", res.retcode, " | comment=", res.comment);
      return(false);
     }
   Print("ORDER PLACED: ", action, " ", symbol, " lot=", lot,
         " @", DoubleToString(req.price, 2),
         " sl=", DoubleToString(req.sl, 2), " tp=", DoubleToString(req.tp, 2),
         " ticket=", res.order);
   g_dayTrades++;
   g_lastSignalAction = action;
   g_lastOrderTicket = IntegerToString(res.order);
   return(true);
  }

//+------------------------------------------------------------------+
//| Parse + handle a new signal                                      |
//+------------------------------------------------------------------+
void HandleSignal(const string json)
  {
   string symbol = "", action = "", reason = "", ts = "";
   double entry = 0, sl = 0, tp = 0, lot = 0;
   JsonGetString(json, "symbol", symbol);
   JsonGetString(json, "action", action);
   JsonGetString(json, "reason", reason);
   JsonGetDouble(json, "entry", entry);
   JsonGetDouble(json, "sl",    sl);
   JsonGetDouble(json, "tp",    tp);
   JsonGetDouble(json, "lot",   lot);

   if(symbol == "") symbol = "XAUUSD";

   Print("=== NEW SIGNAL DETECTED ===");
   Print("symbol=", symbol, " | action=", action, " | timestamp=", ts);
   PrintFormat("entry=%.2f | sl=%.2f | tp=%.2f | lot=%.2f", entry, sl, tp, lot);
   if(reason != "") Print("reason: ", reason);

   // SKIP → log only
   if(action == "SKIP" || action == "HOLD" || action == "")
     {
      Print(">>> NO ORDER (action=", action, ") <<<");
      return;
     }

   // BUY/SELL → guard + order
   if(!GuardsPass(symbol, action, entry, sl, tp, lot))
     {
      Print(">>> ORDER BLOCKED BY GUARD <<<");
      return;
     }
   PlaceOrder(symbol, action, entry, sl, tp, lot);
  }

//+------------------------------------------------------------------+
//| Expert initialization                                           |
//+------------------------------------------------------------------+
int OnInit()
  {
   Print("xau_bridge EA started (stage 3: LEARNING MODE). InpEnabled=", InpEnabled);
   Print("signal path: ", InpSignalPath, " | poll: ", InpPollSec, "s");
   if(FileIsExist(InpSignalPath))
      Print("signal.json EXISTS at: ", InpSignalPath);
   else
      Print("signal.json NOT FOUND at: ", InpSignalPath,
            " — check junction (Files\\signals -> hermes-logs\\signals) or copy file.");
   CheckDayRollover();
   EventSetTimer(InpPollSec);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   Print("xau_bridge EA stopped. reason=", reason);
  }

//+------------------------------------------------------------------+
//| Timer — poll signal.json every InpPollSec                       |
//+------------------------------------------------------------------+
void OnTimer()
  {
   CheckDayRollover();

   string content;
   if(!FileReadAllText(InpSignalPath, content))
     {
      if(!FileReadAllText("signal.json", content))
         return;
     }

   string ts;
   if(!JsonGetString(content, "timestamp", ts))
      return;
   StringTrimLeft(ts);
   StringTrimRight(ts);

   if(ts == g_lastTimestamp)
      return;

   g_lastTimestamp = ts;
   HandleSignal(content);
  }
//+------------------------------------------------------------------+