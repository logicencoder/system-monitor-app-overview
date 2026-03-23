"""
Complete system resource monitor with CPU, Memory, Disk, Network, GPU, and Temperature monitoring.
Requires: textual and psutil libraries
Install with: pip install textual psutil
"""

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Grid
from textual.widgets import Header, Footer, Static, Label
from textual import work
import psutil
import time
import datetime
import subprocess
import os
from typing import Dict, List, Optional

def format_bytes(bytes_value: int) -> str:
    """Format bytes into human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f}PB"

class CPUWidget(Static):
    """Widget to display detailed CPU information."""
    
    def compose(self) -> ComposeResult:
        """Create the CPU widget layout."""
        yield Label("CPU Information", classes="title")
        yield Static(id="cpu_details")
        yield Static(id="cpu_cores")
        
    def update_stats(self) -> None:
        """Update CPU statistics."""
        cpu_freq = psutil.cpu_freq()
        cpu_times = psutil.cpu_times_percent()
        cpu_percentages = psutil.cpu_percent(percpu=True)
        
        details_widget = self.query_one("#cpu_details", Static)
        cores_widget = self.query_one("#cpu_cores", Static)
        
        details = f"Total Usage: {sum(cpu_percentages)/len(cpu_percentages):.1f}%\n"
        if cpu_freq:
            details += f"Frequency: {cpu_freq.current:.0f}MHz"
            if hasattr(cpu_freq, 'min') and hasattr(cpu_freq, 'max'):
                details += f" (Min: {cpu_freq.min:.0f}MHz, Max: {cpu_freq.max:.0f}MHz)"
        details += f"\nUser: {cpu_times.user:.1f}% System: {cpu_times.system:.1f}%\n"
        details += f"Idle: {cpu_times.idle:.1f}%"
        if hasattr(cpu_times, 'iowait'):
            details += f" IO Wait: {cpu_times.iowait:.1f}%"
        details_widget.update(details)
        
        cores_text = "\nCore Usage:\n"
        for i, percentage in enumerate(cpu_percentages):
            bar = "█" * int(percentage / 5)
            cores_text += f"Core {i}: {percentage:5.1f}% |{bar:<20}|\n"
        cores_widget.update(cores_text)

class MemoryWidget(Static):
    """Widget to display detailed memory information."""
    
    def compose(self) -> ComposeResult:
        yield Label("Memory Information", classes="title")
        yield Static(id="memory_details")
        yield Static(id="swap_details")
        
    def update_stats(self) -> None:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        mem_widget = self.query_one("#memory_details", Static)
        swap_widget = self.query_one("#swap_details", Static)
        
        mem_text = f"Total: {format_bytes(memory.total)}\n"
        mem_text += f"Used: {format_bytes(memory.used)} ({memory.percent}%)\n"
        mem_text += f"Available: {format_bytes(memory.available)}\n"
        if hasattr(memory, 'buffers'):
            mem_text += f"Buffers: {format_bytes(memory.buffers)}\n"
        if hasattr(memory, 'cached'):
            mem_text += f"Cached: {format_bytes(memory.cached)}\n"
        mem_widget.update(mem_text)
        
        swap_text = "\nSwap Memory:\n"
        swap_text += f"Total: {format_bytes(swap.total)}\n"
        swap_text += f"Used: {format_bytes(swap.used)} ({swap.percent}%)\n"
        swap_text += f"Free: {format_bytes(swap.free)}"
        swap_widget.update(swap_text)

class DiskWidget(Static):
    """Widget to display detailed disk information."""
    
    def compose(self) -> ComposeResult:
        yield Label("Disk Information", classes="title")
        yield Static(id="disk_usage")
        yield Static(id="disk_io")
        
    def update_stats(self) -> None:
        disk_usage = psutil.disk_usage('/')
        disk_io = psutil.disk_io_counters()
        
        usage_widget = self.query_one("#disk_usage", Static)
        io_widget = self.query_one("#disk_io", Static)
        
        usage_text = f"Total: {format_bytes(disk_usage.total)}\n"
        usage_text += f"Used: {format_bytes(disk_usage.used)} ({disk_usage.percent}%)\n"
        usage_text += f"Free: {format_bytes(disk_usage.free)}\n"
        usage_widget.update(usage_text)
        
        if disk_io:
            io_text = "\nDisk I/O:\n"
            io_text += f"Read: {format_bytes(disk_io.read_bytes)}\n"
            io_text += f"Write: {format_bytes(disk_io.write_bytes)}\n"
            io_text += f"Read Count: {disk_io.read_count:,}\n"
            io_text += f"Write Count: {disk_io.write_count:,}"
            io_widget.update(io_text)

class NetworkWidget(Static):
    """Widget to display detailed network information."""
    
    def __init__(self) -> None:
        super().__init__()
        self.last_io = psutil.net_io_counters()
        self.last_time = time.time()
        
    def compose(self) -> ComposeResult:
        yield Label("Network Information", classes="title")
        yield Static(id="network_io")
        yield Static(id="network_connections")
        
    def update_stats(self) -> None:
        current_io = psutil.net_io_counters()
        current_time = time.time()
        time_delta = current_time - self.last_time
        
        bytes_sent = (current_io.bytes_sent - self.last_io.bytes_sent) / time_delta
        bytes_recv = (current_io.bytes_recv - self.last_io.bytes_recv) / time_delta
        
        io_widget = self.query_one("#network_io", Static)
        conn_widget = self.query_one("#network_connections", Static)
        
        io_text = f"Upload: {format_bytes(int(bytes_sent))}/s\n"
        io_text += f"Download: {format_bytes(int(bytes_recv))}/s\n"
        io_text += f"Total Sent: {format_bytes(current_io.bytes_sent)}\n"
        io_text += f"Total Received: {format_bytes(current_io.bytes_recv)}\n"
        io_text += f"Packets Sent: {current_io.packets_sent:,}\n"
        io_text += f"Packets Received: {current_io.packets_recv:,}"
        io_widget.update(io_text)
        
        try:
            connections = psutil.net_connections()
            conn_text = "\nActive Connections:\n"
            conn_count = {"ESTABLISHED": 0, "LISTEN": 0, "TIME_WAIT": 0, "CLOSE_WAIT": 0}
            for conn in connections:
                if conn.status in conn_count:
                    conn_count[conn.status] += 1
            for status, count in conn_count.items():
                conn_text += f"{status}: {count}\n"
            conn_widget.update(conn_text)
        except (psutil.AccessDenied, psutil.Error):
            conn_widget.update("\nNetwork connections: Access denied")
        
        self.last_io = current_io
        self.last_time = current_time

class ProcessWidget(Static):
    """Widget to display top processes."""
    
    def compose(self) -> ComposeResult:
        yield Label("Top Processes", classes="title")
        yield Static(id="process_list")
        
    def update_stats(self) -> None:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                processes.append(proc.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
                
        processes.sort(key=lambda x: x.get('cpu_percent', 0), reverse=True)
        
        process_widget = self.query_one("#process_list", Static)
        process_text = "\nPID    CPU%    MEM%    Name\n"
        process_text += "=" * 40 + "\n"
        
        for proc in processes[:10]:  # Show top 10 processes
            process_text += f"{proc['pid']:<7}{proc.get('cpu_percent', 0):6.1f}"
            process_text += f"{proc.get('memory_percent', 0):8.1f}    {proc['name']}\n"
            
        process_widget.update(process_text)

class GPUWidget(Static):
    """Widget to display GPU information using nvidia-smi."""
    
    def compose(self) -> ComposeResult:
        yield Label("GPU Information", classes="title")
        yield Static(id="gpu_details")
        
    def get_nvidia_info(self) -> Optional[dict]:
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total',
                 '--format=csv,noheader,nounits'], 
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                temp, util, mem_used, mem_total = result.stdout.strip().split(',')
                return {
                    'temperature': float(temp),
                    'utilization': float(util),
                    'memory_used': float(mem_used),
                    'memory_total': float(mem_total)
                }
        except (FileNotFoundError, subprocess.SubprocessError, ValueError):
            pass
        return None
        
    def update_stats(self) -> None:
        details_widget = self.query_one("#gpu_details", Static)
        gpu_info = self.get_nvidia_info()
        
        if gpu_info:
            text = f"Temperature: {gpu_info['temperature']}°C\n"
            text += f"Utilization: {gpu_info['utilization']}%\n"
            text += f"Memory: {format_bytes(int(gpu_info['memory_used'] * 1024**2))} / "
            text += f"{format_bytes(int(gpu_info['memory_total'] * 1024**2))}\n"
            text += f"Memory Usage: {(gpu_info['memory_used']/gpu_info['memory_total']*100):.1f}%"
        else:
            text = "No NVIDIA GPU detected or nvidia-smi not available"
        
        details_widget.update(text)

class TemperatureWidget(Static):
    """Widget to display system temperatures."""
    
    def compose(self) -> ComposeResult:
        yield Label("Temperature Sensors", classes="title")
        yield Static(id="temp_details")
        
    def update_stats(self) -> None:
        temp_widget = self.query_one("#temp_details", Static)
        try:
            temperatures = psutil.sensors_temperatures()
            if temperatures:
                text = ""
                for name, entries in temperatures.items():
                    text += f"\n{name}:\n"
                    for entry in entries:
                        text += f"  {entry.label or 'Unknown'}: {entry.current:.1f}°C"
                        if entry.high is not None:
                            text += f" (Max: {entry.high:.1f}°C)"
                        text += "\n"
            else:
                text = "No temperature sensors available"
        except AttributeError:
            text = "Temperature sensors not supported on this system"
            
        temp_widget.update(text)

class SystemInfoWidget(Static):
    """Widget to display system information and load averages."""
    
    def compose(self) -> ComposeResult:
        yield Label("System Information", classes="title")
        yield Static(id="system_info")
        
    def update_stats(self) -> None:
        info_widget = self.query_one("#system_info", Static)
        
        boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.datetime.now() - boot_time
        
        try:
            load_avg = psutil.getloadavg()
            load_text = f"Load Averages: {load_avg[0]:.2f} (1m), {load_avg[1]:.2f} (5m), {load_avg[2]:.2f} (15m)"
        except (AttributeError, OSError):
            load_text = "Load averages not available"
        
        uname = os.uname()
        
        text = f"Hostname: {uname.nodename}\n"
        text += f"OS: {uname.sysname} {uname.release}\n"
        text += f"Uptime: {uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m\n"
        text += load_text
        
        info_widget.update(text)

class NetworkDetailWidget(Static):
    """Widget to display detailed network interface information."""
    
    def compose(self) -> ComposeResult:
        yield Label("Network Interfaces", classes="title")
        yield Static(id="interface_details")
        
    def update_stats(self) -> None:
        net_widget = self.query_one("#interface_details", Static)
        
        try:
            interfaces = psutil.net_if_stats()
            addresses = psutil.net_if_addrs()
            
            text = ""
            for name, stats in interfaces.items():
                if name in addresses:
                    text += f"\n{name}:\n"
                    text += f"  Status: {'Up' if stats.isup else 'Down'}\n"
                    text += f"  Speed: {stats.speed} Mbps\n"
                    text += f"  MTU: {stats.mtu}\n"
                    
                    for addr in addresses[name]:
                        if addr.family == 2:  # IPv4
                            text += f"  IPv4: {addr.address}\n"
                        elif addr.family == 17:  # MAC
                            text += f"  MAC: {addr.address}\n"
                        elif addr.family == 10:  # IPv6
                            text += f"  IPv6: {addr.address}\n"
        except (psutil.Error, AttributeError):
            text = "Network interface details not available"
        
        net_widget.update(text)

class EnhancedResourceMonitor(App):
    """Enhanced system resource monitor with detailed statistics."""
    
    CSS = """
    Screen {
        layout: grid;
        grid-size: 4 3;
        grid-gutter: 1 1;
        padding: 1;
    }

    Static {
        height: auto;
        border: round green;
        padding: 1;
        background: $surface;
    }

    .title {
        color: rgb(255,255,0);
        text-style: bold;
        align: center middle;
        height: 1;
        margin-bottom: 1;
    }

    #processes {
        column-span: 4;
        height: 15;
    }

    CPUWidget {
        height: auto;
        row-span: 2;
    }

    MemoryWidget {
        height: auto;
    }

    DiskWidget {
        height: auto;
    }

    NetworkWidget {
        height: auto;
        row-span: 2;
    }

    GPUWidget {
        height: auto;
    }

    TemperatureWidget {
        height: auto;
    }

    SystemInfoWidget {
        height: auto;
    }

    NetworkDetailWidget {
        height: auto;
    }

    Label {
        padding: 0;
        background: $boost;
        color: $warning;
        text-style: bold;
        text-align: center;
        width: 100%;
    }
    """
    
    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield Header()
        
        # First row
        yield SystemInfoWidget()
        yield CPUWidget()
        yield MemoryWidget()
        yield NetworkWidget()
        
        # Second row
        yield GPUWidget()
        yield TemperatureWidget()
        yield DiskWidget()
        yield NetworkDetailWidget()
        
        # Bottom row (full width)
        yield ProcessWidget(id="processes")
        
        yield Footer()
        
    def on_mount(self) -> None:
        """Set up the update interval when the app starts."""
        self.set_interval(1.0, self.update_stats)
        
    def update_stats(self) -> None:
        """Update all widget statistics."""
        # List of all widget types to update
        widgets_to_update = [
            CPUWidget,
            MemoryWidget,
            DiskWidget,
            NetworkWidget,
            GPUWidget,
            TemperatureWidget,
            SystemInfoWidget,
            NetworkDetailWidget,
            ProcessWidget
        ]
        
        # Update each widget type, handling any errors that might occur
        for widget_type in widgets_to_update:
            try:
                widget = self.query_one(widget_type)
                widget.update_stats()
            except Exception as e:
                print(f"Error updating {widget_type.__name__}: {e}")

def main():
    """Main entry point for the application."""
    try:
        # Create and run the application
        app = EnhancedResourceMonitor()
        app.run()
    except Exception as e:
        print(f"Application error: {e}")
        return 1
    return 0

if __name__ == "__main__":
    exit(main())