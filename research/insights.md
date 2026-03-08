# Research Insights

Extracted knowledge organized by topic. Each insight references its source in `sources.md`.

## Market Microstructure

## Sizing & Kelly Criterion

## Calibration & Scoring

- **Brier score** = `sum((predicted_probability - actual_outcome)^2) / N` — perfect = 0.00, random = 0.25
- Win rate alone is misleading — calibration matters more. 70% win rate can be badly calibrated (predicting 90% but winning 60%)
- **Operational rule**: stop trading when Brier score starts rising — edge has disappeared. Check every ~50 trades. Walk away when score degrades (e.g., 0.12 → 0.19), even if profitable
- Track predicted probability vs actual outcomes, not just P&L
- Implementation idea: rolling Brier score over recent trades, auto-pause when threshold exceeded
- *Source: article on quant calibration practices*

## Bot Strategies & Automation

## Math & Probability

## Risk Management

## Prediction Market Theory
