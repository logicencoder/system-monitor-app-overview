# System Monitor App

![System Monitor App TUI](system-monitor-app.png)

All-in-one **terminal UI** for Linux system resources. One binary replaces juggling `htop`, `iostat`, `nvidia-smi`, `sensors`, and separate network tools during debugging or load tests.

**Repository contents:** prebuilt **Linux x86_64** executable + screenshot. Source is not published; behaviour is documented here and in [ARCHITECTURE.md](ARCHITECTURE.md) from the shipped binary and operator workflow.

---

## What / why / who

| | |
|---|---|
| **What** | Keyboard-driven TUI showing CPU, memory, disk I/O, network, GPU, sensors, temperatures, and a sortable process table |
| **Why** | Developers lose time alt-tabbing across terminals; unified view speeds incident response |
| **Who** | Operator on SOL/WSL/home Linux via SSH or local console |

---

## Key features

### CPU monitoring

**What:** Real-time usage with per-core breakdown and history-style graphs in the TUI.  
**Why:** Identify single-thread hotspots vs all-core saturation.  
**Who:** Developer profiling CPU-bound bots or SSR workers.

### Memory tracking

**What:** RAM usage, swap status, process-level consumption in the process table.  
**Why:** OOM risk on small VPS instances is easier to catch early.  
**Who:** Operator running many Python services on one box.

### Disk I/O

**What:** Read/write throughput with per-device statistics.  
**Why:** Log-heavy or database workloads show disk-bound behaviour here first.  
**Who:** Anyone syncing large trees or running DuckDB/SQLite heavy jobs.

### Network stats

**What:** RX/TX bandwidth, connection-oriented counters, interface-oriented details.  
**Why:** Chain monitors and WebSocket fan-out saturate network before CPU.  
**Who:** Operator watching ingestion pipelines.

### GPU monitoring

**What:** Utilization, memory, temperature where drivers expose NVML (NVIDIA tested) or sysfs paths (AMD environment-dependent).  
**Why:** ML, rendering, or GPU-assisted workloads need thermals beside CPU.  
**Who:** Operator on GPU-equipped hosts.

### Sensors and temperatures

**What:** Thermal zones, fan speeds, voltages via lm-sensors-style interfaces when present.  
**Why:** Thermal throttling looks like “mysterious slowness” without sensor view.  
**Who:** Bare-metal or homelab operators.

### Process table

**What:** Sortable process list with resource columns.  
**Why:** Quickly find the PID eating RAM or CPU after deploying a new script.  
**Who:** Daily driver during development sessions.

---

## Quick start

```bash
chmod +x system_monitor_app
./system_monitor_app
```

Requirements:

- Linux x86_64  
- Terminal with UTF-8 support  
- No Python/Node runtime — static binary  

---

## Typical workflow

1. Launch at start of a debugging or deploy session (local or SSH).  
2. Watch CPU/RAM while starting FastAPI bots or Node SSR.  
3. Monitor GPU thermals during sustained load.  
4. Use process table to kill or renice runaway workers.  
5. Check network/disk when ingestion or log rotation spikes.  

---

## Comparison to Hardware Monitor

| | **system-monitor-app** | **hardware-monitor** |
|---|------------------------|----------------------|
| Interface | Terminal (SSH-friendly) | Browser WebSocket dashboard |
| Deploy | Single binary on host | Static HTML + remote WS backend |
| Best for | Session on the machine itself | Glance at remote server from browser |

---

## Dev / packaging notes

- **Artifact:** `system_monitor_app` ELF PIE, dynamically linked against glibc (see `file` output in repo)  
- **Stripped binary** — internal module names not exported; feature list derived from product behaviour  
- Suitable for copying to `~/bin` on SOL without dependency install  
- Refresh intervals configurable inside the TUI (keyboard-driven)  

---

## What this repo does not include

- Source code or build scripts  
- macOS/Windows builds  
- Installer packages (deb/rpm) — copy binary manually  

---

## Contact

Questions or feedback: [logicencoder.com/contact/](https://logicencoder.com/contact/)

---

**Made by [logicencoder](https://github.com/logicencoder)**
