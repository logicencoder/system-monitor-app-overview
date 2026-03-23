"""
System Monitor - Core functionality
Collects and formats system information with proper error handling.
"""

import psutil
import time
import subprocess
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ===== Utility Functions =====

def format_bytes(bytes_value: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024:
            return f"{bytes_value:.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f}TB"

def create_bar(percentage: float, width: int = 20) -> str:
    """Create a visual progress bar using Unicode blocks."""
    filled = int(width * percentage / 100)
    return f"[{'█' * filled}{'·' * (width - filled)}] {percentage:.1f}%"

# ===== Data Collection Functions =====

def get_cpu_info() -> Dict:
    """
    Get comprehensive CPU information.
    Returns dictionary with CPU usage, frequencies, and per-core stats.
    """
    try:
        return {
            'percent': psutil.cpu_percent(interval=0.1),  # Quick sample
            'per_cpu': psutil.cpu_percent(interval=0.1, percpu=True),
            'freq': psutil.cpu_freq(),
            'count': psutil.cpu_count(),
            'times': psutil.cpu_times_percent(),
            'error': None
        }
    except Exception as e:
        return {'error': f"CPU Error: {str(e)}"}

def get_memory_info() -> Dict:
    """
    Get memory usage information.
    Returns dictionary with RAM and swap usage stats.
    """
    try:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            'ram_total': vm.total,
            'ram_used': vm.used,
            'ram_free': vm.available,
            'ram_percent': vm.percent,
            'swap_total': swap.total,
            'swap_used': swap.used,
            'swap_percent': swap.percent,
            'cached': getattr(vm, 'cached', 0),
            'buffers': getattr(vm, 'buffers', 0),
            'error': None
        }
    except Exception as e:
        return {'error': f"Memory Error: {str(e)}"}

def get_network_info(last_io: Optional[Tuple] = None) -> Dict:
    """
    Get network usage and speed information.
    last_io: tuple of (previous counters, timestamp) for speed calculation
    """
    try:
        current_io = psutil.net_io_counters()
        current_time = time.time()
        
        info = {
            'bytes_sent': current_io.bytes_sent,
            'bytes_recv': current_io.bytes_recv,
            'packets_sent': current_io.packets_sent,
            'packets_recv': current_io.packets_recv,
            'error': None
        }
        
        # Calculate speeds if we have previous measurements
        if last_io:
            last_counters, last_time = last_io
            time_diff = current_time - last_time
            if time_diff > 0:
                info['speed_up'] = (current_io.bytes_sent - last_counters.bytes_sent) / time_diff
                info['speed_down'] = (current_io.bytes_recv - last_counters.bytes_recv) / time_diff
        
        # Get interface details
        interfaces = {}
        for name, stats in psutil.net_if_stats().items():
            addr_info = psutil.net_if_addrs().get(name, [])
            interfaces[name] = {
                'up': stats.isup,
                'speed': stats.speed,
                'mtu': stats.mtu,
                'addresses': [addr.address for addr in addr_info if addr.family in {2, 10}]  # IPv4 and IPv6
            }
        info['interfaces'] = interfaces
        
        return info
    except Exception as e:
        return {'error': f"Network Error: {str(e)}"}

def get_disk_info() -> Dict:
    """
    Get disk usage and I/O information.
    Returns dictionary with disk space and I/O stats.
    """
    try:
        partitions = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                partitions.append({
                    'device': part.device,
                    'mountpoint': part.mountpoint,
                    'fstype': part.fstype,
                    'total': usage.total,
                    'used': usage.used,
                    'free': usage.free,
                    'percent': usage.percent
                })
            except Exception:
                continue
        
        io = psutil.disk_io_counters()
        return {
            'partitions': partitions,
            'io_read': io.read_bytes if io else 0,
            'io_write': io.write_bytes if io else 0,
            'io_read_count': io.read_count if io else 0,
            'io_write_count': io.write_count if io else 0,
            'error': None
        }
    except Exception as e:
        return {'error': f"Disk Error: {str(e)}"}

def get_gpu_info() -> Optional[Dict]:
    """
    Get GPU information (supports NVIDIA and AMD).
    Returns None if no GPU is detected.
    """
    # Try NVIDIA GPU first
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
                'memory_total': mem_total,
                'error': None
            }
    except Exception:
        pass

    # Try AMD GPU
    try:
        if os.path.exists('/sys/class/drm/card0/device/gpu_busy_percent'):
            with open('/sys/class/drm/card0/device/gpu_busy_percent') as f:
                util = float(f.read())
            with open('/sys/class/drm/card0/device/mem_info_vram_used') as f:
                mem_used = int(f.read()) / 1024 / 1024
            with open('/sys/class/drm/card0/device/mem_info_vram_total') as f:
                mem_total = int(f.read()) / 1024 / 1024
            return {
                'type': 'AMD',
                'utilization': util,
                'memory_used': mem_used,
                'memory_total': mem_total,
                'error': None
            }
    except Exception:
        pass

    return None

# Let's test these functions to make sure they work
if __name__ == "__main__":
    print("\nTesting CPU info:")
    print(get_cpu_info())
    
    print("\nTesting Memory info:")
    print(get_memory_info())
    
    print("\nTesting Network info:")
    print(get_network_info())
    
    print("\nTesting Disk info:")
    print(get_disk_info())
    
    print("\nTesting GPU info:")
    print(get_gpu_info())