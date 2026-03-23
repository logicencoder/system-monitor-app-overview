"""
System Monitor TUI - Fixed version
Uses simple vertical layout for reliability
"""

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Vertical
from textual.reactive import reactive
from rich.panel import Panel
from rich.table import Table
from rich import box
import psutil
import time
import os
import subprocess
from typing import Optional, Dict, Tuple

def format_bytes(bytes_value: float) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f}TB"

class SystemStats(Static):
    """Display system statistics."""
    
    def on_mount(self) -> None:
        """Set up periodic refresh."""
        self.set_interval(1.0, self.refresh)

    def render(self) -> Panel:
        """Render current system stats."""
        # Get CPU info
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        # Get memory info
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        # Get network info
        net = psutil.net_io_counters()
        
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Metric")
        table.add_column("Value")

        # Add CPU info
        table.add_row("CPU Usage", f"{cpu_percent:.1f}%")
        
        # Add memory info
        table.add_row("Memory Usage", f"{mem.percent:.1f}% ({format_bytes(mem.used)} / {format_bytes(mem.total)})")
        table.add_row("Swap Usage", f"{swap.percent:.1f}% ({format_bytes(swap.used)} / {format_bytes(swap.total)})")
        
        # Add network info
        table.add_row(
            "Network I/O", 
            f"↑ {format_bytes(net.bytes_sent)} ↓ {format_bytes(net.bytes_recv)}"
        )

        return Panel(table, title="System Status", border_style="green")

class DiskInfo(Static):
    """Display disk information."""
    
    def on_mount(self) -> None:
        """Set up periodic refresh."""
        self.set_interval(5.0, self.refresh)

    def render(self) -> Panel:
        """Render disk information."""
        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Mount")
        table.add_column("Used")
        table.add_column("Total")
        table.add_column("Usage")

        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                table.add_row(
                    part.mountpoint,
                    format_bytes(usage.used),
                    format_bytes(usage.total),
                    f"{usage.percent}%"
                )
            except Exception:
                continue

        return Panel(table, title="Disk Usage", border_style="blue")

class GPUInfo(Static):
    """Display GPU information if available."""
    
    def on_mount(self) -> None:
        """Set up periodic refresh."""
        self.set_interval(2.0, self.refresh)

    def get_gpu_info(self) -> Optional[Dict]:
        """Get GPU information."""
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total',
                '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                temp, util, mem_used, mem_total = map(float, result.stdout.strip().split(','))
                return {
                    'type': 'NVIDIA',
                    'temp': temp,
                    'utilization': util,
                    'memory_used': mem_used,
                    'memory_total': mem_total
                }
        except Exception:
            pass
        return None

    def render(self) -> Panel:
        """Render GPU information."""
        gpu_info = self.get_gpu_info()
        
        if not gpu_info:
            return Panel("No GPU detected", title="GPU Status")

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Metric")
        table.add_column("Value")

        table.add_row("GPU Type", gpu_info['type'])
        if 'temp' in gpu_info:
            table.add_row("Temperature", f"{gpu_info['temp']}°C")
        table.add_row("Utilization", f"{gpu_info['utilization']}%")
        table.add_row(
            "Memory",
            f"{gpu_info['memory_used']}MB / {gpu_info['memory_total']}MB"
        )

        return Panel(table, title="GPU Status", border_style="yellow")

class SystemMonitorApp(App):
    """Main application class."""
    
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
        """Create child widgets."""
        yield Header()
        with Vertical():
            yield SystemStats()
            yield DiskInfo()
            yield GPUInfo()
        yield Footer()

if __name__ == "__main__":
    app = SystemMonitorApp()
    app.run()