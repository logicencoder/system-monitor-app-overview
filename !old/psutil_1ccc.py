from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Grid
from rich.panel import Panel, Text
from rich.table import Table
from rich import box
import psutil
import time
import subprocess
from typing import Dict, Optional
from collections import deque

def format_bytes(bytes_value: float) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f}TB"

def create_progress_bar(percentage: float, width: int = 20) -> str:
    """Create a simple text-based progress bar.
    
    Args:
        percentage: Value between 0 and 100
        width: Width of the progress bar in characters
        
    Returns:
        A string representing the progress bar
    """
    filled = int(width * percentage / 100)
    return f"[{'=' * filled}{' ' * (width - filled)}] {percentage:.1f}%"

class CPUMonitor(Static):
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("CPU", style="cyan", justify="right", width=12)
        table.add_column("Value", style="green", ratio=1)
        
        cpu_percent = psutil.cpu_percent(percpu=True)
        cpu_freq = psutil.cpu_freq()
        cpu_times = psutil.cpu_times_percent()
        load_avg = psutil.getloadavg()
        
        # CPU Usage
        total_cpu = sum(cpu_percent)/len(cpu_percent)
        table.add_row("Usage", create_progress_bar(total_cpu))
        
        # Per-Core Usage
        cores_text = "Cores: " + " ".join(f"{p:.0f}%" for p in cpu_percent)
        table.add_row("Per-Core", cores_text)
        
        # Frequency and Load
        table.add_row(
            "Freq",
            f"{int(cpu_freq.current)}MHz | Load: {load_avg[0]:.1f}, {load_avg[1]:.1f}, {load_avg[2]:.1f}"
        )
        
        # CPU Times
        table.add_row(
            "Times",
            f"User: {cpu_times.user:.0f}% Sys: {cpu_times.system:.0f}% Idle: {cpu_times.idle:.0f}%"
        )

        return Panel(table, title="CPU", border_style="blue")

class MemoryMonitor(Static):
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("Mem", style="cyan", justify="right", width=12)
        table.add_column("Usage", style="green", ratio=1)
        
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # RAM Usage
        table.add_row(
            "RAM",
            f"{create_progress_bar(vm.percent)}\n{format_bytes(vm.used)}/{format_bytes(vm.total)}"
        )
        
        # Cache/Buffers
        cached = getattr(vm, 'cached', 0)
        buffers = getattr(vm, 'buffers', 0)
        if cached or buffers:
            table.add_row(
                "Cache/Buf",
                f"Cache: {format_bytes(cached)} Buf: {format_bytes(buffers)}"
            )
        
        # Swap
        table.add_row(
            "Swap",
            f"{create_progress_bar(swap.percent)}\n{format_bytes(swap.used)}/{format_bytes(swap.total)}"
        )
        
        return Panel(table, title="Memory", border_style="green")

class DiskMonitor(Static):
    def __init__(self):
        super().__init__()
        self.last_disk_io = psutil.disk_io_counters()
        self.last_check = time.time()
        self.speed_history = {
            'read': deque(maxlen=5),
            'write': deque(maxlen=5)
        }

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def get_disk_speeds(self) -> Dict[str, float]:
        current_time = time.time()
        current_io = psutil.disk_io_counters()
        time_diff = current_time - self.last_check
        
        speeds = {'read': 0, 'write': 0}
        if time_diff > 0:
            speeds['read'] = (current_io.read_bytes - self.last_disk_io.read_bytes) / time_diff
            speeds['write'] = (current_io.write_bytes - self.last_disk_io.write_bytes) / time_diff
            self.speed_history['read'].append(speeds['read'])
            self.speed_history['write'].append(speeds['write'])
        
        self.last_disk_io = current_io
        self.last_check = current_time
        return speeds

    def render(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("Disk", style="cyan", justify="right", width=12)
        table.add_column("Info", style="green", ratio=1)
        
        speeds = self.get_disk_speeds()
        
        # I/O Speeds
        table.add_row(
            "I/O",
            f"R: {format_bytes(speeds['read'])}/s W: {format_bytes(speeds['write'])}/s"
        )
        
        # Disk Usage
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                table.add_row(
                    part.mountpoint[:10],
                    f"{create_progress_bar(usage.percent)}\n{format_bytes(usage.used)}/{format_bytes(usage.total)}"
                )
            except Exception:
                continue

        return Panel(table, title="Storage", border_style="magenta")

class NetworkMonitor(Static):
    def __init__(self):
        super().__init__()
        self.last_net_io = psutil.net_io_counters()
        self.last_check = time.time()
        self.speed_history = {
            'up': deque(maxlen=5),
            'down': deque(maxlen=5)
        }

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def get_network_speeds(self) -> Dict[str, float]:
        current_time = time.time()
        current_io = psutil.net_io_counters()
        time_diff = current_time - self.last_check
        
        speeds = {'up': 0, 'down': 0}
        if time_diff > 0:
            speeds['up'] = (current_io.bytes_sent - self.last_net_io.bytes_sent) / time_diff
            speeds['down'] = (current_io.bytes_recv - self.last_net_io.bytes_recv) / time_diff
            self.speed_history['up'].append(speeds['up'])
            self.speed_history['down'].append(speeds['down'])
        
        self.last_net_io = current_io
        self.last_check = current_time
        return speeds

    def render(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("Net", style="cyan", justify="right", width=12)
        table.add_column("Info", style="green", ratio=1)
        
        speeds = self.get_network_speeds()
        
        # Transfer Rates
        table.add_row(
            "Speed",
            f"↑{format_bytes(speeds['up'])}/s\n↓{format_bytes(speeds['down'])}/s"
        )
        
        # Network Interfaces
        for name, addrs in psutil.net_if_addrs().items():
            stats = psutil.net_if_stats().get(name)
            if not stats or not stats.isup:
                continue
            addresses = [addr.address for addr in addrs if addr.family in {2, 10}]
            table.add_row(
                name[:10],
                f"{stats.speed}Mbps | {', '.join(addresses)}"
            )

        return Panel(table, title="Network", border_style="cyan")

class HardwareMonitor(Static):
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)

    def get_gpu_info(self) -> Optional[Dict]:
        """Get NVIDIA GPU information using nvidia-smi."""
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,memory.free,power.draw,fan.speed',
                '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                vals = list(map(float, result.stdout.strip().split(',')))
                return {
                    'temp': vals[0],
                    'util': vals[1],
                    'mem_used': vals[2],
                    'mem_total': vals[3],
                    'mem_free': vals[4],
                    'power': vals[5],
                    'fan': vals[6]
                }
        except Exception:
            pass
        return None

    def render(self) -> Panel:
        table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
        table.add_column("HW", style="cyan", justify="right", width=12)
        table.add_column("Status", style="green", ratio=1)
        
        # GPU Information
        gpu_info = self.get_gpu_info()
        if gpu_info:
            # GPU Usage
            table.add_row(
                "GPU Usage", 
                f"{create_progress_bar(gpu_info['util'])}\nTemp: {gpu_info['temp']}°C Fan: {gpu_info['fan']}%"
            )
            
            # GPU Memory
            mem_percent = (gpu_info['mem_used'] / gpu_info['mem_total']) * 100
            table.add_row(
                "GPU Memory",
                f"{create_progress_bar(mem_percent)}\n{gpu_info['mem_used']:.0f}MB/{gpu_info['mem_total']:.0f}MB"
            )
            
            table.add_row(
                "GPU Power",
                f"{gpu_info['power']:.1f}W"
            )
        
        # CPU Temperature
        try:
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                for sensor in temps['coretemp']:
                    if sensor.label.startswith('Core'):
                        table.add_row(
                            sensor.label,
                            f"{sensor.current}°C (High: {sensor.high}°C)"
                        )
        except Exception:
            pass

        return Panel(table, title="Hardware", border_style="yellow")

class SystemMonitorApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    Grid {
        grid-size: 2;
        grid-gutter: 1 2;
        margin: 1;
        height: auto;
    }

    Static {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Grid():
            yield CPUMonitor()
            yield MemoryMonitor()
            yield DiskMonitor()
            yield NetworkMonitor()
            yield HardwareMonitor()
        yield Footer()

if __name__ == "__main__":
    app = SystemMonitorApp()
    app.run()