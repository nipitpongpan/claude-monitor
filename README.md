# claude-monitor

A real-time terminal dashboard that combines local Claude usage statistics with live plan-limit data from claude.ai. Track your token consumption, estimated costs, and plan limits across all your projects.

![Dashboard showing token usage, cost estimates, and plan limit bars]

## Features

- Live plan-limit usage (5-hour and 7-day windows) with reset timers
- Token usage and cost estimates by model, project, and day
- Auto-refreshing dashboard or one-time snapshot mode
- Works on Windows, Linux, and macOS

## Requirements

- Python 3.8 or higher
- No other manual installs needed — dependencies are set up automatically on first run

## Quick Start

### Windows

```bat
git clone https://github.com/nipitpongpan/claude-monitor.git
cd claude-monitor
claude-monitor
```

Add the folder to your PATH so you can run `claude-monitor` from anywhere:

```powershell
[Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";$PWD", "User")
```

### Linux / macOS

```bash
git clone https://github.com/nipitpongpan/claude-monitor.git
cd claude-monitor
chmod +x claude-monitor.sh
./claude-monitor.sh
```

To run `claude-monitor` from anywhere, add the folder to your PATH:

```bash
# Add to ~/.bashrc or ~/.zshrc
export PATH="$PATH:/path/to/claude-monitor"
```

Then reload your shell and call it as:

```bash
claude-monitor.sh
```

Or create an alias in your shell config:

```bash
alias claude-monitor="/path/to/claude-monitor/claude-monitor.sh"
```

## Getting Your Session Key (one-time setup)

The session key lets the tool fetch live plan-limit data from claude.ai.

1. Open [https://claude.ai](https://claude.ai) in Chrome
2. Press `F12` → **Application** → **Cookies** → `https://claude.ai`
3. Copy the value of `sessionKey` (starts with `sk-ant-sid02-...`)
4. Run:

```bash
claude-monitor --session-key YOUR_KEY --save
```

The key is saved to `~/.claude_usage_config.json` and reused automatically on future runs.

## Usage

```
claude-monitor [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--session-key KEY` / `-s KEY` | claude.ai sessionKey cookie value |
| `--save` | Save session key for future runs |
| `--days N` / `-d N` | Days of history to analyze (default: 30) |
| `--interval SECS` / `-i SECS` | Refresh interval in seconds (default: 60) |
| `--once` | Print once and exit (no live refresh) |
| `--summary` | Show 30-day summary panel |
| `--model` | Show by-model breakdown |
| `--project` | Show by-project breakdown |
| `--daily` | Show daily usage history |
| `--all` | Show all panels |

### Examples

```bash
# Live dashboard (default)
claude-monitor

# One-time snapshot
claude-monitor --once

# Save session key and show all panels
claude-monitor --session-key YOUR_KEY --save --all

# Last 7 days, refresh every 30 seconds
claude-monitor --days 7 --interval 30
```

## How It Works

1. **Local usage** — reads JSONL logs from `~/.claude/projects/` to compute token counts and costs
2. **Live plan limits** — fetches 5-hour and 7-day usage windows from the claude.ai API using your session key
3. **Pricing** — uses current model pricing (Sonnet, Opus, Haiku) to estimate costs

## License

MIT
