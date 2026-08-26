#include "pc_raw_stream_service.h"

#include "audio/audio_service.h"
#include "boards/common/board.h"
#include "sdkconfig.h"

#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <esp_log.h>
#include <esp_timer.h>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

namespace {
constexpr char kMagic[] = {'V', 'T', 'K', '1'};
constexpr uint8_t kProtocolVersion = 1;
constexpr uint8_t kTypeAudioCapture = 1;
constexpr uint8_t kTypeAdcCapture = 2;
constexpr uint8_t kTypeAudioPlayback = 3;
constexpr uint8_t kTypeServerHello = 4;
constexpr uint8_t kTypeDeviceDiscovery = 5;
constexpr uint16_t kFlagAdcTimestampedRecords = 1;
constexpr size_t kAdcRecordSize = sizeof(uint64_t) + sizeof(int16_t);
const char* TAG = "PcRawStream";

void PutU16(uint8_t* out, uint16_t value) {
    out[0] = static_cast<uint8_t>(value >> 8);
    out[1] = static_cast<uint8_t>(value);
}

void PutU32(uint8_t* out, uint32_t value) {
    out[0] = static_cast<uint8_t>(value >> 24);
    out[1] = static_cast<uint8_t>(value >> 16);
    out[2] = static_cast<uint8_t>(value >> 8);
    out[3] = static_cast<uint8_t>(value);
}

void PutU64(uint8_t* out, uint64_t value) {
    PutU32(out, static_cast<uint32_t>(value >> 32));
    PutU32(out + 4, static_cast<uint32_t>(value));
}

uint16_t GetU16(const uint8_t* in) {
    return (static_cast<uint16_t>(in[0]) << 8) | in[1];
}

uint32_t GetU32(const uint8_t* in) {
    return (static_cast<uint32_t>(in[0]) << 24) |
           (static_cast<uint32_t>(in[1]) << 16) |
           (static_cast<uint32_t>(in[2]) << 8) | in[3];
}

uint64_t GetU64(const uint8_t* in) {
    return (static_cast<uint64_t>(GetU32(in)) << 32) | GetU32(in + 4);
}
}  // namespace

PcRawStreamService::PcRawStreamService(AudioService& audio_service)
    : audio_service_(audio_service) {}

PcRawStreamService::~PcRawStreamService() {
    Stop();
}

void PcRawStreamService::SetServerDiscoveredCallback(
        std::function<void(const std::string&, uint16_t)> callback) {
    server_discovered_callback_ = std::move(callback);
}

bool PcRawStreamService::Start() {
    socket_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (socket_ < 0) {
        ESP_LOGE(TAG, "socket failed: errno=%d", errno);
        return false;
    }

    int reuse = 1;
    setsockopt(socket_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    int broadcast = 1;
    if (setsockopt(socket_, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast)) != 0) {
        ESP_LOGE(TAG, "enable UDP broadcast failed: errno=%d", errno);
        close(socket_);
        socket_ = -1;
        return false;
    }
    int send_buffer_size = 64 * 1024;
    setsockopt(socket_, SOL_SOCKET, SO_SNDBUF, &send_buffer_size, sizeof(send_buffer_size));
    timeval timeout = {.tv_sec = 0, .tv_usec = 200000};
    setsockopt(socket_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    sockaddr_in local_addr = {};
    local_addr.sin_family = AF_INET;
    local_addr.sin_port = htons(CONFIG_PC_RAW_STREAM_PORT);
    local_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(socket_, reinterpret_cast<sockaddr*>(&local_addr), sizeof(local_addr)) != 0) {
        ESP_LOGE(TAG, "bind port %d failed: errno=%d", CONFIG_PC_RAW_STREAM_PORT, errno);
        close(socket_);
        socket_ = -1;
        return false;
    }

    sockaddr_in configured_server = {};
    configured_server.sin_family = AF_INET;
    configured_server.sin_port = htons(CONFIG_PC_RAW_STREAM_PORT);
    if (inet_pton(AF_INET, CONFIG_PC_RAW_STREAM_SERVER, &configured_server.sin_addr) != 1) {
        ESP_LOGE(TAG, "invalid PC server IPv4 address: %s", CONFIG_PC_RAW_STREAM_SERVER);
        close(socket_);
        socket_ = -1;
        return false;
    }
    server_ipv4_.store(configured_server.sin_addr.s_addr);
    server_port_.store(configured_server.sin_port);

    running_ = true;
    xTaskCreate([](void* arg) {
        static_cast<PcRawStreamService*>(arg)->ReceiveLoop();
        vTaskDelete(nullptr);
    }, "pc_stream_rx", 4096, this, 6, &receive_task_);
    xTaskCreate([](void* arg) {
        static_cast<PcRawStreamService*>(arg)->AdcLoop();
        vTaskDelete(nullptr);
    }, "pc_stream_adc", 3072, this, 5, &adc_task_);
    esp_timer_create_args_t adc_timer_args = {
        .callback = [](void* arg) {
            auto* self = static_cast<PcRawStreamService*>(arg);
            if (self->adc_task_ != nullptr) {
                xTaskNotifyGive(self->adc_task_);
            }
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "pc_adc_sample",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(esp_timer_create(&adc_timer_args, &adc_timer_));
    ESP_ERROR_CHECK(esp_timer_start_periodic(
        adc_timer_, 1000000ULL / CONFIG_PC_RAW_STREAM_ADC_RATE));

    audio_service_.EnableRawAudioCapture(true);
    ESP_LOGI(TAG, "raw audio/ADC -> %s:%d; playback <- UDP %d",
             CONFIG_PC_RAW_STREAM_SERVER, CONFIG_PC_RAW_STREAM_PORT,
             CONFIG_PC_RAW_STREAM_PORT);
    return true;
}

void PcRawStreamService::Stop() {
    audio_service_.EnableRawAudioCapture(false);
    running_ = false;
    if (adc_timer_ != nullptr) {
        esp_timer_stop(adc_timer_);
        esp_timer_delete(adc_timer_);
        adc_timer_ = nullptr;
    }
    if (adc_task_ != nullptr) {
        xTaskNotifyGive(adc_task_);
    }
    if (socket_ >= 0) {
        shutdown(socket_, SHUT_RDWR);
        close(socket_);
        socket_ = -1;
    }
}

bool PcRawStreamService::SendPacket(uint8_t type, const void* payload, size_t payload_size,
                                    uint32_t sample_rate, uint16_t channels,
                                    uint16_t sample_width, uint64_t timestamp_us, uint16_t flags,
                                    uint32_t destination_ipv4) {
    if (socket_ < 0 || payload_size > 4096) {
        return false;
    }
    std::vector<uint8_t> packet(kHeaderSize + payload_size);
    std::memcpy(packet.data(), kMagic, sizeof(kMagic));
    packet[4] = kProtocolVersion;
    packet[5] = type;
    PutU16(packet.data() + 6, flags);
    PutU64(packet.data() + 8, timestamp_us);
    PutU32(packet.data() + 16, sequence_.fetch_add(1));
    PutU32(packet.data() + 20, sample_rate);
    PutU16(packet.data() + 24, channels);
    PutU16(packet.data() + 26, sample_width);
    PutU32(packet.data() + 28, payload_size);
    if (payload_size > 0) {
        std::memcpy(packet.data() + kHeaderSize, payload, payload_size);
    }

    sockaddr_in destination = {};
    destination.sin_family = AF_INET;
    destination.sin_port = server_port_.load();
    destination.sin_addr.s_addr = destination_ipv4 != 0
        ? destination_ipv4 : server_ipv4_.load();
    // Never let Wi-Fi backpressure block the I2S capture task. Missing datagrams
    // are reconstructed as timestamped silence by the PC receiver.
    ssize_t sent = sendto(socket_, packet.data(), packet.size(), MSG_DONTWAIT,
                          reinterpret_cast<sockaddr*>(&destination), sizeof(destination));
    return sent == static_cast<ssize_t>(packet.size());
}

void PcRawStreamService::SendAudio(const int16_t* data, size_t samples, int sample_rate,
                                   int channels, uint64_t timestamp_us) {
    if (!SendPacket(kTypeAudioCapture, data, samples * sizeof(int16_t),
                    sample_rate, channels, sizeof(int16_t), timestamp_us)) {
        ESP_LOGD(TAG, "audio UDP send failed: errno=%d", errno);
    }
}

void PcRawStreamService::AdcLoop() {
    const size_t batch_samples = std::max<size_t>(1, CONFIG_PC_RAW_STREAM_ADC_RATE / 50);
    std::vector<uint8_t> batch;
    batch.reserve(batch_samples * kAdcRecordSize);
    uint64_t first_timestamp_us = 0;
    uint64_t last_discovery_us = 0;
    while (running_) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (!running_) {
            break;
        }
        int16_t raw = 0;
        const uint64_t read_start_us = esp_timer_get_time();
        if (Board::GetInstance().ReadAdcRaw(raw)) {
            const uint64_t timestamp_us = (read_start_us + esp_timer_get_time()) / 2;
            if (timestamp_us - last_discovery_us >= 2000000) {
                SendPacket(kTypeDeviceDiscovery, nullptr, 0, 0, 0, 0,
                           timestamp_us, 0, htonl(INADDR_BROADCAST));
                last_discovery_us = timestamp_us;
            }
            if (batch.empty()) {
                first_timestamp_us = timestamp_us;
            }
            const size_t offset = batch.size();
            batch.resize(offset + kAdcRecordSize);
            PutU64(batch.data() + offset, timestamp_us);
            const uint16_t raw_bits = static_cast<uint16_t>(raw);
            batch[offset + sizeof(uint64_t)] = static_cast<uint8_t>(raw_bits);
            batch[offset + sizeof(uint64_t) + 1] = static_cast<uint8_t>(raw_bits >> 8);

            if (batch.size() >= batch_samples * kAdcRecordSize) {
                SendPacket(kTypeAdcCapture, batch.data(), batch.size(),
                           CONFIG_PC_RAW_STREAM_ADC_RATE, 1, sizeof(raw),
                           first_timestamp_us, kFlagAdcTimestampedRecords);
                batch.clear();
            }
        }
    }
    adc_task_ = nullptr;
}

void PcRawStreamService::ReceiveLoop() {
    std::vector<uint8_t> packet(4096);
    uint32_t playback_received = 0;
    uint32_t playback_dropped = 0;
    while (running_) {
        sockaddr_in peer = {};
        socklen_t peer_len = sizeof(peer);
        ssize_t size = recvfrom(socket_, packet.data(), packet.size(), 0,
                                reinterpret_cast<sockaddr*>(&peer), &peer_len);
        if (size < static_cast<ssize_t>(kHeaderSize)) {
            continue;
        }
        const uint8_t* header = packet.data();
        if (std::memcmp(header, kMagic, sizeof(kMagic)) != 0 ||
            header[4] != kProtocolVersion) {
            continue;
        }
        if (header[5] == kTypeServerHello) {
            const uint32_t previous = server_ipv4_.exchange(peer.sin_addr.s_addr);
            server_port_.store(peer.sin_port);
            if (previous != peer.sin_addr.s_addr) {
                char address[INET_ADDRSTRLEN] = {};
                inet_ntop(AF_INET, &peer.sin_addr, address, sizeof(address));
                ESP_LOGI(TAG, "PC discovered, switching stream to unicast %s:%u",
                         address, ntohs(peer.sin_port));
                if (server_discovered_callback_) {
                    server_discovered_callback_(address, ntohs(peer.sin_port));
                }
            }
            continue;
        }
        if (header[5] != kTypeAudioPlayback) {
            continue;
        }
        const uint32_t payload_size = GetU32(header + 28);
        const uint32_t sample_rate = GetU32(header + 20);
        const uint16_t channels = GetU16(header + 24);
        const uint16_t sample_width = GetU16(header + 26);
        if (sample_width != sizeof(int16_t) || payload_size % sizeof(int16_t) != 0 ||
            payload_size + kHeaderSize != static_cast<size_t>(size)) {
            continue;
        }
        std::vector<int16_t> pcm(payload_size / sizeof(int16_t));
        std::memcpy(pcm.data(), packet.data() + kHeaderSize, payload_size);
        ++playback_received;
        if (!audio_service_.PushPcmToPlaybackQueue(
                std::move(pcm), sample_rate, channels, GetU64(header + 8))) {
            ++playback_dropped;
        }
        if (playback_received % 200 == 0) {
            ESP_LOGI(TAG, "PC playback RX: packets=%lu, dropped=%lu",
                     static_cast<unsigned long>(playback_received),
                     static_cast<unsigned long>(playback_dropped));
        }
    }
    receive_task_ = nullptr;
}
