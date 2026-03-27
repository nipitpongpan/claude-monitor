#!/usr/bin/env python3
"""
Claude Usage Monitor
Combines local JSONL stats with live plan-limit data from claude.ai.

Usage:
    py claude_monitor.py                            # live dashboard (plan + window)
    py claude_monitor.py --once                     # print once and exit
    py claude_monitor.py --session-key YOUR_KEY     # add plan-limit panel
    py claude_monitor.py --session-key YOUR_KEY --save   # save key for future runs
    py claude_monitor.py --days 7                   # show last 7 days (default: 30)
    py claude_monitor.py --interval 30              # refresh every 30s (default: 60)
    py claude_monitor.py --summary                  # also show 30-day summary
    py claude_monitor.py --model                    # also show by-model breakdown
    py claude_monitor.py --project                  # also show by-project breakdown
    py claude_monitor.py --daily                    # also show daily history
    py claude_monitor.py --all                      # show all panels

How to get your session key (one-time setup):
    1. Open https://claude.ai in Chrome
    2. Press F12 -> Application -> Cookies -> https://claude.ai
    3. Copy the value of 'sessionKey' (starts with sk-ant-sid02-...)
    4. Run: py claude_monitor.py --session-key PASTE_HERE --save
"""

import json
import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

try:
    from curl_cffi import requests as cf_requests
except ImportError:
    os.system(f"{sys.executable} -m pip install curl_cffi")
    from curl_cffi import requests as cf_requests

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
except ImportError:
    os.system(f"{sys.executable} -m pip install rich")
    from rich.console import Console, Group
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

# ── Constants ─────────────────────────────────────────────────────────────────

CLAUDE_DIR  = Path.home() / ".claude" / "projects"
CONFIG_FILE = Path.home() / ".claude_usage_config.json"
WINDOW_HOURS = 5

BAR_WIDTH = 30
BAR_FULL  = "#"
BAR_EMPTY = "-"

# ── Pricing (USD per million tokens) ──────────────────────────────────────────

PRICING = {
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00, "cache_read": 0.30,  "cache_write": 3.75},
    "claude-opus-4-6":            {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
    "claude-haiku-4-5":           {"input": 0.80,  "output": 4.00,  "cache_read": 0.08,  "cache_write": 1.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.00,  "cache_read": 0.08,  "cache_write": 1.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00,  "output": 15.00, "cache_read": 0.30,  "cache_write": 3.75},
    "claude-3-5-haiku-20241022":  {"input": 0.80,  "output": 4.00,  "cache_read": 0.08,  "cache_write": 1.00},
    "claude-3-opus-20240229":     {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
}
DEFAULT_PRICE = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}

# ── Config ────────────────────────────────────────────────────────────────────

def save_config(session_key: str):
    CONFIG_FILE.write_text(json.dumps({"session_key": session_key}))
    print(f"[ok] Session key saved to {CONFIG_FILE}")

def load_config() -> str:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text()).get("session_key", "")
        except Exception:
            pass
    return ""

# ── Helpers ───────────────────────────────────────────────────────────────────

def calc_cost(model, inp, out, cr, cw):
    p = PRICING.get(model, DEFAULT_PRICE)
    return (inp * p["input"] + out * p["output"] +
            cr  * p["cache_read"] + cw * p["cache_write"]) / 1_000_000

def fmt_tok(n):
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def fmt_cost(c):
    return f"${c:.4f}" if c >= 1.0 else f"${c:.6f}"

def pct_bar(pct, width=BAR_WIDTH):
    pct = min(max(pct, 0.0), 1.0)
    filled = int(pct * width)
    b = BAR_FULL * filled + BAR_EMPTY * (width - filled)
    color = "green"
    if pct >= 0.9: color = "red"
    elif pct >= 0.7: color = "yellow"
    return f"[{color}]{b}[/{color}] [bold]{pct*100:.1f}%[/bold]"

def rel_bar(value, max_value, width=BAR_WIDTH):
    if not max_value: return BAR_EMPTY * width
    return pct_bar(value / max_value, width)

def short_name(raw: str) -> str:
    for prefix in (
        "C--Users-NPongpan-Project-",
        "c--Users-NPongpan-Project-",
        "C--Users-NPongpan-OneDrive---Lewis-University-Documents-Current-Work-",
        "c--Users-NPongpan-OneDrive---Lewis-University-Documents-Current-Work-",
        "C--Users-NPongpan-OneDrive---Lewis-University-",
        "c--Users-NPongpan-OneDrive---Lewis-University-",
        "C--Users-NPongpan-",
        "c--Users-NPongpan-",
    ):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    return re.sub(r"-\d{6,}$", "", raw)

# ── Local data loading ─────────────────────────────────────────────────────────

def _empty():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "cost": 0.0, "messages": 0}

def load_usage(days: int):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    totals = _empty()
    by_project = defaultdict(_empty)
    by_model   = defaultdict(_empty)
    by_day     = defaultdict(_empty)
    session_count = 0

    if not CLAUDE_DIR.exists():
        return totals, by_project, by_model, by_day, session_count

    for proj_dir in CLAUDE_DIR.iterdir():
        if not proj_dir.is_dir(): continue
        pname = short_name(proj_dir.name)
        for jf in proj_dir.glob("*.jsonl"):
            session_count += 1
            try:
                with open(jf, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue
                        try: obj = json.loads(line)
                        except: continue
                        if obj.get("type") != "assistant": continue
                        msg   = obj.get("message", {})
                        usage = msg.get("usage")
                        if not usage: continue
                        ts_str = obj.get("timestamp", "")
                        try: ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except: continue
                        if ts < cutoff: continue
                        model = msg.get("model", "unknown")
                        inp = usage.get("input_tokens", 0)
                        out = usage.get("output_tokens", 0)
                        cr  = usage.get("cache_read_input_tokens", 0)
                        cw  = usage.get("cache_creation_input_tokens", 0)
                        cost = calc_cost(model, inp, out, cr, cw)
                        day  = ts.astimezone().strftime("%Y-%m-%d")
                        for b in [totals, by_project[pname], by_model[model], by_day[day]]:
                            b["input"]      += inp
                            b["output"]     += out
                            b["cache_read"] += cr
                            b["cache_write"]+= cw
                            b["cost"]       += cost
                            b["messages"]   += 1
            except: continue

    return totals, by_project, by_model, by_day, session_count


def load_window_usage(hours: int = WINDOW_HOURS):
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    buckets = [{"messages": 0, "tokens": 0} for _ in range(hours)]
    totals  = _empty()

    if not CLAUDE_DIR.exists():
        return buckets, totals

    for proj_dir in CLAUDE_DIR.iterdir():
        if not proj_dir.is_dir(): continue
        for jf in proj_dir.glob("*.jsonl"):
            try:
                with open(jf, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue
                        try: obj = json.loads(line)
                        except: continue
                        if obj.get("type") != "assistant": continue
                        msg   = obj.get("message", {})
                        usage = msg.get("usage")
                        if not usage: continue
                        ts_str = obj.get("timestamp", "")
                        try: ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except: continue
                        if ts < cutoff or ts > now: continue
                        age_hours = (now - ts).total_seconds() / 3600
                        idx = max(0, min(hours - 1 - int(age_hours), hours - 1))
                        model = msg.get("model", "unknown")
                        inp = usage.get("input_tokens", 0)
                        out = usage.get("output_tokens", 0)
                        cr  = usage.get("cache_read_input_tokens", 0)
                        cw  = usage.get("cache_creation_input_tokens", 0)
                        tok  = inp + out + cr + cw
                        cost = calc_cost(model, inp, out, cr, cw)
                        buckets[idx]["messages"] += 1
                        buckets[idx]["tokens"]   += tok
                        totals["messages"] += 1
                        totals["input"]    += inp
                        totals["output"]   += out
                        totals["cache_read"]  += cr
                        totals["cache_write"] += cw
                        totals["cost"]     += cost
            except: continue

    return buckets, totals

# ── Plan limit fetch ───────────────────────────────────────────────────────────

def fetch_plan_limit(session_key: str):
    if not session_key:
        return None, "No session key – run with --session-key to see plan limits."

    headers = {
        "Accept": "application/json",
        "Referer": "https://claude.ai/",
        "anthropic-client-platform": "web_claude_ai",
        "anthropic-client-type": "web",
        "Origin": "https://claude.ai",
        "Cookie": f"sessionKey={session_key.strip()}",
    }

    try:
        r = cf_requests.get("https://claude.ai/api/organizations",
                            headers=headers, impersonate="chrome124", timeout=15)
        if r.status_code == 401:
            return None, "401 – Session expired. Update with: py claude_monitor.py --session-key NEW_KEY --save"
        if r.status_code != 200:
            return None, (f"Auth failed ({r.status_code}).\n"
                          "  Refresh key: F12 -> Application -> Cookies -> claude.ai -> sessionKey\n"
                          "  Then: py claude_monitor.py --session-key VALUE --save")

        orgs = r.json()
        if not isinstance(orgs, list) or not orgs:
            return None, "No organizations found."
        org_uuid = orgs[0].get("uuid")

        r2 = cf_requests.get(f"https://claude.ai/api/organizations/{org_uuid}/usage",
                             headers=headers, impersonate="chrome124", timeout=15)
        if r2.status_code != 200:
            return None, f"Usage endpoint returned {r2.status_code}."

        data = r2.json()
        fh = data.get("five_hour") or {}
        sd = data.get("seven_day") or {}
        ex = data.get("extra_usage") or {}

        def parse_dt(s):
            if not s: return None
            try: return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except: return None

        return {
            "five_hour_pct":   (fh.get("utilization", 0.0)) / 100.0,
            "seven_day_pct":   (sd.get("utilization", 0.0)) / 100.0,
            "five_hour_raw":   fh.get("utilization", 0.0),
            "seven_day_raw":   sd.get("utilization", 0.0),
            "five_hour_reset": parse_dt(fh.get("resets_at")),
            "seven_day_reset": parse_dt(sd.get("resets_at")),
            "extra_enabled":   ex.get("is_enabled", False),
            "extra_used":      ex.get("used_credits"),
            "extra_limit":     ex.get("monthly_limit"),
        }, None

    except Exception as e:
        return None, f"Error fetching plan data: {e}"

# ── Panels ────────────────────────────────────────────────────────────────────

def _fmt_reset(dt):
    if not dt: return "unknown"
    now  = datetime.now(timezone.utc)
    diff = dt - now
    if diff.total_seconds() < 0: return "resetting soon"
    total_mins = int(diff.total_seconds() / 60)
    h, m = divmod(total_mins, 60)
    local = dt.astimezone()
    if h > 0:
        return f"in {h}h {m:02d}m  ({local.strftime('%H:%M')})"
    return f"in {m}m  ({local.strftime('%H:%M')})"


def make_window_panel(buckets, totals, plan, plan_err):
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), min_width=54)
    t.add_column("k", style="bold white", no_wrap=True, min_width=20)
    t.add_column("v", no_wrap=True)

    if plan_err:
        t.add_row("[yellow]Plan limit[/yellow]", f"[dim]{plan_err}[/dim]")
        t.add_row("", "")
    elif plan:
        t.add_row("[bold]5-hour window[/bold]", pct_bar(plan["five_hour_pct"]))
        t.add_row("  Resets",                   f"[dim]{_fmt_reset(plan['five_hour_reset'])}[/dim]")
        t.add_row("", "")
        t.add_row("[bold]7-day limit[/bold]",   pct_bar(plan["seven_day_pct"]))
        t.add_row("  Resets",                   f"[dim]{_fmt_reset(plan['seven_day_reset'])}[/dim]")
        if plan.get("extra_enabled") and plan.get("extra_limit"):
            extra_pct = (plan["extra_used"] or 0) / plan["extra_limit"]
            t.add_row("", "")
            t.add_row("[bold]Extra credits[/bold]", pct_bar(extra_pct))
        t.add_row("", "")

    t.add_row(f"[bold]Last {WINDOW_HOURS}h  (local)[/bold]", "")
    t.add_row("Messages", f"[bold]{totals['messages']:,}[/bold]")
    total_tok = totals["input"] + totals["output"] + totals["cache_read"] + totals["cache_write"]
    t.add_row("Tokens",   f"[bold]{fmt_tok(total_tok)}[/bold]")
    t.add_row("Cost",     f"[bold green]{fmt_cost(totals['cost'])}[/bold green]")
    t.add_row("", "")

    if any(b["messages"] for b in buckets):
        max_msgs = max(b["messages"] for b in buckets) or 1
        now = datetime.now()
        t.add_row("[dim]Hour[/dim]", "[dim]Activity[/dim]")
        for i, b in enumerate(buckets):
            h_offset = WINDOW_HOURS - 1 - i
            label = (now - timedelta(hours=h_offset)).strftime("%H:00")
            filled = int((b["messages"] / max_msgs) * 20)
            bar_str = f"[cyan]{BAR_FULL * filled}{BAR_EMPTY * (20 - filled)}[/cyan] {b['messages']}"
            t.add_row(f"[dim]{label}[/dim]", bar_str)

    return Panel(t, title="[bold cyan] Plan Usage + Current Window [/bold cyan]",
                 border_style="cyan", padding=(0, 1))


def make_summary_panel(totals, session_count, days, last_updated, secs_left, interval):
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), min_width=44)
    t.add_column("k", style="bold white", no_wrap=True)
    t.add_column("v", style="cyan")
    total_tok = totals["input"] + totals["output"] + totals["cache_read"] + totals["cache_write"]
    t.add_row("Period",       f"Last [bold]{days}[/bold] days")
    t.add_row("Sessions",     f"[bold]{session_count}[/bold]")
    t.add_row("Messages",     f"[bold]{totals['messages']:,}[/bold]")
    t.add_row("Total tokens", f"[bold]{fmt_tok(total_tok)}[/bold]")
    t.add_row("", "")
    t.add_row("Input",        fmt_tok(totals["input"]))
    t.add_row("Output",       fmt_tok(totals["output"]))
    t.add_row("Cache read",   fmt_tok(totals["cache_read"]))
    t.add_row("Cache write",  fmt_tok(totals["cache_write"]))
    t.add_row("", "")
    t.add_row("Est. cost",    f"[bold green]{fmt_cost(totals['cost'])}[/bold green]")
    t.add_row("", "")
    t.add_row("[dim]Updated[/dim]", f"[dim]{last_updated}[/dim]")
    t.add_row("[dim]Refresh[/dim]", f"[dim]in {secs_left}s (every {interval}s)[/dim]")
    return Panel(t, title="[bold cyan] Summary [/bold cyan]", border_style="cyan", padding=(0, 1))


def make_model_panel(by_model):
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan",
              min_width=62, padding=(0, 1))
    t.add_column("Model",   style="bold white", no_wrap=True)
    t.add_column("Msgs",    justify="right")
    t.add_column("Input",   justify="right")
    t.add_column("Output",  justify="right")
    t.add_column("Cache R", justify="right")
    t.add_column("Cache W", justify="right")
    t.add_column("Cost",    justify="right", style="green")
    if not by_model:
        t.add_row("[dim]No data[/dim]", "", "", "", "", "", "")
    else:
        for model, d in sorted(by_model.items(), key=lambda x: -x[1]["cost"]):
            short = model.replace("claude-", "").replace("-20", " 20")
            t.add_row(short, f"{d['messages']:,}", fmt_tok(d["input"]),
                      fmt_tok(d["output"]), fmt_tok(d["cache_read"]),
                      fmt_tok(d["cache_write"]), fmt_cost(d["cost"]))
    return Panel(t, title="[bold cyan] By Model [/bold cyan]", border_style="cyan", padding=(0, 1))


def make_project_panel(by_project):
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan",
              min_width=70, padding=(0, 1))
    t.add_column("Project", style="bold white", no_wrap=True, min_width=28, max_width=36)
    t.add_column("Msgs",    justify="right", min_width=6)
    t.add_column("Cost",    justify="right", style="green", min_width=10)
    t.add_column("Share",   no_wrap=True, min_width=BAR_WIDTH + 1)
    if not by_project:
        t.add_row("[dim]No data[/dim]", "", "", "")
    else:
        max_cost = max((d["cost"] for d in by_project.values()), default=0)
        for proj, d in sorted(by_project.items(), key=lambda x: -x[1]["cost"])[:15]:
            t.add_row(proj[:36], f"{d['messages']:,}", fmt_cost(d["cost"]),
                      rel_bar(d["cost"], max_cost))
    return Panel(t, title="[bold cyan] By Project (top 15) [/bold cyan]", border_style="cyan", padding=(0, 1))


def make_daily_panel(by_day, days):
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan",
              min_width=62, padding=(0, 1))
    t.add_column("Date",   style="bold white", no_wrap=True)
    t.add_column("Msgs",   justify="right")
    t.add_column("Tokens", justify="right")
    t.add_column("Cost",   justify="right", style="green")
    t.add_column("",       no_wrap=True, min_width=BAR_WIDTH + 1)
    if not by_day:
        t.add_row("[dim]No data[/dim]", "", "", "", "")
    else:
        max_cost = max((d["cost"] for d in by_day.values()), default=0)
        for day in sorted(by_day.keys(), reverse=True)[:days]:
            d = by_day[day]
            tok = d["input"] + d["output"] + d["cache_read"] + d["cache_write"]
            t.add_row(day, f"{d['messages']:,}", fmt_tok(tok),
                      fmt_cost(d["cost"]), rel_bar(d["cost"], max_cost))
    return Panel(t, title="[bold cyan] Daily Usage [/bold cyan]", border_style="cyan", padding=(0, 1))

# ── Main ───────────────────────────────────────────────────────────────────────

def load_all(days, session_key):
    totals, by_project, by_model, by_day, sc = load_usage(days)
    buckets, win_totals = load_window_usage(WINDOW_HOURS)
    plan, plan_err = fetch_plan_limit(session_key)
    return totals, by_project, by_model, by_day, sc, buckets, win_totals, plan, plan_err


def build_display(totals, by_project, by_model, by_day, sc,
                  buckets, win_totals, plan, plan_err,
                  days, interval, last_updated, secs_left, args):
    panels = [make_window_panel(buckets, win_totals, plan, plan_err)]
    if args.summary:
        panels.append(make_summary_panel(totals, sc, days, last_updated, secs_left, interval))
    if args.model:
        panels.append(make_model_panel(dict(by_model)))
    if args.project:
        panels.append(make_project_panel(dict(by_project)))
    if args.daily:
        panels.append(make_daily_panel(dict(by_day), days))
    return Group(*panels)


def main():
    parser = argparse.ArgumentParser(
        description="Claude Usage Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--session-key", "-s", metavar="KEY",
                        help="claude.ai sessionKey cookie value")
    parser.add_argument("--save",     action="store_true",
                        help="Save session key to ~/.claude_usage_config.json")
    parser.add_argument("--days",     "-d", type=int, default=30, metavar="N",
                        help="Days of history (default: 30)")
    parser.add_argument("--interval", "-i", type=int, default=60, metavar="SECS",
                        help="Refresh interval in seconds (default: 60)")
    parser.add_argument("--once",     action="store_true",
                        help="Print once and exit")
    parser.add_argument("--summary",  action="store_true",
                        help="Also show 30-day summary panel")
    parser.add_argument("--model",    action="store_true",
                        help="Also show by-model breakdown")
    parser.add_argument("--project",  action="store_true",
                        help="Also show by-project breakdown")
    parser.add_argument("--daily",    action="store_true",
                        help="Also show daily usage history")
    parser.add_argument("--all",      action="store_true",
                        help="Show all panels")
    args = parser.parse_args()

    session_key = args.session_key or load_config()

    if args.save and args.session_key:
        save_config(args.session_key)

    if args.all:
        args.summary = args.model = args.project = args.daily = True

    if not CLAUDE_DIR.exists():
        print(f"[!] Claude projects directory not found: {CLAUDE_DIR}")
        sys.exit(1)

    if args.once:
        data = load_all(args.days, session_key)
        totals, by_project, by_model, by_day, sc, buckets, win_totals, plan, plan_err = data
        last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        Console().print(build_display(totals, by_project, by_model, by_day, sc,
                                      buckets, win_totals, plan, plan_err,
                                      args.days, args.interval, last_updated, 0, args))
        return

    console = Console()
    cache = None
    last_updated = ""
    next_refresh = time.time()

    with Live(console=console, refresh_per_second=2, screen=True) as live:
        while True:
            now = time.time()
            if now >= next_refresh:
                cache = load_all(args.days, session_key)
                last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                next_refresh = now + args.interval

            if cache:
                totals, by_project, by_model, by_day, sc, buckets, win_totals, plan, plan_err = cache
                secs_left = max(0, int(next_refresh - time.time()))
                live.update(build_display(
                    totals, by_project, by_model, by_day, sc,
                    buckets, win_totals, plan, plan_err,
                    args.days, args.interval, last_updated, secs_left, args,
                ))

            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[bye]")
