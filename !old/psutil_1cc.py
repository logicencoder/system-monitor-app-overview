"""
Comprehensive System Monitor
Displays detailed system metrics including CPU, Memory, Disk, Network, Temperature, and GPU
Each metric is shown in a separate panel with detailed information
"""

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Vertical
from rich.panel import Panel
from rich.table import Table
from rich import box
import psutil
import time
import subprocess
from typing import Dict, List, Optional
from collections import deque

def format_bytes(bytes_value: float) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f}TB"

class CPUMonitor(Static):
    """Detailed CPU information display."""
    
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Details", style="blue")
        
        cpu_percent = psutil.cpu_percent(percpu=True)
        cpu_freq = psutil.cpu_freq()
        cpu_times = psutil.cpu_times_percent()
        cpu_stats = psutil.cpu_stats()
        load_avg = psutil.getloadavg()
        
        table.add_row(
            "Total CPU Usage",
            f"{sum(cpu_percent)/len(cpu_percent):.1f}%",
            f"Frequency: {int(cpu_freq.current)}MHz" if cpu_freq else "N/A"
        )
        
        table.add_row(
            "Load Average",
            f"{load_avg[0]:.2f}",
            f"5min: {load_avg[1]:.2f} | 15min: {load_avg[2]:.2f}"
        )
        
        table.add_row(
            "CPU Times",
            f"User: {cpu_times.user:.1f}%",
            f"System: {cpu_times.system:.1f}% | Idle: {cpu_times.idle:.1f}%"
        )
        
        table.add_row(
            "Context Switches",
            format_bytes(cpu_stats.ctx_switches),
            f"Interrupts: {format_bytes(cpu_stats.interrupts)}"
        )

        return Panel(table, title="CPU Details", border_style="blue")

class MemoryMonitor(Static):
    """Detailed memory information display."""
    
    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Used", style="yellow")
        table.add_column("Total", style="green")
        table.add_column("Percent", style="blue")
        
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        table.add_row(
            "RAM",
            format_bytes(vm.used),
            format_bytes(vm.total),
            f"{vm.percent}%"
        )
        
        table.add_row(
            "Cache/Buffers",
            format_bytes(getattr(vm, 'cached', 0)),
            format_bytes(getattr(vm, 'buffers', 0)),
            f"{(vm.cached + vm.buffers) / vm.total * 100:.1f}%"
        )
        
        table.add_row(
            "Swap",
            format_bytes(swap.used),
            format_bytes(swap.total),
            f"{swap.percent}%"
        )
        
        return Panel(table, title="Memory Details", border_style="green")

class DiskMonitor(Static):
    """Detailed disk I/O information display."""
    
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
        
        speeds['read_avg'] = sum(self.speed_history['read']) / len(self.speed_history['read'])
        speeds['write_avg'] = sum(self.speed_history['write']) / len(self.speed_history['write'])
        
        return speeds

    def render(self) -> Panel:
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Mount", style="cyan")
        table.add_column("Speed", style="green")
        table.add_column("Usage", style="yellow")
        table.add_column("Total I/O", style="blue")
        
        speeds = self.get_disk_speeds()
        
        table.add_row(
            "Disk I/O",
            f"R: {format_bytes(speeds['read_avg'])}/s",
            f"W: {format_bytes(speeds['write_avg'])}/s",
            f"Peak R: {format_bytes(max(speeds['read'], 0))}/s"
        )
        
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                table.add_row(
                    part.mountpoint,
                    f"{usage.percent}% used",
                    f"{format_bytes(usage.used)} / {format_bytes(usage.total)}",
                    part.fstype
                )
            except Exception:
                continue
        
        return Panel(table, title="Disk I/O & Usage", border_style="magenta")

class NetworkMonitor(Static):
    """Detailed network information display."""
    
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
        
        speeds['up_avg'] = sum(self.speed_history['up']) / len(self.speed_history['up'])
        speeds['down_avg'] = sum(self.speed_history['down']) / len(self.speed_history['down'])
        
        return speeds

    def render(self) -> Panel:
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Interface", style="cyan")
        table.add_column("Speed", style="green")
        table.add_column("Details", style="yellow")
        table.add_column("IP Address", style="blue")
        
        speeds = self.get_network_speeds()
        
        table.add_row(
            "Transfer Rate",
            f"↑ {format_bytes(speeds['up_avg'])}/s",
            f"↓ {format_bytes(speeds['down_avg'])}/s",
            f"Peak ↑ {format_bytes(max(speeds['up'], 0))}/s"
        )
        
        for name, addrs in psutil.net_if_addrs().items():
            stats = psutil.net_if_stats().get(name)
            if not stats or not stats.isup:
                continue
                
            addresses = [addr.address for addr in addrs if addr.family in {2, 10}]
            table.add_row(
                name,
                f"{stats.speed} Mbps" if stats.speed else "N/A",
                f"MTU: {stats.mtu}",
                "\n".join(addresses) or "None"
            )
        
        return Panel(table, title="Network Details", border_style="cyan")

class SensorMonitor(Static):
    """Temperature sensor information display."""
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)

    def render(self) -> Panel:
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Sensor", style="cyan")
        table.add_column("Temperature", style="yellow")
        table.add_column("High", style="red")
        table.add_column("Critical", style="red bold")
        
        try:
            for name, sensors in psutil.sensors_temperatures().items():
                for sensor in sensors:
                    table.add_row(
                        sensor.label or name,
                        f"{sensor.current}°C",
                        f"{sensor.high}°C" if sensor.high is not None else "N/A",
                        f"{sensor.critical}°C" if sensor.critical is not None else "N/A"
                    )
        except Exception:
            table.add_row("No sensors", "N/A", "N/A", "N/A")
        
        return Panel(table, title="Temperature Sensors", border_style="red")

class GPUMonitor(Static):
    """GPU information display for NVIDIA cards."""
    
    def on_mount(self) -> None:
        self.set_interval(2.0, self.refresh)

    def get_gpu_info(self) -> Optional[Dict]:
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,memory.free,power.draw,fan.speed',
                '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                temp, util, mem_used, mem_total, mem_free, power, fan = map(float, result.stdout.strip().split(','))
                return {
                    'type': 'NVIDIA',
                    'temp': temp,
                    'utilization': util,
                    'memory_used': mem_used,
                    'memory_total': mem_total,
                    'memory_free': mem_free,
                    'power': power,
                    'fan': fan
                }
        except Exception:
            pass
        return None

    def render(self) -> Panel:
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Details", style="yellow")
        
        gpu_info = self.get_gpu_info()
        
        if not gpu_info:
            return Panel("No NVIDIA GPU detected", title="GPU Status", border_style="yellow")

        table.add_row(
            "GPU Type",
            gpu_info['type'],
            f"Temperature: {gpu_info['temp']}°C"
        )
        
        table.add_row(
            "Utilization",
            f"{gpu_info['utilization']}%",
            f"Fan Speed: {gpu_info['fan']}%"
        )
        
        table.add_row(
            "Memory Usage",
            f"{gpu_info['memory_used']:.0f}MB / {gpu_info['memory_total']:.0f}MB",
            f"Free: {gpu_info['memory_free']:.0f}MB"
        )
        
        table.add_row(
            "Power Draw",
            f"{gpu_info['power']:.1f}W",
            f"Memory Utilization: {(gpu_info['memory_used']/gpu_info['memory_total']*100):.1f}%"
        )

        return Panel(table, title="GPU Status", border_style="yellow")

class SystemMonitorApp(App):
    """Main application."""
    
    CSS = """
    Screen {
        layout: vertical;
    }

    Static {
        height: auto;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CPUMonitor()
        yield MemoryMonitor()
        yield GPUMonitor()
        yield DiskMonitor()
        yield NetworkMonitor()
        yield SensorMonitor()
        yield Footer()

if __name__ == "__main__":
    app = SystemMonitorApp()
    app.run()