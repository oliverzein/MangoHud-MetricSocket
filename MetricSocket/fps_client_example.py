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
        with open('/proc/net/unix', 'r') as f:
            for line in f:
                if 'mangohud-fps-' in line:
                    # Extract PID from socket name
                    # Line format: "... @mangohud-fps-<pid> ..."
                    parts = line.split('mangohud-fps-')
                    if len(parts) > 1:
                        # Get the PID (first token after the prefix)
                        pid_str = parts[1].strip().split()[0]
                        try:
                            return int(pid_str)
                        except ValueError:
                            continue
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
        # Read exactly 48 bytes (one packet)
        expected = 48
        chunks = bytearray()
        while len(chunks) < expected:
            chunk = sock.recv(expected - len(chunks))
            if not chunk:
                return None
            chunks.extend(chunk)
        data = bytes(chunks)

        if len(data) != 48:
            print(f"\nDebug: Received {len(data)} bytes, expected 48 bytes")
            if len(data) == 16:
                print("Debug: This looks like the old 16-byte format!")
                print("Debug: Make sure you restarted the game after rebuilding MangoHud")
            elif len(data) > 0:
                print(f"Debug: First few bytes: {data[:min(20, len(data))].hex()}")
            return None

        # Unpack full metrics packet (48 bytes)
        # Format: double, 3 floats, 5 ints, 2 floats
        values = struct.unpack('=dfffiiiiiff', data)

        return {
            'fps': values[0],
            'fps_avg': values[1],
            'cpu_load': values[2],
            'cpu_power': values[3],
            'gpu_load': values[4],
            'cpu_temp': values[5],
            'gpu_temp': values[6],
            'gpu_junction_temp': values[7],
            'gpu_power': values[8],
            'gpu_vram_used': values[9],
            'fps_1_percent_low': values[10],
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
        print(f"{'FPS':>8} | {'1%Low':>7} | "
              f"{'CPU%':>6} | {'CPUTemp':>8} | {'GPU%':>6} | {'GPUTemp':>7} | {'GPUJunc':>7} |"
              f"{'VRAM':>7}")
        print("-" * 88)
    else:
        # Compact mode - essential metrics only
        print(f"{'FPS':>8} | {'1% Low':>8} | {'CPU':>8} | {'GPU':>8} | {'CPU Temp':>8} | {'GPU Temp':>8}")
        print("-" * 74)
    
    try:
        while True:
            data = read_fps_data(sock)
            if data is None:
                print("\nConnection lost")
                break
            
            if verbose:
                # Verbose display - available metrics
                print(f"{data['fps']:8.1f} | {data['fps_1_percent_low']:7.1f} | "
                      f"{data['cpu_load']:5.1f}% | {data['cpu_temp']:6}°C | "
                      f"{data['gpu_load']:5}% | {data['gpu_temp']:5}°C | {data['gpu_junction_temp']:5}°C | "
                      f"{data['gpu_vram_used']:5.2f}G", end='\r')
            else:
                # Compact display - essential metrics
                print(f"{data['fps']:8.1f} | {data['fps_1_percent_low']:8.1f} | "
                      f"{data['cpu_load']:7.1f}% | {data['gpu_load']:7}% | "
                      f"{data['cpu_temp']:7}°C | {data['gpu_temp']:7}°C", end='\r')
            
    except KeyboardInterrupt:
        print("\n\nDisconnected")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
