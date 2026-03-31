# System Monitor App

All-in-one system resource monitor TUI for developers. Track CPU, memory, disk I/O, network, and processes in a single terminal window instead of managing multiple htop/top/dstat instances.

## Overview

A terminal-based system monitoring dashboard that consolidates hardware metrics, process information, and resource usage into one clean interface. Built for developers who need quick visibility into system state without switching between multiple monitoring tools.

## Key Features

- **CPU Monitoring** — Real-time usage with per-core breakdown and history graphs
- **Memory Tracking** — RAM usage, swap status, and process-level consumption  
- **Disk I/O** — Read/write throughput with per-device statistics
- **Network Stats** — RX/TX bandwidth, connection tracking, interface details
- **Process Table** — Sortable process list with resource consumption
- **TUI Interface** — Keyboard-driven interface with vim-style navigation

## Why This Exists

Instead of running 7 different terminals with htop, iostat, dstat, netstat, etc., this provides a unified view in a single terminal window. Optimized for developer workflow during debugging, profiling, or general system health checks.

## Quick Start

```bash
# Download and run
./system_monitor_app

# Or with options
./system_monitor_app --refresh 1 --sort cpu
```

## Controls

| Key | Action |
|-----|--------|
| `q` / `Ctrl+C` | Quit |
| `1-5` | Switch views (CPU, RAM, Disk, Net, Procs) |
| `↑/↓` | Navigate process list |
| `Enter` | Process details |
| `s` | Change sort column |
| `r` | Refresh rate |
| `?` | Help |

## Typical Workflow

1. Launch during development or debugging sessions
2. Monitor resource impact of running applications
3. Identify resource-heavy processes quickly
4. Check network I/O during API/chain monitoring
5. Track disk usage during data processing

## Dev Notes

- Static binary — no dependencies required
- Cross-platform terminal support
- Minimal resource footprint
- Configurable refresh intervals
- Suitable for remote SSH sessions

## Screenshot

![System Monitor App TUI](system-monitor-app.png)

---

*Terminal monitoring without the terminal clutter.*
