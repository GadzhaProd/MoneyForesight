#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoneyForesight Strategy - независимый бэктест на чистом Python.
Точная копия логики MoneyForesight_strategy.pine:
  Chandelier(22,22,3) + зона, DMI(14,10)+MACD(12,26,9), Rainbow beta-MA(50)+RSI heat,
  Volume SMA(20)x1.2, ADX-порог с опцией "растущий ADX", ретесты зоны (кулдаун 5),
  ре-вход по пробою 5 баров, TP = R x риск, SL = край зоны / линия / база пробоя,
  флип-выход, комиссия 0.05% за сторону.
Данные: Binance spot BTCUSDT (публичный API, без ключей).
"""
import json, math, time, urllib.request, os, sys, csv

CACHE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.0005  # 0.05% за сторону

def fetch_klines(symbol, interval, total_bars):
    fn = os.path.join(CACHE, f"{symbol}_{interval}_{total_bars}.json")
    if os.path.exists(fn):
        return json.load(open(fn))
    out = []
    end = int(time.time() * 1000)
    while len(out) < total_bars:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        with urllib.request.urlopen(url, timeout=20) as r:
            batch = json.load(r)
        if not batch:
            break
        out = batch + out
        end = batch[0][0] - 1
        time.sleep(0.15)
    out = out[-total_bars:]
    json.dump(out, open(fn, "w"))
    return out

# ---------- индикаторы (семантика Pine) ----------
def rma(src, n):
    out = [None] * len(src)
    s = 0.0; cnt = 0; prev = None
    for i, v in enumerate(src):
        if v is None:
            continue
        if prev is None:
            s += v; cnt += 1
            if cnt == n:
                prev = s / n
                out[i] = prev
        else:
            prev = (prev * (n - 1) + v) / n
            out[i] = prev
    return out

def ema(src, n):
    out = [None] * len(src)
    a = 2.0 / (n + 1); prev = None
    for i, v in enumerate(src):
        if v is None:
            continue
        prev = v if prev is None else a * v + (1 - a) * prev
        out[i] = prev
    return out

def sma(src, n):
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        out[i] = sum(src[i - n + 1:i + 1]) / n
    return out

def rolling_max(src, n):
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        out[i] = max(src[i - n + 1:i + 1])
    return out

def rolling_min(src, n):
    out = [None] * len(src)
    for i in range(n - 1, len(src)):
        out[i] = min(src[i - n + 1:i + 1])
    return out

def rsi(src, n):
    ups = [None] * len(src); dns = [None] * len(src)
    for i in range(1, len(src)):
        if src[i] is None or src[i - 1] is None:
            continue
        ch = src[i] - src[i - 1]
        ups[i] = max(ch, 0.0); dns[i] = max(-ch, 0.0)
    ru, rd = rma(ups, n), rma(dns, n)
    out = [None] * len(src)
    for i in range(len(src)):
        if ru[i] is None or rd[i] is None:
            continue
        out[i] = 100.0 if rd[i] == 0 else 100.0 - 100.0 / (1.0 + ru[i] / rd[i])
    return out

def prepare(kl):
    n = len(kl)
    o = [float(k[1]) for k in kl]; h = [float(k[2]) for k in kl]
    l = [float(k[3]) for k in kl]; c = [float(k[4]) for k in kl]
    v = [float(k[5]) for k in kl]; ts = [int(k[0]) for k in kl]

    tr = [None] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr22 = rma(tr, 22)
    atr14 = rma(tr, 14)
    hh22, ll22 = rolling_max(h, 22), rolling_min(l, 22)

    # Chandelier
    longvs = [None] * n; shortvs = [None] * n; csDir = [0] * n
    for i in range(n):
        if atr22[i] is None or hh22[i] is None:
            continue
        ss = ll22[i] + 3.0 * atr22[i]
        ls = hh22[i] - 3.0 * atr22[i]
        pS, pL = shortvs[i - 1] if i else None, longvs[i - 1] if i else None
        shortvs[i] = ss if pS is None else (ss if c[i] > pS else min(ss, pS))
        longvs[i]  = ls if pL is None else (ls if c[i] < pL else max(ls, pL))
        d = csDir[i - 1] if i else 0
        if i and shortvs[i - 1] is not None and longvs[i - 1] is not None:
            longswitch  = c[i] >= shortvs[i - 1] and c[i - 1] < shortvs[i - 1]
            shortswitch = c[i] <= longvs[i - 1] and c[i - 1] > longvs[i - 1]
            if d >= 0 and shortswitch: d = -1
            elif d <= 0 and longswitch: d = 1
        csDir[i] = d
    pc = [None] * n
    for i in range(n):
        pc[i] = longvs[i] if csDir[i] > 0 else shortvs[i]

    # DMI / ADX (14,10)
    pdm = [None] * n; mdm = [None] * n
    for i in range(1, n):
        up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    rp, rm, ra = rma(pdm, 14), rma(mdm, 14), rma(tr, 14)
    dip = [None] * n; dim = [None] * n; dx = [None] * n
    for i in range(n):
        if rp[i] is None or ra[i] is None or ra[i] == 0:
            continue
        dip[i] = 100.0 * rp[i] / ra[i]; dim[i] = 100.0 * rm[i] / ra[i]
        s = dip[i] + dim[i]
        dx[i] = 0.0 if s == 0 else 100.0 * abs(dip[i] - dim[i]) / s
    adx = rma(dx, 10)

    # MACD
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [None if e12[i] is None or e26[i] is None else e12[i] - e26[i] for i in range(n)]
    sig = ema(macd, 9)

    # Rainbow beta-MA(50) + heat
    L = 50
    w = [(i / (L - 1.0)) ** 2 * (1 - i / (L - 1.0)) ** 2 for i in range(L)]
    den = sum(w)
    filt = [None] * n
    for i in range(L - 1, n):
        filt[i] = sum(c[i - j] * w[j] for j in range(L)) / den
    heat_rsi = rsi(filt, L)
    heat = [0.5 if heat_rsi[i] is None else heat_rsi[i] / 100.0 for i in range(n)]

    volSMA = sma(v, 20)
    reHigh = rolling_max(h, 5)   # для пробоя берём [i-1]
    reLow = rolling_min(l, 5)

    risingADX = [False] * n
    for i in range(3, n):
        if None in (adx[i], adx[i - 1], adx[i - 2], adx[i - 3]):
            continue
        risingADX[i] = adx[i] > adx[i - 1] > adx[i - 2] > adx[i - 3]

    return dict(n=n, o=o, h=h, l=l, c=c, v=v, ts=ts, atr22=atr22, csDir=csDir, pc=pc,
                dip=dip, dim=dim, adx=adx, macd=macd, sig=sig, heat=heat,
                volSMA=volSMA, reHigh=reHigh, reLow=reLow, risingADX=risingADX)

def run(D, adxThr, tpR, slmode, zoneW, flipExit, useRising=True, volMult=1.2, start=150):
    n = D["n"]; c, h, l = D["c"], D["h"], D["l"]
    csDir, pc, atr = D["csDir"], D["pc"], D["atr22"]
    trades = []          # (dir, R_net, outcome)
    tradeState = 0
    pos = None           # (dir, entry, sl, tp, entryBar)
    lastRtL = lastRtS = -9999
    for i in range(start, n):
        if None in (pc[i], atr[i], D["adx"][i], D["macd"][i], D["sig"][i], D["volSMA"][i]):
            continue
        zT = pc[i] + zoneW * atr[i]; zB = pc[i] - zoneW * atr[i]
        zTp = pc[i - 1] + zoneW * atr[i - 1] if atr[i - 1] and pc[i - 1] else None
        zBp = pc[i - 1] - zoneW * atr[i - 1] if atr[i - 1] and pc[i - 1] else None
        volOK = D["v"][i] > D["volSMA"][i] * volMult
        adxOK = D["adx"][i] >= adxThr or (useRising and D["adx"][i] >= adxThr * 0.5 and D["risingADX"][i])
        fOK = volOK and adxOK
        lc = (D["dip"][i] > D["dim"][i] and D["macd"][i] > D["sig"][i] and csDir[i] > 0
              and D["heat"][i] > 0.5 and fOK)
        sc = (D["dim"][i] > D["dip"][i] and D["sig"][i] > D["macd"][i] and csDir[i] < 0
              and D["heat"][i] < 0.5 and fOK)
        prevTrade = tradeState
        tradeState = 1 if lc else (-1 if sc else tradeState)
        longFlip = tradeState == 1 and prevTrade != 1
        shortFlip = tradeState == -1 and prevTrade != -1

        # выходы
        if pos is not None:
            d, entry, sl, tp, eb = pos
            if i > eb:
                exitP = None; out = None
                if d == 1:
                    if l[i] <= sl: exitP, out = sl, "SL"
                    elif h[i] >= tp: exitP, out = tp, "TP"
                    elif flipExit and csDir[i] < 0: exitP, out = c[i], "FLIP"
                else:
                    if h[i] >= sl: exitP, out = sl, "SL"
                    elif l[i] <= tp: exitP, out = tp, "TP"
                    elif flipExit and csDir[i] > 0: exitP, out = c[i], "FLIP"
                if exitP is not None:
                    risk = abs(entry - sl)
                    gross = (exitP - entry) / risk * d
                    feeR = FEE * (entry + exitP) / risk
                    trades.append((d, gross - feeR, out))
                    pos = None

        # входы
        if pos is None:
            rtL = (csDir[i] > 0 and csDir[i - 1] > 0 and zTp is not None and c[i] > zT
                   and (l[i] <= zT or c[i - 1] <= zTp) and fOK and i - lastRtL > 5)
            if rtL: lastRtL = i
            rtS = (csDir[i] < 0 and csDir[i - 1] < 0 and zBp is not None and c[i] < zB
                   and (h[i] >= zB or c[i - 1] >= zBp) and fOK and i - lastRtS > 5)
            if rtS: lastRtS = i
            reL = lc and csDir[i] > 0 and D["reHigh"][i - 1] is not None and c[i] > D["reHigh"][i - 1]
            reS = sc and csDir[i] < 0 and D["reLow"][i - 1] is not None and c[i] < D["reLow"][i - 1]
            if longFlip or rtL or reL:
                isRe = not (longFlip or rtL)
                sl = D["reLow"][i - 1] if isRe else (zB if slmode == "zone" else pc[i])
                risk = c[i] - sl
                if risk > 0:
                    pos = (1, c[i], sl, c[i] + tpR * risk, i)
            elif shortFlip or rtS or reS:
                isRe = not (shortFlip or rtS)
                sl = D["reHigh"][i - 1] if isRe else (zT if slmode == "zone" else pc[i])
                risk = sl - c[i]
                if risk > 0:
                    pos = (-1, c[i], sl, c[i] - tpR * risk, i)

    # метрики
    if not trades:
        return None
    net = sum(t[1] for t in trades)
    gw = sum(t[1] for t in trades if t[1] > 0)
    gl = -sum(t[1] for t in trades if t[1] < 0)
    pf = gw / gl if gl > 0 else float("inf")
    wins = sum(1 for t in trades if t[1] > 0)
    eq = 0.0; peak = 0.0; dd = 0.0
    for t in trades:
        eq += t[1]; peak = max(peak, eq); dd = min(dd, eq - peak)
    longR = sum(t[1] for t in trades if t[0] == 1)
    shortR = sum(t[1] for t in trades if t[0] == -1)
    return dict(net=net, pf=pf, trades=len(trades), wins=wins, dd=dd,
                longR=longR, shortR=shortR,
                tp=sum(1 for t in trades if t[2] == "TP"),
                slc=sum(1 for t in trades if t[2] == "SL"),
                fl=sum(1 for t in trades if t[2] == "FLIP"))

def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    print(f"########## {symbol} ##########")
    tfs = [("5m", 50000), ("15m", 35000), ("30m", 35000), ("1h", 17500), ("4h", 8000)]
    grid_adx = [18, 23, 28]
    grid_tp = [1.5, 2.0, 3.0]
    grid_sl = [("zone", 0.25), ("zone", 0.40), ("line", 0.25)]
    grid_flip = [True, False]

    results = []
    for tf, bars in tfs:
        print(f"== {tf}: качаю {bars} свечей...", flush=True)
        kl = fetch_klines(symbol, tf, bars)
        D = prepare(kl)
        d0 = time.strftime("%Y-%m-%d", time.gmtime(D["ts"][150] / 1000))
        d1 = time.strftime("%Y-%m-%d", time.gmtime(D["ts"][-1] / 1000))
        print(f"   {len(kl)} свечей, период {d0} — {d1}", flush=True)
        for adxT in grid_adx:
            for tpR in grid_tp:
                for slm, zw in grid_sl:
                    for fe in grid_flip:
                        r = run(D, adxT, tpR, slm, zw, fe)
                        if r:
                            r.update(tf=tf, adx=adxT, tpR=tpR,
                                     sl=f"{slm}{zw if slm=='zone' else ''}", flip=fe,
                                     period=f"{d0}..{d1}")
                            results.append(r)
        print(f"   готово, конфигураций: {len([x for x in results if x['tf']==tf])}", flush=True)

    with open(os.path.join(CACHE, "results.csv"), "w", newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        wcsv.writeheader(); wcsv.writerows(results)

    print("\n================= ИТОГИ (net в R, после комиссий 0.05%/сторона) =================")
    for tf, _ in tfs:
        sub = [r for r in results if r["tf"] == tf]
        pos = [r for r in sub if r["net"] > 0]
        print(f"\n--- {tf}: конфигураций {len(sub)}, прибыльных {len(pos)} ---")
        for r in sorted(sub, key=lambda x: -x["net"])[:5]:
            wr = 100.0 * r["wins"] / r["trades"]
            print(f"  ADX{r['adx']:>2} TP{r['tpR']:<3} SL={r['sl']:<8} flip={'Y' if r['flip'] else 'N'} | "
                  f"net {r['net']:+7.1f}R  PF {r['pf']:4.2f}  WR {wr:4.1f}%  сделок {r['trades']:>4}  "
                  f"DD {r['dd']:6.1f}R  (TP/SL/FLIP {r['tp']}/{r['slc']}/{r['fl']})  L/S {r['longR']:+.0f}/{r['shortR']:+.0f}R")
    npos = len([r for r in results if r["net"] > 0])
    print(f"\nВСЕГО: {len(results)} конфигураций, из них с net > 0: {npos}")

if __name__ == "__main__":
    main()
