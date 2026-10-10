#!/usr/bin/env python3
"""
JET SMC Scanner (version cloud) - port Python de JET_SMC_Scanner.mq5
Biais HTF, sweep de liquidité, MSS, OB/FVG non mitigés, OTE 62-79% -> note 0 à 5.
SCALP : déclencheur M5, biais M15 + H1.   SWING : déclencheur H1, biais H4 + D1.
Envoie les alertes sur le téléphone via ntfy. Ne passe AUCUN ordre.

Données : Yahoo Finance (gratuit, léger décalage vs ton broker, pas de spread).
L'or est pris sur le future GC=F (proche du spot XAUUSD, pas identique).
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- réglages
SYMBOLS = {            # nom affiché -> ticker Yahoo
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "BTCUSD": "BTC-USD",
}
MIN_STARS = int(os.environ.get("MIN_STARS", "4"))
LONDON = (7, 11)       # killzones en UTC
NEWYORK = (12, 16)
COOLDOWN_MIN = {"SCALP": 60, "SWING": 720}   # pas de ré-alerte identique avant
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "alerts.json")


# ---------------------------------------------------------------- données
class Bars:
    """Séries ordonnées du plus récent (index 0) au plus ancien, comme MT5."""

    def __init__(self, df: pd.DataFrame):
        d = df.iloc[::-1]
        self.o = d["Open"].to_numpy(float)
        self.h = d["High"].to_numpy(float)
        self.l = d["Low"].to_numpy(float)
        self.c = d["Close"].to_numpy(float)
        self.t = d.index[0] if len(d) else None
        self.n = len(d)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    ).dropna()


def download(ticker: str) -> dict:
    import yfinance as yf

    def get(interval, period):
        for _ in range(3):
            try:
                df = yf.download(ticker, interval=interval, period=period,
                                 progress=False, auto_adjust=False, threads=False)
                if df is not None and len(df):
                    return clean(df)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {ticker} {interval}: {e}", file=sys.stderr)
            time.sleep(2)
        return None

    m5 = get("5m", "5d")
    m15 = get("15m", "10d")
    h1 = get("60m", "60d")
    d1 = get("1d", "1y")
    if any(x is None for x in (m5, m15, h1, d1)):
        return {}
    h4 = resample(h1, "4h")
    return {"M5": m5, "M15": m15, "H1": h1, "H4": h4, "D1": d1}


# ---------------------------------------------------------------- logique SMC
def calc_atr(x: Bars, n=14) -> float:
    s = 0.0
    for i in range(1, n + 1):
        s += max(x.h[i] - x.l[i], abs(x.h[i] - x.c[i + 1]), abs(x.l[i] - x.c[i + 1]))
    return s / n


def min_low(x, a, b):
    return float(np.min(x.l[a:b + 1]))


def max_high(x, a, b):
    return float(np.max(x.h[a:b + 1]))


def swing_high(x, i, k):
    return all(x.h[i] > x.h[i - j] and x.h[i] > x.h[i + j] for j in range(1, k + 1))


def swing_low(x, i, k):
    return all(x.l[i] < x.l[i - j] and x.l[i] < x.l[i + j] for j in range(1, k + 1))


def bias(x: Bars) -> int:
    if x.n < 100:
        return 0
    k = 3
    sh, sl = [], []
    for i in range(k + 1, min(140, x.n - k)):
        if len(sh) < 2 and swing_high(x, i, k):
            sh.append(x.h[i])
        if len(sl) < 2 and swing_low(x, i, k):
            sl.append(x.l[i])
        if len(sh) == 2 and len(sl) == 2:
            break
    if len(sh) < 2 or len(sl) < 2:
        return 0
    if sh[0] > sh[1] and sl[0] > sl[1]:
        return 1
    if sh[0] < sh[1] and sl[0] < sl[1]:
        return -1
    return 0


def find_sweep(x, d):
    pH, pL = max_high(x, 13, 70), min_low(x, 13, 70)
    for j in range(1, 13):
        if d == 1 and x.l[j] < pL and x.c[j] > pL:
            return j, x.l[j]
        if d == -1 and x.h[j] > pH and x.c[j] < pH:
            return j, x.h[j]
    return None, 0.0


def find_mss(x, d, sj):
    if sj is None or sj < 2:
        return False
    if d == 1:
        lh = max_high(x, sj + 1, sj + 6)
        return any(x.c[k] > lh for k in range(1, sj))
    ll = min_low(x, sj + 1, sj + 6)
    return any(x.c[k] < ll for k in range(1, sj))


def find_poi(x, d, atr, bid):
    tol = 0.1 * atr
    for i in range(2, 51):                       # FVG
        if d == 1 and x.l[i] > x.h[i + 2]:
            bot, top = x.h[i + 2], x.l[i]
            if top - bot >= 0.25 * atr and min_low(x, 1, i - 1) > bot and bot - tol <= bid <= top + tol:
                return "FVG"
        if d == -1 and x.h[i] < x.l[i + 2]:
            bot, top = x.h[i], x.l[i + 2]
            if top - bot >= 0.25 * atr and max_high(x, 1, i - 1) < top and bot - tol <= bid <= top + tol:
                return "FVG"
    for i in range(3, 51):                       # Order Block
        if d == 1 and x.c[i] < x.o[i] and x.c[i - 1] > x.h[i]:
            move = max(x.h[i - 1], x.h[i - 2]) - x.l[i]
            bot, top = x.l[i], x.h[i]
            if move >= 1.5 * atr and min_low(x, 1, i - 1) > bot and bot - tol <= bid <= top + tol:
                return "OB"
        if d == -1 and x.c[i] > x.o[i] and x.c[i - 1] < x.l[i]:
            move = x.h[i] - min(x.l[i - 1], x.l[i - 2])
            bot, top = x.l[i], x.h[i]
            if move >= 1.5 * atr and max_high(x, 1, i - 1) < top and bot - tol <= bid <= top + tol:
                return "OB"
    return ""


def find_ote(x, d, atr, bid):
    if d == 1:
        iL = int(np.argmin(x.l[1:31])) + 1
        if iL < 3:
            return False
        iH = int(np.argmax(x.h[1:iL])) + 1
        H, L = x.h[iH], x.l[iL]
        R = H - L
        if R < 1.5 * atr:
            return False
        return 0.62 <= (H - bid) / R <= 0.79
    iH = int(np.argmax(x.h[1:31])) + 1
    if iH < 3:
        return False
    iL = int(np.argmin(x.l[1:iH])) + 1
    H, L = x.h[iH], x.l[iL]
    R = H - L
    if R < 1.5 * atr:
        return False
    return 0.62 <= (bid - L) / R <= 0.79


def in_session(now: datetime) -> bool:
    h = now.hour
    return LONDON[0] <= h < LONDON[1] or NEWYORK[0] <= h < NEWYORK[1]


def analyze(trig: Bars, b1: Bars, b2: Bars, use_session: bool, now: datetime):
    if trig.n < 100:
        return None
    atr = calc_atr(trig)
    if atr <= 0:
        return None
    bid = float(trig.c[0])
    hb, mb = bias(b2), bias(b1)

    best = None
    for d in (1, -1):
        bi = hb == d and mb != -d
        sj, ext = find_sweep(trig, d)
        sw = sj is not None
        ms = sw and find_mss(trig, d, sj)
        pt = find_poi(trig, d, atr, bid)
        ot = find_ote(trig, d, atr, bid)
        s = int(bi) + int(sw) + int(ms) + int(bool(pt)) + int(ot)
        cand = dict(dir=d, stars=s, bias=bi, sweep=sw, mss=ms, poi=pt, ote=ot, ext=ext)
        if best is None or s > best["stars"] or (s == best["stars"] and d == hb):
            best = cand

    stars = best["stars"]
    if use_session and not in_session(now):
        stars = min(stars, 3)
    d = best["dir"]
    if d == 1:
        entry = bid
        sl = (best["ext"] if best["sweep"] else bid - 1.5 * atr) - 0.25 * atr
        R = entry - sl
        tp1, tp2 = entry + 1.5 * R, entry + 3.0 * R
    else:
        entry = bid
        sl = (best["ext"] if best["sweep"] else bid + 1.5 * atr) + 0.25 * atr
        R = sl - entry
        tp1, tp2 = entry - 1.5 * R, entry - 3.0 * R
    best.update(stars=stars, entry=entry, sl=sl, tp1=tp1, tp2=tp2)
    return best


def flags(r):
    return "".join([
        "B" if r["bias"] else ".", "S" if r["sweep"] else ".", "M" if r["mss"] else ".",
        "P" if r["poi"] else ".", "O" if r["ote"] else ".",
    ])


def fmt(v, ref):
    return f"{v:.3f}" if ref < 50 else f"{v:.2f}"


# ---------------------------------------------------------------- alertes
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def notify(title: str, msg: str):
    print(f"ALERTE: {title} | {msg}")
    if not NTFY_TOPIC:
        print("  (NTFY_TOPIC absent : alerte non envoyée)")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "chart_with_upwards_trend"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"  ! envoi ntfy échoué: {e}", file=sys.stderr)


def maybe_alert(state, sym, mode, r, now):
    if r["stars"] < MIN_STARS:
        return
    key = f"{sym}|{mode}|{r['dir']}"
    last = state.get(key)
    if last and (now.timestamp() - last) < COOLDOWN_MIN[mode] * 60:
        return
    side = "BUY" if r["dir"] == 1 else "SELL"
    e = r["entry"]
    msg = (f"{sym} {side} {r['stars']}/5 [{flags(r)}] {r['poi']}\n"
           f"Entrée {fmt(e, e)}  SL {fmt(r['sl'], e)}\n"
           f"TP1 {fmt(r['tp1'], e)}  TP2 {fmt(r['tp2'], e)}")
    notify(f"JET {mode} {sym} {side} {r['stars']}*", msg)
    state[key] = now.timestamp()


# ---------------------------------------------------------------- principal
def main():
    now = datetime.now(timezone.utc)
    state = load_state()
    rows = []
    for sym, tick in SYMBOLS.items():
        data = download(tick)
        if not data:
            print(f"{sym}: données indisponibles")
            continue
        B = {k: Bars(v) for k, v in data.items()}
        sc = analyze(B["M5"], B["M15"], B["H1"], True, now)
        sw = analyze(B["H1"], B["H4"], B["D1"], False, now)
        if sc:
            maybe_alert(state, sym, "SCALP", sc, now)
        if sw:
            maybe_alert(state, sym, "SWING", sw, now)
        rows.append((sym, sc, sw))

    rows.sort(key=lambda r: -(r[1]["stars"] if r[1] else -1))
    print(f"\nJET SMC SCANNER  {now:%Y-%m-%d %H:%M} UTC  killzone: {'OUI' if in_session(now) else 'NON'}")
    print(f"{'PAIRE':8}{'SCALP':14}{'BSMPO':7}{'ZONE':5}| SWING")
    for sym, sc, sw in rows:
        def cell(r):
            return f"{'BUY ' if r['dir'] == 1 else 'SELL'} {'*' * r['stars']}{'-' * (5 - r['stars'])}" if r else "n/a"
        print(f"{sym:8}{cell(sc):14}{flags(sc) if sc else '':7}{(sc['poi'] if sc else ''):5}| {cell(sw)}")
    save_state(state)


if __name__ == "__main__":
    main()
