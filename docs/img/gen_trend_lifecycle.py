# Схема жизненного цикла тренда: синтетические свечи, Chandelier и входы
# считаются по тем же правилам, что в индикаторе (упрощённые периоды).
import os, random
random.seed(7)

# опорные точки закрытий: (бар, цена)
keys = [(0,121),(6,112),(10,104),(14,97),(17,95.5),(19,97),(21,104),(23,108),(27,114),
        (30,117),(33,112.8),(34,114.5),(36,119),(40,126),(43,128),(46,124.2),(47,124.8),(48,124.6),
        (51,131),(54,134),(57,131),(60,126),(62,121),(64,118),(66,116)]
N = keys[-1][0] + 1
closes = []
for (a, pa), (b, pb) in zip(keys, keys[1:]):
    for i in range(a, b):
        closes.append(pa + (pb - pa) * (i - a) / (b - a) + random.uniform(-0.6, 0.6))
closes.append(keys[-1][1])
O, H, L, C = [], [], [], []
for i, c in enumerate(closes):
    o = closes[i - 1] if i else c + 1
    hi = max(o, c) + random.uniform(0.3, 1.3)
    lo = min(o, c) - random.uniform(0.3, 1.3)
    O.append(o); H.append(hi); L.append(lo); C.append(c)
# сценарные тени: касание зоны (ретест), пролив под стоп без закрытия под линией
L[34] = 110.2      # тень в зону, закрытие над ней -> ретест
L[46] = 122.0      # второй ретест: тень в зону
L[48] = 119.6      # глубокий пролив: выбивает стоп ретеста, но закрытие выше линии

LB, MULT, ZW = 10, 2.6, 0.35
tr = [H[0] - L[0]] + [max(H[i], C[i-1]) - min(L[i], C[i-1]) for i in range(1, N)]
atr = []
for i in range(N):
    atr.append(tr[i] if i == 0 else atr[-1] + (tr[i] - atr[-1]) / LB)
longvs = shortvs = None; cs = []; pc = []; d = -1
for i in range(N):
    hh = max(H[max(0, i-LB+1):i+1]); ll = min(L[max(0, i-LB+1):i+1])
    ls = hh - MULT * atr[i]; ss = ll + MULT * atr[i]
    pl, ps = longvs, shortvs
    shortvs = ss if ps is None or C[i] > ps else min(ss, ps)
    longvs = ls if pl is None or C[i] < pl else max(ls, pl)
    if i and ps is not None and C[i] >= ps and C[i-1] < ps and d <= 0: d = 1
    elif i and pl is not None and C[i] <= pl and C[i-1] > pl and d >= 0: d = -1
    cs.append(d); pc.append(longvs if d > 0 else shortvs)
zt = [pc[i] + ZW * atr[i] for i in range(N)]; zb = [pc[i] - ZW * atr[i] for i in range(N)]

# события по правилам индикатора
events = []  # (bar, kind)
pos = None; rearm = False; last_rt = -99
for i in range(1, N):
    if pos:
        dd, entry, sl, tp = pos
        if dd == 1 and L[i] <= sl: events.append((i, "SL")); pos = None; rearm = True
        elif dd == 1 and H[i] >= tp: events.append((i, "TP")); pos = None
        elif dd == 1 and cs[i] < 0: events.append((i, "FLIPX")); pos = None
    flipup = cs[i] == 1 and cs[i-1] == -1
    flipdn = cs[i] == -1 and cs[i-1] == 1
    if flipup: events.append((i, "FLIPUP"))
    if flipdn: events.append((i, "FLIPDN"))
    rt = cs[i] > 0 and cs[i-1] > 0 and C[i] > zt[i] and (L[i] <= zt[i] or C[i-1] <= zt[i-1]) and i - last_rt > 3
    if rt: last_rt = i
    re = rearm and cs[i] > 0 and C[i] > max(H[i-5:i])
    if pos is None and i > 0:
        if flipup and not any(e[1] == "BUY" for e in events):
            sl = zb[i]; pos = (1, C[i], sl, C[i] + 2 * (C[i] - sl)); events.append((i, "BUY"))
        elif rt:
            sl = zb[i]; pos = (1, C[i], sl, C[i] + 2 * (C[i] - sl)); events.append((i, "RT"))
        elif re:
            sl = min(L[i-5:i]); pos = (1, C[i], sl, C[i] + 2 * (C[i] - sl)); events.append((i, "RE")); rearm = False
    elif rt:
        events.append((i, "RTx"))
    if flipdn: events.append((i, "SELL"))

# ── отрисовка ──
W, Hh = 1000, 590
X0, X1, Y0, Y1 = 40, 960, 70, 400
pmin, pmax = 88, 142
def x(i): return X0 + (X1 - X0) * (i + 0.5) / N
def y(p): return Y1 - (Y1 - Y0) * (p - pmin) / (pmax - pmin)
AQ, FU, GR, RD = "#00BCD4", "#E040FB", "#26A69A", "#EF5350"
out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {Hh}" width="{W}" height="{Hh}" font-family="-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">',
       f'<rect width="{W}" height="{Hh}" rx="10" fill="#131722"/>',
       '<text x="40" y="36" fill="#D1D4DC" font-size="19" font-weight="600">Жизненный цикл тренда в MoneyForesight</text>',
       '<text x="40" y="58" fill="#8A8F9C" font-size="13">Схема на синтетических данных: линия Chandelier, зона-магнит и типы входов</text>']
# сетка
for p in range(90, 141, 10):
    out.append(f'<line x1="{X0}" x2="{X1}" y1="{y(p):.1f}" y2="{y(p):.1f}" stroke="#2A2E39" stroke-width="1"/>')
# зона и линия по сегментам одного направления
seg = 0
for i in range(1, N + 1):
    if i == N or cs[i] != cs[seg]:
        col = AQ if cs[seg] > 0 else FU
        idx = list(range(seg, i))
        top = " ".join(f"{x(j):.1f},{y(zt[j]):.1f}" for j in idx)
        bot = " ".join(f"{x(j):.1f},{y(zb[j]):.1f}" for j in reversed(idx))
        out.append(f'<polygon points="{top} {bot}" fill="{col}" fill-opacity="0.16"/>')
        line = " ".join(f"{x(j):.1f},{y(pc[j]):.1f}" for j in idx)
        out.append(f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="2.5" stroke-linejoin="round"/>')
        seg = i
# свечи
cw = (X1 - X0) / N * 0.62
for i in range(N):
    col = GR if C[i] >= O[i] else RD
    out.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(H[i]):.1f}" y2="{y(L[i]):.1f}" stroke="{col}" stroke-width="1.3"/>')
    top, bot = y(max(O[i], C[i])), y(min(O[i], C[i]))
    out.append(f'<rect x="{x(i)-cw/2:.1f}" y="{top:.1f}" width="{cw:.1f}" height="{max(bot-top,1.2):.1f}" fill="{col}"/>')

def badge(cx, cy, n):
    out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="11" fill="#D1D4DC"/><text x="{cx:.1f}" y="{cy+4.5:.1f}" text-anchor="middle" font-size="13" font-weight="700" fill="#131722">{n}</text>')

ev = dict()
for i, k in events: ev.setdefault(k, []).append(i)
# 1 — разворот вверх (закрытие над фиолетовой линией)
fu = ev["FLIPUP"][0]
out.append(f'<rect x="{x(fu)-19:.1f}" y="{y(L[fu])+12:.1f}" width="38" height="18" rx="3" fill="{GR}"/><text x="{x(fu):.1f}" y="{y(L[fu])+25:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">BUY</text>')
badge(x(fu) - 32, y(L[fu]) + 21, 1)
# 2 — ретест
for rt in ev.get("RT", []) + ev.get("RTx", []):
    out.append(f'<path d="M{x(rt):.1f},{y(L[rt])+8:.1f} l-6,10 h12 z" fill="{AQ}"/>')
rt = ev["RT"][0]
badge(x(rt), y(L[rt]) + 32, 2)
# TP / SL метки
def tag(i, price, text, col, dy):
    out.append(f'<rect x="{x(i)-15:.1f}" y="{y(price)+dy-10:.1f}" width="30" height="16" rx="3" fill="{col}"/><text x="{x(i):.1f}" y="{y(price)+dy+2:.1f}" text-anchor="middle" font-size="10" font-weight="700" fill="#fff">{text}</text>')
for i in ev.get("TP", []): tag(i, H[i], "TP", GR, -14)
for i in ev.get("SL", []): tag(i, L[i], "SL", RD, 18)
sl = ev["SL"][0]
badge(x(sl) + 24, y(L[sl]) + 16, 3)
# 4 — ре-вход
re = ev["RE"][0]
cx, cy = x(re), y(L[re]) + 14
out.append(f'<path d="M{cx:.1f},{cy-6:.1f} l6,7 l-6,7 l-6,-7 z" fill="{AQ}"/>')
badge(cx + 20, cy + 1, 4)
# 5 — разворот вниз
fd = ev["FLIPDN"][0]
for i in ev.get("FLIPX", []): tag(i, C[i], "FLIP", "#787B86", 22)
out.append(f'<rect x="{x(fd)-21:.1f}" y="{y(H[fd])-44:.1f}" width="42" height="18" rx="3" fill="{RD}"/><text x="{x(fd):.1f}" y="{y(H[fd])-31:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">SELL</text>')
badge(x(fd) + 34, y(H[fd]) - 35, 5)

# подписи этапов
caps = [
 (1, "Разворот вверх.", "Закрытие над фиолетовой линией — она уходит под цену и становится голубой. BUY — если подтвердили фильтры."),
 (2, "Ретест зоны.", "Откат в зону и закрытие над ней — треугольник. Стоп под зоной, тейк 2R. Главный источник прибыли в бэктесте."),
 (3, "Стоп.", "Тень прошла сквозь зону и выбила стоп, но свеча закрылась над линией — тренд не сломан."),
 (4, "Ре-вход.", "После стопа цена обновила максимум 5 баров при живом тренде — ромб, стоп под базой пробоя."),
 (5, "Разворот вниз.", "Закрытие под голубой линией: тренд сменился, сделка закрыта по FLIP, дальше ждём SELL."),
]
yy = 438
for k, (n, t, txt) in enumerate(caps):
    by = yy + k * 29
    badge(51, by, n)
    out.append(f'<text x="70" y="{by+5}" font-size="13.5" fill="#D1D4DC"><tspan font-weight="700">{t} </tspan><tspan fill="#9598A1">{txt}</tspan></text>')
out.append('</svg>')
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trend-lifecycle.svg"), "w", encoding="utf-8").write("\n".join(out))
