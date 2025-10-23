# MangoHud Metric Socket – A4: Thread Safety with Mutex Protection

## Goal

- **[Purpose]** Prevent data races and undefined behavior when accessing `fps_clients` and `fps_client_fail_count` across accept, broadcast, and cleanup paths.
- **[Decision]** Use a lightweight `std::mutex` to guard shared state (Mutex strategy).

## Scope

- **Protected structures**:
  - `fps_clients` (container of client FDs)
  - `fps_client_fail_count` (per-client consecutive EAGAIN/send-fail counters)
  - (Optionally) `server_fd` state transitions during cleanup

## Implementation

### 1) Add a mutex

```cpp
// src/fps_socket.cpp or an internal header
#include <mutex>
static std::mutex fps_sock_mtx; // guards client vectors/maps and server_fd during cleanup
```

### 2) Accept path

```cpp
void fps_socket_accept_clients() {
    for (;;) {
        int client_fd = os_socket_accept(server_fd);
        if (client_fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            // log debug and break on other errors
            break;
        }
        os_socket_block(client_fd, false);

        std::lock_guard<std::mutex> lock(fps_sock_mtx);
        if (fps_clients.size() >= MAX_FPS_SOCKET_CLIENTS) {
            os_socket_close(client_fd);
            continue;
        }
        fps_clients.push_back(client_fd);
        fps_client_fail_count[client_fd] = 0;
    }
}
```

### 3) Broadcast path

```cpp
void fps_socket_broadcast_full(double live_fps) {
    fps_metrics_full_packet pkt = {/* fill from current metrics */};

    std::lock_guard<std::mutex> lock(fps_sock_mtx);
    for (auto it = fps_clients.begin(); it != fps_clients.end(); /* no ++ */) {
        int fd = *it;
        ssize_t n = os_socket_send(fd, &pkt, sizeof(pkt), MSG_NOSIGNAL);
        if (n == (ssize_t)sizeof(pkt)) {
            fps_client_fail_count[fd] = 0;
            ++it;
        } else if (errno == EAGAIN || errno == EWOULDBLOCK) {
            if (++fps_client_fail_count[fd] > 120) {
                os_socket_close(fd);
                fps_client_fail_count.erase(fd);
                it = fps_clients.erase(it);
            } else {
                ++it;
            }
        } else {
            os_socket_close(fd);
            fps_client_fail_count.erase(fd);
            it = fps_clients.erase(it);
        }
    }
}
```

### 4) Cleanup path

```cpp
void fps_socket_cleanup() {
    std::lock_guard<std::mutex> lock(fps_sock_mtx);
    for (int fd : fps_clients) os_socket_close(fd);
    fps_clients.clear();
    fps_client_fail_count.clear();
    if (server_fd >= 0) { os_socket_close(server_fd); server_fd = -1; }
}
```

## Notes & Best Practices

- **[Keep critical sections short]** Do not perform expensive operations (I/O beyond the single send/close) or logging that may block inside the lock.
- **[Single lock]** Use one mutex for all related structures to avoid lock ordering issues.
- **[Iterative erase]** Use iterator-based erasure while holding the lock to avoid invalidation issues.
- **[Integration with A3]** Capacity checks (`fps_clients.size()`) happen under the same mutex.

## Testing

- **[Stress connect/disconnect]** Rapidly connect/disconnect many clients while broadcasting.
- **[Shutdown during broadcast]** Trigger cleanup concurrently to ensure no races or use-after-close.
- **[Sanitizers]** Verify with TSAN/ASAN for race detection.

## Documentation Updates

- Mention Mutex strategy in `MetricSocket/SOCKET_REVIEW.md` under A4.
- No public API changes; purely internal thread-safety improvement.
