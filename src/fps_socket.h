#pragma once
#ifndef MANGOHUD_FPS_SOCKET_H
#define MANGOHUD_FPS_SOCKET_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * FPS data packet structure sent to clients
 * Total size: 16 bytes (aligned)
 */
struct fps_data_packet {
    double fps;           // 8 bytes - current frames per second
    float frametime;      // 4 bytes - current frame time in milliseconds
    uint32_t frame_count; // 4 bytes - total frame count
};

/**
 * Full metrics packet structure with all overlay data
 * Total size: 48 bytes (packed)
 */
struct fps_metrics_full_packet {
    double fps;              // 8 bytes - current frames per second
    float fps_avg;           // 4 bytes - average FPS over time window
    float cpu_load;          // 4 bytes - CPU usage percentage
    float cpu_power;         // 4 bytes - CPU power consumption (watts)
    int gpu_load;            // 4 bytes - GPU usage percentage
    int cpu_temp;            // 4 bytes - CPU temperature (°C)
    int gpu_temp;            // 4 bytes - GPU temperature (°C)
    int gpu_junction_temp;   // 4 bytes - GPU junction temperature (°C)
    int gpu_power;           // 4 bytes - GPU power consumption (watts)
    float gpu_vram_used;     // 4 bytes - GPU VRAM used (GiB)
    float fps_1_percent_low; // 4 bytes - 1% low FPS
} __attribute__((packed));

/**
 * Initialize the FPS broadcast socket.
 * Creates a Unix Domain socket at abstract namespace: @mangohud-fps-<pid>
 * 
 * @return Socket file descriptor on success, -1 on failure
 */
int fps_socket_init();

/**
 * Accept new client connections (non-blocking).
 * Should be called periodically to allow new clients to connect.
 */
void fps_socket_accept_clients();

/**
 * Broadcast full metrics data to all connected clients.
 * Includes CPU, GPU, temps, loads, and percentile FPS metrics.
 * Non-blocking operation - won't impact game performance.
 * Automatically removes disconnected clients.
 * 
 * @param live_fps Current frames per second (smoothed)
 */
void fps_socket_broadcast_full(double live_fps);

/**
 * Close the FPS socket and cleanup all resources.
 * Disconnects all clients and closes the server socket.
 */
void fps_socket_cleanup();

#ifdef __cplusplus
}
#endif

#endif // MANGOHUD_FPS_SOCKET_H
