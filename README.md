# System Monitor App

![System Monitor App TUI](system-monitor-app.png)

**System Monitor App** is a keyboard-driven terminal dashboard for Linux. Instead of jumping between `htop`, `iostat`, `nvidia-smi`, `sensors`, and separate network tools, you get CPU, memory, disks, network, GPU, temperatures, services, firewall status, and a sortable process table in one full-screen TUI.

This repository ships a prebuilt **Linux x86_64** binary plus a screenshot. Download, `chmod +x`, run — no Python or Node runtime on the host.

## The problem it solves

When you SSH into a box to debug load, deploys, or runaway workers, context switching between half a dozen CLI tools slows you down. System Monitor App keeps the numbers you care about visible in one layout, with per-panel refresh tuned for the metric (CPU and network update faster than disk or services).

## Download and run

```bash
chmod +x system_monitor_app
./system_monitor_app
```

**Requirements:** Linux x86_64, UTF-8 terminal, glibc (dynamically linked ELF). Copy the binary to `~/bin` or any path on your `PATH`.

**Quit:** `Ctrl+C`.

## What you see in the TUI

Two columns of live panels. Each panel samples the kernel and optional drivers on its own interval; missing hardware simply hides or degrades that panel instead of crashing the app.

### CPU

Overall and per-core utilization, processor name, and frequency hints where the platform exposes them. Useful when you need to tell a single hot thread from full-box saturation.

### Memory and swap

RAM use, available memory, and swap pressure alongside the rest of the dashboard so OOM risk is visible before the kernel starts killing processes.

### Disk I/O

Per-device read/write throughput and utilization — handy when logs, databases, or large sync jobs turn a box disk-bound.

### Network

RX/TX rates and interface-oriented counters for spotting bandwidth spikes during ingestion or WebSocket-heavy workloads.

### GPU

Utilization, memory, and temperature when NVIDIA NVML or sysfs paths are available; AMD coverage depends on the host drivers.

### Sensors

Thermal zones, fan speeds, and voltages via lm-sensors-style interfaces when present — thermal throttling often looks like “mysterious slowness” without this view.

### Process table

Sortable list of top processes (default limit 20). Press **`c`** to sort by CPU or **`m`** by memory to find the PID eating resources after a deploy.

### Services and firewall

Systemd-oriented service status and firewall summary panels for a quick health check without leaving the monitor.

### Self monitor

Shows the monitor’s own CPU, memory, and I/O so you can see how much overhead the TUI adds during heavy sampling.

### Alerts

Threshold-based warnings (for example sensor heat) surfaced inside the layout when configured limits are crossed.

## Typical session

1. Launch at the start of a debugging or deploy session — locally or over SSH.
2. Watch CPU and memory while starting services or batch jobs.
3. Glance at GPU thermals under sustained load.
4. Sort the process table to find or kill runaway workers.
5. Check network and disk when ingestion or log rotation spikes.

## Configuration

On first run the app creates `~/.config/system_monitor/config.yaml` for panel layout, refresh intervals, and alert thresholds. Logs go to `~/.config/system_monitor/system_monitor.log`.

## Related repositories

See [REPOS.md](REPOS.md) for the private source tree and release workflow.

---

**Made by [Logic Encoder](https://logicencoder.com)** · [GitHub](https://github.com/logicencoder) · [Contact](https://logicencoder.com/contact/)
