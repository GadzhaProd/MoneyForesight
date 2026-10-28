#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты mf_core.signal_series / signal_state — порт блока сигналов индикатора v3.3.

Только стандартная библиотека, синтетические данные, без сети.
Запуск: python3 -m unittest discover -s tests -v
"""
import os, re, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mf_core as core

N = 260


def neutral_D(n=N):
    """Бар, на котором не выполняется ни один сигнал. Тесты включают условия точечно."""
    return dict(
        n=n, c=[100.0] * n, h=[100.0] * n, l=[100.0] * n, v=[100.0] * n, ts=list(range(n)),
        csDir=[0] * n, pc=[100.0] * n, atr22=[1.0] * n,
        dip=[20.0] * n, dim=[20.0] * n, adx=[10.0] * n, risingADX=[False] * n,
        macd=[0.0] * n, sig=[0.0] * n, heat=[0.5] * n, volSMA=[100.0] * n,
    )


def set_bar(D, i, **kw):
    for k, v in kw.items():
        D[k][i] = v


def long_bar(D, i, **override):
    vals = dict(csDir=1, heat=0.6, dip=30.0, dim=10.0, macd=1.0, sig=0.0, adx=25.0, v=200.0)
    vals.update(override)
    set_bar(D, i, **vals)


def short_bar(D, i, **override):
    vals = dict(csDir=-1, heat=0.4, dip=10.0, dim=30.0, macd=0.0, sig=1.0, adx=25.0, v=200.0)
    vals.update(override)
    set_bar(D, i, **vals)


class TestTradeState(unittest.TestCase):
    def test_flip_happens_once_and_state_persists(self):
        D = neutral_D()
        long_bar(D, 200)                         # дальше бары нейтральные: состояние сохраняется
        short_bar(D, 220)
        s = core.signal_series(D)
        self.assertEqual([i for i, f in enumerate(s["flip"]) if f], [200, 220])
        self.assertEqual((s["flip"][200], s["flip"][220]), (1, -1))
        self.assertEqual((s["trade"][199], s["trade"][200], s["trade"][219], s["trade"][220], s["trade"][259]),
                         (0, 1, 1, -1, -1))

    def test_repeated_long_conditions_do_not_flip_again(self):
        D = neutral_D()
        for i in range(200, 206):
            long_bar(D, i)
        s = core.signal_series(D)
        self.assertEqual([i for i, f in enumerate(s["flip"]) if f], [200])

    def test_each_long_condition_is_required(self):
        blockers = {
            "ADX ниже порога": dict(adx=15.0),
            "ADX на половине порога, но не растёт": dict(adx=12.0),
            "ADX ниже половины порога даже если растёт": dict(adx=8.0, _rising=True),
            "объём не выше средней × 1.2": dict(v=100.0),
            "тепло ровно 0.5": dict(heat=0.5),
            "Chandelier не бычий": dict(csDir=0),
            "MACD ниже сигнальной": dict(macd=-1.0),
            "DI+ не выше DI-": dict(dip=10.0, dim=30.0),
        }
        for name, change in blockers.items():
            with self.subTest(name):
                D = neutral_D()
                rising = change.pop("_rising", False)
                long_bar(D, 200, **change)
                D["risingADX"][200] = rising
                self.assertEqual(sum(1 for f in core.signal_series(D)["flip"] if f), 0)

    def test_rising_adx_above_half_threshold_counts_as_trend(self):
        D = neutral_D()
        long_bar(D, 200, adx=12.0)
        D["risingADX"][200] = True
        s = core.signal_series(D)
        self.assertEqual(s["flip"][200], 1)
        self.assertTrue(s["adx_ok"][200])

    def test_rising_rule_can_be_turned_off(self):
        D = neutral_D()
        long_bar(D, 200, adx=12.0)
        D["risingADX"][200] = True
        self.assertEqual(core.signal_series(D, use_rising=False)["flip"][200], 0)

    def test_missing_indicator_values_block_the_signal_without_crashing(self):
        for key in ("dip", "dim", "adx", "macd", "sig"):
            with self.subTest(key):
                D = neutral_D()
                long_bar(D, 200)
                D[key][200] = None
                self.assertEqual(core.signal_series(D)["flip"][200], 0)

    def test_missing_volume_average_does_not_block(self):
        # как в Pine: na-средняя объёма пропускается (volOK = na(volSMA) or ...)
        D = neutral_D()
        long_bar(D, 200, v=1.0)
        D["volSMA"][200] = None
        self.assertEqual(core.signal_series(D)["flip"][200], 1)

    def test_params_are_validated(self):
        D = neutral_D()
        with self.assertRaises(TypeError):
            core.signal_series(D, adx=18)
        with self.assertRaises(ValueError):
            core.signal_series(D, retest_cooldown=-1)
        with self.assertRaises(ValueError):
            core.signal_series(D, min_di_spread=-1)


def retest_long_bar(D, i, low=100.2, close=101.0):
    long_bar(D, i, heat=0.5)                     # ретесту тепло не нужно
    set_bar(D, i, c=close, l=low, h=close)


class TestRetest(unittest.TestCase):
    def _trend_D(self):
        D = neutral_D()
        for i in range(198, 230):
            long_bar(D, i, heat=0.5)             # бычий Chandelier, MACD вверх, ADX и объём в порядке
            set_bar(D, i, c=100.0, l=100.0, h=100.0)
        return D

    def test_same_bar_bounce_off_zone(self):
        D = self._trend_D()
        retest_long_bar(D, 205)
        s = core.signal_series(D)
        self.assertEqual([i for i, r in enumerate(s["retest"]) if r], [205])
        self.assertEqual(s["retest"][205], 1)
        self.assertEqual(sum(1 for f in s["flip"] if f), 0, "ретест не зависит от состояния BUY/SELL")

    def test_reclaim_after_close_inside_zone(self):
        D = self._trend_D()
        set_bar(D, 214, c=100.0)                  # прошлая свеча закрылась внутри зоны (<= 100.25)
        retest_long_bar(D, 215, low=100.5)        # тень зону не задела, но закрытие вернулось за неё
        self.assertEqual(core.signal_series(D)["retest"][215], 1)

    def test_no_retest_without_pullback_into_zone(self):
        D = self._trend_D()
        set_bar(D, 204, c=101.0, l=100.6, h=101.0)
        retest_long_bar(D, 205, low=100.6)
        self.assertEqual(core.signal_series(D)["retest"][205], 0)

    def test_close_inside_zone_is_not_a_retest(self):
        D = self._trend_D()
        retest_long_bar(D, 205, close=100.1)      # закрытие не вышло за внешний край зоны
        self.assertEqual(core.signal_series(D)["retest"][205], 0)

    def test_cooldown_blocks_a_nearby_repeat_but_not_a_later_one(self):
        D = self._trend_D()
        for i in (205, 210, 211):
            retest_long_bar(D, i)
        s = core.signal_series(D)                 # кулдаун 5: разрыв ровно 5 блокируется, 6 проходит
        self.assertEqual([i for i, r in enumerate(s["retest"]) if r], [205, 211])
        s0 = core.signal_series(D, retest_cooldown=0)
        self.assertEqual([i for i, r in enumerate(s0["retest"]) if r], [205, 210, 211])

    def test_retest_against_macd_is_blocked_unless_allowed(self):
        D = self._trend_D()
        retest_long_bar(D, 205)
        set_bar(D, 205, macd=-1.0, sig=0.0)
        self.assertEqual(core.signal_series(D)["retest"][205], 0)
        self.assertEqual(core.signal_series(D, retest_macd=False)["retest"][205], 1)

    def test_weak_di_spread_blocks_retest_unless_threshold_is_zero(self):
        D = self._trend_D()
        retest_long_bar(D, 205)
        set_bar(D, 205, dip=22.0, dim=20.0)
        self.assertEqual(core.signal_series(D)["retest"][205], 0)
        self.assertEqual(core.signal_series(D, min_di_spread=0)["retest"][205], 1)

    def test_weak_adx_or_volume_blocks_retest(self):
        for change in (dict(adx=15.0), dict(v=100.0)):
            with self.subTest(change):
                D = self._trend_D()
                retest_long_bar(D, 205)
                set_bar(D, 205, **change)
                self.assertEqual(core.signal_series(D)["retest"][205], 0)

    def test_first_bar_of_trend_is_not_a_retest(self):
        D = neutral_D()
        for i in range(205, 230):
            long_bar(D, i, heat=0.5)
            set_bar(D, i, c=100.0, l=100.0, h=100.0)
        D["csDir"][204] = 0                       # бар переворота: csDir[1] ещё не в ту же сторону
        retest_long_bar(D, 205)
        self.assertEqual(core.signal_series(D)["retest"][205], 0)

    def test_short_retest_mirrors_long(self):
        D = neutral_D()
        for i in range(198, 230):
            short_bar(D, i, heat=0.5)
        short_bar(D, 205, heat=0.5)
        set_bar(D, 205, c=99.0, h=99.8, l=99.0)   # зона снизу 99.75: тень задела, закрытие под зоной
        s = core.signal_series(D)
        self.assertEqual(s["retest"][205], -1)


class TestSignalState(unittest.TestCase):
    def test_none_before_warmup_and_out_of_range(self):
        D = neutral_D()
        self.assertIsNone(core.signal_state(D, core.WARMUP - 1))
        self.assertIsNone(core.signal_state(D, N))

    def test_fresh_flip_since_and_decay(self):
        D = neutral_D()
        long_bar(D, 200)
        s = core.signal_series(D)
        near = core.signal_state(D, 202, sig=s, fresh_bars=3)
        self.assertEqual((near["trade"], near["trade_since"], near["trade_fresh"]), (1, 2, True))
        edge = core.signal_state(D, 203, sig=s, fresh_bars=3)      # ровно fresh_bars назад — уже не свежее
        self.assertEqual((edge["trade_since"], edge["trade_fresh"]), (3, False))
        far = core.signal_state(D, 210, sig=s, fresh_bars=3)
        self.assertEqual((far["trade"], far["trade_since"], far["trade_fresh"]), (1, 10, False))

    def test_no_signal_yet(self):
        s = core.signal_state(neutral_D(), 220)
        self.assertEqual((s["trade"], s["trade_since"], s["trade_fresh"]), (0, None, False))
        self.assertEqual((s["retest_dir"], s["retest_since"], s["retest_fresh"]), (0, None, False))

    def test_state_reports_last_retest_direction(self):
        D = neutral_D()
        for i in range(198, 230):
            long_bar(D, i, heat=0.5)
            set_bar(D, i, c=100.0, l=100.0, h=100.0)
        retest_long_bar(D, 205)
        s = core.signal_state(D, 206)
        self.assertEqual((s["retest_dir"], s["retest_since"], s["retest_fresh"]), (1, 1, True))
        edge = core.signal_state(D, 208, fresh_bars=3)
        self.assertEqual((edge["retest_since"], edge["retest_fresh"]), (3, False))
        self.assertTrue(s["macd_up"])
        self.assertEqual(s["di_spread"], 20.0)


def trend(n, start, growth):
    def k(i, c):
        t = i * 14_400_000
        vol = "200" if i % 3 == 0 else "100"       # всплески объёма, иначе фильтр объёма глушит все сигналы
        return [t, str(c), str(c * 1.002), str(c * 0.998), str(c), vol, t + 14_399_999, "0", 0, "0", "0", "0"]
    return [k(i, start * growth ** i * (1 + 0.003 * ((i % 7) - 3) / 3)) for i in range(n)]


class TestOnPreparedData(unittest.TestCase):
    def test_invariants_on_real_pipeline(self):
        kl = trend(300, 100, 1.004) + trend(200, 100 * 1.004 ** 299, 0.99) + trend(200, 100 * 1.004 ** 299 * 0.99 ** 199, 1.006)
        D = core.prepare(kl)
        s = core.signal_series(D)
        self.assertGreater(sum(1 for f in s["flip"] if f), 0, "тест пустой: на данных нет ни одного сигнала")
        self.assertTrue(all(len(v) == D["n"] for v in s.values()))
        self.assertTrue(all(t in (-1, 0, 1) for t in s["trade"]))
        for i in range(D["n"]):
            if s["flip"][i]:
                self.assertEqual(s["flip"][i], s["trade"][i])
                self.assertNotEqual(s["trade"][i], s["trade"][i - 1] if i else 0)
            elif i:
                self.assertEqual(s["trade"][i], s["trade"][i - 1], "без flip состояние не меняется")
            if s["retest"][i]:
                self.assertEqual(s["retest"][i], D["csDir"][i])
                self.assertEqual(D["csDir"][i - 1], D["csDir"][i])

    def test_state_does_not_look_into_the_future(self):
        kl = trend(300, 100, 1.004) + trend(150, 100 * 1.004 ** 299, 0.99)
        for k in (260, 300, 340):
            with self.subTest(bar=k):
                full = core.signal_state(core.prepare(kl), k)
                cut = core.signal_state(core.prepare(kl[:k + 1]), k)
                self.assertEqual(full, cut)


class TestPineParity(unittest.TestCase):
    """Порт переносит именно эти строки индикатора: если они изменятся, порт надо перечитать."""

    def test_indicator_signal_formulas_are_the_ported_ones(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "MoneyForesight_v3.pine"), encoding="utf-8") as f:
            src = f.read()
        for line in (
            "longcheck  = diplus > diminus and macdLine > signalLine and (not useCsFilter or csDir > 0) and (not useRbFilter or rbHeat > 0.5) and (not useVolFilter or volOK) and (not useAdxFilter or adxOK)",
            "shortcheck = diminus > diplus and signalLine > macdLine and (not useCsFilter or csDir < 0) and (not useRbFilter or rbHeat < 0.5) and (not useVolFilter or volOK) and (not useAdxFilter or adxOK)",
            "trade := longcheck ? 1 : shortcheck ? -1 : trade",
            "longFlip  = trade == 1 and trade[1] != 1",
            "shortFlip = trade == -1 and trade[1] != -1",
            "volOK  = na(volume) or na(volSMA) or volume > volSMA * volMult",
            "adxOK = adxValue >= adxThreshold or (useAdxRising and adxValue >= adxThreshold * 0.5 and ta.rising(adxValue, 3))",
            "retestLong = retestLongRaw and bar_index - lastRetestLong > retestCooldown",
            "retestShort = retestShortRaw and bar_index - lastRetestShort > retestCooldown",
            "retestFiltersOK = (not useVolFilter or volOK) and (not useAdxFilter or adxOK)",
            "zoneTop = pc + zoneMult * csATR",
            "zoneBot = pc - zoneMult * csATR",
        ):
            with self.subTest(line=line[:48]):
                self.assertTrue(line in src, f"в Pine не найдена строка: {line}")

    def test_indicator_defaults_match_signal_defaults(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "MoneyForesight_v3.pine"), encoding="utf-8") as f:
            src = f.read()
        d = core.SIGNAL_DEFAULTS
        for name, key in (("adxThreshold", "adx_thr"), ("minDiSpread", "min_di_spread"),
                          ("retestCooldown", "retest_cooldown"), ("zoneMult", "zone_w"),
                          ("volMult", "vol_mult")):
            with self.subTest(name):
                m = re.search(rf"^{name}\s*=\s*input\.(?:int|float)\(\s*([0-9.]+)", src, re.M)
                self.assertIsNotNone(m, f"в Pine не найдена настройка {name}")
                self.assertEqual(float(m.group(1)), float(d[key]), f"умолчание {name} разошлось с портом")
        m = re.search(r"^useAdxRising\s*=\s*input\.bool\((true|false)", src, re.M)
        self.assertEqual(m.group(1) == "true", d["use_rising"])


if __name__ == "__main__":
    unittest.main()
