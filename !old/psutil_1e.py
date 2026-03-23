#!/usr/bin/env python3

import os
import time
import signal
import sys
import subprocess
import logging
from typing import Dict, Optional
from collections import deque
import psutil
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Grid, Container
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

CORES_PER_LINE = 4  # Modify this value to change number of cores per line (1, 2, 4, 6, 8, etc)

# Add this right after the imports

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
    """Convert bytes to human readable format with consistent width."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:6.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:6.1f}TB"

def format_speed(speed_mbps: float) -> str:
    """Format network speed with proper units."""
    if speed_mbps >= 1000:
        return f"{speed_mbps/1000:.1f} Gb/s"
    return f"{speed_mbps:.1f} Mb/s"

def create_bar(percentage: float, width: int = 40) -> Text:
    """Create a progress bar with color based on percentage."""
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

class BaseMonitor(Static):
    """Base monitor class with common functionality."""
    
    DEFAULT_CSS = """
    BaseMonitor {
        height: auto;
        margin: 0;
        padding: 0;
    }
    """
    
    def create_table(self) -> Table:
        """Create a consistent table format for all monitors."""
        table = Table(box=None, expand=True, padding=(0,0), show_header=True)
        table.add_column("Metric", style="cyan", width=12, no_wrap=True)
        table.add_column("Usage", ratio=2)
        table.add_column("Details", style="bright_blue")
        return table
    
    def format_detail(self, *items: tuple) -> str:
        """Format detail string consistently."""
        return " | ".join(str(item) for item in items if item)







def get_freq_mhz(hz: float) -> float:
    """
    Get correct CPU frequency in MHz.
    Args:
        hz: Raw frequency value from psutil.cpu_freq()
    Returns:
        Frequency in MHz with proper scaling
    """
    # psutil.cpu_freq() returns MHz already, no need to convert
    return hz








class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor with processor name display."""
    
    def on_mount(self) -> None:
        """Initialize monitor and get processor details on mount."""
        self.set_interval(0.5, self.refresh)
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
            cores_table.add_column(f"Core Column {i}", ratio=1)
        
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
        title = f"CPU Details ({len(cpu_percent)} cores"
        if self.processor_name:
            title += f" - {self.processor_name}"
        title += ")"

        return Panel(
            table,
            title=title,
            border_style="blue"
        )



class MemoryMonitor(BaseMonitor):
    """Memory usage monitor."""
    
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        table = self.create_table()
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        table.add_row(
            "RAM",
            create_bar(vm.percent),
            f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}"
        )
        
        cache_percent = (vm.cached / vm.total) * 100
        table.add_row(
            "Cache",
            create_bar(cache_percent),
            f"Cached: {format_bytes(vm.cached)} | Buffers: {format_bytes(vm.buffers)}"
        )
        
        table.add_row(
            "Swap",
            create_bar(swap.percent),
            f"Used: {format_bytes(swap.used)} / Total: {format_bytes(swap.total)}"
        )

        return Panel(table, title="Memory Details", border_style="green")

class DiskMonitor(BaseMonitor):
    """Disk I/O and usage monitor."""
    
    def __init__(self):
        super().__init__()
        self.last_io = psutil.disk_io_counters()
        self.last_time = time.time()
        self.history = {'read': deque(maxlen=5), 'write': deque(maxlen=5)}

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def get_io_speeds(self) -> Dict[str, float]:
        now = time.time()
        curr_io = psutil.disk_io_counters()
        dt = now - self.last_time
        speeds = {'read': 0, 'write': 0}
        
        if dt > 0:
            speeds['read'] = (curr_io.read_bytes - self.last_io.read_bytes) / dt
            speeds['write'] = (curr_io.write_bytes - self.last_io.write_bytes) / dt
            for k in speeds:
                self.history[k].append(speeds[k])
                
        self.last_io, self.last_time = curr_io, now
        return {k: sum(self.history[k])/len(self.history[k]) for k in speeds}

    def render(self) -> Panel:
        table = self.create_table()
        speeds = self.get_io_speeds()

        table.add_row(
            "Disk I/O",
            f"Read: {format_bytes(speeds['read'])}/s",
            f"Write: {format_bytes(speeds['write'])}/s"
        )

        for part in psutil.disk_partitions(all=False):
            if part.fstype.lower() not in ['squashfs', 'efivarfs'] and '/boot' not in part.mountpoint:
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    table.add_row(
                        os.path.basename(part.mountpoint) or '/',
                        create_bar(usage.percent),
                        f"{format_bytes(usage.used)} / {format_bytes(usage.total)} ({part.fstype})"
                    )
                except Exception:
                    continue

        return Panel(table, title="Disk I/O & Usage", border_style="magenta")

class NetworkMonitor(BaseMonitor):
    """Network usage monitor."""
    
    def __init__(self):
        super().__init__()
        self.last_io = psutil.net_io_counters()
        self.last_time = time.time()
        self.max_seen = {'up': 1, 'down': 1}
        self.history = {'up': deque(maxlen=10), 'down': deque(maxlen=10)}

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def get_speeds(self) -> Dict[str, float]:
        now = time.time()
        curr_io = psutil.net_io_counters()
        dt = now - self.last_time
        speeds = {'up': 0, 'down': 0}
        
        if dt > 0:
            speeds['up'] = (curr_io.bytes_sent - self.last_io.bytes_sent) / dt
            speeds['down'] = (curr_io.bytes_recv - self.last_io.bytes_recv) / dt
            
            for k, v in speeds.items():
                if v > self.max_seen[k]:
                    self.max_seen[k] = v
                self.history[k].append(v)

        self.last_io, self.last_time = curr_io, now
        return speeds

    def render(self) -> Panel:
        table = self.create_table()
        speeds = self.get_speeds()
        max_speed = max(1e6, max(self.max_seen.values()))
        
        table.add_row(
            "Upload",
            create_bar(min(speeds['up'] / max_speed * 100, 100)),
            f"↑ {format_bytes(speeds['up'])}/s | Peak: {format_bytes(self.max_seen['up'])}/s"
        )
        
        table.add_row(
            "Download",
            create_bar(min(speeds['down'] / max_speed * 100, 100)),
            f"↓ {format_bytes(speeds['down'])}/s | Peak: {format_bytes(self.max_seen['down'])}/s"
        )

        for name, addrs in psutil.net_if_addrs().items():
            stats = psutil.net_if_stats().get(name)
            if stats and stats.isup:
                speed_str = format_speed(stats.speed) if stats.speed else "N/A"
                ips = [a.address for a in addrs if a.family in {2, 10}]
                if ips:
                    table.add_row(
                        name[:12],
                        f"Speed: {speed_str}",
                        f"MTU: {stats.mtu} | IP: {', '.join(ips)}"
                    )

        return Panel(table, title="Network Details", border_style="cyan")

class ServiceMonitor(BaseMonitor):
    """Monitor system services and their states."""
    
    def __init__(self):
        super().__init__()
        self.important_services = [
            'ssh', 'NetworkManager', 'systemd-resolved', 'cron', 'rsyslog',
            'dbus', 'udev', 'systemd-timesyncd', 'systemd-logind'
        ]
    
    def on_mount(self) -> None:
        self.set_interval(5.0, self.refresh)
    
    def get_service_status(self, service_name: str) -> dict:
        try:
            cmd = ['systemctl', 'show', f'{service_name}.service', 
                  '--property=ActiveState,SubState,LoadState,UnitFileState']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode == 0:
                status = {}
                for line in result.stdout.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        status[key] = value
                return status
            return None
        except Exception:
            return None
    
    def render(self) -> Panel:
        table = self.create_table()
        
        active_count = 0
        failed_count = 0
        
        for service in self.important_services:
            status = self.get_service_status(service)
            if status:
                if status.get('ActiveState') == 'active':
                    active_count += 1
                elif status.get('ActiveState') == 'failed':
                    failed_count += 1
                
                state = status.get('ActiveState', 'unknown')
                substate = status.get('SubState', 'unknown')
                
                if state == 'active':
                    color = "green"
                elif state == 'failed':
                    color = "red"
                else:
                    color = "yellow"
                
                status_bar = Text('■' * 10, color)
                table.add_row(
                    service[:12],
                    status_bar,
                    f"State: {state}/{substate}"
                )
        
        table.add_row(
            "Summary",
            f"Active: {active_count}",
            f"Failed: {failed_count} | Total: {len(self.important_services)}"
        )
        
        return Panel(table, title="System Services", border_style="blue")

class EnhancedStorageMonitor(BaseMonitor):
    """Enhanced storage monitor with I/O speeds, SMART data, and RAID status."""
    
    def __init__(self):
        super().__init__()
        # Initialize I/O monitoring
        self.last_io = psutil.disk_io_counters()
        self.last_time = time.time()
        # Keep history for smoothing
        self.history = {
            'read': deque(maxlen=5),
            'write': deque(maxlen=5)
        }
        # Initialize SMART and RAID detection
        self.smart_capable = self._check_smartctl()
        self.raid_info = self._check_raid_tools()
    
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)
    
    def get_io_speeds(self) -> Dict[str, float]:
        """Calculate current read/write speeds with smoothing."""
        now = time.time()
        curr_io = psutil.disk_io_counters()
        dt = now - self.last_time
        speeds = {'read': 0, 'write': 0}
        
        if dt > 0:
            # Calculate current speeds
            speeds['read'] = (curr_io.read_bytes - self.last_io.read_bytes) / dt
            speeds['write'] = (curr_io.write_bytes - self.last_io.write_bytes) / dt
            
            # Add to history for smoothing
            for k in speeds:
                self.history[k].append(speeds[k])
            
            # Calculate smoothed speeds
            smoothed = {k: sum(self.history[k])/len(self.history[k]) 
                       for k in speeds}
            
            self.last_io = curr_io
            self.last_time = now
            return smoothed
        return speeds
    
    def _check_smartctl(self) -> bool:
        """Check if smartctl is available."""
        try:
            subprocess.run(['smartctl', '--version'], capture_output=True)
            return True
        except FileNotFoundError:
            return False
    
    def _check_raid_tools(self) -> bool:
        """Check if mdadm is available."""
        try:
            subprocess.run(['mdadm', '--version'], capture_output=True)
            return True
        except FileNotFoundError:
            return False
    
    def get_raid_status(self) -> Optional[Dict[str, str]]:
        """Get RAID array status using mdadm."""
        if not self.raid_info:
            return None
            
        try:
            cmd = ['mdadm', '--detail', '--scan']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode == 0:
                raids = {}
                for line in result.stdout.split('\n'):
                    if line.startswith('ARRAY'):
                        device = line.split()[1]
                        # Get detailed info for this array
                        detail_cmd = ['mdadm', '--detail', device]
                        detail = subprocess.run(detail_cmd, capture_output=True, 
                                             text=True, timeout=1)
                        
                        if detail.returncode == 0:
                            state = None
                            for dline in detail.stdout.split('\n'):
                                if 'State :' in dline:
                                    state = dline.split(':')[1].strip()
                                    break
                            raids[device] = state
                return raids
        except Exception:
            pass
        return None
    
    def get_smart_info(self, device: str) -> Optional[Dict]:
        """Get SMART information for a device."""
        if not self.smart_capable:
            return None
            
        try:
            cmd = ['smartctl', '-A', '-H', '-i', device]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            
            if result.returncode == 0:
                smart_info = {
                    'health': 'PASSED' in result.stdout,
                    'temp': None,
                    'power_on_hours': None,
                    'reallocated_sectors': None,
                    'pending_sectors': None,
                    'start_stop_count': None
                }
                
                for line in result.stdout.split('\n'):
                    if 'Temperature' in line:
                        try:
                            smart_info['temp'] = int(line.split()[-1])
                        except ValueError:
                            pass
                    elif 'Power_On_Hours' in line:
                        try:
                            smart_info['power_on_hours'] = int(line.split()[-1])
                        except ValueError:
                            pass
                    elif 'Reallocated_Sector' in line:
                        try:
                            smart_info['reallocated_sectors'] = int(line.split()[-1])
                        except ValueError:
                            pass
                    elif 'Current_Pending_Sector' in line:
                        try:
                            smart_info['pending_sectors'] = int(line.split()[-1])
                        except ValueError:
                            pass
                    elif 'Start_Stop_Count' in line:
                        try:
                            smart_info['start_stop_count'] = int(line.split()[-1])
                        except ValueError:
                            pass
                            
                return smart_info
        except Exception:
            pass
        return None
    
    def render(self) -> Panel:
        table = self.create_table()
        
        # Show I/O speeds first - most important!
        speeds = self.get_io_speeds()
        table.add_row(
            "Disk I/O",
            create_bar(min(speeds['read'] / 1e6, 100)),
            f"Read: {format_bytes(speeds['read'])}/s | "
            f"Write: {format_bytes(speeds['write'])}/s"
        )
        
        # Show detailed disk information
        for part in psutil.disk_partitions(all=False):
            if part.fstype.lower() not in ['squashfs', 'efivarfs'] and '/boot' not in part.mountpoint:
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    
                    # Get SMART info if available
                    smart_info = None
                    if self.smart_capable and part.device.startswith('/dev/sd'):
                        smart_info = self.get_smart_info(part.device)
                    
                    # Create status line
                    status_details = []
                    if smart_info:
                        if smart_info['temp']:
                            status_details.append(f"Temp: {smart_info['temp']}°C")
                        if smart_info['health']:
                            status_details.append(f"Health: {'OK' if smart_info['health'] else 'CHECK'}")
                        if smart_info['reallocated_sectors'] is not None:
                            status_details.append(f"Reallocated: {smart_info['reallocated_sectors']}")
                    
                    device_name = os.path.basename(part.mountpoint) or '/'
                    table.add_row(
                        device_name,
                        create_bar(usage.percent),
                        f"{format_bytes(usage.used)} / {format_bytes(usage.total)} "
                        f"({part.fstype}) {' | '.join(filter(None, status_details))}"
                    )
                except Exception:
                    continue
        
        # Add RAID status if available
        if raid_status := self.get_raid_status():
            for device, state in raid_status.items():
                table.add_row(
                    "RAID",
                    os.path.basename(device),
                    f"State: {state}"
                )
        
        return Panel(table, title="Storage Monitor", border_style="magenta")

class PowerUsageMonitor(BaseMonitor):
    """Monitor system power usage and states."""
    
    def __init__(self):
        super().__init__()
        self.last_measurement = time.time()
        self.last_energy = self._get_energy_usage()
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)
    
    def _get_energy_usage(self) -> dict:
        usage = {}
        try:
            for domain in os.listdir('/sys/class/powercap/intel-rapl'):
                if domain.startswith('intel-rapl:'):
                    domain_path = f'/sys/class/powercap/intel-rapl/{domain}'
                    with open(f'{domain_path}/name', 'r') as f:
                        name = f.read().strip()
                    with open(f'{domain_path}/energy_uj', 'r') as f:
                        energy = int(f.read().strip())
                    usage[name] = energy
        except Exception:
            pass
        return usage
    
    def render(self) -> Panel:
        table = self.create_table()
        
        current_time = time.time()
        current_energy = self._get_energy_usage()
        
        if self.last_energy and current_energy:
            dt = current_time - self.last_measurement
            if dt > 0:
                for domain, energy in current_energy.items():
                    if domain in self.last_energy:
                        power = (energy - self.last_energy[domain]) / 1e6 / dt
                        table.add_row(
                            domain[:12],
                            create_bar(min(power * 5, 100)),
                            f"{power:.1f} W"
                        )
        
        try:
            battery = psutil.sensors_battery()
            if battery:
                status = "Charging" if battery.power_plugged else "Discharging"
                time_left = ""
                if battery.secsleft > 0:
                    hours = battery.secsleft // 3600
                    minutes = (battery.secsleft % 3600) // 60
                    time_left = f" | {hours}h {minutes}m remaining"
                
                table.add_row(
                    "Battery",
                    create_bar(battery.percent),
                    f"{battery.percent}% ({status}){time_left}"
                )
        except Exception:
            pass
        
        self.last_energy = current_energy
        self.last_measurement = current_time
        
        return Panel(table, title="Power Usage Monitor", border_style="yellow")

class FirewallMonitor(BaseMonitor):
    """Monitor system firewall status and rules."""
    
    def __init__(self):
        super().__init__()
        self.last_blocked = self._get_blocked_count()
        self.last_check_time = time.time()
        self.rules_cache = None
        self.rules_cache_time = 0
        self.rules_cache_ttl = 5
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)
    
    def _get_blocked_count(self) -> int:
        blocked = 0
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
        current_time = time.time()
        
        if (self.rules_cache and 
            current_time - self.rules_cache_time < self.rules_cache_ttl):
            return self.rules_cache
        
        rules = []
        
        try:
            for chain in ['INPUT', 'OUTPUT', 'FORWARD']:
                cmd = ['iptables', '-L', chain, '-n', '-v']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')[2:]
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
        
        self.rules_cache = rules
        self.rules_cache_time = current_time
        return rules
    
    def render(self) -> Panel:
        table = self.create_table()
        
        current_blocked = self._get_blocked_count()
        current_time = time.time()
        blocked_rate = (current_blocked - self.last_blocked) / (current_time - self.last_check_time)
        self.last_blocked = current_blocked
        self.last_check_time = current_time
        
        connections = self._get_active_connections()
        total_connections = sum(connections.values())
        table.add_row(
            "Connections",
            f"Total: {total_connections}",
            f"Active: {connections['ESTABLISHED']} | "
            f"Listening: {connections['LISTEN']}"
        )
        
        table.add_row(
            "Blocked",
            create_bar(min(blocked_rate * 10, 100)),
            f"Rate: {blocked_rate:.1f}/s | Total: {current_blocked}"
        )
        
        rules = self._get_firewall_rules()
        rules_by_target = {'accept': 0, 'drop': 0, 'reject': 0}
        
        for rule in rules:
            target = rule['target'].lower()
            if target in rules_by_target:
                rules_by_target[target] += 1
        
        table.add_row(
            "Rules",
            f"Total: {len(rules)}",
            f"Accept: {rules_by_target['accept']} | "
            f"Drop: {rules_by_target['drop']} | "
            f"Reject: {rules_by_target['reject']}"
        )
        
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



class SensorMonitor(BaseMonitor):
    """Temperature sensors monitor."""
    
    def on_mount(self) -> None:
        # Update every 2 seconds as temperatures don't change rapidly
        self.set_interval(2.0, self.refresh)

    def render(self) -> Panel:
        table = self.create_table()
        
        try:
            # Get all temperature sensors
            for name, sensors in psutil.sensors_temperatures().items():
                for sensor in sensors:
                    # Calculate percentage of max temperature
                    max_temp = sensor.high or 100  # Use 100°C as default max if not specified
                    temp_percent = (sensor.current / max_temp) * 100
                    
                    # Create a bar showing current temperature relative to maximum
                    table.add_row(
                        sensor.label or name,  # Use sensor label or fallback to name
                        create_bar(temp_percent),
                        f"Current: {sensor.current:4.1f}°C | High: {sensor.high:4.1f}°C"
                    )
        except Exception:
            # If no sensors are available, show a message
            table.add_row("No sensors", "N/A", "N/A")

        return Panel(table, title="Temperature Sensors", border_style="red")



class GPUMonitor(BaseMonitor):
    """Complete GPU monitor with model detection and all metrics."""
    
    def __init__(self):
        super().__init__()
        # Store GPU info and history
        self.gpu_info = self._get_gpu_model()
        self.history = {
            'temp': deque(maxlen=10),
            'power': deque(maxlen=10),
            'clocks': deque(maxlen=10)
        }
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)
    
    def _get_gpu_model(self) -> Optional[Dict]:
        """Get detailed GPU information including model."""
        try:
            cmd = ['nvidia-smi', '--query-gpu=gpu_name,vbios_version,serial,uuid',
                  '--format=csv,noheader,nounits']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode == 0:
                name, vbios, serial, uuid = result.stdout.strip().split(',')
                return {
                    'name': name.strip(),
                    'vbios': vbios.strip(),
                    'serial': serial.strip(),
                    'uuid': uuid.strip()
                }
        except Exception:
            pass
        return None

    def get_gpu_info(self) -> Optional[Dict]:
        """Get all GPU metrics including usage, memory, clocks, etc."""
        try:
            # Query all possible GPU metrics
            cmd = ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,'
                  'memory.total,power.draw,fan.speed,clocks.current.graphics,clocks.max.graphics,'
                  'clocks.current.memory,clocks.max.memory,pstate,enforced.power.limit,'
                  'temperature.memory,voltage.graphics',
                  '--format=csv,noheader,nounits']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode == 0:
                vals = [v.strip() for v in result.stdout.strip().split(',')]
                info = {
                    'temp': float(vals[0]),
                    'util': float(vals[1]),
                    'mem_used': float(vals[2]),
                    'mem_total': float(vals[3]),
                    'power': float(vals[4]),
                    'fan': float(vals[5]),
                    'clock': float(vals[6]),
                    'max_clock': float(vals[7]),
                    'mem_clock': float(vals[8]),
                    'max_mem_clock': float(vals[9]),
                    'pstate': vals[10],
                    'power_limit': float(vals[11]),
                    'mem_temp': float(vals[12]) if vals[12] != '[N/A]' else None,
                    'voltage': float(vals[13]) if vals[13] != '[N/A]' else None
                }
                
                # Update history
                self.history['temp'].append(info['temp'])
                self.history['power'].append(info['power'])
                self.history['clocks'].append(info['clock'])
                
                # Add averages
                info['temp_avg'] = sum(self.history['temp']) / len(self.history['temp'])
                info['power_avg'] = sum(self.history['power']) / len(self.history['power'])
                info['clock_avg'] = sum(self.history['clocks']) / len(self.history['clocks'])
                
                return info
        except Exception:
            pass
        return None

    def render(self) -> Panel:
        """Render all GPU information in a detailed panel."""
        table = self.create_table()
        
        try:
            # First get basic GPU info with nvidia-smi
            gpu_cmd = ['nvidia-smi', '--query-gpu=gpu_name,vbios_version,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,fan.speed,clocks.current.graphics,pstate',
                    '--format=csv,noheader,nounits']
            result = subprocess.run(gpu_cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode != 0:
                table.add_row("GPU Status", "No NVIDIA GPU detected", "")
                return Panel(table, title="GPU Status", border_style="yellow")
            
            # Parse GPU info
            gpu_info = result.stdout.strip().split(',')
            gpu_name = gpu_info[0].strip()
            bios = gpu_info[1].strip()
            temp = float(gpu_info[2])
            util = float(gpu_info[3])
            mem_used = float(gpu_info[4])
            mem_total = float(gpu_info[5])
            power = float(gpu_info[6])
            fan = float(gpu_info[7])
            clock = float(gpu_info[8])
            pstate = gpu_info[9].strip()
            
            # Show GPU model and BIOS
            table.add_row(
                "GPU Model",
                gpu_name,
                f"BIOS: {bios}"
            )
            
            # Show GPU usage
            table.add_row(
                "GPU Usage",
                create_bar(util),
                f"Temp: {temp}°C"
            )
            
            # Show Memory usage
            mem_percent = (mem_used / mem_total) * 100
            table.add_row(
                "GPU Memory",
                create_bar(mem_percent),
                f"Used: {mem_used:.0f}MB / {mem_total:.0f}MB"
            )
            
            # Show Power and Fan
            table.add_row(
                "GPU Power",
                f"Draw: {power:.1f}W",
                f"Fan: {fan:.0f}%"
            )
            
            # Show Clock and Performance state
            table.add_row(
                "GPU Clock",
                f"{clock:.0f}MHz",
                f"P-State: {pstate}"
            )
            
            return Panel(table, title=f"GPU Status ({gpu_name})", border_style="yellow")
            
        except Exception as e:
            table.add_row("GPU Status", "Error reading GPU info", str(e))
            return Panel(table, title="GPU Status", border_style="yellow")


class SystemMonitorApp(App):
    """Main application class with independently scrollable columns."""
    
    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;  /* Just 2 columns */
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
    
    Panel {
        height: auto;
        margin: 0;
        border: solid $primary;
    }
    """

    def compose(self) -> ComposeResult:
        """Create child widgets in two columns with stacked panels."""
        yield Header()
        
        # Left column with CPU, Disk, and GPU monitors
        left_container = Container(id="left-column")
        left_container.compose_add_child(CPUMonitor())
        # left_container.compose_add_child(DiskMonitor())
        left_container.compose_add_child(ServiceMonitor())
        left_container.compose_add_child(EnhancedStorageMonitor())
        left_container.compose_add_child(PowerUsageMonitor())
        yield left_container
        
        # Right column with Memory, Network, and Sensor monitors
        right_container = Container(id="right-column")
        right_container.compose_add_child(MemoryMonitor())
        right_container.compose_add_child(NetworkMonitor())
        right_container.compose_add_child(GPUMonitor())
        right_container.compose_add_child(FirewallMonitor())
        right_container.compose_add_child(SensorMonitor())
        yield right_container
        
        yield Footer()

    def action_quit(self) -> None:
        self.exit()

    BINDINGS = [("ctrl+c", "quit", "Quit")]
    
    
    
    
if __name__ == "__main__":
    # Set up clean Ctrl+C exit
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    
    app = SystemMonitorApp()
    try:
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)