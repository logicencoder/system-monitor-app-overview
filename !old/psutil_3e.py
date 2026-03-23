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
from typing import Dict, Optional, List, Union, Any
from datetime import datetime
from collections import deque

from textual.binding import Binding


# Required third-party imports
try:
    # Rich for text formatting
    from rich import box
    from rich.text import Text
    from rich.panel import Panel
    from rich.table import Table
    
    # Textual for UI
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Static
    from textual.containers import Grid, Container
    
    # psutil for system monitoring
    import psutil
except ImportError as e:
    print("Required dependencies not found. Please install with:")
    print("pip install psutil textual rich")
    sys.exit(1)
    
    
    
from textual.screen import Screen
Screen.DIALOG_CLASSES = []  # Remove all dialog classes    
    
    

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('system_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Platform detection and capabilities
PLATFORM_INFO = {
    'system': platform.system().lower(),
    'release': platform.release(),
    'version': platform.version(),
    'machine': platform.machine(),
    'is_linux': platform.system().lower() == 'linux',
    'python_version': sys.version.split()[0],
    'processor': platform.processor()
}

# Monitoring intervals (in seconds)
MONITOR_INTERVALS = {
    'cpu': 1.1,      # CPU monitor refresh
    'memory': 2.1,   # Memory monitor refresh
    'network': 1.1,  # Network monitor refresh
    'disk': 1.1,     # Disk monitor refresh
    'gpu': 1.1,      # GPU monitor refresh
    'sensors': 2.1,  # Sensors monitor refresh
    'services': 5.0  # Services monitor refresh
}

# Number of CPU cores to display per line
CORES_PER_LINE = 4







# Cache for expensive system calls
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

CACHE_TTL = {
    'processor': 60,    # CPU info rarely changes
    'gpu': 1,          # GPU status needs frequent updates
    'services': 5,     # Service status moderate updates
    'partitions': 5,   # Partition info moderate updates
    'network': 2,      # Network interface info
    'sensors': 3       # Sensor data moderate updates
}








should_exit = False




def set_terminal_title(title: str) -> None:
    """Set the terminal window title."""
    try:
        if os.name == 'nt':  # Windows
            os.system(f'title {title}')
        else:  # Unix-like
            print(f'\033]0;{title}\007', end='', flush=True)
    except Exception as e:
        logging.error(f"Failed to set terminal title: {e}")
        
# Set title to current filename
set_terminal_title(os.path.basename(__file__))       


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
        super().__init__()
        self.error_count = 0
        self.max_errors = 3
    
    def handle_error(self, error: Exception, context: str) -> None:
        """Handle and log errors with context."""
        self.error_count += 1
        logger.error(f"Error in {self.__class__.__name__} ({context}): {error}")
        if self.error_count >= self.max_errors:
            logger.warning(f"{self.__class__.__name__} experiencing repeated errors")
    
    def get_interval(self) -> float:
        """Get refresh interval for this monitor type."""
        monitor_type = self.__class__.__name__.lower().replace('monitor', '')
        return MONITOR_INTERVALS.get(monitor_type, 1.0)
    
    def on_mount(self) -> None:
        """Set refresh interval using configuration."""
        self.set_interval(self.get_interval(), self.refresh)

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
            return True
            
        # Method 2: Check common NVIDIA paths
        nvidia_paths = [
            '/proc/driver/nvidia/version',
            '/dev/nvidia0',
            '/dev/nvidiactl'
        ]
        if any(os.path.exists(path) for path in nvidia_paths):
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
                            return True
        except:
            pass
            
        return False
    except Exception:
        # If anything fails, assume GPU exists since we saw it before
        return True
    
    
    
class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor with processor name display."""
    
    # def on_mount(self) -> None:
    #     """Initialize monitor and get processor details on mount."""
    #     self.set_interval(0.5, self.refresh)
    #     self.processor_name = self._get_processor_name()



    def on_mount(self) -> None:
        """Initialize monitor and get processor details on mount."""
        super().on_mount()  # This will use the correct interval from MONITOR_INTERVALS
        self.processor_name = self._get_processor_name()








    
    def _get_processor_name(self) -> str:
        """Get the processor name from system information."""
        try:
            if sys.platform == "linux":
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":")[1].strip()
            return ""
        except Exception:
            return ""
    
    def get_usage_color(self, percentage: float) -> str:
        """Determine color based on usage percentage."""
        if percentage < 50:
            return "green"
        elif percentage < 75:
            return "yellow"
        elif percentage < 90:
            return "red"
        return "bright_red"
    
    def create_colored_bar(self, percentage: float, width: int = 30) -> Text:
        """Create a color-coded bar with percentage before the bar."""
        color = self.get_usage_color(percentage)
        filled = int(width * percentage / 100)
        remainder = width - filled
        return Text(f"{percentage:5.1f}% ", color) + Text('■' * filled, color) + Text('·' * remainder, "bright_black")
    
    def create_core_row(self, start_idx: int, cpu_percent: list) -> list:
        """Create a row of CPU core displays."""
        cores_in_row = []
        for i in range(start_idx, min(start_idx + CORES_PER_LINE, len(cpu_percent))):
            core_text = Text(f"Core {i:2d}: ", "cyan") + self.create_colored_bar(cpu_percent[i])
            cores_in_row.append(core_text)
        return cores_in_row
    
    def render(self) -> Panel:
        table = Table(box=None, expand=True, padding=(0,0))
        
        # Get CPU metrics
        cpu_percent = psutil.cpu_percent(percpu=True)
        freq = psutil.cpu_freq()
        times = psutil.cpu_times_percent()
        load = psutil.getloadavg()
        
        # Calculate total CPU usage
        total = sum(cpu_percent) / len(cpu_percent)
        
        # Create metrics header table
        metrics_table = Table(box=None, expand=True, padding=(0,0))
        metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
        metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)
        
        # Direct use of current frequency in MHz
        current_mhz = int(freq.current)
        
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
            # cores_table.add_column(f"Core Column {i}", ratio=1)
             cores_table.add_column
        
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

class MemoryMonitor(BaseMonitor):
    """Memory usage monitor with independent layout."""
    
    def render(self) -> Panel:
        # Create monitor-specific table
        table = Table(box=None, expand=True, padding=(0,0))
        
        # Add columns specific to memory display
        table.add_column("Memory", style="cyan", width=12, no_wrap=True)
        table.add_column("Usage", ratio=2)
        table.add_column("Details", style="bright_blue")
        
        # Get memory metrics
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # RAM Usage
        table.add_row(
            "RAM",
            create_progress_bar(vm.percent),
            f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}"
        )
        
        # Cache Usage
        cache_percent = (vm.cached / vm.total) * 100
        table.add_row(
            "Cache",
            create_progress_bar(cache_percent),
            f"Cached: {format_bytes(vm.cached)} | Buffers: {format_bytes(vm.buffers)}"
        )

        # Effective Memory
        if hasattr(vm, 'available'):
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
    
    







class NetworkMonitor(BaseMonitor):
    """Network monitor optimized for Linux systems."""

    def __init__(self):
        super().__init__()
        self.last_io = psutil.net_io_counters()
        self.last_time = time.time()
        # Restore full history tracking
        self.history = {
            'bytes_sent': deque(maxlen=60),
            'bytes_recv': deque(maxlen=60),
            'packets_sent': deque(maxlen=60),
            'packets_recv': deque(maxlen=60),
            'error_in': deque(maxlen=10),
            'error_out': deque(maxlen=10)
        }

    def _get_interface_info(self) -> Dict[str, Any]:
        """Get cached network interface information."""
        current_time = time.time()

        if (SYSTEM_INFO_CACHE['network_interfaces'] is None or
            current_time - SYSTEM_INFO_CACHE['network_cache_time'] > CACHE_TTL['network']):

            interfaces = {}
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()

            for name, addrs_list in addrs.items():
                if name not in stats or not stats[name].isup:
                    continue

                ipv4_addrs = []
                ipv6_addrs = []
                for addr in addrs_list:
                    if addr.family == socket.AF_INET:
                        ipv4_addrs.append(addr.address)
                    elif addr.family == socket.AF_INET6:
                        ipv6_addrs.append(addr.address)

                if not (ipv4_addrs or ipv6_addrs):
                    continue

                stat = stats[name]
                interfaces[name] = {
                    'ipv4': ipv4_addrs,
                    'ipv6': ipv6_addrs,
                    'speed': stat.speed or 0,
                    'mtu': stat.mtu,
                    'duplex': getattr(stat, 'duplex', 'unknown'),
                    'is_up': stat.isup
                }

            SYSTEM_INFO_CACHE['network_interfaces'] = interfaces
            SYSTEM_INFO_CACHE['network_cache_time'] = current_time

        return SYSTEM_INFO_CACHE['network_interfaces']

    def _get_network_metrics(self) -> Dict[str, Any]:
        now = time.time()
        curr_io = psutil.net_io_counters()
        dt = now - self.last_time

        metrics = {
            'bytes_sent': curr_io.bytes_sent,
            'bytes_recv': curr_io.bytes_recv,
            'packets_sent': curr_io.packets_sent,
            'packets_recv': curr_io.packets_recv,
            'error_in': curr_io.errin,
            'error_out': curr_io.errout,
            'rates': {}
        }

        if dt > 0:
            # Calculate rates
            for key in ['bytes_sent', 'bytes_recv', 'packets_sent', 'packets_recv']:
                curr_val = getattr(curr_io, key)
                prev_val = getattr(self.last_io, key)
                rate = (curr_val - prev_val) / dt
                metrics['rates'][key] = rate

                # Update history
                if key in self.history:
                    self.history[key].append(rate)

            # Update error history
            self.history['error_in'].append(curr_io.errin - self.last_io.errin)
            self.history['error_out'].append(curr_io.errout - self.last_io.errout)

        self.last_io = curr_io
        self.last_time = now

        return metrics

    def render(self) -> Panel:
        table = Table(box=None, expand=True, padding=(0,0))
        table.add_column("Network", style="cyan", width=12)
        table.add_column("Usage", ratio=2)
        table.add_column("Details", style="bright_blue")

        metrics = self._get_network_metrics()
        interfaces = self._get_interface_info()

        if 'rates' in metrics:
            rates = metrics['rates']

            # Download rate
            if 'bytes_recv' in rates:
                recv_rate = rates['bytes_recv']
                recv_percent = min(100, (recv_rate / (10 * 1024 * 1024)) * 100)
                table.add_row(
                    "Download",
                    create_progress_bar(recv_percent),
                    f"Current: {format_bytes(recv_rate)}/s | "
                    f"Total: {format_bytes(metrics['bytes_recv'])}"
                )

            # Upload rate
            if 'bytes_sent' in rates:
                send_rate = rates['bytes_sent']
                send_percent = min(100, (send_rate / (10 * 1024 * 1024)) * 100)
                table.add_row(
                    "Upload",
                    create_progress_bar(send_percent),
                    f"Current: {format_bytes(send_rate)}/s | "
                    f"Total: {format_bytes(metrics['bytes_sent'])}"
                )

            # Packet rates
            if 'packets_recv' in rates and 'packets_sent' in rates:
                table.add_row(
                    "Packets",
                    f"↓ {rates['packets_recv']:.1f}/s",
                    f"↑ {rates['packets_sent']:.1f}/s"
                )

        # Interface section
        table.add_row("", "", "")
        table.add_row("Interfaces", "", "")

        for name, info in interfaces.items():
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

        # Error information
        if any(metrics.get(key, 0) > 0 for key in ['error_in', 'error_out']):
            table.add_row("", "", "")
            table.add_row(
                "Errors",
                f"In: {metrics['error_in']} | Out: {metrics['error_out']}",
                f"Drops - In: {metrics.get('drop_in', 0)} | "
                f"Out: {metrics.get('drop_out', 0)}"
            )

        return Panel(table, title="Network Monitor", border_style="cyan")













# class NetworkMonitor(BaseMonitor):
#     """Network monitor optimized for Linux systems."""
    
#     def __init__(self):
#         """Initialize network monitor with counters."""
#         super().__init__()
#         # Initialize tracking with expanded history
#         self.last_io = psutil.net_io_counters()
#         self.last_time = time.time()
#         # Expand history tracking
#         self.history = {
#             'bytes_sent': deque(maxlen=60),    # Increased to 1 minute history
#             'bytes_recv': deque(maxlen=60),
#             'packets_sent': deque(maxlen=60),  # Added packet tracking
#             'packets_recv': deque(maxlen=60),
#             'error_in': deque(maxlen=10),
#             'error_out': deque(maxlen=10)
#         }
    
#     def _get_interface_info(self) -> Dict[str, Any]:
#         """Get detailed network interface information."""
#         try:
#             interfaces = {}
#             addrs = psutil.net_if_addrs()
#             stats = psutil.net_if_stats()
            
#             for name, addrs_list in addrs.items():
#                 # Skip interfaces that aren't up
#                 if name not in stats or not stats[name].isup:
#                     continue
                
#                 # Get IP addresses
#                 ipv4_addrs = []
#                 ipv6_addrs = []
#                 for addr in addrs_list:
#                     if addr.family == socket.AF_INET:
#                         ipv4_addrs.append(addr.address)
#                     elif addr.family == socket.AF_INET6:
#                         ipv6_addrs.append(addr.address)
                
#                 # Skip interfaces with no IP addresses
#                 if not (ipv4_addrs or ipv6_addrs):
#                     continue
                
#                 # Get interface stats with more details
#                 stat = stats[name]
#                 interfaces[name] = {
#                     'ipv4': ipv4_addrs,
#                     'ipv6': ipv6_addrs,
#                     'speed': stat.speed or 0,
#                     'mtu': stat.mtu,
#                     'duplex': getattr(stat, 'duplex', 'unknown'),
#                     'is_up': stat.isup
#                 }
            
#             return interfaces
            
#         except Exception as e:
#             self.handle_error(e, "get_interface_info")
#             return {}
    
#     def _get_network_metrics(self) -> Dict[str, Any]:
#         """Calculate network metrics and rates with packet information."""
#         try:
#             now = time.time()
#             curr_io = psutil.net_io_counters()
#             dt = now - self.last_time
            
#             metrics = {
#                 'bytes_sent': curr_io.bytes_sent,
#                 'bytes_recv': curr_io.bytes_recv,
#                 'packets_sent': curr_io.packets_sent,
#                 'packets_recv': curr_io.packets_recv,
#                 'error_in': curr_io.errin,
#                 'error_out': curr_io.errout,
#                 'drop_in': curr_io.dropin,
#                 'drop_out': curr_io.dropout
#             }
            
#             # Calculate rates if we have valid time difference
#             if dt > 0:
#                 metrics['rates'] = {
#                     'bytes_sent': (curr_io.bytes_sent - self.last_io.bytes_sent) / dt,
#                     'bytes_recv': (curr_io.bytes_recv - self.last_io.bytes_recv) / dt,
#                     'packets_sent': (curr_io.packets_sent - self.last_io.packets_sent) / dt,
#                     'packets_recv': (curr_io.packets_recv - self.last_io.packets_recv) / dt,
#                     'error_in': (curr_io.errin - self.last_io.errin) / dt,
#                     'error_out': (curr_io.errout - self.last_io.errout) / dt
#                 }
                
#                 # Update history with more metrics
#                 for key, value in metrics['rates'].items():
#                     base_key = key.replace('_rate', '')
#                     if base_key in self.history:
#                         self.history[base_key].append(value)
                
#                 # Calculate averages
#                 metrics['averages'] = {
#                     key: sum(values) / len(values)
#                     for key, values in self.history.items()
#                     if values
#                 }
            
#             # Update tracking
#             self.last_io = curr_io
#             self.last_time = now
            
#             return metrics
            
#         except Exception as e:
#             self.handle_error(e, "get_network_metrics")
#             return {'rates': {}, 'averages': {}}
    
#     def render(self) -> Panel:
#         """Render network information panel."""
#         try:
#             table = Table(box=None, expand=True, padding=(0,0))
#             table.add_column("Network", style="cyan", width=12)
#             table.add_column("Usage", ratio=2)
#             table.add_column("Details", style="bright_blue")
            
#             # Get metrics
#             metrics = self._get_network_metrics()
#             interfaces = self._get_interface_info()
            
#             # Display current transfer rates with progress bars
#             if 'rates' in metrics:
#                 rates = metrics['rates']
                
#                 # Download rate with progress bar
#                 if 'bytes_recv' in rates:
#                     recv_rate = rates['bytes_recv']
#                     recv_percent = min(100, (recv_rate / (10 * 1024 * 1024)) * 100)  # Scale to 10MB/s
#                     table.add_row(
#                         "Download",
#                         create_progress_bar(recv_percent),
#                         f"Current: {format_bytes(recv_rate)}/s | "
#                         f"Total: {format_bytes(metrics['bytes_recv'])}"
#                     )
                
#                 # Upload rate with progress bar
#                 if 'bytes_sent' in rates:
#                     send_rate = rates['bytes_sent']
#                     send_percent = min(100, (send_rate / (10 * 1024 * 1024)) * 100)  # Scale to 10MB/s
#                     table.add_row(
#                         "Upload",
#                         create_progress_bar(send_percent),
#                         f"Current: {format_bytes(send_rate)}/s | "
#                         f"Total: {format_bytes(metrics['bytes_sent'])}"
#                     )
                
#                 # Added packet rates display
#                 if 'packets_recv' in rates and 'packets_sent' in rates:
#                     table.add_row(
#                         "Packets",
#                         f"↓ {rates['packets_recv']:.1f}/s",
#                         f"↑ {rates['packets_sent']:.1f}/s"
#                     )
            
#             # Interface section with separator
#             table.add_row("", "", "")
#             table.add_row("Interfaces", "", "")
            
#             # Display interfaces with more details
#             for name, info in interfaces.items():
#                 # Format IP addresses
#                 ipv4 = ', '.join(info['ipv4'][:2])
#                 if len(info['ipv4']) > 2:
#                     ipv4 += f" (+{len(info['ipv4'])-2})"
                    
#                 speed_text = f"{info['speed']} Mbps" if info['speed'] else "Auto"
#                 duplex_text = f" ({info['duplex']})" if info['duplex'] != 'unknown' else ""
                
#                 table.add_row(
#                     name[:12],
#                     f"Speed: {speed_text}{duplex_text}",
#                     f"IPv4: {ipv4} | MTU: {info['mtu']}"
#                 )
            
#             # Enhanced error information display
#             if any(metrics.get(key, 0) > 0 for key in ['error_in', 'error_out', 'drop_in', 'drop_out']):
#                 table.add_row("", "", "")
#                 table.add_row(
#                     "Errors",
#                     f"In: {metrics['error_in']} | Out: {metrics['error_out']}",
#                     f"Drops - In: {metrics.get('drop_in', 0)} | "
#                     f"Out: {metrics.get('drop_out', 0)}"
#                 )
            
#             return Panel(table, title="Network Monitor", border_style="cyan")
            
#         except Exception as e:
#             self.handle_error(e, "render")
#             return Panel(Text("Network Monitor Error", style="red"))




class DiskMonitor(BaseMonitor):
    """Disk monitor optimized for Linux systems."""
    
    def __init__(self):
        """Initialize disk monitor with I/O tracking."""
        super().__init__()
        self.last_io = psutil.disk_io_counters()
        self.last_time = time.time()
        self.history = {
            'read_bytes': deque(maxlen=10),
            'write_bytes': deque(maxlen=10),
            'busy_time': deque(maxlen=10)
        }
    
    def _get_disk_io(self) -> Dict[str, Any]:
        """Calculate disk I/O metrics with history."""
        try:
            now = time.time()
            curr_io = psutil.disk_io_counters()
            dt = now - self.last_time
            
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
            if dt > 0:
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
                if hasattr(curr_io, 'busy_time'):
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
                        except Exception:
                            pass
                    
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
            return Panel(Text("Disk Monitor Error", style="red"))
        
        
class ServiceMonitor(BaseMonitor):
    """Service monitor optimized for Linux systems using systemd."""
    
    def __init__(self):
        """Initialize service monitor with important services list."""
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
                    'status_code': int(status.get('ExecMainStatus', 0)),
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
            return Panel(Text("Service Monitor Error", style="red"))



class GPUMonitor(BaseMonitor):
    """GPU monitor optimized for NVIDIA cards with graceful fallback."""
    
    def __init__(self):
        super().__init__()
        self.history = {
            'usage': deque(maxlen=30),
            'temp': deque(maxlen=30)
        }
        self.has_nvidia = check_gpu_available()  # Use the existing function
    
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
                values = result.stdout.strip().split(',')
                # Create base metrics
                metrics = {
                    'name': values[0].strip(),
                    'usage': float(values[1]) if values[1] != '[N/A]' else 0,
                    'temp': float(values[2]) if values[2] != '[N/A]' else 0,
                    'memory_used': float(values[3]) if values[3] != '[N/A]' else 0,
                    'memory_total': float(values[4]) if values[4] != '[N/A]' else 0,
                    'power_draw': float(values[5]) if values[5] != '[N/A]' else 0,
                    'power_limit': float(values[6]) if values[6] != '[N/A]' else 0,
                    'clock_gpu': float(values[7]) if values[7] != '[N/A]' else 0,
                    'clock_gpu_max': float(values[8]) if values[8] != '[N/A]' else 0,
                    'clock_mem': float(values[9]) if values[9] != '[N/A]' else 0,
                    'clock_mem_max': float(values[10]) if values[10] != '[N/A]' else 0,
                    'fan_speed': float(values[11]) if values[11] != '[N/A]' else 0,
                    'perf_state': values[12].strip() if values[12] != '[N/A]' else 'P?'
                }
                
                # Calculate memory percentage
                metrics['memory_percent'] = (metrics['memory_used'] / metrics['memory_total'] * 100) if metrics['memory_total'] > 0 else 0
                
                # Update history
                self.history['usage'].append(metrics['usage'])
                self.history['temp'].append(metrics['temp'])
                
                return metrics
                
        except Exception as e:
            # If any error occurs, log it but don't crash
            self.handle_error(e, "get_gpu_metrics")
            
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
            return Panel(Text("GPU Monitor - No data available", style="yellow"), title="GPU Monitor", border_style="yellow")




def create_bar(percentage: float, width: int = 40) -> Text:
    """
    Create a progress bar with color based on percentage.
    Args:
        percentage: Value between 0 and 100
        width: Width of the progress bar in characters
    Returns:
        Rich Text object containing the formatted progress bar
    """
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



class FirewallMonitor(BaseMonitor):
    """Monitor system firewall status and rules with independent layout."""
    
    def __init__(self):
        super().__init__()
        self.last_blocked = self._get_blocked_count()
        self.last_check_time = time.time()
        self.rules_cache = None
        self.rules_cache_time = 0
        self.rules_cache_ttl = 5  # Cache TTL in seconds
    
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
        except Exception:
            pass
        
        # Check nftables blocks
        try:
            cmd = ['nft', 'list', 'ruleset']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'drop' in line or 'reject' in line:
                        blocked += 1
        except Exception:
            pass
            
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
        except Exception:
            pass
            
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
        except Exception:
            pass
        
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
        except Exception:
            pass
        
        # Update cache
        self.rules_cache = rules
        self.rules_cache_time = current_time
        return rules
    
    def render(self) -> Panel:
        """Render firewall status panel."""
        # Create firewall-specific table
        table = Table(box=None, expand=True, padding=(0,0))
        table.add_column("Firewall", style="cyan", width=12, no_wrap=True)
        table.add_column("Status", ratio=2)
        table.add_column("Details", style="bright_blue")
        
        # Calculate blocked rate
        current_blocked = self._get_blocked_count()
        current_time = time.time()
        blocked_rate = (current_blocked - self.last_blocked) / (current_time - self.last_check_time)
        
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
            create_bar(min(blocked_rate * 10, 100)),
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
    
        
        
        
class SensorMonitor(BaseMonitor):
    """Temperature and fan sensor monitor for Linux systems."""
    
    def __init__(self):
        """Initialize sensor monitoring."""
        super().__init__()
        self.history = {
            'temps': deque(maxlen=30),
            'fans': deque(maxlen=30)
        }
        self.warning_temp = 80
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
    
    def _get_temp_status(self, temp: float) -> str:
        """Determine temperature status based on thresholds."""
        if temp >= self.critical_temp:
            return 'critical'
        elif temp >= self.warning_temp:
            return 'warning'
        return 'normal'
    
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
                table.add_row("Power", "", "")  # Fixed: Removed "---"
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
                table.add_row("--- Fans ---", "", "")
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
            logger.debug(f"Error rendering sensor panel: {e}")
            return Panel(Text("Unable to read sensor data", style="yellow"), title="Sensor Monitor", border_style="red")        



should_exit = False

def restore_terminal():
    """Restore terminal to normal state"""
    # Reset terminal settings
    os.system('stty sane')
    # Clear screen
    os.system('clear')
    # Reset cursor
    print('\033[?25h')  # Show cursor
    print('\033[2J')    # Clear screen
    print('\033[H')     # Move cursor to home position

def setup_signal_handlers():
    """Set up CTRL+C handler with proper terminal cleanup"""
    def clean_exit(sig, frame):
        # First restore terminal
        restore_terminal()
        # Then exit cleanly
        sys.exit(0)
    
    # Register the clean exit handler
    signal.signal(signal.SIGINT, clean_exit)
    signal.signal(signal.SIGTERM, clean_exit)


class SystemMonitorApp(App):
    """Main system monitoring application."""
    
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
        super().__init__()
        self.start_time = datetime.now()
        self.prevent_exit_confirmations = True
        Screen.DIALOG_CLASSES = []  # Disable all popups
    
    def on_key(self, event) -> None:
        """Handle CTRL+C with clean exit"""
        if event.key == "ctrl+c":
            self.exit()
    
    def action_quit(self) -> None:
        """Clean exit when quitting"""
        self.exit()
    
    def _on_exit(self) -> None:
        """Ensure terminal is restored on exit"""
        restore_terminal()
    
    def compose(self) -> ComposeResult:
        # Header info
        header_text = [
            f"OS: {PLATFORM_INFO['system'].title()} {PLATFORM_INFO['release']}",
            f"Python: {PLATFORM_INFO['python_version']}",
            f"Cores: {psutil.cpu_count()}",
            f"RAM: {format_bytes(psutil.virtual_memory().total)}"
        ]
        yield Header(" | ".join(header_text))
        
        # Left side monitors
        left_container = Container(id="left-column")
        left_container.compose_add_child(CPUMonitor())


        left_container.compose_add_child(GPUMonitor())
        
        left_container.compose_add_child(ServiceMonitor())
        yield left_container
        
        # Right side monitors  
        right_container = Container(id="right-column")
        right_container.compose_add_child(MemoryMonitor())
        
        right_container.compose_add_child(DiskMonitor())
        
        right_container.compose_add_child(NetworkMonitor())
        right_container.compose_add_child(FirewallMonitor())

        right_container.compose_add_child(SensorMonitor())
        yield right_container
        
        # Simple footer, no quit message popup
        yield Footer()




def main():
    """Run monitor with clean terminal exit"""
    try:
        # Set up clean exit handlers first
        setup_signal_handlers()
        
        # Run app
        app = SystemMonitorApp()
        app.run()
        
    except Exception as e:
        # Clean up on any error
        restore_terminal()
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        # Final cleanup
        restore_terminal()

if __name__ == "__main__":
    main()
