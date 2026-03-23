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
from collections import deque
from pathlib import Path
import traceback

# Configure basic logging first - will be enhanced later
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("SystemMonitor")
logger.info("Starting System Monitor initialization...")

# Required third-party imports
try:
    # Rich for text formatting
    from rich import box
    from rich.text import Text
    from rich.panel import Panel
    from rich.table import Table
    from rich.logging import RichHandler
    from rich.console import Console
    console = Console()
    
    # Textual for UI
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Static
    from textual.containers import Grid, Container
    from textual.binding import Binding
    
    # psutil for system monitoring
    import psutil
    
    logger.info("All required libraries imported successfully")
except ImportError as e:
    print(f"ERROR: Required dependency not found: {e}")
    print("Please install with: pip install psutil textual rich pyyaml")
    sys.exit(1)

# Disable dialog classes to prevent popups
try:
    from textual.screen import Screen
    Screen.DIALOG_CLASSES = []  # Remove all dialog classes
    logger.info("Dialog classes disabled")
except Exception as e:
    logger.error(f"Failed to disable dialog classes: {e}")

# Configuration
CONFIG_DIR = os.path.expanduser("~/.config/system_monitor")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "system_monitor.log")

# Create config directory if it doesn't exist
try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    logger.info(f"Config directory ensured: {CONFIG_DIR}")
except Exception as e:
    logger.error(f"Failed to create config directory: {e}")

# Now that we have a directory, enhance logging
try:
    log_dir = os.path.join(CONFIG_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # Reconfigure logging with file handler
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add both file and stream handlers
    file_handler = logging.FileHandler(os.path.join(log_dir, 'system_monitor.log'))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    
    rich_handler = RichHandler(rich_tracebacks=True)
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(rich_handler)
    root_logger.setLevel(logging.INFO)  # Changed from DEBUG to INFO
    
    logger.info("Enhanced logging configured")
except Exception as e:
    logger.error(f"Failed to set up enhanced logging: {e}")
    # Carry on with basic logging

# Default configuration - NO DATABASE
DEFAULT_CONFIG = {
    'monitors': {
        'cpu': {'enabled': True, 'interval': 1.1, 'warning_threshold': 75, 'critical_threshold': 90},
        'memory': {'enabled': True, 'interval': 2.1, 'warning_threshold': 75, 'critical_threshold': 90},
        'disk': {'enabled': True, 'interval': 3.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'network': {'enabled': True, 'interval': 1.1},
        'gpu': {'enabled': True, 'interval': 2.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'sensors': {'enabled': True, 'interval': 3.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'services': {'enabled': True, 'interval': 5.0},
        'process': {'enabled': True, 'interval': 3.0, 'limit': 20},  # Set to 20 processes
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
        'persistence_enabled': False  # Explicitly disable database
    }
}

# Platform detection and capabilities
try:
    PLATFORM_INFO = {
        'system': platform.system().lower(),
        'release': platform.release(),
        'version': platform.version(),
        'machine': platform.machine(),
        'is_linux': platform.system().lower() == 'linux',
        'python_version': sys.version.split()[0],
        'processor': platform.processor()
    }
    logger.info(f"System detected: {PLATFORM_INFO['system']} {PLATFORM_INFO['release']}")
except Exception as e:
    logger.error(f"Failed to gather platform info: {e}")
    PLATFORM_INFO = {
        'system': 'unknown',
        'release': 'unknown',
        'version': 'unknown',
        'machine': 'unknown',
        'is_linux': False,
        'python_version': 'unknown',
        'processor': 'unknown'
    }

# Global config and state
config = {}
should_exit = False
alert_history = deque(maxlen=100)

def load_config():
    """Load configuration from file or create default"""
    global config
    
    try:
        # If config file doesn't exist, create it with defaults
        if not os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'w') as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False)
            config = DEFAULT_CONFIG
            logger.info(f"Created default configuration at {CONFIG_FILE}")
        else:
            # Load existing config
            try:
                with open(CONFIG_FILE, 'r') as f:
                    loaded_config = yaml.safe_load(f)
                
                # Merge with defaults in case of missing keys
                config = DEFAULT_CONFIG.copy()
                
                # Update config with loaded values
                if loaded_config:
                    for section in config:
                        if section in loaded_config:
                            if isinstance(config[section], dict) and isinstance(loaded_config[section], dict):
                                for key in config[section]:
                                    if key in loaded_config[section]:
                                        config[section][key] = loaded_config[section][key]
                
                # Force persistence to be disabled
                if 'data' in config:
                    config['data']['persistence_enabled'] = False
                
                logger.info(f"Loaded configuration from {CONFIG_FILE}")
            except Exception as e:
                logger.error(f"Error loading config: {e}, using defaults")
                config = DEFAULT_CONFIG
    except Exception as e:
        logger.error(f"Critical error in load_config: {e}")
        config = DEFAULT_CONFIG
    
    # Update monitor intervals
    global MONITOR_INTERVALS, CORES_PER_LINE
    try:
        MONITOR_INTERVALS = {k: v['interval'] for k, v in config['monitors'].items()}
        CORES_PER_LINE = config['ui']['cores_per_line']
    except Exception as e:
        logger.error(f"Failed to update intervals from config: {e}")
        # Use defaults if update fails
        MONITOR_INTERVALS = {
            'cpu': 1.1, 'memory': 2.1, 'network': 1.1, 'disk': 3.0,
            'gpu': 2.0, 'sensors': 3.0, 'services': 5.0, 'process': 3.0,
            'battery': 5.0, 'firewall': 5.0, 'self': 1.0
        }
        CORES_PER_LINE = 4

# Monitor intervals (in seconds) - will be updated from config
MONITOR_INTERVALS = {
    'cpu': 1.1,      # CPU monitor refresh
    'memory': 2.1,   # Memory monitor refresh
    'network': 1.1,  # Network monitor refresh
    'disk': 3.0,     # Disk monitor refresh
    'gpu': 2.0,      # GPU monitor refresh
    'sensors': 3.0,  # Sensors monitor refresh
    'services': 5.0, # Services monitor refresh
    'process': 3.0,  # Process monitor refresh
    'battery': 5.0,  # Battery monitor refresh
    'firewall': 5.0, # Firewall monitor refresh
    'self': 1.0      # Self monitor refresh
}

# Number of CPU cores to display per line
CORES_PER_LINE = 4

# Simple function for alerts (without database)
def store_alert(category, level, message):
    """Store an alert in memory and log if critical"""
    try:
        alert = {
            'timestamp': datetime.now(),
            'category': category,
            'level': level,
            'message': message
        }
        
        # Add to in-memory history
        alert_history.append(alert)
        
        # Log to file if enabled
        if config['alerts']['log_critical_events'] and level == 'critical':
            logger.critical(f"ALERT - {category}: {message}")
        
        # Show desktop notification if enabled
        if config['alerts']['desktop_notification']:
            try:
                # Try using system notification
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

def set_terminal_title(title: str) -> None:
    """Set the terminal window title."""
    try:
        if os.name == 'nt':  # Windows
            os.system(f'title {title}')
        else:  # Unix-like
            print(f'\033]0;{title}\007', end='', flush=True)
    except Exception as e:
        logger.error(f"Failed to set terminal title: {e}")
        
# Set title to current filename
try:
    set_terminal_title(os.path.basename(__file__))
    logger.debug("Terminal title set")
except Exception as e:
    logger.error(f"Failed to set terminal title: {e}")

def format_bytes(bytes_value: float) -> str:
    """Format bytes to human readable string."""
    try:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024:
                return f"{bytes_value:6.1f}{unit}"
            bytes_value /= 1024
        return f"{bytes_value:6.1f}TB"
    except Exception as e:
        logger.error(f"Error formatting bytes: {e}")
        return "0.0B"

# UNIFIED PROGRESS BAR FUNCTION - replaces all previous progress bar functions
def create_unified_progress_bar(percentage: float, width: int = 40, 
                               show_percentage: bool = True, 
                               percentage_position: str = "after",
                               custom_color: str = None) -> Text:
    """Create a unified progress bar that works consistently across all monitors.
    
    Args:
        percentage: Value between 0-100 to display
        width: Width of the progress bar in characters
        show_percentage: Whether to show percentage text
        percentage_position: Where to show percentage ("before" or "after")
        custom_color: Override the default color selection
        
    Returns:
        Rich Text object containing formatted progress bar
    """
    try:
        # Ensure percentage is within bounds
        percentage = max(0, min(100, percentage))
        
        # Adjust width if showing percentage to ensure it fits
        effective_width = width
        if show_percentage:
            if percentage_position == "before" or percentage_position == "after":
                # Reserve about 8 chars for the percentage display
                effective_width = max(10, width - 8)
        
        # Calculate filled and empty portions
        filled = int(effective_width * percentage / 100)
        remainder = effective_width - filled
        
        # Determine color based on percentage if not provided
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
        
        # Create the bar with the specified color
        bar = Text('■' * filled, color) + Text('·' * remainder, "bright_black")
        
        # Add percentage text based on position preference
        if show_percentage:
            percentage_text = Text(f" {percentage:5.1f}%", color)
            if percentage_position == "before":
                return Text(f"{percentage:5.1f}% ", color) + bar
            else:  # "after" is default
                return bar + percentage_text
        else:
            return bar
    except Exception as e:
        logger.error(f"Error creating unified progress bar: {e}")
        return Text("Error", "red")

class BaseMonitor(Static):
    """Base monitor class with common functionality."""
    
    DEFAULT_CSS = """
    BaseMonitor {
        height: auto;
        margin: 0;
        padding: 0;
    }
    """
    
    def __init__(self):
        try:
            super().__init__()
            self.error_count = 0
            self.max_errors = 3
            monitor_type = self.__class__.__name__.lower().replace('monitor', '')
            self.alert_thresholds = {
                'warning': config['monitors'].get(monitor_type, {}).get('warning_threshold', 75),
                'critical': config['monitors'].get(monitor_type, {}).get('critical_threshold', 90)
            }
            logger.debug(f"Initialized {self.__class__.__name__} with thresholds: {self.alert_thresholds}")
        except Exception as e:
            logger.error(f"Error initializing {self.__class__.__name__}: {e}\n{traceback.format_exc()}")
            # Set default thresholds to allow operation to continue
            self.error_count = 0
            self.max_errors = 3
            self.alert_thresholds = {'warning': 75, 'critical': 90}
    
    def handle_error(self, error: Exception, context: str) -> None:
        """Handle and log errors with context."""
        self.error_count += 1
        logger.error(f"Error in {self.__class__.__name__} ({context}): {error}")
        if self.error_count >= self.max_errors:
            logger.warning(f"{self.__class__.__name__} experiencing repeated errors")
    
    def get_interval(self) -> float:
        """Get refresh interval for this monitor type."""
        try:
            monitor_type = self.__class__.__name__.lower().replace('monitor', '')
            return MONITOR_INTERVALS.get(monitor_type, 1.0)
        except Exception as e:
            logger.error(f"Error getting interval for {self.__class__.__name__}: {e}")
            return 3.0  # Safe default interval
    
    def on_mount(self) -> None:
        """Set refresh interval using configuration."""
        try:
            interval = self.get_interval()
            logger.debug(f"Setting refresh interval for {self.__class__.__name__}: {interval}s")
            self.set_interval(interval, self.refresh)
        except Exception as e:
            logger.error(f"Error in on_mount for {self.__class__.__name__}: {e}")
            # Try to set a safe default interval
            try:
                self.set_interval(3.0, self.refresh)
            except Exception as e2:
                logger.critical(f"Failed to set default interval: {e2}")
    
    def check_threshold(self, value: float, category: str, name: str = None) -> Optional[str]:
        """Check if a value exceeds warning or critical thresholds."""
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

class SelfMonitor(BaseMonitor):
    """Monitor for tracking this application's own resource usage."""
    
    def __init__(self):
        try:
            super().__init__()
            self.process = psutil.Process(os.getpid())
            self.start_time = time.time()
            self.last_io = None
            self.last_io_time = time.time()
            self.history = {
                'cpu': deque(maxlen=60),
                'memory': deque(maxlen=60),
                'io_read': deque(maxlen=30),
                'io_write': deque(maxlen=30)
            }
            self.peak_memory = 0
            self.peak_cpu = 0
            logger.debug("Enhanced Self monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Self monitor: {e}")
            self.process = None
            self.start_time = time.time()
            self.history = {'cpu': deque(maxlen=60), 'memory': deque(maxlen=60)}
            self.peak_memory = 0
            self.peak_cpu = 0
    
    def _get_self_metrics(self) -> Dict[str, Any]:
        """Get detailed resource usage metrics for this application."""
        try:
            if not self.process:
                return {}
                
            current_time = time.time()
            
            # Get CPU usage
            cpu_percent = self.process.cpu_percent()
            cpu_times = self.process.cpu_times()
            
            # Get memory usage
            memory_info = self.process.memory_info()
            mem_percent = self.process.memory_percent()
            
            # Track peak values
            self.peak_memory = max(self.peak_memory, memory_info.rss)
            self.peak_cpu = max(self.peak_cpu, cpu_percent)
            
            # Get I/O counters with rate calculation
            io_counters = None
            io_rates = {'read': 0, 'write': 0}
            
            if hasattr(self.process, 'io_counters'):
                io_counters = self.process.io_counters()
                
                # Calculate I/O rates
                if self.last_io and (current_time - self.last_io_time) > 0:
                    time_diff = current_time - self.last_io_time
                    io_rates = {
                        'read': (io_counters.read_bytes - self.last_io.read_bytes) / time_diff,
                        'write': (io_counters.write_bytes - self.last_io.write_bytes) / time_diff
                    }
                
                self.last_io = io_counters
                self.last_io_time = current_time
                
                # Update I/O history
                self.history['io_read'].append(io_rates['read'])
                self.history['io_write'].append(io_rates['write'])
            
            # Get context switches and file descriptors
            ctx_switches = 0
            if hasattr(self.process, 'num_ctx_switches'):
                ctx_data = self.process.num_ctx_switches()
                ctx_switches = ctx_data.voluntary + ctx_data.involuntary
            
            open_files = len(self.process.open_files()) if hasattr(self.process, 'open_files') else 0
            connections = len(self.process.connections()) if hasattr(self.process, 'connections') else 0
            
            # Get more detailed thread info
            thread_info = []
            if hasattr(self.process, 'threads'):
                thread_data = self.process.threads()
                thread_info = [{'id': t.id, 'user_time': t.user_time, 'system_time': t.system_time} for t in thread_data]
            
            # Update history
            self.history['cpu'].append(cpu_percent)
            self.history['memory'].append(mem_percent)
            
            # Calculate resource overhead compared to system total
            system_cpu = psutil.cpu_percent(interval=None)
            system_memory = psutil.virtual_memory()
            
            cpu_overhead = (cpu_percent / 100) / psutil.cpu_count() * 100
            memory_overhead = (memory_info.rss / system_memory.total) * 100
            
            # Calculate run time
            run_time = current_time - self.start_time
            
            return {
                # Basic usage
                'cpu_percent': cpu_percent,
                'memory_percent': mem_percent,
                'memory_bytes': memory_info.rss,
                
                # Detailed CPU metrics
                'cpu_user_time': cpu_times.user,
                'cpu_system_time': cpu_times.system,
                'cpu_idle_time': getattr(cpu_times, 'idle', 0),
                'cpu_overhead': cpu_overhead,
                
                # Detailed memory metrics
                'memory_rss': memory_info.rss,
                'memory_vms': memory_info.vms,
                'memory_shared': getattr(memory_info, 'shared', 0),
                'memory_text': getattr(memory_info, 'text', 0),
                'memory_data': getattr(memory_info, 'data', 0),
                'memory_overhead': memory_overhead,
                'peak_memory': self.peak_memory,
                
                # I/O metrics
                'read_bytes': io_counters.read_bytes if io_counters else 0,
                'write_bytes': io_counters.write_bytes if io_counters else 0,
                'read_rate': io_rates['read'],
                'write_rate': io_rates['write'],
                
                # Thread info
                'threads_count': len(thread_info),
                'threads': thread_info,
                
                # Additional metrics
                'ctx_switches': ctx_switches,
                'open_files': open_files,
                'connections': connections,
                'run_time': run_time,
                'peak_cpu': self.peak_cpu,
                
                # Averages from history
                'avg_cpu': sum(self.history['cpu']) / len(self.history['cpu']) if self.history['cpu'] else 0,
                'avg_memory': sum(self.history['memory']) / len(self.history['memory']) if self.history['memory'] else 0,
                'avg_io_read': sum(self.history['io_read']) / len(self.history['io_read']) if self.history['io_read'] else 0,
                'avg_io_write': sum(self.history['io_write']) / len(self.history['io_write']) if self.history['io_write'] else 0,
            }
        except Exception as e:
            self.handle_error(e, "get_self_metrics")
            return {}
    
    def render(self) -> Panel:
        """Render detailed monitoring information panel for the script's resource usage."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Resource", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            metrics = self._get_self_metrics()
            
            if not metrics:
                return Panel(Text("Self monitoring unavailable", style="yellow"), title="Monitor Script Usage", border_style="bright_red")
            
            # CPU usage - using the unified progress bar function
            cpu_percent = metrics['cpu_percent']
            cpu_color = "green" if cpu_percent < 50 else "yellow" if cpu_percent < 75 else "red"
            table.add_row(
                "CPU",
                create_unified_progress_bar(cpu_percent, custom_color=cpu_color),
                f"User: {metrics['cpu_user_time']:.1f}s | Sys: {metrics['cpu_system_time']:.1f}s | Peak: {metrics['peak_cpu']:.1f}%"
            )
            
            # CPU overhead - using the unified progress bar function
            cpu_oh = metrics['cpu_overhead']
            cpu_oh_color = "green" if cpu_oh < 1 else "yellow" if cpu_oh < 5 else "red"
            table.add_row(
                "CPU Overhead",
                create_unified_progress_bar(cpu_oh, custom_color=cpu_oh_color),
                f"{metrics['cpu_overhead']:.2f}% of system | Avg: {metrics['avg_cpu']:.1f}%"
            )
            
            # Memory usage - using the unified progress bar function
            mem_percent = metrics['memory_percent']
            mem_color = "green" if mem_percent < 50 else "yellow" if mem_percent < 75 else "red"
            table.add_row(
                "Memory",
                create_unified_progress_bar(mem_percent, custom_color=mem_color),
                f"{format_bytes(metrics['memory_bytes'])} ({metrics['memory_percent']:.1f}%)"
            )
            
            # Memory details
            table.add_row(
                "Mem Details",
                f"RSS: {format_bytes(metrics['memory_rss'])} | VMS: {format_bytes(metrics['memory_vms'])}",
                f"Peak: {format_bytes(metrics['peak_memory'])} | Overhead: {metrics['memory_overhead']:.2f}%"
            )
            
            # Thread count with details
            table.add_row(
                "Threads",
                f"{metrics['threads_count']} threads",
                f"Context Switches: {metrics['ctx_switches']}"
            )
            
            # I/O rates
            table.add_row(
                "I/O Rate",
                f"Read: {format_bytes(metrics['read_rate'])}/s",
                f"Write: {format_bytes(metrics['write_rate'])}/s"
            )
            
            # I/O totals
            table.add_row(
                "I/O Total",
                f"Read: {format_bytes(metrics['read_bytes'])}",
                f"Write: {format_bytes(metrics['write_bytes'])}"
            )
            
            # File handles
            table.add_row(
                "File Handles",
                f"Open Files: {metrics['open_files']}",
                f"Connections: {metrics['connections']}"
            )
            
            # Runtime info
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
            return Panel(Text(f"Self Monitor Error: {str(e)}", style="red"), title="Monitor Script Usage", border_style="bright_red")

class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor with processor name display."""
    
    def on_mount(self) -> None:
        """Initialize monitor and get processor details on mount."""
        try:
            super().on_mount()  # This will use the correct interval from MONITOR_INTERVALS
            self.processor_name = self._get_processor_name()
            logger.debug(f"CPU monitor initialized with processor: {self.processor_name}")
        except Exception as e:
            logger.error(f"Error mounting CPU monitor: {e}")
            self.processor_name = ""
    
    def _get_processor_name(self) -> str:
        """Get the processor name from system information."""
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
        """Get CPU frequency with multiple fallback methods."""
        try:
            # Method 1: psutil.cpu_freq()
            freq = psutil.cpu_freq()
            if freq and freq.current > 100:  # Sanity check - freq should be > 100MHz
                return int(freq.current)
                
            # Method 2: Try /proc/cpuinfo on Linux
            if sys.platform == "linux":
                try:
                    with open("/proc/cpuinfo", "r") as f:
                        for line in f:
                            if "cpu MHz" in line:
                                return int(float(line.split(":")[1].strip()))
                            # Some systems show frequency in GHz in model name
                            if "model name" in line and "GHz" in line:
                                model = line.split(":")[1].strip()
                                # Extract frequency like "2.60GHz"
                                ghz_match = re.search(r'(\d+\.\d+)GHz', model)
                                if ghz_match:
                                    return int(float(ghz_match.group(1)) * 1000)
                except Exception as e:
                    logger.debug(f"Failed to get CPU frequency from /proc/cpuinfo: {e}")
            
            # Method 3: Try /sys/devices/system/cpu/ on Linux
            if sys.platform == "linux":
                try:
                    # Try reading scaling_cur_freq
                    freq_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
                    if os.path.exists(freq_path):
                        with open(freq_path, "r") as f:
                            # Value is in kHz, convert to MHz
                            return int(int(f.read().strip()) / 1000)
                    
                    # Try cpuinfo_max_freq if scaling_cur_freq doesn't exist
                    max_freq_path = "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"
                    if os.path.exists(max_freq_path):
                        with open(max_freq_path, "r") as f:
                            # Value is in kHz, convert to MHz
                            return int(int(f.read().strip()) / 1000)
                except Exception as e:
                    logger.debug(f"Failed to get CPU frequency from sysfs: {e}")
            
            # Method 4: Parse from processor_name as last resort
            if self.processor_name:
                # Look for GHz or MHz in processor name
                ghz_match = re.search(r'(\d+\.\d+)GHz', self.processor_name)
                if ghz_match:
                    return int(float(ghz_match.group(1)) * 1000)
                
                mhz_match = re.search(r'(\d+)MHz', self.processor_name)
                if mhz_match:
                    return int(mhz_match.group(1))
                
                # Check for frequency at the end of model string like "@ 2.60GHz"
                ghz_match = re.search(r'@ (\d+\.\d+)GHz', self.processor_name)
                if ghz_match:
                    return int(float(ghz_match.group(1)) * 1000)
            
            # If all else fails, return 0 (unknown)
            return 0
            
        except Exception as e:
            logger.error(f"Error getting CPU frequency: {e}")
            return 0
    
    def _get_system_uptime(self) -> str:
        """Get system uptime in a human-readable format."""
        try:
            # Get boot time and calculate uptime
            boot_time = psutil.boot_time()
            uptime_seconds = time.time() - boot_time
            
            # Convert to days, hours, minutes, seconds
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            # Format the uptime
            if days > 0:
                return f"{int(days)}d {int(hours)}h {int(minutes)}m"
            elif hours > 0:
                return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
            else:
                return f"{int(minutes)}m {int(seconds)}s"
        except Exception as e:
            logger.error(f"Error getting system uptime: {e}")
            return "Unknown"
    
    def get_usage_color(self, percentage: float) -> str:
        """Determine color based on usage percentage."""
        try:
            if percentage < 50:
                return "green"
            elif percentage < 75:
                return "yellow"
            elif percentage < 90:
                return "red"
            return "bright_red"
        except Exception as e:
            logger.error(f"Error in get_usage_color: {e}")
            return "white"  # Safe default
    
    def create_core_row(self, start_idx: int, cpu_percent: list) -> list:
        """Create a row of CPU core displays."""
        cores_in_row = []
        try:
            for i in range(start_idx, min(start_idx + CORES_PER_LINE, len(cpu_percent))):
                color = self.get_usage_color(cpu_percent[i])
                core_text = Text(f"Core {i:2d}: ", "cyan") + create_unified_progress_bar(
                    cpu_percent[i], width=30, custom_color=color, percentage_position="before"
                )
                cores_in_row.append(core_text)
            return cores_in_row
        except Exception as e:
            logger.error(f"Error creating core row: {e}")
            return [Text("Error", "red")]
    
    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0,1))
            
            # Get CPU metrics
            cpu_percent = psutil.cpu_percent(percpu=True)
            times = psutil.cpu_times_percent()
            load = psutil.getloadavg()
            
            # Get CPU frequency with robust detection
            current_mhz = self._get_cpu_frequency()
            
            # Get system uptime
            uptime = self._get_system_uptime()
            
            # Calculate total CPU usage
            total = sum(cpu_percent) / len(cpu_percent)
            
            # Check for alerts
            self.check_threshold(total, 'CPU Usage', 'Total')
            
            # Create metrics header table
            metrics_table = Table(box=None, expand=True, padding=(0,1))
            metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
            metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)
            
            # Format CPU frequency with appropriate unit (MHz/GHz)
            freq_text = ""
            if current_mhz > 1000:
                freq_text = f"{current_mhz/1000:.2f}GHz"
            elif current_mhz > 0:
                freq_text = f"{current_mhz}MHz"
            else:
                freq_text = "Unknown"
                
            # Use unified progress bar function for total CPU usage
            total_color = self.get_usage_color(total)
            metrics_table.add_row(
                Text("Total: ") + create_unified_progress_bar(total, custom_color=total_color, percentage_position="before"),
                Text(f"Freq: {freq_text} | Load: {load[0]:5.2f}")
            )
            
            # Add CPU states and system uptime
            color_user = self.get_usage_color(times.user)
            color_sys = self.get_usage_color(times.system)
            states_text = (
                Text(f"User: {times.user:4.1f}% ", color_user) +
                Text(f"Sys: {times.system:4.1f}% ", color_sys) +
                Text(f"Idle: {times.idle:4.1f}%", "bright_black")
            )
            
            # Add uptime to the metrics
            uptime_text = Text(f"Uptime: {uptime}", "bright_green")
            metrics_table.add_row(states_text, uptime_text)
            
            # Add metrics to main table
            table.add_row(metrics_table)
            
            # Create cores table with dynamic columns
            cores_table = Table(box=None, expand=True, padding=(0,1))
            
            # Add columns based on CORES_PER_LINE
            for i in range(CORES_PER_LINE):
                cores_table.add_column("", ratio=1)  # Empty column title
            
            # Add core rows
            for i in range(0, len(cpu_percent), CORES_PER_LINE):
                cores_in_row = self.create_core_row(i, cpu_percent)
                # Pad the row with empty strings if needed
                while len(cores_in_row) < CORES_PER_LINE:
                    cores_in_row.append("")
                cores_table.add_row(*cores_in_row)
            
            # Add cores grid to main table
            table.add_row(cores_table)

            # Create title with processor name if available
            title = f"CPU Monitor"
            if self.processor_name:
                title += f" - {self.processor_name}"

            return Panel(
                table,
                title=title,
                border_style="blue"
            )
        except Exception as e:
            logger.error(f"Error rendering CPU monitor: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"CPU Monitor Error: {str(e)}", style="red"), title="CPU Monitor", border_style="blue")

class MemoryMonitor(BaseMonitor):
    """Memory usage monitor with independent layout."""
    
    def render(self) -> Panel:
        try:
            # Create monitor-specific table
            table = Table(box=None, expand=True, padding=(0,0))
            
            # Add columns specific to memory display
            table.add_column("Memory", style="cyan", width=12, no_wrap=True)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get memory metrics
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()

            # Check thresholds
            self.check_threshold(vm.percent, 'Memory Usage', 'RAM')
            self.check_threshold(swap.percent, 'Memory Usage', 'Swap')

            # RAM Usage - using unified progress bar
            table.add_row(
                "RAM",
                create_unified_progress_bar(vm.percent),
                f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}"
            )
            
            # Cache Usage - using unified progress bar
            cache_percent = (vm.cached / vm.total) * 100 if hasattr(vm, 'cached') else 0
            cache_text = ""
            if hasattr(vm, 'cached'):
                cache_text = f"Cached: {format_bytes(vm.cached)}"
                if hasattr(vm, 'buffers'):
                    cache_text += f" | Buffers: {format_bytes(vm.buffers)}"
                
                table.add_row(
                    "Cache",
                    create_unified_progress_bar(cache_percent),
                    cache_text
                )

            # Effective Memory - using unified progress bar
            if hasattr(vm, 'available') and hasattr(vm, 'cached') and hasattr(vm, 'buffers'):
                effective_used = vm.total - vm.available - vm.cached - vm.buffers
                if effective_used >= 0:
                    effective_percent = (effective_used / vm.total) * 100
                    table.add_row(
                        "Effective",
                        create_unified_progress_bar(effective_percent),
                        f"Used: {format_bytes(effective_used)} | Available: {format_bytes(vm.available)}"
                    )
            
            # Swap Usage - using unified progress bar
            table.add_row(
                "Swap",
                create_unified_progress_bar(swap.percent),
                f"Used: {format_bytes(swap.used)} / Total: {format_bytes(swap.total)}"
            )

            return Panel(table, title="Memory Monitor", border_style="green")
        except Exception as e:
            logger.error(f"Error rendering Memory monitor: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"Memory Monitor Error: {str(e)}", style="red"), title="Memory Monitor", border_style="green")

class NetworkMonitor(BaseMonitor):
    """Enhanced network monitor with comprehensive stats and connection info."""
    
    def __init__(self):
        """Initialize network monitor with expanded tracking."""
        try:
            super().__init__()
            # Initialize tracking with expanded history
            self.last_io = psutil.net_io_counters()
            self.last_time = time.time()
            # Expand history tracking
            self.history = {
                'bytes_sent': deque(maxlen=60),    # 1 minute history
                'bytes_recv': deque(maxlen=60),
                'packets_sent': deque(maxlen=60),  # Packet tracking
                'packets_recv': deque(maxlen=60),
                'error_in': deque(maxlen=10),
                'error_out': deque(maxlen=10)
            }
            # Track peak rates
            self.peak_download = 0
            self.peak_upload = 0
            # Track daily totals
            self.today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            self.today_download = 0
            self.today_upload = 0
            # Last ping results
            self.ping_results = {}
            self.last_ping_time = 0
            self.ping_interval = 3600  # Ping every 3600 seconds
            logger.debug("Enhanced Network monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Network monitor: {e}")
            # Initialize with empty defaults
            self.last_io = None
            self.last_time = 0
            self.history = {}
            self.peak_download = 0
            self.peak_upload = 0
            self.today_start = datetime.now()
            self.today_download = 0
            self.today_upload = 0
            self.ping_results = {}
            self.last_ping_time = 0
            self.ping_interval = 3600
    
    def _get_interface_info(self) -> Dict[str, Any]:
        """Get detailed network interface information."""
        try:
            interfaces = {}
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            
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
                    elif addr.family == psutil.AF_LINK:  # MAC address
                        mac_addr = addr.address
                
                # Skip interfaces with no IP addresses
                if not (ipv4_addrs or ipv6_addrs):
                    continue
                
                # Get interface stats with more details
                stat = stats[name]
                interfaces[name] = {
                    'ipv4': ipv4_addrs,
                    'ipv6': ipv6_addrs,
                    'mac': mac_addr,
                    'speed': stat.speed or 0,
                    'mtu': stat.mtu,
                    'duplex': getattr(stat, 'duplex', 'unknown'),
                    'is_up': stat.isup
                }
                
                # Get wireless info if available (Linux only)
                if sys.platform == 'linux' and os.path.exists('/proc/net/wireless'):
                    try:
                        with open('/proc/net/wireless', 'r') as f:
                            for line in f:
                                if name in line:
                                    parts = line.split()
                                    if len(parts) >= 3:
                                        # Wireless signal quality
                                        interfaces[name]['wireless'] = True
                                        # Signal level is usually the 3rd value
                                        try:
                                            signal = float(parts[3].rstrip('.'))
                                            interfaces[name]['signal'] = signal
                                        except (ValueError, IndexError):
                                            pass
                    except Exception as e:
                        logger.debug(f"Could not read wireless info: {e}")
            
            return interfaces
            
        except Exception as e:
            self.handle_error(e, "get_interface_info")
            return {}
    
    def _ping_hosts(self) -> Dict[str, float]:
        """Ping common hosts to check connectivity and latency."""
        now = time.time()
        # Only ping every ping_interval seconds
        if now - self.last_ping_time < self.ping_interval:
            return self.ping_results
            
        ping_targets = ['8.8.8.8', '1.1.1.1']  # Google DNS, Cloudflare DNS
        results = {}
        
        for target in ping_targets:
            try:
                if sys.platform == 'linux' or sys.platform == 'darwin':
                    # Use the ping command on Linux/Mac
                    proc = subprocess.Popen(
                        ['ping', '-c', '1', '-W', '1', target],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    stdout, _ = proc.communicate(timeout=2)
                    
                    # Parse the output to get ping time
                    if proc.returncode == 0:
                        for line in stdout.splitlines():
                            if 'time=' in line:
                                try:
                                    time_part = line.split('time=')[1].split()[0]
                                    results[target] = float(time_part)
                                    break
                                except (IndexError, ValueError):
                                    pass
                else:
                    # On Windows
                    proc = subprocess.Popen(
                        ['ping', '-n', '1', '-w', '1000', target],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    stdout, _ = proc.communicate(timeout=2)
                    
                    # Parse Windows ping output
                    if proc.returncode == 0:
                        for line in stdout.splitlines():
                            if 'time=' in line or 'time<' in line:
                                try:
                                    if 'time=' in line:
                                        time_part = line.split('time=')[1].split()[0]
                                    else:
                                        time_part = '1'  # time<1ms
                                    results[target] = float(time_part)
                                    break
                                except (IndexError, ValueError):
                                    pass
            except Exception as e:
                logger.debug(f"Error pinging {target}: {e}")
        
        self.ping_results = results
        self.last_ping_time = now
        return results
    
    def _get_connection_stats(self) -> Dict[str, Any]:
        """Get statistics about network connections."""
        stats = {
            'total': 0,
            'established': 0,
            'listen': 0,
            'time_wait': 0,
            'close_wait': 0,
            'tcp': 0,
            'udp': 0,
            'tcp6': 0,
            'udp6': 0,
            'by_port': {},
            'remote_ips': set()
        }
        
        try:
            connections = psutil.net_connections(kind='inet')
            stats['total'] = len(connections)
            
            for conn in connections:
                # Count by status
                if conn.status:
                    status = conn.status.lower()
                    if status in stats:
                        stats[status] += 1
                    elif status == 'established':
                        stats['established'] += 1
                
                # Count by type
                if conn.type == socket.SOCK_STREAM:
                    if conn.family == socket.AF_INET:
                        stats['tcp'] += 1
                    elif conn.family == socket.AF_INET6:
                        stats['tcp6'] += 1
                elif conn.type == socket.SOCK_DGRAM:
                    if conn.family == socket.AF_INET:
                        stats['udp'] += 1
                    elif conn.family == socket.AF_INET6:
                        stats['udp6'] += 1
                
                # Count by local port
                if conn.laddr and len(conn.laddr) > 1:
                    port = conn.laddr[1]
                    if port not in stats['by_port']:
                        stats['by_port'][port] = 0
                    stats['by_port'][port] += 1
                
                # Track remote IPs
                if conn.raddr and len(conn.raddr) > 0:
                    stats['remote_ips'].add(conn.raddr[0])
            
            # Get top ports by connection count
            stats['top_ports'] = sorted(
                stats['by_port'].items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:5]  # Top 5 ports
            
            # Count unique remote IPs
            stats['unique_remote_ips'] = len(stats['remote_ips'])
            del stats['remote_ips']  # Remove the set before returning
            
            return stats
            
        except Exception as e:
            self.handle_error(e, "get_connection_stats")
            return stats
    
    def _get_dns_info(self) -> Dict[str, Any]:
        """Get DNS server information."""
        dns_info = {
            'servers': [],
            'resolution_time': None
        }
        
        # Get DNS servers
        try:
            if sys.platform == 'linux':
                with open('/etc/resolv.conf', 'r') as f:
                    for line in f:
                        if line.startswith('nameserver'):
                            parts = line.strip().split()
                            if len(parts) >= 2:
                                dns_info['servers'].append(parts[1])
            else:
                # For other platforms, try to get from a common domain lookup
                dns_info['servers'] = ['System default']
        except Exception as e:
            logger.debug(f"Could not get DNS servers: {e}")
        
        # Test DNS resolution time
        try:
            start_time = time.time()
            socket.gethostbyname('www.google.com')
            end_time = time.time()
            dns_info['resolution_time'] = (end_time - start_time) * 1000  # in ms
        except Exception as e:
            logger.debug(f"Could not test DNS resolution: {e}")
        
        return dns_info
    
    def _get_network_metrics(self) -> Dict[str, Any]:
        """Calculate comprehensive network metrics and rates."""
        try:
            now = time.time()
            
            # Get new IO counters
            try:
                curr_io = psutil.net_io_counters()
            except Exception as e:
                logger.error(f"Failed to get net_io_counters: {e}")
                return {'rates': {}, 'averages': {}}
            
            # Skip first run or if last_io is missing
            if self.last_io is None:
                self.last_io = curr_io
                self.last_time = now
                return {'rates': {}, 'averages': {}}
            
            dt = now - self.last_time
            if dt <= 0:
                return {'rates': {}, 'averages': {}}
            
            metrics = {
                'bytes_sent': curr_io.bytes_sent,
                'bytes_recv': curr_io.bytes_recv,
                'packets_sent': curr_io.packets_sent,
                'packets_recv': curr_io.packets_recv,
                'error_in': curr_io.errin,
                'error_out': curr_io.errout,
                'drop_in': curr_io.dropin,
                'drop_out': curr_io.dropout
            }
            
            # Calculate rates
            metrics['rates'] = {
                'bytes_sent': (curr_io.bytes_sent - self.last_io.bytes_sent) / dt,
                'bytes_recv': (curr_io.bytes_recv - self.last_io.bytes_recv) / dt,
                'packets_sent': (curr_io.packets_sent - self.last_io.packets_sent) / dt,
                'packets_recv': (curr_io.packets_recv - self.last_io.packets_recv) / dt,
                'error_in': (curr_io.errin - self.last_io.errin) / dt,
                'error_out': (curr_io.errout - self.last_io.errout) / dt
            }
            
            # Update peak rates
            self.peak_download = max(self.peak_download, metrics['rates']['bytes_recv'])
            self.peak_upload = max(self.peak_upload, metrics['rates']['bytes_sent'])
            
            # Update history
            for key, value in metrics['rates'].items():
                base_key = key.replace('_rate', '')
                if base_key in self.history:
                    self.history[base_key].append(value)
            
            # Calculate averages
            metrics['averages'] = {
                key: sum(values) / len(values)
                for key, values in self.history.items()
                if values
            }
            
            # Update daily totals
            now_date = datetime.now()
            if now_date.date() > self.today_start.date():
                # Reset counters at day change
                self.today_start = now_date.replace(hour=0, minute=0, second=0, microsecond=0)
                self.today_download = 0
                self.today_upload = 0
            
            # Add the most recent data transfer to today's total
            if dt > 0:
                self.today_download += (curr_io.bytes_recv - self.last_io.bytes_recv)
                self.today_upload += (curr_io.bytes_sent - self.last_io.bytes_sent)
            
            # Add today's totals to metrics
            metrics['today'] = {
                'download': self.today_download,
                'upload': self.today_upload,
                'total': self.today_download + self.today_upload
            }
            
            # Update trackers
            self.last_io = curr_io
            self.last_time = now
            
            # Add peak values
            metrics['peak'] = {
                'download': self.peak_download,
                'upload': self.peak_upload
            }
            
            return metrics
            
        except Exception as e:
            self.handle_error(e, "get_network_metrics")
            return {'rates': {}, 'averages': {}}
    
    def render(self) -> Panel:
        """Render responsive network information panel."""
        try:
            # Use a more responsive table layout
            table = Table(box=None, expand=True, padding=(0,1))
            table.add_column("Network", style="cyan", width=12, no_wrap=True)
            table.add_column("Traffic", ratio=1)
            table.add_column("Details", ratio=1)
            
            # Get metrics
            metrics = self._get_network_metrics()
            interfaces = self._get_interface_info()
            connection_stats = self._get_connection_stats()
            dns_info = self._get_dns_info()
            ping_results = self._ping_hosts()
            
            # Display current transfer rates with responsive indicators
            if 'rates' in metrics:
                rates = metrics['rates']
                
                # Download rate with responsive progress bar
                if 'bytes_recv' in rates:
                    recv_rate = rates['bytes_recv']
                    # Scale percentage for bar - max 10MB/s = 100%
                    recv_percent = min(100, (recv_rate / (10 * 1024 * 1024)) * 100)
                    
                    table.add_row(
                        "Download",
                        create_unified_progress_bar(recv_percent, custom_color="green"),
                        f"{format_bytes(recv_rate)}/s | Peak: {format_bytes(metrics['peak']['download'])}/s"
                    )
                
                # Upload rate with responsive progress bar
                if 'bytes_sent' in rates:
                    send_rate = rates['bytes_sent']
                    # Scale percentage for bar - max 10MB/s = 100%
                    send_percent = min(100, (send_rate / (10 * 1024 * 1024)) * 100)
                    
                    table.add_row(
                        "Upload",
                        create_unified_progress_bar(send_percent, custom_color="blue"),
                        f"{format_bytes(send_rate)}/s | Peak: {format_bytes(metrics['peak']['upload'])}/s"
                    )
                
                # Today's usage statistics - simplified display
                if 'today' in metrics:
                    today = metrics['today']
                    table.add_row(
                        "Today",
                        f"↓ {format_bytes(today['download'])} | ↑ {format_bytes(today['upload'])}",
                        f"Total: {format_bytes(today['total'])}"
                    )
            
            # Connection statistics - simplified
            conn_text = f"Total: {connection_stats['total']}"
            details = []
            if connection_stats['established'] > 0:
                details.append(f"Est: {connection_stats['established']}")
            if connection_stats['listen'] > 0:
                details.append(f"Listen: {connection_stats['listen']}")
            if connection_stats['tcp'] + connection_stats['tcp6'] > 0:
                details.append(f"TCP: {connection_stats['tcp']+connection_stats['tcp6']}")
            if connection_stats['udp'] + connection_stats['udp6'] > 0:
                details.append(f"UDP: {connection_stats['udp']+connection_stats['udp6']}")
            
            table.add_row(
                "Connections",
                conn_text,
                " | ".join(details) if details else ""
            )
            
            # Ping results if available - more compact display
            if ping_results:
                ping_text = []
                for host, latency in ping_results.items():
                    # Colorize based on latency
                    if latency < 50:
                        color = "green"
                    elif latency < 100:
                        color = "yellow"
                    else:
                        color = "red"
                    ping_text.append(Text(f"{host}: {latency:.1f}ms", color))
                
                table.add_row(
                    "Latency",
                    Text(" | ").join(ping_text) if ping_text else Text("No data", "bright_black"),
                    ""
                )
            
            # DNS information if available
            if dns_info and dns_info['servers']:
                dns_servers = ", ".join(dns_info['servers'][:2])  # Show only first 2 servers
                resolution = f"Lookup: {dns_info['resolution_time']:.1f}ms" if dns_info['resolution_time'] else ""
                
                table.add_row(
                    "DNS",
                    dns_servers,
                    resolution
                )
            
            # Show interfaces in a more compact format
            active_interfaces = []
            for name, info in interfaces.items():
                if info['is_up'] and info['ipv4']:
                    active_interfaces.append((name, info))
            
            if active_interfaces:
                # Add a separator
                table.add_row("", "", "")
                table.add_row("Interfaces", "", "")
                
                # Show each active interface
                for name, info in active_interfaces[:2]:  # Limit to 2 interfaces
                    ipv4 = info['ipv4'][0] if info['ipv4'] else ""  # Show primary IP
                    speed_text = f"{info['speed']} Mbps" if info['speed'] else "Auto"
                    
                    # Create a more compact info display
                    iface_info = f"{ipv4}"
                    iface_details = f"Speed: {speed_text}"
                    
                    if info.get('wireless'):
                        iface_details += " | WiFi"
                    
                    table.add_row(
                        name[:10],
                        iface_info,
                        iface_details
                    )
            
            # Enhanced error information display - only if errors exist
            if any(metrics.get(key, 0) > 0 for key in ['error_in', 'error_out', 'drop_in', 'drop_out']):
                table.add_row("", "", "")
                table.add_row(
                    "Errors",
                    f"In: {metrics.get('error_in', 0)} | Out: {metrics.get('error_out', 0)}",
                    f"Drops: In: {metrics.get('drop_in', 0)} | Out: {metrics.get('drop_out', 0)}"
                )
            
            return Panel(table, title="Network Monitor", border_style="cyan")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Network Monitor Error: {str(e)}", style="red"), title="Network Monitor", border_style="cyan")

class DiskMonitor(BaseMonitor):
    """Disk monitor optimized for Linux systems."""
    
    def __init__(self):
        """Initialize disk monitor with I/O tracking."""
        try:
            super().__init__()
            self.last_io = psutil.disk_io_counters()
            self.last_time = time.time()
            self.history = {
                'read_bytes': deque(maxlen=10),
                'write_bytes': deque(maxlen=10),
                'busy_time': deque(maxlen=10)
            }
            logger.debug("Disk monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Disk monitor: {e}")
            # Initialize with empty defaults
            self.last_io = None
            self.last_time = 0
            self.history = {
                'read_bytes': deque(maxlen=10),
                'write_bytes': deque(maxlen=10),
                'busy_time': deque(maxlen=10)
            }
    
    def _get_disk_io(self) -> Dict[str, Any]:
        """Calculate disk I/O metrics with history."""
        try:
            now = time.time()
            try:
                curr_io = psutil.disk_io_counters()
            except Exception as e:
                logger.error(f"Failed to get disk_io_counters: {e}")
                return {'rates': {}}
                
            # Skip first run or if last_io is missing
            if self.last_io is None:
                self.last_io = curr_io
                self.last_time = now
                return {'rates': {}}
                
            dt = now - self.last_time
            if dt <= 0:
                return {'rates': {}}
            
            metrics = {
                'read_bytes': curr_io.read_bytes,
                'write_bytes': curr_io.write_bytes,
                'read_count': curr_io.read_count,
                'write_count': curr_io.write_count,
                'read_time': curr_io.read_time,
                'write_time': curr_io.write_time,
                'busy_time': getattr(curr_io, 'busy_time', 0),
                'rates': {}
            }
            
            # Calculate rates if we have valid time difference
            metrics['rates'] = {
                'read_bytes': (curr_io.read_bytes - self.last_io.read_bytes) / dt,
                'write_bytes': (curr_io.write_bytes - self.last_io.write_bytes) / dt,
                'read_count': (curr_io.read_count - self.last_io.read_count) / dt,
                'write_count': (curr_io.write_count - self.last_io.write_count) / dt
            }
            
            # Update history
            for key in ['read_bytes', 'write_bytes']:
                self.history[key].append(metrics['rates'][key])
            
            # Calculate busy time percentage
            if hasattr(curr_io, 'busy_time') and hasattr(self.last_io, 'busy_time'):
                busy_time = curr_io.busy_time - self.last_io.busy_time
                busy_percent = min(100.0, busy_time / (dt * 1000) * 100)
                self.history['busy_time'].append(busy_percent)
                metrics['busy_percent'] = busy_percent
            
            # Update tracking
            self.last_io = curr_io
            self.last_time = now
            
            return metrics
            
        except Exception as e:
            self.handle_error(e, "get_disk_io")
            return {'rates': {}}
    
    def _get_partitions(self) -> List[Dict[str, Any]]:
        """Get Linux partition information."""
        partitions = []
        try:
            for part in psutil.disk_partitions(all=False):
                # Skip certain filesystem types
                if part.fstype in {'squashfs', 'efivarfs'} or \
                   '/boot' in part.mountpoint or \
                   '/snap' in part.mountpoint:
                    continue
                
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    
                    # Get additional Linux disk info
                    dev_name = os.path.basename(part.device)
                    sys_block_path = f"/sys/block/{dev_name}"
                    
                    # Try to get additional disk info from sysfs
                    additional_info = {}
                    if os.path.exists(sys_block_path):
                        try:
                            # Get scheduler
                            scheduler_path = f"{sys_block_path}/queue/scheduler"
                            if os.path.exists(scheduler_path):
                                with open(scheduler_path) as f:
                                    additional_info['scheduler'] = f.read().strip()
                            
                            # Get rotational status (0 = SSD, 1 = HDD)
                            rotational_path = f"{sys_block_path}/queue/rotational"
                            if os.path.exists(rotational_path):
                                with open(rotational_path) as f:
                                    additional_info['is_ssd'] = f.read().strip() == '0'
                        except Exception as e:
                            logger.debug(f"Error getting additional disk info: {e}")
                    
                    # Check threshold
                    self.check_threshold(usage.percent, 'Disk Usage', part.mountpoint)
                    
                    partitions.append({
                        'device': part.device,
                        'mountpoint': part.mountpoint,
                        'fstype': part.fstype,
                        'opts': part.opts,
                        'total': usage.total,
                        'used': usage.used,
                        'free': usage.free,
                        'percent': usage.percent,
                        **additional_info
                    })
                    
                except PermissionError:
                    continue
                except Exception as e:
                    self.handle_error(e, f"get_partition_info_{part.mountpoint}")
                    continue
                    
        except Exception as e:
            self.handle_error(e, "get_partitions")
            
        return partitions
    
    def render(self) -> Panel:
        """Render disk information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Disk", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get disk metrics
            io_metrics = self._get_disk_io()
            partitions = self._get_partitions()
            
            # Show I/O rates
            if 'rates' in io_metrics:
                rates = io_metrics['rates']
                
                # Read/Write rates
                table.add_row(
                    "Disk I/O",
                    f"Read: {format_bytes(rates.get('read_bytes', 0))}/s",
                    f"Write: {format_bytes(rates.get('write_bytes', 0))}/s"
                )
                
                # Operations per second
                table.add_row(
                    "Operations",
                    f"Read: {rates.get('read_count', 0):.1f}/s",
                    f"Write: {rates.get('write_count', 0):.1f}/s"
                )
                
                # Busy time if available - using unified progress bar
                if 'busy_percent' in io_metrics:
                    table.add_row(
                        "Busy",
                        create_unified_progress_bar(io_metrics['busy_percent']),
                        f"{io_metrics['busy_percent']:.1f}% Utilized"
                    )
            
            # Show partitions with unified progress bar
            for part in partitions:
                name = os.path.basename(part['mountpoint']) or part['mountpoint']
                
                # Create usage bar using unified progress bar
                usage_bar = create_unified_progress_bar(part['percent'])
                
                # Create details string
                details = [
                    f"{format_bytes(part['used'])} / {format_bytes(part['total'])}",
                    f"({part['fstype']})"
                ]
                
                # Add SSD/HDD indicator if available
                if 'is_ssd' in part:
                    details.append("SSD" if part['is_ssd'] else "HDD")
                
                # Add scheduler if available
                if 'scheduler' in part:
                    details.append(f"Scheduler: {part['scheduler']}")
                
                table.add_row(
                    name[:12],
                    usage_bar,
                    " | ".join(details)
                )
            
            return Panel(table, title="Disk Monitor", border_style="magenta")
            
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Disk Monitor Error: {str(e)}", style="red"), title="Disk Monitor", border_style="magenta")

class ServiceMonitor(BaseMonitor):
    """Service monitor optimized for Linux systems using systemd."""
    
    def __init__(self):
        """Initialize service monitor with important services list."""
        try:
            super().__init__()
            self.important_services = [
                'systemd-journald',   # System logging
                'systemd-logind',     # Login management
                'systemd-timesyncd',  # Time synchronization
                'dbus',              # System message bus
                'NetworkManager',     # Network management
                'sshd',              # SSH server
                'cron',              # Task scheduler
                'udev',              # Device management
                'rsyslog',           # System logging
                'ModemManager',      # Modem management
                'irqbalance',        # IRQ balancing
                'acpid',             # ACPI event daemon
                'bluetooth',         # Bluetooth support
                'cups',              # Printing system
                'apache2',           # Web server if installed
                'mysql',             # Database if installed
                'postgresql',        # Database if installed
            ]
            self.status_cache = {}
            self.cache_time = 0
            self.cache_ttl = 5  # Cache TTL in seconds
            logger.debug("Service monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Service monitor: {e}")
            # Initialize with empty defaults
            self.important_services = []
            self.status_cache = {}
            self.cache_time = 0
            self.cache_ttl = 5
    
    def _get_service_status(self, service: str) -> Optional[Dict[str, Any]]:
        """Get detailed service status using systemctl."""
        try:
            cmd = ['systemctl', 'show', f'{service}.service',
                  '--property=ActiveState,SubState,LoadState,UnitFileState,'
                  'Description,StateChangeTimestamp,ExecMainStatus']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode == 0:
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
                    'status_code': int(status.get('ExecMainStatus', 0) or 0),  # Added 'or 0' to handle empty string
                    'last_changed': status.get('StateChangeTimestamp', '')
                }
        except Exception as e:
            self.handle_error(e, f"get_service_status_{service}")
        return None
    
    def _get_all_services(self) -> Dict[str, Any]:
        """Get status of all monitored services with caching."""
        try:
            current_time = time.time()
            
            # Return cached results if valid
            if current_time - self.cache_time < self.cache_ttl:
                return self.status_cache
            
            services = {}
            stats = {
                'total': len(self.important_services),
                'running': 0,
                'stopped': 0,
                'failed': 0,
                'other': 0
            }
            
            # Get status for each service
            for service in self.important_services:
                status = self._get_service_status(service)
                if status:
                    services[service] = status
                    
                    # Update statistics
                    state = status['state'].lower()
                    if state == 'active':
                        stats['running'] += 1
                    elif state == 'inactive':
                        stats['stopped'] += 1
                    elif state in {'failed', 'error'}:
                        stats['failed'] += 1
                        # Log failed service
                        store_alert('Service', 'warning', f"Service '{service}' has failed")
                    else:
                        stats['other'] += 1
            
            # Update cache
            self.status_cache = {
                'services': services,
                'stats': stats,
                'timestamp': current_time
            }
            self.cache_time = current_time
            
            return self.status_cache
            
        except Exception as e:
            self.handle_error(e, "get_all_services")
            return {
                'services': {},
                'stats': {
                    'total': 0, 'running': 0,
                    'stopped': 0, 'failed': 0, 'other': 0
                }
            }
    
    def render(self) -> Panel:
        """Render service information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Service", style="cyan", width=12)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get service information
            info = self._get_all_services()
            stats = info.get('stats', {})
            services = info.get('services', {})
            
            # Show summary stats
            summary_text = (
                f"Running: {stats.get('running', 0)}/"
                f"{stats.get('total', 0)} | "
                f"Failed: {stats.get('failed', 0)}"
            )
            table.add_row("Summary", summary_text, "")
            
            # Sort services by state and name
            sorted_services = sorted(
                services.values(),
                key=lambda x: (
                    x['state'] != 'active',
                    x['state'] == 'failed',
                    x['name']
                )
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
                
                # Create status bar - using unified style without percentage
                status_bar = create_unified_progress_bar(100 if service['state'] == 'active' else 0, 
                                                      show_percentage=False, custom_color=color)
                
                # Create details string
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

# Fixed function for GPU detection
def check_gpu_available():
    """
    Check for GPU using multiple methods to ensure we don't miss it.
    Returns True if any GPU is detected.
    """
    try:
        # Method 1: Try nvidia-smi directly
        try:
            nvidia_check = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=1
            )
            if nvidia_check.returncode == 0:
                logger.info("NVIDIA GPU detected via nvidia-smi")
                return True
        except Exception:
            pass
            
        # Method 2: Check common NVIDIA paths
        nvidia_paths = [
            '/proc/driver/nvidia/version',
            '/dev/nvidia0',
            '/dev/nvidiactl'
        ]
        if any(os.path.exists(path) for path in nvidia_paths):
            logger.info(f"NVIDIA GPU detected via path check")
            return True
            
        # Method 3: Check device vendor files
        try:
            for i in range(5):  # Check first 5 possible GPUs
                vendor_path = f'/sys/class/drm/card{i}/device/vendor'
                if os.path.exists(vendor_path):
                    with open(vendor_path) as f:
                        vendor = f.read().strip()
                        # NVIDIA vendor ID is 0x10de
                        if vendor in ['0x10de', '10de']:
                            logger.info(f"NVIDIA GPU detected via vendor ID on card{i}")
                            return True
        except Exception as e:
            logger.debug(f"Error checking GPU vendor paths: {e}")
            pass
        
        logger.info("No NVIDIA GPU detected")
        return False
    except Exception as e:
        logger.error(f"Error in check_gpu_available: {e}")
        # If anything fails, assume NO GPU exists
        return False

class GPUMonitor(BaseMonitor):
    """GPU monitor optimized for NVIDIA cards with graceful fallback."""
    
    def __init__(self):
        try:
            super().__init__()
            self.history = {
                'usage': deque(maxlen=30),
                'temp': deque(maxlen=30)
            }
            self.has_nvidia = check_gpu_available()  # Now properly defined
            logger.debug(f"GPU monitor initialized, NVIDIA detected: {self.has_nvidia}")
        except Exception as e:
            logger.error(f"Error initializing GPU monitor: {e}")
            self.history = {'usage': deque(maxlen=30), 'temp': deque(maxlen=30)}
            self.has_nvidia = False
    
    def _safe_float(self, value: str, default: float = 0) -> float:
        """Safely convert a string to float, handling [N/A] and other errors."""
        try:
            value = value.strip()
            if '[N/A]' in value or not value:
                return default
            return float(value)
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Error converting to float: {e}, value: {repr(value)}")
            return default
    
    def _get_gpu_metrics(self) -> Dict[str, Any]:
        """Get detailed GPU metrics using nvidia-smi with error handling."""
        # Return empty metrics if no NVIDIA GPU
        if not self.has_nvidia:
            return {
                'name': 'No NVIDIA GPU detected',
                'usage': 0,
                'temp': 0,
                'memory_used': 0,
                'memory_total': 0,
                'memory_percent': 0,
                'clock_gpu': 0,
                'clock_mem': 0,
                'fan_speed': 0,
                'perf_state': 'N/A'
            }
            
        try:
            # Get all needed metrics in one command
            cmd = [
                'nvidia-smi',
                '--query-gpu=name,utilization.gpu,temperature.gpu,'
                'memory.used,memory.total,power.draw,power.limit,'
                'clocks.current.graphics,clocks.max.graphics,'
                'clocks.current.memory,clocks.max.memory,'
                'fan.speed,pstate',
                '--format=csv,noheader,nounits'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            
            if result.returncode == 0:
                # Split the output and handle potential empty lines
                raw_values = result.stdout.strip().split(',')
                values = [v.strip() for v in raw_values if v.strip()]
                
                # Ensure we have at least 12 values
                if len(values) < 12:
                    logger.warning(f"Incomplete GPU metrics: {len(values)} values received")
                    values = values + ['[N/A]'] * (12 - len(values))  # Pad with [N/A]
                
                # Create base metrics with safe conversions
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
                
                # Calculate memory percentage with division by zero protection
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
                
                return metrics
                
        except FileNotFoundError:
            # nvidia-smi command not found - mark GPU as not available
            logger.warning("nvidia-smi command not found")
            self.has_nvidia = False
            return {
                'name': 'No NVIDIA GPU detected',
                'usage': 0,
                'temp': 0,
                'memory_used': 0,
                'memory_total': 0,
                'memory_percent': 0,
                'clock_gpu': 0,
                'clock_mem': 0,
                'fan_speed': 0,
                'perf_state': 'N/A'
            }
        except Exception as e:
            # If any error occurs, log it but don't crash
            self.handle_error(e, "get_gpu_metrics")
            logger.error(f"GPU metrics error: {e}\n{traceback.format_exc()}")
            
        # Return fallback metrics on any error
        return {
            'name': 'NVIDIA GPU (Error reading data)',
            'usage': 0,
            'temp': 0,
            'memory_used': 0,
            'memory_total': 0,
            'memory_percent': 0,
            'clock_gpu': 0,
            'clock_mem': 0,
            'fan_speed': 0,
            'perf_state': 'N/A'
        }
    
    def render(self) -> Panel:
        """Render GPU information panel with graceful handling of no GPU case."""
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
            
            # Name row with Performance State
            table.add_row(
                "Name",
                metrics['name'],
                f"P-State: {metrics.get('perf_state', 'P?')}"
            )
            
            # Usage row with power info - using unified progress bar
            power_text = ""
            if metrics.get('power_draw', 0) > 0:
                power_text = f" | {metrics['power_draw']:.1f}W"
            table.add_row(
                "Usage",
                create_unified_progress_bar(metrics['usage']),
                f"{metrics['usage']:.1f}%{power_text}"
            )
            
            # Temperature row with fan speed - using unified progress bar
            temp_color = "green" if metrics['temp'] < 70 else "yellow" if metrics['temp'] < 85 else "red"
            temp_text = f"{metrics['temp']:.1f}°C"
            if metrics.get('fan_speed', 0) > 0:
                temp_text += f" | Fan: {metrics['fan_speed']:.0f}%"
            table.add_row(
                "Temperature",
                create_unified_progress_bar(metrics['temp'], custom_color=temp_color),
                temp_text
            )
            
            # Memory usage row - using unified progress bar
            table.add_row(
                "Memory",
                create_unified_progress_bar(metrics['memory_percent']),
                f"{metrics['memory_used']:.0f}MB / {metrics['memory_total']:.0f}MB"
            )
            
            # Clock speeds row
            table.add_row(
                "Clocks",
                f"GPU: {metrics['clock_gpu']}MHz | Mem: {metrics['clock_mem']}MHz",
                f"Max GPU: {metrics.get('clock_gpu_max', 0):.0f}MHz"
            )
            
            # Average usage row
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
            # Handle any rendering errors gracefully
            self.handle_error(e, "render")
            logger.error(f"Error rendering GPU monitor: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"GPU Monitor - Error: {str(e)}", style="red"), title="GPU Monitor", border_style="yellow")

class FirewallMonitor(BaseMonitor):
    """Monitor system firewall status and rules with independent layout."""
    
    def __init__(self):
        try:
            super().__init__()
            self.last_blocked = self._get_blocked_count()
            self.last_check_time = time.time()
            self.rules_cache = None
            self.rules_cache_time = 0
            self.rules_cache_ttl = 5  # Cache TTL in seconds
            logger.debug("Firewall monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Firewall monitor: {e}")
            self.last_blocked = 0
            self.last_check_time = time.time()
            self.rules_cache = None
            self.rules_cache_time = 0
            self.rules_cache_ttl = 5

    def _get_blocked_count(self) -> int:
        """Get count of blocked connections from iptables and nftables."""
        blocked = 0
        
        # Check iptables blocks
        try:
            cmd = ['iptables', '-L', 'INPUT', '-v', '-n']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'DROP' in line or 'REJECT' in line:
                        try:
                            blocked += int(line.split()[0])
                        except (IndexError, ValueError):
                            continue
        except Exception as e:
            logger.debug(f"Error checking iptables: {e}")
        
        # Check nftables blocks
        try:
            cmd = ['nft', 'list', 'ruleset']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'drop' in line or 'reject' in line:
                        blocked += 1
        except Exception as e:
            logger.debug(f"Error checking nftables: {e}")
            
        return blocked
    
    def _get_active_connections(self) -> dict:
        """Get counts of connections by state."""
        connections = {
            'ESTABLISHED': 0,
            'LISTEN': 0,
            'TIME_WAIT': 0,
            'CLOSE_WAIT': 0,
            'other': 0
        }
        
        try:
            for conn in psutil.net_connections(kind='inet'):
                status = conn.status
                if status in connections:
                    connections[status] += 1
                else:
                    connections['other'] += 1
        except Exception as e:
            logger.debug(f"Error getting active connections: {e}")
            
        return connections

    def _get_firewall_rules(self) -> list:
        """Get current firewall rules from iptables and nftables."""
        current_time = time.time()
        
        # Return cached rules if they're still valid
        if (self.rules_cache and 
            current_time - self.rules_cache_time < self.rules_cache_ttl):
            return self.rules_cache
        
        rules = []
        
        # Get iptables rules
        try:
            for chain in ['INPUT', 'OUTPUT', 'FORWARD']:
                cmd = ['iptables', '-L', chain, '-n', '-v']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')[2:]  # Skip headers
                    for line in lines:
                        if line.strip():
                            parts = line.split()
                            if len(parts) >= 4:
                                rules.append({
                                    'chain': chain,
                                    'target': parts[2],
                                    'protocol': parts[3],
                                    'source': parts[7] if len(parts) > 7 else '*',
                                    'destination': parts[8] if len(parts) > 8 else '*'
                                })
        except Exception as e:
            logger.debug(f"Error getting iptables rules: {e}")
        
        # Get nftables rules
        try:
            cmd = ['nft', 'list', 'ruleset']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                current_chain = None
                for line in result.stdout.split('\n'):
                    if 'chain' in line:
                        current_chain = line.split()[1]
                    elif current_chain and ('accept' in line or 'drop' in line or 
                                         'reject' in line):
                        parts = line.strip().split()
                        rules.append({
                            'chain': current_chain,
                            'target': next((p for p in parts if p in 
                                          ['accept', 'drop', 'reject']), 'unknown'),
                            'protocol': next((p for p in parts if p in 
                                            ['tcp', 'udp', 'icmp']), '*'),
                            'source': '*',
                            'destination': '*'
                        })
        except Exception as e:
            logger.debug(f"Error getting nftables rules: {e}")
        
        # Update cache
        self.rules_cache = rules
        self.rules_cache_time = current_time
        return rules
    
    def render(self) -> Panel:
        """Render firewall status panel."""
        try:
            # Create firewall-specific table
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Firewall", style="cyan", width=12, no_wrap=True)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Calculate blocked rate
            current_blocked = self._get_blocked_count()
            current_time = time.time()
            time_diff = current_time - self.last_check_time
            
            # Avoid division by zero
            if time_diff > 0:
                blocked_rate = (current_blocked - self.last_blocked) / time_diff
            else:
                blocked_rate = 0
            
            # Update tracking variables
            self.last_blocked = current_blocked
            self.last_check_time = current_time
            
            # Get connection information
            connections = self._get_active_connections()
            total_connections = sum(connections.values())
            
            # Display connection status
            table.add_row(
                "Connections",
                f"Total: {total_connections}",
                f"Active: {connections['ESTABLISHED']} | "
                f"Listening: {connections['LISTEN']}"
            )
            
            # Display block rate using unified progress bar
            table.add_row(
                "Blocked",
                create_unified_progress_bar(min(blocked_rate * 10, 100)),
                f"Rate: {blocked_rate:.1f}/s | Total: {current_blocked}"
            )
            
            # Get and display firewall rules
            rules = self._get_firewall_rules()
            rules_by_target = {'accept': 0, 'drop': 0, 'reject': 0}
            
            for rule in rules:
                target = rule['target'].lower()
                if target in rules_by_target:
                    rules_by_target[target] += 1
            
            # Display rule summary
            table.add_row(
                "Rules",
                f"Total: {len(rules)}",
                f"Accept: {rules_by_target['accept']} | "
                f"Drop: {rules_by_target['drop']} | "
                f"Reject: {rules_by_target['reject']}"
            )
            
            # Display recent rules
            for rule in rules[:3]:  # Show only top 3 rules
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
            return Panel(Text(f"Firewall Monitor Error: {str(e)}", style="red"), title="Firewall Monitor", border_style="red")

class SensorMonitor(BaseMonitor):
    """Temperature and fan sensor monitor for Linux systems with integrated battery information."""
    
    def __init__(self):
        """Initialize sensor monitoring."""
        try:
            super().__init__()
            self.history = {
                'temps': deque(maxlen=30),
                'fans': deque(maxlen=30)
            }
            self.warning_temp = config['monitors']['sensors']['warning_threshold']
            self.critical_temp = config['monitors']['sensors']['critical_threshold']
            logger.debug("Sensor monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Sensor monitor: {e}")
            self.history = {'temps': deque(maxlen=30), 'fans': deque(maxlen=30)}
            self.warning_temp = 75
            self.critical_temp = 90
    
    def _get_temp_status(self, temp):
        """Determine temperature status based on thresholds."""
        try:
            if temp >= self.critical_temp:
                return 'critical'
            elif temp >= self.warning_temp:
                return 'warning'
            return 'normal'
        except Exception as e:
            logger.error(f"Error in _get_temp_status: {e}")
            return 'normal'  # Safe default

    def _get_sensor_data(self):
        """Get comprehensive sensor data from Linux system."""
        data = {
            'temperatures': [],
            'fans': [],
            'voltages': [],
            'power': []
        }
        
        try:
            # Get temperature sensors from psutil
            if hasattr(psutil, 'sensors_temperatures'):
                temps = psutil.sensors_temperatures()
                if temps:  # Check if temps is not None and not empty
                    for name, entries in temps.items():
                        for entry in entries:
                            temp_info = {
                                'name': entry.label or name,
                                'current': entry.current,
                                'high': entry.high,
                                'critical': getattr(entry, 'critical', None),
                                'status': self._get_temp_status(entry.current)
                            }
                            
                            # Check threshold and send alert if needed
                            if temp_info['status'] == 'critical':
                                store_alert('Temperature', 'critical', 
                                          f"Sensor '{temp_info['name']}' temperature is critical: {temp_info['current']}°C")
                            elif temp_info['status'] == 'warning':
                                store_alert('Temperature', 'warning',
                                          f"Sensor '{temp_info['name']}' temperature is high: {temp_info['current']}°C")
                            
                            data['temperatures'].append(temp_info)
            
            # Get fan sensors from psutil
            if hasattr(psutil, 'sensors_fans'):
                fans = psutil.sensors_fans()
                if fans:  # Check if fans is not None and not empty
                    for name, entries in fans.items():
                        for entry in entries:
                            fan_info = {
                                'name': entry.label or name,
                                'speed': entry.current,
                                'min': getattr(entry, 'min', None),
                                'max': getattr(entry, 'max', None)
                            }
                            
                            data['fans'].append(fan_info)
            
            # Try to get additional sensor data from sysfs only if directory exists
            hwmon_path = '/sys/class/hwmon'
            if os.path.exists(hwmon_path) and os.path.isdir(hwmon_path):
                try:
                    for hwmon in os.listdir(hwmon_path):
                        hwmon_dir = os.path.join(hwmon_path, hwmon)
                        name_path = os.path.join(hwmon_dir, 'name')
                        
                        if not os.path.exists(name_path):
                            continue
                            
                        try:
                            with open(name_path) as f:
                                sensor_name = f.read().strip()
                        except (IOError, OSError):
                            continue
                        
                        # Look for power sensors
                        try:
                            for filename in os.listdir(hwmon_dir):
                                if filename.startswith('power') and filename.endswith('_input'):
                                    power_path = os.path.join(hwmon_dir, filename)
                                    try:
                                        with open(power_path) as f:
                                            power = float(f.read()) / 1000000  # Convert to watts
                                            data['power'].append({
                                                'name': filename[:-6],
                                                'value': power
                                            })
                                    except (IOError, OSError, ValueError):
                                        continue
                        except (IOError, OSError):
                            continue
                except Exception as e:
                    logger.debug(f"Error reading sysfs sensor data: {e}")
            
            # Update history only if we have valid data
            if data['temperatures']:
                self.history['temps'].append(
                    max(t['current'] for t in data['temperatures'])
                )
            
            if data['fans']:
                self.history['fans'].append(
                    max(f['speed'] for f in data['fans'])
                )
            
            return data
            
        except Exception as e:
            logger.debug(f"Error getting sensor data: {e}")
            return data

    def _get_battery_info(self):
        """Get battery information."""
        battery_info = {
            'available': False,
            'percent': 0,
            'power_plugged': False,
            'secsleft': 0,
            'status': 'Unknown'
        }
        
        try:
            if hasattr(psutil, 'sensors_battery'):
                battery = psutil.sensors_battery()
                if battery:
                    battery_info['available'] = True
                    battery_info['percent'] = battery.percent
                    battery_info['power_plugged'] = battery.power_plugged
                    battery_info['secsleft'] = battery.secsleft
                    
                    # Determine status
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
        except Exception as e:
            logger.debug(f"Error getting battery info: {e}")
            
        return battery_info
    
    def render(self):
        """Render sensor information panel with integrated battery information."""
        try:
            # Match the column structure of Memory and Disk monitors
            table = Table(box=None, expand=True, padding=(0,1))
            table.add_column("Sensor", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            sensor_data = self._get_sensor_data()
            
            # Show temperatures
            for temp in sensor_data['temperatures']:
                # Get the color based on status
                status_color = {
                    'normal': 'green',
                    'warning': 'yellow',
                    'critical': 'red'
                }[temp['status']]
                
                # Create details text
                details = [f"Current: {temp['current']:4.1f}°C"]
                if temp['high']:
                    details.append(f"High: {temp['high']:4.1f}°C")
                if temp['critical']:
                    details.append(f"Critical: {temp['critical']:4.1f}°C")
                
                # Calculate percentage relative to critical or high temp
                if temp['critical']:
                    temp_percent = (temp['current'] / temp['critical']) * 100
                elif temp['high']:
                    temp_percent = (temp['current'] / temp['high']) * 100
                else:
                    temp_percent = (temp['current'] / 100) * 100
                
                # Use unified progress bar function WITHOUT percentage text
                table.add_row(
                    temp['name'][:12],
                    create_unified_progress_bar(min(100, temp_percent), show_percentage=False, custom_color=status_color),
                    " | ".join(details)
                )
            
            # Show power readings if available
            if sensor_data['power']:
                if sensor_data['temperatures']:
                    table.add_row("", "", "")
                table.add_row("Power", "", "")
                for power in sensor_data['power']:
                    table.add_row(
                        power['name'][:12],
                        Text(f"{power['value']:.1f}W", style="green"),
                        ""
                    )

            # Show fan speeds
            if sensor_data['fans']:
                if sensor_data['temperatures'] or sensor_data['power']:
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
            
            # Show temperature trends
            if self.history['temps']:
                table.add_row("", "", "")
                avg_temp = sum(self.history['temps']) / len(self.history['temps'])
                max_temp = max(self.history['temps'])
                min_temp = min(self.history['temps'])
                
                table.add_row(
                    "Temp Trend",
                    create_unified_progress_bar(avg_temp, show_percentage=False),
                    f"Min: {min_temp:.1f}°C | Avg: {avg_temp:.1f}°C | Max: {max_temp:.1f}°C"
                )
            
            # Add battery info under sensors
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
                
                # Add battery charge row with unified progress bar
                table.add_row(
                    "Charge",
                    create_unified_progress_bar(battery_info['percent'], custom_color=status_color),
                    f"{battery_info['percent']:.1f}% remaining"
                )
                
                # Add remaining time info
                secsleft = battery_info['secsleft']
                if secsleft == psutil.POWER_TIME_UNLIMITED:
                    time_left = "Unlimited"
                elif secsleft == psutil.POWER_TIME_UNKNOWN:
                    time_left = "Unknown"
                else:
                    hours, remainder = divmod(secsleft, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    time_left = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                
                # Add power source and time
                power_source = "AC Power" if battery_info['power_plugged'] else "Battery"
                table.add_row(
                    "Power Source",
                    power_source,
                    f"Time left: {time_left}"
                )
                
                # Add status
                table.add_row(
                    "Status",
                    battery_info['status'],
                    ""
                )
            
            if not any([sensor_data['temperatures'], sensor_data['fans'], sensor_data['power'], battery_info['available']]):
                return Panel(
                    Text("No sensor data available", style="yellow"),
                    title="Sensor Monitor",
                    border_style="red"
                )
            
            return Panel(table, title="Sensor Monitor", border_style="red")
            
        except Exception as e:
            logger.debug(f"Error rendering sensor panel: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"Unable to read sensor data: {str(e)}", style="yellow"), title="Sensor Monitor", border_style="red")

class ProcessMonitor(BaseMonitor):
    """Process monitoring with reliable CPU/Memory toggle sorting."""
    
    DEFAULT_CSS = """
    ProcessMonitor {
        height: auto;
        margin: 0;
        padding: 0;
    }
    """
    
    def __init__(self):
        try:
            super().__init__()
            # Start with CPU sorting by default
            self.sort_by = 'cpu'
            self.processes_limit = config['monitors']['process']['limit']
            self.last_process_check = 0
            self.process_cache = {'cpu': [], 'memory': []}  # Keep separate caches for each sort type
            self.process_cache_ttl = 2
            logger.debug("Process monitor initialized with dual sorting")
        except Exception as e:
            logger.error(f"Error initializing Process monitor: {e}")
            self.sort_by = 'cpu'
            self.processes_limit = 20
            self.last_process_check = 0
            self.process_cache = {'cpu': [], 'memory': []}
            self.process_cache_ttl = 2
    
    def handle_sort_key(self, key: str) -> None:
        """Handle sort key events from the app"""
        try:
            if key == "c":
                logger.info("Changing to CPU sort")
                self.sort_by = 'cpu'
                self.refresh_content()
            elif key == "m":
                logger.info("Changing to Memory sort")
                self.sort_by = 'memory'
                self.refresh_content()
        except Exception as e:
            logger.error(f"Error handling sort key: {e}")
    
    def refresh_content(self) -> None:
        """Refresh the monitor's content"""
        try:
            # Force cache reset to ensure fresh data
            self.last_process_check = 0
            # Force a re-render
            new_content = self.render()
            self.update(new_content)
        except Exception as e:
            logger.error(f"Error refreshing content: {e}")
    
    def _get_all_processes(self) -> Dict[str, List]:
        """Get ALL processes sorted by both CPU and memory."""
        current_time = time.time()
        
        # Only refresh if cache is stale
        if current_time - self.last_process_check < self.process_cache_ttl and self.process_cache['cpu'] and self.process_cache['memory']:
            return self.process_cache
        
        try:
            all_processes = []
            
            # Get data for all processes
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
                try:
                    proc_info = proc.info
                    
                    if not proc_info.get('name'):
                        continue
                    
                    # Get RSS memory
                    try:
                        mem_info = proc.memory_info()
                        proc_info['memory_bytes'] = mem_info.rss
                    except:
                        proc_info['memory_bytes'] = 0
                    
                    # Get command line for python processes
                    try:
                        if proc_info['name'] in ['python', 'python3', 'py', 'python.exe']:
                            cmdline = proc.cmdline()
                            if cmdline and len(cmdline) > 1:
                                script_path = cmdline[1]
                                script_name = os.path.basename(script_path)
                                proc_info['name'] = f"{proc_info['name']}:{script_name}"
                    except:
                        pass
                        
                    all_processes.append(proc_info)
                except:
                    continue
                    
            # Create CPU sorted list - ensure we use the float value for sorting
            cpu_sorted = sorted(
                all_processes,
                key=lambda x: float(x.get('cpu_percent', 0) or 0),
                reverse=True
            )
            
            # Create memory sorted list - ensure we sort by actual bytes for consistency
            memory_sorted = sorted(
                all_processes, 
                key=lambda x: int(x.get('memory_bytes', 0) or 0),
                reverse=True
            )
            
            # Store both lists in cache
            self.process_cache = {
                'cpu': cpu_sorted[:self.processes_limit],
                'memory': memory_sorted[:self.processes_limit]
            }
            
            self.last_process_check = current_time
            
            # Debug the results
            logger.debug(f"Sorted {len(all_processes)} processes")
            for proc in memory_sorted[:3]:
                logger.debug(f"Memory sorting - Top process: {proc.get('name')} - {format_bytes(proc.get('memory_bytes', 0))}")
            
            return self.process_cache
        
        except Exception as e:
            logger.error(f"Error getting processes: {e}")
            return {'cpu': [], 'memory': []}
    
    def render(self) -> Panel:
        """Render a panel showing processes sorted by CPU or Memory."""
        try:
            table = Table(box=None, expand=True, padding=(0,1), collapse_padding=True)
            table.add_column("PID", style="cyan", width=7)
            table.add_column("Name", style="bright_blue", width=25)
            table.add_column("CPU%", justify="right", width=7)
            table.add_column("Memory", justify="right", width=10)
            table.add_column("Status", width=10)
            
            # Get both sorted process lists
            all_sorted = self._get_all_processes()
            
            # Choose which sorted list to display based on sort_by setting
            processes = all_sorted.get(self.sort_by, [])
            
            # Create title with current sorting method
            sort_info = "CPU" if self.sort_by == 'cpu' else "Memory"
            
            # Show the processes
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
                    
                    # Add the row
                    table.add_row(
                        str(proc.get('pid', '')),
                        proc.get('name', '')[:25],
                        cpu_text,
                        mem_text,
                        status_text
                    )
            else:
                table.add_row("No processes found", "", "", "", "")
            
            return Panel(
                table,
                title=f"Process Monitor (Top {self.processes_limit}, Sort: {sort_info}) - Press C=CPU, M=Mem",
                border_style="green",
                padding=(0,0)
            )
        except Exception as e:
            logger.error(f"Error rendering process monitor: {e}")
            return Panel(Text(f"Process Monitor Error: {str(e)}", style="red"))

class AlertMonitor(BaseMonitor):
    """Alert monitor to display active system alerts."""
    
    def render(self) -> Panel:
        """Render alert information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Time", style="cyan", width=8)
            table.add_column("Category", style="bright_blue", width=12)
            table.add_column("Message", ratio=1)
            
            # Get recent alerts
            recent_alerts = list(alert_history)[-10:]  # Last 10 alerts
            
            if not recent_alerts:
                return Panel(Text("No recent alerts", style="green"), title="Alert Monitor", border_style="red")
            
            # Show alerts from newest to oldest
            for alert in reversed(recent_alerts):
                # Format timestamp
                time_str = alert['timestamp'].strftime("%H:%M:%S")
                
                # Determine level color
                level_color = "red" if alert['level'] == 'critical' else "yellow"
                
                # Add alert row
                table.add_row(
                    time_str,
                    Text(alert['category'], level_color),
                    alert['message']
                )
            
            return Panel(table, title=f"Alert Monitor ({len(alert_history)} alerts)", border_style="red")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Alert Monitor Error: {str(e)}", style="red"), title="Alert Monitor", border_style="red")

def restore_terminal():
    """Restore terminal to normal state"""
    try:
        # Reset terminal settings
        os.system('stty sane')
        # Clear screen
        os.system('clear')
        # Reset cursor
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
    """Main system monitoring application with key bindings for process monitor."""
    
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
            
            # Create monitor instances
            logger.info("Creating monitor instances...")
            self.monitors = {}
            
            # Create each monitor with try/except
            try:
                self.monitors['self'] = SelfMonitor()
                logger.debug("Created Self monitor")
            except Exception as e:
                logger.error(f"Failed to create Self monitor: {e}")
                
            try:
                self.monitors['cpu'] = CPUMonitor()
                logger.debug("Created CPU monitor")
            except Exception as e:
                logger.error(f"Failed to create CPU monitor: {e}")
                
            try:
                self.monitors['memory'] = MemoryMonitor()
                logger.debug("Created Memory monitor")
            except Exception as e:
                logger.error(f"Failed to create Memory monitor: {e}")
            
            try:
                self.monitors['disk'] = DiskMonitor()
                logger.debug("Created Disk monitor")
            except Exception as e:
                logger.error(f"Failed to create Disk monitor: {e}")
            
            try:
                self.monitors['network'] = NetworkMonitor()
                logger.debug("Created Network monitor")
            except Exception as e:
                logger.error(f"Failed to create Network monitor: {e}")
            
            try:
                self.monitors['gpu'] = GPUMonitor()
                logger.debug("Created GPU monitor")
            except Exception as e:
                logger.error(f"Failed to create GPU monitor: {e}")
            
            try:
                self.monitors['services'] = ServiceMonitor()
                logger.debug("Created Services monitor")
            except Exception as e:
                logger.error(f"Failed to create Services monitor: {e}")
            
            try:
                self.monitors['firewall'] = FirewallMonitor()
                logger.debug("Created Firewall monitor")
            except Exception as e:
                logger.error(f"Failed to create Firewall monitor: {e}")
            
            try:
                self.monitors['sensors'] = SensorMonitor()
                logger.debug("Created Sensors monitor")
            except Exception as e:
                logger.error(f"Failed to create Sensors monitor: {e}")
            
            try:
                self.monitors['process'] = ProcessMonitor()
                logger.debug("Created Process monitor")
            except Exception as e:
                logger.error(f"Failed to create Process monitor: {e}")
            
            try:
                self.monitors['alerts'] = AlertMonitor()
                logger.debug("Created Alerts monitor")
            except Exception as e:
                logger.error(f"Failed to create Alerts monitor: {e}")
            
            logger.info("SystemMonitorApp initialized successfully")
            
        except Exception as e:
            logger.critical(f"CRITICAL ERROR in SystemMonitorApp init: {e}\n{traceback.format_exc()}")
            # Initialize with minimal defaults to prevent crashes
            self.start_time = datetime.now()
            self.show_monitors = {}
            self.monitors = {}
    
    def on_key(self, event) -> None:
        """Handle key events and forward to process monitor"""
        try:
            if event.key == "ctrl+c":
                logger.info("CTRL+C pressed, exiting")
                self.exit()
            elif event.key in ["c", "m"]:
                # Forward to ProcessMonitor if it exists
                process_monitor = self.monitors.get('process')
                if process_monitor:
                    logger.info(f"Forwarding {event.key} key to process monitor")
                    process_monitor.handle_sort_key(event.key)
        except Exception as e:
            logger.error(f"Error in on_key: {e}")
    
    def _on_exit(self) -> None:
        """Ensure terminal is restored on exit"""
        try:
            logger.info("App exiting, restoring terminal")
            restore_terminal()
        except Exception as e:
            logger.error(f"Error in _on_exit: {e}")
    
    def compose(self) -> ComposeResult:
        try:
            # Header info
            uptime = datetime.now() - self.start_time
            uptime_str = f"{uptime.seconds // 3600}h {(uptime.seconds % 3600) // 60}m {uptime.seconds % 60}s"
            header_text = [
                f"OS: {PLATFORM_INFO['system'].title()} {PLATFORM_INFO['release']}",
                f"Python: {PLATFORM_INFO['python_version']}",
                f"Cores: {psutil.cpu_count()}",
                f"RAM: {format_bytes(psutil.virtual_memory().total)}",
                f"Uptime: {uptime_str}"
            ]
            yield Header(" | ".join(header_text))
            
            # Left and right containers
            yield Container(id="left-column")
            yield Container(id="right-column")
            
            # Simple footer without key bindings
            yield Footer()
            
            logger.debug("Compose complete")
        except Exception as e:
            logger.critical(f"Critical error in compose: {e}\n{traceback.format_exc()}")
            # Try to yield minimal UI if something fails
            yield Header("System Monitor - ERROR")
            yield Container(id="left-column")
            yield Container(id="right-column")
            yield Footer()
    
    def on_mount(self) -> None:
        """Set up monitor layout on mount and start refresh interval."""
        try:
            # Set up the initial monitor layout
            self.refresh_monitors()
            
            # Set up a timer to update uptime in header
            try:
                self.set_interval(1, self._update_header)
                logger.debug("Header update timer set")
            except Exception as e:
                logger.error(f"Failed to set up header update timer: {e}")
            
            logger.info("App mounted successfully")
        except Exception as e:
            logger.error(f"Error in on_mount: {e}\n{traceback.format_exc()}")
    
    def refresh_monitors(self) -> None:
        """Refresh the monitor layout based on visibility settings"""
        try:
            logger.debug("Refreshing monitor layout")
            
            # Try to get the containers
            left_container = self.query_one("#left-column", Container)
            right_container = self.query_one("#right-column", Container)
            
            # Remove all monitors
            left_container.remove_children()
            right_container.remove_children()
            
            # Get layout from config
            left_monitors = list(config['ui']['left_column'])
            if 'alerts' not in left_monitors and 'alerts' not in config['ui']['right_column']:
                left_monitors.append('alerts')
                
            right_monitors = list(config['ui']['right_column'])
            
            # Add visible monitors back
            for name in left_monitors:
                if name in self.monitors and self.show_monitors.get(name, True):
                    left_container.mount(self.monitors[name])
                    logger.debug(f"Mounted {name} to left column")
            
            for name in right_monitors:
                if name in self.monitors and self.show_monitors.get(name, True):
                    right_container.mount(self.monitors[name])
                    logger.debug(f"Mounted {name} to right column")
                    
        except Exception as e:
            logger.error(f"Error refreshing monitors: {e}\n{traceback.format_exc()}")
    
    def _update_header(self) -> None:
        """Update the header with current uptime and system stats."""
        try:
            uptime = datetime.now() - self.start_time
            uptime_str = f"{uptime.seconds // 3600}h {(uptime.seconds % 3600) // 60}m {uptime.seconds % 60}s"
            
            header_text = [
                f"OS: {PLATFORM_INFO['system'].title()} {PLATFORM_INFO['release']}",
                f"Python: {PLATFORM_INFO['python_version']}",
                f"Cores: {psutil.cpu_count()}",
                f"RAM: {format_bytes(psutil.virtual_memory().total)}",
                f"Uptime: {uptime_str}"
            ]
            
            # Get the header and update text
            header = self.query_one(Header)
            if header:
                header.text = " | ".join(header_text)
        except Exception as e:
            logger.debug(f"Error updating header: {e}")

def main():
    """Run monitor with clean terminal exit and robust error handling"""
    # Initialize configuration directory for early logging
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    try:
        console.print("[bold green]System Monitor starting...[/bold green]")
        
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
        
        # Run app
        try:
            logger.info("Starting SystemMonitorApp")
            console.print("[green]Starting system monitor interface...[/green]")
            app = SystemMonitorApp()
            app.run()
            logger.info("SystemMonitorApp exited normally")
        except Exception as e:
            logger.critical(f"Critical application error: {e}\n{traceback.format_exc()}")
            console.print(f"[bold red]Critical application error: {e}[/bold red]")
            restore_terminal()
            return 1
        
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
            logger.info("System Monitor shutdown complete")
        except:
            pass

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
        restore_terminal()
        sys.exit(1)