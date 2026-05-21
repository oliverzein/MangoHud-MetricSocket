#include "fps_socket.h"
#include "mesa/util/os_socket.h"
#include "logging.h"
#include "fps_metrics.h"
#include "gpu.h"
#include <spdlog/spdlog.h>
#include <sys/socket.h>
#include <unistd.h>
#include <errno.h>
#include <cstring>
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <chrono>

static int fps_server_socket = -1;
static std::vector<int> fps_clients;
static std::unordered_map<int, int> fps_client_fail_count;  // Track consecutive send failures

int fps_socket_init() {
    if (fps_server_socket >= 0) {
        SPDLOG_DEBUG("FPS socket already initialized");
        return fps_server_socket;
    }

    // Create socket path with PID for per-process isolation
    char socket_path[256];
    snprintf(socket_path, sizeof(socket_path), "mangohud-fps-%d", getpid());
    
    SPDLOG_DEBUG("Initializing FPS socket: @{}", socket_path);
    
    // Create abstract namespace Unix Domain socket
    fps_server_socket = os_socket_listen_abstract(socket_path, 5);
    
    if (fps_server_socket < 0) {
        if (errno == EADDRINUSE) {
            // Another instance of the layer (e.g. second Vulkan context) already
            // owns the socket for this PID — silently ignore, return -2 so the
            // caller knows to stop retrying.
            SPDLOG_DEBUG("FPS socket already bound by another layer instance, skipping");
            return -2;
        }
        SPDLOG_ERROR("Failed to create FPS socket: {}", strerror(errno));
        return -1;
    }
    
    // Set non-blocking mode to prevent performance impact
    os_socket_block(fps_server_socket, false);
    
    SPDLOG_INFO("FPS socket v0.2 initialized at @{}", socket_path);
    return fps_server_socket;
}

void fps_socket_accept_clients() {
    if (fps_server_socket < 0) return;
    
    // Try to accept new clients (non-blocking)
    // Loop to handle multiple pending connections
    while (true) {
        int client = os_socket_accept(fps_server_socket);
        
        if (client < 0) {
            // No more pending connections
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != ECONNABORTED) {
                SPDLOG_DEBUG("FPS socket accept error: {}", strerror(errno));
            }
            break;
        }
        
        // Set client socket to non-blocking
        os_socket_block(client, false);
        fps_clients.push_back(client);
        fps_client_fail_count[client] = 0;  // Initialize failure counter
        SPDLOG_DEBUG("FPS socket: new client connected (fd={}, total={})", 
                     client, fps_clients.size());
    }
}

void fps_socket_broadcast_full(double live_fps, float frametime_ms) {
    // Early exit if no socket or no clients
    if (fps_server_socket < 0 || fps_clients.empty()) return;
    
    // Query metrics from fpsMetrics
    float fps_1_low = 0.0f;
    float fps_avg = 0.0f;
    
    if (fpsmetrics) {
        // Find metrics by name (0.01 = 1%, AVG = average)
        auto metrics_copy = fpsmetrics->copy_metrics();
        for (const auto& metric : metrics_copy) {
            if (metric.name == "0.01") {
                fps_1_low = metric.value;
            } else if (metric.name == "AVG") {
                fps_avg = metric.value;
            }
        }
    }
    
    // Prepare full metrics packet
    struct fps_metrics_full_packet packet;
    packet.fps = live_fps;
    packet.frametime = frametime_ms;
    packet.fps_avg = fps_avg;
    packet.cpu_load = currentLogData.cpu_load;
    packet.cpu_power = currentLogData.cpu_power;
    packet.gpu_load = currentLogData.gpu_load;
    packet.cpu_temp = currentLogData.cpu_temp;
    packet.gpu_temp = currentLogData.gpu_temp;
    
    // Read junction_temp directly from GPU metrics (not in currentLogData)
    packet.gpu_junction_temp = -1;  // Default if not available
    if (gpus && gpus->active_gpu()) {
        packet.gpu_junction_temp = gpus->active_gpu()->metrics.junction_temp;
    }
    
    packet.gpu_power = currentLogData.gpu_power;
    packet.gpu_vram_used = currentLogData.gpu_vram_used;
    packet.fps_1_percent_low = fps_1_low;
    
    // Broadcast to all clients, removing disconnected ones
    auto it = fps_clients.begin();
    while (it != fps_clients.end()) {
        int client_fd = *it;
        ssize_t sent = os_socket_send(client_fd, &packet, sizeof(packet), MSG_NOSIGNAL);
        
        if (sent < 0) {
            // Check if send failed due to full buffer (stale client)
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                // Buffer full - increment failure counter
                fps_client_fail_count[client_fd]++;
                
                // Disconnect if too many consecutive failures (stale client)
                if (fps_client_fail_count[client_fd] > 120) {  // ~1 second at 120 FPS
                    SPDLOG_WARN("FPS socket: disconnecting stale client (fd={}, {} consecutive failures)", 
                                client_fd, fps_client_fail_count[client_fd]);
                    os_socket_close(client_fd);
                    fps_client_fail_count.erase(client_fd);
                    it = fps_clients.erase(it);
                    continue;
                }
                ++it;
                continue;
            }
            
            // Other error - disconnect immediately
            SPDLOG_DEBUG("FPS socket (full): client disconnected (fd={}, error={})", 
                         client_fd, strerror(errno));
            os_socket_close(client_fd);
            fps_client_fail_count.erase(client_fd);
            it = fps_clients.erase(it);
            continue;
        } else if (sent != sizeof(packet)) {
            // Partial send - close this client
            SPDLOG_DEBUG("FPS socket (full): partial send, closing client (fd={})", client_fd);
            os_socket_close(client_fd);
            fps_client_fail_count.erase(client_fd);
            it = fps_clients.erase(it);
            continue;
        }
        
        // Success - reset failure counter
        fps_client_fail_count[client_fd] = 0;
        ++it;
    }
}

void fps_socket_cleanup() {
    // Close all client connections
    for (int client : fps_clients) {
        os_socket_close(client);
    }
    fps_clients.clear();
    fps_client_fail_count.clear();
    
    // Close server socket
    if (fps_server_socket >= 0) {
        os_socket_close(fps_server_socket);
        fps_server_socket = -1;
        SPDLOG_DEBUG("FPS socket closed");
    }
}
