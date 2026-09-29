#!/usr/bin/env python3
"""news_filter.py — Economic calendar guard (read-only, gratis, no key).

Sumber: ForexFactory JSON (nfs.faireconomy.media/ff_calendar_thisweek.json).
Behavior:
  - Fetch calendar seminggu sekali (cache 6 jam) — hemat bandwidth.
  - Deteksi event HIGH impact US/EUR/GBP: NFP, CPI, FOMC, GDP, dst.
  - Skip entry 5 menit sebelum & sesudah event (window ±5 menit).
  - Kalau fetch gagal / cache kosong → AMAN (tidak block) — fail-open.
"""
import os, json, time, urllib.request, datetime

FF_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'signals', 'news_cache.json')
CACHE_TTL = 6 * 3600          # 6 jam
WINDOW_MIN = 5                # skip 5 menit sebelum/sesudah news
HIGH_COUNTRIES = {'USD', 'EUR', 'GBP'}   # pengaruh XAU paling besar

# Judul event yang dianggap kritis (memicu spread melebar + volatilitas)
CRITICAL_TITLES = ('NFP', 'NON-FARM', 'CPI', 'CONSUMER PRICE', 'FOMC', 'FED FUNDS',
                   'INTEREST RATE', 'GDP', 'UNEMPLOYMENT', 'PCE', 'RETAIL SALES',
                   'ISM MANUFACTURING', 'JOBLESS CLAIMS', 'PPI', 'PRODUCER PRICE')

def _fetch() -> list:
    req = urllib.request.Request(FF_URL, headers={'User-Agent': 'Mozilla/5.0 (XAU-bot)'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8', errors='replace'))

def _load_cache() -> list:
    try:
        with open(CACHE_FILE, encoding='utf-8') as f:
            d = json.load(f)
        if time.time() - d.get('fetched', 0) < CACHE_TTL:
            return d.get('events', [])
    except Exception:
        pass
    return []

def _save_cache(events: list) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'fetched': time.time(), 'events': events}, f)
    except Exception:
        pass

def get_upcoming_events(force_refresh=False) -> list:
    """Kembalikan list event HIGH impact (USD/EUR/GBP) minggu ini. Fail-open."""
    events = [] if force_refresh else _load_cache()
    if not events:
        try:
            raw = _fetch()
            for ev in raw:
                title = str(ev.get('title', '')).upper()
                country = str(ev.get('country', '')).upper()
                impact = str(ev.get('impact', '')).upper()
                if impact == 'HIGH' and country in HIGH_COUNTRIES:
                    # kritis kalau judul match OR event high-impact apa pun (conservative)
                    events.append({
                        'title': ev.get('title', ''),
                        'country': country,
                        'date': ev.get('date', ''),
                        'critical': any(k in title for k in CRITICAL_TITLES),
                    })
            _save_cache(events)
        except Exception as e:
            print(f'[news] fetch gagal (fail-open): {e}', flush=True)
            return []
    return events

def news_block_reason(now=None) -> str:
    """Return reason string kalau sekarang dalam window news (5 mnt sebelum/sesudah), else ''."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
    events = get_upcoming_events()
    if not events:
        return ''
    now_ts = now.timestamp()
    for ev in events:
        try:
            ev_dt = datetime.datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
            ev_ts = ev_dt.timestamp()
        except Exception:
            continue
        if abs(now_ts - ev_ts) <= WINDOW_MIN * 60:
            return f'NEWS WINDOW: {ev["title"]} ({ev["country"]}) — {ev["date"]} (±{WINDOW_MIN} mnt). SKIP.'
    return ''

if __name__ == '__main__':
    print(json.dumps(get_upcoming_events(), indent=2, ensure_ascii=False))