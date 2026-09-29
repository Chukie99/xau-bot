# xau-bot

An LLM-driven XAUUSD (gold) strategy for MetaTrader 5. Point it at an account,
fill in four credentials, and it reads the market, decides, and places orders —
with ten risk guards standing between the decision and the order.

Windows only. MetaTrader 5 required.

**This places real orders. Start on a demo account.** See
[Before you trade real money](#before-you-trade-real-money) — the short version
is that the strategy's edge is unproven and this repository is not evidence that
it isn't.

---

## How it works

Two halves, talking through one file:

```
  every 15 min                          every 10 sec
  brain.py  ──writes──▶  signals/signal.json  ──reads──▶  xau_bridge EA
  (Python, decides)                                        (MQL5, executes)
```

`signal.json` is the entire contract between them. The brain fetches indicators
from MetaTrader 5, blocks entries around high-impact economic releases, asks an
LLM for `BUY` / `SELL` / `SKIP` with a price, stop-loss and target, and writes
the decision. The EA reads that decision, runs ten guards over it, and sends the
order if it survives.

Neither half talks to the other directly, so you can stop either one without
breaking the other. The brain cannot place an order without the EA's consent,
and the EA cannot decide anything on its own.

The decision step uses any OpenAI-compatible endpoint — OpenAI, DeepSeek, Groq,
or a local Ollama, none of which need any particular gateway. If the primary
endpoint is down, the bot retries a second one you configure, rather than
quietly writing `SKIP` and doing nothing.

Change what the model is told to weigh in
[`brain/llm_client.py`](brain/llm_client.py) → `_build_prompt()` — that is a
prompt, not code, and it is meant to be edited in plain English.

**On model choice:** use a fast model, not a reasoning one. The call times out
at 40 seconds and the brain runs every 15 minutes, so a model that spends its
budget thinking about one candle arrives too late to matter. Consistency
between consecutive runs matters more here than cleverness.

---

## Setup

Full instructions: **[START_HERE.md](START_HERE.md)**. The short version:

1. Double-click `setup.bat` — installs Python packages, copies the EA into
   MetaTrader, connects the signals folder, registers a scheduled task
2. Edit `.env` with your MT5 credentials and your LLM API key
3. Compile the EA in MetaEditor (`F7`, must read `0 error(s)`)
4. Attach `xau_bridge` to an **XAUUSD M15** chart, tick **Allow Algo Trading**
5. Wait one cycle, then check `logs/brain.log`

Steps 3 and 4 are manual because MetaTrader exposes no command line for either.

**If it installs but never trades:** the bot writes `SKIP` to `signal.json`
silently when the LLM call fails. No error, no alert. A working `LLM_API_KEY` in
`.env` is the first thing to check.

---

## The guards

The EA will not send an order that fails any of these. Checked in order,
cheapest first:

| Guard | Limit | Why |
|---|---|---|
| `InpEnabled` | flag | Master off switch |
| Weekend | Sat/Sun | No market to trade against |
| SL/TP present | required | A stop-less position is not a position |
| Lot | 0.01 | Broker minimum |
| Spread | `$3.00` | Wide spread eats the edge before the trade starts |
| Slippage | 20 pips | Measured against each side's own fill price |
| Daily loss | 1% of balance | The hard stop for the day |
| Drawdown | 5% of balance | The hard stop for everything |
| Trades/day | 3 | Deliberately slow |
| Open positions | 4 | Caps concurrent exposure |

The last two loss limits are percentages of your **balance**, so they scale to
whatever account you point it at. Change any of them in the EA's properties:
right-click it on the chart → Properties.

---

## Layout

```
setup.bat              one-time installer
.env.example           template — copy to .env and fill in
START_HERE.md          step-by-step setup

brain/
  brain.py             the cycle: fetch → check risk → ask the LLM → write signal
  xau_fetch.py         reads MetaTrader 5, computes EMA/RSI/MACD/BB/ATR
  llm_client.py        asks the model for BUY/SELL/SKIP
  memory_manager.py    records past trades, writes the journal
  news_filter.py       blocks entries around high-impact releases
  ta.py                indicator maths, no MetaTrader dependency
  mql5/
    xau_bridge.mq5     the expert advisor
    risk_guard.mqh     the limits, in one file
    json_parser.mqh    flat-JSON reader for MQL5

signals/   signal.json, history.jsonl
logs/      brain.log
memory/    memory.json, journal.md
```

Everything is relative to the folder the scripts live in. Move the whole
directory and it keeps working.

---

## Before you trade real money

Run it on a demo account for at least a month first. Not because the code is
unfinished — it runs, and you can verify that in a day — but because the
strategy's edge is unproven. A week of profitable demo trading is one data
point, not a track record, and no amount of code changes that. Demo is free;
discover a 40% drawdown on a live account and it is not.

`memory/memory.json` does not yet record trade results, so this repository
carries no performance history. If you generate some, that file is where it
lives, and you are welcome to open a pull request with it.

You are responsible for what this does with your money.
