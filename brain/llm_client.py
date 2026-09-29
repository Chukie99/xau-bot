#!/usr/bin/env python3
"""llm_client.py — LLM wrapper for XAUUSD decisions (any OpenAI-compatible API).

ROLE:    Talk to any OpenAI-compatible endpoint for BUY/SELL/SKIP decisions.
         Endpoint and key come from .env. There is no dependency on any
         particular gateway — a local proxy on one machine is a deployment
         choice, not a requirement of the code.
STATUS:  IMPLEMENTED. Graceful when the endpoint is down: returns SKIP with
         the error rather than raising, so a dead provider looks like a quiet
         no-trade day instead of a crash loop.
"""
import os, json, sys
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(ENV_PATH)

# Defaults for a fresh install. Both are overridable in .env. The endpoint here
# is only a starting point: any OpenAI-compatible base_url works, including a
# local one (Ollama at http://localhost:11434/v1 costs nothing and needs no key).
#
# A reasoning model is deliberately NOT the default. The call has timeout=40
# and the brain runs on a 15-minute cycle, so a model that spends its budget
# thinking about one candle arrives too late to matter. Consistency across
# consecutive runs is what a signal generator needs, not maximum cleverness.
FALLBACK_MODEL = os.environ.get('LLM_FALLBACK_MODEL', 'gpt-4o-mini')
FALLBACK_BASE_URL = os.environ.get('LLM_FALLBACK_BASE_URL', '')
FALLBACK_API_KEY = os.environ.get('LLM_FALLBACK_API_KEY', '')

def get_llm_config():
    return {
        'base_url': os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1'),
        'api_key': os.environ.get('LLM_API_KEY', ''),
        'model': os.environ.get('LLM_MODEL', 'gpt-4o-mini'),
        'fallback_model': FALLBACK_MODEL,
        'fallback_base_url': FALLBACK_BASE_URL or os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1'),
        'fallback_api_key': FALLBACK_API_KEY or os.environ.get('LLM_API_KEY', ''),
    }

# Clients are cached per base_url rather than in one global. The fallback may
# point at a different provider with a different key, so it needs its own
# client — reusing the primary's would send the fallback request to an endpoint
# that just failed, which is the one thing a fallback must not do.
_clients = {}
def _get_client(base_url=None, api_key=None):
    from openai import OpenAI
    cfg = get_llm_config()
    url = base_url or cfg['base_url']
    key = api_key or cfg['api_key']
    if url not in _clients:
        _clients[url] = OpenAI(base_url=url, api_key=key)
    return _clients[url]

def analyze_xau(memory: dict, indicators: dict) -> dict:
    """Ask LLM for a decision. Returns dict:
       {action: BUY|SELL|SKIP, entry, sl, tp, lot, confidence, reason}
       On any failure -> {action: 'SKIP', reason: 'LLM error: ...'} (never crashes)."""
    cfg = get_llm_config()
    if not cfg['api_key'] or cfg['api_key'].startswith('sk-9router-placeholder'):
        return {'action': 'SKIP', 'entry': None, 'sl': None, 'tp': None, 'lot': None,
                'confidence': 'Low', 'reason': 'LLM_API_KEY not configured in .env'}

    prompt = _build_prompt(memory, indicators)
    cfg_fb = get_llm_config()
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=cfg['model'],
            messages=[
                {'role': 'system', 'content': (
                    'Kamu analis trading XAUUSD. Output STRICT JSON, no markdown, no text: '
                    '{"action":"BUY|SELL|SKIP","entry":N,"sl":N,"tp":N,"lot":0.01,'
                    '"confidence":"Low|Med|High","reason":"singkat (Indonesia)"}. '
                    'lot SELALU 0.01 — jangan output lot lain (0.05/0.1 dll TIDAK valid). '
                    'Entry/SL/TP pakai angka harga (bukan persen). '
                    'SL = 2xATR14(H4) dari entry, TP = 2x jarak SL (RR 1:2). '
                    'TREND ALIGNMENT: entry harus searah 1 TF DOMINAN (D1 atau H4). '
                    'Di downtrend D1 (EMA5<EMA21 D1): SELL diizinkan walau H4 uptrend, asalkan H1 bearish (EMA5<EMA21 H1). '
                    'Di uptrend D1 (EMA5>EMA21 D1): BUY diizinkan walau H4 downtrend, asalkan H1 bullish (EMA5>EMA21 H1). '
                    'Kalau D1 dan H4 konflik, pakai arah H1 sebagai penentu (entry searah H1). '
                    'JANGAN entry kalau 3 TF (H1/H4/D1) saling konflik total. '
                    'RSI oversold TETAP bisa jadi alasan entry kalau arah tren mendukung. '
                    'Volume rendah = turunin confidence ke Low (TETAP boleh entry), bukan SKIP. '
                    'SKIP cuma kalau indikator bener-bener nggak jelas / 3 TF konflik total. '
                    'Risk cap: max 1 posisi, SL wajib, RR >= 1.0.')},
                {'role': 'user', 'content': prompt},
            ],
            temperature=0.3,
            max_tokens=300,
            timeout=40.0,
        )
        text = resp.choices[0].message.content.strip()
        decision = _parse_json(text)
        if not isinstance(decision, dict) or 'action' not in decision:
            raise ValueError('invalid response (no action): ' + text[:100])
        decision.setdefault('entry', None)
        decision.setdefault('sl', None)
        decision.setdefault('tp', None)
        decision.setdefault('lot', None)
        decision.setdefault('confidence', 'Low')
        decision.setdefault('reason', '')
        decision['model_used'] = cfg['model']
        return decision
    except Exception as e:
        primary_err = str(e)
        # FALLBACK: primary errored (503/429/timeout/invalid) -> try the backup.
        # The guard is on the pair, not the model name: a "fallback" that
        # resolves to the same endpoint as the primary is not a fallback, it is
        # a second attempt at the thing that just failed.
        fb_model = cfg_fb.get('fallback_model')
        fb_url = cfg_fb.get('fallback_base_url')
        fb_key = cfg_fb.get('fallback_api_key')
        same_target = (fb_model == cfg['model']) and (fb_url == cfg['base_url'])
        if fb_model and not same_target:
            try:
                fb_client = _get_client(fb_url, fb_key)
                fb_resp = fb_client.chat.completions.create(
                    model=cfg_fb['fallback_model'],
                    messages=[
                        {'role': 'system', 'content': (
                            'Kamu analis trading XAUUSD. Output STRICT JSON, no markdown, no text: '
                            '{"action":"BUY|SELL|SKIP","entry":N,"sl":N,"tp":N,"lot":0.01,'
                            '"confidence":"Low|Med|High","reason":"singkat (Indonesia)"}. '
                            'lot SELALU 0.01. Entry/SL/TP pakai angka harga. '
                            'SL = 2xATR14(H4) dari entry, TP = 2x jarak SL (RR 1:2). '
                            'TREND ALIGNMENT searah 1 TF dominan (D1/H4), konflik total = SKIP. '
                            'Risk cap: max 1 posisi, SL wajib, RR >= 1.0.')},
                        {'role': 'user', 'content': prompt},
                    ],
                    temperature=0.3,
                    max_tokens=300,
                    timeout=40.0,
                )
                fb_text = fb_resp.choices[0].message.content.strip()
                fb_decision = _parse_json(fb_text)
                if not isinstance(fb_decision, dict) or 'action' not in fb_decision:
                    raise ValueError('fallback invalid: ' + fb_text[:100])
                fb_decision.setdefault('entry', None)
                fb_decision.setdefault('sl', None)
                fb_decision.setdefault('tp', None)
                fb_decision.setdefault('lot', None)
                fb_decision.setdefault('confidence', 'Low')
                fb_decision.setdefault('reason', '')
                fb_decision['model_used'] = cfg_fb['fallback_model']
                fb_decision['fallback'] = True
                fb_decision['fallback_provider'] = fb_url
                fb_decision['primary_error'] = primary_err
                return fb_decision
            except Exception as fb_e:
                return {'action': 'SKIP', 'entry': None, 'sl': None, 'tp': None, 'lot': None,
                        'confidence': 'Low',
                        'reason': f'LLM error (primary): {primary_err} | fallback: {fb_e}'}
        return {'action': 'SKIP', 'entry': None, 'sl': None, 'tp': None, 'lot': None,
                'confidence': 'Low', 'reason': f'LLM error: {primary_err}'}

def _build_prompt(memory: dict, indicators: dict) -> str:
    """Compact prompt: memory stats + current indicators per TF."""
    trades_n = len(memory.get('trades', []))
    wr = memory.get('win_rate', {})
    mem_summary = f"Trades: {trades_n}, win_rate: {wr}, max_drawdown: {memory.get('max_drawdown', 0)}"
    ind_lines = []
    for tf, d in indicators.items():
        if not isinstance(d, dict) or 'error' in d:
            ind_lines.append(f"{tf}: error {d}")
            continue
        ind_lines.append(
            f"{tf}: price={d.get('price')} RSI={d.get('RSI')} MACD_hist={d.get('MACD_hist')} "
            f"EMA5={d.get('EMA5')} EMA21={d.get('EMA21')} EMA5vs21={d.get('EMA5_vs_EMA21')} "
            f"BB(L/M/U)={d.get('BB_low')}/{d.get('BB_mid')}/{d.get('BB_up')} "
            f"pos_vs_BB={d.get('pos_vs_BB')} ATR14={d.get('ATR14')} vol_ratio={d.get('vol_ratio')}")
    return f"Memory: {mem_summary}\n\nIndicators XAUUSD:\n" + "\n".join(ind_lines)

def _parse_json(text: str) -> dict:
    """Parse LLM JSON response, tolerant of markdown fences."""
    t = text.strip()
    if t.startswith('```'):
        lines = t.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        t = '\n'.join(lines).strip()
    # find first { ... } block
    start = t.find('{')
    end = t.rfind('}')
    if start >= 0 and end > start:
        t = t[start:end+1]
    try:
        return json.loads(t)
    except Exception:
        return {}

if __name__ == '__main__':
    cfg = get_llm_config()
    print('base_url:', cfg['base_url'], '| model:', cfg['model'], '| key set:', bool(cfg['api_key']) and not cfg['api_key'].startswith('sk-9router-placeholder'))