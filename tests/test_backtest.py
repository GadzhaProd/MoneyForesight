#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты backtest.py — по одному на находку код-ревью 2026-09-08.

Только стандартная библиотека, синтетические свечи, без обращений к сети.
Запуск: python3 -m unittest discover -s tests -v
"""
import json, os, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest as bt


def kline(open_time, o, h, l, c, v=100.0, close_time=None):
    """Клайн Binance: [openTime, o, h, l, c, v, closeTime, ...]."""
    return [open_time, str(o), str(h), str(l), str(c), str(v),
            open_time + 59_999 if close_time is None else close_time,
            "0", 0, "0", "0", "0"]


def make_D(bars, pc=90.0, atr=1.0, re_high=None, re_low=95.0, adx=None):
    """Готовый набор индикаторов, где условия лонга выполнены на каждом баре.

    Позволяет проверять модель исполнения и состояние ре-входа точечно, не
    прогоняя 150 баров прогрева через prepare()."""
    n = len(bars)
    if re_high is None:
        re_high = [200.0] * n
    if adx is None:
        adx = [50.0] * n
    return dict(
        n=n,
        o=[b[0] for b in bars], h=[b[1] for b in bars],
        l=[b[2] for b in bars], c=[b[3] for b in bars],
        v=[100.0] * n, ts=[i * 60_000 for i in range(n)],
        atr22=[atr] * n, atr14=[atr] * n,
        csDir=[1] * n, pc=[pc] * n,
        dip=[30.0] * n, dim=[10.0] * n, adx=list(adx),
        macd=[1.0] * n, sig=[0.0] * n, heat=[0.6] * n,
        volSMA=[1.0] * n, reHigh=list(re_high), reLow=[re_low] * n,
        risingADX=[False] * n,
    )


# сценарий: вход по флипу на баре 1, исход на баре 2, пробой на баре 4
BARS_TP = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 116, 99, 110),
           (100, 101, 99, 110), (118, 121, 117, 120), (120, 121, 119, 120)]
BARS_SL = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 85, 95),
           (100, 101, 99, 110), (118, 121, 117, 120), (120, 121, 119, 120)]
RE_HIGH = [200.0, 200.0, 200.0, 105.0, 105.0, 105.0]
NO_BREAKOUT = [200.0] * 6
# ADX ниже порога на барах 2-3: исход сделки от него не зависит, но ретест-вход
# глушится — иначе он подменяет собой проверяемый ре-вход
QUIET_ADX = [50.0, 50.0, 10.0, 10.0, 50.0, 50.0]


def run_long(bars, **kw):
    """Прогон одного лонга: стоп на линии Chandelier (90), TP = 1.5R."""
    D = make_D(bars, re_high=kw.pop("re_high", RE_HIGH), adx=kw.pop("adx", QUIET_ADX))
    params = dict(adxThr=18, tpR=1.5, slmode="line", zoneW=0.25, flipExit=False, start=1)
    params.update(kw)
    return bt.run(D, **params)


class TestAdxParity(unittest.TestCase):
    """Находка №3: ta.rising сравнивает с максимумом, а не требует монотонности."""

    def test_non_monotonic_series_is_rising(self):
        self.assertTrue(bt.rising([17, 18, 15, 20], 3, 3))

    def test_current_below_earlier_peak_is_not_rising(self):
        self.assertFalse(bt.rising([17, 18, 15, 17.5], 3, 3))

    def test_strictly_monotonic_is_rising(self):
        self.assertTrue(bt.rising([10, 11, 12, 13], 3, 3))

    def test_na_in_window_is_not_rising(self):
        self.assertFalse(bt.rising([None, 18, 15, 20], 3, 3))


class TestIndicatorSeeding(unittest.TestCase):
    """Находка №14: Pine ta.tr на первом баре возвращает high - low."""

    def test_atr_warmup_starts_one_bar_earlier(self):
        # 14 одинаковых баров с размахом 20: TR каждого бара равен 20, поэтому
        # ATR(14) может быть определён на индексе 13 — но только если у первого
        # бара есть затравка high - low. Без неё значений 13 и ATR ещё na.
        kl = [kline(i * 60_000, 100, 110, 90, 100) for i in range(14)]
        D = bt.prepare(kl)
        self.assertIsNotNone(D["atr14"][13], "ATR должен стартовать с затравкой первого бара")
        self.assertAlmostEqual(D["atr14"][13], 20.0)


class TestGapExecution(unittest.TestCase):
    """Находка №16: цена стопа после гэпа неисполнима."""

    def test_gap_below_stop_fills_at_open(self):
        bars = list(BARS_SL)
        bars[2] = (80, 85, 78, 82)  # открытие ниже стопа 90
        r = run_long(bars, re_high=NO_BREAKOUT)
        self.assertEqual(r["trades"], 1)
        self.assertEqual(r["slc"], 1)
        # (80 - 100) / 10 = -2R минус комиссии, а не -1R по цене стопа
        self.assertLess(r["net"], -1.9)

    def test_stop_touched_intrabar_fills_at_stop(self):
        r = run_long(BARS_SL, re_high=NO_BREAKOUT)
        # (90 - 100) / 10 = -1R минус комиссии; без гэпа исполнение по уровню
        self.assertEqual(r["trades"], 1)
        self.assertEqual(r["slc"], 1)
        self.assertLess(r["net"], -1.0)
        self.assertGreater(r["net"], -1.1)


class TestReentryArming(unittest.TestCase):
    """Находка №4: ре-вход обещан «после выбитого стопа», а срабатывал после любого выхода."""

    def test_reentry_after_stop_out(self):
        r = run_long(BARS_SL)
        self.assertEqual(r["trades"], 2, "после стопа пробой должен дать повторный вход")
        self.assertEqual(r["slc"], 1)
        self.assertEqual(r["eod"], 1, "повторная сделка доживает до конца датасета")

    def test_no_reentry_after_take_profit(self):
        r = run_long(BARS_TP)
        self.assertEqual(r["trades"], 1, "после тейка ре-входа быть не должно")
        self.assertEqual(r["tp"], 1)

    def test_no_reentry_after_flip_exit(self):
        bars = list(BARS_TP)
        bars[2] = (100, 101, 99, 100)  # ни стоп, ни тейк не задеты
        D = make_D(bars, re_high=RE_HIGH, adx=QUIET_ADX)
        D["csDir"] = [1, 1, -1, 1, 1, 1]  # переворот на баре 2 -> выход по флипу
        r = bt.run(D, adxThr=18, tpR=1.5, slmode="line", zoneW=0.25, flipExit=True, start=1)
        self.assertEqual(r["fl"], 1)
        self.assertEqual(r["trades"], 1, "после флип-выхода ре-входа быть не должно")


class TestEntryQualityFilters(unittest.TestCase):
    """Новые фильтры должны отсекать слабые входы без будущих данных."""

    @staticmethod
    def _params(**overrides):
        params = dict(adxThr=20, tpR=2.0, slmode="zone", zoneW=0.25,
                      flipExit=True, start=1, tick=0.0)
        params.update(overrides)
        return params

    def test_retest_requires_directional_macd_confirmation(self):
        # На баре 1 есть бычий ретест зоны, но MACD ниже signal. Старая v3.1
        # входила, потому что ретест проверял только Chandelier, ADX и объём.
        bars = [(100, 101, 99, 100), (100, 101, 90, 100), (100, 101, 99, 100)]
        D = make_D(bars, pc=90.0, atr=1.0, re_high=NO_BREAKOUT)
        D["macd"] = [-1.0] * len(bars)
        D["sig"] = [0.0] * len(bars)

        legacy = bt.run(D, **self._params(min_di_spread=0.0,
                                          require_retest_macd=False))
        filtered = bt.run(D, **self._params(min_di_spread=0.0,
                                            require_retest_macd=True))

        self.assertEqual(legacy["trades"], 1, "оригинальный ретест должен воспроизводиться")
        self.assertIsNone(filtered, "ретест против MACD должен быть заблокирован")

    def test_minimum_di_spread_blocks_weak_continuation_entry(self):
        # Первый сильный сигнал должен сохраниться. После его TP появляется ретест,
        # где DI+ всё ещё выше DI-, но преимущество всего 2 пункта: это слабое
        # продолжение, которое новый фильтр должен убрать.
        bars = [(100, 101, 99, 100), (100, 101, 99, 100),
                (100, 112, 99, 100), (100, 101, 90, 100),
                (100, 101, 99, 100)]
        D = make_D(bars, pc=90.0, atr=1.0, re_high=NO_BREAKOUT)
        D["dip"] = [30.0, 30.0, 30.0, 12.0, 12.0]
        D["dim"] = [10.0] * len(bars)

        legacy = bt.run(D, **self._params(tpR=1.0, min_di_spread=0.0,
                                          require_retest_macd=False))
        filtered = bt.run(D, **self._params(tpR=1.0, min_di_spread=5.0,
                                            require_retest_macd=False))

        self.assertEqual(legacy["trades"], 2)
        self.assertEqual(filtered["trades"], 1,
                         "продолжение с |DI+ - DI-| < 5 должно быть заблокировано")

    def test_retest_cooldown_can_block_a_nearby_repeat(self):
        bars = [(100, 101, 99, 100) for _ in range(12)]
        bars[1] = (100, 101, 90, 100)   # первый ретест
        bars[2] = (100, 112, 99, 111)   # TP первой сделки
        bars[8] = (100, 101, 90, 100)   # повтор через 7 баров
        bars[9] = (100, 112, 99, 111)   # TP второй сделки
        D = make_D(bars, pc=90.0, atr=1.0, re_high=NO_BREAKOUT)
        D["macd"] = [-1.0] * len(bars)
        D["sig"] = [0.0] * len(bars)

        cooldown_5 = bt.run(D, **self._params(
            tpR=1.0, min_di_spread=0.0, require_retest_macd=False,
            retest_cooldown=5))
        cooldown_10 = bt.run(D, **self._params(
            tpR=1.0, min_di_spread=0.0, require_retest_macd=False,
            retest_cooldown=10))

        self.assertEqual(cooldown_5["trades"], 2)
        self.assertEqual(cooldown_10["trades"], 1)

    def test_filter_thresholds_reject_negative_values(self):
        bars = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100)]
        D = make_D(bars)

        with self.assertRaises(ValueError):
            bt.run(D, **self._params(min_di_spread=-1.0))
        with self.assertRaises(ValueError):
            bt.run(D, **self._params(retest_cooldown=-1))

    def test_directional_metrics_are_reported_separately(self):
        r = run_long(BARS_TP, min_di_spread=5.0, require_retest_macd=True)

        self.assertEqual(r["longTrades"], 1)
        self.assertEqual(r["longWins"], 1)
        self.assertEqual(r["longWR"], 100.0)
        self.assertTrue(r["longPf"] > 1.0)
        self.assertLessEqual(r["longDdMtM"], 0.0)
        self.assertEqual(r["shortTrades"], 0)
        self.assertEqual(r["shortWins"], 0)
        self.assertEqual(r["shortWR"], 0.0)
        self.assertEqual(r["shortDdMtM"], 0.0)


class TestPineFilterParity(unittest.TestCase):
    """Индикатор, Pine-стратегия и Python должны использовать один фильтр."""

    @staticmethod
    def _source(name):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, name)) as f:
            return f.read()

    def test_indicator_contains_di_and_retest_macd_filters(self):
        src = self._source("MoneyForesight_v3.pine")

        self.assertIn("minDiSpread", src)
        self.assertIn("retestNeedsMacd", src)
        self.assertIn("diSpreadOK = math.abs(diplus - diminus) >= minDiSpread", src)
        self.assertNotRegex(src, r"longcheck\s*=.*diSpreadOK")
        self.assertNotRegex(src, r"shortcheck\s*=.*diSpreadOK")
        self.assertRegex(src, r"retestLongRaw\s*=.*macdLine > signalLine.*diSpreadOK")
        self.assertRegex(src, r"retestShortRaw\s*=.*signalLine > macdLine.*diSpreadOK")
        self.assertRegex(src, r"reentryLongRaw\s*=.*diSpreadOK")
        self.assertRegex(src, r"reentryShortRaw\s*=.*diSpreadOK")
        # lookahead_on безопасен только вместе со сдвигом на закрытые HTF-бары
        self.assertIn("lookahead = barmerge.lookahead_on", src)
        self.assertIn("high[x + 1]", src)
        self.assertIn("low[x + 1]", src)
        self.assertNotRegex(src, r"matrix\.set\(hlm, x, \d, (high|low)\[x\]\)")

    def test_strategy_contains_di_and_retest_macd_filters(self):
        src = self._source("MoneyForesight_strategy.pine")

        self.assertIn("minDiSpread", src)
        self.assertIn("retestNeedsMacd", src)
        self.assertIn("diSpreadOK = math.abs(diplus - diminus) >= minDiSpread", src)
        self.assertNotRegex(src, r"longcheck\s*=.*diSpreadOK")
        self.assertNotRegex(src, r"shortcheck\s*=.*diSpreadOK")
        self.assertRegex(src, r"retestLongRaw\s*=.*macdLine > signalLine.*diSpreadOK")
        self.assertRegex(src, r"retestShortRaw\s*=.*signalLine > macdLine.*diSpreadOK")
        self.assertRegex(src, r"reentryLongRaw\s*=.*diSpreadOK")
        self.assertRegex(src, r"reentryShortRaw\s*=.*diSpreadOK")


class TestDrawdown(unittest.TestCase):
    """Находка №6: просадка только по закрытым сделкам прячет нереализованный минус."""

    def test_mark_to_market_captures_unrealized_dip(self):
        bars = list(BARS_TP)
        bars[2] = (100, 101, 91, 100)   # глубокий минус, стоп 90 не задет
        bars[3] = (100, 116, 99, 110)   # тейк отработал позже
        r = run_long(bars)
        self.assertEqual(r["tp"], 1)
        self.assertEqual(r["dd"], 0.0, "по закрытым сделкам просадки нет — сделка прибыльная")
        self.assertLess(r["ddMtM"], -0.5, "по рынку позиция проседала почти на 1R")

    def test_entry_bar_marked_at_close_not_at_low(self):
        # бар входа: минимум мог случиться ДО открытия позиции по закрытию бара,
        # поэтому он не должен попадать в просадку
        bars = list(BARS_TP)
        bars[1] = (100, 101, 50, 100)   # глубокий минимум на баре входа
        r = run_long(bars, re_high=NO_BREAKOUT)
        self.assertGreater(r["ddMtM"], -0.5, "минимум бара входа не должен считаться просадкой")

    def test_open_position_closed_at_dataset_end(self):
        r = run_long(BARS_SL)
        self.assertEqual(r["eod"], 1)
        self.assertEqual(r["trades"], r["tp"] + r["slc"] + r["fl"] + r["eod"])


class TestSlippage(unittest.TestCase):
    """Находка №15: Pine закладывает 1 тик, Python исполнял идеально."""

    def test_slippage_worsens_realized_result(self):
        clean = run_long(BARS_TP, tick=0.0)
        slipped = run_long(BARS_TP, tick=1.0, slippage_ticks=1)
        self.assertLess(slipped["net"], clean["net"])

    def test_planned_risk_denominator_unchanged(self):
        # риск нормировки остаётся плановым (close - sl = 10), поэтому 1 тик
        # на сторону стоит ровно 0.2R на круг
        clean = run_long(BARS_TP, tick=0.0)
        slipped = run_long(BARS_TP, tick=1.0, slippage_ticks=1)
        self.assertAlmostEqual(clean["net"] - slipped["net"], 0.2, places=2)


class TestUnclosedCandle(unittest.TestCase):
    """Находка №2: Binance отдаёт текущую формирующуюся свечу."""

    def test_forming_candle_dropped(self):
        now = 300_000
        kl = [kline(0, 1, 2, 0.5, 1, close_time=59_999),
              kline(60_000, 1, 2, 0.5, 1, close_time=119_999),
              kline(240_000, 1, 2, 0.5, 1, close_time=359_999)]   # ещё формируется
        self.assertEqual(len(bt.drop_unclosed(kl, now)), 2)

    def test_closed_candles_kept(self):
        kl = [kline(0, 1, 2, 0.5, 1, close_time=59_999)]
        self.assertEqual(bt.drop_unclosed(kl, 10**12), kl)


class TestCache(unittest.TestCase):
    """Находка №1: кэш возвращался вечно, новые свечи не попадали в прогон."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cache = bt.CACHE
        bt.CACHE = self.tmp

    def tearDown(self):
        bt.CACHE = self._cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cache(self, klines, symbol="TESTUSDT", interval="1m", bars=2):
        fn = bt.cache_path(symbol, interval, bars)
        with open(fn, "w") as f:
            json.dump({"schema": bt.CACHE_SCHEMA, "symbol": symbol, "interval": interval,
                       "bars": bars, "fetched_at_ms": 0, "klines": klines}, f)
        return fn

    def test_interval_ms(self):
        self.assertEqual(bt.interval_ms("5m"), 300_000)
        self.assertEqual(bt.interval_ms("4h"), 14_400_000)
        self.assertEqual(bt.interval_ms("1d"), 86_400_000)

    def test_cache_with_new_candles_available_is_stale(self):
        payload = {"klines": [kline(0, 1, 2, 0.5, 1, close_time=59_999)]}
        self.assertTrue(bt.cache_is_stale(payload, "1m", 300_000))
        self.assertFalse(bt.cache_is_stale(payload, "1m", 100_000))

    def test_legacy_bare_list_cache_is_rejected(self):
        fn = bt.cache_path("TESTUSDT", "1m", 2)
        with open(fn, "w") as f:
            json.dump([kline(0, 1, 2, 0.5, 1)], f)
        self.assertIsNone(bt.load_cache(fn), "старый формат должен считаться протухшим")

    def test_offline_uses_snapshot_without_staleness_check(self):
        kl = [kline(0, 1, 2, 0.5, 1, close_time=59_999)]
        self._write_cache(kl)
        self.assertEqual(bt.fetch_klines("TESTUSDT", "1m", 2, offline=True, now_ms=10**12), kl)

    def test_offline_without_snapshot_raises(self):
        with self.assertRaises(RuntimeError):
            bt.fetch_klines("TESTUSDT", "1m", 2, offline=True, now_ms=10**12)


class TestHistoryValidation(unittest.TestCase):
    """Находка №17: короткая история роняла прогон с IndexError."""

    def test_short_history_skips_timeframe(self):
        short = [kline(i * 60_000, 100, 101, 99, 100) for i in range(100)]
        result = bt.prepare_tf("TESTUSDT", "1m", 100, fetch=lambda *a, **k: short)
        self.assertIsNone(result)

    def test_sufficient_history_returns_period(self):
        enough = [kline(i * 60_000, 100, 101 + (i % 5), 99 - (i % 5), 100 + (i % 3))
                  for i in range(200)]
        D, d0, d1 = bt.prepare_tf("TESTUSDT", "1m", 200, fetch=lambda *a, **k: enough)
        self.assertEqual(D["n"], 200)
        self.assertRegex(d0, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(d1, r"^\d{4}-\d{2}-\d{2}$")


class TestResultsCsv(unittest.TestCase):
    """Находка №18: пустая сетка роняла создание CSV на results[0]."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_results_write_header_only(self):
        path = os.path.join(self.tmp, "results.csv")
        bt.write_results([], path)
        with open(path) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines, [",".join(bt.FIELDS)])

    def test_rows_follow_explicit_schema(self):
        path = os.path.join(self.tmp, "results.csv")
        row = {k: 0 for k in bt.FIELDS}
        bt.write_results([row], path)
        with open(path) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(bt.FIELDS))
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
