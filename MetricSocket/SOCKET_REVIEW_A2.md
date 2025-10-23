# MangoHud Metric Socket – A2: Versioned Protocol (magic/version/size)

## Goal

- **[Purpose]** Add explicit wire-protocol versioning so clients can verify compatibility and payload size.
- **[Benefit]** Safe evolution of the payload without silent ABI breaks, clearer errors on mismatch.

## Packet Layout (v1)

- **Header (8 bytes, packed)**
  - `magic` (uint32): constant identifier. Suggested: `0x4D484653` (ASCII "MHFS").
  - `version` (uint16): protocol version. Start at `1`.
  - `size` (uint16): payload size in bytes. For v1: `48`.
- **Payload (48 bytes, packed)**
  - Format string (native): `'=dfffiiiiiff'`
  - Fields (in order): `fps(double)`, `fps_avg(float)`, `cpu_load(float)`, `cpu_power(float)`, `gpu_load(int)`, `cpu_temp(int)`, `gpu_temp(int)`, `gpu_junction_temp(int)`, `gpu_power(int)`, `gpu_vram_used(float)`, `fps_1_percent_low(float)`.
- **Total v1 packet size**: 56 bytes (8 + 48).

## C/C++ Definitions

```c
// src/fps_socket.h
struct fps_socket_header {
    uint32_t magic;   // 'MHFS' = 0x4D484653
    uint16_t version; // 1
    uint16_t size;    // payload size in bytes (48 for v1)
} __attribute__((packed));

struct fps_metrics_full_packet { // already present
    double fps;
    float fps_avg;
    float cpu_load;
    float cpu_power;
    int   gpu_load;
    int   cpu_temp;
    int   gpu_temp;
    int   gpu_junction_temp;
    int   gpu_power;
    float gpu_vram_used;
    float fps_1_percent_low;
} __attribute__((packed));
```

## Server Send Path (v1)

- **Where**: `src/fps_socket.cpp`, `fps_socket_broadcast_full(double live_fps)`
- **Action**: Build `fps_socket_header` + existing payload, send together per client.
- **Notes**: Keep non-blocking semantics, EAGAIN policy, and stale-client cutoff.

Example (contiguous buffer):
```c++
fps_socket_header hdr{ 0x4D484653u, 1u, (uint16_t)sizeof(fps_metrics_full_packet) };
fps_metrics_full_packet pkt = {/* fill fields as today */};

uint8_t buf[sizeof(hdr) + sizeof(pkt)];
memcpy(buf, &hdr, sizeof(hdr));
memcpy(buf + sizeof(hdr), &pkt, sizeof(pkt));
ssize_t sent = os_socket_send(client_fd, buf, sizeof(buf), MSG_NOSIGNAL);
```

Alternatively (scatter-gather):
```c++
#include <sys/uio.h>
iovec iov[2] = { { &hdr, sizeof(hdr) }, { &pkt, sizeof(pkt) } };
ssize_t sent = writev(client_fd, iov, 2);
```

## Python Client (v1)

- **Header format**: `HDR_FMT = '=IHH'`, `HDR_SIZE = struct.calcsize(HDR_FMT)`
- **Payload format**: `PAYLOAD_FMT = '=dfffiiiiiff'`, `PAYLOAD_SIZE = 48`

```python
import struct

HDR_FMT = '=IHH'
HDR_SIZE = struct.calcsize(HDR_FMT)
PAYLOAD_FMT = '=dfffiiiiiff'
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)

def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

def read_packet(sock):
    # Read header
    hdr = recv_exact(sock, HDR_SIZE)
    if not hdr:
        return None
    magic, version, size = struct.unpack(HDR_FMT, hdr)
    if magic != 0x4D484653:
        raise ValueError('Bad magic (expected MHFS)')
    if version != 1:
        raise ValueError(f'Unsupported version {version}')
    if size != PAYLOAD_SIZE:
        raise ValueError(f'Unexpected payload size {size} (expected {PAYLOAD_SIZE})')

    # Read payload (v1)
    data = recv_exact(sock, size)
    if not data:
        return None
    return struct.unpack(PAYLOAD_FMT, data)
```

## Backward Compatibility Strategy

- **Server config flag**: `fps_socket_header=0|1` (default `1`).
  - `1`: Send header+payload (new default).
  - `0`: Legacy mode (payload only, 48 bytes).
- **Client dual-mode** (optional): Try to read 8-byte header first; if magic/version/size invalid, fall back to legacy 48-byte reads with a warning.

## Documentation Updates

- **`MetricSocket/SOCKET_FORMAT_SPEC.md`**: Add header section, total packet size (56 bytes for v1), header and payload format strings.
- **`MetricSocket/FULL_METRICS_SOCKET.md`**: Mention versioned header in "Socket Details" and update client example to parse header.

## Versioning Policy

- **v1**: Native-endian payload; header with version=1, size=48.
- **v2+**: On any breaking change (field add/remove/reorder or endianness change):
  - Increment `version` and update `size`.
  - Keep old version handling for a grace period if needed.
  - Consider switching to little-endian (`<`) for cross-arch portability.
- **Future extensibility**: In a later version, add `uint32_t flags` after `size` to advertise optional fields.

## Risks & Mitigations

- **Overhead**: +8 bytes per packet at ~120 Hz is negligible.
- **Partial sends**: Continue current policy (disconnect or short retry). Payload is small; partials are rare on UDS.
- **Race on cleanup**: Unchanged by header; consider addressing separately (mutex or thread confinement).

## Minimal Implementation Checklist

- **[server]** Add `fps_socket_header` type and send alongside payload.
- **[server]** Optional config toggle for legacy mode.
- **[client]** Header parsing; optional legacy fallback.
- **[docs]** Update both spec docs to include header and example code.
