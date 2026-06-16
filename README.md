# System Monitor App

![System Monitor App — full-screen Linux TUI](system-monitor-app.png)

**System Monitor App** is a keyboard-driven **terminal dashboard** for Linux — one full-screen view instead of juggling a pile of separate tools.

When you debug a loaded box you often end up with **five or six browser tabs** open: process list in one, disk stats in another, GPU somewhere else, sensors, network, services — and you keep **alt-tabbing and scrolling** just to answer “is CPU pegged, is RAM dying, is disk saturated?” System Monitor App puts what you actually check on **one screen**: two columns of live panels in the terminal. Glance once, sort processes with a keypress, no clicking through tabs.

CPU, memory, swap, disk I/O, network, GPU, thermal sensors, systemd services, firewall summary, a sortable process table, threshold alerts, and the monitor’s own overhead — all in the same SSH session.

- **Single-screen layout** — two scrollable columns of Rich panels; header shows OS, core count, RAM, and session uptime.
- **Per-panel refresh** — CPU and network tick about once per second; disk, services, and firewall sample slower so the TUI stays responsive.
- **Graceful degradation** — missing GPU drivers, sensors, or firewall tools hide or soften that panel instead of crashing the session.
- **YAML configuration** — panel order, intervals, and alert thresholds in `~/.config/system_monitor/config.yaml` on first run.

## Tech stack

| Layer | Technologies |
|-------|--------------|
| UI | Python 3.10+, **Textual**, **Rich** (panels, tables, progress bars) |
| Metrics | **psutil** — CPU, memory, swap, disk I/O, network, processes |
| GPU | NVIDIA NVML and sysfs paths when drivers expose them |
| Sensors | lm-sensors-style thermal zones, fans, voltages; laptop battery via psutil when present |
| Services | `systemctl` status for a curated list of common Linux units |
| Firewall | iptables / nftables summaries and connection state counts |
| Alerts | In-memory history, optional `notify-send`, critical lines in log file |
| Hosting | Local terminal or SSH — no web UI, no browser tabs |

## Run

From this repo:

```bash
chmod +x system_monitor_app
./system_monitor_app
```

From source (private repo): `python3 system_monitor_app.py` after `pip install -r requirements.txt`.

**Requirements:** Linux, UTF-8 terminal. Works locally or over SSH.

**Quit:** `Ctrl+C` (restores the terminal after exit).

## Screen layout

The app opens one grid: **header** (machine summary), **left column**, **right column**, **footer**. Each column scrolls independently when panels exceed terminal height.

Default column assignment (reorder in config):

| Left column | Right column |
|-------------|--------------|
| CPU | Self monitor |
| GPU | Memory |
| Process table | Disk I/O |
| Services | Network |
| Alerts | Sensors |
| | Firewall |

The **Alerts** panel appends to the left column when not placed elsewhere — it always shows the last threshold events.

## CPU panel

Overall utilization, per-core bars (configurable cores per line), load averages, CPU time breakdown (user/system/idle), processor name, frequency hints, and system uptime since boot. Colour bars shift from green through yellow to red as utilization crosses warning and critical thresholds from config.

## Memory panel

RAM used, available, and percent bars plus swap use — swap pressure visible next to CPU load when a deploy or batch job risks OOM.

## Disk I/O panel

Per-device read/write throughput and utilization so log rotation, database sync, or chain indexing shows up as disk-bound before iowait shows in CPU alone.

## Network panel

Per-interface RX/TX rates, link state, address summary, connection counts by TCP state, and optional ping latency to configured hosts — useful when ingestion or WebSocket-heavy workloads spike bandwidth.

## GPU panel

Utilization, memory, and temperature when NVIDIA NVML or sysfs exposes them; AMD coverage depends on host drivers. Panel stays empty or minimal when no GPU is present.

## Sensors panel

Thermal zones, fan speeds, and voltage lines from standard sensor interfaces. On laptops, **battery charge**, power source (AC vs battery), estimated time remaining, and low-battery alerts fold into the same panel.

## Process table

Top processes (default limit **20**). Press **`c`** to sort by CPU or **`m`** by memory; the table refreshes and re-renders immediately. Python interpreters show the script name when available so long-running workers are easy to spot among many PIDs.

## Services panel

systemd active state for common units (SSH, cron, journal, NetworkManager, databases, web stacks when installed). Quick health check without leaving the monitor.

## Firewall panel

iptables and nftables drop/reject counters plus live connection state totals — enough to see whether a spike is traffic or a newly active rule set.

## Self monitor

CPU, memory, and I/O attributed to the monitor process itself — confirms the TUI overhead while you run heavy sampling on the same box.

## Alerts panel

Rolling in-app list of the latest threshold breaches (CPU, memory, disk, GPU, sensors, battery). Critical events can log to `~/.config/system_monitor/logs/system_monitor.log` and optionally fire a desktop notification when enabled in config.

## Configuration

First launch creates `~/.config/system_monitor/config.yaml` with monitor enable flags, refresh intervals, warning/critical thresholds, left/right column order, cores-per-line, and alert options. Edit YAML and restart to change layout.

## Typical SSH session

SSH in after a deploy, start the monitor once at the top of the session. Watch CPU and memory while services restart; press **`m`** if RAM climbs; glance at disk and network if sync or log writes spike; check GPU and sensors under sustained load. Everything stays on one screen — no tab circus. Exit with **`Ctrl+C`** when done.

Private code: [system-monitor-app](https://github.com/logicencoder/system-monitor-app)

See [REPOS.md](REPOS.md).

---

**Made by [Logic Encoder](https://logicencoder.com)** · [GitHub](https://github.com/logicencoder) · [Contact](https://logicencoder.com/contact/)
