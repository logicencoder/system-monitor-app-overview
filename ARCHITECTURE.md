# System Monitor App — architecture

Documentation for the **shipped Linux binary** in this repository. Source is private; this file captures the **logical architecture** implied by the product surface, README, and deployment model so another developer can reason about fit without reverse-engineering every symbol.

---

## Deployment model

```text
┌──────────────────────────────────────────────┐
│  Linux host (SOL / WSL / bare metal)         │
│  ┌────────────────────────────────────────┐  │
│  │  system_monitor_app (single process)   │  │
│  │  TUI renderer + periodic sampler loop  │  │
│  └───────────────┬────────────────────────┘  │
│                  │ reads                       │
│     /proc, sysfs, NVML, lm-sensors, …        │
└──────────────────────────────────────────────┘
          ▲
          │ SSH or local tty
          │
     Operator terminal
```

| Property | Value |
|----------|--------|
| Distribution | One executable per release (~17 MB in current tree) |
| Architecture | x86_64 Linux |
| Dependencies | Standard glibc dynamic linker; no bundled Python/Node |
| Updates | Replace binary; no in-app auto-update channel in repo |

---

## Logical subsystems

The binary consolidates roles that are usually separate CLI tools:

| Subsystem | Typical data sources | Operator value |
|-----------|---------------------|----------------|
| **CPU sampler** | `/proc/stat`, per-CPU lines | Overall and per-core utilization |
| **Memory sampler** | `/proc/meminfo`, process RSS from `/proc/*/stat` | RAM pressure, swap use |
| **Disk I/O** | `/proc/diskstats` or equivalent | Throughput per device |
| **Network** | `/proc/net/dev`, connection tables | Bandwidth and interface health |
| **GPU** | NVML when NVIDIA present; sysfs on some AMD setups | Load, VRAM, temperature |
| **Sensors** | lm-sensors interfaces | CPU/GPU/chassis thermals, fans |
| **Process registry** | `/proc` scan | Sortable table with resource columns |
| **TUI shell** | Terminal raw mode | Layout, keyboard input, refresh timer |

Subsystems share a **single event loop** mental model: sample → aggregate → redraw on interval or keypress.

---

## Runtime behaviour (conceptual)

1. **Startup** — probe available providers (GPU, sensors); degrade panels gracefully when hardware absent.  
2. **Tick** — on configurable interval, refresh metrics snapshots.  
3. **Render** — paint panels without requiring a graphical server (SSH-safe).  
4. **Input** — keyboard shortcuts for sort order, panel focus, quit (exact keys documented in-app help if present).  
5. **Shutdown** — restore terminal state on exit.

Failure modes:

- Missing GPU driver → GPU panel empty or hidden (same pattern as optional GPU in hardware-monitor web client).  
- Non-root user → most metrics still available; some sensor paths may read N/A.  

---

## Resource footprint design goals

| Goal | Rationale |
|------|-----------|
| Low overhead | Meant to run **during** heavy jobs, not skew them |
| No extra daemons | One process; no agent install on production SOL |
| SSH-friendly | No X11; works over slow links if terminal size adequate |

---

## Security and ops notes

- Binary is **operator-trusted** — obtain only from this GitHub release path or your own build pipeline.  
- Does not open network ports (unlike hardware-monitor’s WebSocket gateway).  
- Read-only towards system metrics; does not modify firewall or sysctl.  
- Stripped symbols — use logs outside the TUI for application-level debug.  

---

## Relationship to other LogicEncoder observability

| Tool | When to use |
|------|-------------|
| **system-monitor-app** | You are logged into the host (SSH) |
| **hardware-monitor** | You want a browser dashboard fed by a WS collector |
| **universal-service-manager** | Supervise many **application** processes, not kernel metrics |

---

## Future documentation (if source is published)

If source becomes available in a private repo, extend this file with:

- Exact crate/module map (likely Rust TUI stack given binary shape)  
- Build reproducibility (`cargo build --release`)  
- CI matrix for glibc versions  

Until then, treat this ARCHITECTURE as the **contract for operators**, not a line-by-line map.

---

## Related

- [README.md](README.md) — features and quick start  
