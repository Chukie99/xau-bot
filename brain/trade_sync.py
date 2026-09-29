#!/usr/bin/env python3
"""trade_sync.py — record closed trades from MetaTrader 5 into memory.json.

WHY: memory.json was never written by anything. The brain only ever called
load_memory(), so the LLM was shown the same three numbers forever. This module
is the missing write path.

It reads MT5 deal history, pairs each opening deal with its closing deals by
position_id, and records one entry per completed round turn. It is incremental
and safe to run every cycle: add_trade() drops a position_id it has already
seen, so re-running over the same history costs nothing and adds nothing.

It never writes on a live connection's behalf — it only reads history and
appends to memory.json. No order is placed from here.

FAILURE BEHAVIOUR: if MetaTrader 5 cannot be initialised, this returns a
'status': 'mt5_unavailable' report and writes nothing. A brain cycle that runs
while the terminal is closed should log that fact rather than record an empty
trade list, which would read as "this bot has never traded".
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory_manager as mm

WIB = timezone(timedelta(hours=7))

# How far back to look on the very first run. A year of M15 scalping would be
# tens of thousands of rows; the brain only ever shows aggregates, so a window
# that comfortably covers an active account is enough.
INITIAL_LOOKBACK_DAYS = 90

# Deals older than this are not recorded even if present in the window. Keeps
# memory.json readable and stops the LLM prompt from growing without bound.
MAX_HOLD_HOURS = 24 * 14


def _deal_entry_role(deal, mt5):
    return 'in' if deal.entry == mt5.DEAL_ENTRY_IN else 'out'


def collect_round_turns(mt5, lookback_days: int = INITIAL_LOOKBACK_DAYS):
    """Return one dict per completed trade, newest last. Read-only."""
    now = datetime.now(timezone.utc)
    frm = now - timedelta(days=lookback_days)
    deals = mt5.history_deals_get(frm.timestamp(), now.timestamp())
    if not deals:
        return []

    by_position = {}
    for d in deals:
        by_position.setdefault(d.position_id, []).append(d)

    out = []
    for pid, group in by_position.items():
        ins = [x for x in group if x.entry == mt5.DEAL_ENTRY_IN]
        outs = [x for x in group if x.entry == mt5.DEAL_ENTRY_OUT]
        if not ins or not outs:
            continue  # still open, or opened and closed outside the window

        opener = min(ins, key=lambda x: x.time)
        volume = sum(x.volume for x in outs)
        if volume <= 0:
            continue
        closed_at = max(x.time for x in outs)
        opened_at = min(x.time for x in ins)
        if (closed_at - opened_at) / 3600 > MAX_HOLD_HOURS:
            continue  # abandoned position, not a trade this system took

        # P/L is the sum across every closing deal: a position closed in two
        # parts realises two results, and reading only the first would understate
        # or misrepresent it. Swap and commission are real cost and belong here.
        pnl = sum(x.profit + x.swap + x.commission for x in outs)

        out.append({
            'position_id': pid,
            'side': 'BUY' if opener.type == mt5.DEAL_TYPE_BUY else 'SELL',
            'volume': round(volume, 2),
            'entry': round(opener.price, 2),
            'exit': round(sum(x.volume * x.price for x in outs) / volume, 2),
            'pnl': round(pnl, 2),
            'opened': datetime.fromtimestamp(opened_at, WIB).strftime('%Y-%m-%d %H:%M'),
            'closed': datetime.fromtimestamp(closed_at, WIB).strftime('%Y-%m-%d %H:%M'),
            'hold_min': int((closed_at - opened_at) / 60),
            'source': opener.comment or 'manual',
        })

    out.sort(key=lambda t: t['closed'])
    return out


def _condition_for(trade: dict) -> str:
    """Bucket a trade the way performance_by_condition is keyed.

    Side alone is the honest split here: this bot has only ever been evaluated
    one direction at a time, so 'SELL|swing' against 'BUY|intraday' is the fact
    that matters. Hold length is included because a 3-day swing and a 50-minute
    intraday trade are not comparable even when the side matches.
    """
    kind = 'swing' if trade.get('hold_min', 0) >= 240 else 'intraday'
    return f"{trade.get('side', '?')} | {kind}"


def sync(memory_path: str | None = None) -> dict:
    """Record any completed trades MT5 knows about. Returns a small report."""
    try:
        import MetaTrader5 as mt5
    except ImportError as e:
        return {'status': 'mt5_not_installed', 'added': 0, 'detail': str(e)}

    # initialize() with no path, not initialize(path=...): the terminal is
    # already logged in, and pointing at an executable from outside is refused.
    if not mt5.initialize():
        return {'status': 'mt5_unavailable', 'added': 0, 'detail': str(mt5.last_error())}

    try:
        turns = collect_round_turns(mt5)
    finally:
        mt5.shutdown()

    if not turns:
        return {'status': 'no_trades', 'added': 0}

    if memory_path:
        mm.MEMORY_JSON = memory_path

    mem = mm.load_memory()
    before = len(mem.get('trades', []))
    for t in turns:
        t['condition'] = _condition_for(t)
        mem = mm.add_trade(mem, t)
    added = len(mem.get('trades', [])) - before

    # Peak-to-trough on the closed-trade curve, in dollars. Recomputed rather
    # than incremented: it is a property of the whole series, so a re-run that
    # adds nothing must still leave it correct.
    running = peak = 0.0
    worst = 0.0
    for t in mem.get('trades', []):
        running += t.get('pnl', 0) or 0
        peak = max(peak, running)
        worst = max(worst, peak - running)
    mem['max_drawdown'] = round(worst, 2)

    if added:
        mm.write_memory(mem)

    return {
        'status': 'ok',
        'added': added,
        'total': len(mem.get('trades', [])),
        'win_rate': mem.get('win_rate', {}).get('overall'),
        'max_drawdown': mem['max_drawdown'],
    }


if __name__ == '__main__':
    import json
    print(json.dumps(sync(), indent=2))
