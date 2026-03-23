#!/usr/bin/env python3

# Standard library imports
import os
import re
import sys
import glob
import time
import signal
import socket
import logging
import platform
import subprocess
import json
import threading
import yaml
from typing import Dict, Optional, List, Union, Any
from datetime import datetime
from collections import deque, defaultdict
from pathlib import Path
import traceback
from concurrent.futures import ThreadPoolExecutor
import errno

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("SystemMonitor")

# Required third-party imports with error handling
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
    from textual.screen import Screen
    
    import psutil
    
    # Disable dialog classes to prevent popups
    Screen.DIALOG_CLASSES = []
    logger.info("All required libraries imported successfully")
except ImportError as e:
    print(f"ERROR: Required dependency not found: {e}")
    print("Please install with: pip install psutil textual rich pyyaml")
    sys.exit(1)

# Configuration
CONFIG_DIR = os.path.expanduser("~/.config/system_monitor")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "system_monitor.log")

# Create config directory
os.makedirs(CONFIG_DIR, exist_ok=True)

# Enhanced logging setup
try:
    log_dir = os.path.join(CONFIG_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    file_handler = logging.FileHandler(os.path.join(log_dir, 'system_monitor.log'))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    rich_handler = RichHandler(rich_tracebacks=True)
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(rich_handler)
    root_logger.setLevel(logging.INFO)
except Exception as e:
    logger.error(f"Failed to set up enhanced logging: {e}")

# Default configuration - NO PINGING
DEFAULT_CONFIG = {
    'monitors': {
        'cpu': {'enabled': True, 'interval': 1.1, 'warning_threshold': 75, 'critical_threshold': 90},
        'memory': {'enabled': True, 'interval': 2.1, 'warning_threshold': 75, 'critical_threshold': 90},
        'disk': {'enabled': True, 'interval': 3.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'network': {'enabled': True, 'interval': 1.1},  # NO PING
        'gpu': {'enabled': True, 'interval': 2.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'sensors': {'enabled': True, 'interval': 3.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'services': {'enabled': True, 'interval': 5.0},
        'process': {'enabled': True, 'interval': 3.0, 'limit': 20},
        'battery': {'enabled': True, 'interval': 5.0, 'warning_threshold': 20, 'critical_threshold': 10},
        'firewall': {'enabled': True, 'interval': 5.0},
        'self': {'enabled': True, 'interval': 1.0}
    },
    'ui': {
        'theme': 'dark',
        'left_column': ['cpu', 'gpu', 'process', 'services'],
        'right_column': ['self', 'memory', 'disk', 'network', 'sensors', 'firewall'],
        'cores_per_line': 4
    },
    'alerts': {
        'enabled': True,
        'desktop_notification': False,
        'log_critical_events': True
    },
    'data': {
        'persistence_enabled': False
    }
}

# Platform detection
PLATFORM_INFO = {
    'system': platform.system().lower(),
    'release': platform.release(),
    'version': platform.version(),
    'machine': platform.machine(),
    'is_linux': platform.system().lower() == 'linux',
    'python_version': sys.version.split()[0],
    'processor': platform.processor()
}

# Global state
config = {}
should_exit = False
alert_history = deque(maxlen=100)

class ThreadSafeCache:
    """Thread-safe cache for expensive operations like subprocess calls"""
    
    def __init__(self):
        self._cache = {}
        self._lock = threading.RLock()
    
    def get_or_compute(self, key: str, compute_func, ttl: float = 10.0):
        """Get cached value or compute it if expired/missing"""
        with self._lock:
            now = time.time()
            if key in self._cache:
                value, timestamp = self._cache[key]
                if now - timestamp < ttl:
                    return value
            
            try:
                value = compute_func()
                self._cache[key] = (value, now)
                return value
            except Exception as e:
                logger.debug(f"Cache computation failed for {key}: {e}")
                # Return stale data if available
                if key in self._cache:
                    return self._cache[key][0]
                return None
    
    def invalidate(self, key: str):
        """Manually invalidate a cache entry"""
        with self._lock:
            self._cache.pop(key, None)
    
    def clear(self):
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()

class SystemDataCollector:
    """Centralized data collector to eliminate redundant psutil calls"""
    
    def __init__(self):
        self._data_cache = {}
        self._last_update = 0
        self._cache_ttl = 0.8  # 800ms cache
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="datacollector")
        
        # Track previous values for rate calculations
        self._prev_disk_io = None
        self._prev_net_io = None
        self._prev_time = time.time()
    
    def get_system_snapshot(self) -> Dict[str, Any]:
        """Get comprehensive system snapshot with intelligent caching"""
        with self._lock:
            now = time.time()
            if now - self._last_update < self._cache_ttl and self._data_cache:
                return self._data_cache.copy()
            
            # Collect all data in one efficient pass
            snapshot = {}
            
            try:
                # Basic system metrics (fast calls)
                snapshot['cpu_percent'] = psutil.cpu_percent(percpu=True)
                snapshot['cpu_times'] = psutil.cpu_times_percent()
                snapshot['memory'] = psutil.virtual_memory()
                snapshot['swap'] = psutil.swap_memory()
                snapshot['load_avg'] = psutil.getloadavg()
                snapshot['boot_time'] = psutil.boot_time()
                
                # I/O metrics with rate calculation
                curr_disk_io = psutil.disk_io_counters()
                curr_net_io = psutil.net_io_counters()
                
                if self._prev_disk_io and curr_disk_io:
                    dt = now - self._prev_time
                    if dt > 0:
                        snapshot['disk_io_rates'] = {
                            'read_bytes': (curr_disk_io.read_bytes - self._prev_disk_io.read_bytes) / dt,
                            'write_bytes': (curr_disk_io.write_bytes - self._prev_disk_io.write_bytes) / dt,
                            'read_count': (curr_disk_io.read_count - self._prev_disk_io.read_count) / dt,
                            'write_count': (curr_disk_io.write_count - self._prev_disk_io.write_count) / dt
                        }
                
                if self._prev_net_io and curr_net_io:
                    dt = now - self._prev_time
                    if dt > 0:
                        snapshot['net_io_rates'] = {
                            'bytes_sent': (curr_net_io.bytes_sent - self._prev_net_io.bytes_sent) / dt,
                            'bytes_recv': (curr_net_io.bytes_recv - self._prev_net_io.bytes_recv) / dt,
                            'packets_sent': (curr_net_io.packets_sent - self._prev_net_io.packets_sent) / dt,
                            'packets_recv': (curr_net_io.packets_recv - self._prev_net_io.packets_recv) / dt
                        }
                
                snapshot['disk_io'] = curr_disk_io
                snapshot['net_io'] = curr_net_io
                
                # Update previous values
                self._prev_disk_io = curr_disk_io
                self._prev_net_io = curr_net_io
                self._prev_time = now
                
                # Additional system info
                snapshot['disk_partitions'] = psutil.disk_partitions()
                snapshot['net_interfaces'] = psutil.net_if_addrs()
                snapshot['net_stats'] = psutil.net_if_stats()
                
            except Exception as e:
                logger.error(f"Error collecting system snapshot: {e}")
                # Return partial data if available
                pass
            
            self._data_cache = snapshot
            self._last_update = now
            return snapshot.copy()
    
    def get_top_processes_efficient(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Efficiently get top processes without scanning all processes"""
        try:
            # Pre-filter processes by getting CPU usage first
            active_processes = []
            
            for pid in psutil.pids():
                try:
                    p = psutil.Process(pid)
                    # Quick CPU check first
                    cpu = p.cpu_percent(interval=0)
                    if cpu > 0.1 or len(active_processes) < limit * 2:  # Keep some low-CPU processes
                        proc_info = {
                            'pid': pid,
                            'name': p.name(),
                            'cpu_percent': cpu,
                            'memory_percent': p.memory_percent(),
                            'memory_bytes': p.memory_info().rss,
                            'status': p.status()
                        }
                        
                        # Handle Python processes specially
                        if proc_info['name'] in ['python', 'python3', 'py']:
                            try:
                                cmdline = p.cmdline()
                                if len(cmdline) > 1:
                                    script_name = os.path.basename(cmdline[1])
                                    proc_info['name'] = f"python:{script_name}"
                            except:
                                pass
                        
                        active_processes.append(proc_info)
                        
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                    
                # Limit initial scan to prevent excessive CPU usage
                if len(active_processes) >= limit * 3:
                    break
            
            # Return top processes by CPU and memory
            cpu_sorted = sorted(active_processes, key=lambda x: x['cpu_percent'], reverse=True)[:limit]
            mem_sorted = sorted(active_processes, key=lambda x: x['memory_bytes'], reverse=True)[:limit]
            
            return {'cpu': cpu_sorted, 'memory': mem_sorted}
            
        except Exception as e:
            logger.error(f"Error getting top processes: {e}")
            return {'cpu': [], 'memory': []}

def load_config():
    """Load configuration from file or create default"""
    global config
    
    try:
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'w') as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False)
            config = DEFAULT_CONFIG
            logger.info(f"Created default configuration at {CONFIG_FILE}")
        else:
            with open(CONFIG_FILE, 'r') as f:
                loaded_config = yaml.safe_load(f)
            
            config = DEFAULT_CONFIG.copy()
            if loaded_config:
                for section in config:
                    if section in loaded_config:
                        if isinstance(config[section], dict) and isinstance(loaded_config[section], dict):
                            config[section].update(loaded_config[section])
            
            logger.info(f"Loaded configuration from {CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        config = DEFAULT_CONFIG

def store_alert(category, level, message):
    """Store an alert in memory and log if critical"""
    try:
        alert = {
            'timestamp': datetime.now(),
            'category': category,
            'level': level,
            'message': message
        }
        
        alert_history.append(alert)
        
        if config['alerts']['log_critical_events'] and level == 'critical':
            logger.critical(f"ALERT - {category}: {message}")
        
        if config['alerts']['desktop_notification']:
            try:
                if PLATFORM_INFO['is_linux']:
                    subprocess.run([
                        'notify-send',
                        f"System Monitor Alert - {category}",
                        message,
                        '--urgency=critical'
                    ], timeout=1)
            except Exception as e:
                logger.debug(f"Error sending desktop notification: {e}")
    except Exception as e:
        logger.error(f"Error in store_alert: {e}")

# Utility functions
def format_bytes(bytes_value: float) -> str:
    """Format bytes to human readable string"""
    try:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024:
                return f"{bytes_value:6.1f}{unit}"
            bytes_value /= 1024
        return f"{bytes_value:6.1f}TB"
    except:
        return "0.0B"

def create_progress_bar(percentage: float, width: int = 40, color: str = None) -> Text:
    """Create a colored progress bar based on percentage"""
    try:
        percentage = max(0, min(100, percentage))
        filled = int(width * percentage / 100)
        remainder = width - filled
        
        if color is None:
            if percentage < 50:
                color = "green"
            elif percentage < 75:
                color = "yellow"
            elif percentage < 90:
                color = "red"
            else:
                color = "bright_red"
        
        bar = Text('■' * filled, color) + Text('·' * remainder, "bright_black")
        return bar + Text(f" {percentage:5.1f}%", color)
    except:
        return Text("Error", "red")

def set_terminal_title(title: str) -> None:
    """Set the terminal window title"""
    try:
        if os.name == 'nt':
            os.system(f'title {title}')
        else:
            print(f'\033]0;{title}\007', end='', flush=True)
    except:
        pass

# Initialize global instances
system_data_collector = SystemDataCollector()
command_cache = ThreadSafeCache()

class BaseMonitor(Static):
    """Optimized base monitor class with shared data access"""
    
    def __init__(self):
        super().__init__()
        self.error_count = 0
        self.max_errors = 3
        monitor_type = self.__class__.__name__.lower().replace('monitor', '')
        monitor_config = config['monitors'].get(monitor_type, {})
        self.alert_thresholds = {
            'warning': monitor_config.get('warning_threshold', 75),
            'critical': monitor_config.get('critical_threshold', 90)
        }
    
    def get_shared_data(self) -> Dict[str, Any]:
        """Get shared system data from centralized collector"""
        return system_data_collector.get_system_snapshot()
    
    def handle_error(self, error: Exception, context: str) -> None:
        """Handle and log errors with context"""
        self.error_count += 1
        logger.error(f"Error in {self.__class__.__name__} ({context}): {error}")
        if self.error_count >= self.max_errors:
            logger.warning(f"{self.__class__.__name__} experiencing repeated errors")
    
    def get_interval(self) -> float:
        """Get refresh interval for this monitor type"""
        monitor_type = self.__class__.__name__.lower().replace('monitor', '')
        return config['monitors'].get(monitor_type, {}).get('interval', 3.0)
    
    def on_mount(self) -> None:
        """Set refresh interval using configuration"""
        try:
            interval = self.get_interval()
            self.set_interval(interval, self.refresh)
        except Exception as e:
            logger.error(f"Error in on_mount for {self.__class__.__name__}: {e}")
            self.set_interval(3.0, self.refresh)
    
    def check_threshold(self, value: float, category: str, name: str = None) -> Optional[str]:
        """Check if a value exceeds warning or critical thresholds"""
        try:
            if value >= self.alert_thresholds['critical']:
                message = f"{category} is critical: {value:.1f}%"
                if name:
                    message = f"{category} '{name}' is critical: {value:.1f}%"
                store_alert(category, 'critical', message)
                return 'critical'
            elif value >= self.alert_thresholds['warning']:
                message = f"{category} is high: {value:.1f}%"
                if name:
                    message = f"{category} '{name}' is high: {value:.1f}%"
                store_alert(category, 'warning', message)
                return 'warning'
            return None
        except Exception as e:
            logger.error(f"Error in check_threshold: {e}")
            return None
        
        
# PART 2: Core Monitor Classes - Optimized with shared data access

class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor with processor name display - OPTIMIZED"""
    
    def __init__(self):
        super().__init__()
        self.processor_name = self._get_processor_name()
        self._cpu_freq_cache = None
        self._freq_cache_time = 0
        self._freq_cache_ttl = 10  # Cache CPU frequency for 10 seconds
        
    def _get_processor_name(self) -> str:
        """Get the processor name from system information"""
        try:
            if sys.platform == "linux":
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":")[1].strip()
            return platform.processor() or ""
        except Exception:
            return platform.processor() or ""
    
    def _get_cpu_frequency(self) -> int:
        """Get CPU frequency with caching and multiple fallback methods"""
        current_time = time.time()
        
        # Return cached frequency if still valid
        if (self._cpu_freq_cache and 
            current_time - self._freq_cache_time < self._freq_cache_ttl):
            return self._cpu_freq_cache
        
        try:
            # Method 1: psutil.cpu_freq()
            freq = psutil.cpu_freq()
            if freq and freq.current > 100:
                self._cpu_freq_cache = int(freq.current)
                self._freq_cache_time = current_time
                return self._cpu_freq_cache
                
            # Method 2: /proc/cpuinfo on Linux
            if sys.platform == "linux":
                try:
                    with open("/proc/cpuinfo", "r") as f:
                        for line in f:
                            if "cpu MHz" in line:
                                freq_mhz = int(float(line.split(":")[1].strip()))
                                self._cpu_freq_cache = freq_mhz
                                self._freq_cache_time = current_time
                                return freq_mhz
                except Exception:
                    pass
            
            # Method 3: /sys/devices/system/cpu/ on Linux
            if sys.platform == "linux":
                try:
                    freq_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
                    if os.path.exists(freq_path):
                        with open(freq_path, "r") as f:
                            freq_khz = int(f.read().strip())
                            freq_mhz = freq_khz // 1000
                            self._cpu_freq_cache = freq_mhz
                            self._freq_cache_time = current_time
                            return freq_mhz
                except Exception:
                    pass
            
            # Fallback: extract from processor name
            if self.processor_name:
                ghz_match = re.search(r'(\d+.\d+)GHz', self.processor_name)
                if ghz_match:
                    freq_mhz = int(float(ghz_match.group(1)) * 1000)
                    self._cpu_freq_cache = freq_mhz
                    self._freq_cache_time = current_time
                    return freq_mhz
            
            return 0
            
        except Exception as e:
            logger.debug(f"Error getting CPU frequency: {e}")
            return 0
    
    def _get_system_uptime(self) -> str:
        """Get system uptime in human-readable format"""
        try:
            # Use shared data to avoid redundant psutil call
            shared_data = self.get_shared_data()
            boot_time = shared_data.get('boot_time', psutil.boot_time())
            uptime_seconds = time.time() - boot_time
            
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            if days > 0:
                return f"{int(days)}d {int(hours)}h {int(minutes)}m"
            elif hours > 0:
                return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
            else:
                return f"{int(minutes)}m {int(seconds)}s"
        except Exception:
            return "Unknown"
    
    def get_usage_color(self, percentage: float) -> str:
        """Determine color based on usage percentage"""
        if percentage < 50:
            return "green"
        elif percentage < 75:
            return "yellow"
        elif percentage < 90:
            return "red"
        return "bright_red"
    
    def create_colored_bar(self, percentage: float, width: int = 30) -> Text:
        """Create a color-coded bar with percentage before the bar"""
        try:
            color = self.get_usage_color(percentage)
            filled = int(width * percentage / 100)
            remainder = width - filled
            return Text(f"{percentage:5.1f}% ", color) + Text('■' * filled, color) + Text('·' * remainder, "bright_black")
        except Exception:
            return Text("Error", "red")
    
    def render(self) -> Panel:
        """Render CPU information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,1))
            
            # Use shared data instead of individual psutil calls
            shared_data = self.get_shared_data()
            cpu_percent = shared_data.get('cpu_percent', [])
            times = shared_data.get('cpu_times', None)
            load = shared_data.get('load_avg', [0, 0, 0])
            
            if not cpu_percent:
                return Panel(Text("CPU data unavailable", style="red"), title="CPU Monitor", border_style="blue")
            
            # Get CPU frequency
            current_mhz = self._get_cpu_frequency()
            uptime = self._get_system_uptime()
            
            # Calculate total CPU usage
            total = sum(cpu_percent) / len(cpu_percent)
            self.check_threshold(total, 'CPU Usage', 'Total')
            
            # Create metrics header table
            metrics_table = Table(box=None, expand=True, padding=(0,1))
            metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
            metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)
            
            # Format CPU frequency
            freq_text = ""
            if current_mhz > 1000:
                freq_text = f"{current_mhz/1000:.2f}GHz"
            elif current_mhz > 0:
                freq_text = f"{current_mhz}MHz"
            else:
                freq_text = "Unknown"
                
            metrics_table.add_row(
                Text("Total: ") + self.create_colored_bar(total),
                Text(f"Freq: {freq_text} | Load: {load[0]:5.2f}")
            )
            
            # Add CPU states and uptime
            if times:
                color_user = self.get_usage_color(times.user)
                color_sys = self.get_usage_color(times.system)
                states_text = (
                    Text(f"User: {times.user:4.1f}% ", color_user) +
                    Text(f"Sys: {times.system:4.1f}% ", color_sys) +
                    Text(f"Idle: {times.idle:4.1f}%", "bright_black")
                )
                uptime_text = Text(f"Uptime: {uptime}", "bright_green")
                metrics_table.add_row(states_text, uptime_text)
            
            table.add_row(metrics_table)
            
            # Create cores table
            cores_table = Table(box=None, expand=True, padding=(0,1))
            cores_per_line = config['ui']['cores_per_line']
            
            for i in range(cores_per_line):
                cores_table.add_column("", ratio=1)
            
            # Add core rows
            for i in range(0, len(cpu_percent), cores_per_line):
                cores_in_row = []
                for j in range(i, min(i + cores_per_line, len(cpu_percent))):
                    core_text = Text(f"Core {j:2d}: ", "cyan") + self.create_colored_bar(cpu_percent[j])
                    cores_in_row.append(core_text)
                
                while len(cores_in_row) < cores_per_line:
                    cores_in_row.append("")
                cores_table.add_row(*cores_in_row)
            
            table.add_row(cores_table)

            # Create title with processor name
            title = f"CPU Monitor"
            if self.processor_name:
                title += f" - {self.processor_name}"

            return Panel(table, title=title, border_style="blue")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"CPU Monitor Error: {str(e)}", style="red"), title="CPU Monitor", border_style="blue")


class MemoryMonitor(BaseMonitor):
    """Memory usage monitor - OPTIMIZED with shared data"""
    
    def render(self) -> Panel:
        """Render memory information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Memory", style="cyan", width=12, no_wrap=True)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Use shared data instead of individual psutil calls
            shared_data = self.get_shared_data()
            vm = shared_data.get('memory')
            swap = shared_data.get('swap')
            
            if not vm or not swap:
                return Panel(Text("Memory data unavailable", style="red"), title="Memory Monitor", border_style="green")

            # Check thresholds
            self.check_threshold(vm.percent, 'Memory Usage', 'RAM')
            self.check_threshold(swap.percent, 'Memory Usage', 'Swap')

            # RAM Usage
            table.add_row(
                "RAM",
                create_progress_bar(vm.percent),
                f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}"
            )
            
            # Cache Usage
            if hasattr(vm, 'cached'):
                cache_percent = (vm.cached / vm.total) * 100
                cache_text = f"Cached: {format_bytes(vm.cached)}"
                if hasattr(vm, 'buffers'):
                    cache_text += f" | Buffers: {format_bytes(vm.buffers)}"
                
                table.add_row(
                    "Cache",
                    create_progress_bar(cache_percent),
                    cache_text
                )

            # Effective Memory
            if hasattr(vm, 'available'):
                available_percent = (vm.available / vm.total) * 100
                effective_percent = 100 - available_percent
                table.add_row(
                    "Effective",
                    create_progress_bar(effective_percent),
                    f"Available: {format_bytes(vm.available)} | Used: {format_bytes(vm.total - vm.available)}"
                )
            
            # Swap Usage
            table.add_row(
                "Swap",
                create_progress_bar(swap.percent),
                f"Used: {format_bytes(swap.used)} / Total: {format_bytes(swap.total)}"
            )

            return Panel(table, title="Memory Monitor", border_style="green")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Memory Monitor Error: {str(e)}", style="red"), title="Memory Monitor", border_style="green")


class DiskMonitor(BaseMonitor):
    """Disk monitor - OPTIMIZED with shared data and efficient I/O tracking"""
    
    def __init__(self):
        super().__init__()
        self.partitions_cache = None
        self.partitions_cache_time = 0
        self.partitions_cache_ttl = 30  # Cache partitions for 30 seconds
    
    def _get_partitions(self) -> List[Dict[str, Any]]:
        """Get Linux partition information with caching"""
        current_time = time.time()
        
        # Return cached partitions if still valid
        if (self.partitions_cache and 
            current_time - self.partitions_cache_time < self.partitions_cache_ttl):
            return self.partitions_cache
        
        partitions = []
        try:
            # Use shared data for disk partitions
            shared_data = self.get_shared_data()
            disk_partitions = shared_data.get('disk_partitions', psutil.disk_partitions(all=False))
            
            for part in disk_partitions:
                # Skip certain filesystem types
                if part.fstype in {'squashfs', 'efivarfs'} or \
                   '/boot' in part.mountpoint or \
                   '/snap' in part.mountpoint:
                    continue
                
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    
                    # Get additional Linux disk info
                    dev_name = os.path.basename(part.device)
                    additional_info = {}
                    
                    # Try to get SSD/HDD info from sysfs
                    sys_block_path = f"/sys/block/{dev_name}"
                    if os.path.exists(sys_block_path):
                        try:
                            rotational_path = f"{sys_block_path}/queue/rotational"
                            if os.path.exists(rotational_path):
                                with open(rotational_path) as f:
                                    additional_info['is_ssd'] = f.read().strip() == '0'
                        except Exception:
                            pass
                    
                    # Check threshold
                    self.check_threshold(usage.percent, 'Disk Usage', part.mountpoint)
                    
                    partitions.append({
                        'device': part.device,
                        'mountpoint': part.mountpoint,
                        'fstype': part.fstype,
                        'total': usage.total,
                        'used': usage.used,
                        'free': usage.free,
                        'percent': usage.percent,
                        **additional_info
                    })
                    
                except (PermissionError, OSError):
                    continue
                    
        except Exception as e:
            self.handle_error(e, "get_partitions")
            
        # Update cache
        self.partitions_cache = partitions
        self.partitions_cache_time = current_time
        return partitions
    
    def render(self) -> Panel:
        """Render disk information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Disk", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Use shared data for I/O metrics
            shared_data = self.get_shared_data()
            io_rates = shared_data.get('disk_io_rates', {})
            partitions = self._get_partitions()
            
            # Show I/O rates if available
            if io_rates:
                table.add_row(
                    "Disk I/O",
                    f"Read: {format_bytes(io_rates.get('read_bytes', 0))}/s",
                    f"Write: {format_bytes(io_rates.get('write_bytes', 0))}/s"
                )
                
                # Operations per second
                table.add_row(
                    "Operations",
                    f"Read: {io_rates.get('read_count', 0):.1f}/s",
                    f"Write: {io_rates.get('write_count', 0):.1f}/s"
                )
            
            # Show partitions
            for part in partitions:
                name = os.path.basename(part['mountpoint']) or part['mountpoint']
                usage_bar = create_progress_bar(part['percent'])
                
                details = [
                    f"{format_bytes(part['used'])} / {format_bytes(part['total'])}",
                    f"({part['fstype']})"
                ]
                
                # Add SSD/HDD indicator if available
                if 'is_ssd' in part:
                    details.append("SSD" if part['is_ssd'] else "HDD")
                
                table.add_row(
                    name[:12],
                    usage_bar,
                    " | ".join(details)
                )
            
            return Panel(table, title="Disk Monitor", border_style="magenta")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Disk Monitor Error: {str(e)}", style="red"), title="Disk Monitor", border_style="magenta")


class NetworkMonitor(BaseMonitor):
    """Network monitor - OPTIMIZED with shared data, NO PING functionality"""
    
    def __init__(self):
        super().__init__()
        self.history = {
            'bytes_sent': deque(maxlen=60),
            'bytes_recv': deque(maxlen=60),
            'packets_sent': deque(maxlen=60),
            'packets_recv': deque(maxlen=60)
        }
        self.peak_download = 0
        self.peak_upload = 0
        self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.today_download = 0
        self.today_upload = 0
        self._interfaces_cache = None
        self._interfaces_cache_time = 0
        self._interfaces_cache_ttl = 10
    
    def _get_interface_info(self) -> Dict[str, Any]:
        """Get network interface information with caching"""
        current_time = time.time()
        
        # Return cached interfaces if still valid
        if (self._interfaces_cache and 
            current_time - self._interfaces_cache_time < self._interfaces_cache_ttl):
            return self._interfaces_cache
        
        interfaces = {}
        try:
            # Use shared data for network interfaces
            shared_data = self.get_shared_data()
            addrs = shared_data.get('net_interfaces', psutil.net_if_addrs())
            stats = shared_data.get('net_stats', psutil.net_if_stats())
            
            for name, addrs_list in addrs.items():
                # Skip interfaces that aren't up
                if name not in stats or not stats[name].isup:
                    continue
                
                # Get IP addresses and MAC
                ipv4_addrs = []
                ipv6_addrs = []
                mac_addr = None
                
                for addr in addrs_list:
                    if addr.family == socket.AF_INET:
                        ipv4_addrs.append(addr.address)
                    elif addr.family == socket.AF_INET6:
                        ipv6_addrs.append(addr.address)
                    elif hasattr(socket, 'AF_LINK') and addr.family == socket.AF_LINK:
                        mac_addr = addr.address
                    elif hasattr(psutil, 'AF_LINK') and addr.family == psutil.AF_LINK:
                        mac_addr = addr.address
                
                # Skip interfaces with no IP addresses
                if not (ipv4_addrs or ipv6_addrs):
                    continue
                
                stat = stats[name]
                interfaces[name] = {
                    'ipv4': ipv4_addrs,
                    'ipv6': ipv6_addrs,
                    'mac': mac_addr,
                    'speed': stat.speed or 0,
                    'mtu': stat.mtu,
                    'is_up': stat.isup
                }
        except Exception as e:
            self.handle_error(e, "get_interface_info")
            
        # Update cache
        self._interfaces_cache = interfaces
        self._interfaces_cache_time = current_time
        return interfaces
    
    def _get_connection_stats(self) -> dict:
        """Get counts of connections by state"""
        connections = {
            'ESTABLISHED': 0,
            'LISTEN': 0,
            'TIME_WAIT': 0,
            'CLOSE_WAIT': 0,
            'other': 0,
            'total': 0
        }
        
        try:
            for conn in psutil.net_connections(kind='inet'):
                connections['total'] += 1
                status = conn.status
                if status in connections:
                    connections[status] += 1
                else:
                    connections['other'] += 1
        except Exception as e:
            logger.debug(f"Error getting connection stats: {e}")
            
        return connections
    
    def _create_traffic_indicator(self, rate: float, max_rate: float = 10 * 1024 * 1024, direction: str = "down") -> Text:
        """Create a traffic indicator with arrows and intensity"""
        try:
            percent = min(100, (rate / max_rate) * 100)
            
            if percent < 25:
                color = "green"
            elif percent < 50:
                color = "bright_green"
            elif percent < 75:
                color = "yellow"
            else:
                color = "red"
            
            arrow = "↓" if direction == "down" else "↑"
            
            if percent < 1:
                indicator = Text("·", "bright_black")
            elif percent < 20:
                indicator = Text(arrow, color)
            elif percent < 40:
                indicator = Text(arrow * 2, color)
            elif percent < 60:
                indicator = Text(arrow * 3, color)
            elif percent < 80:
                indicator = Text(arrow * 4, color)
            else:
                indicator = Text(arrow * 5, color)
            
            rate_text = format_bytes(rate) + "/s"
            return Text(f"{indicator} {rate_text}", color)
        except Exception:
            return Text("Error", "red")
    
    def render(self) -> Panel:
        """Render network information panel - OPTIMIZED, NO PING"""
        try:
            table = Table(box=None, expand=True, padding=(0,1))
            table.add_column("Network", style="cyan", width=12)
            table.add_column("Traffic", ratio=1)
            table.add_column("Details", style="bright_blue")
            
            # Use shared data for network metrics
            shared_data = self.get_shared_data()
            net_rates = shared_data.get('net_io_rates', {})
            net_io = shared_data.get('net_io')
            interfaces = self._get_interface_info()
            connection_stats = self._get_connection_stats()
            
            # Display current transfer rates
            if net_rates:
                recv_rate = net_rates.get('bytes_recv', 0)
                send_rate = net_rates.get('bytes_sent', 0)
                
                # Update peaks
                self.peak_download = max(self.peak_download, recv_rate)
                self.peak_upload = max(self.peak_upload, send_rate)
                
                # Update history
                self.history['bytes_recv'].append(recv_rate)
                self.history['bytes_sent'].append(send_rate)
                
                # Update daily totals
                now = datetime.now()
                if now.date() > self.today_start.date():
                    self.today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    self.today_download = 0
                    self.today_upload = 0
                
                # Download rate
                table.add_row(
                    "Download",
                    self._create_traffic_indicator(recv_rate, direction="down"),
                    f"Total: {format_bytes(net_io.bytes_recv if net_io else 0)} | Peak: {format_bytes(self.peak_download)}/s"
                )
                
                # Upload rate
                table.add_row(
                    "Upload",
                    self._create_traffic_indicator(send_rate, direction="up"),
                    f"Total: {format_bytes(net_io.bytes_sent if net_io else 0)} | Peak: {format_bytes(self.peak_upload)}/s"
                )
                
                # Packet rates
                packet_recv_rate = net_rates.get('packets_recv', 0)
                packet_sent_rate = net_rates.get('packets_sent', 0)
                if packet_recv_rate > 0 or packet_sent_rate > 0:
                    packet_text = Text("↓ ", "cyan") + Text(f"{packet_recv_rate:.1f}/s", "cyan")
                    packet_text += Text(" ↑ ", "cyan") + Text(f"{packet_sent_rate:.1f}/s", "cyan")
                    table.add_row("Packets", packet_text, "")
            
            # Connection statistics
            connection_text = Text(f"Total: {connection_stats['total']}", "bright_blue")
            details = []
            if connection_stats['ESTABLISHED'] > 0:
                details.append(f"Est: {connection_stats['ESTABLISHED']}")
            if connection_stats['LISTEN'] > 0:
                details.append(f"Listen: {connection_stats['LISTEN']}")
            
            table.add_row(
                "Connections",
                connection_text,
                " | ".join(details) if details else ""
            )
            
            # Interface section
            if interfaces:
                table.add_row("", "", "")
                table.add_row("Interfaces", "", "")
                
                for name, info in interfaces.items():
                    ipv4 = ', '.join(info['ipv4'][:2])
                    if len(info['ipv4']) > 2:
                        ipv4 += f" (+{len(info['ipv4'])-2})"
                        
                    speed_text = f"{info['speed']} Mbps" if info['speed'] else "Auto"
                    
                    details = [f"IPv4: {ipv4}", f"MTU: {info['mtu']}"]
                    if info.get('mac'):
                        details.append(f"MAC: {info['mac'][:9]}...")
                    
                    table.add_row(
                        name[:12],
                        f"Speed: {speed_text}",
                        " | ".join(details)
                    )
            
            return Panel(table, title="Network Monitor (Ping Disabled)", border_style="cyan")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Network Monitor Error: {str(e)}", style="red"), title="Network Monitor", border_style="cyan")
        
        
# PART 3: Advanced Monitor Classes - All optimized with caching and efficiency improvements

def check_gpu_available():
    """Check for GPU using multiple methods efficiently"""
    try:
        # Method 1: Try nvidia-smi directly
        try:
            nvidia_check = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=1
            )
            if nvidia_check.returncode == 0:
                return True
        except Exception:
            pass
            
        # Method 2: Check common NVIDIA paths
        nvidia_paths = ['/proc/driver/nvidia/version', '/dev/nvidia0', '/dev/nvidiactl']
        if any(os.path.exists(path) for path in nvidia_paths):
            return True
            
        # Method 3: Check device vendor files
        try:
            for i in range(3):  # Check first 3 possible GPUs
                vendor_path = f'/sys/class/drm/card{i}/device/vendor'
                if os.path.exists(vendor_path):
                    with open(vendor_path) as f:
                        vendor = f.read().strip()
                        if vendor in ['0x10de', '10de']:  # NVIDIA vendor ID
                            return True
        except Exception:
            pass
        
        return False
    except Exception:
        return False


class GPUMonitor(BaseMonitor):
    """GPU monitor - OPTIMIZED with caching and efficient nvidia-smi calls"""
    
    def __init__(self):
        super().__init__()
        self.history = {'usage': deque(maxlen=30), 'temp': deque(maxlen=30)}
        self.has_nvidia = check_gpu_available()
        self.gpu_data_cache = None
        self.gpu_cache_time = 0
        self.gpu_cache_ttl = 1.5  # Cache GPU data for 1.5 seconds
        
    def _safe_float(self, value: str, default: float = 0) -> float:
        """Safely convert a string to float"""
        try:
            value = value.strip()
            if '[N/A]' in value or not value:
                return default
            return float(value)
        except (ValueError, TypeError, AttributeError):
            return default
    
    def _get_gpu_metrics(self) -> Dict[str, Any]:
        """Get GPU metrics with efficient caching"""
        current_time = time.time()
        
        # Return cached data if still valid
        if (self.gpu_data_cache and 
            current_time - self.gpu_cache_time < self.gpu_cache_ttl):
            return self.gpu_data_cache
        
        # Return empty metrics if no NVIDIA GPU
        if not self.has_nvidia:
            empty_metrics = {
                'name': 'No NVIDIA GPU detected',
                'usage': 0, 'temp': 0, 'memory_used': 0, 'memory_total': 0,
                'memory_percent': 0, 'clock_gpu': 0, 'clock_mem': 0,
                'fan_speed': 0, 'perf_state': 'N/A'
            }
            self.gpu_data_cache = empty_metrics
            return empty_metrics
            
        # Use cached command execution
        def get_nvidia_data():
            cmd = [
                'nvidia-smi',
                '--query-gpu=name,utilization.gpu,temperature.gpu,'
                'memory.used,memory.total,power.draw,power.limit,'
                'clocks.current.graphics,clocks.max.graphics,'
                'clocks.current.memory,clocks.max.memory,'
                'fan.speed,pstate',
                '--format=csv,noheader,nounits'
            ]
            return subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        
        try:
            result = command_cache.get_or_compute('nvidia_smi_metrics', get_nvidia_data, ttl=1.0)
            
            if result and result.returncode == 0:
                raw_values = result.stdout.strip().split(',')
                values = [v.strip() for v in raw_values if v.strip()]
                
                # Ensure we have enough values
                if len(values) < 12:
                    values = values + ['[N/A]'] * (12 - len(values))
                
                metrics = {
                    'name': values[0],
                    'usage': self._safe_float(values[1]),
                    'temp': self._safe_float(values[2]),
                    'memory_used': self._safe_float(values[3]),
                    'memory_total': self._safe_float(values[4]),
                    'power_draw': self._safe_float(values[5]),
                    'power_limit': self._safe_float(values[6]),
                    'clock_gpu': self._safe_float(values[7]),
                    'clock_gpu_max': self._safe_float(values[8]),
                    'clock_mem': self._safe_float(values[9]),
                    'clock_mem_max': self._safe_float(values[10]),
                    'fan_speed': self._safe_float(values[11]),
                    'perf_state': values[12] if len(values) > 12 and '[N/A]' not in values[12] else 'P?'
                }
                
                # Calculate memory percentage
                if metrics['memory_total'] > 0:
                    metrics['memory_percent'] = (metrics['memory_used'] / metrics['memory_total'] * 100)
                else:
                    metrics['memory_percent'] = 0
                
                # Update history
                self.history['usage'].append(metrics['usage'])
                self.history['temp'].append(metrics['temp'])
                
                # Check thresholds
                self.check_threshold(metrics['usage'], 'GPU Usage')
                self.check_threshold(metrics['temp'], 'GPU Temperature')
                self.check_threshold(metrics['memory_percent'], 'GPU Memory')
                
                # Cache the result
                self.gpu_data_cache = metrics
                self.gpu_cache_time = current_time
                return metrics
                
        except Exception as e:
            self.handle_error(e, "get_gpu_metrics")
            
        # Fallback metrics
        fallback_metrics = {
            'name': 'NVIDIA GPU (Error reading data)',
            'usage': 0, 'temp': 0, 'memory_used': 0, 'memory_total': 0,
            'memory_percent': 0, 'clock_gpu': 0, 'clock_mem': 0,
            'fan_speed': 0, 'perf_state': 'N/A'
        }
        self.gpu_data_cache = fallback_metrics
        return fallback_metrics
    
    def render(self) -> Panel:
        """Render GPU information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("GPU", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            metrics = self._get_gpu_metrics()
            
            # Special case for no NVIDIA GPU
            if not self.has_nvidia:
                table.add_row(
                    "Status",
                    "No NVIDIA GPU detected",
                    "Install NVIDIA drivers for GPU monitoring"
                )
                return Panel(table, title="GPU Monitor", border_style="yellow")
            
            # Name with Performance State
            table.add_row(
                "Name",
                metrics['name'],
                f"P-State: {metrics.get('perf_state', 'P?')}"
            )
            
            # Usage with power info
            power_text = ""
            if metrics.get('power_draw', 0) > 0:
                power_text = f" | {metrics['power_draw']:.1f}W"
            table.add_row(
                "Usage",
                create_progress_bar(metrics['usage']),
                f"{metrics['usage']:.1f}%{power_text}"
            )
            
            # Temperature with fan speed
            temp_text = f"{metrics['temp']:.1f}°C"
            if metrics.get('fan_speed', 0) > 0:
                temp_text += f" | Fan: {metrics['fan_speed']:.0f}%"
            table.add_row(
                "Temperature",
                create_progress_bar(metrics['temp'], color="green" if metrics['temp'] < 70 
                                  else "yellow" if metrics['temp'] < 85 else "red"),
                temp_text
            )
            
            # Memory usage
            table.add_row(
                "Memory",
                create_progress_bar(metrics['memory_percent']),
                f"{metrics['memory_used']:.0f}MB / {metrics['memory_total']:.0f}MB"
            )
            
            # Clock speeds
            table.add_row(
                "Clocks",
                f"GPU: {metrics['clock_gpu']}MHz | Mem: {metrics['clock_mem']}MHz",
                f"Max GPU: {metrics.get('clock_gpu_max', 0):.0f}MHz"
            )
            
            # Average usage
            if self.history['usage'] or self.history['temp']:
                avg_usage = sum(self.history['usage']) / len(self.history['usage']) if self.history['usage'] else 0
                avg_temp = sum(self.history['temp']) / len(self.history['temp']) if self.history['temp'] else 0
                table.add_row(
                    "Average",
                    f"Usage: {avg_usage:.1f}%",
                    f"Temp: {avg_temp:.1f}°C"
                )
            
            return Panel(table, title="GPU Monitor", border_style="yellow")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"GPU Monitor - Error: {str(e)}", style="red"), title="GPU Monitor", border_style="yellow")


class ServiceMonitor(BaseMonitor):
    """Service monitor - OPTIMIZED with efficient systemctl caching"""
    
    def __init__(self):
        super().__init__()
        self.important_services = [
            'systemd-journald', 'systemd-logind', 'systemd-timesyncd', 'dbus',
            'NetworkManager', 'sshd', 'cron', 'udev', 'rsyslog', 'ModemManager',
            'irqbalance', 'acpid', 'bluetooth', 'cups', 'apache2', 'mysql', 'postgresql'
        ]
        self.status_cache = {}
        self.cache_time = 0
        self.cache_ttl = 5
    
    def _get_service_status(self, service: str) -> Optional[Dict[str, Any]]:
        """Get service status using cached systemctl calls"""
        def get_systemctl_data():
            cmd = ['systemctl', 'show', f'{service}.service',
                  '--property=ActiveState,SubState,LoadState,UnitFileState,'
                  'Description,StateChangeTimestamp,ExecMainStatus']
            return subprocess.run(cmd, capture_output=True, text=True, timeout=1)
        
        try:
            result = command_cache.get_or_compute(f'systemctl_{service}', get_systemctl_data, ttl=5.0)
            
            if result and result.returncode == 0:
                status = {}
                for line in result.stdout.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        status[key] = value
                
                return {
                    'name': service,
                    'state': status.get('ActiveState', 'unknown'),
                    'substate': status.get('SubState', 'unknown'),
                    'enabled': status.get('UnitFileState', '') == 'enabled',
                    'description': status.get('Description', ''),
                    'status_code': int(status.get('ExecMainStatus', 0) or 0),
                    'last_changed': status.get('StateChangeTimestamp', '')
                }
        except Exception as e:
            self.handle_error(e, f"get_service_status_{service}")
        return None
    
    def _get_all_services(self) -> Dict[str, Any]:
        """Get status of all monitored services with caching"""
        current_time = time.time()
        
        # Return cached results if valid
        if current_time - self.cache_time < self.cache_ttl:
            return self.status_cache
        
        services = {}
        stats = {'total': len(self.important_services), 'running': 0, 'stopped': 0, 'failed': 0, 'other': 0}
        
        # Get status for each service
        for service in self.important_services:
            status = self._get_service_status(service)
            if status:
                services[service] = status
                
                state = status['state'].lower()
                if state == 'active':
                    stats['running'] += 1
                elif state == 'inactive':
                    stats['stopped'] += 1
                elif state in {'failed', 'error'}:
                    stats['failed'] += 1
                    store_alert('Service', 'warning', f"Service '{service}' has failed")
                else:
                    stats['other'] += 1
        
        # Update cache
        self.status_cache = {'services': services, 'stats': stats, 'timestamp': current_time}
        self.cache_time = current_time
        return self.status_cache
    
    def render(self) -> Panel:
        """Render service information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Service", style="cyan", width=12)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            info = self._get_all_services()
            stats = info.get('stats', {})
            services = info.get('services', {})
            
            # Show summary stats
            summary_text = f"Running: {stats.get('running', 0)}/{stats.get('total', 0)} | Failed: {stats.get('failed', 0)}"
            table.add_row("Summary", summary_text, "")
            
            # Sort services by state and name
            sorted_services = sorted(
                services.values(),
                key=lambda x: (x['state'] != 'active', x['state'] == 'failed', x['name'])
            )
            
            # Show individual services
            for service in sorted_services:
                # Determine status color
                if service['state'] == 'active':
                    color = "green"
                elif service['state'] == 'failed':
                    color = "red"
                elif service['state'] == 'inactive':
                    color = "yellow"
                else:
                    color = "bright_black"
                
                status_bar = Text('■' * 10, color)
                
                details = [
                    service['substate'],
                    "Enabled" if service['enabled'] else "Disabled"
                ]
                
                if service['description']:
                    details.append(service['description'][:30])
                
                table.add_row(
                    service['name'][:12],
                    status_bar,
                    " | ".join(details)
                )
            
            return Panel(table, title="Service Monitor", border_style="blue")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Service Monitor Error: {str(e)}", style="red"), title="Service Monitor", border_style="blue")


class ProcessMonitor(BaseMonitor):
    """Process monitor - HEAVILY OPTIMIZED to eliminate bottleneck"""
    
    def __init__(self):
        super().__init__()
        self.sort_by = 'cpu'
        self.processes_limit = config['monitors']['process']['limit']
        self.last_sort_change = 0
    
    def handle_sort_key(self, key: str) -> None:
        """Handle sort key events from the app"""
        try:
            if key == "c":
                self.sort_by = 'cpu'
                self.last_sort_change = time.time()
                self.refresh_content()
            elif key == "m":
                self.sort_by = 'memory'
                self.last_sort_change = time.time()
                self.refresh_content()
        except Exception as e:
            logger.error(f"Error handling sort key: {e}")
    
    def refresh_content(self) -> None:
        """Refresh the monitor's content"""
        try:
            new_content = self.render()
            self.update(new_content)
        except Exception as e:
            logger.error(f"Error refreshing content: {e}")
    
    def render(self) -> Panel:
        """Render process panel - OPTIMIZED to use shared efficient process data"""
        try:
            table = Table(box=None, expand=True, padding=(0,1), collapse_padding=True)
            table.add_column("PID", style="cyan", width=7)
            table.add_column("Name", style="bright_blue", width=25)
            table.add_column("CPU%", justify="right", width=7)
            table.add_column("Memory", justify="right", width=10)
            table.add_column("Status", width=10)
            
            # Use efficient process data from shared collector
            all_processes = system_data_collector.get_top_processes_efficient(self.processes_limit)
            processes = all_processes.get(self.sort_by, [])
            
            sort_info = "CPU" if self.sort_by == 'cpu' else "Memory"
            
            if processes:
                for proc in processes:
                    # CPU column
                    cpu_percent = proc.get('cpu_percent', 0) or 0
                    cpu_color = "red" if cpu_percent > 50 else "yellow" if cpu_percent > 20 else "green"
                    cpu_text = Text(f"{cpu_percent:5.1f}", cpu_color)
                    
                    # Memory column
                    mem_percent = proc.get('memory_percent', 0) or 0
                    mem_bytes = proc.get('memory_bytes', 0) or 0
                    mem_text = f"{format_bytes(mem_bytes)} ({mem_percent:.1f}%)"
                    
                    # Status column
                    status = proc.get('status', '')
                    status_color = {
                        'running': 'green',
                        'sleeping': 'bright_black',
                        'stopped': 'yellow',
                        'zombie': 'red'
                    }.get(status, 'white')
                    status_text = Text(status, status_color)
                    
                    table.add_row(
                        str(proc.get('pid', '')),
                        proc.get('name', '')[:25],
                        cpu_text,
                        mem_text,
                        status_text
                    )
            else:
                table.add_row("Loading processes...", "", "", "", "")
            
            return Panel(
                table,
                title=f"Process Monitor (Top {self.processes_limit}, Sort: {sort_info}) - Press C=CPU, M=Mem",
                border_style="green",
                padding=(0,0)
            )
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Process Monitor Error: {str(e)}", style="red"))


class SelfMonitor(BaseMonitor):
    """Self monitor - OPTIMIZED with efficient resource tracking"""
    
    def __init__(self):
        super().__init__()
        try:
            self.process = psutil.Process(os.getpid())
            self.start_time = time.time()
            self.last_io = None
            self.last_io_time = time.time()
            self.history = {
                'cpu': deque(maxlen=30),
                'memory': deque(maxlen=30),
                'io_read': deque(maxlen=15),
                'io_write': deque(maxlen=15)
            }
            self.peak_memory = 0
            self.peak_cpu = 0
        except Exception as e:
            logger.error(f"Error initializing Self monitor: {e}")
            self.process = None
            self.start_time = time.time()
            self.history = {'cpu': deque(maxlen=30), 'memory': deque(maxlen=30)}
            self.peak_memory = 0
            self.peak_cpu = 0
    
    def _get_self_metrics(self) -> Dict[str, Any]:
        """Get resource usage metrics for this application - OPTIMIZED"""
        try:
            if not self.process:
                return {}
                
            current_time = time.time()
            
            # Get basic metrics
            cpu_percent = self.process.cpu_percent(interval=0)  # Non-blocking
            cpu_times = self.process.cpu_times()
            memory_info = self.process.memory_info()
            mem_percent = self.process.memory_percent()
            
            # Track peaks
            self.peak_memory = max(self.peak_memory, memory_info.rss)
            self.peak_cpu = max(self.peak_cpu, cpu_percent)
            
            # Get I/O with rate calculation
            io_rates = {'read': 0, 'write': 0}
            try:
                io_counters = self.process.io_counters()
                if self.last_io and (current_time - self.last_io_time) > 0:
                    time_diff = current_time - self.last_io_time
                    io_rates = {
                        'read': (io_counters.read_bytes - self.last_io.read_bytes) / time_diff,
                        'write': (io_counters.write_bytes - self.last_io.write_bytes) / time_diff
                    }
                self.last_io = io_counters
                self.last_io_time = current_time
            except:
                io_counters = None
            
            # Get additional info
            thread_count = self.process.num_threads()
            open_files = len(self.process.open_files()) if hasattr(self.process, 'open_files') else 0
            
            # Update history (smaller datasets for efficiency)
            self.history['cpu'].append(cpu_percent)
            self.history['memory'].append(mem_percent)
            if io_rates['read'] > 0 or io_rates['write'] > 0:
                self.history['io_read'].append(io_rates['read'])
                self.history['io_write'].append(io_rates['write'])
            
            # Calculate system overhead
            shared_data = self.get_shared_data()
            system_memory = shared_data.get('memory')
            memory_overhead = (memory_info.rss / system_memory.total * 100) if system_memory else 0
            
            run_time = current_time - self.start_time
            
            return {
                'cpu_percent': cpu_percent,
                'memory_percent': mem_percent,
                'memory_bytes': memory_info.rss,
                'memory_vms': memory_info.vms,
                'memory_overhead': memory_overhead,
                'peak_memory': self.peak_memory,
                'peak_cpu': self.peak_cpu,
                'read_bytes': io_counters.read_bytes if io_counters else 0,
                'write_bytes': io_counters.write_bytes if io_counters else 0,
                'read_rate': io_rates['read'],
                'write_rate': io_rates['write'],
                'threads_count': thread_count,
                'open_files': open_files,
                'run_time': run_time,
                'avg_cpu': sum(self.history['cpu']) / len(self.history['cpu']) if self.history['cpu'] else 0,
                'avg_memory': sum(self.history['memory']) / len(self.history['memory']) if self.history['memory'] else 0,
            }
        except Exception as e:
            self.handle_error(e, "get_self_metrics")
            return {}
    
    def render(self) -> Panel:
        """Render self monitoring information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Resource", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            metrics = self._get_self_metrics()
            
            if not metrics:
                return Panel(Text("Self monitoring unavailable", style="yellow"), 
                           title="Monitor Script Usage", border_style="bright_red")
            
            # CPU usage
            table.add_row(
                "CPU",
                create_progress_bar(metrics['cpu_percent']),
                f"Peak: {metrics['peak_cpu']:.1f}% | Avg: {metrics['avg_cpu']:.1f}%"
            )
            
            # Memory usage
            table.add_row(
                "Memory",
                create_progress_bar(metrics['memory_percent']),
                f"{format_bytes(metrics['memory_bytes'])} ({metrics['memory_percent']:.1f}%)"
            )
            
            # Memory overhead
            table.add_row(
                "Overhead",
                create_progress_bar(metrics['memory_overhead']),
                f"{metrics['memory_overhead']:.2f}% of system | Peak: {format_bytes(metrics['peak_memory'])}"
            )
            
            # Thread and file info
            table.add_row(
                "Resources",
                f"{metrics['threads_count']} threads",
                f"Open files: {metrics['open_files']}"
            )
            
            # I/O rates
            if metrics['read_rate'] > 0 or metrics['write_rate'] > 0:
                table.add_row(
                    "I/O Rate",
                    f"R: {format_bytes(metrics['read_rate'])}/s",
                    f"W: {format_bytes(metrics['write_rate'])}/s"
                )
            
            # Runtime
            hours, remainder = divmod(metrics['run_time'], 3600)
            minutes, seconds = divmod(remainder, 60)
            runtime_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
            table.add_row(
                "Runtime",
                runtime_str,
                ""
            )
            
            return Panel(table, title="Monitor Script Resource Usage", border_style="bright_red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Self Monitor Error: {str(e)}", style="red"), 
                       title="Monitor Script Usage", border_style="bright_red")


class SensorMonitor(BaseMonitor):
    """Sensor monitor - OPTIMIZED with caching and integrated battery"""
    
    def __init__(self):
        super().__init__()
        self.history = {'temps': deque(maxlen=20), 'fans': deque(maxlen=20)}
        self.warning_temp = config['monitors']['sensors']['warning_threshold']
        self.critical_temp = config['monitors']['sensors']['critical_threshold']
        self._sensor_cache = None
        self._sensor_cache_time = 0
        self._sensor_cache_ttl = 2.0
    
    def _create_bar_without_percentage(self, percentage, width=40, color=None):
        """Create a colored progress bar WITHOUT percentage text"""
        try:
            percentage = max(0, min(100, percentage))
            filled = int(width * percentage / 100)
            remainder = width - filled
            
            if color is None:
                if percentage < 50:
                    color = "green"
                elif percentage < 75:
                    color = "yellow"
                elif percentage < 90:
                    color = "red"
                else:
                    color = "bright_red"
            
            return Text('■' * filled, color) + Text('·' * remainder, "bright_black")
        except Exception:
            return Text("Error", "red")
    
    def _get_sensor_data(self):
        """Get sensor data with caching"""
        current_time = time.time()
        
        # Return cached data if still valid
        if (self._sensor_cache and 
            current_time - self._sensor_cache_time < self._sensor_cache_ttl):
            return self._sensor_cache
        
        data = {'temperatures': [], 'fans': [], 'power': []}
        
        try:
            # Get temperature sensors
            if hasattr(psutil, 'sensors_temperatures'):
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        for entry in entries:
                            temp_info = {
                                'name': entry.label or name,
                                'current': entry.current,
                                'high': entry.high,
                                'critical': getattr(entry, 'critical', None),
                                'status': self._get_temp_status(entry.current)
                            }
                            
                            # Check thresholds
                            if temp_info['status'] == 'critical':
                                store_alert('Temperature', 'critical', 
                                          f"Sensor '{temp_info['name']}' temperature is critical: {temp_info['current']}°C")
                            elif temp_info['status'] == 'warning':
                                store_alert('Temperature', 'warning',
                                          f"Sensor '{temp_info['name']}' temperature is high: {temp_info['current']}°C")
                            
                            data['temperatures'].append(temp_info)
            
            # Get fan sensors
            if hasattr(psutil, 'sensors_fans'):
                fans = psutil.sensors_fans()
                if fans:
                    for name, entries in fans.items():
                        for entry in entries:
                            fan_info = {
                                'name': entry.label or name,
                                'speed': entry.current,
                                'min': getattr(entry, 'min', None),
                                'max': getattr(entry, 'max', None)
                            }
                            data['fans'].append(fan_info)
            
            # Update history
            if data['temperatures']:
                self.history['temps'].append(max(t['current'] for t in data['temperatures']))
            if data['fans']:
                self.history['fans'].append(max(f['speed'] for f in data['fans']))
            
        except Exception as e:
            logger.debug(f"Error getting sensor data: {e}")
        
        # Cache the result
        self._sensor_cache = data
        self._sensor_cache_time = current_time
        return data
    
    def _get_temp_status(self, temp):
        """Determine temperature status"""
        if temp >= self.critical_temp:
            return 'critical'
        elif temp >= self.warning_temp:
            return 'warning'
        return 'normal'

    def _get_battery_info(self):
        """Get battery information"""
        battery_info = {'available': False, 'percent': 0, 'power_plugged': False, 'secsleft': 0, 'status': 'Unknown'}
        
        try:
            if hasattr(psutil, 'sensors_battery'):
                battery = psutil.sensors_battery()
                if battery:
                    battery_info['available'] = True
                    battery_info['percent'] = battery.percent
                    battery_info['power_plugged'] = battery.power_plugged
                    battery_info['secsleft'] = battery.secsleft
                    
                    if battery.power_plugged:
                        if battery.percent < 100:
                            battery_info['status'] = "Charging"
                        else:
                            battery_info['status'] = "Fully Charged"
                    else:
                        battery_info['status'] = "Discharging"
                        
                    # Check threshold
                    if not battery.power_plugged:
                        if battery.percent <= config['monitors']['battery']['critical_threshold']:
                            store_alert('Battery', 'critical', f"Battery level is critical: {battery.percent:.1f}%")
                        elif battery.percent <= config['monitors']['battery']['warning_threshold']:
                            store_alert('Battery', 'warning', f"Battery level is low: {battery.percent:.1f}%")
        except Exception:
            pass
            
        return battery_info
    
    def render(self):
        """Render sensor information panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,1))
            table.add_column("Sensor", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            sensor_data = self._get_sensor_data()
            
            # Show temperatures
            for temp in sensor_data['temperatures']:
                status_color = {
                    'normal': 'green',
                    'warning': 'yellow',
                    'critical': 'red'
                }[temp['status']]
                
                details = [f"Current: {temp['current']:4.1f}°C"]
                if temp['high']:
                    details.append(f"High: {temp['high']:4.1f}°C")
                if temp['critical']:
                    details.append(f"Critical: {temp['critical']:4.1f}°C")
                
                # Calculate percentage for progress bar
                if temp['critical']:
                    temp_percent = (temp['current'] / temp['critical']) * 100
                elif temp['high']:
                    temp_percent = (temp['current'] / temp['high']) * 100
                else:
                    temp_percent = (temp['current'] / 100) * 100
                
                table.add_row(
                    temp['name'][:12],
                    self._create_bar_without_percentage(min(100, temp_percent), color=status_color),
                    " | ".join(details)
                )
            
            # Show fans
            if sensor_data['fans']:
                if sensor_data['temperatures']:
                    table.add_row("", "", "")
                table.add_row("Fans", "", "")
                for fan in sensor_data['fans']:
                    details = []
                    if fan['min'] is not None:
                        details.append(f"Min: {fan['min']} RPM")
                    if fan['max'] is not None:
                        details.append(f"Max: {fan['max']} RPM")
                    
                    table.add_row(
                        fan['name'][:12],
                        Text(f"{fan['speed']} RPM", style="cyan"),
                        " | ".join(details) if details else ""
                    )
            
            # Battery info
            battery_info = self._get_battery_info()
            if battery_info['available']:
                table.add_row("", "", "")
                table.add_row("Battery", "", "")
                
                # Determine status color
                if battery_info['power_plugged']:
                    status_color = "green"
                elif battery_info['percent'] > 50:
                    status_color = "green"
                elif battery_info['percent'] > 20:
                    status_color = "yellow"
                else:
                    status_color = "red"
                
                table.add_row(
                    "Charge",
                    self._create_bar_without_percentage(battery_info['percent'], color=status_color),
                    f"{battery_info['percent']:.1f}% remaining"
                )
                
                # Time left
                secsleft = battery_info['secsleft']
                if secsleft == psutil.POWER_TIME_UNLIMITED:
                    time_left = "Unlimited"
                elif secsleft == psutil.POWER_TIME_UNKNOWN:
                    time_left = "Unknown"
                else:
                    hours, remainder = divmod(secsleft, 3600)
                    minutes, _ = divmod(remainder, 60)
                    time_left = f"{hours:02d}:{minutes:02d}"
                
                power_source = "AC Power" if battery_info['power_plugged'] else "Battery"
                table.add_row("Power", power_source, f"Time left: {time_left}")
                table.add_row("Status", battery_info['status'], "")
            
            if not any([sensor_data['temperatures'], sensor_data['fans'], battery_info['available']]):
                return Panel(
                    Text("No sensor data available", style="yellow"),
                    title="Sensor Monitor",
                    border_style="red"
                )
            
            return Panel(table, title="Sensor Monitor", border_style="red")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Sensor Monitor Error: {str(e)}", style="yellow"), 
                       title="Sensor Monitor", border_style="red")


class FirewallMonitor(BaseMonitor):
    """Firewall monitor - OPTIMIZED with cached iptables/nftables calls"""
    
    def __init__(self):
        super().__init__()
        self.last_blocked = 0
        self.last_check_time = time.time()
        self.rules_cache = None
        self.rules_cache_time = 0
        self.rules_cache_ttl = 10  # Cache rules for 10 seconds
    
    def _get_blocked_count(self) -> int:
        """Get count of blocked connections with caching"""
        blocked = 0
        
        def get_iptables_data():
            cmd = ['iptables', '-L', 'INPUT', '-v', '-n']
            return subprocess.run(cmd, capture_output=True, text=True, timeout=1)
        
        try:
            result = command_cache.get_or_compute('iptables_blocks', get_iptables_data, ttl=5.0)
            if result and result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'DROP' in line or 'REJECT' in line:
                        try:
                            blocked += int(line.split()[0])
                        except (IndexError, ValueError):
                            continue
        except Exception:
            pass
        
        return blocked
    
    def _get_connection_stats(self) -> dict:
        """Get connection statistics"""
        connections = {'ESTABLISHED': 0, 'LISTEN': 0, 'TIME_WAIT': 0, 'total': 0}
        
        try:
            for conn in psutil.net_connections(kind='inet'):
                connections['total'] += 1
                status = conn.status
                if status in connections:
                    connections[status] += 1
        except Exception:
            pass
            
        return connections

    def _get_firewall_rules(self) -> list:
        """Get firewall rules with caching"""
        current_time = time.time()
        
        # Return cached rules if valid
        if (self.rules_cache and 
            current_time - self.rules_cache_time < self.rules_cache_ttl):
            return self.rules_cache
        
        rules = []
        
        def get_iptables_rules():
            cmd = ['iptables', '-L', 'INPUT', '-n', '-v']
            return subprocess.run(cmd, capture_output=True, text=True, timeout=1)
        
        try:
            result = command_cache.get_or_compute('iptables_rules', get_iptables_rules, ttl=10.0)
            if result and result.returncode == 0:
                lines = result.stdout.split('\n')[2:]  # Skip headers
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 4:
                            rules.append({
                                'chain': 'INPUT',
                                'target': parts[2],
                                'protocol': parts[3],
                                'source': parts[7] if len(parts) > 7 else '*',
                                'destination': parts[8] if len(parts) > 8 else '*'
                            })
        except Exception:
            pass
        
        # Update cache
        self.rules_cache = rules
        self.rules_cache_time = current_time
        return rules
    
    def render(self) -> Panel:
        """Render firewall status panel - OPTIMIZED"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Firewall", style="cyan", width=12, no_wrap=True)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Calculate blocked rate
            current_blocked = self._get_blocked_count()
            current_time = time.time()
            time_diff = current_time - self.last_check_time
            
            if time_diff > 0:
                blocked_rate = (current_blocked - self.last_blocked) / time_diff
            else:
                blocked_rate = 0
            
            self.last_blocked = current_blocked
            self.last_check_time = current_time
            
            # Get connection info
            connections = self._get_connection_stats()
            
            # Display connection status
            table.add_row(
                "Connections",
                f"Total: {connections['total']}",
                f"Active: {connections['ESTABLISHED']} | Listening: {connections['LISTEN']}"
            )
            
            # Display block rate
            table.add_row(
                "Blocked",
                create_progress_bar(min(blocked_rate * 10, 100)),
                f"Rate: {blocked_rate:.1f}/s | Total: {current_blocked}"
            )
            
            # Get and display rules
            rules = self._get_firewall_rules()
            rules_by_target = {'ACCEPT': 0, 'DROP': 0, 'REJECT': 0}
            
            for rule in rules:
                target = rule['target'].upper()
                if target in rules_by_target:
                    rules_by_target[target] += 1
            
            table.add_row(
                "Rules",
                f"Total: {len(rules)}",
                f"Accept: {rules_by_target['ACCEPT']} | Drop: {rules_by_target['DROP']} | Reject: {rules_by_target['REJECT']}"
            )
            
            # Display recent rules
            for rule in rules[:3]:
                target_color = {
                    'ACCEPT': 'green',
                    'DROP': 'red',
                    'REJECT': 'red'
                }.get(rule['target'].upper(), 'white')
                
                status = Text(rule['target'].upper(), target_color)
                table.add_row(
                    rule['chain'][:8],
                    status,
                    f"{rule['protocol']} {rule['source']} → {rule['destination']}"
                )
            
            return Panel(table, title="Firewall Monitor", border_style="red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Firewall Monitor Error: {str(e)}", style="red"), 
                       title="Firewall Monitor", border_style="red")


class AlertMonitor(BaseMonitor):
    """Alert monitor to display active system alerts"""
    
    def render(self) -> Panel:
        """Render alert information panel"""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Time", style="cyan", width=8)
            table.add_column("Category", style="bright_blue", width=12)
            table.add_column("Message", ratio=1)
            
            recent_alerts = list(alert_history)[-10:]
            
            if not recent_alerts:
                return Panel(Text("No recent alerts", style="green"), 
                           title="Alert Monitor", border_style="red")
            
            for alert in reversed(recent_alerts):
                time_str = alert['timestamp'].strftime("%H:%M:%S")
                level_color = "red" if alert['level'] == 'critical' else "yellow"
                
                table.add_row(
                    time_str,
                    Text(alert['category'], level_color),
                    alert['message']
                )
            
            return Panel(table, title=f"Alert Monitor ({len(alert_history)} alerts)", 
                       border_style="red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Alert Monitor Error: {str(e)}", style="red"), 
                       title="Alert Monitor", border_style="red")
            
            
            
# PART 4: Main Application Class and Signal Handling - FINAL OPTIMIZED PART

def restore_terminal():
    """Restore terminal to normal state"""
    try:
        # Reset terminal settings
        os.system('stty sane')
        # Clear screen and reset cursor
        os.system('clear')
        print('\033[?25h')  # Show cursor
        print('\033[2J')    # Clear screen
        print('\033[H')     # Move cursor to home position
        logger.debug("Terminal restored")
    except Exception as e:
        logger.error(f"Error restoring terminal: {e}")

def setup_signal_handlers():
    """Set up CTRL+C handler with proper terminal cleanup"""
    def clean_exit(sig, frame):
        logger.info(f"Received signal {sig}, cleaning up and exiting")
        # First restore terminal
        restore_terminal()
        # Clean shutdown of thread pool
        try:
            system_data_collector._executor.shutdown(wait=False)
        except:
            pass
        # Then exit cleanly
        sys.exit(0)
    
    try:
        # Register the clean exit handler
        signal.signal(signal.SIGINT, clean_exit)
        signal.signal(signal.SIGTERM, clean_exit)
        logger.debug("Signal handlers installed")
    except Exception as e:
        logger.error(f"Failed to set up signal handlers: {e}")


class SystemMonitorApp(App):
    """Main system monitoring application - OPTIMIZED with efficient data flow"""
    
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
    
    def __init__(self):
        try:
            super().__init__()
            self.start_time = datetime.now()
            self.prevent_exit_confirmations = True
            Screen.DIALOG_CLASSES = []  # Disable all dialog classes
            
            # Configure monitors visibility from config
            self.show_monitors = {name: data['enabled'] 
                                for name, data in config['monitors'].items()}
            
            # Add alerts monitor
            self.show_monitors['alerts'] = True
            
            # Header update optimization
            self._last_header_update = 0
            self._header_update_interval = 1.0  # Update header every 1 second
            self._cached_system_info = None
            
            # Create monitor instances - OPTIMIZED creation with error handling
            logger.info("Creating optimized monitor instances...")
            self.monitors = {}
            
            monitor_classes = [
                ('self', SelfMonitor),
                ('cpu', CPUMonitor),
                ('memory', MemoryMonitor),
                ('disk', DiskMonitor),
                ('network', NetworkMonitor),
                ('gpu', GPUMonitor),
                ('services', ServiceMonitor),
                ('firewall', FirewallMonitor),
                ('sensors', SensorMonitor),
                ('process', ProcessMonitor),
                ('alerts', AlertMonitor)
            ]
            
            # Create monitors with individual error handling
            for name, monitor_class in monitor_classes:
                try:
                    self.monitors[name] = monitor_class()
                    logger.debug(f"Created optimized {name} monitor")
                except Exception as e:
                    logger.error(f"Failed to create {name} monitor: {e}")
                    # Continue without this monitor rather than crashing
            
            logger.info(f"SystemMonitorApp initialized with {len(self.monitors)} monitors")
            
        except Exception as e:
            logger.critical(f"CRITICAL ERROR in SystemMonitorApp init: {e}\n{traceback.format_exc()}")
            # Initialize with minimal defaults to prevent crashes
            self.start_time = datetime.now()
            self.show_monitors = {}
            self.monitors = {}
            self._last_header_update = 0
            self._header_update_interval = 1.0
            self._cached_system_info = None
    
    def on_key(self, event) -> None:
        """Handle key events and forward to process monitor - OPTIMIZED"""
        try:
            if event.key == "ctrl+c":
                logger.info("CTRL+C pressed, exiting")
                self.exit()
            elif event.key in ["c", "m"]:
                # Forward to ProcessMonitor if it exists and is enabled
                process_monitor = self.monitors.get('process')
                if process_monitor and self.show_monitors.get('process', True):
                    logger.debug(f"Forwarding {event.key} key to process monitor")
                    process_monitor.handle_sort_key(event.key)
                else:
                    logger.debug(f"Process monitor not available for key {event.key}")
        except Exception as e:
            logger.error(f"Error in on_key: {e}")
    
    def _on_exit(self) -> None:
        """Ensure terminal is restored and resources cleaned up on exit"""
        try:
            logger.info("App exiting, cleaning up resources")
            # Shutdown the thread pool executor
            try:
                system_data_collector._executor.shutdown(wait=True, timeout=2)
            except Exception as e:
                logger.debug(f"Error shutting down thread pool: {e}")
            
            # Clear caches
            try:
                command_cache.clear()
            except Exception as e:
                logger.debug(f"Error clearing command cache: {e}")
            
            # Restore terminal
            restore_terminal()
        except Exception as e:
            logger.error(f"Error in _on_exit: {e}")
    
    def _get_cached_system_info(self) -> Dict[str, str]:
        """Get cached system information for header"""
        if not self._cached_system_info:
            try:
                # Cache static system info that doesn't change
                self._cached_system_info = {
                    'os': f"{PLATFORM_INFO['system'].title()} {PLATFORM_INFO['release']}",
                    'python': PLATFORM_INFO['python_version'],
                    'cores': str(psutil.cpu_count()),
                    'ram': format_bytes(psutil.virtual_memory().total)
                }
            except Exception as e:
                logger.error(f"Error caching system info: {e}")
                self._cached_system_info = {
                    'os': 'Unknown', 'python': 'Unknown', 'cores': '?', 'ram': '?'
                }
        return self._cached_system_info
    
    def compose(self) -> ComposeResult:
        """Compose the UI layout - OPTIMIZED"""
        try:
            # Get cached system info
            sys_info = self._get_cached_system_info()
            
            # Initial header with static info
            header_text = [
                f"OS: {sys_info['os']}",
                f"Python: {sys_info['python']}",
                f"Cores: {sys_info['cores']}",
                f"RAM: {sys_info['ram']}",
                "Uptime: Starting..."
            ]
            yield Header(" | ".join(header_text))
            
            # Left and right containers
            yield Container(id="left-column")
            yield Container(id="right-column")
            
            # Footer with key bindings
            yield Footer()
            
            logger.debug("UI composition complete")
        except Exception as e:
            logger.critical(f"Critical error in compose: {e}\n{traceback.format_exc()}")
            # Try to yield minimal UI if something fails
            yield Header("System Monitor - ERROR")
            yield Container(id="left-column")
            yield Container(id="right-column")
            yield Footer()
    
    def on_mount(self) -> None:
        """Set up monitor layout and optimized refresh timers"""
        try:
            # Set up the initial monitor layout
            self.refresh_monitors()
            
            # Set up optimized header update timer (less frequent)
            try:
                self.set_interval(self._header_update_interval, self._update_header)
                logger.debug("Optimized header update timer set")
            except Exception as e:
                logger.error(f"Failed to set up header update timer: {e}")
            
            # Pre-warm the data collector
            try:
                system_data_collector.get_system_snapshot()
                logger.debug("Data collector pre-warmed")
            except Exception as e:
                logger.debug(f"Data collector pre-warm failed: {e}")
            
            logger.info("App mounted successfully with optimizations")
        except Exception as e:
            logger.error(f"Error in on_mount: {e}\n{traceback.format_exc()}")
    
    def refresh_monitors(self) -> None:
        """Refresh the monitor layout based on visibility settings - OPTIMIZED"""
        try:
            logger.debug("Refreshing optimized monitor layout")
            
            # Get the containers
            left_container = self.query_one("#left-column", Container)
            right_container = self.query_one("#right-column", Container)
            
            # Remove all monitors efficiently
            left_container.remove_children()
            right_container.remove_children()
            
            # Get layout from config
            left_monitors = list(config['ui']['left_column'])
            if 'alerts' not in left_monitors and 'alerts' not in config['ui']['right_column']:
                left_monitors.append('alerts')
                
            right_monitors = list(config['ui']['right_column'])
            
            # Mount monitors with error handling
            mounted_count = 0
            for name in left_monitors:
                if name in self.monitors and self.show_monitors.get(name, True):
                    try:
                        left_container.mount(self.monitors[name])
                        mounted_count += 1
                        logger.debug(f"Mounted {name} to left column")
                    except Exception as e:
                        logger.error(f"Failed to mount {name} to left column: {e}")
            
            for name in right_monitors:
                if name in self.monitors and self.show_monitors.get(name, True):
                    try:
                        right_container.mount(self.monitors[name])
                        mounted_count += 1
                        logger.debug(f"Mounted {name} to right column")
                    except Exception as e:
                        logger.error(f"Failed to mount {name} to right column: {e}")
                        
            logger.info(f"Successfully mounted {mounted_count} monitors")
                    
        except Exception as e:
            logger.error(f"Error refreshing monitors: {e}\n{traceback.format_exc()}")
    
    def _update_header(self) -> None:
        """Update the header with current uptime and system stats - OPTIMIZED"""
        try:
            current_time = time.time()
            
            # Skip update if called too frequently
            if current_time - self._last_header_update < self._header_update_interval:
                return
            
            # Calculate uptime efficiently
            uptime_seconds = int(current_time - self.start_time.timestamp())
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            if hours > 0:
                uptime_str = f"{hours}h {minutes}m {seconds}s"
            else:
                uptime_str = f"{minutes}m {seconds}s"
            
            # Get cached system info
            sys_info = self._get_cached_system_info()
            
            header_text = [
                f"OS: {sys_info['os']}",
                f"Python: {sys_info['python']}",
                f"Cores: {sys_info['cores']}",
                f"RAM: {sys_info['ram']}",
                f"Uptime: {uptime_str}"
            ]
            
            # Update the header efficiently
            try:
                header = self.query_one(Header)
                if header:
                    new_text = " | ".join(header_text)
                    if header.text != new_text:  # Only update if changed
                        header.text = new_text
            except Exception as e:
                logger.debug(f"Error updating header text: {e}")
            
            self._last_header_update = current_time
            
        except Exception as e:
            logger.debug(f"Error updating header: {e}")


def main():
    """Run monitor with optimized startup and clean error handling"""
    # Set terminal title early
    try:
        set_terminal_title(os.path.basename(__file__))
    except Exception:
        pass
    
    # Initialize configuration directory
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except Exception as e:
        print(f"Failed to create config directory: {e}")
        return 1
    
    try:
        console.print("[bold green]Optimized System Monitor starting...[/bold green]")
        
        # Load configuration first
        try:
            load_config()
            logger.info("Configuration loaded successfully")
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}\n{traceback.format_exc()}")
            console.print(f"[bold red]Failed to load configuration: {e}[/bold red]")
            return 1
        
        # Set up clean exit handlers
        try:
            setup_signal_handlers()
            logger.info("Signal handlers setup complete")
        except Exception as e:
            logger.error(f"Signal handler setup failed: {e}")
            console.print(f"[bold yellow]Warning: Signal handler setup failed: {e}[/bold yellow]")
        
        # Validate system requirements
        try:
            # Check if we can access basic system info
            psutil.cpu_percent()
            psutil.virtual_memory()
            logger.info("System access validation successful")
        except Exception as e:
            logger.error(f"System access validation failed: {e}")
            console.print(f"[bold red]System access error: {e}[/bold red]")
            return 1
        
        # Pre-initialize the global data collector
        try:
            logger.info("Pre-initializing optimized data collector...")
            system_data_collector.get_system_snapshot()
            logger.info("Data collector initialization successful")
        except Exception as e:
            logger.warning(f"Data collector pre-initialization failed: {e}")
            # Continue anyway, it will initialize on first use
        
        # Run the optimized app
        try:
            logger.info("Starting optimized SystemMonitorApp")
            console.print("[green]Starting optimized system monitor interface...[/green]")
            
            app = SystemMonitorApp()
            app.run()
            
            logger.info("SystemMonitorApp exited normally")
            console.print("[green]System monitor stopped cleanly[/green]")
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
            console.print("[yellow]System monitor interrupted by user[/yellow]")
        except Exception as e:
            logger.critical(f"Critical application error: {e}\n{traceback.format_exc()}")
            console.print(f"[bold red]Critical application error: {e}[/bold red]")
            restore_terminal()
            return 1
        
        return 0
        
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt during startup")
        console.print("[yellow]Startup interrupted by user[/yellow]")
        restore_terminal()
        return 0
    except Exception as e:
        # Catch-all for any unexpected errors
        try:
            logger.critical(f"Unhandled exception in main: {e}\n{traceback.format_exc()}")
        except:
            pass  # If even logging fails
        
        # Clean up on any error
        restore_terminal()
        console.print(f"[bold red]Fatal error: {e}[/bold red]")
        return 1
    finally:
        # Final cleanup
        try:
            restore_terminal()
            # Cleanup global resources
            try:
                system_data_collector._executor.shutdown(wait=False)
            except:
                pass
            try:
                command_cache.clear()
            except:
                pass
            logger.info("System Monitor shutdown complete")
        except:
            pass


if __name__ == "__main__":
    """Main entry point with comprehensive error handling"""
    try:
        # Ensure we have a clean start
        logger.info("="*60)
        logger.info("OPTIMIZED SYSTEM MONITOR STARTING")
        logger.info("="*60)
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Platform: {PLATFORM_INFO['system']} {PLATFORM_INFO['release']}")
        logger.info(f"CPU cores: {psutil.cpu_count()}")
        logger.info(f"Total RAM: {format_bytes(psutil.virtual_memory().total)}")
        
        # Check dependencies
        missing_deps = []
        try:
            import psutil
        except ImportError:
            missing_deps.append('psutil')
        
        try:
            import textual
        except ImportError:
            missing_deps.append('textual')
            
        try:
            import rich
        except ImportError:
            missing_deps.append('rich')
            
        try:
            import yaml
        except ImportError:
            missing_deps.append('pyyaml')
        
        if missing_deps:
            print(f"Missing dependencies: {', '.join(missing_deps)}")
            print("Install with: pip install " + ' '.join(missing_deps))
            sys.exit(1)
        
        # Run the main application
        exit_code = main()
        
        logger.info("="*60)
        logger.info(f"OPTIMIZED SYSTEM MONITOR STOPPED (exit code: {exit_code})")
        logger.info("="*60)
        
        sys.exit(exit_code)
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        restore_terminal()
        sys.exit(0)
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
        restore_terminal()
        sys.exit(1)
    except SystemExit:
        # Handle clean exits
        restore_terminal()
        raise