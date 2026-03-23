# Code Analysis Report — `psutil_2h.py`
> System Monitor Application | Bottlenecks • Optimisations • Resource Efficiency

---

## 1. Executive Summary

`psutil_2h.py` is a terminal-based system monitor (~137 KB, ~3,400 lines) built on Python 3 using `psutil`, `Textual`, and `Rich`. It monitors CPU, memory, network, disk, GPU, services, firewall, sensors, processes, battery, and itself.

The codebase is broadly functional and well-structured, but the analysis uncovered **22 distinct issues** across five categories: subprocess bottlenecks, redundant psutil calls, static-value caching gaps, thread-safety risks, and code-quality concerns.

| Attribute | Value |
|---|---|
| File size | ~137 KB |
| Lines of code | ~3,400 |
| Monitor classes | 11 (BaseMonitor + 10 specialisations) |
| Total issues found | 22 |
| Critical bottlenecks | 6 |
| High-priority issues | 8 |
| Medium-priority issues | 5 |
| Low/Info items | 3 |

---

## 2. Architecture Overview

Each monitor is a `Static` Textual widget that re-renders on an independent timer. There is **no shared data bus** — each widget polls system APIs independently.

### Class Hierarchy

- `BaseMonitor` — shared error handling, threshold checking, interval management
  - `SelfMonitor` — tracks the app's own resource usage
  - `CPUMonitor` — per-core and total CPU stats
  - `MemoryMonitor` — RAM and swap
  - `NetworkMonitor` — I/O rates, interfaces, DNS, latency
  - `DiskMonitor` — partition usage and I/O
  - `GPUMonitor` — nvidia-smi wrapper
  - `ServiceMonitor` — systemd service states
  - `FirewallMonitor` — iptables/nftables
  - `SensorMonitor` — temperature, fans, battery
  - `ProcessMonitor` — top-N processes by CPU or memory
  - `AlertMonitor` — displays the in-memory alert history

### Key Globals

- `config` — loaded from `~/.config/system_monitor/config.yaml`
- `MONITOR_INTERVALS` — per-monitor refresh seconds (1.0–5.0 s)
- `alert_history` — `deque(maxlen=100)`, shared across all monitors
- `PLATFORM_INFO` — detected at import time

---

## 3. Bottlenecks & Critical Issues

### 3.1 Subprocess Spawning on Hot Paths

Several monitors spawn child processes on every render cycle. Spawning a subprocess costs ~5–30 ms and blocks the Python GIL during the wait.

#### 3.1.1 `FirewallMonitor._get_blocked_count()` — No cache

Called every 5 seconds. Runs **two** subprocess calls (`iptables` + `nft`) with zero caching. That's 720 subprocess pairs per hour.

```python
# CURRENT — called every 5 seconds, no cache
def _get_blocked_count(self) -> int:
    subprocess.run(['iptables', '-L', 'INPUT', ...], timeout=1)
    subprocess.run(['nft', 'list', 'ruleset'], timeout=1)

# FIX — cache it like _get_firewall_rules() already does
self._blocked_cache = 0
self._blocked_cache_time = 0

def _get_blocked_count(self):
    if time.time() - self._blocked_cache_time < self.rules_cache_ttl:
        return self._blocked_cache
    # ... run subprocesses ...
    self._blocked_cache = blocked
    self._blocked_cache_time = time.time()
    return blocked
```

#### 3.1.2 `ServiceMonitor` — 17 sequential `systemctl` calls per refresh

`_get_all_services()` calls `systemctl show` individually for each of 17 services. That's 17 sequential subprocesses every 5 seconds. `systemctl` accepts multiple units in one call.

```python
# FIX — batch all services into one systemctl call
units = [f'{s}.service' for s in self.important_services]
cmd = ['systemctl', 'show', *units, '--property=ActiveState,SubState,...']
result = subprocess.run(cmd, ...)
```

#### 3.1.3 `NetworkMonitor._get_dns_info()` — live DNS lookup every 1.1 s

Calls `socket.gethostbyname('www.google.com')` on **every render** to measure latency. That's **3,273 DNS lookups per hour**.

```python
# FIX — cache both DNS servers and resolution time
DNS_CACHE_TTL = 60

if time.time() - self._dns_cache_time < DNS_CACHE_TTL:
    return self._dns_cache
```

#### 3.1.4 `CPUMonitor._get_cpu_frequency()` — `/proc/cpuinfo` read every 1.1 s

CPU base frequency is a **static hardware property**. It should be cached after the first successful read.

```python
# FIX — cache after first successful read
self._cached_freq: Optional[int] = None

def _get_cpu_frequency(self) -> int:
    if self._cached_freq is not None:
        return self._cached_freq
    # ... detect freq ...
    self._cached_freq = result
    return result
```

---

### 3.2 Redundant psutil Calls — No Shared Data Bus

Each monitor calls psutil independently with no coordination. Key duplications:

- `psutil.net_connections(kind='inet')` — called by both `NetworkMonitor._get_connection_stats()` and `FirewallMonitor._get_active_connections()`
- `psutil.virtual_memory()` — called by `MemoryMonitor` and `SelfMonitor` in the same second
- `psutil.cpu_percent()` — called system-wide in `SelfMonitor` just to compute an overhead ratio, while `CPUMonitor` already collects it

**Fix:** Introduce a lightweight shared `DataCache` class:

```python
class DataCache:
    _net_conns = None
    _net_conns_time = 0

    @classmethod
    def net_connections(cls, ttl=2.0):
        if time.time() - cls._net_conns_time > ttl:
            cls._net_conns = psutil.net_connections(kind='inet')
            cls._net_conns_time = time.time()
        return cls._net_conns
```

---

### 3.3 Static Values Re-read on Every Cycle

| Location | What's re-read | How often | Fix |
|---|---|---|---|
| `DiskMonitor._get_partitions()` | `/sys/block/{dev}/queue/rotational` + `scheduler` | Every 3 s | Read once in `__init__` |
| `SensorMonitor._get_sensor_data()` | `os.listdir('/sys/class/hwmon')` | Every render | Read once in `__init__` |
| `SystemMonitorApp._update_header()` | `virtual_memory().total`, `cpu_count()` | Every 1 s | Read once in `on_mount()` |

---

## 4. High-Priority Issues

### 4.1 Thread Safety — Shared `alert_history` Deque

`alert_history` is a module-level deque shared across all monitors. `list(alert_history)[-10:]` in `AlertMonitor.render()` is not atomic with concurrent appends and can raise `RuntimeError`.

```python
# FIX
_alert_lock = threading.Lock()

def store_alert(...):
    with _alert_lock:
        alert_history.append(alert)

# In AlertMonitor.render():
with _alert_lock:
    recent = list(alert_history)[-10:]
```

### 4.2 Bare `except:` in `ProcessMonitor`

Two bare `except:` clauses catch `BaseException`, including `SystemExit` and `KeyboardInterrupt`. This silently swallows CTRL+C and prevents clean shutdown.

```python
# CURRENT — dangerous
try:
    mem_info = proc.memory_info()
except:    # catches SystemExit, KeyboardInterrupt!
    proc_info['memory_bytes'] = 0

# FIX
except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
    proc_info['memory_bytes'] = 0
```

### 4.3 Double Sort + Double Memory in `ProcessMonitor`

`_get_all_processes()` sorts all processes **twice** (by CPU and by memory) and stores both lists. Only one is ever displayed at a time. On systems with thousands of processes, this doubles both computation and peak memory.

**Fix:** Only sort by the currently active `sort_by` key; invalidate cache on toggle.

### 4.4 `load_config()` Shallow-Copies `DEFAULT_CONFIG`

`config = DEFAULT_CONFIG.copy()` is a shallow copy. Mutating a nested value (e.g. `config['monitors']['cpu']['interval'] = 2`) also mutates `DEFAULT_CONFIG`, causing unexpected behaviour if `load_config()` is ever called again.

```python
import copy
config = copy.deepcopy(DEFAULT_CONFIG)
```

### 4.5 `restore_terminal()` Called Twice on Error

`main()` calls `restore_terminal()` in both the `except` block and the `finally` block. On an error path it resets terminal state twice and logs an extra shutdown message.

```python
# FIX — remove from except, keep only in finally
def main():
    try:
        ...
    except Exception as e:
        logger.critical(...)
        return 1          # no restore_terminal() here
    finally:
        restore_terminal()   # always runs
```

### 4.6 `MONITOR_INTERVALS` Defined Twice

Defined as a module-level constant **and** re-assigned inside `load_config()`. The two definitions can silently diverge if one is updated without the other. Consolidate into a single definition inside `load_config()`.

### 4.7 `GPUMonitor` — `nvidia-smi` Subprocess Every 2 Seconds

`nvidia-smi` start-up time can be 200–500 ms on some systems. Replace with `pynvml` bindings to avoid process spawning entirely.

```python
# FIX — in-process GPU queries, no subprocess
import pynvml
pynvml.nvmlInit()
handle = pynvml.nvmlDeviceGetHandleByIndex(0)
util = pynvml.nvmlDeviceGetUtilizationRates(handle)
gpu_percent = util.gpu
```

### 4.8 `DiskMonitor` SSD/Scheduler Detection Every 3 s

`/sys/block/{dev}/queue/rotational` and `scheduler` are hardware properties. They don't change unless a drive is hot-swapped. Read once in `__init__()`.

---

## 5. Medium-Priority Issues

### 5.1 `format_bytes()` — Mutates Input in Loop

```python
# FIX — use a local variable
def format_bytes(value: float) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if value < 1024.0:
            return f'{value:6.1f}{unit}'
        value /= 1024.0
    return f'{value:6.1f}PB'
```

### 5.2 `create_unified_progress_bar()` — `try/except` on a Hot Path

Called dozens of times per second. The full-body `try/except` masks bugs and discourages fast-path optimisation. If inputs are validated by callers, the clamp (`max(0, min(100, percentage))`) and the exception wrapper are both redundant.

### 5.3 Inconsistent Logging Levels

Real errors logged at `DEBUG` level (e.g. `logger.debug(f'Error reading wireless info: {e}')`) means actual failures are invisible unless debug logging is on. Convention: `debug` for absent-but-expected data, `warning` for recoverable errors, `error` for correctness failures.

### 5.4 `SensorMonitor` — Duplicate Threshold Storage

`warning_temp` and `critical_temp` are stored as instance attributes, but `BaseMonitor.__init__()` already stores them in `self.alert_thresholds`. Use `self.alert_thresholds` only.

### 5.5 `NetworkMonitor._get_interface_info()` Re-reads Every Cycle

`psutil.net_if_addrs()` and `psutil.net_if_stats()` called every 1.1 s. Interface config rarely changes. Cache for 30–60 s.

---

## 6. Full Issue Reference Table

| Severity | Location | Issue | Recommendation |
|---|---|---|---|
| CRITICAL | `FirewallMonitor._get_blocked_count()` | No cache — 2 subprocesses every 5 s | Add TTL cache matching `rules_cache_ttl` |
| CRITICAL | `ServiceMonitor._get_all_services()` | 17 sequential `systemctl` calls per refresh | Batch into single `systemctl show` call |
| CRITICAL | `NetworkMonitor._get_dns_info()` | Live DNS lookup every 1.1 s | Cache DNS info for 60 s |
| CRITICAL | `CPUMonitor._get_cpu_frequency()` | `/proc/cpuinfo` scanned every 1.1 s | Cache after first successful read |
| CRITICAL | All monitors | No shared data bus — duplicate psutil calls | Introduce `DataCache` singleton |
| CRITICAL | `GPUMonitor._get_gpu_metrics()` | `nvidia-smi` subprocess every 2 s | Replace with `pynvml` in-process calls |
| HIGH | `alert_history` | Concurrent iteration not thread-safe | Add `threading.Lock` for iteration |
| HIGH | `ProcessMonitor._get_all_processes()` | Bare `except:` catches `BaseException` | Use `except (NoSuchProcess, AccessDenied)` |
| HIGH | `ProcessMonitor._get_all_processes()` | Double sort + double memory for process list | Sort only the active `sort_by` key |
| HIGH | `load_config()` | Shallow copy of `DEFAULT_CONFIG` — mutations persist | Use `copy.deepcopy(DEFAULT_CONFIG)` |
| HIGH | `NetworkMonitor` | `today_download/upload` — no lock | Add `threading.Lock` |
| HIGH | `main()` | `restore_terminal()` called twice on error | Remove from `except`, keep in `finally` only |
| HIGH | `MONITOR_INTERVALS` | Defined twice — can diverge | Single definition inside `load_config()` |
| HIGH | `DiskMonitor._get_partitions()` | SSD/scheduler sysfs read every 3 s | Cache as instance attribute in `__init__` |
| MEDIUM | `format_bytes()` | Mutates input parameter in loop | Use local variable |
| MEDIUM | `create_unified_progress_bar()` | `try/except` on hot path, redundant clamp | Remove clamp if callers validate |
| MEDIUM | Multiple monitors | Inconsistent logging levels for errors | Standardise: debug/warning/error per severity |
| MEDIUM | `SensorMonitor` | `warning_temp`/`critical_temp` duplicated from `BaseMonitor` | Use `self.alert_thresholds` only |
| MEDIUM | `NetworkMonitor._get_interface_info()` | Reads net interfaces every 1.1 s | Cache for 30–60 s with TTL |
| LOW | `SelfMonitor._get_self_metrics()` | Calls `psutil.cpu_percent()` separately | Re-use `CPUMonitor` data via `DataCache` |
| LOW | `SystemMonitorApp._update_header()` | `virtual_memory().total` called every 1 s | Read once in `on_mount()` |
| INFO | `store_alert()` | `notify-send` subprocess on every critical alert | Queue notifications in a background thread |

---

## 7. Optimisation Roadmap

### Phase 1 — Quick wins (1–2 hours)
- Cache `_get_blocked_count()` with the same TTL as `_get_firewall_rules()`
- Cache `_get_cpu_frequency()` as a one-time instance attribute
- Cache `_get_dns_info()` for 60 seconds
- Cache `DiskMonitor` SSD/scheduler detection in `__init__()`
- Cache `NetworkMonitor` interface info for 30 seconds
- Fix double `restore_terminal()` call in `main()`
- Replace bare `except:` with specific exception types in `ProcessMonitor`
- Replace `DEFAULT_CONFIG.copy()` with `copy.deepcopy()`

> Estimated impact: **~40–60% reduction in the monitor's own CPU overhead**

### Phase 2 — Medium effort (half a day)
- Batch `ServiceMonitor`'s 17 `systemctl` calls into one
- Introduce `DataCache` singleton for `net_connections()`, `virtual_memory()`, `cpu_percent()`
- Add `threading.Lock` to `alert_history` iteration
- Make `ProcessMonitor` sort only the active key; invalidate cache on toggle
- Move `virtual_memory().total` and `cpu_count()` to `on_mount()` in App

### Phase 3 — Larger refactors (1–2 days)
- Replace `nvidia-smi` subprocess calls with `pynvml` bindings
- Standardise all logging levels across monitor classes
- Add a background notification thread to decouple `store_alert()` from `render()`
- Consider a central `SystemState` object injected into monitors to fully eliminate duplicate polling

---

## 8. Conclusion

`psutil_2h.py` is a solid and feature-rich system monitor with a clean widget-based architecture. The primary performance concern is the pattern of spawning subprocesses and making live system calls on every render cycle without caching.

The six critical issues are responsible for the majority of unnecessary resource consumption. All identified issues have clear, low-risk fixes. Applying Phase 1 alone will produce a noticeably lighter and more responsive monitor with no behavioural changes for the end user.
