#!/usr/bin/env python3

# Standard library imports
from textual.screen import Screen
from textual.binding import Binding
from textual.widgets import Header, Footer, Static
from textual.containers import Container
from textual.app import App, ComposeResult
import psutil
import os
import sys
import time
import logging
import signal
from datetime import datetime
from collections import deque
import subprocess
import shutil
import gc
from typing import Dict, Optional, List, Union, Any, Tuple
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

import traceback

import platform
import socket
import re
import os
import sys
import time
import logging
import signal
from datetime import datetime
from collections import deque
import subprocess
import shutil
import gc
import psutil
from typing import Dict, Optional, List, Union, Any, Tuple



def check_gpu_available() -> bool:
    """
    Check if NVIDIA GPU is available on the system
    
    Returns:
        Boolean indicating if NVIDIA GPU is detected
    """
    try:
        # Method 1: Check nvidia-smi
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        # Method 2: Check common NVIDIA device paths
        nvidia_paths = [
            '/proc/driver/nvidia/version',
            '/dev/nvidia0',
            '/dev/nvidiactl'
        ]
        for path in nvidia_paths:
            if os.path.exists(path):
                return True

        # Method 3: Check PCI vendor ID
        try:
            for i in range(5):  # Check first 5 possible GPUs
                vendor_path = f'/sys/class/drm/card{i}/device/vendor'
                if os.path.exists(vendor_path):
                    with open(vendor_path) as f:
                        vendor = f.read().strip()
                        if vendor in ['0x10de', '10de']:  # NVIDIA vendor ID
                            return True
        except Exception:
            pass

        return False

    except Exception as e:
        logging.error(f"Error checking GPU availability: {e}")
        return False

def get_platform_info() -> Dict[str, str]:
    """
    Get system platform information
    
    Returns:
        Dictionary containing platform details
    """
    return {
        'system': platform.system().lower(),
        'release': platform.release(),
        'version': platform.version(),
        'machine': platform.machine(),
        'is_linux': platform.system().lower() == 'linux',
        'python_version': sys.version.split()[0],
        'processor': platform.processor()
    }

def get_socket_families() -> Dict[str, int]:
    """
    Get socket family mappings
    
    Returns:
        Dictionary mapping socket family names to values
    """
    return {
        'ipv4': socket.AF_INET,
        'ipv6': socket.AF_INET6,
        'unix': socket.AF_UNIX
    }

# Fix for socket reference in NetworkMonitor
def get_network_info() -> Dict[str, Any]:
    """
    Get network interface information
    
    Returns:
        Dictionary containing network interface details
    """
    interfaces = {}
    try:
        # Get network interfaces
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        
        for name, addrs_list in addrs.items():
            if name not in stats:
                continue
                
            # Skip interfaces that are down    
            if not stats[name].isup:
                continue
                
            # Process addresses
            ipv4 = []
            ipv6 = []
            for addr in addrs_list:
                if addr.family == socket.AF_INET:
                    ipv4.append(addr.address)
                elif addr.family == socket.AF_INET6:
                    ipv6.append(addr.address)
                    
            # Only include interfaces with IP addresses
            if ipv4 or ipv6:
                interfaces[name] = {
                    'ipv4': ipv4,
                    'ipv6': ipv6,
                    'speed': stats[name].speed or 0,
                    'mtu': stats[name].mtu,
                    'duplex': getattr(stats[name], 'duplex', 'unknown'),
                    'is_up': stats[name].isup
                }
                
    except Exception as e:
        logging.error(f"Error getting network info: {e}")
        
    return interfaces

def get_rules() -> List[Dict[str, str]]:
    """
    Get firewall rules safely
    
    Returns:
        List of dictionaries containing firewall rules
    """
    rules = []
    
    try:
        # Get iptables rules
        cmd = ['iptables', '-L', '-n', '-v']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        
        if result.returncode == 0:
            for line in result.stdout.split('\n')[2:]:  # Skip headers
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
                        
    except Exception as e:
        logging.error(f"Error getting firewall rules: {e}")
        
    return rules



# Set up crash dump directory
CRASH_DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'crash_dumps')
os.makedirs(CRASH_DUMP_DIR, exist_ok=True)

def setup_crash_logging():
    """Initialize crash logging with both file and console output"""
    try:
        # Create logs directory with error handling
        log_dir = 'logs'
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception as e:
            sys.stderr.write(f"Critical: Cannot create log directory: {e}\n")
            sys.exit(1)

        # Generate unique log filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f'logs/system_monitor_{timestamp}.log'
        crash_file = f'logs/crash_{timestamp}.log'

        # Configure main logger
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)

        # Create and configure file handler for normal logging
        try:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')
            )
            logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(f"Critical: Cannot create log file handler: {e}\n")
            sys.exit(1)

        # Create and configure crash file handler
        try:
            crash_handler = logging.FileHandler(crash_file)
            crash_handler.setLevel(logging.ERROR)
            crash_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s\n'
                                'Exception details:\n%(exc_info)s')
            )
            logger.addHandler(crash_handler)
        except Exception as e:
            sys.stderr.write(f"Critical: Cannot create crash file handler: {e}\n")
            sys.exit(1)

        # Console handler for immediate feedback
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(console_handler)

        # Remove any duplicate handlers
        seen_handlers = set()
        for handler in logger.handlers[:]:
            handler_key = (handler.__class__, handler.level)
            if handler_key in seen_handlers:
                logger.removeHandler(handler)
            seen_handlers.add(handler_key)

        logger.info("Logging system initialized successfully")
        return logger

    except Exception as e:
        sys.stderr.write(f"CRITICAL: Failed to initialize logging system: {e}\n")
        traceback.print_exc()
        sys.exit(1)

# Initialize logging
logger = setup_crash_logging()

# Required third-party imports with error handling
REQUIRED_PACKAGES = {
    'rich': 'Rich text formatting library',
    'textual': 'TUI framework',
    'psutil': 'System monitoring library'
}

missing_packages = []

try:
    # Rich imports
    from rich import box
    from rich.text import Text
    from rich.panel import Panel
    from rich.table import Table
    logger.debug("Rich imports successful")

    # Textual imports
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Static
    from textual.containers import Grid, Container
    from textual.binding import Binding
    logger.debug("Textual imports successful")

    # psutil import
    import psutil
    logger.debug("psutil import successful")

except ImportError as e:
    logger.critical(f"Failed to import required package: {e}")
    print("\nMissing required dependencies. Please install with:")
    print("pip install psutil textual rich")
    sys.exit(1)
except Exception as e:
    logger.critical(f"Unexpected error during imports: {e}", exc_info=True)
    sys.exit(1)

# Platform detection with error handling
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
    logger.info(f"Platform detected: {PLATFORM_INFO['system']} {PLATFORM_INFO['release']}")
    
    
except Exception as e:
    logger.critical(f"Failed to detect platform information: {e}", exc_info=True)
    PLATFORM_INFO = {
        'system': 'unknown',
        'release': 'unknown',
        'version': 'unknown',
        'machine': 'unknown',
        'is_linux': False,
        'python_version': 'unknown',
        'processor': 'unknown'
    }

# Monitoring intervals with safety defaults
MONITOR_INTERVALS = {
    'cpu': 1.5,
    'memory': 1.5,
    'network': 1.5,
    'disk': 1.5,
    'gpu': 1.5,
    'sensors': 2.0,
    'services': 5.0
}

# Global configuration
CORES_PER_LINE = 4
should_exit = False

# Cache configuration with error handling
try:
    CACHE_TTL = {
        'processor': 60,
        'gpu': 1,
        'services': 5,
        'partitions': 5,
        'network': 2,
        'sensors': 3
    }

    SYSTEM_INFO_CACHE = {
        'processor_name': None,
        'processor_cache_time': 0,
        'gpu_info': None,
        'gpu_cache_time': 0,
        'service_status': {},
        'service_cache_time': 0,
        'partition_info': None,
        'partition_cache_time': 0,
        'network_interfaces': None,
        'network_cache_time': 0,
        'sensor_data': None,
        'sensor_cache_time': 0
    }
except Exception as e:
    logger.critical(f"Failed to initialize cache structures: {e}", exc_info=True)
    sys.exit(1)



def create_crash_dump(exc_info, context: str) -> None:
    """
    Create detailed crash dump with full system state
    
    Args:
        exc_info: Exception information from sys.exc_info()
        context: String describing where the crash occurred
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dump_file = os.path.join(CRASH_DUMP_DIR, f'crash_dump_{timestamp}.txt')
        
        with open(dump_file, 'w') as f:
            # Basic crash info
            f.write(f"=== System Monitor Crash Dump ===\n")
            f.write(f"Time: {datetime.now()}\n")
            f.write(f"Context: {context}\n\n")
            
            # Exception details
            f.write("=== Exception Information ===\n")
            f.write(f"Type: {exc_info[0].__name__}\n")
            f.write(f"Message: {str(exc_info[1])}\n")
            f.write("\nTraceback:\n")
            f.write(''.join(traceback.format_tb(exc_info[2])))
            f.write("\n")
            
            # System state
            f.write("=== System State ===\n")
            try:
                f.write(f"CPU Usage: {psutil.cpu_percent(interval=0.1)}%\n")
                mem = psutil.virtual_memory()
                f.write(f"Memory Usage: {mem.percent}%\n")
                f.write(f"Available Memory: {mem.available/1024/1024:.1f} MB\n")
            except Exception as e:
                f.write(f"Failed to get system state: {e}\n")
            
            # Cache state
            f.write("\n=== Cache State ===\n")
            for key, value in SYSTEM_INFO_CACHE.items():
                f.write(f"{key}: {value}\n")
                
        logger.info(f"Created crash dump: {dump_file}")
    except Exception as e:
        logger.error(f"Failed to create crash dump: {e}", exc_info=True)



        

def set_terminal_title(title: str) -> None:
    """
    Set the terminal window title with error handling
    
    Args:
        title: String to set as terminal title
    """
    try:
        if os.name == 'nt':  # Windows
            result = os.system(f'title {title}')
            if result != 0:
                logger.warning(f"Failed to set Windows terminal title, error code: {result}")
        else:  # Unix-like
            sys.stdout.write(f'\033]0;{title}\007')
            sys.stdout.flush()
        logger.debug(f"Terminal title set to: {title}")
    except Exception as e:
        logger.error(f"Failed to set terminal title: {e}", exc_info=True)

def format_bytes(bytes_value: float) -> str:
    """
    Format byte values to human readable string with error handling
    
    Args:
        bytes_value: Number of bytes to format
        
    Returns:
        Formatted string representation
    """
    try:
        if not isinstance(bytes_value, (int, float)):
            raise ValueError(f"Invalid bytes value type: {type(bytes_value)}")
            
        bytes_value = float(bytes_value)
        if bytes_value < 0:
            raise ValueError(f"Negative bytes value: {bytes_value}")
            
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024:
                return f"{bytes_value:6.1f}{unit}"
            bytes_value /= 1024
        return f"{bytes_value:6.1f}TB"
        
    except Exception as e:
        logger.error(f"Error formatting bytes: {e}", exc_info=True)
        return "ERROR"

def create_progress_bar(percentage: float, width: int = 40, color: str = None) -> Text:
    """
    Create a progress bar with error handling
    
    Args:
        percentage: Value between 0 and 100
        width: Width of progress bar in characters
        color: Color name for the bar
        
    Returns:
        Rich Text object containing the progress bar
    """
    try:
        # Input validation
        if not isinstance(percentage, (int, float)):
            raise ValueError(f"Invalid percentage type: {type(percentage)}")
        if not isinstance(width, int):
            raise ValueError(f"Invalid width type: {type(width)}")
            
        percentage = float(percentage)
        percentage = max(0, min(100, percentage))  # Clamp between 0 and 100
        
        if color is None:
            color = ("green" if percentage < 50 else 
                    "yellow" if percentage < 75 else 
                    "red" if percentage < 90 else 
                    "bright_red")
        
        filled = int(width * percentage / 100)
        remainder = width - filled
        
        # Create bar components
        try:
            filled_section = Text('■' * filled, color)
            empty_section = Text('·' * remainder, "bright_black")
            percentage_text = Text(f" {percentage:5.1f}%", color)
            
            # Combine sections
            return filled_section + empty_section + percentage_text
        except Exception as e:
            logger.error(f"Error creating progress bar components: {e}", exc_info=True)
            return Text("Error creating progress bar", "red")
            
    except Exception as e:
        logger.error(f"Error in create_progress_bar: {e}", exc_info=True)
        return Text("Error creating progress bar", "red")

def restore_terminal() -> None:
    """
    Restore terminal to normal state with comprehensive error handling
    """
    logger.info("Starting terminal restoration")
    
    errors = []
    
    # Reset terminal settings
    try:
        if os.name != 'nt':  # Unix-like systems
            result = os.system('stty sane')
            if result != 0:
                errors.append(f"stty command failed with code {result}")
    except Exception as e:
        errors.append(f"Failed to reset terminal settings: {e}")
    
    # Clear screen
    try:
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')
    except Exception as e:
        errors.append(f"Failed to clear screen: {e}")
    
    # Reset cursor
    try:
        # Show cursor
        print('\033[?25h', end='', flush=True)
        # Clear screen
        print('\033[2J', end='', flush=True)
        # Move cursor home
        print('\033[H', end='', flush=True)
    except Exception as e:
        errors.append(f"Failed to reset cursor: {e}")
    
    if errors:
        logger.error("Errors during terminal restoration:\n" + "\n".join(errors))
    else:
        logger.info("Terminal restored successfully")

def setup_signal_handlers() -> None:
    """
    Set up signal handlers with proper terminal cleanup and error handling
    """
    def safe_exit(sig: int, frame) -> None:
        try:
            logger.info(f"Received signal {sig}, initiating safe shutdown")
            
            # Create crash dump if it's not a normal termination
            if sig != signal.SIGTERM:
                create_crash_dump(sys.exc_info(), f"Signal handler: {sig}")
            
            # Attempt terminal restoration
            try:
                restore_terminal()
            except Exception as e:
                logger.error(f"Failed to restore terminal during signal handling: {e}")
            
            # Set exit flag
            global should_exit
            should_exit = True
            
            logger.info("Clean shutdown completed")
            sys.exit(0)
            
        except Exception as e:
            logger.critical(f"Fatal error in signal handler: {e}", exc_info=True)
            sys.exit(1)
    
    try:
        # Register handlers
        signal.signal(signal.SIGINT, safe_exit)
        signal.signal(signal.SIGTERM, safe_exit)
        logger.info("Signal handlers registered successfully")
        
    except Exception as e:
        logger.critical(f"Failed to setup signal handlers: {e}", exc_info=True)
        sys.exit(1)



class BaseMonitor(Static):
    """Base class for all system monitors with enhanced error handling and logging"""
    
    def __init__(self):
        try:
            # Initialize parent
            super().__init__()
            
            # Monitor state
            self.error_count = 0
            self.max_errors = 3
            self.last_update = time.time()
            self.error_backoff = 1.0  # Initial backoff in seconds
            self.max_backoff = 30.0   # Maximum backoff in seconds
            
            # Monitor status
            self._active = True
            self._failed = False
            
            logger.debug(f"{self.__class__.__name__} initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize {self.__class__.__name__}: {e}", 
                          exc_info=True)
            self._failed = True
            create_crash_dump(sys.exc_info(), f"{self.__class__.__name__} initialization")
            raise

    def handle_error(self, error: Exception, context: str) -> None:
        """
        Handle monitor errors with exponential backoff and crash dumps
        
        Args:
            error: Exception that occurred
            context: String describing error context
        """
        try:
            self.error_count += 1
            
            # Create detailed error context
            error_info = {
                'monitor_class': self.__class__.__name__,
                'error_count': self.error_count,
                'context': context,
                'error_type': type(error).__name__,
                'error_msg': str(error),
                'time': datetime.now().isoformat()
            }
            
            # Log error with context
            logger.error(
                f"Error in {self.__class__.__name__} ({context}): {error}",
                extra={'error_info': error_info},
                exc_info=True
            )
            
            # Create crash dump for serious errors
            if self.error_count >= self.max_errors:
                create_crash_dump(
                    sys.exc_info(),
                    f"{self.__class__.__name__} exceeded max errors"
                )
                self._failed = True
                
            # Implement exponential backoff
            self.error_backoff = min(
                self.error_backoff * 2,
                self.max_backoff
            )
            
        except Exception as e:
            logger.critical(f"Error in error handler: {e}", exc_info=True)

    def get_interval(self) -> float:
        """
        Get monitor update interval with safety checks
        
        Returns:
            Float representing seconds between updates
        """
        try:
            monitor_type = self.__class__.__name__.lower().replace('monitor', '')
            interval = MONITOR_INTERVALS.get(monitor_type, 1.0)
            
            # Apply backoff if errors occurred
            if self.error_count > 0:
                interval = max(interval, self.error_backoff)
                
            return interval
            
        except Exception as e:
            logger.error(f"Error getting interval: {e}", exc_info=True)
            return 5.0  # Safe default

    async def _do_refresh(self) -> None:
        """Safely perform monitor refresh with error handling"""
        if self._failed:
            return
            
        try:
            # Check update interval
            now = time.time()
            if now - self.last_update < self.get_interval():
                return
                
            # Attempt refresh
            rendered = self.render()
            self.update(rendered)
            
            # Update successful - reset error state
            self.error_count = max(0, self.error_count - 1)
            self.error_backoff = max(1.0, self.error_backoff / 2)
            self.last_update = now
            
        except Exception as e:
            self.handle_error(e, "refresh")
            
            # Show error state in UI
            try:
                error_panel = Panel(
                    f"Monitor Error: {str(e)}",
                    title=self.__class__.__name__,
                    border_style="red"
                )
                self.update(error_panel)
            except Exception as render_err:
                logger.error(f"Failed to show error state: {render_err}")

    def on_mount(self) -> None:
        """Safe monitor mounting with error handling"""
        try:
            if not self._failed:
                self.set_interval(self.get_interval(), self.refresh)
                logger.debug(f"{self.__class__.__name__} mounted successfully")
        except Exception as e:
            logger.error(f"Error mounting {self.__class__.__name__}: {e}", 
                        exc_info=True)

class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor with enhanced error handling"""
    
    def __init__(self):
        try:
            super().__init__()
            self.update_interval = MONITOR_INTERVALS['cpu']
            self.cpu_history = deque(maxlen=60)  # 1 minute history
            self.last_cpu_percent = None
            
            # Initialize processor name
            self._get_processor_name()
            
            logger.debug("CPUMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize CPUMonitor: {e}", exc_info=True)
            raise

    def _get_processor_name(self) -> str:
        """
        Get processor name with caching and error handling
        
        Returns:
            String containing processor name or empty string on error
        """
        try:
            current_time = time.time()
            
            # Check cache validity
            if (SYSTEM_INFO_CACHE['processor_name'] is None or 
                current_time - SYSTEM_INFO_CACHE['processor_cache_time'] > 
                CACHE_TTL['processor']):
                
                processor_name = ""
                
                if sys.platform == "linux":
                    try:
                        with open("/proc/cpuinfo", "r") as f:
                            for line in f:
                                if "model name" in line:
                                    processor_name = line.split(":")[1].strip()
                                    break
                    except Exception as e:
                        logger.error(f"Error reading /proc/cpuinfo: {e}")
                        
                elif sys.platform == "darwin":  # macOS
                    try:
                        result = subprocess.run(
                            ['sysctl', '-n', 'machdep.cpu.brand_string'],
                            capture_output=True,
                            text=True,
                            timeout=1
                        )
                        if result.returncode == 0:
                            processor_name = result.stdout.strip()
                    except Exception as e:
                        logger.error(f"Error getting macOS CPU info: {e}")
                        
                elif sys.platform == "win32":  # Windows
                    try:
                        result = subprocess.run(
                            ['wmic', 'cpu', 'get', 'name'],
                            capture_output=True,
                            text=True,
                            timeout=1
                        )
                        if result.returncode == 0:
                            processor_name = result.stdout.split('\n')[1].strip()
                    except Exception as e:
                        logger.error(f"Error getting Windows CPU info: {e}")
                
                # Update cache
                SYSTEM_INFO_CACHE['processor_name'] = processor_name
                SYSTEM_INFO_CACHE['processor_cache_time'] = current_time
                
            return SYSTEM_INFO_CACHE['processor_name']
            
        except Exception as e:
            logger.error(f"Error getting processor name: {e}", exc_info=True)
            return ""
        

class CPUMonitor(BaseMonitor):
    def get_usage_color(self, percentage: float) -> str:
        """
        Determine color based on CPU usage percentage with safety checks
        
        Args:
            percentage: CPU usage percentage
            
        Returns:
            String containing color name
        """
        try:
            if not isinstance(percentage, (int, float)):
                logger.warning(f"Invalid percentage type: {type(percentage)}")
                return "yellow"
                
            percentage = float(percentage)
            if percentage < 0 or percentage > 100:
                logger.warning(f"Percentage out of range: {percentage}")
                return "yellow"
                
            if percentage < 50:
                return "green"
            elif percentage < 75:
                return "yellow"
            elif percentage < 90:
                return "red"
            return "bright_red"
            
        except Exception as e:
            logger.error(f"Error determining CPU color: {e}", exc_info=True)
            return "yellow"  # Safe default

    def create_colored_bar(self, percentage: float, width: int = 30) -> Text:
        """
        Create a color-coded CPU usage bar with error handling
        
        Args:
            percentage: CPU usage percentage
            width: Width of bar in characters
            
        Returns:
            Rich Text object containing the bar
        """
        try:
            color = self.get_usage_color(percentage)
            
            # Validate width
            width = max(10, min(100, int(width)))
            
            # Calculate filled portion
            filled = int(width * max(0, min(100, percentage)) / 100)
            remainder = width - filled
            
            # Create bar components
            try:
                value_text = Text(f"{percentage:5.1f}% ", color)
                filled_bar = Text('■' * filled, color)
                empty_bar = Text('·' * remainder, "bright_black")
                return value_text + filled_bar + empty_bar
            except Exception as e:
                logger.error(f"Error creating bar components: {e}", exc_info=True)
                return Text("Error", "red")
                
        except Exception as e:
            logger.error(f"Error creating CPU bar: {e}", exc_info=True)
            return Text("Error", "red")

    def create_core_row(self, start_idx: int, cpu_percent: list) -> list:
        """
        Create a row of CPU core displays with error handling
        
        Args:
            start_idx: Starting core index
            cpu_percent: List of CPU percentages
            
        Returns:
            List of Text objects for the row
        """
        try:
            cores_in_row = []
            end_idx = min(start_idx + CORES_PER_LINE, len(cpu_percent))
            
            for i in range(start_idx, end_idx):
                try:
                    core_text = Text(f"Core {i:2d}: ", "cyan")
                    usage_bar = self.create_colored_bar(cpu_percent[i])
                    cores_in_row.append(core_text + usage_bar)
                except Exception as e:
                    logger.error(f"Error creating core {i} display: {e}")
                    cores_in_row.append(Text(f"Core {i} Error", "red"))
            
            return cores_in_row
            
        except Exception as e:
            logger.error(f"Error creating core row: {e}", exc_info=True)
            return [Text("Core Row Error", "red")]

    def get_cpu_metrics(self) -> Dict[str, Any]:
        """
        Safely gather all CPU metrics with error handling
        
        Returns:
            Dictionary containing CPU metrics
        """
        metrics = {
            'cpu_percent': None,
            'freq': None,
            'times': None,
            'load': None,
            'errors': []
        }
        
        try:
            # Get CPU percentages
            metrics['cpu_percent'] = psutil.cpu_percent(percpu=True)
            
            # Get CPU frequency
            try:
                metrics['freq'] = psutil.cpu_freq()
            except Exception as e:
                metrics['errors'].append(f"Frequency error: {e}")
                metrics['freq'] = None
            
            # Get CPU times
            try:
                metrics['times'] = psutil.cpu_times_percent()
            except Exception as e:
                metrics['errors'].append(f"Times error: {e}")
                metrics['times'] = None
            
            # Get load average
            try:
                metrics['load'] = psutil.getloadavg()
            except Exception as e:
                metrics['errors'].append(f"Load error: {e}")
                metrics['load'] = None
                
        except Exception as e:
            logger.error(f"Error getting CPU metrics: {e}", exc_info=True)
            metrics['errors'].append(f"Critical error: {e}")
            
        return metrics

    def render(self) -> Panel:
        """
        Render CPU monitor display with comprehensive error handling
        
        Returns:
            Rich Panel containing CPU information
        """
        try:
            # Create main table
            table = Table(box=None, expand=True, padding=(0,0))
            
            # Get CPU metrics
            metrics = self.get_cpu_metrics()
            
            if metrics['errors']:
                logger.warning("CPU metric errors: " + "; ".join(metrics['errors']))
            
            # Create metrics header table
            metrics_table = Table(box=None, expand=True, padding=(0,0))
            metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
            metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)
            
            # Add total CPU usage
            if metrics['cpu_percent']:
                try:
                    total = sum(metrics['cpu_percent']) / len(metrics['cpu_percent'])
                    metrics_table.add_row(
                        Text("Total: ") + self.create_colored_bar(total),
                        Text(f"Freq: {int(metrics['freq'].current) if metrics['freq'] else 'N/A'}MHz | "
                             f"Load: {metrics['load'][0]:5.2f if metrics['load'] else 'N/A'}")
                    )
                except Exception as e:
                    logger.error(f"Error creating total CPU row: {e}")
                    metrics_table.add_row("Error calculating total", "")
            
            # Add CPU states
            if metrics['times']:
                try:
                    color_user = self.get_usage_color(metrics['times'].user)
                    color_sys = self.get_usage_color(metrics['times'].system)
                    states_text = (
                        Text(f"User: {metrics['times'].user:4.1f}% ", color_user) +
                        Text(f"Sys: {metrics['times'].system:4.1f}% ", color_sys) +
                        Text(f"Idle: {metrics['times'].idle:4.1f}%", "bright_black")
                    )
                    metrics_table.add_row(states_text, "")
                except Exception as e:
                    logger.error(f"Error creating CPU states row: {e}")
                    metrics_table.add_row("Error showing CPU states", "")
            
            # Add metrics to main table
            table.add_row(metrics_table)
            
            # Create cores table
            if metrics['cpu_percent']:
                try:
                    cores_table = Table(box=None, expand=True, padding=(0,0))
                    
                    # Add columns based on CORES_PER_LINE
                    for _ in range(CORES_PER_LINE):
                        cores_table.add_column(ratio=1)
                    
                    # Add core rows
                    for i in range(0, len(metrics['cpu_percent']), CORES_PER_LINE):
                        cores_in_row = self.create_core_row(i, metrics['cpu_percent'])
                        # Pad row if needed
                        while len(cores_in_row) < CORES_PER_LINE:
                            cores_in_row.append("")
                        cores_table.add_row(*cores_in_row)
                    
                    # Add cores grid to main table
                    table.add_row(cores_table)
                except Exception as e:
                    logger.error(f"Error creating cores table: {e}")
                    table.add_row(Text("Error displaying CPU cores", "red"))
            
            # Get processor name and create title
            processor_name = self._get_processor_name()
            title = f"CPU Monitor - {processor_name}" if processor_name else "CPU Monitor"

            return Panel(
                table,
                title=title,
                border_style="blue"
            )
            
        except Exception as e:
            logger.error(f"Critical error in CPU monitor render: {e}", exc_info=True)
            return Panel(
                Text("CPU Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        

class MemoryMonitor(BaseMonitor):
    """
    Memory usage monitor with comprehensive error handling and detailed metrics
    
    Monitors:
    - RAM usage
    - Swap usage
    - Cache statistics
    - Memory pressure
    """
    
    def __init__(self):
        try:
            super().__init__()
            self.update_interval = MONITOR_INTERVALS['memory']
            
            # Initialize history tracking
            self.history = {
                'ram_used': deque(maxlen=60),
                'swap_used': deque(maxlen=60),
                'cache_used': deque(maxlen=60)
            }
            
            # Memory pressure thresholds (percentages)
            self.thresholds = {
                'ram_warning': 80.0,
                'ram_critical': 90.0,
                'swap_warning': 50.0,
                'swap_critical': 80.0
            }
            
            logger.debug("MemoryMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize MemoryMonitor: {e}", exc_info=True)
            raise

    def _get_memory_metrics(self) -> Dict[str, Any]:
        """
        Gather comprehensive memory metrics with error handling
        
        Returns:
            Dictionary containing all memory-related metrics
        """
        metrics = {
            'ram': None,
            'swap': None,
            'cache': None,
            'pressure': None,
            'errors': []
        }
        
        try:
            # Get virtual memory metrics
            try:
                vm = psutil.virtual_memory()
                metrics['ram'] = {
                    'total': vm.total,
                    'used': vm.used,
                    'free': vm.free,
                    'available': vm.available,
                    'percent': vm.percent,
                    'cached': getattr(vm, 'cached', 0),
                    'buffers': getattr(vm, 'buffers', 0)
                }
                
                # Update history
                self.history['ram_used'].append(vm.used)
                
            except Exception as e:
                error_msg = f"RAM metrics error: {e}"
                logger.error(error_msg, exc_info=True)
                metrics['errors'].append(error_msg)
            
            # Get swap metrics
            try:
                swap = psutil.swap_memory()
                metrics['swap'] = {
                    'total': swap.total,
                    'used': swap.used,
                    'free': swap.free,
                    'percent': swap.percent,
                    'sin': getattr(swap, 'sin', 0),
                    'sout': getattr(swap, 'sout', 0)
                }
                
                # Update history
                self.history['swap_used'].append(swap.used)
                
            except Exception as e:
                error_msg = f"Swap metrics error: {e}"
                logger.error(error_msg, exc_info=True)
                metrics['errors'].append(error_msg)
            
            # Get cache metrics if available
            if hasattr(vm, 'cached'):
                try:
                    cache_total = vm.cached + getattr(vm, 'buffers', 0)
                    cache_percent = (cache_total / vm.total) * 100
                    
                    metrics['cache'] = {
                        'total': cache_total,
                        'percent': cache_percent,
                        'cached': vm.cached,
                        'buffers': getattr(vm, 'buffers', 0)
                    }
                    
                    # Update history
                    self.history['cache_used'].append(cache_total)
                    
                except Exception as e:
                    error_msg = f"Cache metrics error: {e}"
                    logger.error(error_msg, exc_info=True)
                    metrics['errors'].append(error_msg)
            
            # Get memory pressure on Linux systems
            if PLATFORM_INFO['is_linux']:
                try:
                    pressure_file = '/proc/pressure/memory'
                    if os.path.exists(pressure_file):
                        with open(pressure_file, 'r') as f:
                            content = f.read().strip()
                            # Parse pressure data
                            # Format: some avg10=0.00 avg60=0.00 avg300=0.00 total=0
                            data = {}
                            for line in content.split('\n'):
                                kind, stats = line.split(' ', 1)
                                data[kind] = {
                                    item.split('=')[0]: float(item.split('=')[1])
                                    for item in stats.split(' ')
                                }
                            metrics['pressure'] = data
                            
                except Exception as e:
                    error_msg = f"Pressure metrics error: {e}"
                    logger.error(error_msg, exc_info=True)
                    metrics['errors'].append(error_msg)
            
        except Exception as e:
            logger.error(f"Critical error getting memory metrics: {e}", exc_info=True)
            metrics['errors'].append(f"Critical error: {e}")
        
        return metrics

    def _get_status_color(self, percent: float, is_swap: bool = False) -> str:
        """
        Determine color based on memory usage percentage with safety checks
        
        Args:
            percent: Usage percentage
            is_swap: Whether this is for swap space
            
        Returns:
            String containing color name
        """
        try:
            if not isinstance(percent, (int, float)):
                return "yellow"
                
            percent = float(percent)
            
            # Get appropriate thresholds
            warning = (self.thresholds['swap_warning'] if is_swap 
                      else self.thresholds['ram_warning'])
            critical = (self.thresholds['swap_critical'] if is_swap 
                       else self.thresholds['ram_critical'])
            
            if percent < warning:
                return "green"
            elif percent < critical:
                return "yellow"
            return "red"
            
        except Exception as e:
            logger.error(f"Error determining status color: {e}", exc_info=True)
            return "yellow"

    def render(self) -> Panel:
        """
        Render memory information with comprehensive error handling
        
        Returns:
            Rich Panel containing memory information
        """
        try:
            # Create monitor-specific table
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Memory", style="cyan", width=12, no_wrap=True)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get memory metrics
            metrics = self._get_memory_metrics()
            
            if metrics['errors']:
                logger.warning("Memory metric errors: " + "; ".join(metrics['errors']))
            
            # RAM Usage
            if metrics['ram']:
                try:
                    ram = metrics['ram']
                    color = self._get_status_color(ram['percent'])
                    
                    table.add_row(
                        "RAM",
                        create_progress_bar(ram['percent'], color=color),
                        f"Used: {format_bytes(ram['used'])} / "
                        f"Total: {format_bytes(ram['total'])}"
                    )
                except Exception as e:
                    logger.error(f"Error creating RAM row: {e}")
                    table.add_row("RAM", "Error displaying RAM usage", "")
            
            # Cache Usage
            if metrics['cache']:
                try:
                    cache = metrics['cache']
                    table.add_row(
                        "Cache",
                        create_progress_bar(cache['percent']),
                        f"Cached: {format_bytes(cache['cached'])} | "
                        f"Buffers: {format_bytes(cache['buffers'])}"
                    )
                except Exception as e:
                    logger.error(f"Error creating cache row: {e}")
                    table.add_row("Cache", "Error displaying cache", "")
            
            # Effective Memory (RAM minus cache)
            if metrics['ram']:
                try:
                    ram = metrics['ram']
                    effective_used = ram['total'] - ram['available']
                    if metrics['cache']:
                        effective_used -= metrics['cache']['total']
                    
                    if effective_used >= 0:
                        effective_percent = (effective_used / ram['total']) * 100
                        table.add_row(
                            "Effective",
                            create_progress_bar(effective_percent),
                            f"Used: {format_bytes(effective_used)} | "
                            f"Available: {format_bytes(ram['available'])}"
                        )
                except Exception as e:
                    logger.error(f"Error creating effective memory row: {e}")
                    table.add_row("Effective", "Error calculating effective memory", "")
            
            # Swap Usage
            if metrics['swap']:
                try:
                    swap = metrics['swap']
                    color = self._get_status_color(swap['percent'], is_swap=True)
                    
                    table.add_row(
                        "Swap",
                        create_progress_bar(swap['percent'], color=color),
                        f"Used: {format_bytes(swap['used'])} / "
                        f"Total: {format_bytes(swap['total'])}"
                    )
                except Exception as e:
                    logger.error(f"Error creating swap row: {e}")
                    table.add_row("Swap", "Error displaying swap usage", "")
            
            # Memory Pressure (Linux only)
            if metrics['pressure']:
                try:
                    pressure = metrics['pressure']
                    if 'some' in pressure:
                        table.add_row(
                            "Pressure",
                            f"10s: {pressure['some']['avg10']:5.2f}%",
                            f"60s: {pressure['some']['avg60']:5.2f}% | "
                            f"300s: {pressure['some']['avg300']:5.2f}%"
                        )
                except Exception as e:
                    logger.error(f"Error creating pressure row: {e}")
                    table.add_row("Pressure", "Error displaying memory pressure", "")

            return Panel(table, title="Memory Monitor", border_style="green")
            
        except Exception as e:
            logger.error(f"Critical error in memory monitor render: {e}", exc_info=True)
            return Panel(
                Text("Memory Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        


class NetworkMonitor(BaseMonitor):
    """
    Network monitoring with detailed statistics and error handling
    
    Features:
    - Bandwidth monitoring
    - Connection tracking
    - Interface statistics
    - Error detection
    """
    
    def __init__(self):
        try:
            super().__init__()
            # Initialize network tracking
            self.last_io = self._safe_get_counters()
            self.last_time = time.time()
            
            # Initialize history tracking with safety limits
            self.history = {
                'bytes_sent': deque(maxlen=60),     # 1 minute of bandwidth history
                'bytes_recv': deque(maxlen=60),
                'packets_sent': deque(maxlen=60),
                'packets_recv': deque(maxlen=60),
                'error_in': deque(maxlen=30),       # Error history
                'error_out': deque(maxlen=30),
                'drop_in': deque(maxlen=30),        # Drop history
                'drop_out': deque(maxlen=30)
            }
            
            # Speed thresholds for coloring (bytes per second)
            self.thresholds = {
                'low_speed': 1024 * 1024,        # 1 MB/s
                'medium_speed': 10 * 1024 * 1024,  # 10 MB/s
                'high_speed': 50 * 1024 * 1024    # 50 MB/s
            }
            
            logger.debug("NetworkMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize NetworkMonitor: {e}", exc_info=True)
            raise

    def _safe_get_counters(self) -> Optional[psutil._common.snetio]:
        """
        Safely get network counters with error handling
        
        Returns:
            Network IO counters or None on error
        """
        try:
            return psutil.net_io_counters()
        except Exception as e:
            logger.error(f"Error getting network counters: {e}", exc_info=True)
            return None

    def _get_interface_info(self) -> Dict[str, Any]:
        """
        Get cached network interface information with error handling
        
        Returns:
            Dictionary containing interface information and status
        """
        try:
            current_time = time.time()
            
            if (SYSTEM_INFO_CACHE['network_interfaces'] is None or 
                current_time - SYSTEM_INFO_CACHE['network_cache_time'] > 
                CACHE_TTL['network']):
                
                interfaces = {}
                errors = []
                
                try:
                    addrs = psutil.net_if_addrs()
                except Exception as e:
                    logger.error(f"Failed to get interface addresses: {e}")
                    errors.append(f"Address error: {e}")
                    addrs = {}
                
                try:
                    stats = psutil.net_if_stats()
                except Exception as e:
                    logger.error(f"Failed to get interface statistics: {e}")
                    errors.append(f"Stats error: {e}")
                    stats = {}
                
                # Process each interface
                for name, addrs_list in addrs.items():
                    try:
                        # Skip interfaces with no stats
                        if name not in stats:
                            continue
                        
                        stat = stats[name]
                        if not stat.isup:
                            continue
                        
                        # Process addresses
                        ipv4_addrs = []
                        ipv6_addrs = []
                        
                        for addr in addrs_list:
                            try:
                                if addr.family == socket.AF_INET:
                                    ipv4_addrs.append(addr.address)
                                elif addr.family == socket.AF_INET6:
                                    ipv6_addrs.append(addr.address)
                            except Exception as e:
                                logger.error(f"Error processing address {addr}: {e}")
                                continue
                        
                        # Skip interfaces with no IP addresses
                        if not (ipv4_addrs or ipv6_addrs):
                            continue
                        
                        # Store interface information
                        interfaces[name] = {
                            'ipv4': ipv4_addrs,
                            'ipv6': ipv6_addrs,
                            'speed': stat.speed or 0,
                            'mtu': stat.mtu,
                            'duplex': getattr(stat, 'duplex', 'unknown'),
                            'is_up': stat.isup,
                            'errors': errors
                        }
                        
                    except Exception as e:
                        logger.error(f"Error processing interface {name}: {e}")
                        continue
                
                # Update cache
                SYSTEM_INFO_CACHE['network_interfaces'] = interfaces
                SYSTEM_INFO_CACHE['network_cache_time'] = current_time
                
            return SYSTEM_INFO_CACHE['network_interfaces']
            
        except Exception as e:
            logger.error(f"Critical error getting interface info: {e}", exc_info=True)
            return {}

    def _get_network_metrics(self) -> Dict[str, Any]:
        """
        Get current network metrics with rate calculations
        
        Returns:
            Dictionary containing network metrics and rates
        """
        try:
            now = time.time()
            curr_io = self._safe_get_counters()
            
            if curr_io is None or self.last_io is None:
                return {
                    'error': "Failed to get network metrics",
                    'rates': {},
                    'totals': {}
                }
            
            dt = now - self.last_time
            
            metrics = {
                'bytes_sent': curr_io.bytes_sent,
                'bytes_recv': curr_io.bytes_recv,
                'packets_sent': curr_io.packets_sent,
                'packets_recv': curr_io.packets_recv,
                'error_in': curr_io.errin,
                'error_out': curr_io.errout,
                'drop_in': curr_io.dropin,
                'drop_out': curr_io.dropout,
                'rates': {},
                'errors': []
            }
            
            # Calculate rates if time difference is valid
            if dt > 0:
                try:
                    # Calculate all rates
                    for key in ['bytes_sent', 'bytes_recv', 
                              'packets_sent', 'packets_recv']:
                        curr_val = getattr(curr_io, key)
                        prev_val = getattr(self.last_io, key)
                        rate = (curr_val - prev_val) / dt
                        metrics['rates'][key] = rate
                        
                        # Update history
                        if key in self.history:
                            self.history[key].append(rate)
                    
                    # Calculate error and drop rates
                    for key in ['error_in', 'error_out', 'drop_in', 'drop_out']:
                        curr_val = getattr(curr_io, key)
                        prev_val = getattr(self.last_io, key)
                        change = curr_val - prev_val
                        if change > 0:
                            self.history[key].append(change)
                            metrics['rates'][key] = change / dt
                        else:
                            metrics['rates'][key] = 0
                            
                except Exception as e:
                    logger.error(f"Error calculating network rates: {e}")
                    metrics['errors'].append(f"Rate calculation error: {e}")
            
            # Update tracking variables
            self.last_io = curr_io
            self.last_time = now
            
            return metrics
            
        except Exception as e:
            logger.error(f"Critical error in network metrics: {e}", exc_info=True)
            return {
                'error': str(e),
                'rates': {},
                'totals': {}
            }
        


class DiskMonitor(BaseMonitor):
    """
    Disk monitoring with detailed I/O statistics and partition information.
    
    Features:
    - Disk I/O monitoring
    - Partition usage tracking
    - Disk scheduler information
    - SSD/HDD detection
    """
    
    def __init__(self):
        try:
            super().__init__()
            # Initialize disk tracking
            self.last_io = self._safe_get_io_counters()
            self.last_time = time.time()
            
            # Performance history tracking
            self.history = {
                'read_bytes': deque(maxlen=60),    # 1 minute of I/O history
                'write_bytes': deque(maxlen=60),
                'read_count': deque(maxlen=60),
                'write_count': deque(maxlen=60),
                'busy_time': deque(maxlen=60)
            }
            
            # Performance thresholds (bytes per second)
            self.thresholds = {
                'read_warning': 50 * 1024 * 1024,    # 50 MB/s
                'read_critical': 100 * 1024 * 1024,  # 100 MB/s
                'write_warning': 50 * 1024 * 1024,   # 50 MB/s
                'write_critical': 100 * 1024 * 1024, # 100 MB/s
                'busy_warning': 80.0,                # 80% busy
                'busy_critical': 95.0                # 95% busy
            }
            
            logger.debug("DiskMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize DiskMonitor: {e}", exc_info=True)
            raise

    def _safe_get_io_counters(self) -> Optional[psutil._common.sdiskio]:
        """
        Safely get disk I/O counters with error handling
        
        Returns:
            Disk I/O counters or None on error
        """
        try:
            return psutil.disk_io_counters()
        except Exception as e:
            logger.error(f"Error getting disk I/O counters: {e}", exc_info=True)
            return None

    def _get_disk_type(self, device_path: str) -> Dict[str, Any]:
        """
        Determine disk type (SSD/HDD) and get additional disk information
        
        Args:
            device_path: Path to disk device
            
        Returns:
            Dictionary containing disk type and additional information
        """
        info = {
            'type': 'unknown',
            'scheduler': 'unknown',
            'errors': []
        }
        
        try:
            # Extract device name
            device_name = os.path.basename(device_path)
            sys_block_path = f"/sys/block/{device_name}"
            
            if not os.path.exists(sys_block_path):
                info['errors'].append(f"Device path not found: {sys_block_path}")
                return info
            
            # Check rotational status (0 = SSD, 1 = HDD)
            try:
                rotational_path = f"{sys_block_path}/queue/rotational"
                if os.path.exists(rotational_path):
                    with open(rotational_path) as f:
                        is_rotational = int(f.read().strip())
                        info['type'] = 'HDD' if is_rotational else 'SSD'
            except Exception as e:
                info['errors'].append(f"Rotational check error: {e}")
            
            # Get scheduler information
            try:
                scheduler_path = f"{sys_block_path}/queue/scheduler"
                if os.path.exists(scheduler_path):
                    with open(scheduler_path) as f:
                        schedulers = f.read().strip()
                        # Extract active scheduler (enclosed in [])
                        active = re.search(r'\[(.*?)\]', schedulers)
                        if active:
                            info['scheduler'] = active.group(1)
                        else:
                            info['scheduler'] = schedulers
            except Exception as e:
                info['errors'].append(f"Scheduler check error: {e}")
            
            # Get additional disk information if available
            try:
                # Queue depth
                queue_depth_path = f"{sys_block_path}/queue/nr_requests"
                if os.path.exists(queue_depth_path):
                    with open(queue_depth_path) as f:
                        info['queue_depth'] = int(f.read().strip())
                
                # Read-ahead value
                readahead_path = f"{sys_block_path}/queue/read_ahead_kb"
                if os.path.exists(readahead_path):
                    with open(readahead_path) as f:
                        info['read_ahead_kb'] = int(f.read().strip())
            except Exception as e:
                info['errors'].append(f"Additional info error: {e}")
            
            return info
            
        except Exception as e:
            logger.error(f"Error getting disk type for {device_path}: {e}", 
                        exc_info=True)
            info['errors'].append(f"Critical error: {e}")
            return info

    def _get_partitions(self) -> List[Dict[str, Any]]:
        """
        Get cached partition information with detailed error handling
        
        Returns:
            List of dictionaries containing partition information
        """
        try:
            current_time = time.time()
            
            if (SYSTEM_INFO_CACHE['partition_info'] is None or 
                current_time - SYSTEM_INFO_CACHE['partition_cache_time'] > 
                CACHE_TTL['partitions']):
                
                partitions = []
                
                # Get all partitions
                for part in psutil.disk_partitions(all=False):
                    try:
                        # Skip certain filesystem types
                        if part.fstype in {'squashfs', 'efivarfs'} or \
                           '/boot' in part.mountpoint or \
                           '/snap' in part.mountpoint:
                            continue
                        
                        # Get usage information
                        try:
                            usage = psutil.disk_usage(part.mountpoint)
                        except PermissionError:
                            logger.warning(
                                f"Permission denied reading {part.mountpoint}")
                            continue
                        except Exception as e:
                            logger.error(
                                f"Error getting usage for {part.mountpoint}: {e}")
                            continue
                        
                        # Get disk type information
                        disk_info = self._get_disk_type(part.device)
                        
                        partition = {
                            'device': part.device,
                            'mountpoint': part.mountpoint,
                            'fstype': part.fstype,
                            'opts': part.opts,
                            'total': usage.total,
                            'used': usage.used,
                            'free': usage.free,
                            'percent': usage.percent,
                            'type': disk_info['type'],
                            'scheduler': disk_info['scheduler']
                        }
                        
                        # Add additional disk info if available
                        for key in ['queue_depth', 'read_ahead_kb']:
                            if key in disk_info:
                                partition[key] = disk_info[key]
                        
                        partitions.append(partition)
                        
                    except Exception as e:
                        logger.error(
                            f"Error processing partition {part.device}: {e}")
                        continue
                
                # Update cache
                SYSTEM_INFO_CACHE['partition_info'] = partitions
                SYSTEM_INFO_CACHE['partition_cache_time'] = current_time
            
            return SYSTEM_INFO_CACHE['partition_info']
            
        except Exception as e:
            logger.error(f"Critical error getting partitions: {e}", exc_info=True)
            return []

    def _get_disk_io(self) -> Dict[str, Any]:
        """
        Get disk I/O metrics with rate calculations
        
        Returns:
            Dictionary containing disk metrics and rates
        """
        try:
            now = time.time()
            curr_io = self._safe_get_io_counters()
            
            if curr_io is None or self.last_io is None:
                return {
                    'error': "Failed to get disk I/O metrics",
                    'rates': {}
                }
            
            dt = now - self.last_time
            
            metrics = {
                'read_bytes': curr_io.read_bytes,
                'write_bytes': curr_io.write_bytes,
                'read_count': curr_io.read_count,
                'write_count': curr_io.write_count,
                'read_time': curr_io.read_time,
                'write_time': curr_io.write_time,
                'busy_time': getattr(curr_io, 'busy_time', 0),
                'rates': {},
                'errors': []
            }



class DiskMonitor(BaseMonitor):  # Continuing from previous implementation
    def _calculate_io_rates(self, curr_io: psutil._common.sdiskio, dt: float) -> Dict[str, Any]:
        """
        Calculate I/O rates with comprehensive error checking
        
        Args:
            curr_io: Current I/O counters
            dt: Time delta in seconds
            
        Returns:
            Dictionary containing calculated rates
        """
        try:
            rates = {}
            errors = []
            
            # Calculate basic I/O rates
            for key in ['read_bytes', 'write_bytes', 'read_count', 'write_count']:
                try:
                    curr_val = getattr(curr_io, key)
                    prev_val = getattr(self.last_io, key)
                    rate = (curr_val - prev_val) / dt if dt > 0 else 0
                    rates[key] = max(0, rate)  # Ensure non-negative
                    
                    # Update history
                    if key in self.history:
                        self.history[key].append(rates[key])
                except Exception as e:
                    error_msg = f"Error calculating {key} rate: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    rates[key] = 0
            
            # Calculate busy percentage if available
            if hasattr(curr_io, 'busy_time'):
                try:
                    busy_time = curr_io.busy_time - self.last_io.busy_time
                    busy_percent = min(100.0, busy_time / (dt * 1000) * 100) if dt > 0 else 0
                    rates['busy_percent'] = max(0, busy_percent)
                    self.history['busy_time'].append(rates['busy_percent'])
                except Exception as e:
                    error_msg = f"Error calculating busy time: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    rates['busy_percent'] = 0
            
            return {'rates': rates, 'errors': errors}
            
        except Exception as e:
            logger.error(f"Critical error calculating I/O rates: {e}", exc_info=True)
            return {'rates': {}, 'errors': [str(e)]}

    def _get_performance_color(self, value: float, metric_type: str) -> str:
        """
        Determine color based on disk performance metrics
        
        Args:
            value: Performance value
            metric_type: Type of metric (read, write, busy)
            
        Returns:
            String containing appropriate color name
        """
        try:
            if not isinstance(value, (int, float)):
                return "yellow"
                
            if metric_type.startswith('read'):
                warning = self.thresholds['read_warning']
                critical = self.thresholds['read_critical']
            elif metric_type.startswith('write'):
                warning = self.thresholds['write_warning']
                critical = self.thresholds['write_critical']
            elif metric_type == 'busy':
                warning = self.thresholds['busy_warning']
                critical = self.thresholds['busy_critical']
            else:
                return "yellow"
            
            if value < warning:
                return "green"
            elif value < critical:
                return "yellow"
            return "red"
            
        except Exception as e:
            logger.error(f"Error determining performance color: {e}", exc_info=True)
            return "yellow"

    def render(self) -> Panel:
        """
        Render disk monitor display with comprehensive error handling
        
        Returns:
            Rich Panel containing disk information
        """
        try:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Disk", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get disk I/O metrics
            curr_io = self._safe_get_io_counters()
            if curr_io is None:
                return Panel(
                    Text("Failed to get disk I/O metrics", "red"),
                    title="Disk Monitor",
                    border_style="red"
                )
            
            now = time.time()
            dt = now - self.last_time
            
            # Calculate rates
            rate_info = self._calculate_io_rates(curr_io, dt)
            rates = rate_info['rates']
            
            if rate_info['errors']:
                logger.warning("Rate calculation errors: " + 
                             "; ".join(rate_info['errors']))
            
            # Display I/O rates
            try:
                # Read/Write speeds
                read_color = self._get_performance_color(
                    rates.get('read_bytes', 0), 'read')
                write_color = self._get_performance_color(
                    rates.get('write_bytes', 0), 'write')
                
                table.add_row(
                    "Disk I/O",
                    Text(f"Read: {format_bytes(rates.get('read_bytes', 0))}/s", 
                         style=read_color),
                    Text(f"Write: {format_bytes(rates.get('write_bytes', 0))}/s", 
                         style=write_color)
                )
                
                # Operations per second
                table.add_row(
                    "Operations",
                    f"Read: {rates.get('read_count', 0):.1f}/s",
                    f"Write: {rates.get('write_count', 0):.1f}/s"
                )
                
                # Busy time if available
                if 'busy_percent' in rates:
                    busy_color = self._get_performance_color(
                        rates['busy_percent'], 'busy')
                    table.add_row(
                        "Busy",
                        create_progress_bar(rates['busy_percent'], 
                                          color=busy_color),
                        f"{rates['busy_percent']:.1f}% Utilized"
                    )
            except Exception as e:
                logger.error(f"Error displaying I/O rates: {e}")
                table.add_row("I/O", "Error displaying I/O rates", "")
            
            # Display partitions
            try:
                partitions = self._get_partitions()
                if partitions:
                    table.add_row("", "", "")
                    table.add_row("Partitions", "", "")
                    
                    for part in partitions:
                        try:
                            # Create usage bar
                            usage_bar = create_progress_bar(part['percent'])
                            
                            # Format details
                            details = [
                                f"{format_bytes(part['used'])} / "
                                f"{format_bytes(part['total'])}",
                                f"({part['fstype']})",
                                part['type']
                            ]
                            
                            if part['scheduler'] != 'unknown':
                                details.append(f"Scheduler: {part['scheduler']}")
                            
                            # Add partition row
                            name = os.path.basename(part['mountpoint']) or part['mountpoint']
                            table.add_row(
                                name[:12],
                                usage_bar,
                                " | ".join(details)
                            )
                            
                            # Add additional info if available
                            extra_info = []
                            if 'queue_depth' in part:
                                extra_info.append(f"Queue: {part['queue_depth']}")
                            if 'read_ahead_kb' in part:
                                extra_info.append(
                                    f"Read-ahead: {part['read_ahead_kb']}KB")
                            
                            if extra_info:
                                table.add_row(
                                    "",
                                    "",
                                    " | ".join(extra_info)
                                )
                                
                        except Exception as e:
                            logger.error(
                                f"Error displaying partition {part.get('device')}: {e}")
                            table.add_row(
                                part.get('device', 'Unknown'),
                                "Error displaying partition",
                                ""
                            )
            except Exception as e:
                logger.error(f"Error displaying partitions: {e}")
                table.add_row("Partitions", "Error displaying partitions", "")
            
            # Update tracking variables
            self.last_io = curr_io
            self.last_time = now
            
            return Panel(table, title="Disk Monitor", border_style="magenta")
            
        except Exception as e:
            logger.error(f"Critical error in disk monitor render: {e}", 
                        exc_info=True)
            return Panel(
                Text("Disk Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        


class GPUMonitor(BaseMonitor):
    """
    GPU monitoring with comprehensive NVIDIA metrics and error handling.
    
    Features:
    - GPU usage tracking
    - Memory monitoring
    - Temperature monitoring
    - Power usage tracking
    - Clock speed monitoring
    - PCIe status
    """
    
    def __init__(self):
        try:
            super().__init__()
            
            # Initialize performance tracking with safe defaults
            self.history = {
                'usage': deque(maxlen=60),      # 1 minute of history
                'temp': deque(maxlen=60),
                'memory': deque(maxlen=60),
                'power': deque(maxlen=60),
                'errors': deque(maxlen=10)      # Track recent errors
            }
            
            # Performance thresholds
            self.thresholds = {
                'temp_warning': 70,      # GPU temperature warning threshold
                'temp_critical': 85,     # GPU temperature critical threshold
                'usage_high': 90,        # GPU usage high threshold
                'memory_warning': 80,    # Memory usage warning threshold
                'memory_critical': 95,   # Memory usage critical threshold
                'power_warning': 90      # Power usage warning percent
            }
            
            # Initialize last query time
            self.last_query_time = 0
            self.query_interval = 1.0    # Minimum time between queries
            
            # Verify NVIDIA GPU presence
            if not self._check_gpu_available():
                raise RuntimeError("No NVIDIA GPU detected")
                
            logger.debug("GPUMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize GPUMonitor: {e}", exc_info=True)
            raise

    def _check_gpu_available(self) -> bool:
        """
        Check for NVIDIA GPU availability using multiple detection methods
        
        Returns:
            Boolean indicating if NVIDIA GPU is available
        """
        try:
            # Method 1: Check nvidia-smi
            try:
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    logger.debug("GPU detected via nvidia-smi")
                    return True
            except Exception as e:
                logger.debug(f"nvidia-smi check failed: {e}")
            
            # Method 2: Check common NVIDIA paths
            nvidia_paths = [
                '/proc/driver/nvidia/version',
                '/dev/nvidia0',
                '/dev/nvidiactl'
            ]
            for path in nvidia_paths:
                if os.path.exists(path):
                    logger.debug(f"GPU detected via path: {path}")
                    return True
            
            # Method 3: Check device vendor ID
            try:
                for i in range(5):  # Check first 5 possible GPUs
                    vendor_path = f'/sys/class/drm/card{i}/device/vendor'
                    if os.path.exists(vendor_path):
                        with open(vendor_path) as f:
                            vendor = f.read().strip()
                            # NVIDIA vendor ID: 0x10de
                            if vendor in ['0x10de', '10de']:
                                logger.debug(f"GPU detected via vendor ID: {vendor}")
                                return True
            except Exception as e:
                logger.debug(f"Vendor ID check failed: {e}")
            
            logger.warning("No NVIDIA GPU detected")
            return False
            
        except Exception as e:
            logger.error(f"Error checking GPU availability: {e}", exc_info=True)
            return False

    def _get_gpu_metrics(self) -> Dict[str, Any]:
        """
        Get comprehensive GPU metrics using nvidia-smi
        
        Returns:
            Dictionary containing GPU metrics and status
        """
        try:
            # Respect query interval to prevent excessive calls
            current_time = time.time()
            if current_time - self.last_query_time < self.query_interval:
                logger.debug("Skipping GPU query - too soon")
                return {}
            
            # Build nvidia-smi query
            query_params = [
                'name',                    # GPU name
                'utilization.gpu',         # GPU usage
                'temperature.gpu',         # GPU temperature
                'memory.used',             # Memory used
                'memory.total',            # Total memory
                'power.draw',              # Power consumption
                'clocks.current.graphics', # GPU clock
                'clocks.current.memory',   # Memory clock
                'pcie.link.gen.current',   # PCIe generation
                'pcie.link.width.current', # PCIe width
                'compute_mode',            # Compute mode
                'pstate',                  # Performance state
                'fan.speed',               # Fan speed
                'temperature.memory',      # Memory temperature
                'voltage.graphics'         # GPU voltage
            ]
            
            cmd = [
                'nvidia-smi',
                f"--query-gpu={','.join(query_params)}",
                '--format=csv,noheader,nounits'
            ]
            
            # Execute nvidia-smi command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode != 0:
                raise RuntimeError(
                    f"nvidia-smi failed with code {result.returncode}: "
                    f"{result.stderr}")
            
            # Parse results
            values = result.stdout.strip().split(',')
            metrics = {}
            
            # Process each metric with error handling
            try:
                metrics['name'] = values[0].strip()
                metrics['usage'] = float(values[1]) if values[1] != '[N/A]' else 0
                metrics['temperature'] = float(values[2]) if values[2] != '[N/A]' else 0
                metrics['memory_used'] = float(values[3]) if values[3] != '[N/A]' else 0
                metrics['memory_total'] = float(values[4]) if values[4] != '[N/A]' else 1
                metrics['power_draw'] = float(values[5]) if values[5] != '[N/A]' else 0
                metrics['clock_gpu'] = float(values[6]) if values[6] != '[N/A]' else 0
                metrics['clock_mem'] = float(values[7]) if values[7] != '[N/A]' else 0
                metrics['pcie_gen'] = values[8].strip() if values[8] != '[N/A]' else 'Unknown'
                metrics['pcie_width'] = values[9].strip() if values[9] != '[N/A]' else 'Unknown'
                metrics['compute_mode'] = values[10].strip() if values[10] != '[N/A]' else 'Unknown'
                metrics['pstate'] = values[11].strip() if values[11] != '[N/A]' else 'Unknown'
                metrics['fan_speed'] = float(values[12]) if values[12] != '[N/A]' else 0
                metrics['memory_temp'] = float(values[13]) if values[13] != '[N/A]' else 0
                metrics['voltage'] = float(values[14]) if values[14] != '[N/A]' else 0
                
                # Calculate derived metrics
                metrics['memory_percent'] = (metrics['memory_used'] / 
                                           metrics['memory_total'] * 100)
                
                # Update history
                self.history['usage'].append(metrics['usage'])
                self.history['temp'].append(metrics['temperature'])
                self.history['memory'].append(metrics['memory_percent'])
                self.history['power'].append(metrics['power_draw'])
                
            except Exception as e:
                logger.error(f"Error parsing GPU metrics: {e}")
                self.history['errors'].append(str(e))
                return {}
            
            # Update query time
            self.last_query_time = current_time
            
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to get GPU metrics: {e}", exc_info=True)
            self.history['errors'].append(str(e))
            return {}

    def _get_metric_color(self, value: float, metric_type: str) -> str:
        """
        Determine color based on GPU metric values
        
        Args:
            value: Metric value
            metric_type: Type of metric (temp, usage, memory, power)
            
        Returns:
            String containing appropriate color name
        """
        try:
            if not isinstance(value, (int, float)):
                return "yellow"
                
            if metric_type == 'temp':
                if value < self.thresholds['temp_warning']:
                    return "green"
                elif value < self.thresholds['temp_critical']:
                    return "yellow"
                return "red"
                
            elif metric_type == 'usage':
                if value < 50:
                    return "green"
                elif value < self.thresholds['usage_high']:
                    return "yellow"
                return "red"
                
            elif metric_type == 'memory':
                if value < self.thresholds['memory_warning']:
                    return "green"
                elif value < self.thresholds['memory_critical']:
                    return "yellow"
                return "red"
                
            elif metric_type == 'power':
                if value < self.thresholds['power_warning']:
                    return "green"
                return "yellow"
                
            return "yellow"
            
        except Exception as e:
            logger.error(f"Error determining metric color: {e}", exc_info=True)
            return "yellow"
        


class GPUMonitor(BaseMonitor):  # Continuing from previous implementation
    def create_gpu_bar(self, percentage: float, width: int = 35) -> Text:
        """
        Create a colored progress bar for GPU metrics
        
        Args:
            percentage: Value between 0-100
            width: Width of the progress bar in characters
            
        Returns:
            Rich Text object containing formatted progress bar
        """
        try:
            # Validate input
            if not isinstance(percentage, (int, float)):
                logger.error(f"Invalid percentage type: {type(percentage)}")
                return Text("Error", "red")
                
            # Clamp percentage to valid range
            percentage = max(0, min(100, float(percentage)))
            
            # Calculate bar segments
            filled = int((width * percentage) / 100)
            remainder = width - filled
            
            # Create bar with appropriate color based on usage
            try:
                if percentage < 50:
                    color = "green"
                elif percentage < 80:
                    color = "yellow"
                else:
                    color = "red"
                    
                return Text("■" * filled + "·" * remainder, color)
            except Exception as e:
                logger.error(f"Error creating bar segments: {e}")
                return Text("Error", "red")
                
        except Exception as e:
            logger.error(f"Error creating GPU bar: {e}", exc_info=True)
            return Text("Error", "red")

    def format_clock_speed(self, speed: float, clock_type: str) -> Text:
        """
        Format GPU clock speeds with appropriate styling
        
        Args:
            speed: Clock speed in MHz
            clock_type: Type of clock (gpu/memory)
            
        Returns:
            Rich Text object with formatted clock speed
        """
        try:
            # Define base/boost clocks for comparison
            base_clocks = {
                'gpu': 1500,    # Typical base GPU clock
                'memory': 7000  # Typical base memory clock
            }
            
            # Determine color based on clock speed
            if speed <= 0:
                return Text("N/A", "bright_black")
            elif speed < base_clocks.get(clock_type, 0) * 0.8:
                color = "yellow"  # Underclocked
            elif speed > base_clocks.get(clock_type, 0) * 1.2:
                color = "green"   # Overclocked
            else:
                color = "white"   # Normal range
                
            return Text(f"{speed:.0f}MHz", color)
            
        except Exception as e:
            logger.error(f"Error formatting clock speed: {e}", exc_info=True)
            return Text("Error", "red")

    def render(self) -> Panel:
        """
        Render GPU monitor display with comprehensive error handling
        
        Returns:
            Rich Panel containing GPU information
        """
        try:
            # Create main table
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Label", style="cyan", width=12)
            table.add_column("Value", ratio=1)
            table.add_column("Details", justify="right", style="bright_magenta")
            
            # Get GPU metrics
            metrics = self._get_gpu_metrics()
            
            if not metrics:
                # Check if it's due to recent errors
                error_msg = (self.history['errors'][-1] 
                           if self.history['errors'] else "Unknown error")
                return Panel(
                    Text(f"GPU Monitor Error: {error_msg}", "red"),
                    border_style="red"
                )
            
            try:
                # GPU Name and Driver Info
                table.add_row(
                    Text("Name", style="cyan"),
                    Text(metrics['name']),
                    Text(f"P-State: {metrics['pstate']}", style="bright_magenta")
                )
                
                # Compute mode and additional info
                table.add_row(
                    "",
                    Text(f"Mode: {metrics['compute_mode']}"),
                    Text(f"Voltage: {metrics['voltage']:.2f}V", 
                         style="bright_magenta")
                )
                
                # GPU Usage
                usage_color = self._get_metric_color(metrics['usage'], 'usage')
                table.add_row(
                    Text("Usage", style="cyan"),
                    self.create_gpu_bar(metrics['usage']),
                    Text(f"{metrics['usage']:.1f}% | "
                         f"{metrics['power_draw']:.1f}W", 
                         style=usage_color)
                )
                
                # Temperature with combined GPU/Memory temps
                temp_color = self._get_metric_color(
                    metrics['temperature'], 'temp')
                table.add_row(
                    Text("Temperature", style="cyan"),
                    self.create_gpu_bar(
                        metrics['temperature'] * 100 / self.thresholds['temp_critical']
                    ),
                    Text(f"GPU: {metrics['temperature']:.1f}°C | "
                         f"Mem: {metrics['memory_temp']:.1f}°C | "
                         f"Fan: {metrics['fan_speed']:.0f}%", 
                         style=temp_color)
                )
                
                # Memory Usage
                memory_color = self._get_metric_color(
                    metrics['memory_percent'], 'memory')
                table.add_row(
                    Text("Memory", style="cyan"),
                    self.create_gpu_bar(metrics['memory_percent']),
                    Text(f"{metrics['memory_used']:.0f}MB / "
                         f"{metrics['memory_total']:.0f}MB", 
                         style=memory_color)
                )
                
                # Clock Speeds
                table.add_row(
                    Text("Core Clock", style="cyan"),
                    self.format_clock_speed(metrics['clock_gpu'], 'gpu'),
                    ""
                )
                
                table.add_row(
                    Text("Mem Clock", style="cyan"),
                    self.format_clock_speed(metrics['clock_mem'], 'memory'),
                    ""
                )
                
                # PCIe Status
                table.add_row(
                    Text("PCIe", style="cyan"),
                    Text(f"Gen {metrics['pcie_gen']} x{metrics['pcie_width']}"),
                    ""
                )
                
                # Performance History
                if len(self.history['usage']) > 1:
                    usage_trend = (sum(self.history['usage'][-5:]) / 5 
                                 if len(self.history['usage']) >= 5 else 
                                 self.history['usage'][-1])
                    temp_trend = (sum(self.history['temp'][-5:]) / 5 
                                if len(self.history['temp']) >= 5 else 
                                self.history['temp'][-1])
                    
                    table.add_row(
                        Text("Trends", style="cyan"),
                        Text(f"Usage: {usage_trend:.1f}% | "
                             f"Temp: {temp_trend:.1f}°C"),
                        ""
                    )
                
            except Exception as e:
                logger.error(f"Error creating GPU monitor rows: {e}")
                table.add_row(
                    "Error",
                    f"Failed to display GPU information: {e}",
                    ""
                )

            return Panel(
                table,
                title="GPU Monitor",
                border_style="yellow"
            )
            
        except Exception as e:
            logger.error(f"Critical error in GPU monitor render: {e}", 
                        exc_info=True)
            return Panel(
                Text("GPU Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        


class ServiceMonitor(BaseMonitor):
    """
    Service monitoring for Linux systems using systemd.
    
    Features:
    - Service status tracking
    - Automatic service discovery
    - Status caching
    - Detailed error reporting
    """
    
    def __init__(self):
        try:
            super().__init__()
            
            # Core system services to monitor
            self.important_services = [
                # System logging services
                'systemd-journald',   # System logging daemon
                'systemd-logind',     # User login management
                'rsyslog',           # System logging
                
                # Core system services
                'systemd-timesyncd',  # Time synchronization
                'systemd-resolved',   # DNS resolution
                'systemd-networkd',   # Network management
                'dbus',              # System message bus
                
                # Network services
                'NetworkManager',     # Network configuration
                'sshd',              # SSH server
                'firewalld',         # Firewall
                'nginx',             # Web server
                'apache2',           # Web server
                
                # System management
                'cron',              # Task scheduler
                'atd',               # Scheduled task execution
                'acpid',             # Power management
                'irqbalance',        # IRQ balancing
                
                # Hardware management
                'udev',              # Device management
                'bluetooth',         # Bluetooth support
                'cups',              # Printing system
                'ModemManager',      # Modem management
                
                # Database services
                'mysql',             # MySQL database
                'postgresql',        # PostgreSQL database
                'mongodb',           # MongoDB database
                
                # Other important services
                'smartd',            # SMART disk monitoring
                'thermald',          # Thermal management
                'power-profiles-daemon'  # Power profiles
            ]
            
            # Service status cache
            self.status_cache = {}
            self.cache_time = 0
            self.cache_ttl = 5  # Cache TTL in seconds
            
            # Error tracking
            self.error_history = deque(maxlen=10)
            
            logger.debug("ServiceMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize ServiceMonitor: {e}", 
                          exc_info=True)
            raise

    def _get_systemctl_output(self, cmd: List[str]) -> Optional[str]:
        """
        Safely execute systemctl commands with timeout and error handling
        
        Args:
            cmd: List of command components
            
        Returns:
            Command output or None on error
        """
        try:
            # Add systemctl base command
            full_cmd = ['systemctl'] + cmd
            
            # Execute command with timeout
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=2  # 2 second timeout
            )
            
            # Check for errors
            if result.returncode != 0:
                logger.error(
                    f"systemctl command failed: {result.stderr.strip()}")
                return None
                
            return result.stdout.strip()
            
        except subprocess.TimeoutExpired:
            logger.error(f"systemctl command timed out: {' '.join(cmd)}")
            return None
        except Exception as e:
            logger.error(f"Error executing systemctl command: {e}", 
                        exc_info=True)
            return None

    def _parse_service_properties(self, properties: str) -> Dict[str, str]:
        """
        Parse systemctl show output into a dictionary
        
        Args:
            properties: String output from systemctl show
            
        Returns:
            Dictionary of service properties
        """
        try:
            result = {}
            for line in properties.split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    result[key.strip()] = value.strip()
            return result
        except Exception as e:
            logger.error(f"Error parsing service properties: {e}", 
                        exc_info=True)
            return {}

    def _get_service_status(self, service: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed status information for a single service
        
        Args:
            service: Service name
            
        Returns:
            Dictionary containing service status information
        """
        try:
            # Get service properties
            properties = self._get_systemctl_output([
                'show',
                f'{service}.service',
                '--property=ActiveState,SubState,LoadState,UnitFileState,'
                'Description,StateChangeTimestamp,ExecMainStatus,'
                'StatusText,MainPID'
            ])
            
            if not properties:
                return None
                
            # Parse properties
            info = self._parse_service_properties(properties)
            
            # Get process information if service is running
            pid = info.get('MainPID', '0')
            if pid.isdigit() and int(pid) > 0:
                try:
                    process = psutil.Process(int(pid))
                    info['cpu_percent'] = process.cpu_percent()
                    info['memory_percent'] = process.memory_percent()
                    info['create_time'] = datetime.fromtimestamp(
                        process.create_time()).isoformat()
                except psutil.NoSuchProcess:
                    logger.debug(f"Process {pid} no longer exists")
                except Exception as e:
                    logger.error(f"Error getting process info: {e}")
            
            # Format status information
            status = {
                'name': service,
                'state': info.get('ActiveState', 'unknown'),
                'substate': info.get('SubState', 'unknown'),
                'enabled': info.get('UnitFileState', '') == 'enabled',
                'description': info.get('Description', ''),
                'status_code': int(info.get('ExecMainStatus', 0)),
                'status_text': info.get('StatusText', ''),
                'pid': int(pid) if pid.isdigit() else None,
                'cpu_percent': info.get('cpu_percent'),
                'memory_percent': info.get('memory_percent'),
                'start_time': info.get('create_time'),
                'last_changed': info.get('StateChangeTimestamp', '')
            }
            
            return status
            
        except Exception as e:
            logger.error(
                f"Error getting status for service {service}: {e}", 
                exc_info=True)
            self.error_history.append(f"Status error ({service}): {str(e)}")
            return None

    def _get_all_services(self) -> Dict[str, Any]:
        """
        Get status of all monitored services with caching
        
        Returns:
            Dictionary containing service status and statistics
        """
        try:
            current_time = time.time()
            
            # Check cache validity
            if (current_time - self.cache_time < self.cache_ttl and 
                self.status_cache):
                return self.status_cache
            
            services = {}
            stats = {
                'total': len(self.important_services),
                'running': 0,
                'stopped': 0,
                'failed': 0,
                'other': 0,
                'enabled': 0,
                'disabled': 0
            }
            
            # Get status for each service
            for service in self.important_services:
                try:
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
                        else:
                            stats['other'] += 1
                            
                        if status['enabled']:
                            stats['enabled'] += 1
                        else:
                            stats['disabled'] += 1
                            
                except Exception as e:
                    logger.error(
                        f"Error processing service {service}: {e}")
                    continue
            
            # Update cache
            self.status_cache = {
                'services': services,
                'stats': stats,
                'timestamp': current_time
            }
            self.cache_time = current_time
            
            return self.status_cache
            
        except Exception as e:
            logger.error("Critical error getting service status: {e}", 
                        exc_info=True)
            return {
                'services': {},
                'stats': {},
                'timestamp': current_time,
                'error': str(e)
            }
        



class ServiceMonitor(BaseMonitor):  # Continuing from previous implementation
    def _get_service_color(self, status: Dict[str, Any]) -> str:
        """
        Determine appropriate color for service status display
        
        Args:
            status: Dictionary containing service status information
            
        Returns:
            String containing color name based on service state
        """
        try:
            state = status.get('state', '').lower()
            substate = status.get('substate', '').lower()
            
            # Critical states first
            if state == 'failed' or 'error' in substate:
                return "red"
            
            # Active states
            if state == 'active':
                if substate in ['running', 'listening', 'online']:
                    return "green"
                else:
                    return "yellow"  # Unusual active substates
                    
            # Inactive states
            if state == 'inactive':
                return "bright_black"  # Dimmed color for inactive
                
            # Other states
            if state in ['reloading', 'activating']:
                return "yellow"
                
            # Default/unknown states
            return "white"
            
        except Exception as e:
            logger.error(f"Error determining service color: {e}", exc_info=True)
            return "white"

    def _format_service_details(self, status: Dict[str, Any]) -> Text:
        """
        Format detailed service information with status indicators
        
        Args:
            status: Dictionary containing service status information
            
        Returns:
            Rich Text object containing formatted service details
        """
        try:
            details = []
            
            # Add basic state information
            details.append(status['substate'].capitalize())
            
            # Add enabled/disabled status
            details.append(
                "Enabled" if status['enabled'] else "Disabled"
            )
            
            # Add process information if available
            if status['pid'] is not None:
                if status.get('cpu_percent') is not None:
                    details.append(
                        f"CPU: {status['cpu_percent']:.1f}%"
                    )
                if status.get('memory_percent') is not None:
                    details.append(
                        f"MEM: {status['memory_percent']:.1f}%"
                    )
            
            # Add status text if available
            if status.get('status_text'):
                details.append(status['status_text'][:30])
                
            return Text(" | ".join(details))
            
        except Exception as e:
            logger.error(f"Error formatting service details: {e}", 
                        exc_info=True)
            return Text("Error displaying details", "red")

    def _create_status_indicator(self, status: Dict[str, Any]) -> Text:
        """
        Create visual status indicator for service state
        
        Args:
            status: Dictionary containing service status information
            
        Returns:
            Rich Text object containing status indicator
        """
        try:
            state = status.get('state', '').lower()
            color = self._get_service_color(status)
            
            # Create appropriate indicator based on state
            if state == 'active':
                indicator = '■' * 10  # Full block for active
            elif state == 'failed':
                indicator = '✗' * 10  # Cross for failed
            elif state in ['activating', 'deactivating', 'reloading']:
                indicator = '◯' * 10  # Circle for transitioning
            else:
                indicator = '·' * 10  # Dots for inactive
                
            return Text(indicator, color)
            
        except Exception as e:
            logger.error(f"Error creating status indicator: {e}", 
                        exc_info=True)
            return Text("ERROR", "red")

    def render(self) -> Panel:
        """
        Render service monitor display with comprehensive status information
        
        Returns:
            Rich Panel containing formatted service information
        """
        try:
            # Create main display table
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Service", style="cyan", width=12)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get current service information
            info = self._get_all_services()
            
            if 'error' in info:
                return Panel(
                    Text(f"Service Monitor Error: {info['error']}", "red"),
                    border_style="red"
                )
            
            stats = info.get('stats', {})
            services = info.get('services', {})
            
            # Show summary statistics
            try:
                total = stats.get('total', 0)
                running = stats.get('running', 0)
                failed = stats.get('failed', 0)
                
                summary_text = (
                    f"Running: {running}/{total} | "
                    f"Failed: {failed} | "
                    f"Enabled: {stats.get('enabled', 0)}"
                )
                
                table.add_row(
                    "Summary",
                    Text(summary_text),
                    ""
                )
                
                # Add separator
                table.add_row("", "", "")
                
            except Exception as e:
                logger.error(f"Error displaying summary: {e}")
                table.add_row("Summary", "Error displaying summary", "")
            
            # Sort and display services
            try:
                # Sort services by state priority
                sorted_services = sorted(
                    services.values(),
                    key=lambda x: (
                        x['state'] != 'failed',     # Failed first
                        x['state'] != 'active',     # Then active
                        x['state'] == 'inactive',   # Then inactive
                        x['name']                   # Then alphabetically
                    )
                )
                
                # Display each service
                for service in sorted_services:
                    try:
                        # Create status indicator
                        status_indicator = self._create_status_indicator(service)
                        
                        # Format service details
                        details = self._format_service_details(service)
                        
                        # Add service row
                        table.add_row(
                            service['name'][:12],
                            status_indicator,
                            details
                        )
                        
                        # Add description on next row if available
                        if service.get('description'):
                            table.add_row(
                                "",
                                Text(service['description'][:50], 
                                     style="bright_black"),
                                ""
                            )
                            
                    except Exception as e:
                        logger.error(
                            f"Error displaying service {service.get('name')}: {e}")
                        table.add_row(
                            service.get('name', 'Unknown'),
                            "Error displaying service",
                            ""
                        )
                        
            except Exception as e:
                logger.error(f"Error sorting/displaying services: {e}")
                table.add_row("Error", "Failed to display services", "")
            
            # Show recent errors if any
            if self.error_history:
                table.add_row("", "", "")
                table.add_row(
                    "Recent Errors",
                    Text(self.error_history[-1], "red"),
                    ""
                )

            return Panel(
                table,
                title="Service Monitor",
                border_style="blue"
            )
            
        except Exception as e:
            logger.error(f"Critical error in service monitor render: {e}", 
                        exc_info=True)
            return Panel(
                Text("Service Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        


class SensorMonitor(BaseMonitor):
    """
    System temperature and fan sensor monitoring.
    
    Features:
    - CPU temperature monitoring
    - Fan speed tracking
    - Power consumption monitoring
    - Voltage monitoring
    - Detailed sensor data caching
    """
    
    def __init__(self):
        try:
            super().__init__()
            
            # Temperature thresholds (in Celsius)
            self.temp_thresholds = {
                'cpu': {
                    'warning': 70,
                    'critical': 85
                },
                'gpu': {
                    'warning': 75,
                    'critical': 90
                },
                'system': {
                    'warning': 60,
                    'critical': 75
                }
            }
            
            # Fan speed thresholds (in RPM)
            self.fan_thresholds = {
                'minimum': 300,    # Minimum acceptable speed
                'warning': 500     # Warning threshold for low speed
            }
            
            # Initialize history tracking
            self.history = {
                'temperatures': {
                    'cpu': deque(maxlen=60),
                    'gpu': deque(maxlen=60),
                    'system': deque(maxlen=60)
                },
                'fans': deque(maxlen=60),
                'power': deque(maxlen=60),
                'errors': deque(maxlen=10)
            }
            
            # Sensor type classification patterns
            self.sensor_patterns = {
                'cpu_temp': ['cpu', 'core', 'tdie', 'package'],
                'gpu_temp': ['gpu', 'nvidia', 'amdgpu'],
                'system_temp': ['system', 'board', 'ambient'],
                'power': ['power', 'package'],
                'voltage': ['volt', 'vin']
            }
            
            logger.debug("SensorMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize SensorMonitor: {e}", 
                          exc_info=True)
            raise

    def _classify_sensor(self, name: str, type_hint: str = '') -> str:
        """
        Classify sensor based on name and type hint
        
        Args:
            name: Sensor name
            type_hint: Optional type hint
            
        Returns:
            String containing sensor classification
        """
        try:
            name = name.lower()
            type_hint = type_hint.lower()
            
            # Check each sensor pattern
            for sensor_type, patterns in self.sensor_patterns.items():
                for pattern in patterns:
                    if pattern in name or pattern in type_hint:
                        return sensor_type
                        
            return 'other'
            
        except Exception as e:
            logger.error(f"Error classifying sensor: {e}", exc_info=True)
            return 'unknown'

    def _get_hwmon_sensors(self) -> List[Dict[str, Any]]:
        """
        Read sensors from Linux hwmon interface
        
        Returns:
            List of dictionaries containing sensor information
        """
        sensors = []
        hwmon_path = '/sys/class/hwmon'
        
        try:
            if not os.path.exists(hwmon_path):
                logger.warning(f"hwmon path not found: {hwmon_path}")
                return sensors
                
            # Iterate through hwmon devices
            for device in os.listdir(hwmon_path):
                device_path = os.path.join(hwmon_path, device)
                
                try:
                    # Get device name
                    name_path = os.path.join(device_path, 'name')
                    if os.path.exists(name_path):
                        with open(name_path) as f:
                            device_name = f.read().strip()
                    else:
                        device_name = device
                        
                    # Process temperature inputs
                    for entry in os.listdir(device_path):
                        try:
                            if entry.startswith('temp') and entry.endswith('_input'):
                                base = entry[:-6]  # Remove '_input'
                                
                                # Get sensor label if available
                                label_path = os.path.join(device_path, f'{base}_label')
                                label = None
                                if os.path.exists(label_path):
                                    with open(label_path) as f:
                                        label = f.read().strip()
                                
                                # Get current value
                                input_path = os.path.join(device_path, entry)
                                with open(input_path) as f:
                                    value = float(f.read()) / 1000  # Convert to Celsius
                                    
                                # Get critical threshold if available
                                crit_path = os.path.join(device_path, f'{base}_crit')
                                crit_temp = None
                                if os.path.exists(crit_path):
                                    with open(crit_path) as f:
                                        crit_temp = float(f.read()) / 1000
                                
                                # Classify sensor
                                sensor_type = self._classify_sensor(
                                    label or device_name,
                                    'temp'
                                )
                                
                                sensors.append({
                                    'name': label or f"{device_name}_{base}",
                                    'type': sensor_type,
                                    'value': value,
                                    'critical': crit_temp,
                                    'device': device_name,
                                    'raw_name': entry
                                })
                                
                        except Exception as e:
                            logger.error(
                                f"Error processing sensor {entry} in {device}: {e}")
                            continue
                            
                except Exception as e:
                    logger.error(f"Error processing device {device}: {e}")
                    continue
                    
            return sensors
            
        except Exception as e:
            logger.error(f"Error reading hwmon sensors: {e}", exc_info=True)
            return []

    def _get_fan_sensors(self) -> List[Dict[str, Any]]:
        """
        Get fan speed information from system
        
        Returns:
            List of dictionaries containing fan information
        """
        fans = []
        
        try:
            # Try psutil fans first
            if hasattr(psutil, 'sensors_fans'):
                try:
                    psutil_fans = psutil.sensors_fans()
                    for device, entries in psutil_fans.items():
                        for fan in entries:
                            fans.append({
                                'name': fan.label or device,
                                'current': fan.current,
                                'min': getattr(fan, 'min', None),
                                'max': getattr(fan, 'max', None),
                                'source': 'psutil'
                            })
                except Exception as e:
                    logger.error(f"Error getting psutil fans: {e}")
            
            # Try hwmon fans
            hwmon_path = '/sys/class/hwmon'
            if os.path.exists(hwmon_path):
                for device in os.listdir(hwmon_path):
                    device_path = os.path.join(hwmon_path, device)
                    
                    try:
                        # Get device name
                        with open(os.path.join(device_path, 'name')) as f:
                            device_name = f.read().strip()
                            
                        # Look for fan inputs
                        for entry in os.listdir(device_path):
                            if entry.startswith('fan') and entry.endswith('_input'):
                                try:
                                    base = entry[:-6]
                                    
                                    # Get current speed
                                    with open(os.path.join(device_path, entry)) as f:
                                        speed = int(f.read().strip())
                                        
                                    # Get minimum speed if available
                                    min_speed = None
                                    min_path = os.path.join(device_path, f'{base}_min')
                                    if os.path.exists(min_path):
                                        with open(min_path) as f:
                                            min_speed = int(f.read().strip())
                                            
                                    fans.append({
                                        'name': f"{device_name}_{base}",
                                        'current': speed,
                                        'min': min_speed,
                                        'source': 'hwmon'
                                    })
                                    
                                except Exception as e:
                                    logger.error(
                                        f"Error processing fan {entry}: {e}")
                                    continue
                                    
                    except Exception as e:
                        logger.error(f"Error processing device {device}: {e}")
                        continue
                        
            return fans
            
        except Exception as e:
            logger.error(f"Error getting fan information: {e}", exc_info=True)
            return []
        



class SensorMonitor(BaseMonitor):  # Continuing from previous implementation
    def _format_temperature_row(self, sensor: Dict[str, Any]) -> Tuple[Text, Text, Text]:
        """
        Format a temperature sensor reading for display
        
        Args:
            sensor: Dictionary containing sensor information
            
        Returns:
            Tuple of (name, reading, details) Text objects
        """
        try:
            # Get status and color
            status, color = self._get_temp_status(
                sensor['value'], 
                sensor['type']
            )
            
            # Create progress bar based on critical threshold
            if sensor['critical']:
                percentage = (sensor['value'] / sensor['critical']) * 100
            else:
                # Use default thresholds if no critical value
                thresholds = self.temp_thresholds[
                    sensor['type'] if sensor['type'] in self.temp_thresholds 
                    else 'system'
                ]
                percentage = (sensor['value'] / thresholds['critical']) * 100
            
            # Format display components
            name = Text(sensor['name'][:12], style="cyan")
            reading = create_progress_bar(percentage, color=color)
            details = Text(
                f"{sensor['value']:4.1f}°C | {status}",
                style=color
            )
            
            return name, reading, details
            
        except Exception as e:
            logger.error(f"Error formatting temperature row: {e}", exc_info=True)
            return (
                Text(sensor['name'][:12], "red"),
                Text("ERROR", "red"),
                Text(str(e), "red")
            )

    def _format_fan_row(self, fan: Dict[str, Any]) -> Tuple[Text, Text, Text]:
        """
        Format a fan sensor reading for display
        
        Args:
            fan: Dictionary containing fan information
            
        Returns:
            Tuple of (name, reading, details) Text objects
        """
        try:
            # Determine fan status
            if fan['current'] <= self.fan_thresholds['minimum']:
                status = 'STOPPED'
                color = "red"
            elif fan['current'] <= self.fan_thresholds['warning']:
                status = 'LOW'
                color = "yellow"
            else:
                status = 'OK'
                color = "green"
            
            # Calculate percentage if we have min/max values
            if fan.get('min') is not None and fan.get('max') is not None:
                percentage = ((fan['current'] - fan['min']) / 
                            (fan['max'] - fan['min'])) * 100
                reading = create_progress_bar(percentage, color=color)
            else:
                # Create simple indicator if no min/max
                reading = Text("■" * 10, color)
            
            # Format details
            details = [f"{fan['current']} RPM"]
            if fan.get('min'):
                details.append(f"Min: {fan['min']}")
            if fan.get('max'):
                details.append(f"Max: {fan['max']}")
            details.append(status)
            
            return (
                Text(fan['name'][:12], style="cyan"),
                reading,
                Text(" | ".join(details), style=color)
            )
            
        except Exception as e:
            logger.error(f"Error formatting fan row: {e}", exc_info=True)
            return (
                Text(fan['name'][:12], "red"),
                Text("ERROR", "red"),
                Text(str(e), "red")
            )

    def _format_power_row(self, sensor: Dict[str, Any]) -> Tuple[Text, Text, Text]:
        """
        Format a power sensor reading for display
        
        Args:
            sensor: Dictionary containing power sensor information
            
        Returns:
            Tuple of (name, reading, details) Text objects
        """
        try:
            # Determine appropriate color based on power usage
            if sensor.get('value', 0) > 100:  # High power usage
                color = "yellow"
            elif sensor.get('value', 0) > 150:  # Very high power usage
                color = "red"
            else:
                color = "green"
            
            # Special handling for battery
            if sensor.get('source') == 'battery':
                if sensor.get('plugged'):
                    status = "Plugged In"
                    details = f"Battery: {sensor['percent']}%"
                else:
                    status = "On Battery"
                    details = f"Remaining: {sensor['percent']}%"
                    
                reading = create_progress_bar(sensor['percent'], color=color)
            else:
                # Normal power sensor
                reading = Text(f"{sensor['value']:.1f}{sensor['unit']}", color)
                status = "OK"
                details = f"Source: {sensor['source']}"
            
            return (
                Text(sensor['name'][:12], style="cyan"),
                reading,
                Text(f"{status} | {details}", style=color)
            )
            
        except Exception as e:
            logger.error(f"Error formatting power row: {e}", exc_info=True)
            return (
                Text(sensor['name'][:12], "red"),
                Text("ERROR", "red"),
                Text(str(e), "red")
            )

    def render(self) -> Panel:
        """
        Render sensor monitor display with comprehensive information
        
        Returns:
            Rich Panel containing formatted sensor information
        """
        try:
            # Create main display table
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Sensor", style="cyan", width=12)
            table.add_column("Reading", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get sensor data
            data = self._get_sensor_data()
            
            if data['errors']:
                logger.warning("Sensor errors: " + "; ".join(data['errors']))
            
            # Display temperatures
            if data['temperatures']:
                table.add_row("Temperatures", "", "")
                
                # Group temperatures by type
                temp_groups = {}
                for temp in data['temperatures']:
                    sensor_type = temp['type']
                    if sensor_type not in temp_groups:
                        temp_groups[sensor_type] = []
                    temp_groups[sensor_type].append(temp)
                
                # Display each temperature group
                for sensor_type, sensors in sorted(temp_groups.items()):
                    try:
                        # Add group header
                        table.add_row(
                            Text(sensor_type.upper(), "bright_black"),
                            "", ""
                        )
                        
                        # Add each sensor in group
                        for sensor in sorted(
                            sensors, 
                            key=lambda x: x['value'],
                            reverse=True
                        ):
                            name, reading, details = self._format_temperature_row(
                                sensor
                            )
                            table.add_row(name, reading, details)
                            
                    except Exception as e:
                        logger.error(
                            f"Error displaying temperature group {sensor_type}: {e}"
                        )
                        continue
            
            # Display fans
            if data['fans']:
                table.add_row("", "", "")
                table.add_row("Fans", "", "")
                
                for fan in sorted(
                    data['fans'],
                    key=lambda x: x['current'],
                    reverse=True
                ):
                    try:
                        name, reading, details = self._format_fan_row(fan)
                        table.add_row(name, reading, details)
                    except Exception as e:
                        logger.error(f"Error displaying fan: {e}")
                        continue
            
            # Display power sensors
            if data['power']:
                table.add_row("", "", "")
                table.add_row("Power", "", "")
                
                for sensor in sorted(
                    data['power'],
                    key=lambda x: x.get('value', 0),
                    reverse=True
                ):
                    try:
                        name, reading, details = self._format_power_row(sensor)
                        table.add_row(name, reading, details)
                    except Exception as e:
                        logger.error(f"Error displaying power sensor: {e}")
                        continue
            
            return Panel(
                table,
                title="Sensor Monitor",
                border_style="red"
            )
            
        except Exception as e:
            logger.error(f"Critical error in sensor monitor render: {e}", 
                        exc_info=True)
            return Panel(
                Text("Sensor Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        



class FirewallMonitor(BaseMonitor):
    """
    Firewall monitoring with comprehensive status tracking and rule analysis.
    Features:
    - Firewall rule monitoring
    - Connection tracking
    - Block/Drop statistics
    - Rate monitoring
    """
    
    def __init__(self):
        try:
            super().__init__()
            
            # Initialize counters for blocked connections
            self.last_blocked = self._get_blocked_count()
            self.last_check_time = time.time()
            
            # Initialize rule cache with safety limits
            self.rules_cache = []        # Store firewall rules
            self.rules_cache_time = 0    # Last cache update
            self.rules_cache_ttl = 5     # Cache TTL in seconds
            
            # Initialize history tracking
            self.history = {
                'blocked_rate': deque(maxlen=60),    # 1 minute of history
                'connections': deque(maxlen=60),
                'errors': deque(maxlen=10)
            }
            
            logger.debug("FirewallMonitor initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize FirewallMonitor: {e}", 
                          exc_info=True)
            raise

    def _get_blocked_count(self) -> int:
        """
        Get count of blocked connections from iptables and nftables
        
        Returns:
            Integer count of blocked connections
        
        Note:
            Combines blocked counts from both iptables and nftables
            for comprehensive tracking
        """
        blocked = 0
        
        try:
            # Check iptables blocks
            try:
                cmd = ['iptables', '-L', 'INPUT', '-v', '-n']
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        # Look for DROP or REJECT targets
                        if 'DROP' in line or 'REJECT' in line:
                            try:
                                # First field is packet count
                                blocked += int(line.split()[0])
                            except (IndexError, ValueError) as e:
                                logger.debug(f"Error parsing iptables line: {e}")
                                continue
                else:
                    logger.warning(
                        f"iptables command failed: {result.stderr.strip()}"
                    )
                    
            except subprocess.TimeoutExpired:
                logger.error("iptables command timed out")
            except Exception as e:
                logger.error(f"Error checking iptables blocks: {e}")
            
            # Check nftables blocks
            try:
                cmd = ['nft', 'list', 'ruleset']
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                
                if result.returncode == 0:
                    # Count drop and reject rules
                    for line in result.stdout.split('\n'):
                        if 'drop' in line or 'reject' in line:
                            blocked += 1  # Increment for each blocking rule
                else:
                    logger.warning(
                        f"nftables command failed: {result.stderr.strip()}"
                    )
                    
            except subprocess.TimeoutExpired:
                logger.error("nftables command timed out")
            except Exception as e:
                logger.error(f"Error checking nftables blocks: {e}")
            
            return blocked
            
        except Exception as e:
            logger.error(f"Error getting blocked count: {e}", exc_info=True)
            return 0

    def _get_active_connections(self) -> dict:
        """
        Get counts of connections by state
        
        Returns:
            Dictionary containing connection counts by state
        
        Note:
            Tracks ESTABLISHED, LISTEN, TIME_WAIT, CLOSE_WAIT states
            and groups others under 'other'
        """
        connections = {
            'ESTABLISHED': 0,
            'LISTEN': 0,
            'TIME_WAIT': 0,
            'CLOSE_WAIT': 0,
            'other': 0
        }
        
        try:
            # Get all network connections
            for conn in psutil.net_connections(kind='inet'):
                try:
                    status = conn.status
                    if status in connections:
                        connections[status] += 1
                    else:
                        connections['other'] += 1
                except Exception as e:
                    logger.error(f"Error processing connection: {e}")
                    continue
            
            return connections
            
        except Exception as e:
            logger.error(f"Error getting active connections: {e}", exc_info=True)
            return connections

    def _get_firewall_rules(self) -> list:
        """
        Get current firewall rules from iptables and nftables
        
        Returns:
            List of dictionaries containing firewall rules
        
        Note:
            Combines rules from both iptables and nftables
            Caches results to prevent excessive command execution
        """
        try:
            current_time = time.time()
            
            # Return cached rules if still valid
            if (self.rules_cache and 
                current_time - self.rules_cache_time < self.rules_cache_ttl):
                return self.rules_cache
            
            rules = []
            
            # Get iptables rules
            try:
                # Check each chain (INPUT, OUTPUT, FORWARD)
                for chain in ['INPUT', 'OUTPUT', 'FORWARD']:
                    cmd = ['iptables', '-L', chain, '-n', '-v']
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=1
                    )
                    
                    if result.returncode == 0:
                        # Skip header lines
                        lines = result.stdout.split('\n')[2:]
                        
                        for line in lines:
                            if line.strip():
                                parts = line.split()
                                if len(parts) >= 4:
                                    rules.append({
                                        'chain': chain,
                                        'target': parts[2],
                                        'protocol': parts[3],
                                        'source': parts[7] if len(parts) > 7 
                                                 else '*',
                                        'destination': parts[8] if len(parts) > 8 
                                                      else '*',
                                        'type': 'iptables'
                                    })
                    else:
                        logger.warning(
                            f"iptables command failed for {chain}: "
                            f"{result.stderr.strip()}"
                        )
                        
            except Exception as e:
                logger.error(f"Error getting iptables rules: {e}")








class FirewallMonitor(BaseMonitor):  # Continuing from previous implementation
    def _get_firewall_rules(self) -> list:  # Continuing the method
        """
        Get current firewall rules from iptables and nftables
        """
        try:
            # Get nftables rules (continuing from previous implementation)
            try:
                cmd = ['nft', 'list', 'ruleset']
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                
                if result.returncode == 0:
                    current_chain = None
                    
                    for line in result.stdout.split('\n'):
                        # Check for chain definition
                        if 'chain' in line:
                            current_chain = line.split()[1]
                        # Check for rules with accept/drop/reject
                        elif current_chain and any(
                            action in line for action in 
                            ['accept', 'drop', 'reject']
                        ):
                            parts = line.strip().split()
                            
                            # Extract rule components
                            rule = {
                                'chain': current_chain,
                                'target': next(
                                    (p for p in parts if p in 
                                     ['accept', 'drop', 'reject']), 
                                    'unknown'
                                ),
                                'protocol': next(
                                    (p for p in parts if p in 
                                     ['tcp', 'udp', 'icmp']), 
                                    '*'
                                ),
                                'source': '*',
                                'destination': '*',
                                'type': 'nftables'
                            }
                            
                            rules.append(rule)
                else:
                    logger.warning(
                        f"nftables command failed: {result.stderr.strip()}"
                    )
                    
            except Exception as e:
                logger.error(f"Error getting nftables rules: {e}")
            
            # Update cache
            self.rules_cache = rules
            self.rules_cache_time = current_time
            
            return rules
            
        except Exception as e:
            logger.error(f"Error getting firewall rules: {e}", exc_info=True)
            return []

    def _calculate_block_rate(self) -> Dict[str, Any]:
        """
        Calculate current blocking rate and update history
        
        Returns:
            Dictionary containing block rate and related metrics
        """
        try:
            # Get current block count
            current_blocked = self._get_blocked_count()
            current_time = time.time()
            
            # Calculate time difference
            dt = current_time - self.last_check_time
            
            # Calculate metrics
            metrics = {
                'total_blocked': current_blocked,
                'block_rate': 0,
                'time_period': dt
            }
            
            # Calculate rate if time difference is valid
            if dt > 0:
                block_diff = current_blocked - self.last_blocked
                block_rate = block_diff / dt
                
                metrics['block_rate'] = max(0, block_rate)  # Ensure non-negative
                
                # Update history
                self.history['blocked_rate'].append(block_rate)
            
            # Update tracking variables
            self.last_blocked = current_blocked
            self.last_check_time = current_time
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error calculating block rate: {e}", exc_info=True)
            return {
                'total_blocked': 0,
                'block_rate': 0,
                'time_period': 0,
                'error': str(e)
            }

    def _format_rule_display(self, rule: Dict[str, Any]) -> Tuple[Text, Text]:
        """
        Format a firewall rule for display
        
        Args:
            rule: Dictionary containing rule information
            
        Returns:
            Tuple of (status, details) Text objects
        """
        try:
            # Determine rule color based on target
            target_color = {
                'ACCEPT': 'green',
                'DROP': 'red',
                'REJECT': 'red'
            }.get(rule['target'].upper(), 'white')
            
            # Create status display
            status = Text(rule['target'].upper(), target_color)
            
            # Create details display
            details = [
                f"{rule['protocol']}",
                f"{rule['source']} → {rule['destination']}"
            ]
            
            if rule.get('type'):
                details.append(f"({rule['type']})")
                
            return status, Text(" | ".join(details))
            
        except Exception as e:
            logger.error(f"Error formatting rule display: {e}", exc_info=True)
            return (
                Text("ERROR", "red"),
                Text(str(e), "red")
            )

    def render(self) -> Panel:
        """
        Render firewall monitor display with comprehensive status
        
        Returns:
            Rich Panel containing firewall information
        """
        try:
            # Create main display table
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Firewall", style="cyan", width=12)
            table.add_column("Status", ratio=2)
            table.add_column("Details", style="bright_blue")
            
            # Get connection information
            connections = self._get_active_connections()
            total_connections = sum(connections.values())
            
            # Display connection status
            try:
                table.add_row(
                    "Connections",
                    f"Total: {total_connections}",
                    f"Active: {connections['ESTABLISHED']} | "
                    f"Listening: {connections['LISTEN']}"
                )
                
                # Add additional connection states if present
                if connections['TIME_WAIT'] > 0 or connections['CLOSE_WAIT'] > 0:
                    table.add_row(
                        "",
                        f"Time Wait: {connections['TIME_WAIT']}",
                        f"Close Wait: {connections['CLOSE_WAIT']}"
                    )
            except Exception as e:
                logger.error(f"Error displaying connections: {e}")
                table.add_row("Connections", "Error displaying connections", "")
            
            # Get and display block rate
            try:
                metrics = self._calculate_block_rate()
                
                if 'error' not in metrics:
                    table.add_row(
                        "Blocked",
                        create_progress_bar(
                            min(metrics['block_rate'] * 10, 100)
                        ),
                        f"Rate: {metrics['block_rate']:.1f}/s | "
                        f"Total: {metrics['total_blocked']}"
                    )
            except Exception as e:
                logger.error(f"Error displaying block rate: {e}")
                table.add_row("Blocked", "Error displaying block rate", "")
            
            # Get and display firewall rules
            try:
                rules = self._get_firewall_rules()
                
                if rules:
                    # Count rules by target
                    rule_counts = {
                        'accept': 0,
                        'drop': 0,
                        'reject': 0
                    }
                    
                    for rule in rules:
                        target = rule['target'].lower()
                        if target in rule_counts:
                            rule_counts[target] += 1
                    
                    # Display rule summary
                    table.add_row(
                        "Rules",
                        f"Total: {len(rules)}",
                        f"Accept: {rule_counts['accept']} | "
                        f"Drop: {rule_counts['drop']} | "
                        f"Reject: {rule_counts['reject']}"
                    )
                    
                    # Display recent rules
                    table.add_row("", "", "")
                    table.add_row("Recent Rules", "", "")
                    
                    for rule in rules[:5]:  # Show top 5 rules
                        try:
                            status, details = self._format_rule_display(rule)
                            table.add_row(
                                rule['chain'][:8],
                                status,
                                details
                            )
                        except Exception as e:
                            logger.error(f"Error displaying rule: {e}")
                            continue
            except Exception as e:
                logger.error(f"Error displaying rules: {e}")
                table.add_row("Rules", "Error displaying rules", "")
            
            return Panel(
                table,
                title="Firewall Monitor",
                border_style="red"
            )
            
        except Exception as e:
            logger.error(f"Critical error in firewall monitor render: {e}", 
                        exc_info=True)
            return Panel(
                Text("Firewall Monitor Error - Check Logs", "red"),
                border_style="red"
            )
        


class SystemMonitorApp(App):
    """
    Main system monitoring application handling UI and monitor coordination.
    
    Features:
    - Dual-column layout
    - Real-time updates
    - Safe terminal handling
    - Crash recovery
    """
    
    # Define CSS for layout and styling
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
        content-align: center middle;
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
        """Initialize the application with error handling"""
        try:
            super().__init__()
            
            # Disable exit confirmations for clean shutdown
            self.prevent_exit_confirmations = True
            
            # Remove dialog classes for cleaner interface
            Screen.DIALOG_CLASSES = []
            
            # Initialize monitor states
            self.monitor_states = {
                'cpu': {'active': True, 'error_count': 0},
                'memory': {'active': True, 'error_count': 0},
                'network': {'active': True, 'error_count': 0},
                'disk': {'active': True, 'error_count': 0},
                'gpu': {'active': True, 'error_count': 0},
                'sensors': {'active': True, 'error_count': 0},
                'firewall': {'active': True, 'error_count': 0},
                'services': {'active': True, 'error_count': 0}
            }
            
            # Track application state
            self.startup_time = time.time()
            self.last_refresh = 0
            self.min_refresh_interval = 0.1  # Minimum seconds between refreshes
            
            logger.debug("SystemMonitorApp initialized successfully")
            
        except Exception as e:
            logger.critical(f"Failed to initialize SystemMonitorApp: {e}", 
                          exc_info=True)
            raise

    def _safe_create_monitor(self, 
                           monitor_class: type, 
                           monitor_name: str) -> Optional[BaseMonitor]:
        """
        Safely create a monitor instance with error handling
        
        Args:
            monitor_class: Monitor class to instantiate
            monitor_name: Name of the monitor for logging
            
        Returns:
            Monitor instance or None on failure
        """
        try:
            monitor = monitor_class()
            logger.debug(f"Successfully created {monitor_name} monitor")
            return monitor
            
        except Exception as e:
            logger.error(f"Failed to create {monitor_name} monitor: {e}", 
                        exc_info=True)
            self.monitor_states[monitor_name.lower()]['active'] = False
            self.monitor_states[monitor_name.lower()]['error_count'] += 1
            
            # Create error display
            error_text = Text(
                f"{monitor_name} Monitor Error\n{str(e)}",
                style="red"
            )
            return Static(error_text)

    async def _update_monitor(self, 
                            monitor: BaseMonitor, 
                            monitor_name: str) -> None:
        """
        Safely update a monitor with error handling
        
        Args:
            monitor: Monitor instance to update
            monitor_name: Name of the monitor for logging
        """
        try:
            # Check if enough time has passed since last refresh
            current_time = time.time()
            if (current_time - self.last_refresh) < self.min_refresh_interval:
                return
                
            # Update monitor state
            if isinstance(monitor, BaseMonitor):
                await monitor._do_refresh()
                
            self.last_refresh = current_time
            
        except Exception as e:
            logger.error(f"Error updating {monitor_name} monitor: {e}", 
                        exc_info=True)
            self.monitor_states[monitor_name.lower()]['error_count'] += 1

    def on_mount(self) -> None:
        """Handle mount event with error handling"""
        try:
            logger.debug("SystemMonitorApp mounting")
            
            # Schedule periodic cleanup
            self.set_interval(60, self._cleanup_resources)
            
            logger.debug("SystemMonitorApp mount complete")
            
        except Exception as e:
            logger.critical(f"Error during app mount: {e}", exc_info=True)
            raise

    def _cleanup_resources(self) -> None:
        """Perform periodic resource cleanup"""
        try:
            # Clear any accumulated messages
            if hasattr(self, 'message_queue'):
                self.message_queue.clear()
                
            # Force garbage collection
            gc.collect()
            
            logger.debug("Resource cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during resource cleanup: {e}", exc_info=True)

    def on_key(self, event) -> None:
        """Handle keyboard events with error handling"""
        try:
            # Handle CTRL+C for clean exit
            if event.key == "ctrl+c":
                logger.info("CTRL+C detected, initiating clean shutdown")
                self.exit()
                
        except Exception as e:
            logger.error(f"Error in key handler: {e}", exc_info=True)

    def action_quit(self) -> None:
        """Handle quit action with clean shutdown"""
        try:
            logger.info("Quit action triggered")
            self._cleanup_resources()
            restore_terminal()
            self.exit()
            
        except Exception as e:
            logger.error(f"Error during quit: {e}", exc_info=True)
            # Force exit if clean shutdown fails
            sys.exit(1)

    def _on_exit(self) -> None:
        """Ensure terminal is properly restored on exit"""
        try:
            logger.info("Running exit cleanup")
            restore_terminal()
            
        except Exception as e:
            logger.error(f"Error during terminal restore: {e}", exc_info=True)







class SystemMonitorApp(App):  # Continuing from previous implementation
    def compose(self) -> ComposeResult:
        """
        Compose the application layout with comprehensive error handling
        
        Returns:
            ComposeResult containing the application layout
        """
        try:
            logger.debug("Starting application composition")
            
            # Create header with system information
            try:
                # Gather system info components
                header_components = [
                    f"OS: {PLATFORM_INFO['system'].title()} "
                    f"{PLATFORM_INFO['release']}",
                    f"Python: {PLATFORM_INFO['python_version']}",
                    f"Cores: {psutil.cpu_count()}",
                    f"RAM: {format_bytes(psutil.virtual_memory().total)}"
                ]
                
                # Join components and create header
                header_text = " | ".join(header_components)
                yield Header(header_text)
                
                logger.debug("Header created successfully")
                
            except Exception as e:
                logger.error(f"Error creating header: {e}")
                yield Header("System Monitor")
            
            # Create left column with monitors
            try:
                logger.debug("Creating left column")
                left_container = Container(id="left-column")
                
                # CPU Monitor
                cpu_monitor = self._safe_create_monitor(CPUMonitor, "CPU")
                if cpu_monitor:
                    left_container.compose_add_child(cpu_monitor)
                
                # GPU Monitor (if available)
                if check_gpu_available():
                    gpu_monitor = self._safe_create_monitor(GPUMonitor, "GPU")
                    if gpu_monitor:
                        left_container.compose_add_child(gpu_monitor)
                
                # Service Monitor
                service_monitor = self._safe_create_monitor(
                    ServiceMonitor, "Service")
                if service_monitor:
                    left_container.compose_add_child(service_monitor)
                
                yield left_container
                logger.debug("Left column created successfully")
                
            except Exception as e:
                logger.error(f"Error creating left column: {e}")
                yield Container(
                    Static("Error loading monitors", style="red"),
                    id="left-column"
                )
            
            # Create right column with monitors
            try:
                logger.debug("Creating right column")
                right_container = Container(id="right-column")
                
                # Memory Monitor
                memory_monitor = self._safe_create_monitor(MemoryMonitor, "Memory")
                if memory_monitor:
                    right_container.compose_add_child(memory_monitor)
                
                # Disk Monitor
                disk_monitor = self._safe_create_monitor(DiskMonitor, "Disk")
                if disk_monitor:
                    right_container.compose_add_child(disk_monitor)
                
                # Network Monitor
                network_monitor = self._safe_create_monitor(
                    NetworkMonitor, "Network")
                if network_monitor:
                    right_container.compose_add_child(network_monitor)
                
                # Firewall Monitor
                firewall_monitor = self._safe_create_monitor(
                    FirewallMonitor, "Firewall")
                if firewall_monitor:
                    right_container.compose_add_child(firewall_monitor)
                
                # Sensor Monitor
                sensor_monitor = self._safe_create_monitor(
                    SensorMonitor, "Sensor")
                if sensor_monitor:
                    right_container.compose_add_child(sensor_monitor)
                
                yield right_container
                logger.debug("Right column created successfully")
                
            except Exception as e:
                logger.error(f"Error creating right column: {e}")
                yield Container(
                    Static("Error loading monitors", style="red"),
                    id="right-column"
                )
            
            # Create footer
            try:
                # Create footer with runtime information
                runtime = time.time() - self.startup_time
                footer_text = f"Runtime: {int(runtime)}s | Press Ctrl+C to exit"
                yield Footer(footer_text)
                
                logger.debug("Footer created successfully")
                
            except Exception as e:
                logger.error(f"Error creating footer: {e}")
                yield Footer("System Monitor")
            
            logger.debug("Application composition completed successfully")
            
        except Exception as e:
            logger.critical(f"Critical error in compose: {e}", exc_info=True)
            # Create minimal error display
            yield Header("System Monitor - ERROR")
            yield Container(
                Static(f"Critical Error: {e}", style="red"),
                id="left-column"
            )
            yield Container(
                Static("Please check logs for details", style="red"),
                id="right-column"
            )
            yield Footer("Error State")

    def run(self, *args, **kwargs):
        """
        Override run to add comprehensive error handling
        
        Args:
            *args: Positional arguments for parent run method
            **kwargs: Keyword arguments for parent run method
            
        Returns:
            Result from parent run method
        """
        try:
            logger.info("Starting application run")
            
            # Set terminal title
            set_terminal_title("System Monitor")
            
            # Run application
            result = super().run(*args, **kwargs)
            
            logger.info("Application run completed normally")
            return result
            
        except Exception as e:
            logger.critical(f"Critical error in application run: {e}", 
                          exc_info=True)
            restore_terminal()
            raise



def restore_terminal() -> None:
    """
    Restore terminal to normal state with comprehensive error handling.
    Ensures terminal is left in a usable state even if errors occur.
    """
    logger.info("Starting terminal restoration")
    errors = []

    try:
        # Step 1: Reset terminal settings based on platform
        if os.name != 'nt':  # Unix-like systems
            try:
                result = os.system('stty sane')
                if result != 0:
                    errors.append(f"stty command failed with code {result}")
            except Exception as e:
                errors.append(f"Failed to reset terminal settings: {e}")

        # Step 2: Clear screen using platform-appropriate command
        try:
            if os.name == 'nt':
                os.system('cls')
            else:
                os.system('clear')
        except Exception as e:
            errors.append(f"Failed to clear screen: {e}")

        # Step 3: Reset cursor state and position
        try:
            # Show cursor
            print('\033[?25h', end='', flush=True)
            # Clear screen
            print('\033[2J', end='', flush=True)
            # Move cursor to home position
            print('\033[H', end='', flush=True)
        except Exception as e:
            errors.append(f"Failed to reset cursor: {e}")

    except Exception as e:
        logger.error(f"Critical error during terminal restoration: {e}", 
                    exc_info=True)
        errors.append(f"Critical error: {e}")

    # Log any errors that occurred during restoration
    if errors:
        logger.error("Errors during terminal restoration:\n" + 
                    "\n".join(errors))
    else:
        logger.info("Terminal restored successfully")

def setup_signal_handlers() -> None:
    """
    Set up signal handlers for clean application shutdown.
    Ensures resources are properly cleaned up on exit signals.
    """
    def safe_exit(sig: int, frame) -> None:
        """
        Handle exit signals with proper cleanup
        
        Args:
            sig: Signal number
            frame: Current stack frame
        """
        try:
            logger.info(f"Received signal {sig}, initiating safe shutdown")
            
            # Create crash dump if it's not a normal termination
            if sig != signal.SIGTERM:
                create_crash_dump(
                    sys.exc_info(),
                    f"Signal handler: {sig}"
                )
            
            # Attempt terminal restoration
            try:
                restore_terminal()
                logger.info("Terminal restored during shutdown")
            except Exception as e:
                logger.error(
                    f"Failed to restore terminal during shutdown: {e}"
                )
            
            # Set global exit flag
            global should_exit
            should_exit = True
            
            logger.info("Clean shutdown completed")
            sys.exit(0)
            
        except Exception as e:
            logger.critical(f"Fatal error in signal handler: {e}", 
                          exc_info=True)
            sys.exit(1)

    try:
        # Register handlers for interrupt and termination signals
        signal.signal(signal.SIGINT, safe_exit)
        signal.signal(signal.SIGTERM, safe_exit)
        logger.info("Signal handlers registered successfully")
        
    except Exception as e:
        logger.critical(f"Failed to setup signal handlers: {e}", 
                      exc_info=True)
        sys.exit(1)

def main() -> None:
    """
    Main application entry point with comprehensive error handling.
    Coordinates application startup, execution, and shutdown.
    """
    logger.info("Starting main function")
    
    try:
        # Step 1: Set up signal handlers for clean exit
        setup_signal_handlers()
        logger.info("Signal handlers set up successfully")
        
        # Step 2: Verify system requirements
        if not check_system_requirements():
            logger.critical("System requirements not met")
            sys.exit(1)
        
        # Step 3: Initialize crash handling
        setup_crash_handling()
        logger.info("Crash handling initialized")
        
        # Step 4: Create and run application
        logger.info("Creating SystemMonitorApp instance")
        app = SystemMonitorApp()
        
        logger.info("Starting application")
        app.run()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        restore_terminal()
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Critical error in main: {e}", exc_info=True)
        restore_terminal()
        sys.exit(1)
    finally:
        logger.info("Exiting main function")
        restore_terminal()

def check_system_requirements() -> bool:
    """
    Verify that all system requirements are met.
    
    Returns:
        Boolean indicating whether requirements are met
    """
    try:
        # Check Python version
        if sys.version_info < (3, 7):
            logger.error("Python 3.7 or higher is required")
            return False
        
        # Check required commands
        required_commands = ['stty', 'clear', 'iptables', 'nft']
        for cmd in required_commands:
            if os.name != 'nt':  # Skip on Windows
                if not shutil.which(cmd):
                    logger.warning(f"Required command not found: {cmd}")
        
        # Check terminal capabilities
        try:
            if os.name != 'nt':
                subprocess.run(['tput', 'colors'], 
                             capture_output=True, 
                             check=True)
        except subprocess.CalledProcessError:
            logger.warning("Terminal may not support colors")
        
        # Check disk access
        try:
            with open(os.devnull, 'w') as f:
                pass
        except Exception as e:
            logger.error(f"Disk access error: {e}")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error checking system requirements: {e}", 
                    exc_info=True)
        return False

def setup_crash_handling() -> None:
    """
    Initialize crash handling and reporting system.
    Sets up crash dumps and emergency logging.
    """
    try:
        # Create crash dump directory
        os.makedirs(CRASH_DUMP_DIR, exist_ok=True)
        
        # Set up emergency file logging
        crash_log = os.path.join(
            CRASH_DUMP_DIR,
            f'crash_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        )
        
        # Add crash file handler
        crash_handler = logging.FileHandler(crash_log)
        crash_handler.setLevel(logging.ERROR)
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        crash_handler.setFormatter(formatter)
        logger.addHandler(crash_handler)
        
        logger.info("Crash handling system initialized")
        
    except Exception as e:
        logger.error(f"Failed to setup crash handling: {e}", 
                    exc_info=True)
        raise

if __name__ == "__main__":
    try:
        logger.info("Starting application")
        main()
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
        sys.exit(1)