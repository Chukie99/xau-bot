# XAU Trading Bot — setup

Runs an LLM-driven XAUUSD strategy on your own MetaTrader 5 account.

The bot is two halves that talk through one file:

```
  every 15 min                        every 10 sec
  brain.py  ──writes──▶  signals/signal.json  ──reads──▶  xau_bridge EA
  (Python, decides)                                          (MQL5, executes)
```

`signal.json` is the whole contract between them. The brain decides BUY/SELL/SKIP
with a price, stop-loss and target. The EA refuses anything unsafe and sends the
order. Neither talks to the other directly, so you can stop either half without
breaking the other.

---

## Step 1 — double-click `setup.bat`

It finds your Python, installs four packages, copies the EA into MetaTrader's
folder, connects the signals folder, and registers a Windows task that runs the
brain every 15 minutes. It asks no questions. Run it once.

It will stop and tell you if it cannot find something — fix that one thing and
run it again. Running it twice is harmless.

---

## Step 2 — fill in `.env`

Setup creates `.env` from `.env.example`. Open it in Notepad. Three things you
must set:

| Key | Where to get it |
|---|---|
| `MT5_LOGIN` | Your account number |
| `MT5_PASSWORD` | Your account password |
| `MT5_SERVER` | MT5 → File → Login to Trade Account. Copy it exactly, including any `-Demo` suffix |
| `LLM_API_KEY` | Your key for whichever provider you set in `LLM_BASE_URL` |

Save. That is the only configuration you will ever need to touch.

**The Telegram keys are optional.** Leave them blank and the bot simply sends no
notifications.

> **Without a working `LLM_API_KEY` the bot will not trade.** It writes `SKIP`
> to `signal.json` instead of a decision, and does so silently — no error, no
> alert. If setup passes and you still see no orders after a few cycles, this
> key is the first thing to check.

---

## Step 3 — compile the EA (MetaEditor, ~1 minute)

This is the one part that has to be done by hand. MetaTrader provides no way to
compile from the command line, so it is a few clicks in the UI.

1. Open MetaTrader 5
2. Press `F4` for the **Navigator** window
3. Expand **Expert Advisors**
4. Right-click `xau_bridge` → **Edit**
5. MetaEditor opens. Press `F7` to compile
6. Bottom panel must read **`0 error(s)`**. If it does not, the error is named
   there — the most likely cause is that the `.mqh` includes were not copied,
   which means re-run `setup.bat`
7. Switch back to MetaTrader and press `F7` there too

---

## Step 4 — attach the EA to a chart

Also by hand, three clicks.

1. In MetaTrader, open **XAUUSD, M15**
2. Drag `xau_bridge` from Navigator onto the chart
3. In the dialog: tick **Allow Algo Trading**, leave every other value alone, OK
4. Confirm the top toolbar shows a green smiley — AutoTrading is on

The button text in the toolbar should read **Algo Trading: ON**. If it says
OFF, click it.

---

## Step 5 — confirm it is alive

Wait one cycle, then check `logs\brain.log`. A healthy line looks like:

```
[2026-09-28 21:45:42] fetch OK — indicators: H1(RSI=20.58), H4(RSI=20.84)
[2026-09-28 21:47:23] LLM decision: BUY | conf=Med | reason: ...
[2026-09-28 21:47:23] signal.json written: action=BUY entry=4120.7 sl=4062.64
```

In MetaTrader, the **Experts** tab (bottom panel) shows what the EA did with it:

```
=== NEW SIGNAL DETECTED ===
symbol=XAUUSD | action=BUY
>>> NO ORDER (action=BUY) <<<       ← not this
ORDER PLACED: BUY XAUUSD lot=0.01    ← this
>>> ORDER BLOCKED BY GUARD <<<       ← or this, and the line above says why
```

**Running but not trading is the normal state.** The bot takes at most 3 trades
a day, and a guard will refuse a signal that arrives at a bad spread, outside
the risk budget, or while 4 positions are already open. Those refusals are the
system working. `ORDER PLACED` is the line that means money moved.

---

## What the guards are, and why they refuse

The EA will not send an order that fails any of these. They are checked in
order, cheapest first:

| Guard | Limit | Why it exists |
|---|---|---|
| Enabled flag | `InpEnabled` | The master off switch |
| Weekend | Sat/Sun | Gold has no market to trade against |
| SL/TP present | must be set | A stop-less position is not a position |
| Lot | 0.01 | Broker minimum; anything larger is rejected |
| Spread | `$3.00` | Wide spread eats the edge before the trade starts |
| Slippage | 20 pips | Measured against the side's own fill price — a SELL fills at bid, a BUY at ask |
| Daily loss | 1% of balance | The hard stop for the day |
| Drawdown | 5% of balance | The hard stop for everything |
| Trades/day | 3 | Deliberately slow |
| Open positions | 4 (`InpMaxPositions`) | Caps concurrent exposure |

The daily loss and drawdown limits are percentages of your **balance**, not of
a fixed dollar figure, so they scale to whatever account you point it at.

Change any of them in the EA's properties: right-click the EA on the chart →
Properties. `InpMaxPositions` and `InpMaxSpreadUsd` are the two worth touching.

---

## Tuning

Right-click the EA on the chart → **Properties**:

| Input | Default | Raise it if… |
|---|---|---|
| `InpEnabled` | `true` | Nothing — this is the kill switch. Set false to pause trading without removing the EA |
| `InpPollSec` | 10 | Nothing. 10s is already responsive |
| `InpMaxSpreadUsd` | 3.0 | You see `spread too wide` often and want more trades at the cost of worse entry prices |
| `InpMaxPositions` | 4 | You want more exposure. Understand that each position is an independent risk |

The LLM's own view is in `brain/llm_client.py` → `_build_prompt()`. That is where
you change what the model is told to weigh. It is a prompt, not code — edit it
in plain English.

---

## Files

```
setup.bat              one-time installer
.env.example           template; copy to .env and fill in
START_HERE.md          this file

brain/
  brain.py             the cycle: fetch → check risk → ask the LLM → write signal
  xau_fetch.py         reads MetaTrader 5, computes EMA/RSI/MACD/BB/ATR
  llm_client.py        asks the model for BUY/SELL/SKIP; edit _build_prompt here
  memory_manager.py    keeps track of past trades and writes the journal
  news_filter.py       blocks entries around high-impact economic releases
  ta.py                indicator maths, no MetaTrader dependency
  mql5/
    xau_bridge.mq5     the expert advisor
    risk_guard.mqh     the limits, in one file
    json_parser.mqh    flat-JSON reader for MQL5

signals/               signal.json, history.jsonl  (created by setup)
logs/                  brain.log  (created by setup)
memory/                memory.json, journal.md  (created by setup)
```

---

## Turning it off

Set `InpEnabled` to `false` in the EA's properties and click OK. The brain keeps
writing signals, the EA keeps reading them, and nothing is sent. Set it back to
`true` to resume.

To stop completely, remove the EA from the chart. To stop the brain too, open
Task Scheduler, find `XauBot Brain`, and disable it.

Note that existing positions are **not** closed by any of this. The EA only
opens. Manage open trades in MetaTrader.

---

## Before you trade real money

Run it on a **demo account** for at least a month first. Not because the code is
unfinished — it works, and you can verify that in a day — but because the
strategy's edge is unproven. A week of profitable demo trading is one data
point, not a track record, and no amount of code changes that. Demo is free;
discover a 40% drawdown on a live account and it is not.

This bot places real orders. You are responsible for what it does with your
money.
