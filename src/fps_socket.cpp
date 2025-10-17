#include "fps_socket.h"
#include "mesa/util/os_socket.h"
#include "logging.h"
#include "fps_metrics.h"
#include <spdlog/spdlog.h>
#include <sys/socket.h>
#include <unistd.h>
#include <errno.h>
#include <cstring>
#include <vector>
#include <algorithm>
#include <chrono>

static int fps_server_socket = -1;
static std::vector<int> fps_clients;

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
        SPDLOG_ERROR("Failed to create FPS socket: {}", strerror(errno));
        return -1;
    }
    
    // Set non-blocking mode to prevent performance impact
    os_socket_block(fps_server_socket, false);
    
    SPDLOG_INFO("FPS socket initialized at @{}", socket_path);
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
        SPDLOG_DEBUG("FPS socket: new client connected (fd={}, total={})", 
                     client, fps_clients.size());
    }
}

void fps_socket_broadcast_full(double live_fps, float live_frametime) {
    // Early exit if no socket or no clients
    if (fps_server_socket < 0 || fps_clients.empty()) return;
    
    // Query percentile metrics from fpsMetrics
    float fps_1_low = 0.0f;
    float fps_0_1_low = 0.0f;
    float fps_97 = 0.0f;
    
    if (fpsmetrics && !fpsmetrics->metrics.empty()) {
        // Find metrics by name (0.001 = 0.1%, 0.01 = 1%, 0.97 = 97%)
        for (const auto& metric : fpsmetrics->metrics) {
            if (metric.name == "0.001") {
                fps_0_1_low = metric.value;
            } else if (metric.name == "0.01") {
                fps_1_low = metric.value;
            } else if (metric.name == "0.97") {
                fps_97 = metric.value;
            }
        }
    }
    
    // Prepare full metrics packet
    struct fps_metrics_full_packet packet;
    packet.fps = live_fps;  // Use live FPS, not currentLogData
    packet.frametime = live_frametime;  // Use live frametime, not currentLogData
    packet.cpu_load = currentLogData.cpu_load;
    packet.cpu_power = currentLogData.cpu_power;
    packet.cpu_mhz = currentLogData.cpu_mhz;
    packet.gpu_load = currentLogData.gpu_load;
    packet.cpu_temp = currentLogData.cpu_temp;
    packet.gpu_temp = currentLogData.gpu_temp;
    packet.gpu_core_clock = currentLogData.gpu_core_clock;
    packet.gpu_mem_clock = currentLogData.gpu_mem_clock;
    packet.gpu_power = currentLogData.gpu_power;
    packet.gpu_vram_used = currentLogData.gpu_vram_used;
    packet.ram_used = currentLogData.ram_used;
    packet.swap_used = currentLogData.swap_used;
    packet.process_rss = currentLogData.process_rss;
    packet.fps_1_percent_low = fps_1_low;
    packet.fps_0_1_percent_low = fps_0_1_low;
    packet.fps_97_percentile = fps_97;
    packet.elapsed_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                            currentLogData.previous).count();
    
    // Broadcast to all clients, removing disconnected ones
    auto it = fps_clients.begin();
    while (it != fps_clients.end()) {
        ssize_t sent = os_socket_send(*it, &packet, sizeof(packet), MSG_NOSIGNAL);
        
        if (sent < 0) {
            // Client disconnected or error (but not EAGAIN/EWOULDBLOCK)
            if (errno != EAGAIN && errno != EWOULDBLOCK) {
                SPDLOG_DEBUG("FPS socket (full): client disconnected (fd={}, error={})", 
                             *it, strerror(errno));
                os_socket_close(*it);
                it = fps_clients.erase(it);
                continue;
            }
        } else if (sent != sizeof(packet)) {
            // Partial send - close this client
            SPDLOG_DEBUG("FPS socket (full): partial send, closing client (fd={})", *it);
            os_socket_close(*it);
            it = fps_clients.erase(it);
            continue;
        }
        ++it;
    }
}

void fps_socket_cleanup() {
    // Close all client connections
    for (int client : fps_clients) {
        os_socket_close(client);
    }
    fps_clients.clear();
    
    // Close server socket
    if (fps_server_socket >= 0) {
        os_socket_close(fps_server_socket);
        fps_server_socket = -1;
        SPDLOG_DEBUG("FPS socket closed");
    }
}
