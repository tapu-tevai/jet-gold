#!/usr/bin/env python3
"""
JET Gold Intelligence
Analyse l'or (Gold) avec :
  1. la technique SMC (sweep, MSS, OB/FVG, OTE, biais HTF) sur le future GC=F
  2. les actifs corrélés (dollar, rendements US, EURUSD, USDJPY, AUDUSD, argent, pétrole, S&P500, VIX)
  3. le calendrier économique (annonces USD à fort impact, discours des membres de la Fed)
  4. les titres d'actualité (FXStreet, Fed, Google News) et le ton des discours de la Fed
Note finale 0-5 étoiles = technique +/- confirmation macro, plafonnée à 3 près d'une annonce.
Alertes à 4-5 étoiles (SCALP et SWING) sur le téléphone via ntfy. N'ouvre AUCUN ordre.
"""
import json
import math
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd

import scanner as sc

MIN_STARS = int(os.environ.get("MIN_STARS", "4"))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
GOLD_TICKER = "GC=F"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
STATE_FILE = os.path.join(STATE_DIR, "gold_state.json")
COOLDOWN_MIN = {"SCALP": 60, "SWING": 720}

# nom, ticker Yahoo, poids (signe = effet sur l'or si l'actif MONTE), type de variation
ASSETS = [
    ("DXY", "DX-Y.NYB", -3.0, "pct"),
    ("US10Y", "^TNX", -3.0, "diff"),
    ("EURUSD", "EURUSD=X", 2.0, "pct"),
    ("USDJPY", "JPY=X", -1.5, "pct"),
    ("AUDUSD", "AUDUSD=X", 1.0, "pct"),
    ("ARGENT", "SI=F", 1.5, "pct"),
    ("PETROLE", "CL=F", 0.5, "pct"),
    ("SP500", "^GSPC", -0.5, "pct"),
    ("VIX", "^VIX", 0.8, "diff"),
]

CAL_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
FEEDS = [
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("News-Or", "https://news.google.com/rss/search?q=gold+price+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("News-Fed", "https://news.google.com/rss/search?q=federal+reserve+OR+inflation+OR+%22dollar+index%22+when:1d&hl=en-US&gl=US&ceid=US:en"),
]
SPEECH_FEED = "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml"

# (expression, poids) ; positif = favorable à l'or
BULL = [
    (r"gold (rises|rise|climbs|jumps|gains|advances|extends gains|hits|rallies|rally)", 2.0),
    (r"gold surges|gold soars|record high.{0,20}gold|gold.{0,20}record high", 2.5),
    (r"rate cuts?|cuts? rates|dovish|fed pause|pause bets|easing", 1.5),
    (r"dollar (falls|slides|weakens|drops|slumps|retreats|softens)|weaker dollar|dollar index (falls|drops|slides)", 1.5),
    (r"yields? (fall|drop|slide|decline|retreat|tumble)", 1.5),
    (r"safe[- ]haven|flight to safety|risk[- ]off", 1.5),
    (r"geopolitic|war\b|tensions|escalat|missile|sanctions", 1.0),
    (r"central bank (buying|purchases|demand)", 1.5),
    (r"weak(er)? jobs|jobs miss|unemployment rises|payrolls (miss|disappoint|fall)", 1.5),
    (r"inflation (cools|eases|slows|falls)|cooler[- ]than[- ]expected|softer[- ]than[- ]expected", 1.5),
    (r"recession|slowdown", 1.0),
]
BEAR = [
    (r"gold (falls|drops|slips|slides|declines|retreats|weakens|eases|dips)", 2.0),
    (r"gold tumbles|gold plunges|gold sinks|gold slumps", 2.5),
    (r"rate hikes?|hawkish|hawks\b|higher for longer|tightening", 2.0),
    (r"dollar (rises|gains|strengthens|jumps|climbs|rallies|firms)|stronger dollar|dollar index (rises|climbs|gains)", 1.5),
    (r"yields? (rise|jump|climb|surge|spike|advance)", 1.5),
    (r"strong(er)? jobs|jobs beat|payrolls (beat|surge|jump)|unemployment falls", 1.5),
    (r"hotter[- ]than[- ]expected|inflation (accelerates|rises|jumps|surges|heats)|sticky inflation", 1.5),
    (r"stronger[- ]than[- ]expected|resilient economy", 1.0),
    (r"ceasefire|peace (deal|talks|agreement)|de-escalat", 1.0),
    (r"risk[- ]on|profit[- ]taking", 0.7),
]
HAWK_WORDS = [
    r"inflation remains (elevated|above|too high)", r"upside risks? to inflation", r"restrictive",
    r"further tightening", r"higher for longer", r"persistent inflation", r"sticky", r"not yet",
    r"inflation.{0,30}(stubborn|persist)",
]
DOVE_WORDS = [
    r"downside risks?", r"labor market (has )?(soften|weaken|cool)", r"(softening|weakening|cooling) (labor|job)",
    r"rate cuts?", r"cutting rates", r"lower (the )?(policy )?rate", r"easing", r"accommodat",
    r"less restrictive", r"slowdown", r"unemployment (has )?risen",
]


# ---------------------------------------------------------------- réseau
def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JET-Gold-Bot)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  ! fetch {url[:70]}: {e}", file=sys.stderr)
        return None


def get_series(ticker, interval, period):
    import yfinance as yf
    for _ in range(3):
        try:
            df = yf.download(ticker, interval=interval, period=period, progress=False,
                             auto_adjust=False, threads=False)
            if df is not None and len(df):
                return sc.clean(df)["Close"].astype(float)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {ticker} {interval}: {e}", file=sys.stderr)
        time.sleep(2)
    return None


# ---------------------------------------------------------------- 1. actifs corrélés
def zmove(s, n, kind):
    if s is None or len(s) < n + 30:
        return None
    ch = s.pct_change() if kind == "pct" else s.diff()
    sd = float(ch.iloc[-250:].std())
    if not sd or math.isnan(sd):
        return None
    move = (s.iloc[-1] / s.iloc[-1 - n] - 1) if kind == "pct" else (s.iloc[-1] - s.iloc[-1 - n])
    return float(move / (sd * math.sqrt(n)))


def blend(a, b):
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return 0.6 * a + 0.4 * b


def correlated():
    rows = []
    for name, tick, w, kind in ASSETS:
        h1 = get_series(tick, "60m", "10d")
        d1 = get_series(tick, "1d", "6mo")
        zs = blend(zmove(h1, 4, kind), zmove(h1, 12, kind))     # court terme (scalp)
        zw = blend(zmove(d1, 5, kind), zmove(d1, 20, kind))     # moyen terme (swing)
        rows.append(dict(
            name=name, w=w, zs=zs, zw=zw,
            cs=None if zs is None else w * math.tanh(zs / 1.5),
            cw=None if zw is None else w * math.tanh(zw / 1.5),
            last=None if h1 is None or not len(h1) else float(h1.iloc[-1]),
        ))
    return rows


def corr_scores(rows):
    out = {}
    for key, ck in (("scalp", "cs"), ("swing", "cw")):
        used = [r for r in rows if r[ck] is not None]
        if len(used) < 4:
            out[key] = None
        else:
            out[key] = 100.0 * sum(r[ck] for r in used) / sum(abs(r["w"]) for r in used)
    return out


# ---------------------------------------------------------------- 2. calendrier
def load_calendar():
    ev, seen = [], set()
    for url in CAL_URLS:
        txt = fetch(url)
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:  # noqa: BLE001
            continue
        for e in data:
            try:
                t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            key = (e.get("title"), t)
            if key in seen:
                continue
            seen.add(key)
            ev.append(dict(title=e.get("title", ""), country=e.get("country", ""),
                           impact=e.get("impact", ""), time=t,
                           forecast=e.get("forecast", ""), previous=e.get("previous", "")))
    ev.sort(key=lambda x: x["time"])
    return ev


def blackout(now, events, mode):
    before = timedelta(minutes=30 if mode == "SCALP" else 120)
    after = timedelta(minutes=30)
    for e in events:
        if e["country"] == "USD" and e["impact"] == "High" and e["time"] - before <= now <= e["time"] + after:
            return e
    return None


def next_high(now, events):
    for e in events:
        if e["country"] == "USD" and e["impact"] == "High" and e["time"] > now:
            return e
    return None


def human_delta(td):
    m = int(td.total_seconds() // 60)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


# ---------------------------------------------------------------- 3. actualités et discours
def score_title(title):
    t = title.lower()
    s = 0.0
    for pat, w in BULL:
        if re.search(pat, t):
            s += w
    for pat, w in BEAR:
        if re.search(pat, t):
            s -= w
    return max(-4.0, min(4.0, s))


def parse_rss(txt):
    out = []
    try:
        root = ET.fromstring(txt)
    except Exception:  # noqa: BLE001
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        try:
            t = parsedate_to_datetime(pub)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            t = t.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if title:
            out.append(dict(title=title, link=link, time=t))
    return out


def speech_lean(url):
    txt = fetch(url, timeout=25)
    if not txt:
        return None
    txt = re.sub(r"<[^>]+>", " ", txt).lower()[:250000]
    hawk = sum(len(re.findall(p, txt)) for p in HAWK_WORDS)
    dove = sum(len(re.findall(p, txt)) for p in DOVE_WORDS)
    if hawk + dove < 3:
        return 0.0
    return (dove - hawk) / (hawk + dove) * min(1.0, (hawk + dove) / 8.0)


def load_news(now):
    items, seen, total = [], set(), 0.0
    for src, url in FEEDS:
        txt = fetch(url)
        if not txt:
            continue
        for it in parse_rss(txt):
            key = re.sub(r"\W+", "", it["title"].lower())[:60]
            age = (now - it["time"]).total_seconds() / 3600.0
            if key in seen or age < 0 or age > 10:
                continue
            seen.add(key)
            s = score_title(it["title"])
            dec = 0.5 ** (age / 3.0)
            total += s * dec
            items.append(dict(src=src, title=it["title"], time=it["time"], score=s, age=age))
    # discours de la Fed (texte complet) des dernières 36 h
    txt = fetch(SPEECH_FEED)
    if txt:
        for it in parse_rss(txt)[:4]:
            age = (now - it["time"]).total_seconds() / 3600.0
            if age < 0 or age > 36 or not it["link"]:
                continue
            lean = speech_lean(it["link"])
            if lean is None:
                continue
            s = 4.0 * lean
            total += s * 0.5 ** (age / 12.0)
            tone = "colombe (favorable or)" if lean > 0.15 else "faucon (défavorable or)" if lean < -0.15 else "neutre"
            items.append(dict(src="Discours Fed", title=f"{it['title']} -> ton {tone}",
                              time=it["time"], score=s, age=age))
    items.sort(key=lambda x: -abs(x["score"]))
    return 100.0 * math.tanh(total / 6.0), items


# ---------------------------------------------------------------- fusion
def combine(corr, news, wc):
    if corr is None:
        return news
    return wc * corr + (1 - wc) * news


def fuse(tech, macro, black, near):
    d = tech["dir"]
    align = macro * d / 100.0
    s = tech["stars"]
    if align >= 0.35:
        s += 1
    elif align <= -0.35:
        s -= 2
    elif align <= -0.10:
        s -= 1
    s = max(0, min(5, s))
    if black or near:
        s = min(s, 3)
    return s, align


def drivers(rows, key_z, key_c, n=3):
    rr = [r for r in rows if r[key_c] is not None]
    rr.sort(key=lambda r: -abs(r[key_c]))
    return " ".join(f"{r['name']}{'↑' if r[key_z] > 0 else '↓'}" for r in rr[:n])


def label(x):
    return "HAUSSIER" if x >= 40 else "BAISSIER" if x <= -40 else "NEUTRE"


# ---------------------------------------------------------------- alertes et rapport
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def notify(title, msg, priority="high"):
    print(f"ALERTE: {title}\n{msg}\n")
    if not NTFY_TOPIC:
        print("  (NTFY_TOPIC absent : alerte non envoyée)")
        return
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"),
                                 headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"),
                                          "Priority": priority})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"  ! ntfy: {e}", file=sys.stderr)


def trade_alert(state, now, mode, tech, final, macro, drv, nxt):
    if final < MIN_STARS:
        return
    key = f"GOLD|{mode}|{tech['dir']}"
    if key in state and now.timestamp() - state[key] < COOLDOWN_MIN[mode] * 60:
        return
    side = "BUY" if tech["dir"] == 1 else "SELL"
    e = tech["entry"]
    lines = [
        f"Tech {tech['stars']}/5 [{sc.flags(tech)}] {tech['poi']}  |  Macro {macro:+.0f} ({drv})",
        "Entrée : au prix de TON Gold chez le broker, puis applique ces distances :",
        f"SL {tech['sl'] - e:+.1f}$   TP1 {tech['tp1'] - e:+.1f}$   TP2 {tech['tp2'] - e:+.1f}$",
    ]
    if nxt:
        lines.append(f"Prochaine annonce USD : {nxt['title']} dans {human_delta(nxt['time'] - now)}")
    notify(f"JET GOLD {side} {final}* {mode}", "\n".join(lines))
    state[key] = now.timestamp()


def pre_event_alerts(state, now, events):
    for e in events:
        if e["country"] != "USD" or e["impact"] != "High":
            continue
        dt = e["time"] - now
        if timedelta(0) < dt <= timedelta(minutes=45):
            key = f"PRE|{e['title']}|{e['time'].isoformat()}"
            if key in state:
                continue
            fc = f" prévu {e['forecast']}" if e["forecast"] else ""
            pv = f" préc. {e['previous']}" if e["previous"] else ""
            notify("JET annonce USD dans " + human_delta(dt),
                   f"{e['title']}{fc}{pv}\nPas de nouveau trade scalp avant et après : forte volatilité sur l'or.",
                   priority="default")
            state[key] = now.timestamp()


def write_outputs(now, rows, cs, cw, news_score, items, events, signals, closed):
    os.makedirs(STATE_DIR, exist_ok=True)
    macro = dict(updated=now.isoformat(), scalp_macro=round(cs, 1), swing_macro=round(cw, 1),
                 news=round(news_score, 1), market_closed=closed,
                 signals={k: dict(dir=v["dir"], tech=v["tech"], final=v["final"]) for k, v in signals.items()})
    with open(os.path.join(STATE_DIR, "macro.json"), "w", encoding="utf-8") as f:
        json.dump(macro, f, indent=1)

    L = [f"# JET Gold Intelligence\nMis à jour : {now:%Y-%m-%d %H:%M} UTC"
         + ("  (marché de l'or fermé)" if closed else ""), ""]
    L += [f"**Biais macro SCALP : {cs:+.0f} ({label(cs)})**  |  **SWING : {cw:+.0f} ({label(cw)})**  |  actualités : {news_score:+.0f}", ""]
    L += ["## Signaux", "| Mode | Sens | Technique | Macro | Note finale |", "|---|---|---|---|---|"]
    for m, v in signals.items():
        side = "BUY" if v["dir"] == 1 else "SELL"
        L.append(f"| {m} | {side} | {v['tech']}/5 | {v['macro']:+.0f} | **{v['final']}/5** {v['note']} |")
    L += ["", "## Actifs corrélés (effet sur l'or)", "| Actif | Mouvement | Effet scalp | Effet swing |", "|---|---|---|---|"]
    for r in rows:
        def eff(c):
            return "n/a" if c is None else ("favorable" if c > 0.15 else "défavorable" if c < -0.15 else "neutre")
        mv = "n/a" if r["zs"] is None else ("hausse" if r["zs"] > 0.3 else "baisse" if r["zs"] < -0.3 else "stable")
        L.append(f"| {r['name']} | {mv} | {eff(r['cs'])} | {eff(r['cw'])} |")
    L += ["", "## Prochaines annonces USD (48 h)"]
    up = [e for e in events if e["country"] == "USD" and e["impact"] in ("High", "Medium")
          and timedelta(minutes=-30) < e["time"] - now < timedelta(hours=48)]
    L += [f"- {e['time']:%a %H:%M} UTC = {e['time'] - timedelta(hours=10):%a %H:%M} Tahiti : **{e['title']}** ({e['impact']})"
          for e in up] or ["- aucune"]
    L += ["", "## Titres qui pèsent sur l'or (10 h)"]
    L += [f"- [{it['src']}] {it['title']} ({it['score']:+.1f})" for it in items[:8]] or ["- aucun"]
    with open(os.path.join(STATE_DIR, "rapport.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------- principal
def main():
    now = datetime.now(timezone.utc)
    state = load_state()

    events = load_calendar()
    rows = correlated()
    cs_raw = corr_scores(rows)
    news_score, items = load_news(now)
    cs = combine(cs_raw["scalp"], news_score, 0.7)
    cw = combine(cs_raw["swing"], news_score, 0.8)

    data = sc.download(GOLD_TICKER)
    closed, signals = True, {}
    nxt = next_high(now, events)
    if data:
        B = {k: sc.Bars(v) for k, v in data.items()}
        ts = pd.Timestamp(B["M5"].t)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        closed = (now - ts.to_pydatetime()) > timedelta(minutes=30)
        if not closed:
            plan = {
                "SCALP": (sc.analyze(B["M5"], B["M15"], B["H1"], True, now), cs, "scalp"),
                "SWING": (sc.analyze(B["H1"], B["H4"], B["D1"], False, now), cw, "swing"),
            }
            for mode, (tech, macro, k) in plan.items():
                if not tech:
                    continue
                black = blackout(now, events, mode)
                near = nxt is not None and (nxt["time"] - now) <= timedelta(minutes=60 if mode == "SCALP" else 240)
                final, _ = fuse(tech, macro, black, near)
                note = "(annonce proche : plafonné à 3)" if (black or near) and final == 3 else ""
                signals[mode] = dict(dir=tech["dir"], tech=tech["stars"], macro=macro, final=final, note=note)
                drv = drivers(rows, "zs" if k == "scalp" else "zw", "cs" if k == "scalp" else "cw")
                trade_alert(state, now, mode, tech, final, macro, drv, nxt)
    pre_event_alerts(state, now, events)

    write_outputs(now, rows, cs, cw, news_score, items, events, signals, closed)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in state.items() if now.timestamp() - v < 7 * 86400}, f, indent=1)

    print(f"JET GOLD  {now:%Y-%m-%d %H:%M} UTC  marché {'FERMÉ' if closed else 'ouvert'}")
    print(f"Macro scalp {cs:+.0f} ({label(cs)}) | swing {cw:+.0f} ({label(cw)}) | actualités {news_score:+.0f}")
    for m, v in signals.items():
        print(f"{m}: {'BUY' if v['dir'] == 1 else 'SELL'} tech {v['tech']}/5 -> final {v['final']}/5 {v['note']}")
    if nxt:
        print(f"Prochaine annonce USD forte : {nxt['title']} dans {human_delta(nxt['time'] - now)}")


if __name__ == "__main__":
    main()
