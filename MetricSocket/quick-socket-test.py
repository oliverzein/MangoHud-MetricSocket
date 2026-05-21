import os, sys, socket, struct

FMT = '<dffffiiiiiff'
SIZE = struct.calcsize(FMT)

def find_pid():
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
                            pass
        return max(pids) if pids else None
    except Exception:
        return None

def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

pid = int(sys.argv[1]) if len(sys.argv) > 1 else (find_pid() or 0)
if not pid:
    print('No MangoHud socket found. Start a game/app with MANGOHUD and fps_socket=1.')
    sys.exit(1)

addr = b'\x00' + f"mangohud-fps-{pid}".encode()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(addr)
data = recv_exact(s, SIZE)
s.close()

if not data or len(data) != SIZE:
    print(f'Failed to read {SIZE} bytes, got {0 if not data else len(data)}')
    sys.exit(1)

vals = struct.unpack(FMT, data)
print('Raw tuple:', vals)
print('Assume [fps, frametime, fps_avg]: fps=%.1f ft=%.3f avg=%.1f' % (vals[0], vals[1], vals[2]))
print('Assume [fps, fps_avg, frametime]: fps=%.1f avg=%.1f ft=%.3f' % (vals[0], vals[1], vals[2]))