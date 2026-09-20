#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoneyForesight Strategy - независимый бэктест на чистом Python.
Точная копия логики MoneyForesight_strategy.pine:
  Chandelier(22,22,3) + зона, DMI(14,10)+MACD(12,26,9), Rainbow beta-MA(50)+RSI heat,
  Volume SMA(20)x1.2, ADX-порог с опцией "растущий ADX", ретесты зоны (кулдаун 5),
  минимальный разрыв DI и подтверждение направления ретеста по MACD,
  ре-вход по пробою 5 баров ПОСЛЕ ВЫБИТОГО СТОПА, TP = R x риск,
  SL = край зоны / линия / база пробоя, флип-выход,
  комиссия 0.05% за сторону + проскальзывание 1 тик (как slippage в Pine).
Данные: Binance spot BTCUSDT (публичный API, без ключей).

Модель исполнения (допущения задокументированы, потому что OHLC не даёт
внутрибарного порядка):
  * незакрытая свеча отбрасывается — торгуем только по завершённым барам;
  * если бар открылся за стопом/тейком, исполнение идёт по цене открытия (гэп),
    а не по уровню — иначе убыток на гэпе занижается;
  * бар, задевший и стоп, и тейк, засчитывается как стоп (консервативно);
  * просадка считается побарно по неблагоприятному экстремуму, включая
    нереализованный результат открытой позиции (ddMtM); dd по закрытым сделкам
    сохранён отдельно для сопоставимости со старыми прогонами.

Запуск:
  python3 backtest.py BTCUSDT              # обычный прогон, кэш обновляется при протухании
  python3 backtest.py BTCUSDT --refresh    # принудительно перекачать данные
  python3 backtest.py BTCUSDT --offline    # использовать снапшот как есть (воспроизводимость)
"""
import json, math, time, urllib.request, os, sys, csv, argparse

CACHE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.0005              # 0.05% за сторону
CACHE_SCHEMA = 1          # версия формата кэша; старые голые списки считаются протухшими
SLIPPAGE_TICKS = 1        # как slippage = 1 в MoneyForesight_strategy.pine
WARMUP = 150              # баров на прогрев индикаторов до первой возможной сделки
MIN_DI_SPREAD = 5.0       # минимум DI для ретестов/ре-входов по анализу v3.1
REQUIRE_RETEST_MACD = True  # ретест только при MACD в сторону сделки

# явная схема CSV: файл создаётся даже когда ни одна конфигурация не дала сделок
FIELDS = ["tf", "adx", "tpR", "sl", "flip", "minDi", "rtMacd", "rtCd", "period",
          "net", "pf", "trades", "wins", "dd", "ddMtM",
          "longR", "longPf", "longTrades", "longWins", "longWR", "longDd", "longDdMtM",
          "shortR", "shortPf", "shortTrades", "shortWins", "shortWR", "shortDd", "shortDdMtM",
          "tp", "slc", "fl", "eod"]

_INTERVAL_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def interval_ms(interval):
    """'5m' -> 300000. Нужен, чтобы понять, появились ли новые свечи после кэширования."""
    unit = interval[-1].lower()
    if unit not in _INTERVAL_UNIT_MS:
        raise ValueError(f"неизвестный интервал: {interval}")
    return int(interval[:-1]) * _INTERVAL_UNIT_MS[unit]


def drop_unclosed(klines, now_ms):
    """Убирает текущую формирующуюся свечу: её OHLCV ещё меняется.

    Binance кладёт её в ответ последней; если записать такую свечу в кэш, она
    навсегда застынет в промежуточном состоянии и будет открывать/закрывать
    сделки по неокончательным ценам. Индекс 6 в клайне — closeTime."""
    return [k for k in klines if int(k[6]) < now_ms]


def cache_path(symbol, interval, total_bars):
    return os.path.join(CACHE, f"{symbol}_{interval}_{total_bars}.json")


def load_cache(fn):
    """Читает кэш нового формата. Голый список (старый формат) и чужая схема -> None."""
    if not os.path.exists(fn):
        return None
    try:
        with open(fn) as f:
            payload = json.load(f)
    except (ValueError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
        return None
    if not payload.get("klines"):
        return None
    return payload


def cache_is_stale(payload, interval, now_ms):
    """Протух, если с закрытия последней свечи прошло больше одного интервала."""
    last_close = int(payload["klines"][-1][6])
    return now_ms - last_close > interval_ms(interval)


def fetch_klines(symbol, interval, total_bars, refresh=False, offline=False, now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    fn = cache_path(symbol, interval, total_bars)
    payload = load_cache(fn)

    if payload is not None and (offline or (not refresh and not cache_is_stale(payload, interval, now_ms))):
        return payload["klines"]
    if offline:
        raise RuntimeError(f"--offline, но пригодного снапшота нет: {fn}")

    out = []
    end = now_ms
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
    out = drop_unclosed(out, now_ms)[-total_bars:]
    with open(fn, "w") as f:
        json.dump({"schema": CACHE_SCHEMA, "symbol": symbol, "interval": interval,
                   "bars": total_bars, "fetched_at_ms": now_ms, "klines": out}, f)
    return out


def fetch_tick_size(symbol, offline=False):
    """Шаг цены инструмента — база для проскальзывания. При недоступности -> 0.0."""
    fn = os.path.join(CACHE, f"{symbol}_ticksize.json")
    if os.path.exists(fn):
        try:
            with open(fn) as f:
                return float(json.load(f)["tickSize"])
        except (ValueError, OSError, KeyError):
            pass
    if offline:
        return 0.0
    try:
        url = f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}"
        with urllib.request.urlopen(url, timeout=20) as r:
            info = json.load(r)
        filters = info["symbols"][0]["filters"]
        tick = float(next(f["tickSize"] for f in filters if f["filterType"] == "PRICE_FILTER"))
    except Exception as e:
        print(f"   ! шаг цены не получен ({e}); проскальзывание отключено", flush=True)
        return 0.0
    with open(fn, "w") as f:
        json.dump({"symbol": symbol, "tickSize": tick}, f)
    return tick


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


def rising(src, i, length):
    """Аналог Pine ta.rising(src, length): текущее значение больше МАКСИМУМА
    length предыдущих. Монотонность предыдущих значений не требуется —
    например 17, 18, 15, 20 проходит (20 > max(15, 18, 17))."""
    if i < length:
        return False
    window = src[i - length:i]
    if src[i] is None or any(v is None for v in window):
        return False
    return src[i] > max(window)


def prepare(kl):
    n = len(kl)
    o = [float(k[1]) for k in kl]; h = [float(k[2]) for k in kl]
    l = [float(k[3]) for k in kl]; c = [float(k[4]) for k in kl]
    v = [float(k[5]) for k in kl]; ts = [int(k[0]) for k in kl]

    # Pine ta.tr на первом баре возвращает high - low (предыдущего close ещё нет).
    # Без затравки ATR стартовал на бар позже, а направление Chandelier — состояние,
    # поэтому сдвиг закреплялся навсегда.
    tr = [None] * n
    if n:
        tr[0] = h[0] - l[0]
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
            # разворот вверх — закрытие выше обеих линий прошлой свечи, вниз — ниже обеих
            # (без требования пересечения — иначе после импульса состояние застревало)
            longswitch  = c[i] >= shortvs[i - 1] and c[i] > longvs[i - 1]
            shortswitch = c[i] <= longvs[i - 1] and c[i] < shortvs[i - 1]
            if d >= 0 and shortswitch: d = -1
            elif d <= 0 and longswitch: d = 1
        csDir[i] = d
    pc = [None] * n
    for i in range(n):
        pc[i] = longvs[i] if csDir[i] > 0 else shortvs[i]

    # DMI / ADX (14,10). pdm/mdm на первом баре остаются None: в Pine ta.change(high)
    # там na, поэтому DM тоже na — в отличие от ta.tr, у которого затравка есть.
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

    risingADX = [rising(adx, i, 3) for i in range(n)]

    return dict(n=n, o=o, h=h, l=l, c=c, v=v, ts=ts, atr22=atr22, atr14=atr14,
                csDir=csDir, pc=pc, dip=dip, dim=dim, adx=adx, macd=macd, sig=sig,
                heat=heat, volSMA=volSMA, reHigh=reHigh, reLow=reLow, risingADX=risingADX)


def run(D, adxThr, tpR, slmode, zoneW, flipExit, useRising=True, volMult=1.2,
        start=WARMUP, tick=0.0, slippage_ticks=SLIPPAGE_TICKS,
        min_di_spread=MIN_DI_SPREAD, require_retest_macd=REQUIRE_RETEST_MACD,
        retest_cooldown=5):
    if min_di_spread < 0:
        raise ValueError("min_di_spread должен быть >= 0")
    if retest_cooldown < 0:
        raise ValueError("retest_cooldown должен быть >= 0")

    n = D["n"]; o, c, h, l = D["o"], D["c"], D["h"], D["l"]
    csDir, pc, atr = D["csDir"], D["pc"], D["atr22"]
    slip = tick * slippage_ticks
    trades = []          # (dir, R_net, outcome)
    tradeState = 0
    pos = None           # (dir, fill, plannedRisk, sl, tp, entryBar)
    lastRtL = lastRtS = -9999
    # ре-вход разрешён только после ВЫБИТОГО СТОПА и только в том же направлении
    reArmLong = reArmShort = False
    equityR = 0.0; peakClosed = 0.0; ddClosed = 0.0
    peakMark = 0.0; ddMtM = 0.0
    sideEquity = {1: 0.0, -1: 0.0}
    sidePeakClosed = {1: 0.0, -1: 0.0}
    sideDdClosed = {1: 0.0, -1: 0.0}
    sidePeakMark = {1: 0.0, -1: 0.0}
    sideDdMtM = {1: 0.0, -1: 0.0}

    def close_trade(d, fill, prisk, exit_price, outcome):
        """Считает результат в R. Знаменатель — ПЛАНОВЫЙ риск (close - sl), как
        считает размер позиции Pine: проскальзывание должно быть видно в
        реализованном R, а не прятаться в нормировке."""
        exit_fill = exit_price - slip if d == 1 else exit_price + slip
        gross = (exit_fill - fill) / prisk * d
        feeR = FEE * (fill + exit_fill) / prisk
        trades.append((d, gross - feeR, outcome))
        return gross - feeR

    for i in range(start, n):
        if None in (pc[i], atr[i], D["dip"][i], D["dim"][i], D["adx"][i],
                    D["macd"][i], D["sig"][i], D["volSMA"][i]):
            continue
        zT = pc[i] + zoneW * atr[i]; zB = pc[i] - zoneW * atr[i]
        zTp = pc[i - 1] + zoneW * atr[i - 1] if atr[i - 1] and pc[i - 1] else None
        zBp = pc[i - 1] - zoneW * atr[i - 1] if atr[i - 1] and pc[i - 1] else None
        volOK = D["v"][i] > D["volSMA"][i] * volMult
        adxOK = D["adx"][i] >= adxThr or (useRising and D["adx"][i] >= adxThr * 0.5 and D["risingADX"][i])
        fOK = volOK and adxOK
        diSpreadOK = abs(D["dip"][i] - D["dim"][i]) >= min_di_spread
        lc = (D["dip"][i] > D["dim"][i] and D["macd"][i] > D["sig"][i] and csDir[i] > 0
              and D["heat"][i] > 0.5 and fOK)
        sc = (D["dim"][i] > D["dip"][i] and D["sig"][i] > D["macd"][i] and csDir[i] < 0
              and D["heat"][i] < 0.5 and fOK)
        prevTrade = tradeState
        tradeState = 1 if lc else (-1 if sc else tradeState)
        longFlip = tradeState == 1 and prevTrade != 1
        shortFlip = tradeState == -1 and prevTrade != -1

        # выходы. Бар, открывшийся за уровнем, исполняется по открытию — цена уровня
        # после гэпа недостижима. Бар, задевший и стоп, и тейк, считаем стопом.
        if pos is not None:
            d, fill, prisk, sl, tp, eb = pos
            if i > eb:
                exitP = None; out = None
                if d == 1:
                    if o[i] <= sl: exitP, out = o[i], "SL"
                    elif l[i] <= sl: exitP, out = sl, "SL"
                    elif o[i] >= tp: exitP, out = o[i], "TP"
                    elif h[i] >= tp: exitP, out = tp, "TP"
                    elif flipExit and csDir[i] < 0: exitP, out = c[i], "FLIP"
                else:
                    if o[i] >= sl: exitP, out = o[i], "SL"
                    elif h[i] >= sl: exitP, out = sl, "SL"
                    elif o[i] <= tp: exitP, out = o[i], "TP"
                    elif l[i] <= tp: exitP, out = tp, "TP"
                    elif flipExit and csDir[i] > 0: exitP, out = c[i], "FLIP"
                if exitP is not None:
                    tradeR = close_trade(d, fill, prisk, exitP, out)
                    equityR += tradeR
                    peakClosed = max(peakClosed, equityR)
                    ddClosed = min(ddClosed, equityR - peakClosed)
                    sideEquity[d] += tradeR
                    sidePeakClosed[d] = max(sidePeakClosed[d], sideEquity[d])
                    sideDdClosed[d] = min(sideDdClosed[d], sideEquity[d] - sidePeakClosed[d])
                    if out == "SL":
                        if d == 1: reArmLong = True
                        else: reArmShort = True
                    pos = None

        # входы
        if pos is None:
            rtL = (csDir[i] > 0 and csDir[i - 1] > 0 and zTp is not None and c[i] > zT
                   and (l[i] <= zT or c[i - 1] <= zTp) and fOK and diSpreadOK
                   and (not require_retest_macd or D["macd"][i] > D["sig"][i])
                   and i - lastRtL > retest_cooldown)
            if rtL: lastRtL = i
            rtS = (csDir[i] < 0 and csDir[i - 1] < 0 and zBp is not None and c[i] < zB
                   and (h[i] >= zB or c[i - 1] >= zBp) and fOK and diSpreadOK
                   and (not require_retest_macd or D["sig"][i] > D["macd"][i])
                   and i - lastRtS > retest_cooldown)
            if rtS: lastRtS = i
            reL = (reArmLong and lc and csDir[i] > 0
                   and D["reHigh"][i - 1] is not None and c[i] > D["reHigh"][i - 1]
                   and diSpreadOK)
            reS = (reArmShort and sc and csDir[i] < 0
                   and D["reLow"][i - 1] is not None and c[i] < D["reLow"][i - 1]
                   and diSpreadOK)
            if longFlip or rtL or reL:
                isRe = not (longFlip or rtL)
                sl = D["reLow"][i - 1] if isRe else (zB if slmode == "zone" else pc[i])
                risk = c[i] - sl
                if risk > 0:
                    pos = (1, c[i] + slip, risk, sl, c[i] + tpR * risk, i)
                    reArmLong = False
            elif shortFlip or rtS or reS:
                isRe = not (shortFlip or rtS)
                sl = D["reHigh"][i - 1] if isRe else (zT if slmode == "zone" else pc[i])
                risk = sl - c[i]
                if risk > 0:
                    pos = (-1, c[i] - slip, risk, sl, c[i] - tpR * risk, i)
                    reArmShort = False

        # просадка по рынку: открытая позиция маркируется по НЕБЛАГОПРИЯТНОМУ
        # экстремуму бара, комиссии круга учтены — иначе крупный нереализованный
        # минус, который потом отыгрался, вообще не попадал в максимальную просадку
        markR = equityR
        if pos is not None:
            d, fill, prisk, _, _, eb = pos
            # на баре входа позиция открылась по закрытию — экстремум бара мог
            # случиться ДО входа, поэтому маркируем по close, а не по low/high
            adverse = c[i] if i == eb else (l[i] if d == 1 else h[i])
            markR += (adverse - fill) / prisk * d - FEE * (fill + adverse) / prisk
        peakMark = max(peakMark, markR)
        ddMtM = min(ddMtM, markR - peakMark)
        for direction in (1, -1):
            sideMark = sideEquity[direction]
            if pos is not None and pos[0] == direction:
                d, fill, prisk, _, _, eb = pos
                adverse = c[i] if i == eb else (l[i] if d == 1 else h[i])
                sideMark += (adverse - fill) / prisk * d - FEE * (fill + adverse) / prisk
            sidePeakMark[direction] = max(sidePeakMark[direction], sideMark)
            sideDdMtM[direction] = min(sideDdMtM[direction],
                                       sideMark - sidePeakMark[direction])

    # позиция, дожившая до конца датасета, закрывается по последнему close:
    # иначе она молча выпадала из всех метрик
    if pos is not None:
        d, fill, prisk, _, _, _ = pos
        tradeR = close_trade(d, fill, prisk, c[n - 1], "EOD")
        equityR += tradeR
        peakClosed = max(peakClosed, equityR)
        ddClosed = min(ddClosed, equityR - peakClosed)
        sideEquity[d] += tradeR
        sidePeakClosed[d] = max(sidePeakClosed[d], sideEquity[d])
        sideDdClosed[d] = min(sideDdClosed[d], sideEquity[d] - sidePeakClosed[d])
        peakMark = max(peakMark, equityR)
        ddMtM = min(ddMtM, equityR - peakMark)
        sidePeakMark[d] = max(sidePeakMark[d], sideEquity[d])
        sideDdMtM[d] = min(sideDdMtM[d], sideEquity[d] - sidePeakMark[d])

    # метрики
    if not trades:
        return None
    net = sum(t[1] for t in trades)
    gw = sum(t[1] for t in trades if t[1] > 0)
    gl = -sum(t[1] for t in trades if t[1] < 0)
    pf = gw / gl if gl > 0 else float("inf")
    wins = sum(1 for t in trades if t[1] > 0)

    def side_metrics(direction):
        side = [t for t in trades if t[0] == direction]
        sideNet = sum(t[1] for t in side)
        sideGrossWin = sum(t[1] for t in side if t[1] > 0)
        sideGrossLoss = -sum(t[1] for t in side if t[1] < 0)
        sidePf = sideGrossWin / sideGrossLoss if sideGrossLoss > 0 else (
            float("inf") if sideGrossWin > 0 else 0.0)
        sideWins = sum(1 for t in side if t[1] > 0)
        sideTrades = len(side)
        sideWr = 100.0 * sideWins / sideTrades if sideTrades else 0.0
        return sideNet, sidePf, sideTrades, sideWins, sideWr

    longR, longPf, longTrades, longWins, longWR = side_metrics(1)
    shortR, shortPf, shortTrades, shortWins, shortWR = side_metrics(-1)
    return dict(net=net, pf=pf, trades=len(trades), wins=wins,
                dd=ddClosed, ddMtM=ddMtM, longR=longR, shortR=shortR,
                longPf=longPf, longTrades=longTrades, longWins=longWins, longWR=longWR,
                longDd=sideDdClosed[1], longDdMtM=sideDdMtM[1],
                shortPf=shortPf, shortTrades=shortTrades, shortWins=shortWins, shortWR=shortWR,
                shortDd=sideDdClosed[-1], shortDdMtM=sideDdMtM[-1],
                tp=sum(1 for t in trades if t[2] == "TP"),
                slc=sum(1 for t in trades if t[2] == "SL"),
                fl=sum(1 for t in trades if t[2] == "FLIP"),
                eod=sum(1 for t in trades if t[2] == "EOD"))


def prepare_tf(symbol, tf, bars, refresh=False, offline=False, start=WARMUP, fetch=fetch_klines):
    """Качает и готовит один таймфрейм. None — истории не хватает на прогрев."""
    kl = fetch(symbol, tf, bars, refresh=refresh, offline=offline)
    if len(kl) <= start:
        print(f"   ! свечей {len(kl)}, нужно больше {start} на прогрев — таймфрейм пропущен", flush=True)
        return None
    D = prepare(kl)
    d0 = time.strftime("%Y-%m-%d", time.gmtime(D["ts"][start] / 1000))
    d1 = time.strftime("%Y-%m-%d", time.gmtime(D["ts"][-1] / 1000))
    print(f"   {len(kl)} свечей, период {d0} — {d1}", flush=True)
    return D, d0, d1


def write_results(results, path):
    """CSV пишется по явной схеме, поэтому пустая сетка не роняет прогон."""
    with open(path, "w", newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=FIELDS)
        wcsv.writeheader()
        wcsv.writerows(results)


def main(argv=None):
    ap = argparse.ArgumentParser(description="MoneyForesight — сетка бэктестов по таймфреймам")
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--refresh", action="store_true", help="принудительно перекачать свечи")
    ap.add_argument("--offline", action="store_true", help="использовать снапшот кэша как есть")
    ap.add_argument("--slippage-ticks", type=float, default=SLIPPAGE_TICKS,
                    help="проскальзывание в тиках на сторону (по умолчанию 1, как в Pine)")
    ap.add_argument("--min-di-spread", type=float, default=MIN_DI_SPREAD,
                    help="минимум |DI+ - DI-| для ретеста/ре-входа "
                         "(0 вместе с --allow-retest-macd-mismatch = исходная v3.1)")
    ap.add_argument("--allow-retest-macd-mismatch", action="store_true",
                    help="разрешить ретест против MACD (воспроизводит исходную v3.1)")
    ap.add_argument("--retest-cooldown", type=int, default=5,
                    help="минимум баров между ретестами одного направления")
    args = ap.parse_args(argv)
    symbol = args.symbol

    print(f"########## {symbol} ##########")
    tick = fetch_tick_size(symbol, offline=args.offline)
    print(f"шаг цены {tick}, проскальзывание {args.slippage_ticks} тик(а) на сторону")
    print(f"фильтр продолжений: |DI+ - DI-| >= {args.min_di_spread}, "
          f"MACD для ретеста {'не обязателен' if args.allow_retest_macd_mismatch else 'обязателен'}")
    tfs = [("5m", 50000), ("15m", 35000), ("30m", 35000), ("1h", 17500), ("4h", 8000)]
    grid_adx = [18, 23, 28]
    grid_tp = [1.5, 2.0, 3.0]
    grid_sl = [("zone", 0.25), ("zone", 0.40), ("line", 0.25)]
    grid_flip = [True, False]

    results = []
    for tf, bars in tfs:
        print(f"== {tf}: качаю {bars} свечей...", flush=True)
        prepared = prepare_tf(symbol, tf, bars, refresh=args.refresh, offline=args.offline)
        if prepared is None:
            continue
        D, d0, d1 = prepared
        for adxT in grid_adx:
            for tpR in grid_tp:
                for slm, zw in grid_sl:
                    for fe in grid_flip:
                        r = run(D, adxT, tpR, slm, zw, fe, tick=tick,
                                slippage_ticks=args.slippage_ticks,
                                min_di_spread=args.min_di_spread,
                                require_retest_macd=not args.allow_retest_macd_mismatch,
                                retest_cooldown=args.retest_cooldown)
                        if r:
                            r.update(tf=tf, adx=adxT, tpR=tpR,
                                     sl=f"{slm}{zw if slm=='zone' else ''}", flip=fe,
                                     minDi=args.min_di_spread,
                                     rtMacd=not args.allow_retest_macd_mismatch,
                                     rtCd=args.retest_cooldown,
                                     period=f"{d0}..{d1}")
                            results.append(r)
        print(f"   готово, конфигураций: {len([x for x in results if x['tf']==tf])}", flush=True)

    out_csv = os.path.join(CACHE, "results.csv")
    write_results(results, out_csv)
    if not results:
        print(f"\nНИ ОДНА конфигурация не дала закрытых сделок. {out_csv} создан с одним "
              f"заголовком. Проверьте символ, доступность данных и порог ADX.")
        return

    print("\n================= ИТОГИ (net в R, после комиссий 0.05%/сторона и проскальзывания) =================")
    for tf, _ in tfs:
        sub = [r for r in results if r["tf"] == tf]
        if not sub:
            continue
        pos = [r for r in sub if r["net"] > 0]
        print(f"\n--- {tf}: конфигураций {len(sub)}, прибыльных {len(pos)} ---")
        for r in sorted(sub, key=lambda x: -x["net"])[:5]:
            wr = 100.0 * r["wins"] / r["trades"]
            print(f"  ADX{r['adx']:>2} TP{r['tpR']:<3} SL={r['sl']:<8} flip={'Y' if r['flip'] else 'N'} | "
                  f"net {r['net']:+7.1f}R  PF {r['pf']:4.2f}  WR {wr:4.1f}%  сделок {r['trades']:>4}  "
                  f"DD {r['dd']:6.1f}R  DDmtm {r['ddMtM']:6.1f}R  (TP/SL/FLIP {r['tp']}/{r['slc']}/{r['fl']})  "
                  f"L/S {r['longR']:+.0f}/{r['shortR']:+.0f}R")
    npos = len([r for r in results if r["net"] > 0])
    print(f"\nВСЕГО: {len(results)} конфигураций, из них с net > 0: {npos}")


if __name__ == "__main__":
    main()
