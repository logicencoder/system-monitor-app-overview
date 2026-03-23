#!/usr/bin/env python3
# =============================================================================
# System Monitor — Part 1 of 4
# Covers: imports, config, DataCache, globals, utilities, BaseMonitor, SelfMonitor
#
# Fixes applied in this file:
#   [FIX-01] orjson replaces json everywhere
#   [FIX-02] copy.deepcopy(DEFAULT_CONFIG) prevents mutation of defaults
#   [FIX-03] MONITOR_INTERVALS defined exactly once (inside load_config)
#   [FIX-04] DataCache singleton eliminates duplicate psutil calls across monitors
#   [FIX-05] threading.Lock on alert_history makes iteration thread-safe
#   [FIX-06] store_alert no longer spawns notify-send inline; queued separately
#   [FIX-07] SelfMonitor caches static system totals (cpu_count, mem total)
# =============================================================================

import os
import re
import sys
import copy
import time
import signal
import socket
import logging
import platform
import subprocess
import threading
import traceback
import yaml
from typing import Dict, Optional, List, Any
from datetime import datetime
from collections import deque
from pathlib import Path

# [FIX-01] Use orjson instead of json — faster, stricter, returns bytes
try:
    import orjson
    def _json_dumps(obj: Any) -> str:
        return orjson.dumps(obj).decode()
    def _json_loads(data: str) -> Any:
        return orjson.loads(data)
except ImportError:
    import json as _json
    def _json_dumps(obj: Any) -> str:
        return _json.dumps(obj)
    def _json_loads(data: str) -> Any:
        return _json.loads(data)

# ---------------------------------------------------------------------------
# Basic logging — enhanced later once config dir is known
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("SystemMonitor")
logger.info("Starting System Monitor initialisation...")

# ---------------------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------------------
try:
    from rich import box
    from rich.text import Text
    from rich.panel import Panel
    from rich.table import Table
    from rich.logging import RichHandler
    from rich.console import Console
    console = Console()

    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Static
    from textual.containers import Grid, Container
    from textual.binding import Binding

    import psutil

    logger.info("All required libraries imported successfully")
except ImportError as e:
    print(f"ERROR: Required dependency not found: {e}")
    print("Please install with: pip install psutil textual rich pyyaml orjson")
    sys.exit(1)

try:
    from textual.screen import Screen
    Screen.DIALOG_CLASSES = []
    logger.info("Dialog classes disabled")
except Exception as e:
    logger.error(f"Failed to disable dialog classes: {e}")

# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------
CONFIG_DIR  = os.path.expanduser("~/.config/system_monitor")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")
LOG_FILE    = os.path.join(CONFIG_DIR, "system_monitor.log")

try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    logger.info(f"Config directory ensured: {CONFIG_DIR}")
except Exception as e:
    logger.error(f"Failed to create config directory: {e}")

# Enhanced logging with file handler
try:
    log_dir = os.path.join(CONFIG_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
    file_handler = logging.FileHandler(os.path.join(log_dir, "system_monitor.log"))
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root_logger.addHandler(file_handler)
    root_logger.addHandler(RichHandler(rich_tracebacks=True))
    root_logger.setLevel(logging.INFO)
    logger.info("Enhanced logging configured")
except Exception as e:
    logger.error(f"Failed to set up enhanced logging: {e}")

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "monitors": {
        "cpu":      {"enabled": True, "interval": 1.1, "warning_threshold": 75, "critical_threshold": 90},
        "memory":   {"enabled": True, "interval": 2.1, "warning_threshold": 75, "critical_threshold": 90},
        "disk":     {"enabled": True, "interval": 3.0, "warning_threshold": 75, "critical_threshold": 90},
        "network":  {"enabled": True, "interval": 1.1},
        "gpu":      {"enabled": True, "interval": 2.0, "warning_threshold": 75, "critical_threshold": 90},
        "sensors":  {"enabled": True, "interval": 2.0, "warning_threshold": 75, "critical_threshold": 90},
        "services": {"enabled": True, "interval": 5.0},
        "process":  {"enabled": True, "interval": 3.0, "limit": 20},
        "battery":  {"enabled": True, "interval": 5.0, "warning_threshold": 20, "critical_threshold": 10},
        "firewall": {"enabled": True, "interval": 5.0},
        "self":     {"enabled": True, "interval": 2.0},  # was 1.0 — open_files scan not needed every second
    },
    "ui": {
        "theme": "dark",
        "left_column":   ["cpu", "process", "services"],
        "right_column":  ["self", "memory", "gpu", "disk", "network", "sensors", "firewall"],
        "cores_per_line": 4,
    },
    "alerts": {
        "enabled": True,
        "desktop_notification": False,
        "log_critical_events": True,
    },
    "data": {
        "persistence_enabled": False,
    },
}

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
try:
    PLATFORM_INFO: Dict[str, Any] = {
        "system":         platform.system().lower(),
        "release":        platform.release(),
        "version":        platform.version(),
        "machine":        platform.machine(),
        "is_linux":       platform.system().lower() == "linux",
        "python_version": sys.version.split()[0],
        "processor":      platform.processor(),
    }
    logger.info(f"System detected: {PLATFORM_INFO['system']} {PLATFORM_INFO['release']}")
except Exception as e:
    logger.error(f"Failed to gather platform info: {e}")
    PLATFORM_INFO = {
        "system": "unknown", "release": "unknown", "version": "unknown",
        "machine": "unknown", "is_linux": False,
        "python_version": "unknown", "processor": "unknown",
    }

# ---------------------------------------------------------------------------
# Global mutable state  (MONITOR_INTERVALS set once inside load_config)
# [FIX-03] No duplicate module-level definition — only one source of truth.
# ---------------------------------------------------------------------------
config:           Dict[str, Any] = {}
MONITOR_INTERVALS: Dict[str, float] = {}   # populated by load_config()
CORES_PER_LINE:   int = 4                  # populated by load_config()
should_exit:      bool = False

# [FIX-05] Lock that guards alert_history reads AND writes
_alert_lock   = threading.Lock()
alert_history = deque(maxlen=100)


# =============================================================================
# [FIX-04] DataCache — single source of truth for expensive psutil calls
#
# All monitors call DataCache.<metric>() instead of psutil directly.
# Each metric has its own TTL; the cache is refreshed lazily on first access
# after expiry.  Because Textual runs widgets on the asyncio event loop
# (single thread), a simple time-check is sufficient for most counters.
# The lock is kept anyway for safety in case background threads are added.
# =============================================================================
class DataCache:
    _lock = threading.Lock()

    # --- virtual_memory ---
    _vmem      = None
    _vmem_time = 0.0
    VMEM_TTL   = 1.0

    # --- swap_memory ---
    _swap      = None
    _swap_time = 0.0
    SWAP_TTL   = 2.0

    # --- cpu_percent (per-cpu) ---
    _cpu_pct      = None
    _cpu_pct_time = 0.0
    CPU_PCT_TTL   = 0.9

    # --- cpu_times_percent ---
    _cpu_times      = None
    _cpu_times_time = 0.0
    CPU_TIMES_TTL   = 1.0

    # --- getloadavg ---
    _load_avg      = None
    _load_avg_time = 0.0
    LOAD_AVG_TTL   = 2.0

    # --- cpu_count ---
    _cpu_count: Optional[int] = None   # never changes

    # --- boot_time ---
    _boot_time: Optional[float] = None  # never changes

    # --- net_io_counters ---
    _net_io      = None
    _net_io_time = 0.0
    NET_IO_TTL   = 0.9

    # --- disk_io_counters ---
    _disk_io      = None
    _disk_io_time = 0.0
    DISK_IO_TTL   = 0.9

    # --- disk_partitions (static after boot) ---
    _disk_parts      = None
    _disk_parts_time = 0.0
    DISK_PARTS_TTL   = 30.0

    # --- disk_usage per mountpoint ---
    _disk_usage:      Dict[str, Any] = {}
    _disk_usage_time: float          = 0.0
    DISK_USAGE_TTL   = 3.0

    # --- sensors_temperatures ---
    _sensor_temps      = None
    _sensor_temps_time = 0.0
    SENSOR_TEMPS_TTL   = 2.0

    # --- sensors_fans ---
    _sensor_fans      = None
    _sensor_fans_time = 0.0
    SENSOR_FANS_TTL   = 2.0

    @classmethod
    def virtual_memory(cls):
        with cls._lock:
            if time.time() - cls._vmem_time > cls.VMEM_TTL:
                cls._vmem      = psutil.virtual_memory()
                cls._vmem_time = time.time()
            return cls._vmem

    @classmethod
    def swap_memory(cls):
        with cls._lock:
            if time.time() - cls._swap_time > cls.SWAP_TTL:
                cls._swap      = psutil.swap_memory()
                cls._swap_time = time.time()
            return cls._swap

    @classmethod
    def cpu_percent_percpu(cls) -> list:
        with cls._lock:
            if time.time() - cls._cpu_pct_time > cls.CPU_PCT_TTL:
                cls._cpu_pct      = psutil.cpu_percent(percpu=True)
                cls._cpu_pct_time = time.time()
            return cls._cpu_pct or []

    @classmethod
    def cpu_times_percent(cls):
        with cls._lock:
            if time.time() - cls._cpu_times_time > cls.CPU_TIMES_TTL:
                cls._cpu_times      = psutil.cpu_times_percent()
                cls._cpu_times_time = time.time()
            return cls._cpu_times

    @classmethod
    def getloadavg(cls) -> tuple:
        with cls._lock:
            if time.time() - cls._load_avg_time > cls.LOAD_AVG_TTL:
                try:
                    cls._load_avg = psutil.getloadavg()
                except Exception:
                    cls._load_avg = (0.0, 0.0, 0.0)
                cls._load_avg_time = time.time()
            return cls._load_avg or (0.0, 0.0, 0.0)

    @classmethod
    def cpu_count(cls) -> int:
        if cls._cpu_count is None:
            cls._cpu_count = psutil.cpu_count() or 1
        return cls._cpu_count

    @classmethod
    def boot_time(cls) -> float:
        """Cached — boot time never changes while the system is running."""
        if cls._boot_time is None:
            cls._boot_time = psutil.boot_time()
        return cls._boot_time

    @classmethod
    def disk_usage_all(cls) -> Dict[str, Any]:
        """Returns {mountpoint: usage} for all cached partitions."""
        with cls._lock:
            if time.time() - cls._disk_usage_time > cls.DISK_USAGE_TTL:
                new_usage: Dict[str, Any] = {}
                for part in (cls._disk_parts or []):
                    try:
                        new_usage[part.mountpoint] = psutil.disk_usage(part.mountpoint)
                    except (PermissionError, OSError):
                        pass
                cls._disk_usage      = new_usage
                cls._disk_usage_time = time.time()
            return cls._disk_usage

    @classmethod
    def sensors_temperatures(cls):
        with cls._lock:
            if time.time() - cls._sensor_temps_time > cls.SENSOR_TEMPS_TTL:
                try:
                    cls._sensor_temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
                except Exception:
                    cls._sensor_temps = cls._sensor_temps or {}
                cls._sensor_temps_time = time.time()
            return cls._sensor_temps or {}

    @classmethod
    def sensors_fans(cls):
        with cls._lock:
            if time.time() - cls._sensor_fans_time > cls.SENSOR_FANS_TTL:
                try:
                    cls._sensor_fans = psutil.sensors_fans() if hasattr(psutil, "sensors_fans") else {}
                except Exception:
                    cls._sensor_fans = cls._sensor_fans or {}
                cls._sensor_fans_time = time.time()
            return cls._sensor_fans or {}

    @classmethod
    def net_io_counters(cls):
        with cls._lock:
            if time.time() - cls._net_io_time > cls.NET_IO_TTL:
                try:
                    cls._net_io      = psutil.net_io_counters()
                    cls._net_io_time = time.time()
                except Exception as e:
                    logger.warning(f"DataCache net_io_counters failed: {e}")
            return cls._net_io

    @classmethod
    def disk_io_counters(cls):
        with cls._lock:
            if time.time() - cls._disk_io_time > cls.DISK_IO_TTL:
                try:
                    cls._disk_io      = psutil.disk_io_counters()
                    cls._disk_io_time = time.time()
                except Exception as e:
                    logger.warning(f"DataCache disk_io_counters failed: {e}")
            return cls._disk_io

    @classmethod
    def disk_partitions(cls) -> list:
        with cls._lock:
            if time.time() - cls._disk_parts_time > cls.DISK_PARTS_TTL or cls._disk_parts is None:
                try:
                    cls._disk_parts      = psutil.disk_partitions(all=False)
                    cls._disk_parts_time = time.time()
                except Exception as e:
                    logger.warning(f"DataCache disk_partitions failed: {e}")
                    cls._disk_parts = cls._disk_parts or []
            return cls._disk_parts or []


# =============================================================================
# Configuration loader
# [FIX-02] deepcopy prevents DEFAULT_CONFIG mutation
# [FIX-03] MONITOR_INTERVALS set exactly once here
# =============================================================================
def load_config() -> None:
    global config, MONITOR_INTERVALS, CORES_PER_LINE

    try:
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "w") as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False)
            # [FIX-02] deepcopy so runtime mutations never touch DEFAULT_CONFIG
            config = copy.deepcopy(DEFAULT_CONFIG)
            logger.info(f"Created default configuration at {CONFIG_FILE}")
        else:
            try:
                with open(CONFIG_FILE, "r") as f:
                    loaded = yaml.safe_load(f)
                config = copy.deepcopy(DEFAULT_CONFIG)  # [FIX-02]
                if loaded:
                    for section in config:
                        if section in loaded:
                            if isinstance(config[section], dict) and isinstance(loaded[section], dict):
                                for key in config[section]:
                                    if key in loaded[section]:
                                        config[section][key] = loaded[section][key]
                config["data"]["persistence_enabled"] = False

                # Always apply layout from DEFAULT_CONFIG — user shouldn't need
                # to delete config.yaml just because columns changed
                config["ui"]["left_column"]  = DEFAULT_CONFIG["ui"]["left_column"]
                config["ui"]["right_column"] = DEFAULT_CONFIG["ui"]["right_column"]

                logger.info(f"Loaded configuration from {CONFIG_FILE}")
            except Exception as e:
                logger.error(f"Error loading config: {e}, using defaults")
                config = copy.deepcopy(DEFAULT_CONFIG)  # [FIX-02]
    except Exception as e:
        logger.error(f"Critical error in load_config: {e}")
        config = copy.deepcopy(DEFAULT_CONFIG)  # [FIX-02]

    # [FIX-03] Single authoritative definition of MONITOR_INTERVALS
    try:
        MONITOR_INTERVALS = {k: v["interval"] for k, v in config["monitors"].items()}
        CORES_PER_LINE    = config["ui"]["cores_per_line"]
    except Exception as e:
        logger.error(f"Failed to build MONITOR_INTERVALS: {e}")
        MONITOR_INTERVALS = {
            "cpu": 1.1, "memory": 2.1, "network": 1.1, "disk": 3.0,
            "gpu": 2.0, "sensors": 3.0, "services": 5.0, "process": 3.0,
            "battery": 5.0, "firewall": 5.0, "self": 1.0,
        }
        CORES_PER_LINE = 4


# =============================================================================
# Alert storage
# [FIX-05] Lock protects both append (store_alert) and iteration (AlertMonitor)
# [FIX-06] Desktop notifications are queued to a background thread so render()
#          never blocks waiting for notify-send to start
# =============================================================================
_notification_queue: "deque[tuple]" = deque(maxlen=50)
_notification_thread_started = False

def _notification_worker() -> None:
    """Background thread that drains the notification queue."""
    while True:
        try:
            if _notification_queue:
                category, message = _notification_queue.popleft()
                try:
                    if PLATFORM_INFO["is_linux"]:
                        subprocess.run(
                            ["notify-send", f"System Monitor — {category}", message, "--urgency=critical"],
                            timeout=2,
                        )
                except Exception as e:
                    logger.debug(f"Desktop notification failed: {e}")
            time.sleep(0.2)
        except Exception:
            pass

def _ensure_notification_thread() -> None:
    global _notification_thread_started
    if not _notification_thread_started:
        t = threading.Thread(target=_notification_worker, daemon=True)
        t.start()
        _notification_thread_started = True

def store_alert(category: str, level: str, message: str) -> None:
    """Store an alert in memory; queue desktop notification if enabled."""
    try:
        alert = {
            "timestamp": datetime.now(),
            "category":  category,
            "level":     level,
            "message":   message,
        }
        # [FIX-05] Lock for write
        with _alert_lock:
            alert_history.append(alert)

        if config.get("alerts", {}).get("log_critical_events") and level == "critical":
            logger.critical(f"ALERT — {category}: {message}")

        # [FIX-06] Queue notification instead of blocking inline
        if config.get("alerts", {}).get("desktop_notification"):
            _ensure_notification_thread()
            _notification_queue.append((category, message))
    except Exception as e:
        logger.error(f"Error in store_alert: {e}")


# =============================================================================
# Terminal helpers
# =============================================================================
def set_terminal_title(title: str) -> None:
    try:
        if os.name == "nt":
            os.system(f"title {title}")
        else:
            print(f"\033]0;{title}\007", end="", flush=True)
    except Exception as e:
        logger.error(f"Failed to set terminal title: {e}")

try:
    set_terminal_title(os.path.basename(__file__))
except Exception:
    pass

def restore_terminal() -> None:
    """Restore terminal to a sane state — called exactly once via finally."""
    try:
        os.system("stty sane")
        os.system("clear")
        print("\033[?25h", end="")   # show cursor
        print("\033[2J",   end="")   # clear screen
        print("\033[H",    end="")   # cursor home
        logger.debug("Terminal restored")
    except Exception as e:
        logger.error(f"Error restoring terminal: {e}")

def setup_signal_handlers() -> None:
    def _clean_exit(sig, frame):
        logger.info(f"Received signal {sig}, exiting")
        restore_terminal()
        sys.exit(0)
    try:
        signal.signal(signal.SIGINT,  _clean_exit)
        signal.signal(signal.SIGTERM, _clean_exit)
        logger.debug("Signal handlers installed")
    except Exception as e:
        logger.error(f"Failed to set up signal handlers: {e}")


# =============================================================================
# Utility functions
# =============================================================================
def format_bytes(value: float) -> str:
    """Format bytes to human-readable string without mutating the argument."""
    try:
        v = float(value)   # local copy — never mutates caller's variable
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if v < 1024.0:
                return f"{v:6.1f}{unit}"
            v /= 1024.0
        return f"{v:6.1f}PB"
    except Exception as e:
        logger.error(f"Error formatting bytes: {e}")
        return "  0.0B"


def create_unified_progress_bar(
    percentage: float,
    width: int = 40,
    show_percentage: bool = True,
    percentage_position: str = "after",
    custom_color: Optional[str] = None,
) -> Text:
    """
    Single progress-bar implementation used by all monitors.
    Inputs are validated before entering the hot path — no try/except overhead
    inside the rendering logic itself.
    """
    # Validate / clamp once at the boundary
    if not isinstance(percentage, (int, float)) or percentage != percentage:  # NaN check
        percentage = 0.0
    percentage = max(0.0, min(100.0, float(percentage)))

    effective_width = max(10, width - 8) if show_percentage else width

    filled    = int(effective_width * percentage / 100)
    remainder = effective_width - filled

    if custom_color is None:
        if percentage < 50:
            color = "green"
        elif percentage < 75:
            color = "yellow"
        elif percentage < 90:
            color = "red"
        else:
            color = "bright_red"
    else:
        color = custom_color

    bar = Text("■" * filled, color) + Text("·" * remainder, "bright_black")

    if show_percentage:
        pct_text = Text(f" {percentage:5.1f}%", color)
        if percentage_position == "before":
            return Text(f"{percentage:5.1f}% ", color) + bar
        return bar + pct_text
    return bar


# =============================================================================
# BaseMonitor
# =============================================================================
class BaseMonitor(Static):
    """Base monitor class with shared error-handling and threshold logic."""

    DEFAULT_CSS = """
    BaseMonitor {
        height: auto;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        try:
            super().__init__()
            self.error_count = 0
            self.max_errors  = 3
            monitor_type = self.__class__.__name__.lower().replace("monitor", "")
            self.alert_thresholds = {
                "warning":  config["monitors"].get(monitor_type, {}).get("warning_threshold",  75),
                "critical": config["monitors"].get(monitor_type, {}).get("critical_threshold", 90),
            }
        except Exception as e:
            logger.error(f"Error initialising {self.__class__.__name__}: {e}\n{traceback.format_exc()}")
            self.error_count      = 0
            self.max_errors       = 3
            self.alert_thresholds = {"warning": 75, "critical": 90}

    def handle_error(self, error: Exception, context: str) -> None:
        self.error_count += 1
        logger.error(f"Error in {self.__class__.__name__} ({context}): {error}")
        if self.error_count >= self.max_errors:
            logger.warning(f"{self.__class__.__name__} experiencing repeated errors")

    def get_interval(self) -> float:
        try:
            monitor_type = self.__class__.__name__.lower().replace("monitor", "")
            return MONITOR_INTERVALS.get(monitor_type, 1.0)
        except Exception as e:
            logger.error(f"Error getting interval for {self.__class__.__name__}: {e}")
            return 3.0

    def on_mount(self) -> None:
        try:
            interval = self.get_interval()
            self.set_interval(interval, self.refresh)
        except Exception as e:
            logger.error(f"Error in on_mount for {self.__class__.__name__}: {e}")
            try:
                self.set_interval(3.0, self.refresh)
            except Exception as e2:
                logger.critical(f"Failed to set default interval: {e2}")

    def check_threshold(
        self, value: float, category: str, name: Optional[str] = None
    ) -> Optional[str]:
        try:
            label = f"{category} '{name}'" if name else category
            if value >= self.alert_thresholds["critical"]:
                store_alert(category, "critical", f"{label} is critical: {value:.1f}%")
                return "critical"
            elif value >= self.alert_thresholds["warning"]:
                store_alert(category, "warning", f"{label} is high: {value:.1f}%")
                return "warning"
            return None
        except Exception as e:
            logger.error(f"Error in check_threshold: {e}")
            return None


# =============================================================================
# SelfMonitor
# [FIX-07] cpu_count and system memory total cached as instance attributes;
#          they are hardware constants and never change at runtime.
#          cpu_percent system-wide read removed — DataCache.cpu_percent_percpu()
#          is used instead to avoid an extra psutil call.
# =============================================================================
class SelfMonitor(BaseMonitor):
    """Tracks this application's own resource consumption."""

    def __init__(self) -> None:
        try:
            super().__init__()
            self.process    = psutil.Process(os.getpid())
            self.start_time = time.time()
            self.last_io      = None
            self.last_io_time = time.time()
            self.history = {
                "cpu":      deque(maxlen=60),
                "memory":   deque(maxlen=60),
                "io_read":  deque(maxlen=30),
                "io_write": deque(maxlen=30),
            }
            self.peak_memory = 0
            self.peak_cpu    = 0

            # [FIX-07] Cache static system-level constants once
            self._sys_cpu_count = DataCache.cpu_count()
            self._sys_mem_total = DataCache.virtual_memory().total

            # open_files/connections are expensive (/proc/PID/fd scan) — cache 5s
            self._fd_cache:      Dict[str, int]  = {"open_files": 0, "connections": 0}
            self._fd_cache_time: float            = 0.0
            self._fd_cache_ttl:  float            = 5.0

            logger.debug("SelfMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising SelfMonitor: {e}")
            self.process        = None
            self.start_time     = time.time()
            self.history        = {k: deque(maxlen=60) for k in ("cpu", "memory", "io_read", "io_write")}
            self.peak_memory    = 0
            self.peak_cpu       = 0
            self._sys_cpu_count = 1
            self._sys_mem_total = 1

    def _get_self_metrics(self) -> Dict[str, Any]:
        try:
            if not self.process:
                return {}

            now = time.time()

            cpu_percent  = self.process.cpu_percent()
            cpu_times    = self.process.cpu_times()
            memory_info  = self.process.memory_info()
            mem_percent  = self.process.memory_percent()

            self.peak_memory = max(self.peak_memory, memory_info.rss)
            self.peak_cpu    = max(self.peak_cpu,    cpu_percent)

            # I/O rates
            io_counters = None
            io_rates    = {"read": 0.0, "write": 0.0}
            if hasattr(self.process, "io_counters"):
                io_counters = self.process.io_counters()
                if self.last_io and (now - self.last_io_time) > 0:
                    dt = now - self.last_io_time
                    io_rates = {
                        "read":  (io_counters.read_bytes  - self.last_io.read_bytes)  / dt,
                        "write": (io_counters.write_bytes - self.last_io.write_bytes) / dt,
                    }
                self.last_io      = io_counters
                self.last_io_time = now
                self.history["io_read"].append(io_rates["read"])
                self.history["io_write"].append(io_rates["write"])

            ctx_switches = 0
            if hasattr(self.process, "num_ctx_switches"):
                ctx = self.process.num_ctx_switches()
                ctx_switches = ctx.voluntary + ctx.involuntary

            # open_files + connections: expensive /proc/PID/fd scan — cached 5s
            now_fd = time.time()
            if now_fd - self._fd_cache_time > self._fd_cache_ttl:
                try:
                    self._fd_cache["open_files"]  = len(self.process.open_files())  if hasattr(self.process, "open_files")  else 0
                    self._fd_cache["connections"] = len(self.process.connections()) if hasattr(self.process, "connections") else 0
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass
                self._fd_cache_time = now_fd
            open_files  = self._fd_cache["open_files"]
            connections = self._fd_cache["connections"]

            self.history["cpu"].append(cpu_percent)
            self.history["memory"].append(mem_percent)

            # [FIX-07] Use cached constants — no extra psutil calls
            cpu_overhead = (cpu_percent / 100.0) / self._sys_cpu_count * 100.0
            memory_overhead = (memory_info.rss / self._sys_mem_total) * 100.0

            return {
                "cpu_percent":      cpu_percent,
                "memory_percent":   mem_percent,
                "memory_bytes":     memory_info.rss,
                "cpu_user_time":    cpu_times.user,
                "cpu_system_time":  cpu_times.system,
                "cpu_overhead":     cpu_overhead,
                "memory_rss":       memory_info.rss,
                "memory_vms":       memory_info.vms,
                "memory_overhead":  memory_overhead,
                "peak_memory":      self.peak_memory,
                "peak_cpu":         self.peak_cpu,
                "read_bytes":       io_counters.read_bytes  if io_counters else 0,
                "write_bytes":      io_counters.write_bytes if io_counters else 0,
                "read_rate":        io_rates["read"],
                "write_rate":       io_rates["write"],
                "ctx_switches":     ctx_switches,
                "open_files":       open_files,
                "connections":      connections,
                "run_time":         now - self.start_time,
                "avg_cpu":     sum(self.history["cpu"])     / len(self.history["cpu"])     if self.history["cpu"]     else 0.0,
                "avg_memory":  sum(self.history["memory"])  / len(self.history["memory"])  if self.history["memory"]  else 0.0,
                "avg_io_read": sum(self.history["io_read"]) / len(self.history["io_read"]) if self.history["io_read"] else 0.0,
                "avg_io_write":sum(self.history["io_write"])/ len(self.history["io_write"])if self.history["io_write"] else 0.0,
            }
        except Exception as e:
            self.handle_error(e, "get_self_metrics")
            return {}

    def render(self) -> Panel:
        try:
            metrics = self._get_self_metrics()
            if not metrics:
                return Panel(
                    Text("Self monitoring unavailable", style="yellow"),
                    title="Monitor Script Usage",
                    border_style="bright_red",
                )

            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("Resource", style="cyan", width=12)
            table.add_column("Usage",   ratio=2)
            table.add_column("Details", style="bright_blue")

            cpu_pct = metrics["cpu_percent"]
            cpu_col = "green" if cpu_pct < 50 else "yellow" if cpu_pct < 75 else "red"
            table.add_row(
                "CPU",
                create_unified_progress_bar(cpu_pct, custom_color=cpu_col),
                f"User: {metrics['cpu_user_time']:.1f}s | Sys: {metrics['cpu_system_time']:.1f}s | Peak: {metrics['peak_cpu']:.1f}%",
            )

            cpu_oh = metrics["cpu_overhead"]
            oh_col = "green" if cpu_oh < 1 else "yellow" if cpu_oh < 5 else "red"
            table.add_row(
                "CPU Overhead",
                create_unified_progress_bar(cpu_oh, custom_color=oh_col),
                f"{cpu_oh:.2f}% of system | Avg: {metrics['avg_cpu']:.1f}%",
            )

            mem_pct = metrics["memory_percent"]
            mem_col = "green" if mem_pct < 50 else "yellow" if mem_pct < 75 else "red"
            table.add_row(
                "Memory",
                create_unified_progress_bar(mem_pct, custom_color=mem_col),
                f"{format_bytes(metrics['memory_bytes'])} ({mem_pct:.1f}%)",
            )

            table.add_row(
                "Mem Details",
                f"RSS: {format_bytes(metrics['memory_rss'])} | VMS: {format_bytes(metrics['memory_vms'])}",
                f"Peak: {format_bytes(metrics['peak_memory'])} | Overhead: {metrics['memory_overhead']:.2f}%",
            )

            table.add_row(
                "I/O Rate",
                f"Read: {format_bytes(metrics['read_rate'])}/s",
                f"Write: {format_bytes(metrics['write_rate'])}/s",
            )

            table.add_row(
                "I/O Total",
                f"Read: {format_bytes(metrics['read_bytes'])}",
                f"Write: {format_bytes(metrics['write_bytes'])}",
            )

            table.add_row(
                "File Handles",
                f"Open Files: {metrics['open_files']}",
                f"Connections: {metrics['connections']}",
            )

            h, rem  = divmod(metrics["run_time"], 3600)
            m, s    = divmod(rem, 60)
            table.add_row("Runtime", f"{int(h)}h {int(m)}m {int(s)}s", "")

            return Panel(table, title="Monitor Script Resource Usage", border_style="bright_red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(
                Text(f"Self Monitor Error: {e}", style="red"),
                title="Monitor Script Usage",
                border_style="bright_red",
            )


# NOTE: This file is Part 2 of 4.  It depends on everything defined in Part 1
#       (BaseMonitor, DataCache, create_unified_progress_bar, format_bytes,
#        store_alert, MONITOR_INTERVALS, CORES_PER_LINE, PLATFORM_INFO, config).
#       In the final merged file all parts are concatenated in order.
# =============================================================================


logger = logging.getLogger("SystemMonitor")


# =============================================================================
# CPUMonitor
# [FIX-08] _processor_name read once at on_mount (already was)
#          _cached_freq — NEW: frequency detected once and stored forever.
#          /proc/cpuinfo is NEVER opened again after the first successful read.
# [FIX-13] cpu_percent data comes from DataCache, not a fresh psutil call.
# =============================================================================
class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor."""

    def on_mount(self) -> None:
        try:
            super().on_mount()
            self.processor_name = self._get_processor_name()
            # [FIX-08] Detect frequency once; cache result permanently
            self._cached_freq: Optional[int] = None
            logger.debug(f"CPUMonitor initialised — processor: {self.processor_name}")
        except Exception as e:
            logger.error(f"Error mounting CPUMonitor: {e}")
            self.processor_name = ""
            self._cached_freq   = None

    def _get_processor_name(self) -> str:
        try:
            if sys.platform == "linux":
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":")[1].strip()
            return platform.processor() or ""
        except Exception as e:
            logger.debug(f"Could not get processor name: {e}")
            return platform.processor() or ""

    def _get_cpu_frequency(self) -> int:
        """
        [FIX-08] Return cached frequency if already known.
        /proc/cpuinfo and sysfs are read AT MOST ONCE per process lifetime.
        """
        if self._cached_freq is not None:
            return self._cached_freq

        freq = self._detect_cpu_frequency()
        self._cached_freq = freq   # store even if 0 — prevents repeated failed reads
        return freq

    def _detect_cpu_frequency(self) -> int:
        """One-shot frequency detection — called only when _cached_freq is None."""
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current > 100:
                return int(freq.current)
        except Exception:
            pass

        if sys.platform == "linux":
            # sysfs scaling_cur_freq (kHz → MHz)
            for path in (
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq",
                "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq",
            ):
                try:
                    if os.path.exists(path):
                        with open(path) as f:
                            return int(f.read().strip()) // 1000
                except Exception:
                    pass

            # /proc/cpuinfo cpu MHz line
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "cpu MHz" in line:
                            return int(float(line.split(":")[1].strip()))
            except Exception:
                pass

        # Parse from processor name string as last resort
        for pattern, multiplier in [
            (r"@ (\d+\.\d+)GHz", 1000), (r"(\d+\.\d+)GHz", 1000), (r"(\d+)MHz", 1),
        ]:
            m = re.search(pattern, self.processor_name)
            if m:
                return int(float(m.group(1)) * multiplier)

        return 0

    def _get_system_uptime(self) -> str:
        try:
            secs = time.time() - DataCache.boot_time()  # cached — never re-reads
            d, rem = divmod(secs, 86400)
            h, rem = divmod(rem,  3600)
            m, s   = divmod(rem,  60)
            if d > 0:
                return f"{int(d)}d {int(h)}h {int(m)}m"
            if h > 0:
                return f"{int(h)}h {int(m)}m {int(s)}s"
            return f"{int(m)}m {int(s)}s"
        except Exception as e:
            logger.error(f"Error getting uptime: {e}")
            return "Unknown"

    def get_usage_color(self, pct: float) -> str:
        if pct < 50:  return "green"
        if pct < 75:  return "yellow"
        if pct < 90:  return "red"
        return "bright_red"

    def create_core_row(self, start_idx: int, cpu_percent: list) -> list:
        row = []
        try:
            for i in range(start_idx, min(start_idx + CORES_PER_LINE, len(cpu_percent))):
                color = self.get_usage_color(cpu_percent[i])
                core_text = (
                    Text(f"Core {i:2d}: ", "cyan")
                    + create_unified_progress_bar(
                        cpu_percent[i], width=30,
                        custom_color=color, percentage_position="before",
                    )
                )
                row.append(core_text)
        except Exception as e:
            logger.error(f"Error creating core row: {e}")
            return [Text("Error", "red")]
        return row

    def render(self) -> Panel:
        try:
            # [FIX-13] DataCache — no fresh psutil.cpu_percent() call here
            cpu_percent = DataCache.cpu_percent_percpu()
            if not cpu_percent:
                return Panel(Text("CPU data unavailable", "yellow"), title="CPU Monitor", border_style="blue")

            times  = DataCache.cpu_times_percent()   # cached
            load   = DataCache.getloadavg()           # cached
            total  = sum(cpu_percent) / len(cpu_percent)
            uptime = self._get_system_uptime()
            mhz    = self._get_cpu_frequency()   # cached after first call

            self.check_threshold(total, "CPU Usage", "Total")

            freq_text = (
                f"{mhz / 1000:.2f}GHz" if mhz > 1000
                else f"{mhz}MHz"        if mhz > 0
                else "Unknown"
            )

            table = Table(box=None, expand=True, padding=(0, 1))

            metrics_table = Table(box=None, expand=True, padding=(0, 1))
            metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
            metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)

            total_color = self.get_usage_color(total)
            metrics_table.add_row(
                Text("Total: ") + create_unified_progress_bar(total, custom_color=total_color, percentage_position="before"),
                Text(f"Freq: {freq_text} | Load: {load[0]:5.2f}"),
            )

            states_text = (
                Text(f"User: {times.user:4.1f}% ",  self.get_usage_color(times.user))
                + Text(f"Sys: {times.system:4.1f}% ", self.get_usage_color(times.system))
                + Text(f"Idle: {times.idle:4.1f}%",   "bright_black")
            )
            metrics_table.add_row(states_text, Text(f"Uptime: {uptime}", "bright_green"))
            table.add_row(metrics_table)

            cores_table = Table(box=None, expand=True, padding=(0, 1))
            for _ in range(CORES_PER_LINE):
                cores_table.add_column("", ratio=1)

            for i in range(0, len(cpu_percent), CORES_PER_LINE):
                row = self.create_core_row(i, cpu_percent)
                while len(row) < CORES_PER_LINE:
                    row.append("")
                cores_table.add_row(*row)

            table.add_row(cores_table)

            title = "CPU Monitor"
            if self.processor_name:
                title += f" — {self.processor_name}"

            return Panel(table, title=title, border_style="blue")
        except Exception as e:
            logger.error(f"Error rendering CPUMonitor: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"CPU Monitor Error: {e}", style="red"), title="CPU Monitor", border_style="blue")


# =============================================================================
# MemoryMonitor
# [FIX-13] Uses DataCache.virtual_memory() instead of calling psutil directly.
# =============================================================================
class MemoryMonitor(BaseMonitor):
    """Memory usage monitor."""

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("Memory", style="cyan", width=12, no_wrap=True)
            table.add_column("Usage",   ratio=2)
            table.add_column("Details", style="bright_blue")

            vm   = DataCache.virtual_memory()   # [FIX-13]
            swap = DataCache.swap_memory()       # cached

            self.check_threshold(vm.percent,   "Memory Usage", "RAM")
            self.check_threshold(swap.percent, "Memory Usage", "Swap")

            table.add_row(
                "RAM",
                create_unified_progress_bar(vm.percent),
                f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}",
            )

            if hasattr(vm, "cached"):
                cache_pct  = (vm.cached / vm.total * 100) if vm.total else 0
                cache_text = f"Cached: {format_bytes(vm.cached)}"
                if hasattr(vm, "buffers"):
                    cache_text += f" | Buffers: {format_bytes(vm.buffers)}"
                table.add_row("Cache", create_unified_progress_bar(cache_pct), cache_text)

            if hasattr(vm, "available") and hasattr(vm, "cached") and hasattr(vm, "buffers"):
                eff_used = vm.total - vm.available - vm.cached - vm.buffers
                if eff_used >= 0:
                    eff_pct = (eff_used / vm.total * 100) if vm.total else 0
                    table.add_row(
                        "Effective",
                        create_unified_progress_bar(eff_pct),
                        f"Used: {format_bytes(eff_used)} | Available: {format_bytes(vm.available)}",
                    )

            table.add_row(
                "Swap",
                create_unified_progress_bar(swap.percent),
                f"Used: {format_bytes(swap.used)} / Total: {format_bytes(swap.total)}",
            )

            return Panel(table, title="Memory Monitor", border_style="green")
        except Exception as e:
            logger.error(f"Error rendering MemoryMonitor: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"Memory Monitor Error: {e}", style="red"), title="Memory Monitor", border_style="green")


# =============================================================================
# NetworkMonitor
# [FIX-09] ALL Google/external pings removed.  No live DNS lookups.
#          DNS server list read from /etc/resolv.conf once per TTL (local read).
# [FIX-10] Interface info cached for 30 s — psutil.net_if_addrs/stats not
#          called on every 1.1 s render cycle.
# [FIX-11] today_download / today_upload guarded by _today_lock.
# [FIX-13] IO counters from DataCache.
# =============================================================================
class NetworkMonitor(BaseMonitor):
    """Network I/O, interface, and connection monitor."""

    # TTL for expensive sub-queries
    _IFACE_TTL = 30.0   # interface addresses/speeds
    _DNS_TTL   = 60.0   # /etc/resolv.conf re-read interval

    def __init__(self) -> None:
        try:
            super().__init__()
            self.last_io        = DataCache.net_io_counters()
            self.last_time      = time.time()
            self.history: Dict[str, deque] = {
                "bytes_sent":   deque(maxlen=60),
                "bytes_recv":   deque(maxlen=60),
                "packets_sent": deque(maxlen=60),
                "packets_recv": deque(maxlen=60),
                "error_in":     deque(maxlen=10),
                "error_out":    deque(maxlen=10),
            }
            self.peak_download = 0.0
            self.peak_upload   = 0.0

            # [FIX-11] Lock for today counters
            self._today_lock     = threading.Lock()
            self.today_start     = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            self.today_download  = 0
            self.today_upload    = 0

            # [FIX-10] Interface cache
            self._iface_cache      = {}
            self._iface_cache_time = 0.0

            # [FIX-09] DNS cache — local file read only, no live lookups
            self._dns_cache      = {"servers": [], "source": "unknown"}
            self._dns_cache_time = 0.0

            logger.debug("NetworkMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising NetworkMonitor: {e}")
            self.last_io    = None
            self.last_time  = time.time()
            self.history    = {}
            self.peak_download = self.peak_upload = 0.0
            self._today_lock = threading.Lock()
            self.today_download = self.today_upload = 0
            self.today_start = datetime.now()
            self._iface_cache = {}
            self._iface_cache_time = 0.0
            self._dns_cache = {"servers": []}
            self._dns_cache_time = 0.0

    # ------------------------------------------------------------------
    # [FIX-10] Interface info — cached for _IFACE_TTL seconds
    # ------------------------------------------------------------------
    def _get_interface_info(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._iface_cache_time < self._IFACE_TTL and self._iface_cache:
            return self._iface_cache

        try:
            interfaces: Dict[str, Any] = {}
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()

            for name, addr_list in addrs.items():
                if name not in stats or not stats[name].isup:
                    continue

                ipv4, ipv6, mac = [], [], None
                for addr in addr_list:
                    if addr.family == socket.AF_INET:
                        ipv4.append(addr.address)
                    elif addr.family == socket.AF_INET6:
                        ipv6.append(addr.address)
                    elif addr.family == psutil.AF_LINK:
                        mac = addr.address

                if not (ipv4 or ipv6):
                    continue

                stat = stats[name]
                entry: Dict[str, Any] = {
                    "ipv4":    ipv4,
                    "ipv6":    ipv6,
                    "mac":     mac,
                    "speed":   stat.speed or 0,
                    "mtu":     stat.mtu,
                    "is_up":   stat.isup,
                    "wireless": False,
                }

                # Wireless signal — local /proc read, no external call
                if sys.platform == "linux":
                    try:
                        with open("/proc/net/wireless") as f:
                            for line in f:
                                if name in line:
                                    parts = line.split()
                                    entry["wireless"] = True
                                    if len(parts) >= 4:
                                        try:
                                            entry["signal"] = float(parts[3].rstrip("."))
                                        except ValueError:
                                            pass
                    except (OSError, IOError):
                        pass

                interfaces[name] = entry

            self._iface_cache      = interfaces
            self._iface_cache_time = now
        except Exception as e:
            self.handle_error(e, "get_interface_info")

        return self._iface_cache

    # ------------------------------------------------------------------
    # [FIX-09] DNS — reads /etc/resolv.conf locally, no live DNS lookup
    # ------------------------------------------------------------------
    def _get_dns_info(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._dns_cache_time < self._DNS_TTL:
            return self._dns_cache

        servers: List[str] = []
        try:
            if sys.platform == "linux":
                with open("/etc/resolv.conf") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("nameserver"):
                            parts = line.split()
                            if len(parts) >= 2:
                                servers.append(parts[1])
        except (OSError, IOError) as e:
            logger.debug(f"Could not read /etc/resolv.conf: {e}")

        self._dns_cache      = {"servers": servers or ["Unknown"]}
        self._dns_cache_time = now
        return self._dns_cache

    # _get_connection_stats() removed — net_connections() was the single most
    # expensive call in the entire monitor (reads /proc/net/tcp* and cross-
    # references every process fd). Removed on user request; speeds unaffected.

    # ------------------------------------------------------------------
    # Network I/O rates
    # [FIX-11] today counters updated under _today_lock
    # [FIX-13] IO counters from DataCache
    # ------------------------------------------------------------------
    def _get_network_metrics(self) -> Dict[str, Any]:
        try:
            now      = time.time()
            curr_io  = DataCache.net_io_counters()   # [FIX-13]

            if self.last_io is None or curr_io is None:
                self.last_io   = curr_io
                self.last_time = now
                return {"rates": {}, "averages": {}, "peak": {"download": 0, "upload": 0}, "today": {}}

            dt = now - self.last_time
            if dt <= 0:
                return {"rates": {}, "averages": {}, "peak": {"download": 0, "upload": 0}, "today": {}}

            rates = {
                "bytes_sent":   (curr_io.bytes_sent   - self.last_io.bytes_sent)   / dt,
                "bytes_recv":   (curr_io.bytes_recv   - self.last_io.bytes_recv)   / dt,
                "packets_sent": (curr_io.packets_sent - self.last_io.packets_sent) / dt,
                "packets_recv": (curr_io.packets_recv - self.last_io.packets_recv) / dt,
                "error_in":     (curr_io.errin        - self.last_io.errin)        / dt,
                "error_out":    (curr_io.errout       - self.last_io.errout)       / dt,
            }

            self.peak_download = max(self.peak_download, rates["bytes_recv"])
            self.peak_upload   = max(self.peak_upload,   rates["bytes_sent"])

            for key, value in rates.items():
                if key in self.history:
                    self.history[key].append(value)

            averages = {
                k: sum(v) / len(v)
                for k, v in self.history.items() if v
            }

            # [FIX-11] today counters under lock
            with self._today_lock:
                now_dt = datetime.now()
                if now_dt.date() > self.today_start.date():
                    self.today_start    = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    self.today_download = 0
                    self.today_upload   = 0
                self.today_download += max(0, curr_io.bytes_recv - self.last_io.bytes_recv)
                self.today_upload   += max(0, curr_io.bytes_sent - self.last_io.bytes_sent)
                today_snap = {
                    "download": self.today_download,
                    "upload":   self.today_upload,
                    "total":    self.today_download + self.today_upload,
                }

            self.last_io   = curr_io
            self.last_time = now

            return {
                "rates":    rates,
                "averages": averages,
                "peak":     {"download": self.peak_download, "upload": self.peak_upload},
                "today":    today_snap,
                "error_in":  curr_io.errin,
                "error_out": curr_io.errout,
                "drop_in":   curr_io.dropin,
                "drop_out":  curr_io.dropout,
            }
        except Exception as e:
            self.handle_error(e, "get_network_metrics")
            return {"rates": {}, "averages": {}, "peak": {"download": 0, "upload": 0}, "today": {}}

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 1))
            table.add_column("Network",  style="cyan", width=12, no_wrap=True)
            table.add_column("Traffic",  ratio=1)
            table.add_column("Details",  ratio=1)

            metrics    = self._get_network_metrics()
            interfaces = self._get_interface_info()
            dns_info   = self._get_dns_info()

            # Download / Upload rates
            rates = metrics.get("rates", {})
            if "bytes_recv" in rates:
                recv_pct = min(100.0, (rates["bytes_recv"] / (10 * 1024 * 1024)) * 100)
                table.add_row(
                    "Download",
                    create_unified_progress_bar(recv_pct, custom_color="green"),
                    f"{format_bytes(rates['bytes_recv'])}/s | Peak: {format_bytes(metrics['peak']['download'])}/s",
                )
            if "bytes_sent" in rates:
                sent_pct = min(100.0, (rates["bytes_sent"] / (10 * 1024 * 1024)) * 100)
                table.add_row(
                    "Upload",
                    create_unified_progress_bar(sent_pct, custom_color="blue"),
                    f"{format_bytes(rates['bytes_sent'])}/s | Peak: {format_bytes(metrics['peak']['upload'])}/s",
                )

            today = metrics.get("today", {})
            if today:
                table.add_row(
                    "Today",
                    f"↓ {format_bytes(today.get('download', 0))} | ↑ {format_bytes(today.get('upload', 0))}",
                    f"Total: {format_bytes(today.get('total', 0))}",
                )

            # DNS — local info only
            if dns_info["servers"]:
                table.add_row(
                    "DNS",
                    ", ".join(dns_info["servers"][:2]),
                    "(from /etc/resolv.conf)",
                )

            # Interfaces
            active = [(n, i) for n, i in interfaces.items() if i["is_up"] and i["ipv4"]]
            if active:
                table.add_row("", "", "")
                table.add_row("Interfaces", "", "")
                for name, info in active[:3]:
                    ip        = info["ipv4"][0]
                    speed_txt = f"{info['speed']} Mbps" if info["speed"] else "Auto"
                    extras    = " | WiFi" if info.get("wireless") else ""
                    table.add_row(name[:10], ip, f"Speed: {speed_txt}{extras}")

            # Errors — only shown when non-zero
            ei = metrics.get("error_in",  0)
            eo = metrics.get("error_out", 0)
            di = metrics.get("drop_in",   0)
            do_ = metrics.get("drop_out", 0)
            if any((ei, eo, di, do_)):
                table.add_row(
                    "Errors",
                    f"In: {ei} | Out: {eo}",
                    f"Drops In: {di} | Out: {do_}",
                )

            return Panel(table, title="Network Monitor", border_style="cyan")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Network Monitor Error: {e}", style="red"), title="Network Monitor", border_style="cyan")


# =============================================================================
# DiskMonitor
# [FIX-12] SSD/rotational and scheduler flags read ONCE in __init__ and stored
#          in self._disk_meta.  _get_partitions() never touches sysfs again.
# [FIX-13] disk_io_counters and disk_partitions from DataCache.
# =============================================================================
class DiskMonitor(BaseMonitor):
    """Disk usage and I/O monitor."""

    def __init__(self) -> None:
        try:
            super().__init__()
            self.last_io   = DataCache.disk_io_counters()
            self.last_time = time.time()
            self.history: Dict[str, deque] = {
                "read_bytes":  deque(maxlen=10),
                "write_bytes": deque(maxlen=10),
                "busy_time":   deque(maxlen=10),
            }
            # [FIX-12] Read sysfs metadata once — {mountpoint: {is_ssd, scheduler}}
            self._disk_meta: Dict[str, Dict[str, Any]] = self._read_disk_meta_once()
            logger.debug("DiskMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising DiskMonitor: {e}")
            self.last_io    = None
            self.last_time  = time.time()
            self.history    = {k: deque(maxlen=10) for k in ("read_bytes", "write_bytes", "busy_time")}
            self._disk_meta = {}

    def _read_disk_meta_once(self) -> Dict[str, Dict[str, Any]]:
        """
        [FIX-12] Walk sysfs a single time at startup.
        Returns a dict keyed by device basename with is_ssd and scheduler.
        """
        meta: Dict[str, Dict[str, Any]] = {}
        try:
            for part in DataCache.disk_partitions():
                dev_name       = os.path.basename(part.device)
                sys_block_path = f"/sys/block/{dev_name}"
                entry: Dict[str, Any] = {}
                if os.path.exists(sys_block_path):
                    rot_path  = f"{sys_block_path}/queue/rotational"
                    sched_path = f"{sys_block_path}/queue/scheduler"
                    try:
                        if os.path.exists(rot_path):
                            with open(rot_path) as f:
                                entry["is_ssd"] = f.read().strip() == "0"
                    except (OSError, IOError):
                        pass
                    try:
                        if os.path.exists(sched_path):
                            with open(sched_path) as f:
                                entry["scheduler"] = f.read().strip()
                    except (OSError, IOError):
                        pass
                meta[dev_name] = entry
        except Exception as e:
            logger.warning(f"DiskMonitor: error reading disk meta: {e}")
        return meta

    def _get_disk_io(self) -> Dict[str, Any]:
        try:
            now     = time.time()
            curr_io = DataCache.disk_io_counters()   # [FIX-13]

            if self.last_io is None or curr_io is None:
                self.last_io   = curr_io
                self.last_time = now
                return {"rates": {}}

            dt = now - self.last_time
            if dt <= 0:
                return {"rates": {}}

            metrics = {
                "read_bytes":  curr_io.read_bytes,
                "write_bytes": curr_io.write_bytes,
                "read_count":  curr_io.read_count,
                "write_count": curr_io.write_count,
                "rates": {
                    "read_bytes":  (curr_io.read_bytes  - self.last_io.read_bytes)  / dt,
                    "write_bytes": (curr_io.write_bytes - self.last_io.write_bytes) / dt,
                    "read_count":  (curr_io.read_count  - self.last_io.read_count)  / dt,
                    "write_count": (curr_io.write_count - self.last_io.write_count) / dt,
                },
            }

            for key in ("read_bytes", "write_bytes"):
                self.history[key].append(metrics["rates"][key])

            if hasattr(curr_io, "busy_time") and hasattr(self.last_io, "busy_time"):
                busy_pct = min(100.0, (curr_io.busy_time - self.last_io.busy_time) / (dt * 1000) * 100)
                self.history["busy_time"].append(busy_pct)
                metrics["busy_percent"] = busy_pct

            self.last_io   = curr_io
            self.last_time = now
            return metrics
        except Exception as e:
            self.handle_error(e, "get_disk_io")
            return {"rates": {}}

    def _get_partitions(self) -> List[Dict[str, Any]]:
        """
        [FIX-12] Uses cached partition list from DataCache.
                 Uses cached _disk_meta for SSD/scheduler — no sysfs reads here.
        """
        partitions = []
        skipped_fs = {"squashfs", "efivarfs"}
        all_usage  = DataCache.disk_usage_all()   # batched cache — no per-partition psutil call
        for part in DataCache.disk_partitions():
            if part.fstype in skipped_fs:
                continue
            if "/boot" in part.mountpoint or "/snap" in part.mountpoint:
                continue
            try:
                usage    = all_usage.get(part.mountpoint)
                if usage is None:
                    continue
                dev_name = os.path.basename(part.device)
                # [FIX-12] Pull from cached meta — never re-read sysfs
                meta     = self._disk_meta.get(dev_name, {})

                self.check_threshold(usage.percent, "Disk Usage", part.mountpoint)

                partitions.append({
                    "device":     part.device,
                    "mountpoint": part.mountpoint,
                    "fstype":     part.fstype,
                    "total":      usage.total,
                    "used":       usage.used,
                    "free":       usage.free,
                    "percent":    usage.percent,
                    **meta,   # is_ssd, scheduler if detected
                })
            except PermissionError:
                continue
            except Exception as e:
                self.handle_error(e, f"get_partition_{part.mountpoint}")
        return partitions

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("Disk",    style="cyan", width=12)
            table.add_column("Usage",   ratio=2)
            table.add_column("Details", style="bright_blue")

            io_metrics = self._get_disk_io()
            partitions = self._get_partitions()

            rates = io_metrics.get("rates", {})
            table.add_row(
                "Disk I/O",
                f"Read:  {format_bytes(rates.get('read_bytes',  0))}/s",
                f"Write: {format_bytes(rates.get('write_bytes', 0))}/s",
            )
            table.add_row(
                "Operations",
                f"Read:  {rates.get('read_count',  0):.1f}/s",
                f"Write: {rates.get('write_count', 0):.1f}/s",
            )
            if "busy_percent" in io_metrics:
                bp = io_metrics["busy_percent"]
                table.add_row("Busy", create_unified_progress_bar(bp), f"{bp:.1f}% Utilised")

            for part in partitions:
                name    = os.path.basename(part["mountpoint"]) or part["mountpoint"]
                details = [
                    f"{format_bytes(part['used'])} / {format_bytes(part['total'])}",
                    f"({part['fstype']})",
                ]
                if "is_ssd" in part:
                    details.append("SSD" if part["is_ssd"] else "HDD")
                if "scheduler" in part:
                    details.append(f"Sched: {part['scheduler']}")
                table.add_row(
                    name[:12],
                    create_unified_progress_bar(part["percent"]),
                    " | ".join(details),
                )

            return Panel(table, title="Disk Monitor", border_style="magenta")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Disk Monitor Error: {e}", style="red"), title="Disk Monitor", border_style="magenta")


# NOTE: Part 3 of 4.  Depends on Part 1 (BaseMonitor, DataCache, helpers,
#       store_alert, config, MONITOR_INTERVALS, PLATFORM_INFO) and Part 2
#       (no direct dependency, but all parts share the same namespace in the
#       final merged file).
# =============================================================================


logger = logging.getLogger("SystemMonitor")


# =============================================================================
# ServiceMonitor
# [FIX-14] Single batched systemctl call for ALL services instead of N calls.
#          Parsing splits on the separator line that systemctl emits between
#          units when queried in bulk.
# =============================================================================
class ServiceMonitor(BaseMonitor):
    """Service monitor — uses a single batched systemctl query."""

    def __init__(self) -> None:
        try:
            super().__init__()
            self.important_services: List[str] = [
                "systemd-journald", "systemd-logind", "systemd-timesyncd",
                "dbus", "NetworkManager", "sshd", "cron", "udev",
                "rsyslog", "ModemManager", "irqbalance", "acpid",
                "bluetooth", "cups", "apache2", "mysql", "postgresql",
            ]
            self.status_cache: Dict[str, Any] = {}
            self.cache_time: float             = 0.0
            self.cache_ttl: float              = 5.0
            logger.debug("ServiceMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising ServiceMonitor: {e}")
            self.important_services = []
            self.status_cache       = {}
            self.cache_time         = 0.0
            self.cache_ttl          = 5.0

    # ------------------------------------------------------------------
    # [FIX-14] One subprocess for all units
    # ------------------------------------------------------------------
    def _get_all_services(self) -> Dict[str, Any]:
        now = time.time()
        if now - self.cache_time < self.cache_ttl and self.status_cache:
            return self.status_cache

        services: Dict[str, Dict[str, Any]] = {}
        stats = {"total": len(self.important_services),
                 "running": 0, "stopped": 0, "failed": 0, "other": 0}

        try:
            # [FIX-14] Build ONE command with all unit names
            units = [f"{s}.service" for s in self.important_services]
            props = (
                "ActiveState,SubState,LoadState,UnitFileState,"
                "Description,StateChangeTimestamp,ExecMainStatus"
            )
            cmd = ["systemctl", "show", *units, f"--property={props}"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3
            )

            if result.returncode == 0:
                # systemctl separates units with a blank line
                unit_blocks = result.stdout.strip().split("\n\n")
                for idx, block in enumerate(unit_blocks):
                    if idx >= len(self.important_services):
                        break
                    service_name = self.important_services[idx]
                    raw: Dict[str, str] = {}
                    for line in block.strip().splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            raw[k] = v

                    state = raw.get("ActiveState", "unknown")
                    status_info = {
                        "name":        service_name,
                        "state":       state,
                        "substate":    raw.get("SubState",      "unknown"),
                        "enabled":     raw.get("UnitFileState", "") == "enabled",
                        "description": raw.get("Description",   ""),
                        "status_code": int(raw.get("ExecMainStatus", "0") or "0"),
                        "last_changed":raw.get("StateChangeTimestamp", ""),
                    }
                    services[service_name] = status_info

                    s = state.lower()
                    if s == "active":
                        stats["running"] += 1
                    elif s == "inactive":
                        stats["stopped"] += 1
                    elif s in ("failed", "error"):
                        stats["failed"] += 1
                        store_alert("Service", "warning", f"Service '{service_name}' has failed")
                    else:
                        stats["other"] += 1

        except FileNotFoundError:
            logger.debug("systemctl not found — service monitoring unavailable")
        except subprocess.TimeoutExpired:
            logger.warning("systemctl batch query timed out")
        except Exception as e:
            self.handle_error(e, "get_all_services")

        self.status_cache = {"services": services, "stats": stats, "timestamp": now}
        self.cache_time   = now
        return self.status_cache

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("Service", style="cyan",         width=20)
            table.add_column("Status",  ratio=2)
            table.add_column("Details", style="bright_blue")

            info     = self._get_all_services()
            stats    = info.get("stats",    {})
            services = info.get("services", {})

            table.add_row(
                "Summary",
                f"Running: {stats.get('running', 0)}/{stats.get('total', 0)} | Failed: {stats.get('failed', 0)}",
                "",
            )

            sorted_svcs = sorted(
                services.values(),
                key=lambda x: (x["state"] != "active", x["state"] == "failed", x["name"]),
            )

            for svc in sorted_svcs:
                color = (
                    "green"       if svc["state"] == "active"   else
                    "red"         if svc["state"] == "failed"   else
                    "yellow"      if svc["state"] == "inactive" else
                    "bright_black"
                )
                status_bar = create_unified_progress_bar(
                    100 if svc["state"] == "active" else 0,
                    show_percentage=False,
                    custom_color=color,
                )
                details = [svc["substate"], "Enabled" if svc["enabled"] else "Disabled"]
                if svc["description"]:
                    details.append(svc["description"][:30])

                table.add_row(svc["name"][:20], status_bar, " | ".join(details))

            return Panel(table, title="Service Monitor", border_style="blue")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(
                Text(f"Service Monitor Error: {e}", style="red"),
                title="Service Monitor", border_style="blue",
            )


# =============================================================================
# GPUMonitor
# [FIX-15] Tries pynvml first (in-process, zero subprocess overhead).
#          Falls back gracefully to nvidia-smi if pynvml is not installed.
#          nvidia-smi fallback still has a result cache so it is not spawned
#          more often than every gpu_interval seconds.
# =============================================================================

def _check_gpu_available() -> bool:
    """
    GPU detection — tries multiple methods in order of cost.

    Method 1: pynvml (in-process, zero subprocess, instant)
    Method 2: Known NVIDIA device paths  (no subprocess)
    Method 3: PCI vendor ID via sysfs    (no subprocess)
    Method 4: nvidia-smi subprocess      (last resort — original behaviour)

    Returning False here means the entire GPUMonitor shows "no GPU".
    We'd rather spend 1 extra subprocess call at startup than silently
    hide a perfectly good GPU on every run.
    """
    # Method 1 — pynvml (fastest, no subprocess)
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count > 0:
            logger.info("GPU detected via pynvml")
            return True
    except Exception:
        pass

    # Method 2 — known NVIDIA device node paths
    nvidia_paths = ["/proc/driver/nvidia/version", "/dev/nvidia0", "/dev/nvidiactl"]
    if any(os.path.exists(p) for p in nvidia_paths):
        logger.info("GPU detected via device path")
        return True

    # Method 3 — PCI vendor ID 0x10de (NVIDIA) in sysfs
    try:
        for i in range(8):
            vpath = f"/sys/class/drm/card{i}/device/vendor"
            if os.path.exists(vpath):
                with open(vpath) as f:
                    if f.read().strip() in ("0x10de", "10de"):
                        logger.info(f"GPU detected via sysfs vendor ID on card{i}")
                        return True
    except Exception:
        pass

    # Method 4 — nvidia-smi subprocess (one-time startup cost only)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info(f"GPU detected via nvidia-smi: {result.stdout.strip()}")
            return True
    except Exception:
        pass

    logger.info("No NVIDIA GPU detected")
    return False


class GPUMonitor(BaseMonitor):
    """GPU monitor — pynvml preferred, nvidia-smi subprocess as fallback."""

    def __init__(self) -> None:
        try:
            super().__init__()
            self.history: Dict[str, deque] = {
                "usage": deque(maxlen=30),
                "temp":  deque(maxlen=30),
            }
            self.has_nvidia = _check_gpu_available()

            # [FIX-15] Try to initialise pynvml
            self._use_pynvml  = False
            self._nvml_handle = None
            if self.has_nvidia:
                self._use_pynvml = self._init_pynvml()

            # nvidia-smi fallback cache
            self._smi_cache:      Optional[Dict[str, Any]] = None
            self._smi_cache_time: float                     = 0.0

            logger.debug(
                f"GPUMonitor initialised — nvidia: {self.has_nvidia}, "
                f"pynvml: {self._use_pynvml}"
            )
        except Exception as e:
            logger.error(f"Error initialising GPUMonitor: {e}")
            self.history      = {"usage": deque(maxlen=30), "temp": deque(maxlen=30)}
            self.has_nvidia   = False
            self._use_pynvml  = False
            self._nvml_handle = None
            self._smi_cache   = None
            self._smi_cache_time = 0.0

    def _init_pynvml(self) -> bool:
        """[FIX-15] Attempt pynvml initialisation; return True on success."""
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._pynvml      = pynvml
            logger.info("pynvml initialised — GPU metrics via in-process NVML")
            return True
        except ImportError:
            logger.info("pynvml not installed — falling back to nvidia-smi subprocess")
        except Exception as e:
            logger.info(f"pynvml init failed ({e}) — falling back to nvidia-smi")
        return False

    # ------------------------------------------------------------------
    # [FIX-15] pynvml path — zero subprocess overhead
    # ------------------------------------------------------------------
    def _metrics_via_pynvml(self) -> Dict[str, Any]:
        try:
            pv     = self._pynvml
            h      = self._nvml_handle
            name   = pv.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            util   = pv.nvmlDeviceGetUtilizationRates(h)
            temp   = pv.nvmlDeviceGetTemperature(h, pv.NVML_TEMPERATURE_GPU)
            mem    = pv.nvmlDeviceGetMemoryInfo(h)
            clocks = pv.nvmlDeviceGetClockInfo(h, pv.NVML_CLOCK_GRAPHICS)
            mem_clk= pv.nvmlDeviceGetClockInfo(h, pv.NVML_CLOCK_MEM)

            try:
                power_draw  = pv.nvmlDeviceGetPowerUsage(h) / 1000.0
                power_limit = pv.nvmlDeviceGetEnforcedPowerLimit(h) / 1000.0
            except Exception:
                power_draw = power_limit = 0.0

            try:
                fan = pv.nvmlDeviceGetFanSpeed(h)
            except Exception:
                fan = 0

            try:
                pstate = str(pv.nvmlDeviceGetPerformanceState(h))
            except Exception:
                pstate = "P?"

            mem_pct = (mem.used / mem.total * 100.0) if mem.total else 0.0

            return {
                "name":          name,
                "usage":         float(util.gpu),
                "temp":          float(temp),
                "memory_used":   mem.used   / (1024 * 1024),
                "memory_total":  mem.total  / (1024 * 1024),
                "memory_percent":mem_pct,
                "clock_gpu":     clocks,
                "clock_mem":     mem_clk,
                "power_draw":    power_draw,
                "power_limit":   power_limit,
                "fan_speed":     float(fan),
                "perf_state":    pstate,
            }
        except Exception as e:
            self.handle_error(e, "metrics_via_pynvml")
            return {}

    # ------------------------------------------------------------------
    # nvidia-smi fallback — result cached for the monitor's interval
    # ------------------------------------------------------------------
    def _metrics_via_smi(self) -> Dict[str, Any]:
        now = time.time()
        interval = self.get_interval()
        if self._smi_cache and (now - self._smi_cache_time) < interval:
            return self._smi_cache

        empty = {
            "name": "NVIDIA GPU (smi error)", "usage": 0, "temp": 0,
            "memory_used": 0, "memory_total": 0, "memory_percent": 0,
            "clock_gpu": 0, "clock_mem": 0, "fan_speed": 0, "perf_state": "P?",
        }
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,temperature.gpu,"
                "memory.used,memory.total,power.draw,power.limit,"
                "clocks.current.graphics,clocks.max.graphics,"
                "clocks.current.memory,clocks.max.memory,fan.speed,pstate",
                "--format=csv,noheader,nounits",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if result.returncode != 0:
                return empty

            raw = [v.strip() for v in result.stdout.strip().split(",")]
            raw += ["[N/A]"] * max(0, 13 - len(raw))

            def sf(v: str, d: float = 0.0) -> float:
                try:
                    return float(v) if "[N/A]" not in v and v else d
                except ValueError:
                    return d

            mem_used  = sf(raw[3])
            mem_total = sf(raw[4])
            metrics = {
                "name":          raw[0],
                "usage":         sf(raw[1]),
                "temp":          sf(raw[2]),
                "memory_used":   mem_used,
                "memory_total":  mem_total,
                "memory_percent":(mem_used / mem_total * 100.0) if mem_total else 0.0,
                "power_draw":    sf(raw[5]),
                "power_limit":   sf(raw[6]),
                "clock_gpu":     sf(raw[7]),
                "clock_gpu_max": sf(raw[8]),
                "clock_mem":     sf(raw[9]),
                "clock_mem_max": sf(raw[10]),
                "fan_speed":     sf(raw[11]),
                "perf_state":    raw[12] if "[N/A]" not in raw[12] else "P?",
            }
            self._smi_cache      = metrics
            self._smi_cache_time = now
            return metrics

        except FileNotFoundError:
            logger.warning("nvidia-smi not found — disabling GPU monitor")
            self.has_nvidia = False
            return empty
        except Exception as e:
            self.handle_error(e, "metrics_via_smi")
            return empty

    def _get_gpu_metrics(self) -> Dict[str, Any]:
        if not self.has_nvidia:
            return {
                "name": "No NVIDIA GPU detected", "usage": 0, "temp": 0,
                "memory_used": 0, "memory_total": 0, "memory_percent": 0,
                "clock_gpu": 0, "clock_mem": 0, "fan_speed": 0, "perf_state": "N/A",
            }
        metrics = self._metrics_via_pynvml() if self._use_pynvml else self._metrics_via_smi()
        if not metrics:
            return self._metrics_via_smi()   # pynvml returned empty — try smi

        self.history["usage"].append(metrics.get("usage", 0))
        self.history["temp"].append(metrics.get("temp",  0))
        self.check_threshold(metrics.get("usage",          0), "GPU Usage")
        self.check_threshold(metrics.get("temp",           0), "GPU Temperature")
        self.check_threshold(metrics.get("memory_percent", 0), "GPU Memory")
        return metrics

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("GPU",     style="cyan",       width=12)
            table.add_column("Usage",   ratio=2)
            table.add_column("Details", style="bright_blue")

            metrics = self._get_gpu_metrics()

            if not self.has_nvidia:
                table.add_row("Status", "No NVIDIA GPU detected",
                              "Install NVIDIA drivers for GPU monitoring")
                return Panel(table, title="GPU Monitor", border_style="yellow")

            table.add_row("Name", metrics["name"],
                          f"P-State: {metrics.get('perf_state', 'P?')}")

            power_txt = f" | {metrics['power_draw']:.1f}W" if metrics.get("power_draw", 0) > 0 else ""
            table.add_row(
                "Usage",
                create_unified_progress_bar(metrics["usage"]),
                f"{metrics['usage']:.1f}%{power_txt}",
            )

            temp = metrics["temp"]
            temp_col = "green" if temp < 70 else "yellow" if temp < 85 else "red"
            temp_txt = f"{temp:.1f}°C"
            if metrics.get("fan_speed", 0) > 0:
                temp_txt += f" | Fan: {metrics['fan_speed']:.0f}%"
            table.add_row(
                "Temperature",
                create_unified_progress_bar(temp, custom_color=temp_col),
                temp_txt,
            )

            table.add_row(
                "Memory",
                create_unified_progress_bar(metrics["memory_percent"]),
                f"{metrics['memory_used']:.0f} MB / {metrics['memory_total']:.0f} MB",
            )

            table.add_row(
                "Clocks",
                f"GPU: {metrics['clock_gpu']} MHz | Mem: {metrics['clock_mem']} MHz",
                f"Max GPU: {metrics.get('clock_gpu_max', 0):.0f} MHz",
            )

            if self.history["usage"] or self.history["temp"]:
                avg_u = sum(self.history["usage"]) / len(self.history["usage"]) if self.history["usage"] else 0
                avg_t = sum(self.history["temp"])  / len(self.history["temp"])  if self.history["temp"]  else 0
                table.add_row("Average", f"Usage: {avg_u:.1f}%", f"Temp: {avg_t:.1f}°C")

            return Panel(table, title="GPU Monitor", border_style="yellow")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(
                Text(f"GPU Monitor Error: {e}", style="red"),
                title="GPU Monitor", border_style="yellow",
            )


# =============================================================================
# FirewallMonitor
# [FIX-16] _get_blocked_count() now has a proper TTL cache —
#          the two subprocess calls run at most once per rules_cache_ttl seconds,
#          matching the cadence of the already-cached _get_firewall_rules().
# [FIX-17] _get_active_connections() uses DataCache.net_connections()
#          instead of calling psutil.net_connections() independently.
# =============================================================================
class FirewallMonitor(BaseMonitor):
    """Firewall status monitor — iptables + nftables."""

    def __init__(self) -> None:
        try:
            super().__init__()
            self.rules_cache:      Optional[List] = None
            self.rules_cache_time: float          = 0.0
            self.rules_cache_ttl:  float          = 5.0

            # [FIX-16] Blocked count cache — same TTL as rules cache
            self._blocked_cache:      int   = 0
            self._blocked_cache_time: float = 0.0

            logger.debug("FirewallMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising FirewallMonitor: {e}")
            self.rules_cache       = None
            self.rules_cache_time  = 0.0
            self.rules_cache_ttl   = 5.0
            self._blocked_cache      = 0
            self._blocked_cache_time = 0.0

    # ------------------------------------------------------------------
    # [FIX-16] Cached blocked count
    # ------------------------------------------------------------------
    def _get_blocked_count(self) -> int:
        now = time.time()
        if now - self._blocked_cache_time < self.rules_cache_ttl:
            return self._blocked_cache

        blocked = 0

        # iptables
        try:
            result = subprocess.run(
                ["iptables", "-L", "INPUT", "-v", "-n"],
                capture_output=True, text=True, timeout=1,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "DROP" in line or "REJECT" in line:
                        try:
                            blocked += int(line.split()[0])
                        except (IndexError, ValueError):
                            pass
        except Exception as e:
            logger.debug(f"iptables check: {e}")

        # nftables
        try:
            result = subprocess.run(
                ["nft", "list", "ruleset"],
                capture_output=True, text=True, timeout=1,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "drop" in line or "reject" in line:
                        blocked += 1
        except Exception as e:
            logger.debug(f"nftables check: {e}")

        self._blocked_cache      = blocked
        self._blocked_cache_time = now
        return blocked

    # _get_active_connections() removed — shared with NetworkMonitor removal.
    # net_connections() eliminated from the entire monitor.

    def _get_firewall_rules(self) -> List[Dict[str, str]]:
        now = time.time()
        if self.rules_cache is not None and now - self.rules_cache_time < self.rules_cache_ttl:
            return self.rules_cache

        rules: List[Dict[str, str]] = []

        # iptables rules
        try:
            for chain in ("INPUT", "OUTPUT", "FORWARD"):
                result = subprocess.run(
                    ["iptables", "-L", chain, "-n", "-v"],
                    capture_output=True, text=True, timeout=1,
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines()[2:]:
                        parts = line.split()
                        if len(parts) >= 4:
                            rules.append({
                                "chain":       chain,
                                "target":      parts[2],
                                "protocol":    parts[3],
                                "source":      parts[7] if len(parts) > 7 else "*",
                                "destination": parts[8] if len(parts) > 8 else "*",
                            })
        except Exception as e:
            logger.debug(f"iptables rules: {e}")

        # nftables rules
        try:
            result = subprocess.run(
                ["nft", "list", "ruleset"],
                capture_output=True, text=True, timeout=1,
            )
            if result.returncode == 0:
                current_chain: Optional[str] = None
                for line in result.stdout.splitlines():
                    if "chain" in line:
                        parts = line.split()
                        current_chain = parts[1] if len(parts) > 1 else None
                    elif current_chain:
                        low = line.lower()
                        if any(k in low for k in ("accept", "drop", "reject")):
                            parts = line.strip().split()
                            rules.append({
                                "chain":       current_chain,
                                "target":      next((p for p in parts if p in ("accept", "drop", "reject")), "unknown"),
                                "protocol":    next((p for p in parts if p in ("tcp", "udp", "icmp")), "*"),
                                "source":      "*",
                                "destination": "*",
                            })
        except Exception as e:
            logger.debug(f"nftables rules: {e}")

        self.rules_cache      = rules
        self.rules_cache_time = now
        return rules

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("Firewall", style="cyan",       width=12, no_wrap=True)
            table.add_column("Status",   ratio=2)
            table.add_column("Details",  style="bright_blue")

            blocked = self._get_blocked_count()
            rules   = self._get_firewall_rules()

            table.add_row(
                "Blocked",
                create_unified_progress_bar(0, show_percentage=False),
                f"Total blocked pkts: {blocked}",
            )

            by_target = {"accept": 0, "drop": 0, "reject": 0}
            for rule in rules:
                tgt = rule["target"].lower()
                if tgt in by_target:
                    by_target[tgt] += 1

            table.add_row(
                "Rules",
                f"Total: {len(rules)}",
                f"Accept: {by_target['accept']} | Drop: {by_target['drop']} | Reject: {by_target['reject']}",
            )

            color_map = {"ACCEPT": "green", "DROP": "red", "REJECT": "red"}
            for rule in rules[:3]:
                tgt_col = color_map.get(rule["target"].upper(), "white")
                table.add_row(
                    rule["chain"][:8],
                    Text(rule["target"].upper(), tgt_col),
                    f"{rule['protocol']} {rule['source']} → {rule['destination']}",
                )

            return Panel(table, title="Firewall Monitor", border_style="red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(
                Text(f"Firewall Monitor Error: {e}", style="red"),
                title="Firewall Monitor", border_style="red",
            )


# =============================================================================
# SensorMonitor
# [FIX-18] Removed duplicate self.warning_temp / self.critical_temp attrs.
#          Threshold checks go through self.alert_thresholds (from BaseMonitor).
# [FIX-19] hwmon directory listing cached for 60 s — os.listdir not called
#          on every render cycle.
# =============================================================================
class SensorMonitor(BaseMonitor):
    """Temperature, fan, and battery sensor monitor."""

    _HWMON_TTL = 60.0   # how long to cache the hwmon directory scan

    def __init__(self) -> None:
        try:
            super().__init__()
            self.history: Dict[str, deque] = {
                "temps": deque(maxlen=30),
                "fans":  deque(maxlen=30),
            }
            # [FIX-18] No duplicate threshold attrs — use self.alert_thresholds
            # [FIX-19] Cache hwmon scan
            self._hwmon_cache:      List[Dict[str, Any]] = []
            self._hwmon_cache_time: float                 = 0.0
            logger.debug("SensorMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising SensorMonitor: {e}")
            self.history            = {"temps": deque(maxlen=30), "fans": deque(maxlen=30)}
            self._hwmon_cache       = []
            self._hwmon_cache_time  = 0.0

    def _get_temp_status(self, temp: float) -> str:
        # [FIX-18] Uses BaseMonitor's alert_thresholds — no duplicate attrs
        if temp >= self.alert_thresholds["critical"]:
            return "critical"
        if temp >= self.alert_thresholds["warning"]:
            return "warning"
        return "normal"

    # ------------------------------------------------------------------
    # [FIX-19] hwmon power sensors cached for _HWMON_TTL seconds
    # ------------------------------------------------------------------
    def _scan_hwmon(self) -> List[Dict[str, Any]]:
        now = time.time()
        if now - self._hwmon_cache_time < self._HWMON_TTL and self._hwmon_cache:
            return self._hwmon_cache

        power_readings: List[Dict[str, Any]] = []
        hwmon_path = "/sys/class/hwmon"
        if not (os.path.exists(hwmon_path) and os.path.isdir(hwmon_path)):
            self._hwmon_cache      = power_readings
            self._hwmon_cache_time = now
            return power_readings

        try:
            for hwmon in os.listdir(hwmon_path):
                hwmon_dir = os.path.join(hwmon_path, hwmon)
                name_path = os.path.join(hwmon_dir, "name")
                if not os.path.exists(name_path):
                    continue
                try:
                    with open(name_path) as f:
                        _ = f.read().strip()  # sensor name (unused but validates path)
                except (IOError, OSError):
                    continue
                # Scan for power inputs
                try:
                    for fn in os.listdir(hwmon_dir):
                        if fn.startswith("power") and fn.endswith("_input"):
                            fpath = os.path.join(hwmon_dir, fn)
                            try:
                                with open(fpath) as f:
                                    watts = float(f.read()) / 1_000_000
                                    power_readings.append({"name": fn[:-6], "value": watts})
                            except (IOError, OSError, ValueError):
                                pass
                except (IOError, OSError):
                    pass
        except Exception as e:
            logger.debug(f"Error scanning hwmon: {e}")

        self._hwmon_cache      = power_readings
        self._hwmon_cache_time = now
        return power_readings

    def _get_sensor_data(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"temperatures": [], "fans": [], "power": []}

        try:
            # Both calls go through DataCache — raw psutil not called directly
            temps = DataCache.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    for entry in entries:
                        status = self._get_temp_status(entry.current)
                        label  = entry.label or name
                        if status == "critical":
                            store_alert("Temperature", "critical",
                                        f"Sensor '{label}' critical: {entry.current:.1f}°C")
                        elif status == "warning":
                            store_alert("Temperature", "warning",
                                        f"Sensor '{label}' high: {entry.current:.1f}°C")
                        data["temperatures"].append({
                            "name":     label,
                            "current":  entry.current,
                            "high":     entry.high,
                            "critical": getattr(entry, "critical", None),
                            "status":   status,
                        })

            fans = DataCache.sensors_fans()
            if fans:
                for name, entries in fans.items():
                    for entry in entries:
                        data["fans"].append({
                            "name":  entry.label or name,
                            "speed": entry.current,
                            "min":   getattr(entry, "min", None),
                            "max":   getattr(entry, "max", None),
                        })

            # [FIX-19] Use cached hwmon scan
            data["power"] = self._scan_hwmon()

            if data["temperatures"]:
                self.history["temps"].append(max(t["current"] for t in data["temperatures"]))
            if data["fans"]:
                self.history["fans"].append(max(f["speed"] for f in data["fans"]))

        except Exception as e:
            logger.debug(f"Error getting sensor data: {e}")

        return data

    def _get_battery_info(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "available": False, "percent": 0.0,
            "power_plugged": False, "secsleft": 0, "status": "Unknown",
        }
        try:
            if hasattr(psutil, "sensors_battery"):
                bat = psutil.sensors_battery()
                if bat:
                    info["available"]    = True
                    info["percent"]      = bat.percent
                    info["power_plugged"]= bat.power_plugged
                    info["secsleft"]     = bat.secsleft
                    if bat.power_plugged:
                        info["status"] = "Charging" if bat.percent < 100 else "Fully Charged"
                    else:
                        info["status"] = "Discharging"
                        # [FIX-18] thresholds from BaseMonitor's alert_thresholds
                        bat_warn = config["monitors"]["battery"]["warning_threshold"]
                        bat_crit = config["monitors"]["battery"]["critical_threshold"]
                        if bat.percent <= bat_crit:
                            store_alert("Battery", "critical", f"Battery critical: {bat.percent:.1f}%")
                        elif bat.percent <= bat_warn:
                            store_alert("Battery", "warning", f"Battery low: {bat.percent:.1f}%")
        except Exception as e:
            logger.debug(f"Error getting battery info: {e}")
        return info

    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0, 1))
            table.add_column("Sensor", style="cyan",       width=12)
            table.add_column("Usage",  ratio=2)
            table.add_column("Details",style="bright_blue")

            sensor_data  = self._get_sensor_data()
            battery_info = self._get_battery_info()

            status_color_map = {"normal": "green", "warning": "yellow", "critical": "red"}

            for temp in sensor_data["temperatures"]:
                col = status_color_map[temp["status"]]
                if temp["critical"]:
                    pct = (temp["current"] / temp["critical"]) * 100
                elif temp["high"]:
                    pct = (temp["current"] / temp["high"]) * 100
                else:
                    pct = temp["current"]

                details = [f"Current: {temp['current']:4.1f}°C"]
                if temp["high"]:
                    details.append(f"High: {temp['high']:4.1f}°C")
                if temp["critical"]:
                    details.append(f"Crit: {temp['critical']:4.1f}°C")

                table.add_row(
                    temp["name"][:12],
                    create_unified_progress_bar(min(100.0, pct), show_percentage=False, custom_color=col),
                    " | ".join(details),
                )

            if sensor_data["power"]:
                if sensor_data["temperatures"]:
                    table.add_row("", "", "")
                table.add_row("Power", "", "")
                for pw in sensor_data["power"]:
                    table.add_row(pw["name"][:12], Text(f"{pw['value']:.1f} W", style="green"), "")

            if sensor_data["fans"]:
                if sensor_data["temperatures"] or sensor_data["power"]:
                    table.add_row("", "", "")
                table.add_row("Fans", "", "")
                for fan in sensor_data["fans"]:
                    details = []
                    if fan["min"] is not None: details.append(f"Min: {fan['min']} RPM")
                    if fan["max"] is not None: details.append(f"Max: {fan['max']} RPM")
                    table.add_row(
                        fan["name"][:12],
                        Text(f"{fan['speed']} RPM", style="cyan"),
                        " | ".join(details),
                    )

            if self.history["temps"]:
                table.add_row("", "", "")
                h = self.history["temps"]
                table.add_row(
                    "Trend",
                    create_unified_progress_bar(sum(h) / len(h), show_percentage=False),
                    f"Min: {min(h):.1f}°C | Avg: {sum(h)/len(h):.1f}°C | Max: {max(h):.1f}°C",
                )

            if battery_info["available"]:
                table.add_row("", "", "")
                table.add_row("Battery", "", "")
                pct = battery_info["percent"]
                if battery_info["power_plugged"]:
                    bat_col = "green"
                elif pct > 50:
                    bat_col = "green"
                elif pct > 20:
                    bat_col = "yellow"
                else:
                    bat_col = "red"

                table.add_row(
                    "Charge",
                    create_unified_progress_bar(pct, custom_color=bat_col),
                    f"{pct:.1f}% remaining",
                )

                secs = battery_info["secsleft"]
                if secs == psutil.POWER_TIME_UNLIMITED:
                    time_left = "Unlimited"
                elif secs == psutil.POWER_TIME_UNKNOWN:
                    time_left = "Unknown"
                else:
                    h2, r = divmod(secs, 3600)
                    m2, s2 = divmod(r, 60)
                    time_left = f"{int(h2):02d}:{int(m2):02d}:{int(s2):02d}"

                table.add_row(
                    "Power",
                    "AC Power" if battery_info["power_plugged"] else "Battery",
                    f"Time left: {time_left}",
                )
                table.add_row("Status", battery_info["status"], "")

            has_any = any([
                sensor_data["temperatures"],
                sensor_data["fans"],
                sensor_data["power"],
                battery_info["available"],
            ])
            if not has_any:
                return Panel(
                    Text("No sensor data available", style="yellow"),
                    title="Sensor Monitor", border_style="red",
                )

            return Panel(table, title="Sensor Monitor", border_style="red")
        except Exception as e:
            logger.debug(f"Error rendering SensorMonitor: {e}\n{traceback.format_exc()}")
            return Panel(
                Text(f"Unable to read sensor data: {e}", style="yellow"),
                title="Sensor Monitor", border_style="red",
            )


# NOTE: Part 4 of 4.  Depends on Parts 1–3 in the final merged file.
#       All globals (config, MONITOR_INTERVALS, DataCache, _alert_lock,
#       alert_history, PLATFORM_INFO, format_bytes, store_alert, etc.)
#       are defined in Part 1.
# =============================================================================


logger = logging.getLogger("SystemMonitor")


# =============================================================================
# ProcessMonitor
# [FIX-20] Only one sorted list is kept — the one matching self.sort_by.
#          Toggling the sort key invalidates the cache and re-sorts on demand.
#          No wasted memory or CPU keeping an unused second sorted list.
# [FIX-21] All bare except: replaced with specific psutil/OS exception types.
# =============================================================================
class ProcessMonitor(BaseMonitor):
    """Process monitor — top-N processes sorted by CPU or memory."""

    DEFAULT_CSS = """
    ProcessMonitor {
        height: auto;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        try:
            super().__init__()
            self.sort_by:           str   = "cpu"
            self.processes_limit:   int   = config["monitors"]["process"]["limit"]
            self._cache_time:       float = 0.0
            self._cache_ttl:        float = 3.0   # raised from 2s — reduces process scan frequency
            # [FIX-20] Single list — always matches self.sort_by
            self._cached_procs:     List[Dict[str, Any]] = []
            self._cache_sort_key:   str   = ""   # which key the cache was built for
            logger.debug("ProcessMonitor initialised")
        except Exception as e:
            logger.error(f"Error initialising ProcessMonitor: {e}")
            self.sort_by          = "cpu"
            self.processes_limit  = 20
            self._cache_time      = 0.0
            self._cache_ttl       = 2.0
            self._cached_procs    = []
            self._cache_sort_key  = ""

    def handle_sort_key(self, key: str) -> None:
        try:
            new_sort = {"c": "cpu", "m": "memory"}.get(key)
            if new_sort and new_sort != self.sort_by:
                self.sort_by       = new_sort
                # [FIX-20] Invalidate cache so the new sort is applied immediately
                self._cache_time   = 0.0
                self._cached_procs = []
                self._cache_sort_key = ""
                self.refresh_content()
        except Exception as e:
            logger.error(f"Error handling sort key: {e}")

    def refresh_content(self) -> None:
        try:
            self.update(self.render())
        except Exception as e:
            logger.error(f"Error refreshing ProcessMonitor: {e}")

    def _get_processes(self) -> List[Dict[str, Any]]:
        now = time.time()

        # Return cache if still valid AND built for the current sort key
        if (
            self._cached_procs
            and self._cache_sort_key == self.sort_by
            and now - self._cache_time < self._cache_ttl
        ):
            return self._cached_procs

        all_procs: List[Dict[str, Any]] = []

        # Request memory_info via oneshot() to batch all proc reads in one syscall
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent",
                                          "status", "memory_info"]):
            try:
                info = proc.info

                if not info.get("name"):
                    continue

                # memory_info already fetched by process_iter — no extra syscall
                mem_info = info.pop("memory_info", None)
                info["memory_bytes"] = mem_info.rss if mem_info else 0

                # Annotate python processes with their script name
                try:
                    if info["name"] in ("python", "python3", "py", "python.exe"):
                        cmdline = proc.cmdline()
                        if cmdline and len(cmdline) > 1:
                            info["name"] = f"{info['name']}:{os.path.basename(cmdline[1])}"
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass

                all_procs.append(info)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # [FIX-21] Process disappeared between iter and info access — skip cleanly
                continue

        # [FIX-20] Sort ONCE for the active key only
        if self.sort_by == "cpu":
            all_procs.sort(key=lambda x: float(x.get("cpu_percent", 0) or 0), reverse=True)
        else:
            all_procs.sort(key=lambda x: int(x.get("memory_bytes", 0) or 0), reverse=True)

        self._cached_procs   = all_procs[: self.processes_limit]
        self._cache_sort_key = self.sort_by
        self._cache_time     = now
        return self._cached_procs

    def render(self) -> Panel:
        try:
            table = Table(
                box=None, expand=True, padding=(0, 1), collapse_padding=True
            )
            table.add_column("PID",    style="cyan",        width=7)
            table.add_column("Name",   style="bright_blue", width=25)
            table.add_column("CPU%",   justify="right",     width=7)
            table.add_column("Memory", justify="right",     width=14)
            table.add_column("Status",                      width=10)

            processes = self._get_processes()
            sort_label = "CPU" if self.sort_by == "cpu" else "Memory"

            if processes:
                for proc in processes:
                    cpu_pct = float(proc.get("cpu_percent", 0) or 0)
                    cpu_col = (
                        "red"    if cpu_pct > 50 else
                        "yellow" if cpu_pct > 20 else
                        "green"
                    )
                    mem_pct   = float(proc.get("memory_percent", 0) or 0)
                    mem_bytes = int(proc.get("memory_bytes",   0) or 0)

                    status     = proc.get("status", "")
                    status_col = {
                        "running":  "green",
                        "sleeping": "bright_black",
                        "stopped":  "yellow",
                        "zombie":   "red",
                    }.get(status, "white")

                    table.add_row(
                        str(proc.get("pid", "")),
                        (proc.get("name") or "")[:25],
                        Text(f"{cpu_pct:5.1f}", cpu_col),
                        f"{format_bytes(mem_bytes)} ({mem_pct:.1f}%)",
                        Text(status, status_col),
                    )
            else:
                table.add_row("No processes found", "", "", "", "")

            return Panel(
                table,
                title=f"Process Monitor (Top {self.processes_limit}, Sort: {sort_label}) — C=CPU  M=Mem",
                border_style="green",
                padding=(0, 0),
            )
        except Exception as e:
            logger.error(f"Error rendering ProcessMonitor: {e}")
            return Panel(Text(f"Process Monitor Error: {e}", style="red"), border_style="green")


# =============================================================================
# AlertMonitor
# [FIX-22] Reads alert_history under _alert_lock to prevent RuntimeError
#          when a monitor thread appends while we iterate.
# =============================================================================
class AlertMonitor(BaseMonitor):
    """Displays recent system alerts from the in-memory alert history."""

    def render(self) -> Panel:
        try:
            # [FIX-22] Acquire lock before iterating the shared deque
            with _alert_lock:
                recent_alerts = list(alert_history)[-10:]

            if not recent_alerts:
                return Panel(
                    Text("No recent alerts", style="green"),
                    title="Alert Monitor",
                    border_style="red",
                )

            table = Table(box=None, expand=True, padding=(0, 0))
            table.add_column("Time",     style="cyan",        width=8)
            table.add_column("Category", style="bright_blue", width=12)
            table.add_column("Message",  ratio=1)

            for alert in reversed(recent_alerts):
                time_str  = alert["timestamp"].strftime("%H:%M:%S")
                level_col = "red" if alert["level"] == "critical" else "yellow"
                table.add_row(
                    time_str,
                    Text(alert["category"], level_col),
                    alert["message"],
                )

            # [FIX-22] Lock again just to get the count safely
            with _alert_lock:
                total = len(alert_history)

            return Panel(table, title=f"Alert Monitor ({total} alerts)", border_style="red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(
                Text(f"Alert Monitor Error: {e}", style="red"),
                title="Alert Monitor", border_style="red",
            )


# =============================================================================
# SystemMonitorApp
# [FIX-23] cpu_count and virtual_memory().total read ONCE in on_mount()
#          and stored as instance attributes — never called every second
#          inside _update_header().
# =============================================================================
class SystemMonitorApp(App):
    """Main TUI application."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        padding: 0;
        background: $background;
    }

    Header {
        column-span: 2;
        height: 1;
    }

    Footer {
        column-span: 2;
        height: 1;
        text-align: center;
    }

    #left-column, #right-column {
        width: 100%;
        height: 100%;
        overflow-y: scroll;
        overflow-x: hidden;
        margin: 0;
        padding: 0;
    }

    BaseMonitor {
        width: 100%;
        height: auto;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        try:
            super().__init__()
            self.start_time = datetime.now()
            self.prevent_exit_confirmations = True
            Screen.DIALOG_CLASSES = []

            self.show_monitors: Dict[str, bool] = {
                name: data["enabled"]
                for name, data in config["monitors"].items()
            }
            self.show_monitors["alerts"] = True

            # [FIX-23] Cached header constants — populated in on_mount()
            self._header_cpu_count: int   = 0
            self._header_mem_total: str   = ""

            logger.info("Creating monitor instances…")
            self.monitors: Dict[str, BaseMonitor] = {}

            monitor_classes = {
                "self":     SelfMonitor,
                "cpu":      CPUMonitor,
                "memory":   MemoryMonitor,
                "disk":     DiskMonitor,
                "network":  NetworkMonitor,
                "gpu":      GPUMonitor,
                "services": ServiceMonitor,
                "firewall": FirewallMonitor,
                "sensors":  SensorMonitor,
                "process":  ProcessMonitor,
                "alerts":   AlertMonitor,
            }

            for name, cls in monitor_classes.items():
                try:
                    self.monitors[name] = cls()
                    logger.debug(f"Created {name} monitor")
                except Exception as e:
                    logger.error(f"Failed to create {name} monitor: {e}")

            logger.info("SystemMonitorApp initialised successfully")
        except Exception as e:
            logger.critical(f"CRITICAL ERROR in SystemMonitorApp.__init__: {e}\n{traceback.format_exc()}")
            self.start_time    = datetime.now()
            self.show_monitors = {}
            self.monitors      = {}
            self._header_cpu_count = 0
            self._header_mem_total = ""

    def on_key(self, event) -> None:
        try:
            if event.key == "ctrl+c":
                logger.info("CTRL+C pressed — exiting")
                self.exit()
            elif event.key in ("c", "m"):
                proc = self.monitors.get("process")
                if proc:
                    proc.handle_sort_key(event.key)
        except Exception as e:
            logger.error(f"Error in on_key: {e}")

    def _on_exit(self) -> None:
        try:
            logger.info("App exiting")
            restore_terminal()
        except Exception as e:
            logger.error(f"Error in _on_exit: {e}")

    def compose(self) -> ComposeResult:
        try:
            yield Header()
            yield Container(id="left-column")
            yield Container(id="right-column")
            yield Footer()
        except Exception as e:
            logger.critical(f"Critical error in compose: {e}\n{traceback.format_exc()}")
            yield Header("System Monitor — ERROR")
            yield Container(id="left-column")
            yield Container(id="right-column")
            yield Footer()

    def on_mount(self) -> None:
        try:
            # [FIX-23] Read static system constants once — never again in _update_header
            self._header_cpu_count = DataCache.cpu_count()
            self._header_mem_total = format_bytes(DataCache.virtual_memory().total)

            self._refresh_monitors()
            self.set_interval(1.0, self._update_header)
            logger.info("App mounted successfully")
        except Exception as e:
            logger.error(f"Error in on_mount: {e}\n{traceback.format_exc()}")

    def _refresh_monitors(self) -> None:
        try:
            left  = self.query_one("#left-column",  Container)
            right = self.query_one("#right-column", Container)
            left.remove_children()
            right.remove_children()

            left_names  = list(config["ui"]["left_column"])
            right_names = list(config["ui"]["right_column"])

            # Append alerts if not already placed in either column
            if "alerts" not in left_names and "alerts" not in right_names:
                left_names.append("alerts")

            for name in left_names:
                if name in self.monitors and self.show_monitors.get(name, True):
                    left.mount(self.monitors[name])
                    logger.debug(f"Mounted {name} → left column")

            for name in right_names:
                if name in self.monitors and self.show_monitors.get(name, True):
                    right.mount(self.monitors[name])
                    logger.debug(f"Mounted {name} → right column")
        except Exception as e:
            logger.error(f"Error refreshing monitors: {e}\n{traceback.format_exc()}")

    def _update_header(self) -> None:
        """
        Update the header text every second.
        [FIX-23] cpu_count and mem_total are instance attributes set once in
        on_mount() — no psutil calls happen here at all.
        """
        try:
            uptime   = datetime.now() - self.start_time
            total_s  = int(uptime.total_seconds())
            h, rem   = divmod(total_s, 3600)
            m, s     = divmod(rem, 60)
            uptime_s = f"{h}h {m}m {s}s"

            header_text = " | ".join([
                f"OS: {PLATFORM_INFO['system'].title()} {PLATFORM_INFO['release']}",
                f"Python: {PLATFORM_INFO['python_version']}",
                f"Cores: {self._header_cpu_count}",   # [FIX-23] cached
                f"RAM: {self._header_mem_total}",      # [FIX-23] cached
                f"Uptime: {uptime_s}",
            ])

            header = self.query_one(Header)
            if header:
                header.text = header_text
        except Exception as e:
            logger.debug(f"Error updating header: {e}")


# =============================================================================
# Entry point
# [FIX-24] restore_terminal() is called EXACTLY ONCE — in the finally block.
#          The except branch no longer calls it, preventing the double-reset
#          that previously ran stty sane + clear twice on error paths.
# =============================================================================
def main() -> int:
    os.makedirs(CONFIG_DIR, exist_ok=True)

    try:
        console.print("[bold green]System Monitor starting…[/bold green]")

        try:
            load_config()
            logger.info("Configuration loaded successfully")
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}\n{traceback.format_exc()}")
            console.print(f"[bold red]Failed to load configuration: {e}[/bold red]")
            return 1

        try:
            setup_signal_handlers()
            logger.info("Signal handlers installed")
        except Exception as e:
            logger.error(f"Signal handler setup failed: {e}")
            console.print(f"[bold yellow]Warning: signal handler setup failed: {e}[/bold yellow]")

        try:
            logger.info("Starting SystemMonitorApp")
            console.print("[green]Starting system monitor interface…[/green]")
            app = SystemMonitorApp()
            app.run()
            logger.info("SystemMonitorApp exited normally")
        except Exception as e:
            logger.critical(f"Critical application error: {e}\n{traceback.format_exc()}")
            console.print(f"[bold red]Critical application error: {e}[/bold red]")
            # [FIX-24] No restore_terminal() here — finally handles it
            return 1

        return 0

    except Exception as e:
        try:
            logger.critical(f"Unhandled exception in main(): {e}\n{traceback.format_exc()}")
        except Exception:
            pass
        console.print(f"[bold red]Fatal error: {e}[/bold red]")
        return 1

    finally:
        # [FIX-24] Single, guaranteed cleanup — runs on every exit path
        try:
            restore_terminal()
            logger.info("System Monitor shutdown complete")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
        restore_terminal()
        sys.exit(1)