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

# Create logs directory
os.makedirs('logs', exist_ok=True)

# Configure logging with file output only for debug messages
debug_handler = logging.FileHandler(
    f'logs/system_monitor_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
)
debug_handler.setLevel(logging.DEBUG)
debug_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
)

# Create console handler for errors only
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.ERROR)  # Only show errors on console
console_handler.setFormatter(
    logging.Formatter('%(levelname)s: %(message)s')
)

# Set up root logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(debug_handler)
logger.addHandler(console_handler)

# Remove any existing handlers to avoid duplicates
for handler in logger.handlers[:]:
    if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
        logger.removeHandler(handler)

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
    'cpu': 1.5,      # CPU needs fast updates
    'memory': 1.5,   # Memory is fast to check
    'network': 1.5,  # Network needs frequent updates
    'disk': 1.5,     # Disk can be slower
    'gpu': 1.5,      # GPU can be slower
    'sensors': 2.0,  # Sensors are slow to read
    'services': 5.0  # Services don't change often
}

# Number of CPU cores to display per line
CORES_PER_LINE = 4


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


def format_bytes(bytes_value: float) -> str:
    """Optimized bytes formatting without try/except"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:6.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:6.1f}TB"

def create_progress_bar(percentage: float, width: int = 40, color: str = None) -> Text:
    """Optimized progress bar creation without unnecessary formatting"""
    percentage = max(0, min(100, percentage))
    filled = int(width * percentage / 100)
    remainder = width - filled

    if color is None:
        color = ("green" if percentage < 50 else
                "yellow" if percentage < 75 else
                "red" if percentage < 90 else
                "bright_red")

    # Create bar components once
    filled_section = Text('■' * filled, color)
    empty_section = Text('·' * remainder, "bright_black")
    percentage_text = Text(f" {percentage:5.1f}%", color)

    # Combine sections efficiently
    return filled_section + empty_section + percentage_text


class BaseMonitor(Static):
    def __init__(self):
        super().__init__()
        self.error_count = 0
        self.max_errors = 3

    def handle_error(self, error: Exception, context: str) -> None:
        self.error_count += 1
        logger.error(f"Error in {self.__class__.__name__} ({context}): {error}")
        if self.error_count >= self.max_errors:
            logger.warning(f"{self.__class__.__name__} experiencing repeated errors")

    def get_interval(self) -> float:
        monitor_type = self.__class__.__name__.lower().replace('monitor', '')
        return MONITOR_INTERVALS.get(monitor_type, 1.0)

    async def _do_refresh(self) -> None:
        try:
            rendered = self.render()
            self.update(rendered)
        except Exception as e:
            self.handle_error(e, "refresh")

    def on_mount(self) -> None:
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

    def __init__(self):
        super().__init__()
        self.update_interval = MONITOR_INTERVALS['cpu']
        self._get_processor_name()  # Cache processor name on init

    def _get_processor_name(self) -> str:
        """Get the processor name with caching."""
        current_time = time.time()

        if (SYSTEM_INFO_CACHE['processor_name'] is None or
            current_time - SYSTEM_INFO_CACHE['processor_cache_time'] > CACHE_TTL['processor']):

            try:
                if sys.platform == "linux":
                    with open("/proc/cpuinfo", "r") as f:
                        for line in f:
                            if "model name" in line:
                                SYSTEM_INFO_CACHE['processor_name'] = line.split(":")[1].strip()
                                break
                SYSTEM_INFO_CACHE['processor_cache_time'] = current_time
            except Exception:
                SYSTEM_INFO_CACHE['processor_name'] = ""

        return SYSTEM_INFO_CACHE['processor_name'] or ""

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
        """Create a color-coded bar with percentage."""
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

    def on_mount(self) -> None:
        """Set up refresh interval."""
        self.set_interval(self.update_interval, self.refresh)

    def render(self) -> Panel:
        # Get CPU metrics efficiently
        cpu_percent = psutil.cpu_percent(percpu=True)
        freq = psutil.cpu_freq()
        times = psutil.cpu_times_percent()
        load = psutil.getloadavg()

        # Calculate total CPU usage
        total = sum(cpu_percent) / len(cpu_percent)

        # Create main table
        table = Table(box=None, expand=True, padding=(0,0))

        # Create metrics header table
        metrics_table = Table(box=None, expand=True, padding=(0,0))
        metrics_table.add_column("Total CPU", justify="left", style="cyan", ratio=1)
        metrics_table.add_column("System Info", justify="left", style="cyan", ratio=1)

        # Add total CPU usage with visual bar
        metrics_table.add_row(
            Text("Total: ") + self.create_colored_bar(total),
            Text(f"Freq: {int(freq.current)}MHz | Load: {load[0]:5.2f}")
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

        # Create cores table
        cores_table = Table(box=None, expand=True, padding=(0,0))

        # Add columns based on CORES_PER_LINE
        for _ in range(CORES_PER_LINE):
            cores_table.add_column(ratio=1)

        # Add core rows
        for i in range(0, len(cpu_percent), CORES_PER_LINE):
            cores_in_row = self.create_core_row(i, cpu_percent)
            # Pad row if needed
            while len(cores_in_row) < CORES_PER_LINE:
                cores_in_row.append("")
            cores_table.add_row(*cores_in_row)

        # Add cores grid to main table
        table.add_row(cores_table)

        # Get cached processor name
        processor_name = self._get_processor_name()
        title = f"CPU Monitor - {processor_name}" if processor_name else "CPU Monitor"

        return Panel(
            table,
            title=title,
            border_style="blue"
        )






class MemoryMonitor(BaseMonitor):
    """Memory usage monitor with independent layout."""

    def __init__(self):
        super().__init__()
        self.update_interval = MONITOR_INTERVALS['memory']

    def on_mount(self) -> None:
        self.set_interval(self.update_interval, self.refresh)

    def render(self) -> Panel:
        # Create monitor-specific table
        table = Table(box=None, expand=True, padding=(0,0))
        table.add_column("Memory", style="cyan", width=12, no_wrap=True)
        table.add_column("Usage", ratio=2)
        table.add_column("Details", style="bright_blue")

        # Get memory metrics once
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Precalculate common values
        cache_percent = (vm.cached / vm.total) * 100 if hasattr(vm, 'cached') else 0

        # RAM Usage
        table.add_row(
            "RAM",
            create_progress_bar(vm.percent),
            f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}"
        )

        # Cache Usage
        if hasattr(vm, 'cached'):
            table.add_row(
                "Cache",
                create_progress_bar(cache_percent),
                f"Cached: {format_bytes(vm.cached)} | Buffers: {format_bytes(vm.buffers)}"
            )

        # Effective Memory
        if hasattr(vm, 'available'):
            effective_used = vm.total - vm.available
            if hasattr(vm, 'cached'):
                effective_used -= (vm.cached + vm.buffers)
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






class DiskMonitor(BaseMonitor):
    """Disk monitor optimized for Linux systems."""

    def __init__(self):
        super().__init__()
        self.last_io = psutil.disk_io_counters()
        self.last_time = time.time()
        self.history = {
            'read_bytes': deque(maxlen=10),
            'write_bytes': deque(maxlen=10),
            'busy_time': deque(maxlen=10)
        }

    def _get_disk_io(self) -> Dict[str, Any]:
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

        if dt > 0:
            for key in ['read_bytes', 'write_bytes', 'read_count', 'write_count']:
                curr_val = getattr(curr_io, key)
                prev_val = getattr(self.last_io, key)
                metrics['rates'][key] = (curr_val - prev_val) / dt

            # Update history
            for key in ['read_bytes', 'write_bytes']:
                self.history[key].append(metrics['rates'][key])

            if hasattr(curr_io, 'busy_time'):
                busy_time = curr_io.busy_time - self.last_io.busy_time
                busy_percent = min(100.0, busy_time / (dt * 1000) * 100)
                self.history['busy_time'].append(busy_percent)
                metrics['busy_percent'] = busy_percent

        self.last_io = curr_io
        self.last_time = now

        return metrics

    def _get_partitions(self) -> List[Dict[str, Any]]:
        """Get cached partition information."""
        current_time = time.time()

        if (SYSTEM_INFO_CACHE['partition_info'] is None or
            current_time - SYSTEM_INFO_CACHE['partition_cache_time'] > CACHE_TTL['partitions']):

            partitions = []
            for part in psutil.disk_partitions(all=False):
                if part.fstype in {'squashfs', 'efivarfs'} or \
                   '/boot' in part.mountpoint or \
                   '/snap' in part.mountpoint:
                    continue

                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    dev_name = os.path.basename(part.device)
                    sys_block_path = f"/sys/block/{dev_name}"

                    additional_info = {}
                    if os.path.exists(sys_block_path):
                        # Get scheduler
                        scheduler_path = f"{sys_block_path}/queue/scheduler"
                        if os.path.exists(scheduler_path):
                            with open(scheduler_path) as f:
                                additional_info['scheduler'] = f.read().strip()

                        # Get rotational status
                        rotational_path = f"{sys_block_path}/queue/rotational"
                        if os.path.exists(rotational_path):
                            with open(rotational_path) as f:
                                additional_info['is_ssd'] = f.read().strip() == '0'

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
                    logger.debug(f"Error getting partition info for {part.mountpoint}: {e}")
                    continue

            SYSTEM_INFO_CACHE['partition_info'] = partitions
            SYSTEM_INFO_CACHE['partition_cache_time'] = current_time

        return SYSTEM_INFO_CACHE['partition_info']

    def render(self) -> Panel:
            table = Table(box=None, expand=True, padding=(0,0))
            table.add_column("Disk", style="cyan", width=12)
            table.add_column("Usage", ratio=2)
            table.add_column("Details", style="bright_blue")

            io_metrics = self._get_disk_io()
            partitions = self._get_partitions()

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
                usage_bar = create_progress_bar(part['percent'])

                details = [
                    f"{format_bytes(part['used'])} / {format_bytes(part['total'])}",
                    f"({part['fstype']})"
                ]

                if 'is_ssd' in part:
                    details.append("SSD" if part['is_ssd'] else "HDD")

                if 'scheduler' in part:
                    details.append(f"Scheduler: {part['scheduler']}")

                table.add_row(
                    name[:12],
                    usage_bar,
                    " | ".join(details)
                )

            return Panel(table, title="Disk Monitor", border_style="magenta")









class ServiceMonitor(BaseMonitor):
    """Service monitor optimized for Linux systems using systemd."""

    def __init__(self):
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

    def _get_service_status(self, service: str) -> Optional[Dict[str, Any]]:
        cmd = ['systemctl', 'show', f'{service}.service',
              '--property=ActiveState,SubState,LoadState,UnitFileState,'
              'Description,StateChangeTimestamp,ExecMainStatus']

        try:
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
            logger.debug(f"Error getting service status for {service}: {e}")
        return None

    def _get_all_services(self) -> Dict[str, Any]:
        current_time = time.time()

        if (current_time - SYSTEM_INFO_CACHE.get('service_cache_time', 0) > CACHE_TTL['services']):
            services = {}
            stats = {
                'total': len(self.important_services),
                'running': 0,
                'stopped': 0,
                'failed': 0,
                'other': 0
            }

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
                    else:
                        stats['other'] += 1

            SYSTEM_INFO_CACHE['service_status'] = {
                'services': services,
                'stats': stats,
                'timestamp': current_time
            }
            SYSTEM_INFO_CACHE['service_cache_time'] = current_time

        return SYSTEM_INFO_CACHE['service_status']

    def render(self) -> Panel:
        table = Table(box=None, expand=True, padding=(0,0))
        table.add_column("Service", style="cyan", width=12)
        table.add_column("Status", ratio=2)
        table.add_column("Details", style="bright_blue")

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

        # Sort and show services
        sorted_services = sorted(
            services.values(),
            key=lambda x: (
                x['state'] != 'active',
                x['state'] == 'failed',
                x['name']
            )
        )

        for service in sorted_services:
            color = {
                'active': "green",
                'failed': "red",
                'inactive': "yellow"
            }.get(service['state'], "bright_black")

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










class GPUMonitor(BaseMonitor):
    def __init__(self):
        super().__init__()
        self.last_full_query_time = 0
        self.full_query_interval = 1
        self.history = {
            'usage': deque(maxlen=60),
            'temp': deque(maxlen=60),
            'memory': deque(maxlen=60)
        }

    def create_gpu_bar(self, percentage: float, width: int = 35) -> Text:
        """Create a green progress bar with exact spacing."""
        filled = int((width * percentage) / 100)
        return Text("■" * filled + "·" * (width - filled), "green")

    def _get_gpu_metrics(self) -> Dict[str, Any]:
        try:
            cmd = [
                'nvidia-smi',
                '--query-gpu=name,utilization.gpu,temperature.gpu,' +
                'memory.used,memory.total,power.draw,' +
                'clocks.current.graphics,clocks.current.memory,' +
                'pcie.link.gen.current,pcie.link.width.current,' +
                'vbios_version,driver_version',
                '--format=csv,noheader,nounits'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)

            if result.returncode == 0:
                values = result.stdout.strip().split(',')
                return {
                    'name': values[0].strip(),
                    'usage': float(values[1]) if values[1] != '[N/A]' else 0,
                    'temperature': float(values[2]) if values[2] != '[N/A]' else 0,
                    'memory_used': float(values[3]) if values[3] != '[N/A]' else 0,
                    'memory_total': float(values[4]) if values[4] != '[N/A]' else 1,
                    'power_draw': float(values[5]) if values[5] != '[N/A]' else 0,
                    'clock_gpu': float(values[6]) if values[6] != '[N/A]' else 0,
                    'clock_mem': float(values[7]) if values[7] != '[N/A]' else 0,
                    'pcie_gen': values[8].strip() if values[8] != '[N/A]' else '?',
                    'pcie_width': values[9].strip() if values[9] != '[N/A]' else '?',
                    'bios': values[10].strip() if values[10] != '[N/A]' else 'N/A',
                    'driver': values[11].strip() if values[11] != '[N/A]' else 'N/A'
                }
        except Exception as e:
            logger.error(f"GPU metrics error: {e}")
            return {}

    def render(self) -> Panel:
        """Render GPU monitor with exact formatting."""
        table = Table(box=None, expand=True, padding=(0,0))
        table.add_column("Label", style="cyan", width=12)
        table.add_column("Value", ratio=1)
        table.add_column("Details", justify="right", style="bright_magenta")

        metrics = self._get_gpu_metrics()
        if not metrics:
            return Panel(Text("GPU Monitor Error", style="red"))

        # Name and version info
        table.add_row(
            Text("Name", style="cyan"),
            Text(metrics['name']),
            Text(f"BIOS: {metrics['bios']}", style="bright_magenta")
        )

        # Driver version on next line
        table.add_row(
            "",
            "",
            Text(f"Driver: {metrics['driver']}", style="bright_magenta")
        )

        # Usage with power
        table.add_row(
            Text("Usage", style="cyan"),
            self.create_gpu_bar(metrics['usage']),
            Text(f"{metrics['usage']:.1f}% | {metrics['power_draw']:.1f}W", style="bright_magenta")
        )

        # Temperature
        table.add_row(
            Text("Temperature", style="cyan"),
            self.create_gpu_bar(metrics['temperature']),
            Text(f"{metrics['temperature']:.1f}°C", style="bright_magenta")
        )

        # Memory
        memory_percent = (metrics['memory_used'] / metrics['memory_total'] * 100)
        table.add_row(
            Text("Memory", style="cyan"),
            self.create_gpu_bar(memory_percent),
            Text(f"{metrics['memory_used']:.0f}MB / {metrics['memory_total']:.0f}MB", style="bright_magenta")
        )

        # Clock speeds
        table.add_row(
            Text("Core Clock", style="cyan"),
            Text(f"GPU: {metrics['clock_gpu']:.0f}MHz"),
            ""
        )

        table.add_row(
            Text("Mem Clock", style="cyan"),
            Text(f"Memory: {metrics['clock_mem']:.0f}MHz"),
            ""
        )

        # PCIe status
        table.add_row(
            Text("PCIe", style="cyan"),
            Text(f"Gen {metrics['pcie_gen']} x{metrics['pcie_width']}"),
            Text("P-State: P?", style="bright_magenta")
        )

        return Panel(table, title="GPU Monitor", border_style="yellow")



class FirewallMonitor(BaseMonitor):
    """Monitor system firewall status and rules with independent layout."""

    def __init__(self):
        super().__init__()
        self.last_blocked = self._get_blocked_count()
        self.last_check_time = time.time()
        self.rules_cache = []  # Initialize as empty list
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
            create_progress_bar(min(blocked_rate * 10, 100)),  # Changed from create_bar to create_progress_bar
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
    """Temperature and fan sensor monitor."""

    def __init__(self):
        super().__init__()
        self.warning_temp = 80
        self.critical_temp = 90

    def _get_temp_status(self, temp: float) -> str:
        if temp >= self.critical_temp:
            return 'critical'
        elif temp >= self.warning_temp:
            return 'warning'
        return 'normal'

    def _get_sensor_data(self) -> Dict[str, Any]:
        current_time = time.time()

        if (SYSTEM_INFO_CACHE.get('sensor_data') is None or
            current_time - SYSTEM_INFO_CACHE.get('sensor_cache_time', 0) > CACHE_TTL['sensors']):

            data = {
                'temperatures': [],
                'fans': [],
                'voltages': [],
                'power': []
            }

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

                # Get power sensors from hwmon
                hwmon_path = '/sys/class/hwmon'
                if os.path.exists(hwmon_path):
                    for hwmon in os.listdir(hwmon_path):
                        current_path = os.path.join(hwmon_path, hwmon)
                        if not os.path.isdir(current_path):
                            continue

                        name_path = os.path.join(current_path, 'name')
                        if not os.path.exists(name_path):
                            continue

                        with open(name_path) as f:
                            sensor_name = f.read().strip()

                        for entry in os.listdir(current_path):
                            if entry.startswith('power') and entry.endswith('_input'):
                                power_path = os.path.join(current_path, entry)
                                try:
                                    with open(power_path) as f:
                                        power_value = float(f.read()) / 1000000
                                        data['power'].append({
                                            'name': f"{sensor_name}_{entry[:-6]}",
                                            'value': power_value
                                        })
                                except (IOError, OSError, ValueError):
                                    continue

                SYSTEM_INFO_CACHE['sensor_data'] = data
                SYSTEM_INFO_CACHE['sensor_cache_time'] = current_time

            except Exception as e:
                logger.debug(f"Error getting sensor data: {e}")
                return data

        return SYSTEM_INFO_CACHE['sensor_data']

    def render(self) -> Panel:
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
            status_color = {
                'normal': "green",
                'warning': "yellow",
                'critical': "red"
            }[temp['status']]

            details = [f"Current: {temp['current']:4.1f}°C"]
            if temp['high']:
                details.append(f"High: {temp['high']:4.1f}°C")
            if temp['critical']:
                details.append(f"Critical: {temp['critical']:4.1f}°C")

            max_temp = temp['critical'] or temp['high'] or 100
            temp_percent = (temp['current'] / max_temp) * 100

            table.add_row(
                temp['name'][:12],
                create_progress_bar(min(100, temp_percent), color=status_color),
                " | ".join(details)
            )

        # Show power readings
        if sensor_data['power']:
            if sensor_data['temperatures']:
                table.add_row("", "", "")
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

        return Panel(table, title="Sensor Monitor", border_style="red")














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
        self.prevent_exit_confirmations = True
        Screen.DIALOG_CLASSES = []

    def on_mount(self) -> None:
        """Handle mount event - removed invalid super() call"""
        try:
            logger.debug("SystemMonitorApp mounting")
            # Initialize anything needed at mount time here
            logger.debug("SystemMonitorApp mount complete")
        except Exception as e:
            logger.critical(f"Error during app mount: {e}", exc_info=True)
            raise

    def on_key(self, event) -> None:
        """Handle CTRL+C with clean exit"""
        try:
            if event.key == "ctrl+c":
                logger.info("CTRL+C detected, exiting")
                self.exit()
        except Exception as e:
            logger.error(f"Error in key handler: {e}", exc_info=True)

    def action_quit(self) -> None:
        """Clean exit when quitting"""
        try:
            logger.info("Quit action triggered")
            self.exit()
        except Exception as e:
            logger.error(f"Error during quit: {e}", exc_info=True)
            sys.exit(1)

    def _on_exit(self) -> None:
        """Ensure terminal is restored on exit"""
        try:
            logger.info("Running exit cleanup")
            restore_terminal()
        except Exception as e:
            logger.error(f"Error during terminal restore: {e}", exc_info=True)

    def compose(self) -> ComposeResult:
        try:
            logger.debug("Starting compose")
            # Header info
            header_text = [
                f"OS: {PLATFORM_INFO['system'].title()} {PLATFORM_INFO['release']}",
                f"Python: {PLATFORM_INFO['python_version']}",
                f"Cores: {psutil.cpu_count()}",
                f"RAM: {format_bytes(psutil.virtual_memory().total)}"
            ]
            logger.debug(f"Created header text: {header_text}")
            yield Header(" | ".join(header_text))

            # Left side monitors
            logger.debug("Creating left container")
            left_container = Container(id="left-column")
            left_container.compose_add_child(CPUMonitor())
            left_container.compose_add_child(GPUMonitor())
            left_container.compose_add_child(ServiceMonitor())
            yield left_container
            logger.debug("Left container complete")

            # Right side monitors
            logger.debug("Creating right container")
            right_container = Container(id="right-column")
            right_container.compose_add_child(MemoryMonitor())
            right_container.compose_add_child(DiskMonitor())
            right_container.compose_add_child(NetworkMonitor())
            right_container.compose_add_child(FirewallMonitor())
            right_container.compose_add_child(SensorMonitor())
            yield right_container
            logger.debug("Right container complete")

            # Footer
            yield Footer()
            logger.debug("Compose completed successfully")

        except Exception as e:
            logger.critical(f"Critical error in compose: {e}", exc_info=True)
            raise



    def run(self, *args, **kwargs):
        """Override run to add error handling."""
        try:
            logger.info("Starting app run")
            result = super().run(*args, **kwargs)
            logger.info("App run completed normally")
            return result
        except Exception as e:
            logger.critical(f"Critical error in app run: {e}", exc_info=True)
            restore_terminal()
            raise




def main():
    """Run monitor with complete error logging"""
    logger.info("Entering main function")
    try:
        # Set up clean exit handlers first
        setup_signal_handlers()
        logger.info("Signal handlers set up")

        # Create and run app
        logger.info("Creating SystemMonitorApp instance")
        app = SystemMonitorApp()
        logger.info("Starting app.run()")
        app.run()

    except Exception as e:
        logger.critical(f"Critical error in main: {e}", exc_info=True)
        restore_terminal()
        sys.exit(1)
    finally:
        logger.info("Exiting main function")
        restore_terminal()

if __name__ == "__main__":
    try:
        logger.info("Starting main()")
        main()
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
        sys.exit(1)