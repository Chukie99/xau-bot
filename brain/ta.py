"""TA validation: 1H RSI(14), MACD(12,26,9), EMA5/EMA21 for Hyperliquid exports."""
import json, sys, math, datetime

def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None

def ema_series(vals, n):
    if len(vals) < n:
        return []
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    out = [e]
    for v in vals[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out

def rsi_series(closes, n=14):
    if len(closes) < n + 1:
        return []
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i-1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    out = []
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out

def macd_series(closes, fast=12, slow=26, sig=9):
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    if not ef or not es:
        return [], [], []
    m = [f - s for f, s in zip(ef, es)]
    # signal = EMA of macd; align lengths (macd is shorter than closes by slow-1)
    k = 2 / (sig + 1)
    sigv = [m[0]]
    for v in m[1:]:
        sigv.append(v * k + sigv[-1] * (1 - k))
    hist = [v - s for v, s in zip(m, sigv)]
    return m, sigv, hist

def analyze(path, coin):
    d = json.load(open(path, encoding='utf-8'))
    cs = d['candles']
    closes = [float(c['close']) for c in cs]
    vols = [float(c['volume']) for c in cs]
    last = cs[-1]
    # RSI
    rsi = rsi_series(closes, 14)
    # MACD
    macd, sig, hist = macd_series(closes, 12, 26, 9)
    # EMAs
    e5 = ema_series(closes, 5)
    e21 = ema_series(closes, 21)
    price = closes[-1]
    ema5 = e5[-1] if e5 else None
    ema21 = e21[-1] if e21 else None
    print(f'\n=== {coin} ===')
    tz7 = datetime.timezone(datetime.timedelta(hours=7))
    print(f'Last candle (close WIB): {datetime.datetime.fromtimestamp(last["time"]/1000, tz7).strftime("%Y-%m-%d %H:%M")} close={price}')
    print(f'RSI(14): {rsi[-1]:.2f} (n={len(rsi)})')
    print(f'MACD hist: {hist[-1]:.4f} (macd={macd[-1]:.4f} sig={sig[-1]:.4f}, n={len(hist)})')
    ema5v = f'{ema5:.2f}' if ema5 else 'N/A'
    ema21v = f'{ema21:.2f}' if ema21 else 'N/A'
    print(f'EMA5={ema5v} EMA21={ema21v} price={price} -> price{" ABOVE" if ema5 and ema21 and price > ema21 else " below"} EMA21')
    print(f'EMA5>EMA21: {ema5 > ema21 if ema5 and ema21 else "N/A"}')
    # volume vs avg
    avg_vol = sum(vols[:-1]) / (len(vols) - 1) if len(vols) > 1 else None
    if avg_vol:
        print(f'Last vol {vols[-1]:.2f} vs avg(prev) {avg_vol:.2f} = {vols[-1]/avg_vol*100:.0f}%')

for arg in sys.argv[1:]:
    analyze(arg, arg.split('-')[0])