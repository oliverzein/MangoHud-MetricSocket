# MangoHud Metric Socket Review

## Summary

This review evaluates the Unix Domain Socket implementation that broadcasts MangoHud metrics to clients. It focuses on robustness, performance, and security, and highlights discrepancies between documentation and code. References:
- Code: `src/fps_socket.cpp`, `src/fps_socket.h`, `src/overlay.cpp`, `src/meson.build`
- Docs: `MetricSocket/FULL_METRICS_SOCKET.md`, `MetricSocket/SOCKET_FORMAT_SPEC.md`

## Findings

- **[Socket creation]** Uses abstract UDS via `os_socket_listen_abstract("mangohud-fps-<pid>", 5)`; non-blocking server (`os_socket_block(sock, false)`). Found in `src/fps_socket.cpp`.
- **[Client handling]** Non-blocking accept loop (`fps_socket_accept_clients()`), sets client sockets non-blocking, tracks per-client consecutive EAGAIN/EWOULDBLOCK send failures, and disconnects after >120 failures. Partial sends cause immediate disconnect. Found in `fps_socket_broadcast_full()`.
- **[Packet schema in code]** `struct fps_metrics_full_packet` is 48 bytes, packed, fields:
  - `double fps`, `float fps_avg`, `float cpu_load`, `float cpu_power`, `int gpu_load`, `int cpu_temp`, `int gpu_temp`, `int gpu_junction_temp`, `int gpu_power`, `float gpu_vram_used`, `float fps_1_percent_low`.
  - Endianness: native, no versioning.
- **[Data sourcing]** Most metrics from `currentLogData`; `gpu_junction_temp` read directly from `gpus->active_gpu()->metrics.junction_temp` if available; otherwise `-1`.
- **[Scheduling]** Broadcast is called once per frame from `update_hud_info_with_frametime()` when `OVERLAY_PARAM_ENABLED_fps_socket` is enabled. Accept is called each frame just before broadcast, both on the same thread.
- **[Backpressure policy]** Reasonable: disconnect stale/slow clients after ~1s at 120 FPS. Uses `MSG_NOSIGNAL` to avoid SIGPIPE.
- **[Build integration]** `src/meson.build` includes `fps_socket.cpp` in the build. No platform-specific conditionals around the socket.

## Discrepancies (Docs vs Code)

- **[Packet size/fields]**
  - `SOCKET_FORMAT_SPEC.md` claims a version 2.0, 88-byte format with many fields and a Python unpack string `'=dfffiiiiiiiifffffffQ'`.
  - `FULL_METRICS_SOCKET.md` describes an 84-byte full-metrics packet (older design) and also shows yet another 84-byte Python format.
  - **Actual code (`src/fps_socket.h`) defines a 48-byte packed struct** with 11 fields (up to `fps_1_percent_low`). No elapsed time, no many of the additional floats/ints shown in docs.
  - Conclusion: Documentation is out of sync with the implementation. Clients built from the docs will not interoperate with the current server.

- **[Function signature]**
  - `FULL_METRICS_SOCKET.md` shows `fps_socket_broadcast_full(double live_fps, float live_frametime)`; the code has `void fps_socket_broadcast_full(double live_fps)` and no frametime in the struct.

- **[Buffer sizing]**
  - Docs state default send/recv buffers ~212 KB with capacity/retention expectations; the code does not set `SO_SNDBUF`/`SO_RCVBUF` and relies on defaults.

- **[Percentiles]**
  - Docs mention 1% low, 0.1% low, 97th percentile, avg fps; code computes `fps_1_low` and `fps_avg` only, and puts only `fps_1_percent_low` in the packet (no 0.1%/97%).

## Robustness Assessment

- **[Non-blocking I/O]** Correctly used for server and clients; send path handles EAGAIN/EWOULDBLOCK and drops stale clients after a threshold.
- **[Partial send handling]** Partial sends result in disconnect; acceptable for fixed-size messages on UDS but rare. Consider retry loop to finish sending if partial writes occur, though they are unlikely for 48 bytes.
- **[Error handling]**
  - Accept: non-EAGAIN errors logged at debug level and loop breaks; OK.
  - Send: other errors cause immediate disconnect; OK.
  - Cleanup: closes clients and server socket. Potential race if called concurrently with broadcast (see below).
- **[Thread safety]** `fps_clients` and `fps_client_fail_count` are global and mutated in `fps_socket_accept_clients()`, `fps_socket_broadcast_full()`, and `fps_socket_cleanup()`.
  - In normal operation, accept and broadcast are called on the same thread each frame, so no contention.
  - `fps_socket_cleanup()` is called from `stop_hw_updater()` which can be invoked outside the render/update frame path; potential for concurrent access if a frame is broadcasting while cleanup runs. No mutex protects these structures; a minor race risk on shutdown.
- **[Framing]** Uses a fixed-size struct over SOCK_STREAM without explicit framing. Safe as long as both sides read/write in exact struct-sized chunks. Documentation for clients should state to read exactly `sizeof(struct)` bytes per message.
- **[Versioning/compatibility]** No magic/version or size field; any future struct change is a silent ABI break. Clients cannot detect mismatches except by manual size checks.
- **[Resource limits]** No cap on client count; a malicious local process could open many connections and bloat `fps_clients`. Backlog is 5; once accepted, unlimited growth possible.

## Performance Assessment

- **[Per-frame overhead]** Constructing a 48-byte struct and sending to N clients: negligible on UDS. Non-blocking send avoids stalls.
- **[Metrics sourcing]** Reading from in-memory structures; one extra `gpus->active_gpu()` read for junction temp. Minimal cost.
- **[Buffer sizes]** Not tuning SO_SNDBUF means relying on OS defaults, which are sufficient for 48-byte messages at 120 Hz in almost all cases. Optional tuning could extend tolerance to slow readers.

## Security Assessment

- **[Access control]** Abstract UDS in the abstract namespace does not leverage filesystem permissions. Any local user can discover and connect to `@mangohud-fps-<pid>`.
  - If metrics are not sensitive, this is acceptable. If considered sensitive (e.g., process presence, performance, temps), note the exposure.
- **[Input handling]** Server only writes to clients; it does not read from them, limiting attack surface. Accept loop and send paths check errors properly.
- **[Denial-of-service]** Unlimited clients and no overall rate limiting. A local user can connect many clients, causing per-frame loops to iterate over a large vector and consume CPU. Backpressure only disconnects slow readers, not fast noop readers.

## Recommendations

- **[Align docs and code]** Update either the implementation or the docs to a single agreed wire format. If moving to the documented 88-byte v2.0 format, implement it in `src/fps_socket.h/.cpp`. Otherwise, fix `SOCKET_FORMAT_SPEC.md` and `FULL_METRICS_SOCKET.md` to match the current 48-byte struct and Python examples.
- **[Introduce versioning]** Add a tiny header to the packet (e.g., `uint32_t magic`, `uint16_t version`, `uint16_t size`) before the payload or replace the struct with a versioned struct. This allows clients to verify size/version and fail gracefully.
- **[Optional frametime]** If frametime is needed by clients, add it to the struct (docs currently suggest it in older 84-byte design).
- **[Client cap]** Add a maximum clients limit (e.g., 32) and refuse additional connections with a log warning.
- **[Thread safety]** Guard `fps_clients` and `fps_client_fail_count` with a lightweight mutex, or ensure `fps_socket_cleanup()` is only called from the same thread as broadcast.
- **[Buffer tuning]** Optionally set `SO_SNDBUF` to a higher value (e.g., 256 KB) if large bursts or slow readers are expected. Also consider `TCP_CORK` equivalents are not applicable to UDS; current approach is fine.
- **[Handshake/hello message]** On accept, send a small hello containing version/size so clients can immediately validate before reading streaming data.
- **[Drop partial send disconnect]** Either keep as-is or implement a short retry for partial writes before disconnecting to be robust in rare conditions.
- **[Discovery scope]** If exposure concerns exist, consider a filesystem socket with restrictive permissions in a per-user directory (e.g., `/run/user/<uid>/mangohud-fps-<pid>.sock`) instead of abstract UDS. Trade-off: cleanup handling.

## Suggested Tests

- **[Interoperability]** Write a small client that reads exactly 48 bytes and validates the field mapping. Compare against in-overlay metrics.
- **[Slow reader]** Create a client that never reads to trigger EAGAIN/EWOULDBLOCK and verify disconnection after ~1s.
- **[Many clients]** Connect many clients to measure per-frame overhead and identify the need for a client cap.
- **[Shutdown race]** Stress test shutdown while clients are connected to detect any data races or crashes.
- **[Missing junction temp]** Validate `gpu_junction_temp` is `-1` when unavailable.

## Concrete Action Items

- **[A1]** Decide target packet format and update either code or docs. If adopting 88-byte v2.0: implement fields and adjust `fps_socket_broadcast_full()` accordingly. — COMPLETED (2025-10-21)
- **[A2]** Add versioning (magic/version/size) and document it in both `SOCKET_FORMAT_SPEC.md` and code comments.
- **[A3]** Add a max clients limit and refuse extra connections.
- **[A4]** Protect socket client lists with a mutex or confine cleanup to the broadcast thread. — SELECTED: Mutex strategy (`std::mutex fps_sock_mtx`) guarding `fps_clients` and `fps_client_fail_count`. See `MetricSocket/SOCKET_REVIEW_A4.md` for details.
- **[A5]** Optionally set `SO_SNDBUF` and log actual buffer sizes at init.
- **[A6]** Update Python example(s) to match the final format and clearly show fixed-size reads. — COMPLETED (2025-10-21)

## Code References

- `src/fps_socket.h`: struct `fps_metrics_full_packet` (48 bytes), API declarations.
- `src/fps_socket.cpp`: socket init/accept/broadcast/cleanup, EAGAIN policy, MSG_NOSIGNAL.
- `src/overlay.cpp`: per-frame `fps_socket_accept_clients()` and `fps_socket_broadcast_full(sw_stats.fps)` call site.
- `src/meson.build`: inclusion of `fps_socket.cpp` in the build.
