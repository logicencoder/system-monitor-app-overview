#!/usr/bin/env python3

# Standard library imports
import os
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
import sqlite3
from pathlib import Path
import traceback

# Configure basic logging first - will be enhanced later
logging.basicConfig(
    level=logging.DEBUG,
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
DB_FILE = os.path.join(CONFIG_DIR, "metrics.db")
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
    root_logger.setLevel(logging.DEBUG)
    
    logger.info("Enhanced logging configured")
except Exception as e:
    logger.error(f"Failed to set up enhanced logging: {e}")
    # Carry on with basic logging

# Default configuration
DEFAULT_CONFIG = {
    'monitors': {
        'cpu': {'enabled': True, 'interval': 1.1, 'warning_threshold': 75, 'critical_threshold': 90},
        'memory': {'enabled': True, 'interval': 2.1, 'warning_threshold': 75, 'critical_threshold': 90},
        'disk': {'enabled': True, 'interval': 3.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'network': {'enabled': True, 'interval': 1.1},
        'gpu': {'enabled': True, 'interval': 2.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'sensors': {'enabled': True, 'interval': 3.0, 'warning_threshold': 75, 'critical_threshold': 90},
        'services': {'enabled': True, 'interval': 5.0},
        'process': {'enabled': True, 'interval': 3.0, 'limit': 8},
        'battery': {'enabled': True, 'interval': 5.0, 'warning_threshold': 20, 'critical_threshold': 10},
        'firewall': {'enabled': True, 'interval': 5.0}
    },
    'ui': {
        'theme': 'dark',
        'left_column': ['cpu', 'gpu', 'process', 'services'],
        'right_column': ['memory', 'disk', 'network', 'battery', 'firewall', 'sensors'],
        'cores_per_line': 4
    },
    'alerts': {
        'enabled': True,
        'desktop_notification': False,
        'log_critical_events': True
    },
    'data': {
        'persistence_enabled': True,
        'history_length_days': 7,
        'storage_interval': 60  # seconds
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
            'battery': 5.0, 'firewall': 5.0
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
    'firewall': 5.0  # Firewall monitor refresh
}

# Number of CPU cores to display per line
CORES_PER_LINE = 4

def setup_database():
    """Set up SQLite database for metric storage if enabled"""
    if not config['data']['persistence_enabled']:
        logger.info("Metrics persistence disabled, skipping database setup")
        return None
        
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Create tables if they don't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS metrics (
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            category TEXT,
            name TEXT,
            value REAL,
            PRIMARY KEY (timestamp, category, name)
        )
        ''')
        
        # Create index for faster querying
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS metrics_timestamp ON metrics (timestamp)
        ''')
        
        # Create alerts table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            category TEXT,
            level TEXT,
            message TEXT
        )
        ''')
        
        conn.commit()
        logger.info(f"Database initialized at {DB_FILE}")
        return conn
    except Exception as e:
        logger.error(f"Error setting up database: {e}\n{traceback.format_exc()}")
        return None

def store_metric(category, name, value):
    """Store a metric in the database"""
    if not config['data']['persistence_enabled']:
        return
        
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO metrics (category, name, value) VALUES (?, ?, ?)",
            (category, name, value)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Error storing metric: {e}")

def store_alert(category, level, message):
    """Store an alert in the database and alert history"""
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
        
        # Store in database if persistence is enabled
        if config['data']['persistence_enabled']:
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO alerts (category, level, message) VALUES (?, ?, ?)",
                    (category, level, message)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.debug(f"Error storing alert in database: {e}")
        
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

def create_progress_bar(percentage: float, width: int = 40, color: str = None) -> Text:
    """Create a colored progress bar based on percentage."""
    try:
        # Ensure percentage is within bounds
        percentage = max(0, min(100, percentage))
        
        # Calculate filled and empty portions
        filled = int(width * percentage / 100)
        remainder = width - filled
        
        # Determine color based on percentage if not provided
        if color is None:
            if percentage < 50:
                color = "green"
            elif percentage < 75:
                color = "yellow"
            elif percentage < 90:
                color = "red"
            else:
                color = "bright_red"
        
        # Create the bar with the specified color
        bar = Text('■' * filled, color) + Text('·' * remainder, "bright_black")
        return bar + Text(f" {percentage:5.1f}%", color)
    except Exception as e:
        logger.error(f"Error creating progress bar: {e}")
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

def check_gpu_available():
    """
    Check for GPU using multiple methods to ensure we don't miss it.
    Returns True if any GPU is detected.
    """
    try:
        # Method 1: Try nvidia-smi directly
        nvidia_check = subprocess.run(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=1
        )
        if nvidia_check.returncode == 0:
            logger.info("NVIDIA GPU detected via nvidia-smi")
            return True
            
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
            return ""
        except Exception as e:
            logger.debug(f"Could not get processor name: {e}")
            return ""
    
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
    
    def create_colored_bar(self, percentage: float, width: int = 30) -> Text:
        """Create a color-coded bar with percentage before the bar."""
        try:
            color = self.get_usage_color(percentage)
            filled = int(width * percentage / 100)
            remainder = width - filled
            return Text(f"{percentage:5.1f}% ", color) + Text('■' * filled, color) + Text('·' * remainder, "bright_black")
        except Exception as e:
            logger.error(f"Error creating colored bar: {e}")
            return Text("Error", "red")
    
    def create_core_row(self, start_idx: int, cpu_percent: list) -> list:
        """Create a row of CPU core displays."""
        cores_in_row = []
        try:
            for i in range(start_idx, min(start_idx + CORES_PER_LINE, len(cpu_percent))):
                core_text = Text(f"Core {i:2d}: ", "cyan") + self.create_colored_bar(cpu_percent[i])
                cores_in_row.append(core_text)
            return cores_in_row
        except Exception as e:
            logger.error(f"Error creating core row: {e}")
            return [Text("Error", "red")]
    
    def render(self) -> Panel:
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            
            # Get CPU metrics
            cpu_percent = psutil.cpu_percent(percpu=True)
            freq = psutil.cpu_freq()
            times = psutil.cpu_times_percent()
            load = psutil.getloadavg()
            
            # Calculate total CPU usage
            total = sum(cpu_percent) / len(cpu_percent)
            
            # Store metrics if persistence is enabled
            if config['data']['persistence_enabled']:
                store_metric('cpu', 'total', total)
                for i, core in enumerate(cpu_percent):
                    store_metric('cpu', f'core_{i}', core)
            
            # Check for alerts
            self.check_threshold(total, 'CPU Usage', 'Total')
            
            # Create metrics header table
            metrics_table = Table(box=None, expand=True, padding=(0,0))
            metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
            metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)
            
            # Direct use of current frequency in MHz
            current_mhz = int(freq.current) if freq else 0
            
            metrics_table.add_row(
                Text("Total: ") + self.create_colored_bar(total),
                Text(f"Freq: {current_mhz}MHz | Load: {load[0]:5.2f}")
            )
            
            # Add CPU states
            color_user = self.get_usage_color(times.user)
            color_sys = self.get_usage_color(times.system)
            states_text = (
                Text(f"User: {times.user:4.1f}% ", color_user) +
                Text(f"Sys: {times.system:4.1f}% ", color_sys) +
                Text(f"Idle: {times.idle:4.1f}%", "bright_black")
            )
            metrics_table.add_row(states_text, "")
            
            # Add metrics to main table
            table.add_row(metrics_table)
            
            # Create cores table with dynamic columns
            cores_table = Table(box=None, expand=True, padding=(0,0))
            
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

            # Store metrics if persistence is enabled
            if config['data']['persistence_enabled']:
                store_metric('memory', 'ram_percent', vm.percent)
                store_metric('memory', 'ram_used', vm.used)
                store_metric('memory', 'swap_percent', swap.percent)
                store_metric('memory', 'swap_used', swap.used)
            
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
            cache_percent = (vm.cached / vm.total) * 100 if hasattr(vm, 'cached') else 0
            cache_text = ""
            if hasattr(vm, 'cached'):
                cache_text = f"Cached: {format_bytes(vm.cached)}"
                if hasattr(vm, 'buffers'):
                    cache_text += f" | Buffers: {format_bytes(vm.buffers)}"
                
                table.add_row(
                    "Cache",
                    create_progress_bar(cache_percent),
                    cache_text
                )

            # Effective Memory
            if hasattr(vm, 'available') and hasattr(vm, 'cached') and hasattr(vm, 'buffers'):
                effective_used = vm.total - vm.available - vm.cached - vm.buffers
                if effective_used >= 0:
                    effective_percent = (effective_used / vm.total) * 100
                    table.add_row(
                        "Effective",
                        create_progress_bar(effective_percent),
                        f"Used: {format_bytes(effective_used)} | Available: {format_bytes(vm.available)}"
                    )
            
            # Swap Usage
            table.add_row(
                "Swap",
                create_progress_bar(swap.percent),
                f"Used: {format_bytes(swap.used)} / Total: {format_bytes(swap.total)}"
            )

            return Panel(table, title="Memory Monitor", border_style="green")
        except Exception as e:
            logger.error(f"Error rendering Memory monitor: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"Memory Monitor Error: {str(e)}", style="red"), title="Memory Monitor", border_style="green")

class NetworkMonitor(BaseMonitor):
    """Network monitor optimized for Linux systems."""
    
    def __init__(self):
        """Initialize network monitor with counters."""
        try:
            super().__init__()
            # Initialize tracking with expanded history
            self.last_io = psutil.net_io_counters()
            self.last_time = time.time()
            # Expand history tracking
            self.history = {
                'bytes_sent': deque(maxlen=60),    # Increased to 1 minute history
                'bytes_recv': deque(maxlen=60),
                'packets_sent': deque(maxlen=60),  # Added packet tracking
                'packets_recv': deque(maxlen=60),
                'error_in': deque(maxlen=10),
                'error_out': deque(maxlen=10)
            }
            logger.debug("Network monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Network monitor: {e}")
            # Initialize with empty defaults
            self.last_io = None
            self.last_time = 0
            self.history = {}
    
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
                
                # Get IP addresses
                ipv4_addrs = []
                ipv6_addrs = []
                for addr in addrs_list:
                    if addr.family == socket.AF_INET:
                        ipv4_addrs.append(addr.address)
                    elif addr.family == socket.AF_INET6:
                        ipv6_addrs.append(addr.address)
                
                # Skip interfaces with no IP addresses
                if not (ipv4_addrs or ipv6_addrs):
                    continue
                
                # Get interface stats with more details
                stat = stats[name]
                interfaces[name] = {
                    'ipv4': ipv4_addrs,
                    'ipv6': ipv6_addrs,
                    'speed': stat.speed or 0,
                    'mtu': stat.mtu,
                    'duplex': getattr(stat, 'duplex', 'unknown'),
                    'is_up': stat.isup
                }
            
            return interfaces
            
        except Exception as e:
            self.handle_error(e, "get_interface_info")
            return {}
    
    def _get_network_metrics(self) -> Dict[str, Any]:
        """Calculate network metrics and rates with packet information."""
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
            
            # Calculate rates if we have valid time difference
            metrics['rates'] = {
                'bytes_sent': (curr_io.bytes_sent - self.last_io.bytes_sent) / dt,
                'bytes_recv': (curr_io.bytes_recv - self.last_io.bytes_recv) / dt,
                'packets_sent': (curr_io.packets_sent - self.last_io.packets_sent) / dt,
                'packets_recv': (curr_io.packets_recv - self.last_io.packets_recv) / dt,
                'error_in': (curr_io.errin - self.last_io.errin) / dt,
                'error_out': (curr_io.errout - self.last_io.errout) / dt
            }
            
            # Update history with more metrics
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
            
            # Store metrics in database
            if config['data']['persistence_enabled']:
                for key, value in metrics['rates'].items():
                    store_metric('network', key, value)
            
            # Update tracking
            self.last_io = curr_io
            self.last_time = now
            
            return metrics
            
        except Exception as e:
            self.handle_error(e, "get_network_metrics")
            return {'rates': {}, 'averages': {}}
    
    def _create_traffic_indicator(self, rate: float, max_rate: float = 10 * 1024 * 1024, direction: str = "down") -> Text:
        """Create a traffic indicator with arrows and intensity."""
        try:
            percent = min(100, (rate / max_rate) * 100)
            
            # Determine color based on percent
            if percent < 25:
                color = "green"
            elif percent < 50:
                color = "bright_green"
            elif percent < 75:
                color = "yellow"
            else:
                color = "red"
            
            # Determine arrow symbols based on direction
            arrow = "↓" if direction == "down" else "↑"
            
            # Create a visual indicator with 1-5 arrows based on intensity
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
            
            # Add formatted rate
            rate_text = format_bytes(rate) + "/s"
            return Text(f"{indicator} {rate_text}", color)
        except Exception as e:
            logger.error(f"Error creating traffic indicator: {e}")
            return Text("Error", "red")
    
    def render(self) -> Panel:
        """Render network information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Network", style="cyan", width=12)
            table.add_column("Traffic", ratio=1)
            table.add_column("Details", style="bright_blue")
            
            # Get metrics
            metrics = self._get_network_metrics()
            interfaces = self._get_interface_info()
            
            # Display current transfer rates with arrow indicators
            if 'rates' in metrics:
                rates = metrics['rates']
                
                # Download rate with arrow indicator
                if 'bytes_recv' in rates:
                    recv_rate = rates['bytes_recv']
                    table.add_row(
                        "Download",
                        self._create_traffic_indicator(recv_rate, direction="down"),
                        f"Total: {format_bytes(metrics['bytes_recv'])}"
                    )
                
                # Upload rate with arrow indicator
                if 'bytes_sent' in rates:
                    send_rate = rates['bytes_sent']
                    table.add_row(
                        "Upload",
                        self._create_traffic_indicator(send_rate, direction="up"),
                        f"Total: {format_bytes(metrics['bytes_sent'])}"
                    )
                
                # Packet rates display
                if 'packets_recv' in rates and 'packets_sent' in rates:
                    packet_text = Text("↓ ", "cyan") + Text(f"{rates['packets_recv']:.1f}/s", "cyan")
                    packet_text += Text(" ↑ ", "cyan") + Text(f"{rates['packets_sent']:.1f}/s", "cyan")
                    table.add_row("Packets", packet_text, "")
            
            # Interface section with separator
            table.add_row("", "", "")
            table.add_row("Interfaces", "", "")
            
            # Display interfaces with more details
            for name, info in interfaces.items():
                # Format IP addresses
                ipv4 = ', '.join(info['ipv4'][:2])
                if len(info['ipv4']) > 2:
                    ipv4 += f" (+{len(info['ipv4'])-2})"
                    
                speed_text = f"{info['speed']} Mbps" if info['speed'] else "Auto"
                duplex_text = f" ({info['duplex']})" if info['duplex'] != 'unknown' else ""
                
                table.add_row(
                    name[:12],
                    f"Speed: {speed_text}{duplex_text}",
                    f"IPv4: {ipv4} | MTU: {info['mtu']}"
                )
            
            # Enhanced error information display
            if any(metrics.get(key, 0) > 0 for key in ['error_in', 'error_out', 'drop_in', 'drop_out']):
                table.add_row("", "", "")
                table.add_row(
                    "Errors",
                    f"In: {metrics['error_in']} | Out: {metrics['error_out']}",
                    f"Drops - In: {metrics.get('drop_in', 0)} | "
                    f"Out: {metrics.get('drop_out', 0)}"
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
            
            # Store metrics in database
            if config['data']['persistence_enabled']:
                for key, value in metrics['rates'].items():
                    store_metric('disk', key, value)
                if 'busy_percent' in metrics:
                    store_metric('disk', 'busy_percent', metrics['busy_percent'])
            
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
                    
                    # Store partition metrics
                    if config['data']['persistence_enabled']:
                        store_metric('disk', f'usage_{part.mountpoint}', usage.percent)
                    
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
                
                # Busy time if available
                if 'busy_percent' in io_metrics:
                    table.add_row(
                        "Busy",
                        create_progress_bar(io_metrics['busy_percent']),
                        f"{io_metrics['busy_percent']:.1f}% Utilized"
                    )
            
            # Show partitions
            for part in partitions:
                name = os.path.basename(part['mountpoint']) or part['mountpoint']
                
                # Create usage bar
                usage_bar = create_progress_bar(part['percent'])
                
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
            
            # Store service metrics
            if config['data']['persistence_enabled']:
                for key, value in stats.items():
                    if key != 'total':  # Don't need to store the total
                        store_metric('services', key, value)
            
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
                
                # Create status bar
                status_bar = Text('■' * 10, color)
                
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

class GPUMonitor(BaseMonitor):
    """GPU monitor optimized for NVIDIA cards with graceful fallback."""
    
    def __init__(self):
        try:
            super().__init__()
            self.history = {
                'usage': deque(maxlen=30),
                'temp': deque(maxlen=30)
            }
            self.has_nvidia = check_gpu_available()  # Use the existing function
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
                
                # Store metrics in database
                if config['data']['persistence_enabled']:
                    store_metric('gpu', 'usage', metrics['usage'])
                    store_metric('gpu', 'temp', metrics['temp'])
                    store_metric('gpu', 'memory_percent', metrics['memory_percent'])
                
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
            
            # Usage row with power info
            power_text = ""
            if metrics.get('power_draw', 0) > 0:
                power_text = f" | {metrics['power_draw']:.1f}W"
            table.add_row(
                "Usage",
                create_progress_bar(metrics['usage']),
                f"{metrics['usage']:.1f}%{power_text}"
            )
            
            # Temperature row with fan speed
            temp_text = f"{metrics['temp']:.1f}°C"
            if metrics.get('fan_speed', 0) > 0:
                temp_text += f" | Fan: {metrics['fan_speed']:.0f}%"
            table.add_row(
                "Temperature",
                create_progress_bar(metrics['temp'], color="green" if metrics['temp'] < 70 
                                  else "yellow" if metrics['temp'] < 85 else "red"),
                temp_text
            )
            
            # Memory usage row
            table.add_row(
                "Memory",
                create_progress_bar(metrics['memory_percent']),
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
            
            # Store metrics in database
            if config['data']['persistence_enabled']:
                store_metric('firewall', 'blocked_rate', blocked_rate)
                store_metric('firewall', 'total_blocked', current_blocked)
            
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
            
            # Display block rate
            table.add_row(
                "Blocked",
                create_progress_bar(min(blocked_rate * 10, 100)),
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
    """Temperature and fan sensor monitor for Linux systems."""
    
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
    
    def _get_sensor_data(self) -> Dict[str, Any]:
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
                            
                            # Store temperature metrics
                            if config['data']['persistence_enabled']:
                                store_metric('sensors', f"temp_{temp_info['name']}", temp_info['current'])
                            
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
                            
                            # Store fan speed metrics
                            if config['data']['persistence_enabled']:
                                store_metric('sensors', f"fan_{fan_info['name']}", fan_info['speed'])
                            
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
                                            
                                            # Store power metrics
                                            if config['data']['persistence_enabled']:
                                                store_metric('sensors', f"power_{filename[:-6]}", power)
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
    
    def _get_temp_status(self, temp: float) -> str:
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
    
    def render(self) -> Panel:
        """Render sensor information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Sensor", style="cyan", width=12)
            table.add_column("Reading", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            sensor_data = self._get_sensor_data()
            
            if not any([sensor_data['temperatures'], sensor_data['fans'], sensor_data['power']]):
                return Panel(
                    Text("No sensor data available", style="yellow"),
                    title="Sensor Monitor",
                    border_style="red"
                )
            
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
                
                table.add_row(
                    temp['name'][:12],
                    create_progress_bar(min(100, temp_percent), color=status_color),
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
                        f"{power['value']:.1f}W",
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
                        f"{fan['speed']} RPM",
                        " | ".join(details)
                    )
            
            # Show temperature trends
            if self.history['temps']:
                table.add_row("", "", "")
                avg_temp = sum(self.history['temps']) / len(self.history['temps'])
                max_temp = max(self.history['temps'])
                min_temp = min(self.history['temps'])
                
                table.add_row(
                    "Temp Trend",
                    create_progress_bar(avg_temp),
                    f"Min: {min_temp:.1f}°C | Avg: {avg_temp:.1f}°C | Max: {max_temp:.1f}°C"
                )
            
            return Panel(table, title="Sensor Monitor", border_style="red")
            
        except Exception as e:
            logger.debug(f"Error rendering sensor panel: {e}\n{traceback.format_exc()}")
            return Panel(Text(f"Unable to read sensor data: {str(e)}", style="yellow"), title="Sensor Monitor", border_style="red")

class ProcessMonitor(BaseMonitor):
    """Process monitoring with sorting and filtering capabilities."""
    
    def __init__(self):
        try:
            super().__init__()
            self.sort_by = 'cpu'  # Default sort by CPU usage
            self.processes_limit = config['monitors']['process']['limit']
            self.last_process_check = 0
            self.process_cache = []
            self.process_cache_ttl = 2  # Cache TTL in seconds
            logger.debug("Process monitor initialized")
        except Exception as e:
            logger.error(f"Error initializing Process monitor: {e}")
            self.sort_by = 'cpu'
            self.processes_limit = 8  # Default value
            self.last_process_check = 0
            self.process_cache = []
            self.process_cache_ttl = 2
    
    def _get_processes(self) -> List[Dict[str, Any]]:
        """Get sorted process information with caching."""
        try:
            current_time = time.time()
            
            # Return cached results if valid
            if current_time - self.last_process_check < self.process_cache_ttl:
                return self.process_cache
            
            processes = []
            
            # Get all running processes
            for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent', 'create_time', 'status']):
                try:
                    # Get process info
                    proc_info = proc.info
                    
                    # Skip processes with no name
                    if not proc_info.get('name'):
                        continue
                    
                    # Get memory info in bytes
                    try:
                        mem_info = proc.memory_info()
                        proc_info['memory_bytes'] = mem_info.rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        proc_info['memory_bytes'] = 0
                    
                    # Get CPU affinity if available
                    try:
                        proc_info['cpu_affinity'] = proc.cpu_affinity()
                    except (psutil.AccessDenied, AttributeError):
                        proc_info['cpu_affinity'] = []
                    
                    # Get number of threads
                    try:
                        proc_info['num_threads'] = proc.num_threads()
                    except psutil.AccessDenied:
                        proc_info['num_threads'] = 0
                    
                    # Get process command line
                    try:
                        cmdline = proc.cmdline()
                        proc_info['cmdline'] = ' '.join(cmdline) if cmdline else ''
                    except (psutil.AccessDenied, psutil.ZombieProcess):
                        proc_info['cmdline'] = ''
                    
                    # Add process to list
                    processes.append(proc_info)
                    
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            
            # Sort processes by specified attribute
            if self.sort_by == 'cpu':
                processes.sort(key=lambda x: x.get('cpu_percent', 0) or 0, reverse=True)
            elif self.sort_by == 'memory':
                processes.sort(key=lambda x: x.get('memory_percent', 0) or 0, reverse=True)
            elif self.sort_by == 'time':
                processes.sort(key=lambda x: x.get('create_time', 0) or 0, reverse=True)
            
            # Store top processes in metrics database
            if config['data']['persistence_enabled']:
                for proc in processes[:5]:  # Store top 5 processes
                    pid = proc.get('pid', 0)
                    cpu = proc.get('cpu_percent', 0) or 0
                    mem = proc.get('memory_percent', 0) or 0
                    store_metric('process', f"cpu_pid_{pid}", cpu)
                    store_metric('process', f"mem_pid_{pid}", mem)
            
            # Update cache
            self.process_cache = processes[:self.processes_limit]
            self.last_process_check = current_time
            
            return self.process_cache
            
        except Exception as e:
            self.handle_error(e, "get_processes")
            logger.error(f"Error getting processes: {e}\n{traceback.format_exc()}")
            return []
    
    def render(self) -> Panel:
        """Render process information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("PID", style="cyan", width=7)
            table.add_column("Name", style="bright_blue", width=20)
            table.add_column("CPU%", justify="right", width=6)
            table.add_column("Memory", justify="right", width=10)
            table.add_column("Status", width=8)
            
            # Get process information
            processes = self._get_processes()
            
            # Show processes
            if processes:
                for proc in processes:
                    # Format CPU percentage with color
                    cpu_percent = proc.get('cpu_percent', 0) or 0
                    if cpu_percent > 50:
                        cpu_color = "red"
                    elif cpu_percent > 20:
                        cpu_color = "yellow"
                    else:
                        cpu_color = "green"
                    cpu_text = Text(f"{cpu_percent:5.1f}", cpu_color)
                    
                    # Format memory usage
                    mem_percent = proc.get('memory_percent', 0) or 0
                    mem_bytes = proc.get('memory_bytes', 0) or 0
                    mem_text = f"{format_bytes(mem_bytes)} ({mem_percent:.1f}%)"
                    
                    # Format process status
                    status = proc.get('status', '')
                    status_color = {
                        'running': 'green',
                        'sleeping': 'bright_black',
                        'stopped': 'yellow',
                        'zombie': 'red'
                    }.get(status, 'white')
                    status_text = Text(status, status_color)
                    
                    # Add process row
                    table.add_row(
                        str(proc.get('pid', '')),
                        proc.get('name', '')[:20],
                        cpu_text,
                        mem_text,
                        status_text
                    )
            else:
                table.add_row("No processes found", "", "", "", "")


            return Panel(table, title="Process Monitor", border_style="green")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Process Monitor Error: {str(e)}", style="red"), title="Process Monitor", border_style="green")

class BatteryMonitor(BaseMonitor):
    """Battery status monitor for laptop systems."""
    
    def render(self) -> Panel:
        """Render battery information panel."""
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Battery", style="cyan", width=12)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Check if battery is available
            battery = None
            if hasattr(psutil, 'sensors_battery'):
                try:
                    battery = psutil.sensors_battery()
                except Exception as e:
                    logger.debug(f"Failed to get battery info: {e}")
                    
            if not battery:
                return Panel(Text("No battery detected", style="yellow"), title="Battery Monitor", border_style="blue")
            
            # Store battery metrics
            if config['data']['persistence_enabled']:
                store_metric('battery', 'percent', battery.percent)
                store_metric('battery', 'plugged', 1 if battery.power_plugged else 0)
                store_metric('battery', 'seconds_left', battery.secsleft if battery.secsleft > 0 else 0)
            
            # Check battery level thresholds
            if not battery.power_plugged:
                self.check_threshold(100 - battery.percent, 'Battery Level', 'Remaining')
            
            # Calculate remaining time
            if battery.secsleft == psutil.POWER_TIME_UNLIMITED:
                time_left = "Unlimited"
            elif battery.secsleft == psutil.POWER_TIME_UNKNOWN:
                time_left = "Unknown"
            else:
                hours, remainder = divmod(battery.secsleft, 3600)
                minutes, seconds = divmod(remainder, 60)
                time_left = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            
            # Determine power source
            power_source = "AC Power" if battery.power_plugged else "Battery"
            
            # Determine status color
            if battery.power_plugged:
                status_color = "green"
            elif battery.percent > 50:
                status_color = "green"
            elif battery.percent > 20:
                status_color = "yellow"
            else:
                status_color = "red"
            
            # Add battery charge row
            table.add_row(
                "Charge",
                create_progress_bar(battery.percent, color=status_color),
                f"{battery.percent:.1f}% remaining"
            )
            
            # Add power source and time remaining
            table.add_row(
                "Power Source",
                power_source,
                f"Time left: {time_left}"
            )
            
            # Add charging status
            if battery.power_plugged:
                if battery.percent < 100:
                    status = "Charging"
                else:
                    status = "Fully Charged"
            else:
                status = "Discharging"
            
            table.add_row(
                "Status",
                status,
                ""
            )
            
            return Panel(table, title="Battery Monitor", border_style="blue")
        except Exception as e:
            self.handle_error(e, "render")
            return Panel(Text(f"Battery Monitor Error: {str(e)}", style="red"), title="Battery Monitor", border_style="blue")

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

def create_bar(percentage: float, width: int = 40) -> Text:
    """
    Create a progress bar with color based on percentage.
    Args:
        percentage: Value between 0 and 100
        width: Width of the progress bar in characters
    Returns:
        Rich Text object containing the formatted progress bar
    """
    try:
        filled = int(width * percentage / 100)
        remainder = width - filled

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
    except Exception as e:
        logger.error(f"Error creating bar: {e}")
        return Text("Error", "red")

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
    """Main system monitoring application."""
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "toggle_cpu", "CPU Monitor"),
        Binding("m", "toggle_memory", "Memory Monitor"),
        Binding("d", "toggle_disk", "Disk Monitor"),
        Binding("n", "toggle_network", "Network Monitor"),
        Binding("p", "toggle_process", "Process Monitor"),
        Binding("b", "toggle_battery", "Battery Monitor"),
        Binding("g", "toggle_gpu", "GPU Monitor"),
        Binding("s", "toggle_sensors", "Sensors"),
        Binding("a", "toggle_alerts", "Alerts"),
    ]
    
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
            Screen.DIALOG_CLASSES = []  # Disable all popups
            
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
                self.monitors['battery'] = BatteryMonitor()
                logger.debug("Created Battery monitor")
            except Exception as e:
                logger.error(f"Failed to create Battery monitor: {e}")
            
            try:
                self.monitors['alerts'] = AlertMonitor()
                logger.debug("Created Alerts monitor")
            except Exception as e:
                logger.error(f"Failed to create Alerts monitor: {e}")
            
            # Data persistence thread
            self.metrics_thread = None
            if config['data']['persistence_enabled']:
                try:
                    logger.info("Starting metrics cleanup thread")
                    self.metrics_thread = threading.Thread(target=self._metrics_cleanup_thread, daemon=True)
                    self.metrics_thread.start()
                except Exception as e:
                    logger.error(f"Failed to start metrics thread: {e}")
                    
            logger.info("SystemMonitorApp initialized successfully")
            
        except Exception as e:
            logger.critical(f"CRITICAL ERROR in SystemMonitorApp init: {e}\n{traceback.format_exc()}")
            # Initialize with minimal defaults to prevent crashes
            self.start_time = datetime.now()
            self.show_monitors = {}
            self.monitors = {}
    
    def _metrics_cleanup_thread(self):
        """Background thread to clean up old metrics."""
        try:
            while True:
                # Clean up metrics older than the configured retention period
                try:
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    
                    # Calculate cutoff date based on configured history length
                    retention_days = config['data']['history_length_days']
                    cursor.execute(
                        "DELETE FROM metrics WHERE timestamp < datetime('now', ?)",
                        [f"-{retention_days} days"]
                    )
                    
                    # Clean up old alerts
                    cursor.execute(
                        "DELETE FROM alerts WHERE timestamp < datetime('now', ?)",
                        [f"-{retention_days} days"]
                    )
                    
                    conn.commit()
                    conn.close()
                    logger.debug(f"Cleaned up metrics older than {retention_days} days")
                except Exception as e:
                    logger.error(f"Error cleaning up metrics: {e}")
                
                # Sleep for an hour before next cleanup
                time.sleep(3600)
        except Exception as e:
            logger.error(f"Metrics cleanup thread error: {e}")
    
    def on_key(self, event) -> None:
        """Handle CTRL+C with clean exit"""
        try:
            if event.key == "ctrl+c":
                logger.info("CTRL+C pressed, exiting")
                self.exit()
        except Exception as e:
            logger.error(f"Error in on_key: {e}")
    
    def action_quit(self) -> None:
        """Clean exit when quitting"""
        try:
            logger.info("Quit action triggered")
            self.exit()
        except Exception as e:
            logger.error(f"Error in action_quit: {e}")
            # Force exit if normal exit fails
            restore_terminal()
            sys.exit(1)
    
    def action_refresh(self) -> None:
        """Force refresh all monitors"""
        try:
            logger.debug("Manual refresh triggered")
            for name, monitor in self.monitors.items():
                try:
                    if hasattr(monitor, 'refresh'):
                        monitor.refresh()
                except Exception as e:
                    logger.error(f"Error refreshing {name} monitor: {e}")
        except Exception as e:
            logger.error(f"Error in action_refresh: {e}")
    
    def _toggle_monitor(self, monitor_name: str) -> None:
        """Toggle visibility of a specific monitor"""
        try:
            if monitor_name in self.show_monitors:
                self.show_monitors[monitor_name] = not self.show_monitors[monitor_name]
                logger.debug(f"Toggled {monitor_name} to {self.show_monitors[monitor_name]}")
                self.refresh_monitors()
        except Exception as e:
            logger.error(f"Error toggling monitor {monitor_name}: {e}")
    
    def action_toggle_cpu(self) -> None:
        self._toggle_monitor('cpu')
    
    def action_toggle_memory(self) -> None:
        self._toggle_monitor('memory')
    
    def action_toggle_disk(self) -> None:
        self._toggle_monitor('disk')
    
    def action_toggle_network(self) -> None:
        self._toggle_monitor('network')
    
    def action_toggle_process(self) -> None:
        self._toggle_monitor('process')
    
    def action_toggle_battery(self) -> None:
        self._toggle_monitor('battery')
    
    def action_toggle_gpu(self) -> None:
        self._toggle_monitor('gpu')
    
    def action_toggle_sensors(self) -> None:
        self._toggle_monitor('sensors')
    
    def action_toggle_alerts(self) -> None:
        self._toggle_monitor('alerts')
    
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
            
            # Simple footer with key bindings
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
            self.set_interval(1, self.update_header)
            
            logger.info("App mounted successfully")
        except Exception as e:
            logger.error(f"Error in on_mount: {e}\n{traceback.format_exc()}")
    
    def update_header(self) -> None:
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
            
            header = self.query_one(Header)
            if header:
                header.update(" | ".join(header_text))
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
        
        # Set up database if enabled
        if config['data']['persistence_enabled']:
            try:
                setup_database()
                logger.info("Database setup complete")
            except Exception as e:
                logger.error(f"Database setup failed: {e}\n{traceback.format_exc()}")
                console.print(f"[bold yellow]Warning: Database setup failed: {e}[/bold yellow]")
                # Continue without database
        
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