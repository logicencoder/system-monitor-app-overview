# System Monitor App

![System Monitor App — full-screen Linux TUI](system-monitor-app.png)

**System Monitor App** is a keyboard-driven **terminal dashboard** for Linux. One full-screen Textual layout replaces hopping between `htop`, `iostat`, `nvidia-smi`, `sensors`, and separate network tools — CPU, memory, disks, network, GPU, thermal sensors, systemd services, firewall summary, a sortable process table, threshold alerts, and the monitor’s own overhead, all visible while you stay in SSH.

This public repo ships a prebuilt **Linux x86_64** binary plus the screenshot above. Download, `chmod +x`, run — no Python install on the host. Source is built with Textual, Rich, and **psutil** (the same stack as the private tree under the operator’s `psutil` folder on production hosts).

- **Single-screen layout** — two scrollable columns of Rich panels; header shows OS, Python build info, core count, RAM, and session uptime.
- **Per-panel refresh** — CPU and network tick about once per second; disk, services, and firewall sample slower so the TUI stays light.
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
| Release | Nuitka one-file **ELF** binary in this repo |
| Hosting | Local terminal or SSH — no web server |

## Download and run

```bash
chmod +x system_monitor_app
./system_monitor_app
```

**Requirements:** Linux x86_64, UTF-8 terminal, glibc (dynamically linked ELF). Add the binary to `~/bin` or any directory on your `PATH`.

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

Per-interface RX/TX rates, link state, address summary, connection counts by TCP state, and optional ping latency to configured hosts — useful when ingestion or WebSocket fan-out spikes bandwidth.

## GPU panel

Utilization, memory, and temperature when NVIDIA NVML or sysfs exposes them; AMD coverage depends on host drivers. Panel stays empty or minimal when no GPU is present.

## Sensors panel

Thermal zones, fan speeds, and voltage lines from standard sensor interfaces. On laptops, **battery charge**, power source (AC vs battery), estimated time remaining, and low-battery alerts fold into the same panel.

## Process table

Top processes (default limit **20**). Press **`c`** to sort by CPU or **`m`** by memory; the table refreshes and re-renders immediately. Python interpreters show the script name when available so `python3:multi_coin_monitor.py` is identifiable among many workers.

## Services panel

systemd active state for common units (SSH, cron, journal, NetworkManager, databases, web stacks when installed). Quick health check without leaving the monitor.

## Firewall panel

iptables and nftables drop/reject counters plus live connection state totals — enough to see whether a spike is traffic or a newly active rule set.

## Self monitor

CPU, memory, and I/O attributed to the monitor process itself — confirms the TUI overhead while you run heavy sampling on the same box.

## Alerts panel

Rolling in-app list of the latest threshold breaches (CPU, memory, disk, GPU, sensors, battery). Critical events can log to `~/.config/system_monitor/logs/system_monitor.log` and optionally fire a desktop notification when enabled in config.

## Configuration

First launch creates `~/.config/system_monitor/config.yaml` with monitor enable flags, refresh intervals, warning/critical thresholds, left/right column order, cores-per-line, and alert options. Edit YAML and restart to change layout — no rebuild required for operators using the shipped binary.

## Typical SSH session

When you land on a loaded box after a deploy, start the binary at the top of the session. Watch CPU and memory while services restart; sort the process table with **`m`** if RAM climbs; glance at disk and network if sync or log writes spike; check GPU and sensors under sustained compile or mining load. Exit with **`Ctrl+C`** when done — terminal mode restores cleanly.

Private code: [system-monitor-app](https://github.com/logicencoder/system-monitor-app)

See [REPOS.md](REPOS.md).

---

**Made by [Logic Encoder](https://logicencoder.com)** · [GitHub](https://github.com/logicencoder) · [Contact](https://logicencoder.com/contact/)
