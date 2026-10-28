#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MoneyForesight — сканер пульта (этап 1): состояние Rainbow по монетам и таймфреймам.

Для каждой пары «монета × таймфрейм» берёт последнюю ЗАКРЫТУЮ свечу и показывает:
  цвет линии (тепло 0..1), положение цены относительно линии и свежие пробития.

Ячейка:  🟩 0.62↑*+
  🟥🟥 сильное падение · 🟧 падение · 🟨 флэт · 🟩 рост · 🟩🟩 сильный рост  (по теплу)
  число — тепло Rainbow (0 красный, 0.5 оранжевый, 1 зелёный)
  ↑ / ↓ — цена закрытия выше / ниже линии
  *     — цена пересекла линию в последние N свечей этого таймфрейма (FRESH_BARS)
  +     — цвет линии перешёл через 0.5 в последние N свечей

Вторая таблица — сигналы самого индикатора (BUY/SELL по состоянию и ретест зоны-магнита):
  LONG 12   — состояние BUY, возникло 12 свечей назад (… — давно, до окна истории)
  SHORT 3!  — состояние SELL, ! — сигнал новый (в последние N свечей)
  r↑ / r↓   — свежий ретест зоны-магнита вверх / вниз

Запуск:
  python3 scan.py                          # монеты и таймфреймы из mf_config.py
  python3 scan.py BTCUSDT ETHUSDT          # свои монеты
  python3 scan.py BTCUSDT --tf 1d --tf 4h  # только выбранные таймфреймы
  python3 scan.py BTCUSDT --tf 4h --inspect 5   # последние 5 свечей: цена, линия, тепло
  python3 scan.py --offline                # только кэш, без сети

--inspect нужен для сверки с TradingView: значение «Rainbow MA» в окне данных графика
на той же свече должно совпасть со столбцом «линия».
Если api.binance.com недоступен, задайте MF_BINANCE_API=https://data-api.binance.vision
"""
import argparse, sys, time

import backtest as bt
import mf_config as cfg
from mf_core import WARMUP, prepare, rainbow_state, signal_state

BUCKET_EMOJI = {-2: "🟥🟥", -1: "🟧  ", 0: "🟨  ", 1: "🟩  ", 2: "🟩🟩"}   # ширина 4 колонки у всех
ARROW = {1: "↑", -1: "↓", 0: "·"}
SIGNAL_LABEL = {1: "LONG", -1: "SHORT", 0: "—"}
EMOJI_W, TEXT_W = 4, 9
CELL_W = EMOJI_W + 1 + TEXT_W


def scan_cell(symbol, tf, bars, fetch, fresh_bars, **fetch_kw):
    """Состояние Rainbow на последней закрытой свече. None — истории мало на прогрев."""
    kl = fetch(symbol, tf, bars, **fetch_kw)
    if len(kl) <= WARMUP:
        return None
    D = prepare(kl)
    i = D["n"] - 1
    state = rainbow_state(D, i, fresh_bars=fresh_bars)
    if state is not None:
        state["signal"] = signal_state(D, i, fresh_bars=fresh_bars)
    return state


def cell_text(state):
    """state: dict | None (н/д) | str (текст ошибки)."""
    if isinstance(state, str):
        return "ошибка".ljust(CELL_W)
    if state is None:
        return "н/д".ljust(CELL_W)
    marks = ("*" if state["price_fresh"] else "") + ("+" if state["heat_fresh"] else "")
    text = f"{state['heat']:.2f}{ARROW[state['price_side']]}{marks}"
    return BUCKET_EMOJI[state["bucket"]] + " " + text.ljust(TEXT_W)


def signal_cell_text(state):
    """Ячейка второй таблицы: состояние BUY/SELL индикатора, давность, свежий сигнал и ретест."""
    if isinstance(state, str):
        return "ошибка".ljust(CELL_W)
    sg = state.get("signal") if state else None
    if sg is None:
        return "н/д".ljust(CELL_W)
    if sg["trade"] == 0:
        text = SIGNAL_LABEL[0]
    else:
        since = "…" if sg["trade_since"] is None else str(sg["trade_since"])
        text = f"{SIGNAL_LABEL[sg['trade']]} {since}" + ("!" if sg["trade_fresh"] else "")
    if sg["retest_fresh"]:
        text += f" r{ARROW[sg['retest_dir']]}"
    return text.ljust(CELL_W)


def _ago(bars):
    return "на последней свече" if bars == 0 else f"{bars} св. назад"


def events(symbol, tf, state):
    """Человекочитаемые свежие пробития для одной ячейки."""
    if not isinstance(state, dict):
        return []
    out = []
    if state["price_fresh"]:
        way = "вверх" if state["price_side"] > 0 else "вниз"
        out.append(f"{symbol} {tf}: цена пересекла линию {way} ({_ago(state['price_cross_bars'])})")
    if state["heat_fresh"]:
        way = "в зелёную половину" if state["heat_side"] > 0 else "в красно-оранжевую половину"
        out.append(f"{symbol} {tf}: цвет линии перешёл через 0.5 {way} ({_ago(state['heat_cross_bars'])})")
    sg = state.get("signal")
    if sg and sg["trade_fresh"]:
        way = "BUY (лонг)" if sg["trade"] > 0 else "SELL (шорт)"
        out.append(f"{symbol} {tf}: сигнал индикатора {way} ({_ago(sg['trade_since'])})")
    if sg and sg["retest_fresh"]:
        way = "вверх (лонг)" if sg["retest_dir"] > 0 else "вниз (шорт)"
        out.append(f"{symbol} {tf}: ретест зоны-магнита {way} ({_ago(sg['retest_since'])})")
    return out


def format_table(rows, symbols, tfs, cell=cell_text):
    head = "Монета".ljust(10) + "".join(tf.upper().ljust(CELL_W + 2) for tf in tfs)
    lines = [head, "-" * len(head)]
    for sym in symbols:
        lines.append(sym.replace("USDT", "").ljust(10)
                     + "".join(cell(rows[sym][tf]).ljust(CELL_W) + "  " for tf in tfs))
    return "\n".join(lines)


def inspect(symbol, tf, bars, count, fetch, **fetch_kw):
    """Последние count свечей с линией и теплом — для сверки значений с TradingView."""
    kl = fetch(symbol, tf, bars, **fetch_kw)
    D = prepare(kl)
    lines = [f"{symbol} {tf}: последние {count} закрытых свечей (время UTC, начало свечи)",
             "время              закрытие         линия Rainbow    тепло"]
    for i in range(max(D["n"] - count, 0), D["n"]):
        t = time.strftime("%Y-%m-%d %H:%M", time.gmtime(D["ts"][i] / 1000))
        line = "н/д" if D["rb"][i] is None else f"{D['rb'][i]:.6g}"
        warm = "" if i >= WARMUP else "  (прогрев)"
        lines.append(f"{t}  {D['c'][i]:<15.8g}  {line:<15}  {D['heat'][i]:.3f}{warm}")
    return "\n".join(lines)


def main(argv=None, fetch=None, out=print):
    fetch = fetch or bt.fetch_klines
    ap = argparse.ArgumentParser(description="MoneyForesight — сканер Rainbow по монетам и таймфреймам")
    ap.add_argument("symbols", nargs="*", help="по умолчанию SYMBOLS из mf_config.py")
    ap.add_argument("--tf", action="append", help="ограничить таймфреймами (можно повторять)")
    ap.add_argument("--refresh", action="store_true", help="принудительно перекачать свечи")
    ap.add_argument("--offline", action="store_true", help="использовать кэш как есть")
    ap.add_argument("--inspect", type=int, metavar="N",
                    help="показать последние N свечей одной монеты и одного таймфрейма")
    args = ap.parse_args(argv)

    known = dict(cfg.TIMEFRAMES)
    tfs = args.tf or [tf for tf, _ in cfg.TIMEFRAMES]
    for tf in tfs:
        if tf not in known:
            ap.error(f"неизвестный таймфрейм {tf}; в mf_config.py: {', '.join(known)}")
    symbols = [s.upper() for s in args.symbols] or list(cfg.SYMBOLS)
    kw = dict(refresh=args.refresh, offline=args.offline)

    if args.inspect:
        if len(symbols) != 1 or len(tfs) != 1:
            ap.error("--inspect работает с одной монетой и одним таймфреймом: --tf TF и один тикер")
        out(inspect(symbols[0], tfs[0], known[tfs[0]], args.inspect, fetch, **kw))
        return 0

    rows, errors = {}, {}
    for sym in symbols:
        rows[sym] = {}
        for tf in tfs:
            try:
                rows[sym][tf] = scan_cell(sym, tf, known[tf], fetch, cfg.FRESH_BARS.get(tf, 3), **kw)
            except Exception as e:  # одна монета или таймфрейм не должны ронять весь обход
                rows[sym][tf] = "ошибка"
                errors.setdefault(f"{type(e).__name__}: {e}", []).append(f"{sym} {tf}")

    out("Линия Rainbow")
    out(format_table(rows, symbols, tfs))
    out("🟥🟥 сильное падение · 🟧 падение · 🟨 флэт · 🟩 рост · 🟩🟩 сильный рост   "
        "↑↓ цена выше/ниже линии   * пробила линию   + цвет перешёл через 0.5")
    out("\nСигнал индикатора")
    out(format_table(rows, symbols, tfs, cell=signal_cell_text))
    out("LONG/SHORT N — состояние BUY/SELL и сколько свечей назад возникло   ! новый сигнал   "
        "r↑ r↓ свежий ретест зоны")

    found = [e for sym in symbols for tf in tfs for e in events(sym, tf, rows[sym][tf])]
    if found:
        out("\nСвежие пробития:")
    else:
        out("\nСвежих пробитий нет." if not errors else
            "\nСреди ячеек, которые удалось загрузить, свежих пробитий нет.")
    for e in found:
        out("  " + e)

    if errors:
        out(f"\nОшибки ({sum(len(v) for v in errors.values())} ячеек):")
        for msg, cells in errors.items():
            shown = ", ".join(cells[:4]) + (f" и ещё {len(cells) - 4}" if len(cells) > 4 else "")
            out(f"  {msg}\n    {shown}")
        if any(m.startswith(("URLError", "HTTPError", "TimeoutError")) for m in errors):
            out("  Если Binance недоступен, задайте MF_BINANCE_API=https://data-api.binance.vision")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
