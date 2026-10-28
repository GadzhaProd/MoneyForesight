#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты mf_core (состояние Rainbow) и scan.py (сканер пульта).

Только стандартная библиотека, синтетические свечи, без обращений к сети.
Запуск: python3 -m unittest discover -s tests -v
"""
import contextlib, io, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mf_core as core
import scan


def kline(i, c, step_ms=14_400_000):
    vol = "200" if i % 3 == 0 else "100"       # всплески объёма, иначе фильтр объёма глушит все сигналы
    return [i * step_ms, str(c), str(c * 1.002), str(c * 0.998), str(c), vol,
            i * step_ms + step_ms - 1, "0", 0, "0", "0", "0"]


def trend(n, start, growth):
    """Ровный тренд с лёгкой рябью, чтобы ATR и DMI не вырождались."""
    return [kline(i, start * growth ** i * (1 + 0.003 * ((i % 7) - 3) / 3)) for i in range(n)]


def state_D(n, c, rb, heat):
    """Минимальный набор рядов для rainbow_state; c, rb, heat — списки длины n."""
    return dict(n=n, c=c, rb=rb, heat=heat, ts=[i * 1000 for i in range(n)])


class TestHeatPalette(unittest.TestCase):
    def test_gradient_endpoints_match_pine(self):
        self.assertEqual(core.heat_color(0.0), "#ff1100")
        self.assertEqual(core.heat_color(0.5), "#ffab00")
        self.assertEqual(core.heat_color(1.0), "#30ff85")

    def test_out_of_range_heat_is_clamped(self):
        self.assertEqual(core.heat_color(-1), core.heat_color(0))
        self.assertEqual(core.heat_color(2), core.heat_color(1))

    def test_palette_constants_are_the_ones_the_indicator_uses(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "MoneyForesight_v3.pine"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("0.0, 0.5, #FF1100, #FFAB00", src)
        self.assertIn("0.5, 1.0, #FFAB00, #30FF85", src)

    def test_bucket_boundaries(self):
        score = lambda h: core.heat_bucket(h)[0]
        self.assertEqual([score(h) for h in (0.0, 0.29, 0.30, 0.44, 0.45, 0.54, 0.55, 0.69, 0.70, 1.0)],
                         [-2, -2, -1, -1, 0, 0, 1, 1, 2, 2])


class TestRainbowCross(unittest.TestCase):
    N = 260

    def _price_case(self, cross_at):
        c = [100.0] * self.N
        rb = [101.0 if j < cross_at else 99.0 for j in range(self.N)]   # цена ниже линии, потом выше
        return state_D(self.N, c, rb, [0.5] * self.N)

    def test_price_cross_counts_bars_since_it_happened(self):
        D = self._price_case(201)
        s = core.rainbow_state(D, 203)
        self.assertEqual((s["price_side"], s["price_cross_bars"]), (1, 2))
        self.assertTrue(s["price_fresh"])
        self.assertFalse(core.rainbow_state(D, 210)["price_fresh"])

    def test_freshness_boundary_is_exactly_fresh_bars(self):
        D = self._price_case(201)
        heat = [0.4 if j < 201 else 0.6 for j in range(self.N)]
        D["heat"] = heat
        inside = core.rainbow_state(D, 203, fresh_bars=3)          # 2 бара назад
        edge = core.rainbow_state(D, 204, fresh_bars=3)            # ровно 3 бара назад — уже не свежее
        self.assertEqual((inside["price_fresh"], inside["heat_fresh"]), (True, True))
        self.assertEqual((edge["price_cross_bars"], edge["price_fresh"], edge["heat_fresh"]),
                         (3, False, False))

    def test_cross_on_the_last_bar_is_zero_bars_ago(self):
        s = core.rainbow_state(self._price_case(203), 203)
        self.assertEqual(s["price_cross_bars"], 0)
        self.assertTrue(s["price_fresh"])

    def test_cross_in_other_direction(self):
        c = [100.0] * self.N
        rb = [99.0 if j < 201 else 101.0 for j in range(self.N)]       # цена выше линии, потом ниже
        s = core.rainbow_state(state_D(self.N, c, rb, [0.5] * self.N), 202)
        self.assertEqual((s["price_side"], s["price_cross_bars"]), (-1, 1))

    def test_no_cross_in_available_history(self):
        D = state_D(self.N, [100.0] * self.N, [99.0] * self.N, [0.5] * self.N)
        s = core.rainbow_state(D, 250)
        self.assertEqual(s["price_side"], 1)
        self.assertIsNone(s["price_cross_bars"])
        self.assertFalse(s["price_fresh"])

    def test_touching_the_line_does_not_reset_the_streak(self):
        rb = [101.0 if j < 201 else 99.0 for j in range(self.N)]
        rb[205] = 100.0                                                  # ровно на линии
        s = core.rainbow_state(state_D(self.N, [100.0] * self.N, rb, [0.5] * self.N), 210)
        self.assertEqual(s["price_cross_bars"], 9)

    def test_heat_cross_through_half(self):
        heat = [0.4 if j < 201 else 0.6 for j in range(self.N)]
        s = core.rainbow_state(state_D(self.N, [100.0] * self.N, [99.0] * self.N, heat), 202)
        self.assertEqual((s["heat_side"], s["heat_cross_bars"]), (1, 1))
        self.assertTrue(s["heat_fresh"])
        self.assertEqual(s["bucket"], 1)

    def test_price_and_heat_crosses_are_independent(self):
        heat = [0.4 if j < 220 else 0.6 for j in range(self.N)]         # цвет перешёл позже цены
        rb = [101.0 if j < 201 else 99.0 for j in range(self.N)]
        s = core.rainbow_state(state_D(self.N, [100.0] * self.N, rb, heat), 222)
        self.assertEqual((s["price_cross_bars"], s["heat_cross_bars"]), (21, 2))
        self.assertFalse(s["price_fresh"])
        self.assertTrue(s["heat_fresh"])

    def test_heat_delta_shows_cooling(self):
        heat = [0.8] * 200 + [0.8, 0.75, 0.7, 0.65] + [0.65] * 56
        s = core.rainbow_state(state_D(self.N, [100.0] * self.N, [99.0] * self.N, heat), 203)
        self.assertAlmostEqual(s["heat_delta"], -0.15)


class TestWarmup(unittest.TestCase):
    def test_none_before_warmup_and_when_line_is_undefined(self):
        n = 260
        D = state_D(n, [100.0] * n, [99.0] * n, [0.5] * n)
        self.assertIsNone(core.rainbow_state(D, core.WARMUP - 1))
        self.assertIsNotNone(core.rainbow_state(D, core.WARMUP))
        D["rb"][200] = None
        self.assertIsNone(core.rainbow_state(D, 200))

    def test_index_out_of_range_is_none(self):
        D = state_D(200, [100.0] * 200, [99.0] * 200, [0.5] * 200)
        self.assertIsNone(core.rainbow_state(D, 200))


class TestPrepareRainbow(unittest.TestCase):
    def test_prepare_exposes_rainbow_line(self):
        kl = trend(400, 100, 1.004)
        D = core.prepare(kl)
        self.assertEqual(len(D["rb"]), 400)
        self.assertTrue(all(v is None for v in D["rb"][:49]))
        self.assertIsNotNone(D["rb"][49])
        # независимый пересчёт по формуле бета-весов из Pine (вес текущей свечи = 0)
        L, i = 50, 300
        w = [(j / (L - 1.0)) ** 2 * (1 - j / (L - 1.0)) ** 2 for j in range(L)]
        want = sum(D["c"][i - j] * w[j] for j in range(L)) / sum(w)
        self.assertAlmostEqual(D["rb"][i], want, places=9)

    def test_line_lags_a_rising_price(self):
        D = core.prepare(trend(400, 100, 1.004))
        self.assertLess(D["rb"][399], D["c"][399])

    def test_uptrend_is_strong_up_and_price_above_line(self):
        D = core.prepare(trend(400, 100, 1.004))
        s = core.rainbow_state(D, D["n"] - 1)
        self.assertEqual((s["bucket"], s["price_side"], s["heat_side"]), (2, 1, 1))

    def test_downtrend_is_strong_down_and_price_below_line(self):
        D = core.prepare(trend(400, 1000, 0.996))
        s = core.rainbow_state(D, D["n"] - 1)
        self.assertEqual((s["bucket"], s["price_side"], s["heat_side"]), (-2, -1, -1))

    def test_state_does_not_look_into_the_future(self):
        # разворот тренда на 300-й свече; состояние на 300-й не должно зависеть от свечей после неё
        kl = trend(300, 100, 1.004) + trend(120, 100 * 1.004 ** 299, 0.99)
        k = 300
        full = core.rainbow_state(core.prepare(kl), k)
        cut = core.rainbow_state(core.prepare(kl[:k + 1]), k)
        self.assertEqual(full, cut)


class TestScanner(unittest.TestCase):
    @staticmethod
    def fake_fetch(symbol, tf, bars, **kw):
        if symbol == "BADUSDT":
            raise RuntimeError("boom")
        if symbol == "NEWUSDT":
            return trend(100, 100, 1.004)                     # мало истории на прогрев
        return trend(400, 100, 1.004) if symbol == "UPUSDT" else trend(400, 1000, 0.996)

    def run_main(self, *argv):
        lines = []
        code = scan.main(list(argv), fetch=self.fake_fetch, out=lines.append)
        return code, "\n".join(lines)

    def test_cell_texts(self):
        self.assertTrue(scan.cell_text(None).startswith("н/д"))
        self.assertTrue(scan.cell_text("ошибка").startswith("ошибка"))
        D = core.prepare(trend(400, 100, 1.004))
        cell = scan.cell_text(core.rainbow_state(D, D["n"] - 1))
        self.assertTrue(cell.startswith("🟩🟩 "))
        self.assertIn("↑", cell)

    def test_fresh_marks_and_events(self):
        D = state_D(260, [100.0] * 260, [101.0 if j < 257 else 99.0 for j in range(260)],
                    [0.4 if j < 258 else 0.6 for j in range(260)])
        s = core.rainbow_state(D, 259)
        cell = scan.cell_text(s)
        self.assertIn("*", cell)
        self.assertIn("+", cell)
        ev = scan.events("BTCUSDT", "1d", s)
        self.assertEqual(len(ev), 2)
        self.assertIn("вверх", ev[0])
        self.assertIn("зелёную", ev[1])

    def test_signal_cell_and_events(self):
        base = dict(trade=1, trade_since=2, trade_fresh=True, retest_dir=-1, retest_since=1,
                    retest_fresh=True)
        state = {"signal": base}
        self.assertEqual(scan.signal_cell_text(state).rstrip(), "LONG 2! r↓")
        quiet = {"signal": dict(base, trade=-1, trade_since=40, trade_fresh=False, retest_fresh=False)}
        self.assertEqual(scan.signal_cell_text(quiet).rstrip(), "SHORT 40")
        old = {"signal": dict(base, trade_since=None, trade_fresh=False, retest_fresh=False)}
        self.assertEqual(scan.signal_cell_text(old).rstrip(), "LONG …")
        none_yet = {"signal": dict(base, trade=0, trade_since=None, trade_fresh=False, retest_fresh=False)}
        self.assertEqual(scan.signal_cell_text(none_yet).rstrip(), "—")
        self.assertTrue(scan.signal_cell_text(None).startswith("н/д"))
        self.assertTrue(scan.signal_cell_text("ошибка").startswith("ошибка"))

        rb = dict(price_fresh=False, heat_fresh=False, signal=base)
        ev = scan.events("ETHUSDT", "4h", rb)
        self.assertEqual(len(ev), 2)
        self.assertIn("BUY", ev[0])
        self.assertIn("на последней" if base["trade_since"] == 0 else "2 св. назад", ev[0])
        self.assertIn("ретест", ev[1])
        self.assertIn("вниз", ev[1])

    def test_main_prints_both_tables(self):
        code, text = self.run_main("UPUSDT", "--tf", "1d")
        self.assertEqual(code, 0)
        self.assertIn("Линия Rainbow", text)
        self.assertIn("Сигнал индикатора", text)

    def test_table_and_flat_summary(self):
        code, text = self.run_main("UPUSDT", "DOWNUSDT", "--tf", "1d", "--tf", "4h")
        self.assertEqual(code, 0)
        self.assertIn("UP", text)
        self.assertIn("1D", text)
        self.assertIn("🟩🟩", text)
        self.assertIn("🟥🟥", text)
        self.assertNotIn("1W", text)

    def test_one_failing_coin_does_not_stop_the_scan(self):
        code, text = self.run_main("UPUSDT", "BADUSDT", "NEWUSDT", "--tf", "1d")
        self.assertEqual(code, 1)
        self.assertIn("🟩🟩", text)
        self.assertIn("ошибка", text)
        self.assertIn("RuntimeError: boom", text)
        self.assertIn("BADUSDT 1d", text)
        self.assertIn("н/д", text)

    def test_network_failure_suggests_alternative_host_and_does_not_claim_quiet_market(self):
        import urllib.error

        def down(*a, **k):
            raise urllib.error.URLError("blocked")
        lines = []
        code = scan.main(["BTCUSDT", "--tf", "1d"], fetch=down, out=lines.append)
        text = "\n".join(lines)
        self.assertEqual(code, 1)
        self.assertIn("MF_BINANCE_API", text)
        self.assertNotIn("Свежих пробитий нет.", text)

    def assert_rejected(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_main(*argv)

    def test_unknown_timeframe_is_rejected(self):
        self.assert_rejected("UPUSDT", "--tf", "7m")

    def test_inspect_prints_last_bars_with_line_and_heat(self):
        code, text = self.run_main("UPUSDT", "--tf", "4h", "--inspect", "3")
        self.assertEqual(code, 0)
        self.assertEqual(len(text.splitlines()), 2 + 3)
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_inspect_needs_exactly_one_coin_and_timeframe(self):
        self.assert_rejected("UPUSDT", "DOWNUSDT", "--tf", "4h", "--inspect", "3")
        self.assert_rejected("UPUSDT", "--inspect", "3")


if __name__ == "__main__":
    unittest.main()
