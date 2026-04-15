# XRP Trading Bot — Project Memory

## Architecture

```
bot.py          — main loop, runs every 15 min strategy check
config.py       — all tunable parameters (edit here first)
analysis.py     — fetch candles, compute RSI/EMA, entry signal logic
strategy.py     — position open/monitor/close, TP/SL logic, paper sim
risk_manager.py — daily loss/gain limits, drawdown, flash crash, pauses
kraken_client.py— ccxt wrapper for Kraken API (balance, orders, candles)
notifier.py     — Telegram alerts
trade_logger.py — CSV trade log (trades.csv)
```

## Deployment

- **Remote machine**: Oracle Cloud `ubuntu@192.18.133.114`
- **SSH key**: `.key/ssh-key-2026-04-04.key`
- **Service**: `xrp-bot.service` (systemd)
- **Repo on server**: `~/first_bot/`

### Standard deploy workflow
```bash
# 1. Edit locally, commit, push
git add xrp_bot/config.py && git commit -m "..." && git push origin master

# 2. Pull and restart on Oracle
ssh -i xrp_bot/.key/ssh-key-2026-04-04.key ubuntu@192.18.133.114 \
  "cd ~/first_bot && git pull && sudo systemctl restart xrp-bot"

# 3. Check logs
sudo journalctl -u xrp-bot -f
```

## Current Config (as of 2026-04-15)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `PAPER_TRADE` | true | never gone live yet |
| `RSI_OVERSOLD` | 50 | raised from 40 — was never triggering |
| `SUPPORT_TOLERANCE` | 1.2% | raised from 0.5% |
| `STRATEGY_INTERVAL_MIN` | 15 | lowered from 60 min |
| `MAX_DAILY_GAIN` | 3% | raised from 1% |
| `TRADE_RATIO` | 80% | of USD cash balance per trade |
| `TP1_PCT` | 0.82% | sell 50%, move SL to breakeven |
| `TP2_PCT` | 1.32% | sell remaining 50% |
| `SL_PCT` | 0.80% | full exit |

## Known Issues / Fixed Bugs

### `fetch_balance()` — FIXED 2026-04-15
`kraken_client.py` was computing `usd = xrp_free * price` instead of reading
the real USD/ZUSD cash balance from Kraken. This would have caused the bot to
attempt oversized buy orders on live trading. Now reads `USD` then falls back
to `ZUSD` from the Kraken balance response.

## Roadmap

1. **Now**: paper trade with new parameters, watch for signals via Telegram
2. **1-2 weeks**: review paper trade results, check TP/SL hit rates
3. **Go live**: sell some XRP or deposit USD to get meaningful cash balance, set `PAPER_TRADE=false` in `.env` on Oracle
4. **Later (Phase 3 only)**: consider adding ETH/SOL — but only after XRP is profitable live

## Live Trading Checklist (when ready)
- [ ] Paper trade shows consistent signals and reasonable TP/SL ratio
- [ ] Kraken USD cash balance is meaningful (e.g. $500+)
- [ ] SSH into Oracle: edit `.env` → set `PAPER_TRADE=false`
- [ ] Restart service and monitor first live trade closely
