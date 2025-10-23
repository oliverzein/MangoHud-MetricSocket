# MangoHud Metric Socket – A3: Max Clients Limit and Refuse Extra Connections

## Goal

- **[Purpose]** Prevent unbounded growth of connected clients to mitigate local DoS and bound per-frame broadcast cost.
- **[Outcome]** When the maximum number of clients is reached, further incoming connections are refused (immediately closed) with an optional log warning.

## Design

- **[Cap]** Introduce a hard limit, e.g., `MAX_FPS_SOCKET_CLIENTS = 32` (configurable).
- **[Refusal policy]** Continue accepting from the listening socket, but if at capacity:
  - Accept the new connection, immediately set non-blocking to avoid incidental blocking.
  - Optionally send a short one-shot error code/message (if a header is implemented in [A2], a one-shot header with version `0` and size `0` could be sent). Otherwise, simply close.
  - Close the client FD and do not add it to the clients vector.
- **[Logging]** Log a rate-limited warning when refusing (e.g., at most once every N seconds) to avoid log spam under connection storms.

## Server Changes

- **Where**: `src/fps_socket.cpp`
  - Function: `fps_socket_accept_clients()` – where accepted sockets are currently pushed into `fps_clients` and set non-blocking.

### Pseudocode

```c++
// Constants (could be a config)
static constexpr size_t MAX_FPS_SOCKET_CLIENTS = 32; // reasonable default

void fps_socket_accept_clients() {
    for (;;) {
        int client_fd = os_socket_accept(server_fd);
        if (client_fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            // log debug and break on other errors
            break;
        }

        // Always set non-blocking on newly accepted client
        os_socket_block(client_fd, false);

        // Enforce capacity
        if (fps_clients.size() >= MAX_FPS_SOCKET_CLIENTS) {
            // Optional: send a small hello/error if A2 header is enabled
            // (best-effort; ignore errors)
            // os_socket_send(client_fd, "\0", 0, MSG_NOSIGNAL);

            // Log (rate-limited)
            LOG_WARN_RL("fps_socket: refusing connection: at max capacity (%zu)", MAX_FPS_SOCKET_CLIENTS);

            // Close and continue
            os_socket_close(client_fd);
            continue;
        }

        // Normal path: track client
        fps_clients.push_back(client_fd);
        fps_client_fail_count[client_fd] = 0;
    }
}
```

### Configuration (Optional)

- **Config key**: `fps_socket_max_clients=<int>` in `MangoHud.conf`.
  - Default: `32`.
  - Min: `1`. Upper bound: `1024` (practical limit; more increases per-frame overhead).
- **Runtime**: Read once at init; not hot-reloadable.

## Behavior Notes

- **[Backlog vs capacity]** The kernel backlog (e.g., 5) limits pending connections; this cap limits the number of active client FDs. New connections beyond the cap are accepted and immediately closed.
- **[Non-blocking safety]** Always set non-blocking before any optional send to ensure refusal never blocks.
- **[Per-frame cost]** With a cap, `fps_socket_broadcast_full()` cost is bounded by `O(MAX_CLIENTS)`.

## Thread-Safety Considerations

- **Existing risk**: `fps_clients` is accessed in accept, broadcast, and cleanup. If cleanup can run concurrently, consider:
  - Guarding `fps_clients` and `fps_client_fail_count` with a mutex; or
  - Ensuring cleanup is invoked from the same thread/execution path as accept/broadcast.
- **A3 impact**: Capacity checks use `fps_clients.size()` – same synchronization requirements as current code.

## Testing

- **[Capacity enforcement]** Run a script that opens >MAX clients and verify only MAX remain connected (others get closed immediately).
- **[Stability under churn]** Rapidly connect/disconnect clients around the threshold; verify no leaks and no crashes.
- **[Performance]** Measure per-frame send time with MAX clients to confirm bounded cost.
- **[Refusal logging]** Confirm logs appear at most at the rate limit under a connection storm.

## Documentation Updates

- **`MetricSocket/SOCKET_FORMAT_SPEC.md`**: Add a note under "Socket Implementation" that the server limits concurrent clients (default 32) and refuses additional connections.
- **`MetricSocket/FULL_METRICS_SOCKET.md`**: Mention the capacity limit in "Socket Details" and that extra clients are refused.

## Optional Enhancements

- **[Graceful error/hello]** If [A2] header is implemented, send a one-time header with `magic='MHFS'`, `version=0`, `size=0` to signal refusal reason before closing.
- **[Metrics]** Track and expose a counter of refused connections for observability.
- **[Per-user cap]** If needed, implement per-UID limiting (most setups won’t need this as sockets are local).

## Minimal Implementation Checklist

- **[server]** Add `MAX_FPS_SOCKET_CLIENTS` (or config) and enforce in `fps_socket_accept_clients()`.
- **[server]** Ensure non-blocking is set before optional refusal message.
- **[docs]** Update spec and implementation docs to mention the limit and default.
