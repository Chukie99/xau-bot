#!/usr/bin/env python3
"""xau_fetch.py — XAUUSD fetcher + TA (standalone, read-only).

Connects to MT5 (Exness demo via .env), fetches XAUUSD H1/H4/D1,
computes indicators (EMA5/21, RSI14, MACD12/26/9, BB20x2, ATR14), prints JSON.

READ-ONLY: no order placement, no write APIs. Password from .env only.
Usage:
    python xau_fetch.py [--tf H1,H4,D1] [--bars 200] [--json out.json]
"""
import os, sys, json, argparse
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')  # hermes-logs/.env
load_dotenv(ENV_PATH)

def get_creds():
    login = os.environ.get('MT5_LOGIN')
    pwd = os.environ.get('MT5_PASSWORD')
    srv = os.environ.get('MT5_SERVER')
    if not all([login, pwd, srv]):
        sys.exit('ERROR: MT5_LOGIN/MT5_PASSWORD/MT5_SERVER missing in .env or env')
    return int(login), pwd, srv

#----------------- indicators (shared, importable) -----------------
def ema_series(vals, n):
    if len(vals) < n: return []
    k = 2/(n+1); e = sum(vals[:n])/n; out=[e]
    for v in vals[n:]: e = v*k + e*(1-k); out.append(e)
    return out

def rsi_series(closes, n=14):
    if len(closes) < n+1: return []
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        ch = closes[i]-closes[i-1]
        gains.append(max(ch,0.0)); losses.append(max(-ch,0.0))
    ag=sum(gains[:n])/n; al=sum(losses[:n])/n; out=[]
    for i in range(n,len(gains)):
        ag=(ag*(n-1)+gains[i])/n; al=(al*(n-1)+losses[i])/n
        out.append(100.0 if al==0 else 100-100/(1+ag/al))
    return out

def macd_series(closes, fast=12, slow=26, sig=9):
    ef=ema_series(closes,fast); es=ema_series(closes,slow)
    if not ef or not es: return [],[],[]
    m=[f-s for f,s in zip(ef,es)]
    k=2/(sig+1); sigv=[m[0]]
    for v in m[1:]: sigv.append(v*k+sigv[-1]*(1-k))
    hist=[v-s for v,s in zip(m,sigv)]
    return m,sigv,hist

def bb_series(closes, n=20, mult=2.0):
    if len(closes) < n: return [],[],[]
    mids=[]; ups=[]; lows=[]
    for i in range(n-1, len(closes)):
        window = closes[i-n+1:i+1]
        mid = sum(window)/n
        sd = (sum((x-mid)**2 for x in window)/n)**0.5
        mids.append(mid); ups.append(mid+mult*sd); lows.append(mid-mult*sd)
    return lows,mids,ups

def atr_series(highs, lows, closes, n=14):
    """ATR (Wilder). Returns list aligned to closes."""
    if len(closes) < n+1: return []
    trs=[]
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    a = sum(trs[:n])/n
    out=[a]
    for tr in trs[n:]:
        a = (a*(n-1)+tr)/n
        out.append(a)
    return out

#----------------- analyze -----------------
def analyze(sym, tf, label, count=200):
    import MetaTrader5 as mt5, pandas as pd
    rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
    if rates is None: return {'symbol': sym, 'tf': label, 'error': str(mt5.last_error())}
    df = pd.DataFrame(rates)
    closes = df['close'].tolist()
    highs = df['high'].tolist()
    lows = df['low'].tolist()
    price = closes[-1]
    rsi = rsi_series(closes)
    m,s,h = macd_series(closes)
    e5 = ema_series(closes,5); e21 = ema_series(closes,21)
    blo,bmid,bup = bb_series(closes)
    atr = atr_series(highs, lows, closes)
    vol_avg = df['tick_volume'].tail(50).mean(); vol_last = df['tick_volume'].iloc[-1]
    last_t = pd.to_datetime(df['time'].iloc[-1], unit='s')
    out = {'symbol': sym,'tf': label,'last_time': str(last_t),'price': round(price,2),
           'RSI': round(rsi[-1],2) if rsi else None,
           'MACD_hist': round(h[-1],2) if h else None,
           'MACD': round(m[-1],2) if m else None,
           'EMA5': round(e5[-1],2) if e5 else None,
           'EMA21': round(e21[-1],2) if e21 else None,
           'BB_low': round(blo[-1],2) if blo else None,
           'BB_mid': round(bmid[-1],2) if bmid else None,
           'BB_up': round(bup[-1],2) if bup else None,
           'ATR14': round(atr[-1],2) if atr else None,
           'vol_last': int(vol_last), 'vol_avg50': round(vol_avg,1),
           'vol_ratio': round(vol_last/vol_avg,2) if vol_avg else None}
    out['pos_vs_EMA21'] = 'ABOVE' if price > (out['EMA21'] or 0) else ('BELOW' if price < (out['EMA21'] or 0) else 'AT')
    out['pos_vs_BB'] = ('ABOVE_UP' if price > (out['BB_up'] or 0) else ('BELOW_LOW' if price < (out['BB_low'] or 0) else 'INSIDE'))
    out['EMA5_vs_EMA21'] = 'ABOVE' if out['EMA5'] and out['EMA21'] and out['EMA5']>out['EMA21'] else ('BELOW' if out['EMA5'] and out['EMA21'] and out['EMA5']<out['EMA21'] else 'N/A')
    return out

def fetch_and_analyze(tfs=('H1','H4','D1'), bars=200, symbol='XAUUSD'):
    """Connect, fetch, analyze. Returns (meta_dict, results_dict)."""
    import MetaTrader5 as mt5
    login, pwd, srv = get_creds()
    ok = mt5.initialize()
    if not ok:
        ok = mt5.initialize('C:/Program Files/MetaTrader 5/terminal64.exe')
    if not ok:
        raise RuntimeError(f'MT5 init failed: {mt5.last_error()}')
    auth = mt5.login(login, password=pwd, server=srv)
    if not auth:
        mt5.shutdown(); raise RuntimeError(f'MT5 login failed: {mt5.last_error()}')
    acct = mt5.account_info()
    tfmap = {'M1':mt5.TIMEFRAME_M1,'M5':mt5.TIMEFRAME_M5,'M15':mt5.TIMEFRAME_M15,'H1':mt5.TIMEFRAME_H1,'H4':mt5.TIMEFRAME_H4,'D1':mt5.TIMEFRAME_D1}
    results = {}
    for label in tfs:
        r = analyze(symbol, tfmap[label], label, bars)
        results[label] = r
    meta = {
        'account': {'login': acct.login, 'server': acct.server, 'currency': acct.currency,
                    'balance': acct.balance, 'equity': acct.equity, 'leverage': acct.leverage} if acct else None,
        'symbol': symbol, 'generated': 'read-only',
    }
    mt5.shutdown()
    return meta, results

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tf', default='H1,H4,D1')
    ap.add_argument('--bars', type=int, default=200)
    ap.add_argument('--json', default=None, help='write JSON to this file')
    args = ap.parse_args()
    tfs = [x.strip().upper() for x in args.tf.split(',') if x.strip().upper() in ('M1','M5','M15','H1','H4','D1')]
    meta, results = fetch_and_analyze(tfs, args.bars)
    out = {'meta': meta, 'indicators': results}
    print(json.dumps(out, indent=2, default=str))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2, default=str)

if __name__ == '__main__':
    main()