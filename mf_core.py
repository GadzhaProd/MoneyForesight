#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoneyForesight — ядро расчёта индикаторов на чистом Python (семантика Pine).

Общий код для бэктестера (backtest.py) и сканера пульта (scan.py): Chandelier, DMI/ADX,
MACD и Rainbow MA с «теплом». Сетевого кода и кэша здесь нет — только математика над
списками свечей Binance [openTime, o, h, l, c, v, closeTime, ...].
"""

WARMUP = 150              # баров на прогрев индикаторов до первого надёжного значения


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
                heat=heat, volSMA=volSMA, reHigh=reHigh, reLow=reLow, risingADX=risingADX,
                rb=filt)


# ---------- состояние Rainbow для пульта ----------
# Границы корзин тепла — рабочие, пока не откалиброваны по истории (этап 3 плана).
# Кортеж: (верхняя граница тепла, оценка от -2 до +2, подпись)
HEAT_BUCKETS = (
    (0.30, -2, "сильное падение"),
    (0.45, -1, "падение"),
    (0.55, 0, "флэт"),
    (0.70, 1, "рост"),
    (1.01, 2, "сильный рост"),
)

# палитра из Pine (MoneyForesight_v3.pine, блок Rainbow): 0 красный, 0.5 оранжевый, 1 зелёный
_HEAT_RED, _HEAT_ORANGE, _HEAT_GREEN = (0xFF, 0x11, 0x00), (0xFF, 0xAB, 0x00), (0x30, 0xFF, 0x85)


def heat_bucket(heat):
    """(оценка -2..+2, подпись) для тепла Rainbow."""
    for top, score, label in HEAT_BUCKETS:
        if heat < top:
            return score, label
    return HEAT_BUCKETS[-1][1], HEAT_BUCKETS[-1][2]


def heat_color(heat):
    """Цвет линии Rainbow '#rrggbb' — тот же линейный градиент, что color.from_gradient в Pine."""
    h = min(max(heat, 0.0), 1.0)
    if h <= 0.5:
        a, b, t = _HEAT_RED, _HEAT_ORANGE, h / 0.5
    else:
        a, b, t = _HEAT_ORANGE, _HEAT_GREEN, (h - 0.5) / 0.5
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _last_cross(side, i, lo):
    """Текущая сторона и сколько баров назад она установилась.

    side(j) даёт +1 / -1 / 0 (0 — ровно на линии, такие бары пропускаются). Идём назад от
    бара i до lo. 0 баров — пересечение случилось на самом баре i. None — смены стороны на
    доступной истории не было (или сторону определить не удалось)."""
    cur = 0
    for j in range(i, lo - 1, -1):
        s = side(j)
        if s == 0:
            continue
        if cur == 0:
            cur = s
        elif s != cur:
            return cur, i - (j + 1)
    return cur, None


def rainbow_state(D, i, fresh_bars=3, delta_bars=3):
    """Состояние Rainbow на закрытом баре i или None, если индикатор ещё не прогрелся.

    Считает только по барам до i включительно, будущих данных не касается. Два вида
    «пробития» независимы:
      * price_*: цена закрытия относительно самой линии (раньше, но шумнее);
      * heat_*: тепло относительно 0.5 — цвет линии между красно-оранжевой и зелёной
        половинами палитры (позже, но надёжнее).
    *_cross_bars — сколько баров назад установилась текущая сторона (0 — на баре i),
    *_fresh — это случилось в последние fresh_bars баров."""
    if i < WARMUP or i >= D["n"] or D["rb"][i] is None:
        return None
    c, rb, heat = D["c"], D["rb"], D["heat"]

    price_side, price_bars = _last_cross(
        lambda j: (c[j] > rb[j]) - (c[j] < rb[j]), i, WARMUP)
    heat_side, heat_bars = _last_cross(
        lambda j: (heat[j] > 0.5) - (heat[j] < 0.5), i, WARMUP)
    score, label = heat_bucket(heat[i])

    return dict(
        ts=D["ts"][i], close=c[i], line=rb[i], heat=heat[i], color=heat_color(heat[i]),
        bucket=score, label=label,
        heat_delta=heat[i] - heat[i - delta_bars],
        price_side=price_side, price_cross_bars=price_bars,
        price_fresh=price_bars is not None and price_bars < fresh_bars,
        heat_side=heat_side, heat_cross_bars=heat_bars,
        heat_fresh=heat_bars is not None and heat_bars < fresh_bars,
    )


# ---------- сигналы индикатора для пульта ----------
# Порт блока «СИГНАЛЫ И АЛЕРТЫ» из MoneyForesight_v3.pine. Значения по умолчанию — как у
# ИНДИКАТОРА (ADX 20), а не у стратегии (ADX 18). Не портированы: S/R-фильтр (работает только
# на живом баре), ре-вход (требует симуляции виртуальной сделки и выбитого стопа) и грейд A/B.
SIGNAL_DEFAULTS = dict(adx_thr=20.0, use_rising=True, vol_mult=1.2, min_di_spread=5.0,
                       retest_macd=True, retest_cooldown=5, zone_w=0.25)


def signal_series(D, **params):
    """Сигналы индикатора на каждом баре (по закрытию).

    trade   — состояние BUY/SELL: +1 лонг, -1 шорт, 0 — сигнала ещё не было; меняется только
              когда выполнены все условия новой стороны, иначе сохраняется;
    flip    — +1 / -1 на баре, где состояние сменилось (метка BUY / SELL), иначе 0;
    retest  — +1 / -1 на баре ретеста зоны-магнита, иначе 0. Кулдаун считается от прошлого
              ретеста в ту же сторону независимо от позиции — как в индикаторе, но не как в
              backtest.run(), где он обновляется только пока позиции нет;
    adx_ok  — тренд по ADX (порог или растущий ADX выше половины порога)."""
    unknown = set(params) - set(SIGNAL_DEFAULTS)
    if unknown:
        raise TypeError(f"неизвестные параметры сигналов: {sorted(unknown)}")
    p = {**SIGNAL_DEFAULTS, **params}
    if p["min_di_spread"] < 0 or p["retest_cooldown"] < 0:
        raise ValueError("min_di_spread и retest_cooldown должны быть >= 0")

    n = D["n"]
    c, h, l, v = D["c"], D["h"], D["l"], D["v"]
    cs, pc, atr = D["csDir"], D["pc"], D["atr22"]
    zw = p["zone_w"]
    trade, flip, retest, adx_ok_s = [0] * n, [0] * n, [0] * n, [False] * n
    state = 0
    last_rt = {1: -99999, -1: -99999}

    for i in range(n):
        dip, dim, adx, macd, sig = D["dip"][i], D["dim"][i], D["adx"][i], D["macd"][i], D["sig"][i]
        have = None not in (dip, dim, adx, macd, sig)     # в Pine сравнения с na дают false
        vol_sma = D["volSMA"][i]
        vol_ok = vol_sma is None or v[i] > vol_sma * p["vol_mult"]
        thr = p["adx_thr"]
        adx_ok = have and (adx >= thr or (p["use_rising"] and adx >= thr * 0.5 and D["risingADX"][i]))
        adx_ok_s[i] = bool(adx_ok)
        filters_ok = vol_ok and adx_ok

        lc = have and dip > dim and macd > sig and cs[i] > 0 and D["heat"][i] > 0.5 and filters_ok
        sc = have and dim > dip and sig > macd and cs[i] < 0 and D["heat"][i] < 0.5 and filters_ok
        prev = state
        state = 1 if lc else -1 if sc else state
        trade[i] = state
        if state != prev:
            flip[i] = state

        if i == 0 or not filters_ok or pc[i] is None or atr[i] is None:
            continue
        if abs(dip - dim) < p["min_di_spread"]:
            continue
        zt, zb = pc[i] + zw * atr[i], pc[i] - zw * atr[i]
        prev_ok = pc[i - 1] is not None and atr[i - 1] is not None
        zt_p = pc[i - 1] + zw * atr[i - 1] if prev_ok else None
        zb_p = pc[i - 1] - zw * atr[i - 1] if prev_ok else None
        # откат в зону: тень достала край зоны либо прошлая свеча закрылась внутри неё,
        # и текущая закрылась обратно за внешним краем
        rl = (cs[i] > 0 and cs[i - 1] > 0 and c[i] > zt
              and (l[i] <= zt or (zt_p is not None and c[i - 1] <= zt_p))
              and (not p["retest_macd"] or macd > sig))
        rs = (cs[i] < 0 and cs[i - 1] < 0 and c[i] < zb
              and (h[i] >= zb or (zb_p is not None and c[i - 1] >= zb_p))
              and (not p["retest_macd"] or sig > macd))
        for side, hit in ((1, rl), (-1, rs)):
            if hit and i - last_rt[side] > p["retest_cooldown"]:
                retest[i] = side
                last_rt[side] = i

    return dict(trade=trade, flip=flip, retest=retest, adx_ok=adx_ok_s)


def _last_event(series, i, lo):
    """Индекс последнего ненулевого значения в series[lo..i] или None."""
    for j in range(i, lo - 1, -1):
        if series[j]:
            return j
    return None


def signal_state(D, i, sig=None, fresh_bars=3, **params):
    """Сигнальное состояние индикатора на закрытом баре i или None, если он не прогрелся.

    sig — готовый результат signal_series(D, ...): при обходе многих баров не пересчитывать.
    *_since — сколько баров назад случилось событие (0 — на баре i); None — в доступной
    после прогрева истории события не было."""
    if i < WARMUP or i >= D["n"]:
        return None
    sig = sig or signal_series(D, **params)
    f = _last_event(sig["flip"], i, WARMUP)
    r = _last_event(sig["retest"], i, WARMUP)
    dip, dim, macd, sg = D["dip"][i], D["dim"][i], D["macd"][i], D["sig"][i]
    return dict(
        trade=sig["trade"][i],
        trade_since=None if f is None else i - f,
        trade_fresh=f is not None and i - f < fresh_bars,
        retest_dir=0 if r is None else sig["retest"][r],
        retest_since=None if r is None else i - r,
        retest_fresh=r is not None and i - r < fresh_bars,
        cs_dir=D["csDir"][i],
        adx=D["adx"][i], adx_ok=sig["adx_ok"][i],
        di_spread=None if dip is None or dim is None else dip - dim,
        macd_up=None if macd is None or sg is None else macd > sg,
    )
