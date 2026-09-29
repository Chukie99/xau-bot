#!/usr/bin/env python3
"""memory_manager.py — Persistent memory for XAUUSD hybrid (JSON + markdown).

ROLE:    Read/write memory/memory.json (trades, win_rate, positions,
         performance_by_condition, max_drawdown) + journal.md + progress.md.
STATUS:  IMPLEMENTED (stage 2). Robust to missing/corrupt files.
"""
import os, json, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.path.join(ROOT, 'memory')
MEMORY_JSON = os.path.join(MEMORY_DIR, 'memory.json')
JOURNAL = os.path.join(MEMORY_DIR, 'journal.md')

DEFAULT_MEMORY = {
    "trades": [],
    "win_rate": {},
    "active_positions": [],
    "performance_by_condition": {},
    "max_drawdown": 0
}

def load_memory() -> dict:
    """Read memory.json, merge with defaults. Corrupt/missing -> fresh defaults."""
    mem = dict(DEFAULT_MEMORY)
    if os.path.exists(MEMORY_JSON):
        try:
            with open(MEMORY_JSON, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    mem[k] = v
        except Exception as e:
            print(f'[memory] WARN: corrupt memory.json ({e}) — using defaults', flush=True)
    return mem

def write_memory(mem: dict) -> None:
    """Atomic write memory.json (tmp + replace)."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    tmp = MEMORY_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MEMORY_JSON)   # atomic on Windows

def append_journal(entry: str) -> None:
    """Append a markdown entry to journal.md with a leading blank line."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    if not os.path.exists(JOURNAL):
        with open(JOURNAL, 'w', encoding='utf-8') as f:
            f.write('# Trade Journal — XAUUSD Hybrid Bot\n\n')
    with open(JOURNAL, 'a', encoding='utf-8') as f:
        f.write('\n' + entry.strip() + '\n')

def get_performance_by_condition(mem: dict | None = None) -> dict:
    """Return win-rate stats by market condition (from memory.json)."""
    m = mem if mem is not None else load_memory()
    return m.get('performance_by_condition', {})

def add_trade(mem: dict, trade: dict) -> dict:
    """Append a closed-trade record, update win_rate + performance_by_condition."""
    mem.setdefault('trades', []).append(trade)
    # win_rate overall
    wins = sum(1 for t in mem['trades'] if t.get('pnl', 0) > 0)
    total = max(1, len(mem['trades']))
    mem.setdefault('win_rate', {})['overall'] = round(wins / total, 3)
    # by condition (if provided)
    cond = trade.get('condition')
    if cond:
        stats = mem.setdefault('performance_by_condition', {}).setdefault(cond, {'trades': 0, 'wins': 0, 'pnl': 0.0})
        stats['trades'] += 1
        if trade.get('pnl', 0) > 0:
            stats['wins'] += 1
        stats['pnl'] += trade.get('pnl', 0)
        stats['win_rate'] = round(stats['wins'] / stats['trades'], 3)
    return mem

if __name__ == '__main__':
    print('[memory_manager] OK')
    m = load_memory()
    print('trades:', len(m.get('trades', [])), '| win_rate:', m.get('win_rate'), '| max_dd:', m.get('max_drawdown'))