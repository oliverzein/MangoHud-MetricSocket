# MangoHud FPS Socket - Data Format Specification

**Version**: 1.0 (48-byte format)  
**Last Updated**: 2025-10-21

---

## Socket Implementation

### Connection Details
- **Type**: Unix Domain Socket (SOCK_STREAM)
- **Path**: Abstract namespace `@mangohud-fps-<pid>`
- **Discovery**: Auto-discovery via `/proc/net/unix`
- **Mode**: Non-blocking I/O
- **Broadcast Rate**: Game FPS rate (e.g., 60-120 Hz)

### Buffer Configuration
- Uses OS defaults for send/receive buffers
- Packet size: 48 bytes

---

## Packet Format (48 bytes)

### Binary Structure

**Struct Format String**: `'=dfffiiiiiff'`
- `=` : Native byte order, standard alignment
- `d` : double (8 bytes)
- `f` : float (4 bytes)
- `i` : int (4 bytes)

**Total**: 48 bytes (packed)

### Field Mapping

| Offset | Type   | Size | Index | Field Name             | Unit/Range      | Description                          |
|--------|--------|------|-------|------------------------|-----------------|--------------------------------------|
| 0      | double | 8    | [0]   | fps                    | FPS             | Current frames per second (smoothed) |
| 8      | float  | 4    | [1]   | fps_avg                | FPS             | Average FPS over time window         |
| 12     | float  | 4    | [2]   | cpu_load               | 0.0 - 100.0 %   | CPU usage percentage                 |
| 16     | float  | 4    | [3]   | cpu_power              | watts           | CPU power consumption                |
| 20     | int    | 4    | [4]   | gpu_load               | 0 - 100 %       | GPU usage percentage                 |
| 24     | int    | 4    | [5]   | cpu_temp               | °C              | CPU temperature                      |
| 28     | int    | 4    | [6]   | gpu_temp               | °C              | GPU edge temperature                 |
| 32     | int    | 4    | [7]   | gpu_junction_temp      | °C              | GPU junction temperature (hotspot)   |
| 36     | int    | 4    | [8]   | gpu_power              | watts           | GPU power consumption                |
| 40     | float  | 4    | [9]   | gpu_vram_used          | GiB             | GPU VRAM used                        |
| 44     | float  | 4    | [10]  | fps_1_percent_low      | FPS             | 1% low FPS (99th percentile)         |

---

## Server Implementation (C++)

### Location
`/home/oliverzein/Dokumente/Daten/Development/C/MangoHud-MetricSocket/src/fps_socket.cpp`

### Key Functions

#### `fps_socket_broadcast_full(double live_fps)`
Broadcasts full metrics packet to all connected clients.

**Behavior**:
- Early exit if no clients connected (zero overhead)
- Non-blocking send with `MSG_NOSIGNAL` flag
- Tracks consecutive send failures per client
- Disconnects stale clients after 120 failures (~1 second at 120 FPS)

**Stale Client Protection**:
```cpp
// Failure counter per client
static std::unordered_map<int, int> fps_client_fail_count;

// Disconnect after 120 consecutive EAGAIN/EWOULDBLOCK errors
if (fps_client_fail_count[client_fd] > 120) {
    // Disconnect stale client
}
```

**Performance**:
- No overhead when no clients connected
- Minimal overhead per client (~2µs per send)
- Automatic cleanup of stale clients prevents memory leaks

### Data Sources

| Field              | Source                                    |
|--------------------|-------------------------------------------|
| fps, frametime     | Function parameters (live values)         |
| cpu_*, ram_*, swap | `currentLogData` struct                   |
| gpu_* (except junc)| `currentLogData` struct                   |
| gpu_junction_temp  | `gpus->active_gpu()->metrics.junction_temp` (direct read) |
| fps_*_percent_*    | `fpsmetrics->metrics` (percentile queries)|

---

## Client Implementation (Python)

### Location
`/home/oliverzein/Dokumente/Daten/Development/Python/turing-smart-screen-python-mangohud/library/sensors/sensors_custom.py`

### Buffer Draining Strategy

**Problem**: Server broadcasts at 120 Hz, client reads at 1 Hz
- Without draining: Client would read 1-second-old stale data
- Socket buffer would contain ~120 packets

**Solution**: Non-blocking buffer draining
```python
# Drain loop - keeps only latest packet
latest_data = None
while True:
    try:
        data = self.sock.recv(48)  # Non-blocking, exact packet size
        if len(data) == 48:
            latest_data = data  # Overwrite with newest
    except BlockingIOError:
        break  # Buffer empty, done
```

**Performance**:
- Drains ~120 packets in microseconds
- Always gets most recent FPS value
- No stale data displayed

### Unpacking Example

```python
import struct

# Unpack 48-byte packet
values = struct.unpack('=dfffiiiiiff', data)

# Extract fields
fps = values[0]
fps_avg = values[1]
cpu_load = values[2]
cpu_power = values[3]
gpu_load = values[4]
cpu_temp = values[5]
gpu_temp = values[6]
gpu_junction_temp = values[7]
gpu_power = values[8]
gpu_vram_used = values[9]
one_percent_low = values[10]
```

### Error Handling

```python
# Validate packet size
if len(data) != 48:
    # Wrong size - disconnect and retry
    self.disconnect()
    return False

# Handle connection loss
try:
    values = struct.unpack('=dfffiiiiiff', data)
except struct.error:
    # Unpacking failed - disconnect
    self.disconnect()
    return False
```

---

## Version Notes

- Current packet is 48 bytes, fixed-size, packed, native endianness.
- Any change in fields or sizes is a breaking change; clients must match server packet size exactly.

---

## Important Notes

### Compatibility
- **Requires**: MangoHud with FPS socket feature
- **Config**: `fps_socket=1` in MangoHud config
- **Breaking**: Clients must match server packet size exactly

### Best Practices

1. **Always validate packet size** before unpacking
2. **Use non-blocking I/O** for buffer draining
3. **Handle connection loss gracefully** (auto-reconnect)
4. **Drain buffer completely** to get latest data
5. **Check for -1 values** (indicates unavailable metric)

### Performance Considerations

- **Server overhead**: Negligible when no clients (<0.01% CPU)
- **Client overhead**: ~0.1% CPU for 1 Hz reads with draining
- **Network impact**: None (local Unix socket only)
- **Memory**: ~1 KB per client for buffers
- **Latency**: <1ms from game to client display

---

## Testing

### Verify Packet Format
```bash
# Run example client
python MetricSocket/fps_client_example.py --verbose

# Expected output (subset):
# FPS | 1%Low | CPU% | GPU% | CPUTemp | GPUTemp | GPUJunc | ...
```

### Debug Connection Issues
```bash
# Check for MangoHud sockets
cat /proc/net/unix | grep mangohud-fps

# Expected: @mangohud-fps-<pid>
```

### Verify Packet Size
```python
import struct
format_str = '=dfffiiiiiff'
size = struct.calcsize(format_str)
print(f"Packet size: {size} bytes")  # Should print: 48 bytes
```

---

## References

- **Server Code**: `src/fps_socket.cpp`, `src/fps_socket.h`
- **Example Client**: `MetricSocket/fps_client_example.py`
- **Documentation**: `MetricSocket/FULL_METRICS_SOCKET.md`
