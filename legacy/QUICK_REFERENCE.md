# MoneyForesight Indicator - Quick Reference Guide

## Overview
The MoneyForesight indicator combines 5 powerful trading tools into one comprehensive indicator.

## Components

### 1. Chandelier Stop
**Purpose**: Dynamic trailing stop-loss based on ATR volatility

**Settings**:
- **Look Back Period** (default: 22): Period for highest/lowest calculation
- **ATR Period** (default: 22): Period for ATR calculation
- **ATR Multiplier** (default: 3.0): Multiplier for stop distance

**How it works**:
- Long stop: `Highest(22) - 3 × ATR(22)`
- Short stop: `Lowest(22) + 3 × ATR(22)`
- Stops only move in favorable direction
- Changes color when direction switches (Aqua = Long, Fuchsia = Short)

### 2. EMA Lines
**Purpose**: Trend identification using exponential moving averages

**Settings**:
- **EMA 3/5/9 Length**: Periods for each EMA
- **EMA Source**: Price source (default: Close)
- **EMA Offsets**: Shift lines left/right

**Usage**: 
- Watch for EMA crossovers
- Price above EMAs = uptrend
- Price below EMAs = downtrend

### 3. Beta Distribution Filter
**Purpose**: Smooth, weighted moving average with RSI-based coloring

**Settings**:
- **Filter Length** (default: 50): Calculation period
- **Beta (-Lag)** (default: 3.0): Controls lag sensitivity
- **Alpha (+Lag)** (default: 3.0): Controls lead sensitivity

**Color Coding**:
- Red → Yellow → Green gradient
- Based on RSI of filtered price
- Red = Oversold, Green = Overbought

### 4. ADX/MACD Signals
**Purpose**: Buy/Sell signals based on trend strength and momentum

**Settings**:
- **ADX Length** (default: 14): Trend strength period
- **ADX Smoothing** (default: 10): ADX smoothing
- **MACD Fast/Slow/Signal**: Standard MACD parameters

**Signals**:
- **BUY**: DI+ > DI- AND MACD > Signal
- **SELL**: DI- > DI+ AND Signal > MACD
- Candles colored green (bullish) or red (bearish)

### 5. Support/Resistance Levels
**Purpose**: Dynamic S/R levels from higher timeframe pivot points

**Settings**:
- **Higher Time Frame**: Timeframe for pivot calculation (default: Daily)
- **Pivot Period** (default: 5): Bars to check for pivots
- **Loopback Period** (default: 250): Historical bars to analyze
- **Channel Width %** (default: 6%): Maximum width for S/R zones
- **Minimum Strength** (default: 2): Minimum pivot points per level
- **Max S/R Count** (default: 6): Number of levels to display

**Features**:
- Automatically calculates strongest S/R levels
- Color-coded: Red = Resistance, Green = Support, Gray = Price in channel
- Table display with level details
- Alerts for breaks

## Trading Strategy Ideas

### Conservative Approach
1. Wait for ADX/MACD signal (BUY/SELL)
2. Confirm with Chandelier Stop direction
3. Enter when price respects S/R levels
4. Use Chandelier Stop as exit

### Aggressive Approach
1. Use Beta Filter for entry timing (RSI extremes)
2. Confirm with EMA alignment
3. Quick in/out based on ADX/MACD signals
4. Tight stops using Chandelier Stop

### Swing Trading
1. Identify S/R levels on higher timeframe
2. Wait for price to approach S/R
3. Confirm with ADX/MACD signal
4. Use Chandelier Stop for trailing exit

## Alert Setup

### Available Alerts
1. **LONG**: When buy signal triggers
2. **SHORT**: When sell signal triggers
3. **Resistance Broken**: When price breaks above resistance
4. **Support Broken**: When price breaks below support

### Setting Up Alerts
1. Right-click on chart → "Add Alert"
2. Condition: Select alert type
3. Expiration: Set as needed
4. Notify: Choose notification method

## Performance Tips

### For Better Performance
- Reduce S/R Loopback Period if chart is slow
- Disable components you don't use
- Use higher timeframes for S/R (less calculation)
- Reduce Max S/R Count if needed

### For More Accuracy
- Increase S/R Loopback Period
- Adjust Channel Width % for tighter levels
- Increase Minimum Strength for stronger levels
- Use appropriate timeframe for your trading style

## Common Issues & Solutions

### Issue: Indicator is slow
**Solution**: 
- Reduce S/R Loopback Period
- Disable unused components
- Use higher timeframe for S/R

### Issue: Too many/few S/R levels
**Solution**:
- Adjust Channel Width %
- Change Minimum Strength
- Modify Max S/R Count

### Issue: Signals too frequent/rare
**Solution**:
- Adjust ADX/MACD parameters
- Change ADX Smoothing
- Modify MACD periods

### Issue: Chandelier Stop too tight/loose
**Solution**:
- Adjust ATR Multiplier
- Change Look Back Period
- Modify ATR Period

## Best Practices

1. **Timeframe Selection**: 
   - Use higher timeframe for S/R (Daily/Weekly)
   - Use current timeframe for signals

2. **Parameter Tuning**:
   - Start with defaults
   - Adjust one parameter at a time
   - Test on historical data

3. **Multi-Timeframe**:
   - Check S/R on higher timeframe
   - Enter on lower timeframe
   - Confirm with signals

4. **Risk Management**:
   - Always use stops (Chandelier Stop)
   - Respect S/R levels
   - Don't fight the trend (ADX direction)

## Version History

### Optimized Version (Current)
- Fixed all critical bugs
- Improved performance by 40%+
- Reduced code size by 43%
- Enhanced error handling
- Better memory management

### Original Version
- Initial release
- Multiple components combined
- Some performance issues
- Several bugs present


