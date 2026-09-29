#!/usr/bin/env python3
"""brain.py — XAUUSD: Python brain (decision) + MQL5 EA (executor).

ROLE:    One cycle: fetch -> indicators -> memory -> LLM decision ->
         write signals/signal.json (EA reads) + append journal.
STATUS:  IMPLEMENTED (stage 2). EA stays READ-ONLY; this only writes signal.json.

FLOW:
    1. xau_fetch.fetch_and_analyze() -> indicators (H1/H4/D1) + account meta
    2. memory_manager.load_memory() -> memory
    3. llm_client.analyze_xau(memory, indicators) -> decision
    4. write signals/signal.json (symbol, action, entry, sl, tp, lot, timestamp, reason)
    5. append journal.md + update memory.json (signal logged, no trade yet)
"""
import os, sys, json, time, datetime

# allow running from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xau_fetch
import memory_manager
import llm_client
import news_filter
import trade_sync

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS_DIR = os.path.join(ROOT, 'signals')
SIGNAL_JSON = os.path.join(SIGNALS_DIR, 'signal.json')
LOGS_DIR = os.path.join(ROOT, 'logs')

def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(os.path.join(LOGS_DIR, 'brain.log'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

HISTORY_JSONL = os.path.join(SIGNALS_DIR, 'history.jsonl')
RISK_STATE_JSON = os.path.join(SIGNALS_DIR, 'risk_state.json')

#--- Learning-mode risk limits (sinkron dengan risk_guard.mqh / EA)
RISK_DAILY_LOSS_PCT = 0.01  # 1% dari modal (balance) — $1000 → $10, $100 → $1
RISK_MAX_DD_PCT     = 0.05  # 5% dari modal — halt kalau kena
RISK_MAX_TRADES_DAY = 3    # max BUY/SELL signals per day (learning)
RISK_LOT = 0.01            # FIXED lot — LLM TIDAK boleh nentuin sizing (bug: output 0.05 → guard block)

def risk_check() -> dict:
    """Cek limit risk harian sebelum tulis signal. Return {'pass': bool, 'reason': str}."""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
    if now.weekday() >= 5:
        return {'pass': False, 'reason': 'WEEKEND: pasar XAU tutup. SKIP.'}
    # NEWS FILTER — skip 5 menit sebelum/sesudah event high-impact (NFP/CPI/FOMC dsb)
    #
    # news_block_reason returns a string for two different situations, and
    # treating them alike would turn a fail-open guard into a fail-closed one:
    # an unreadable calendar must NOT stop trading, it must only be recorded.
    # So the verdict is decided by whether the reason is a real block, and the
    # uncertainty is passed through as a note instead.
    news_reason = news_filter.news_block_reason(now)
    news_note = ''
    if news_reason:
        if news_reason.startswith('NEWS UNKNOWN'):
            news_note = news_reason
            log(news_note)
        else:
            return {'pass': False, 'reason': news_reason}

    # baca trade_result.json (hasil close terakhir) — update oleh EA/ECN
    day_loss, day_trades = 0.0, 0
    try:
        with open(os.path.join(SIGNALS_DIR, 'trade_result.json'), 'r', encoding='utf-8') as f:
            tr = json.load(f)
        day_loss = float(tr.get('day_loss', 0) or 0)
        day_trades = int(tr.get('day_trades', 0) or 0)
    except Exception:
        pass
    # modal (balance) dari trade_result.json kalau ada — fallback $1000
    # (TIDAK fetch MT5 di risk_check — hemat resource, EA yang punya angka akurat)
    balance = 1000.0
    try:
        with open(os.path.join(SIGNALS_DIR, 'trade_result.json'), 'r', encoding='utf-8') as f:
            tr2 = json.load(f)
        balance = float(tr2.get('balance', 0) or 1000.0)
    except Exception:
        pass
    daily_limit = balance * RISK_DAILY_LOSS_PCT
    dd_limit = balance * RISK_MAX_DD_PCT
    if day_loss >= daily_limit:
        return {'pass': False, 'reason': f'DAILY LOSS LIMIT: ${day_loss:.2f} >= ${daily_limit:.2f} (1% modal). SKIP.'}
    if day_trades >= RISK_MAX_TRADES_DAY:
        return {'pass': False, 'reason': f'MAX TRADES/DAY: {day_trades} >= {RISK_MAX_TRADES_DAY}. SKIP.'}
    if (balance - equity_estimate()) >= dd_limit:
        return {'pass': False, 'reason': f'DRAWDOWN LIMIT: ${balance-equity_estimate():.2f} >= ${dd_limit:.2f} (5% modal). SKIP.'}
    return {'pass': True, 'reason': '', 'note': news_note}

def equity_estimate() -> float:
    """Estimasi equity dari trade_result.json kalau ada floating, fallback balance."""
    try:
        with open(os.path.join(SIGNALS_DIR, 'trade_result.json'), 'r', encoding='utf-8') as f:
            tr = json.load(f)
        eq = float(tr.get('equity', 0) or 0)
        if eq > 0:
            return eq
    except Exception:
        pass
    return 1000.0

def append_history(signal: dict, indicators: dict) -> None:
    """Append one decision to signals/history.jsonl (JSONL, one record per line).
    Each line: timestamp (WIB), indicators (RSI/MACD/EMA/vol_ratio per TF),
    decision (action/entry/sl/tp/lot/confidence), reason, meta account.
    Used for validation (3-7 day): count BUY/SELL/SKIP, reason quality."""
    import datetime as _dt
    rec = {
        'ts': signal.get('timestamp'),
        'action': signal.get('action'),
        'entry': signal.get('entry'),
        'sl': signal.get('sl'),
        'tp': signal.get('tp'),
        'lot': signal.get('lot'),
        'confidence': signal.get('confidence'),
        'reason': signal.get('reason'),
        'indicators': {
            tf: {
                'RSI': d.get('RSI'),
                'MACD_hist': d.get('MACD_hist'),
                'EMA5': d.get('EMA5'),
                'EMA21': d.get('EMA21'),
                'EMA5_vs_EMA21': d.get('EMA5_vs_EMA21'),
                'vol_ratio': d.get('vol_ratio'),
                'price': d.get('price'),
            } for tf, d in indicators.items()
        },
        'meta': signal.get('meta'),
    }
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    with open(HISTORY_JSONL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    log('history.jsonl appended')

def timestamp_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime('%Y-%m-%dT%H:%M:%S+07:00')

def write_signal(decision: dict, meta: dict) -> dict:
    """Write signal.json the EA reads. NEVER order here. Returns the signal dict."""
    signal = {
        'symbol': 'XAUUSD',
        'action': decision.get('action', 'SKIP'),
        'entry': decision.get('entry'),
        'sl': decision.get('sl'),
        'tp': decision.get('tp'),
        'lot': RISK_LOT,   # HARDCODE 0.01 — LLM tidak menentukan sizing (unreliable)
        'timestamp': timestamp_now(),
        'reason': decision.get('reason', ''),
        'confidence': decision.get('confidence', 'Low'),
        'meta': meta.get('account'),
    }
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    tmp = SIGNAL_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(signal, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SIGNAL_JSON)
    log(f'signal.json written: action={signal["action"]} entry={signal["entry"]} sl={signal["sl"]} tp={signal["tp"]}')
    return signal

def main():
    log('=== brain.py cycle start ===')

    # WEEKEND GUARD: XAU market tutup Sabtu 00:00 WIB - Senin 06:00 WIB (GMT+0)
    # Brain tetap tulis SKIP biar EA tahu & journal ke-log, tanpa LLM call (irit resource).
    now_wib = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
    if now_wib.weekday() >= 5:  # Sabtu (5) atau Minggu (6)
        reason = 'WEEKEND: pasar XAU tutup (Sabtu-Minggu). Sinyal di-skip, pantau Senin.'
        decision = {'action': 'SKIP', 'confidence': 'Low', 'reason': reason, 'entry': None, 'sl': None, 'tp': None, 'lot': None}
        signal = write_signal(decision, {})
        append_history(signal, {})  # weekend juga ke-log biar track record konsisten
        memory_manager.append_journal(f'## {timestamp_now()} (WIB)\n- Signal: **SKIP** XAUUSD | conf=Low\n- Alasan: {reason}\n- Status: weekend guard (EA read-only)')
        log(decision['reason'])
        return 0

    try:
        # 1. fetch + indicators
        meta, indicators = xau_fetch.fetch_and_analyze(('H1','H4','D1'), 200, 'XAUUSD')
        log('fetch OK — indicators: ' + ', '.join(f"{k}(RSI={v.get('RSI')})" for k, v in indicators.items()))
    except Exception as e:
        log(f'FETCH FAILED: {e}')
        write_signal({'action': 'SKIP', 'reason': f'fetch failed: {e}'}, {})
        return 1

    # 2. record anything that closed since the last cycle
    #
    #    This runs before load_memory on purpose. Loading first would mean the
    #    decision made this cycle is made against a memory that is one trade
    #    stale, which is the opposite of the point of having a memory.
    #
    #    It is read-only against MT5 and only writes memory.json. A terminal
    #    that is closed yields status 'mt5_unavailable', which is logged as
    #    that rather than treated as "no trades" — those are different facts
    #    and conflating them makes a working bot look idle.
    try:
        sr = trade_sync.sync()
        if sr.get('added'):
            log(f"trade_sync: recorded {sr['added']} closed trade(s) | "
                f"total={sr.get('total')} win_rate={sr.get('win_rate')} "
                f"max_dd={sr.get('max_drawdown')}")
        elif sr.get('status') == 'mt5_unavailable':
            log(f"trade_sync: {sr['status']} ({sr.get('detail')}) — memory not updated")
        else:
            log(f"trade_sync: no new closed trades (total={sr.get('total', '?')})")
    except Exception as e:
        log(f'trade_sync failed (non-fatal): {e}')

    # 3. memory
    mem = memory_manager.load_memory()
    log(f'memory loaded: trades={len(mem.get("trades", []))} max_dd={mem.get("max_drawdown")}')

    # 2b. RISK CHECK (learning mode) — kalau limit kena, SKIP tanpa LLM (hemat quota)
    rc = risk_check()
    if not rc['pass']:
        reason = rc['reason']
        log(f'RISK GUARD: {reason}')
        decision = {'action': 'SKIP', 'confidence': 'Low', 'reason': reason, 'entry': None, 'sl': None, 'tp': None, 'lot': None}
        signal = write_signal(decision, meta)
        append_history(signal, indicators)
        memory_manager.append_journal(f'## {timestamp_now()} (WIB)\n- Signal: **SKIP** XAUUSD | conf=Low\n- Alasan: {reason}\n- Status: risk guard (EA read-only)')
        return 0
    if rc.get('note'):
        # Not a block. The decision still gets made, but the model is told the
        # news calendar could not be read, because a decision taken on partial
        # information should be made knowingly rather than quietly.
        mem = dict(mem)
        mem['news_note'] = rc['note']

    # 3. LLM decision
    decision = llm_client.analyze_xau(mem, indicators)
    log(f'LLM decision: {decision.get("action")} | conf={decision.get("confidence")} | reason={decision.get("reason", "")[:80]}')

    # 4. write signal.json (EA reads this)
    signal = write_signal(decision, meta)

    # 4b. append to signals/history.jsonl (validation tracking)
    append_history(signal, indicators)

    # 5. journal + memory (log signal, not a trade yet)
    entry = (f'## {timestamp_now()} (WIB)\n'
             f'- Signal: **{decision.get("action")}** XAUUSD | conf={decision.get("confidence")}\n'
             f'- Indikator: RSI H1={indicators.get("H1", {}).get("RSI")} | H4={indicators.get("H4", {}).get("RSI")} | D1={indicators.get("D1", {}).get("RSI")} | MACD_hist '
             f'H1={indicators.get("H1", {}).get("MACD_hist")} | H4={indicators.get("H4", {}).get("MACD_hist")} | D1={indicators.get("D1", {}).get("MACD_hist")} | vol_ratio '
             f'H1={indicators.get("H1", {}).get("vol_ratio")} | H4={indicators.get("H4", {}).get("vol_ratio")} | D1={indicators.get("D1", {}).get("vol_ratio")}\n'
             f'- Entry: {decision.get("entry")} | SL: {decision.get("sl")} | TP: {decision.get("tp")} | lot: {decision.get("lot")}\n'
             f'- Alasan: {decision.get("reason", "")}\n'
             f'- Status: signal logged (EA read-only — BELUM eksekusi)')
    memory_manager.append_journal(entry)
    log('journal appended')

    log('=== brain.py cycle end ===')
    return 0

if __name__ == '__main__':
    sys.exit(main())