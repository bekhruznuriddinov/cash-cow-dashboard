#!/usr/bin/env python3
"""
Parses all trades.log* files and outputs data.json for the dashboard.
Run from the dashboard/ directory or anywhere — uses absolute path resolution.
"""
import re
import json
import glob
import os
import sys
from datetime import datetime, date

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_DIR     = os.path.dirname(SCRIPT_DIR)
LOG_PATTERN = os.path.join(LOG_DIR, "trades.log*")
OUT_FILE    = os.path.join(SCRIPT_DIR, "data.json")
BALANCE_HISTORY_FILE = os.path.join(SCRIPT_DIR, "balance_history.json")

LIVE_RESULTS = {"PROFIT", "STOP", "EXPIRE", "EXPIRE_ITM", "SKIP"}

sys.path.insert(0, LOG_DIR)

def _float(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default

def _int(s, default=0):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default

def parse_fields(fields_str):
    def get(pattern):
        m = re.search(pattern, fields_str)
        return m.group(1) if m else None

    pnl_raw = get(r'pnl=[+]?\$([-+]?[\d.]+)')
    credit_raw = get(r'credit=\$?([-+]?[\d.]+)')

    # If credit is negative the position was adopted mid-session and Robinhood's
    # avg_price was misread — approximate with minimum entry credit.
    FALLBACK_CREDIT = 0.04
    credit = _float(credit_raw)
    if credit <= 0:
        credit = FALLBACK_CREDIT

    return {
        "short_strike": _float(get(r'short=([\d.]+)')),
        "long_strike":  _float(get(r'long=([\d.]+)')),
        "delta":        _float(get(r'delta=([\d.]+)')),
        "credit":       credit,
        "contracts":    _int(get(r'contracts=(\d+)')),
        "pnl":          _float(pnl_raw),
        "buying_power": _float(get(r'eod_bp=\$([\d.]+)')),
        "hold_min":     _float(get(r'hold_min=([\d.]+)')),
        "direction":    get(r'dir=(\w+)') or "",
        "vix":          _float(get(r'vix=([\d.]+)')),
    }

def parse_logs():
    records = []
    vix_by_date = {}
    files = sorted(glob.glob(LOG_PATTERN))

    for filepath in files:
        current_date = None
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Date header ════════ YYYY-MM-DD ════════
            m = re.match(r'[══]+\s+(\d{4}-\d{2}-\d{2})\s+[══]+', line)
            if m:
                current_date = m.group(1)
                continue

            # PREMARKET line — extract VIX for this date
            if line.startswith("PREMARKET") and current_date:
                m = re.search(r'vix=([\d.]+)', line)
                if m:
                    vix_by_date[current_date] = _float(m.group(1))
                continue

            # New format: TRADE N | RESULT | time=HH:MM:SS | Trading Mode: MODE | ...
            m = re.match(
                r'TRADE\s+\d+\s*\|\s*(\w+)\s*\|\s*time=(\S+)\s*\|\s*Trading Mode:\s*(\w+)\s*\|(.+)',
                line
            )
            if m:
                result, time_str, mode, rest = m.groups()
                if mode == "LIVE" and result in LIVE_RESULTS:
                    fields = parse_fields(rest)
                    fields.update({"date": current_date, "time": time_str, "result": result})
                    records.append(fields)
                continue

            # Old format: YYYY-MM-DD | RESULT | Trading Mode: MODE | ...
            m = re.match(
                r'(\d{4}-\d{2}-\d{2})\s*\|\s*(\w+)\s*\|\s*Trading Mode:\s*(\w+)\s*\|(.+)',
                line
            )
            if m:
                date_str, result, mode, rest = m.groups()
                if mode == "LIVE" and result in LIVE_RESULTS:
                    fields = parse_fields(rest)
                    fields.update({"date": date_str, "time": None, "result": result})
                    records.append(fields)

    # Stamp per-date VIX onto each trade record
    for r in records:
        if r.get("vix", 0.0) == 0.0 and r.get("date") in vix_by_date:
            r["vix"] = vix_by_date[r["date"]]

    return records


def _load_balance_history():
    """Load existing balance history from disk."""
    if not os.path.exists(BALANCE_HISTORY_FILE):
        return []
    try:
        with open(BALANCE_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_balance_history(history):
    """Save balance history to disk."""
    with open(BALANCE_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


AGENT_START_DATE = "2026-06-23"

def _fetch_rh_balance():
    """Log in to Robinhood and fetch real account balance + net deposits since agent start."""
    try:
        from auth import login, logout
        import robin_stocks.robinhood as rh
        from config import RH_ACCOUNT_NUMBER

        login()

        portfolio = rh.profiles.load_portfolio_profile()
        equity = float(portfolio.get("equity", 0) or 0)

        transfers = rh.account.get_bank_transfers() or []
        net_deposits = 0.0
        for t in transfers:
            if t.get("cancel") is not None:
                continue
            state = t.get("state", "")
            if state not in ("completed",):
                continue
            created = (t.get("created_at") or "")[:10]
            if created < AGENT_START_DATE:
                continue
            amount = float(t.get("amount", 0) or 0)
            direction = t.get("direction", "")
            if direction == "deposit":
                net_deposits += amount
            elif direction == "withdraw":
                net_deposits -= amount

        logout()
        return round(equity, 2), round(net_deposits, 2)

    except Exception as exc:
        print(f"Warning: Could not fetch Robinhood balance: {exc}")
        return None, None


def _record_daily_balance():
    """
    Fetch today's real balance from Robinhood and append to balance_history.json.
    Returns (balance_history, real_balance, net_deposits).
    """
    history = _load_balance_history()
    today_str = date.today().isoformat()

    real_balance, net_deposits = _fetch_rh_balance()

    if real_balance is not None:
        existing = next((h for h in history if h["date"] == today_str), None)
        entry = {
            "date": today_str,
            "balance": real_balance,
            "net_deposits": net_deposits,
        }
        if existing:
            existing.update(entry)
        else:
            history.append(entry)
        history.sort(key=lambda h: h["date"])
        _save_balance_history(history)
        print(f"Recorded balance: ${real_balance:.2f} | Net deposits: ${net_deposits:.2f}")

    return history, real_balance, net_deposits


def summarise(records, balance_history, real_balance, net_deposits):
    # For each date, prefer actual trades over SKIP entries
    by_date = {}
    for r in records:
        d = r["date"]
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(r)

    trades = []
    for dt in sorted(by_date):
        day = by_date[dt]
        real = [r for r in day if r["result"] != "SKIP"]
        if real:
            # Deduplicate: if same result + same strike appeared multiple times keep last
            seen = {}
            for r in real:
                key = (r["result"], r["short_strike"])
                seen[key] = r
            trades.extend(seen.values())
        else:
            # Only SKIPs for this date — record once
            trades.append(day[-1])

    entered = [t for t in trades if t["result"] != "SKIP"]
    # Stats use all trades; P&L only counts trades after the reset date
    tracked = [t for t in entered if (t.get("date") or "") > AGENT_START_DATE]
    wins    = [t for t in entered if t["pnl"] > 0]
    losses  = [t for t in entered if t["pnl"] < 0]
    total_pnl   = sum(t["pnl"] for t in tracked)
    avg_hold    = (sum(t["hold_min"] for t in entered) / len(entered)) if entered else 0
    current_bp  = trades[-1]["buying_power"] if trades else 0
    win_rate    = (len(wins) / len(entered) * 100) if entered else 0

    starting_balance = 1525.08

    summary = {
        "total_pnl":        round(total_pnl, 2),
        "win_rate":         round(win_rate, 1),
        "total_trades":     len(entered),
        "wins":             len(wins),
        "losses":           len(losses),
        "skips":            len(trades) - len(entered),
        "avg_hold_min":     round(avg_hold, 1),
        "current_bp":       current_bp,
        "starting_balance": starting_balance,
        "start_date":       AGENT_START_DATE,
    }

    if real_balance is not None:
        summary["real_balance"] = real_balance
    if net_deposits is not None:
        summary["net_deposits"] = net_deposits

    return {
        "generated":        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "trades":           trades,
        "summary":          summary,
        "balance_history":  balance_history,
    }

if __name__ == "__main__":
    records = parse_logs()
    balance_history, real_balance, net_deposits = _record_daily_balance()
    data = summarise(records, balance_history, real_balance, net_deposits)
    with open(OUT_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data['trades'])} trade(s) to {OUT_FILE}")
