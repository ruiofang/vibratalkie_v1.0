#ifndef PC_RAW_STREAM_SERVICE_H
#define PC_RAW_STREAM_SERVICE_H

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_timer.h>
#include <sys/socket.h>
#include <netinet/in.h>

class AudioService;

class PcRawStreamService {
public:
    explicit PcRawStreamService(AudioService& audio_service);
    ~PcRawStreamService();

    bool Start();
    void Stop();
    void SetServerDiscoveredCallback(
        std::function<void(const std::string&, uint16_t)> callback);
    void SendAudio(const int16_t* data, size_t samples, int sample_rate,
                   int channels, uint64_t timestamp_us);

private:
    static constexpr size_t kHeaderSize = 32;

    AudioService& audio_service_;
    int socket_ = -1;
    std::atomic<uint32_t> server_ipv4_{0};
    std::atomic<uint16_t> server_port_{0};
    std::atomic<uint32_t> sequence_{0};
    std::atomic<bool> running_{false};
    TaskHandle_t receive_task_ = nullptr;
    TaskHandle_t adc_task_ = nullptr;
    esp_timer_handle_t adc_timer_ = nullptr;
    std::function<void(const std::string&, uint16_t)> server_discovered_callback_;

    bool SendPacket(uint8_t type, const void* payload, size_t payload_size,
                    uint32_t sample_rate, uint16_t channels,
                    uint16_t sample_width, uint64_t timestamp_us, uint16_t flags = 0,
                    uint32_t destination_ipv4 = 0);
    void ReceiveLoop();
    void AdcLoop();
};

#endif
