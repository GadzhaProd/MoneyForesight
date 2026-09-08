# Code Review — 2026-09-08

## Review context

- **Reviewed commit:** `9181a8c1d23fa2d4439f4ce31bcd7c6866c822d2`
- **Project:** MoneyForesight
- **Review status:** Findings recorded; fixes have not been applied as part of this document.
- **Verification limitations:** The Python module passed syntax compilation. No automated tests or local Pine compiler were available. Legacy sources contain additional runtime and compilation failures.

## Summary

The review identified material data-freshness, calculation-parity, execution-model, and reporting defects that can change trading signals and overstate backtest quality.

### Findings by priority

| Priority | Count |
| --- | ---: |
| P1 | 6 |
| P2 | 13 |
| P3 | 4 |
| **Total** | **23** |

## P1 — High priority

### 1. Refresh cached market data before reruns

**Location:** `backtest.py:19-20`

For the same symbol, interval, and bar count, every later run returns the existing JSON indefinitely. The documented periodic reruns therefore never include new candles and can silently preserve obsolete registry results.

**Recommended resolution:** Add cache-expiry metadata, refresh and merge recent candles, or provide an explicit immutable-snapshot versus refresh mode.

### 2. Exclude the still-forming Binance candle

**Location:** `backtest.py:22-27`

Binance normally includes the current, incomplete candle in this response. Its high, low, close, and volume can therefore trigger or close trades before the values are final, after which the candle is frozen in the cache.

**Recommended resolution:** Filter using the kline close-time field or align `endTime` to the most recent fully closed interval.

### 3. Match Pine's ADX rising predicate

**Location:** `backtest.py:167-170`

The Python test requires four strictly increasing ADX values, whereas Pine's `ta.rising(adxValue, 3)` requires the current value to exceed the previous three values without requiring those earlier values to be ordered. For example, `17, 18, 15, 20` passes Pine but fails in Python, producing different entries and invalidating the claimed strategy parity.

**Recommended resolution:** Implement the rolling-maximum predicate in Python or use the same explicit monotonic predicate in Pine.

### 4. Arm re-entry only after a stop-out

**Locations:**

- `MoneyForesight_strategy.pine:130-133`
- `MoneyForesight_v3.pine:568-569`
- `backtest.py:229-230`

The predicate requires only a flat position and a breakout. The feature described as re-entry after a stop-out therefore also enters after a take-profit, flip exit, or any other flat period.

**Recommended resolution:** Track the last exit reason and arm this entry path only after an actual stop loss.

### 5. Guard empty S/R pivot sets

**Locations:**

- `MoneyForesight_v3.pine:317-319`
- `MoneyForesight_v2.pine:265-267`

When the selected window contains no confirmed pivots, `pivotvals` is empty and `array.max()` or `array.min()` aborts the entire indicator. The matrix row-count check cannot prevent this because the matrix is preallocated.

**Recommended resolution:** Check `array.size(pivotvals)` and clear or hide S/R output when it is zero.

### 6. Track mark-to-market drawdown

**Location:** `backtest.py:252-254`

Drawdown is calculated only after closed trades. A position can therefore suffer a large unrealized loss and recover without affecting the reported maximum drawdown. A position still open at the dataset boundary is also omitted from all metrics. This can materially understate the registry's `Макс. DD` values.

**Recommended resolution:** Track bar-by-bar marked equity, including active positions and fees, or clearly report closed-trade drawdown and open exposure separately.

## P2 — Medium priority

### 7. Include point value in risk sizing

**Locations:**

- `MoneyForesight_strategy.pine:180-183`
- `MoneyForesight_strategy.pine:201`

On instruments where `syminfo.pointvalue != 1`, one contract loses `riskL * syminfo.pointvalue` at the stop. The current quantity therefore does not risk the advertised percentage of equity.

**Recommended resolution:** Divide the risk budget by price distance multiplied by point value. Apply the same correction to short quantity and optionally incorporate expected fees and slippage.

### 8. Reject zero-sum Rainbow weights

**Locations:**

- `MoneyForesight_v3.pine:179-184`
- `MoneyForesight_strategy.pine:91-97`
- `MoneyForesight_v2.pine:96-104`

The permitted setting `rbLength = 2` with the default alpha and beta of `3` produces weights `[0, 0]`, making `rbDen` zero and `filt` undefined. With the Rainbow filter enabled, neutral heat then suppresses every signal.

**Recommended resolution:** Require at least three samples for this configuration or detect a zero denominator and use a defined fallback.

### 9. Grade signals against the configured stop

**Location:** `MoneyForesight_v3.pine:441-444`

A/B grading always measures risk to `pc`, but the default virtual trade places its stop at `zoneBot` or `zoneTop`. This understates default risk by `zoneMult * ATR` and can label a trade A when its configured stop exceeds `gradeMaxRisk`.

**Recommended resolution:** Calculate the grade from the same stop-level expression used by the entry logic.

### 10. Separate retest visibility from retest generation

**Location:** `MoneyForesight_v3.pine:453-455`

Turning off `Show Retest Entries` makes both raw retest predicates false, thereby disabling retest alerts and virtual entries even when `Enter on Retests Too` remains enabled.

**Recommended resolution:** Compute retests independently and apply `showRetest` only to the corresponding `plotshape` calls.

### 11. Label ADX regimes consistently

**Location:** `MoneyForesight_v3.pine:709-714`

Episodes are classified using `adxOK`, which also accepts a rising ADX as low as half the threshold. The table nevertheless labels that group `ADX >= threshold` and labels the remainder `ADX < threshold`, placing some below-threshold observations in a row claiming the opposite.

**Recommended resolution:** Classify using the raw threshold comparison or rename the rows to describe the complete filter predicate.

### 12. Clear obsolete S/R table rows

**Locations:**

- `MoneyForesight_v3.pine:385-391`
- `MoneyForesight_v2.pine:330-347`

Only rows for currently discovered channels are overwritten. When a later recalculation produces fewer channels, cells for removed channels remain visible with obsolete prices and labels.

**Recommended resolution:** Clear the table at the beginning of each last-bar rebuild or explicitly blank every unused row.

### 13. Distinguish in-channel S/R rows

**Locations:**

- `MoneyForesight_v3.pine:392-394`
- `MoneyForesight_v2.pine:340-342`
- `legacy/MoneyForesight_Optimized.pine`

When price is inside a channel, `get_color()` returns `inch_col`, but the two-way branch treats every non-resistance color as Support and paints it green.

**Recommended resolution:** Use a three-way classification for resistance, support, and in-channel.

### 14. Seed true range from the first bar

**Location:** `backtest.py:103-106`

Pine's ATR uses the first bar's `high - low` when no previous close exists, whereas the Python implementation leaves `tr[0]` undefined. This shifts ATR initialization, and because Chandelier direction is stateful, it can cause persistent signal divergence.

**Recommended resolution:** Initialize the first true range and verify DMI initialization against Pine as well.

### 15. Model strategy slippage in Python

**Locations:**

- `backtest.py:215-218`
- `MoneyForesight_strategy.pine` strategy configuration

The Pine strategy configures one tick of slippage, while the independent backtester subtracts only commission and assumes ideal fills. Tight-stop and low-timeframe configurations therefore receive systematically better executions than the TradingView strategy.

**Recommended resolution:** Introduce tick-size and slippage inputs and apply equivalent assumptions to entries and exits.

### 16. Fill stop gaps at an executable price

**Location:** `backtest.py:205-213`

When a candle opens beyond the stored stop, the current branches still record an exit exactly at the stop even though that price was not executable after the gap, understating losses.

**Recommended resolution:** Check the candle open before intrabar high/low tests and fill a crossed stop at the open, or use a documented lower-timeframe execution model. Also document the chosen ordering for bars touching both stop and target.

### 17. Validate history length before indexing

**Location:** `backtest.py:275-278`

A newly listed symbol or partial API response can contain fewer than 151 candles, after which `D["ts"][150]` raises `IndexError` instead of producing a useful result.

**Recommended resolution:** Validate fetched history before preparation and date extraction, then skip or report timeframes that cannot satisfy the warm-up period.

### 18. Handle an empty result grid

**Location:** `backtest.py:292-294`

If every configuration returns `None` because no trade closes, `results[0]` raises `IndexError` while creating the CSV.

**Recommended resolution:** Check for an empty result set and emit a diagnostic or create the file using an explicit field schema.

### 19. Exclude the zone-return bar from favorable excursion

**Location:** `MoneyForesight_v3.pine:491-496`

The current bar's high or low is added to `excExtreme` before checking whether that same bar returned to the zone. On a bar that both sets a new favorable extreme and touches the zone, OHLC data cannot prove which happened first, so this ordering can overstate the reported average move after a bounce.

**Recommended resolution:** Detect termination before updating the extreme, or use lower-timeframe data and document the intrabar ordering assumption.

## P3 — Low priority and legacy maintenance

### 20. Reset legacy breakout flags each bar

**Location:** `legacy/MoneyForesight_Optimized.pine:286-287`

These flags are persistent `var` values and are never reset to false. After the first S/R break, their alert conditions therefore remain true on every subsequent bar.

**Recommended resolution:** Make them ordinary per-bar booleans or explicitly reset them before each breakout calculation.

### 21. Use swing order to choose Fibonacci direction

**Location:** `legacy/MoneyForesight_Optimized.pine:686-689`

The uptrend test `low_price < high_price` and the downtrend test `high_price > low_price` are algebraically identical. Both retracement sets are therefore evaluated for every swing pair regardless of which pivot occurred first.

**Recommended resolution:** Compare the corresponding swing bar indices and evaluate only the direction implied by their temporal order, as the visualization code already does.

### 22. Guard the legacy warm-up color index

**Location:** `legacy/MoneyForesight_Optimized.pine:115-117`

During filter and RSI warm-up, `os` and therefore `color_idx` can be `na`, but the code immediately passes that value to `array.get`, risking an invalid-index runtime failure.

**Recommended resolution:** Use `nz()` or defer the lookup until RSI is defined, matching the protection already present in v2 and v3.

### 23. Quarantine the non-compiling legacy source

**Location:** `legacy/MoneyForesight.pine`

The archived Pine file cannot compile because:

- `array.show` at line 263 is not a Pine array API;
- there is an unmatched function call at line 847;
- `srtate` is undefined at line 1107.

**Recommended resolution:** Mark or relocate the file as a deliberately non-buildable artifact, or fix all compile blockers before retaining it as project source.

## Suggested remediation order

1. Fix all P1 findings affecting data validity, Pine/Python parity, runtime stability, and drawdown reporting.
2. Add focused Python tests for indicator initialization, ADX parity, execution pricing, re-entry state, history validation, and empty results.
3. Resolve P2 calculation and presentation inconsistencies.
4. Compile and smoke-test the maintained Pine scripts in TradingView.
5. Decide whether legacy scripts are supported source or archival artifacts, then fix or quarantine them accordingly.

