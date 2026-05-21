#!/usr/bin/env python3
"""
MangoHud FPS Socket Client Example

This script connects to MangoHud's FPS broadcast socket and displays
real-time FPS and system metrics from a running game.

Usage:
    python3 fps_client_example.py [game_pid] [--verbose]

Examples:
    # Auto-discover first MangoHud socket
    python3 fps_client_example.py
    
    # Connect to specific PID
    python3 fps_client_example.py 12345
    
    # Verbose mode (show all metrics)
    python3 fps_client_example.py --verbose
    python3 fps_client_example.py 12345 --verbose
"""

import socket
import struct
import sys
import time
from collections import deque
from typing import Optional


def find_mangohud_socket() -> Optional[int]:
    """
    Auto-discover MangoHud FPS socket by scanning /proc/net/unix.
    Returns the PID of the first MangoHud game found, or None.
    """
    try:
        pids = []
        with open('/proc/net/unix', 'r') as f:
            for line in f:
                if 'mangohud-fps-' in line:
                    parts = line.split('mangohud-fps-')
                    if len(parts) > 1:
                        pid_str = parts[1].strip().split()[0]
                        try:
                            pids.append(int(pid_str))
                        except ValueError:
                            continue
        if pids:
            # Prefer the highest PID (most recent process)
            return max(pids)
    except (FileNotFoundError, PermissionError):
        pass
    return None


def connect_fps_socket(pid):
    """
    Connect to MangoHud FPS socket for the given process ID.
    
    Args:
        pid: Process ID of the game running with MangoHud
        
    Returns:
        Connected socket object
        
    Raises:
        ConnectionRefusedError: If MangoHud is not running or fps_socket is disabled
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Abstract namespace socket (leading null byte)
    socket_path = f"\0mangohud-fps-{pid}"
    
    try:
        sock.connect(socket_path)
        return sock
    except ConnectionRefusedError:
        print(f"Error: Could not connect to MangoHud FPS socket for PID {pid}")
        print("Make sure:")
        print("  1. The game is running with MangoHud")
        print("  2. fps_socket=1 is set in MangoHud config")
        print("  3. The PID is correct")
        raise


PACKET_FMT = '<dffffiiiiiff'  # little-endian packed
PACKET_SIZE = struct.calcsize(PACKET_FMT)


def _values_look_sane(vals):
    fps, ft, fps_avg = vals[0], vals[1], vals[2]
    # Basic sanity for alignment/protocol
    if not (0.0 <= fps < 20000.0):
        return False
    if not (0.0 <= ft < 1000.0):  # ms
        return False
    if not (0.0 <= fps_avg < 20000.0):
        return False
    return True


def read_fps_data(sock):
    """
    Read one FPS data packet from the socket.
    
    Packet format (84 bytes):
        - fps (double, 8 bytes): Current frames per second
        - frametime (float, 4 bytes): Current frame time in milliseconds
        - cpu_load (float, 4 bytes): CPU usage percentage
        - cpu_power (float, 4 bytes): CPU power consumption (watts)
        - cpu_mhz (int, 4 bytes): CPU frequency
        - gpu_load (int, 4 bytes): GPU usage percentage
        - cpu_temp (int, 4 bytes): CPU temperature (°C)
        - gpu_temp (int, 4 bytes): GPU temperature (°C)
        - gpu_junction_temp (int, 4 bytes): GPU junction temperature (°C)
        - gpu_core_clock (int, 4 bytes): GPU core clock (MHz)
        - gpu_mem_clock (int, 4 bytes): GPU memory clock (MHz)
        - gpu_power (int, 4 bytes): GPU power consumption (watts)
        - gpu_vram_used (float, 4 bytes): GPU VRAM used (GiB)
        - ram_used (float, 4 bytes): System RAM used (GiB)
        - swap_used (float, 4 bytes): Swap used (GiB)
        - process_rss (float, 4 bytes): Process memory (GiB)
        - fps_1_percent_low (float, 4 bytes): 1% low FPS
        - fps_0_1_percent_low (float, 4 bytes): 0.1% low FPS
        - fps_97_percentile (float, 4 bytes): 97th percentile FPS
        - elapsed_ns (uint64, 8 bytes): Elapsed time since log start (nanoseconds)
        
    Args:
        sock: Connected socket object
        
    Returns:
        Dictionary with all metrics or None on error
    """
    try:
        # Persistent buffer on the function
        if not hasattr(read_fps_data, '_buf'):
            read_fps_data._buf = bytearray()
            read_fps_data._aligned = False

        buf = read_fps_data._buf
        # Fill buffer until we can parse at least one packet
        while len(buf) < PACKET_SIZE:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf.extend(chunk)

        # If not aligned yet, try to find alignment by scanning
        if not read_fps_data._aligned:
            resynced = False
            while len(buf) >= PACKET_SIZE:
                try:
                    vals = struct.unpack(PACKET_FMT, buf[:PACKET_SIZE])
                except struct.error:
                    vals = None
                if vals and _values_look_sane(vals):
                    read_fps_data._aligned = True
                    if resynced:
                        print("\nInfo: Stream resynchronized to packet boundary.")
                    break
                # drop one byte and continue scanning
                buf.pop(0)
                resynced = True
                # Top-up if needed
                if len(buf) < PACKET_SIZE:
                    chunk = sock.recv(4096)
                    if not chunk:
                        return None
                    buf.extend(chunk)

        # At this point we should be aligned; if not, give up
        if not read_fps_data._aligned:
            return None

        # Ensure we have one full packet
        while len(buf) < PACKET_SIZE:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf.extend(chunk)

        data = bytes(buf[:PACKET_SIZE])
        del buf[:PACKET_SIZE]

        # Unpack full metrics packet (52 bytes)
        values = struct.unpack(PACKET_FMT, data)
        if not _values_look_sane(values):
            # Lose alignment and try again next call
            read_fps_data._aligned = False
            return None

        return {
            'fps': values[0],
            'frametime_ms': values[1],
            'fps_avg': values[2],
            'cpu_load': values[3],
            'cpu_power': values[4],
            'gpu_load': values[5],
            'cpu_temp': values[6],
            'gpu_temp': values[7],
            'gpu_junction_temp': values[8],
            'gpu_power': values[9],
            'gpu_vram_used': values[10],
            'fps_1_percent_low': values[11],
        }
    except socket.error:
        return None


def main():
    # Parse arguments
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    args = [arg for arg in sys.argv[1:] if not arg.startswith('-')]
    
    # Auto-discover PID if not provided
    if len(args) == 0:
        print("No PID provided, auto-discovering MangoHud socket...")
        pid = find_mangohud_socket()
        if pid is None:
            print("Error: No MangoHud FPS socket found")
            print("Make sure:")
            print("  1. A game is running with MangoHud")
            print("  2. fps_socket=1 is set in MangoHud config")
            sys.exit(1)
        print(f"Found MangoHud socket for PID {pid}\n")
    elif len(args) == 1:
        try:
            pid = int(args[0])
        except ValueError:
            print(f"Error: Invalid PID '{args[0]}' - must be a number")
            sys.exit(1)
    else:
        print(f"Usage: {sys.argv[0]} [game_pid] [--verbose]")
        print("\nExamples:")
        print(f"  {sys.argv[0]}              # Auto-discover")
        print(f"  {sys.argv[0]} 12345        # Connect to specific PID")
        print(f"  {sys.argv[0]} --verbose    # Show all metrics")
        sys.exit(1)
    
    # Connect to FPS socket
    try:
        sock = connect_fps_socket(pid)
    except (ConnectionRefusedError, FileNotFoundError):
        sys.exit(1)
    
    print(f"Connected to MangoHud FPS socket for PID {pid}")
    print(f"Mode: {'Verbose (all metrics)' if verbose else 'Compact'}")
    print("Press Ctrl+C to disconnect\n")
    
    if verbose:
        # Verbose mode - show available metrics
        print(f"{'FPS':>8} | {'FT(ms)':>7} | {'1%Low':>7} | "
              f"{'CPU%':>6} | {'CPUTemp':>8} | {'GPU%':>6} | {'GPUTemp':>7} | {'GPUJunc':>7} |"
              f"{'VRAM':>7}")
        print("-" * 98)
    else:
        # Compact mode - essential metrics only
        print(f"{'FPS':>8} | {'FT ms':>7} | {'1% Low':>8} | {'CPU':>8} | {'GPU':>8} | {'CPU Temp':>8} | {'GPU Temp':>8}")
        print("-" * 84)
    
    try:
        while True:
            data = read_fps_data(sock)
            if data is None:
                print("\nConnection lost")
                break
            
            if verbose:
                # Verbose display - available metrics
                print(f"{data['fps']:8.1f} | {data['frametime_ms']:7.3f} | {data['fps_1_percent_low']:7.1f} | "
                      f"{data['cpu_load']:5.1f}% | {data['cpu_temp']:6}°C | "
                      f"{data['gpu_load']:5}% | {data['gpu_temp']:5}°C | {data['gpu_junction_temp']:5}°C | "
                      f"{data['gpu_vram_used']:5.2f}G", end='\r')
            else:
                # Compact display - essential metrics
                print(f"{data['fps']:8.1f} | {data['frametime_ms']:7.3f} | {data['fps_1_percent_low']:8.1f} | "
                      f"{data['cpu_load']:7.1f}% | {data['gpu_load']:7}% | "
                      f"{data['cpu_temp']:7}°C | {data['gpu_temp']:7}°C", end='\r')
            
    except KeyboardInterrupt:
        print("\n\nDisconnected")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
