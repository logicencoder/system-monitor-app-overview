from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Grid
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
import psutil
import time
import subprocess
import os
import signal
import sys
from typing import Dict, Optional
from collections import deque

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

    if percentage < 50: color = "green"
    elif percentage < 75: color = "yellow"
    elif percentage < 90: color = "red"
    else: color = "bright_red"

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

class CPUMonitor(BaseMonitor):
    """CPU usage and statistics monitor."""
    
    def on_mount(self) -> None:
        self.set_interval(0.5, self.refresh)
    
    def render(self) -> Panel:
        table = self.create_table()
        cpu_percent = psutil.cpu_percent(percpu=True)
        freq = psutil.cpu_freq()
        times = psutil.cpu_times_percent()
        load = psutil.getloadavg()
        ctx = psutil.cpu_stats()

        # Total CPU usage
        total = sum(cpu_percent) / len(cpu_percent)
        table.add_row(
            "CPU Total",
            create_bar(total),
            self.format_detail(f"Freq: {freq.current:.0f}MHz", f"Tasks: {format_bytes(ctx.ctx_switches)}/s")
        )

        # Load average and CPU states
        table.add_row(
            "Load AVG",
            f"1m: {load[0]:5.2f} | 5m: {load[1]:5.2f} | 15m: {load[2]:5.2f}",
            self.format_detail(f"User: {times.user:4.1f}%", f"Sys: {times.system:4.1f}%", f"Idle: {times.idle:4.1f}%")
        )

        # Individual core usage
        for i, percent in enumerate(cpu_percent):
            freq_info = f"Min: {freq.min:.0f}MHz Max: {freq.max:.0f}MHz" if i == 0 else ""
            table.add_row(f"Core {i:2d}", create_bar(percent), freq_info)

        return Panel(table, title=f"CPU Details ({len(cpu_percent)} cores)", border_style="blue")

class MemoryMonitor(BaseMonitor):
    """Memory usage monitor."""
    
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        table = self.create_table()
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # RAM usage
        table.add_row(
            "RAM",
            create_bar(vm.percent),
            f"Used: {format_bytes(vm.used)} / Total: {format_bytes(vm.total)}"
        )
        
        # Cache usage
        cache_percent = (vm.cached / vm.total) * 100
        table.add_row(
            "Cache",
            create_bar(cache_percent),
            f"Cached: {format_bytes(vm.cached)} | Buffers: {format_bytes(vm.buffers)}"
        )
        
        # Swap usage
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

        # I/O speeds
        table.add_row(
            "Disk I/O",
            f"Read: {format_bytes(speeds['read'])}/s",
            f"Write: {format_bytes(speeds['write'])}/s"
        )

        # Disk usage for each partition
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
        
        # Upload speed
        table.add_row(
            "Upload",
            create_bar(min(speeds['up'] / max_speed * 100, 100)),
            f"↑ {format_bytes(speeds['up'])}/s | Peak: {format_bytes(self.max_seen['up'])}/s"
        )
        
        # Download speed
        table.add_row(
            "Download",
            create_bar(min(speeds['down'] / max_speed * 100, 100)),
            f"↓ {format_bytes(speeds['down'])}/s | Peak: {format_bytes(self.max_seen['down'])}/s"
        )

        # Network interfaces
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

class GPUMonitor(BaseMonitor):
    """GPU usage and statistics monitor."""
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)

    def get_gpu_info(self) -> Optional[Dict]:
        try:
            cmd = ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,'
                  'memory.total,power.draw,fan.speed,clocks.current.graphics,clocks.max.graphics',
                  '--format=csv,noheader,nounits']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            
            if result.returncode == 0:
                vals = list(map(float, result.stdout.strip().split(',')))
                keys = ['temp', 'util', 'mem_used', 'mem_total', 'power', 'fan', 'clock', 'max_clock']
                return dict(zip(keys, vals))
        except Exception:
            pass
        return None

    def render(self) -> Panel:
        table = self.create_table()
        
        if info := self.get_gpu_info():
            table.add_row(
                "GPU Usage",
                create_bar(info['util']),
                f"Temp: {info['temp']}°C | Clock: {info['clock']}MHz"
            )
            
            mem_percent = (info['mem_used'] / info['mem_total']) * 100
            table.add_row(
                "GPU Memory",
                create_bar(mem_percent),
                f"Used: {info['mem_used']:.0f}MB / {info['mem_total']:.0f}MB"
            )
            
            table.add_row(
                "GPU Power",
                create_bar(info['fan']),
                f"Power: {info['power']:.1f}W | Fan: {info['fan']:.0f}%"
            )
        else:
            table.add_row("GPU Status", "No NVIDIA GPU detected", "")

        return Panel(table, title="GPU Status", border_style="yellow")

class SensorMonitor(BaseMonitor):
    """Temperature sensors monitor."""
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)

    def render(self) -> Panel:
        table = self.create_table()
        try:
            for name, sensors in psutil.sensors_temperatures().items():
                for sensor in sensors:
                    max_temp = sensor.high or 100
                    temp_percent = (sensor.current / max_temp) * 100
                    table.add_row(
                        sensor.label or name,
                        create_bar(temp_percent),
                        f"Current: {sensor.current:4.1f}°C | High: {sensor.high:4.1f}°C"
                    )
        except Exception:
            table.add_row("No sensors", "N/A", "N/A")

        return Panel(table, title="Temperature Sensors", border_style="red")

class SystemMonitorApp(App):
    """Main application class."""
    
    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: auto auto auto;
        background: $background;
    }
    
    Static {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CPUMonitor()
        yield MemoryMonitor()
        yield DiskMonitor()
        yield NetworkMonitor()
        yield GPUMonitor()
        yield SensorMonitor()
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