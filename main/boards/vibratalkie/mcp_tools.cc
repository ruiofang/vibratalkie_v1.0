#include "mcp_tools.h"

#include "mcp_server.h"
#include "axp173.h"
#include "board.h"
#include "power_save_timer.h"
#include "settings.h"

#include <esp_log.h>

#include <cstdio>
#include <string>

namespace {

static const char* TAG = "VibratalkieMcp";

void RegisterPowerOffTool(McpServer& server, Board& board) {
    server.AddTool(
        "self.device.poweroff",
        "关闭设备电源，实现软关机。调用此工具后设备将断电关机。\n"
        "参数:\n"
        "  `confirm`: （可选，bool类型），为true时立即关机，否则先提示用户确认。\n",
        PropertyList({
            Property("confirm", kPropertyTypeBoolean, false)
        }),
        [&board](const PropertyList& properties) -> ReturnValue {
            bool confirm = false;
            try {
                confirm = properties["confirm"].value<bool>();
            } catch (const std::exception&) {
                confirm = false;
            }
            if (!confirm) {
                return std::string("{\"success\": false, \"message\": \"请确认是否关机。请再次调用本工具并设置 confirm=true 以完成关机。\"}");
            }
            board.SoftPowerOff();
            return std::string("{\"success\": true, \"message\": \"设备已关机\"}");
        });
    ESP_LOGD(TAG, "Registered MCP tool: self.device.poweroff");
}

void RegisterPowerSaveTools(McpServer& server, Board& board) {
    auto timer = board.GetPowerSaveTimer();
    if (!timer) {
        ESP_LOGW(TAG, "Power save timer unavailable; skip registering related tools");
        return;
    }

    // 读取持久化参数并恢复设置
    Settings settings("powersave", true);
    bool enabled = settings.GetBool("enabled", true);
    int sleep_sec = settings.GetInt("sleep_to", -1);
    int shutdown_sec = settings.GetInt("shut_to", -1);

    if (sleep_sec != -1 || shutdown_sec != -1) {
        timer->SetTimeout(sleep_sec, shutdown_sec);
    }
    timer->SetEnabled(enabled);

    server.AddTool(
        "self.powersave.enable",
        "开启节能定时器（自动休眠/关机）。当用户要求开启节能模式时使用此工具。",
        PropertyList(),
        [&board](const PropertyList&) -> ReturnValue {
            auto timer = board.GetPowerSaveTimer();
            if (timer) {
                timer->SetEnabled(true);
            }
            Settings settings("powersave", true);
            settings.SetBool("enabled", true);
            return "{\"success\":true,\"message\":\"节能定时器已开启\"}";
        });

    server.AddTool(
        "self.powersave.disable",
        "关闭节能定时器（自动休眠/关机）。当用户要求关闭节能模式时使用此工具。",
        PropertyList(),
        [&board](const PropertyList&) -> ReturnValue {
            auto timer = board.GetPowerSaveTimer();
            if (timer) {
                timer->SetEnabled(false);
            }
            Settings settings("powersave", true);
            settings.SetBool("enabled", false);
            return "{\"success\":true,\"message\":\"节能定时器已关闭\"}";
        });

    server.AddTool(
        "self.powersave.set_timeout",
        "设置休眠和关机超时时间（分钟）。当用户要求设置节能时间时使用此工具。\n"
        "参数:\n"
        "  `sleep_timeout`: 休眠超时时间（分钟），-1表示禁用休眠\n"
        "  `shutdown_timeout`: 关机超时时间（分钟），-1表示禁用关机",
        PropertyList({
            Property("sleep_timeout", kPropertyTypeInteger, -1),
            Property("shutdown_timeout", kPropertyTypeInteger, -1)
        }),
        [&board](const PropertyList& properties) -> ReturnValue {
            int sleep_timeout = properties["sleep_timeout"].value<int>();
            int shutdown_timeout = properties["shutdown_timeout"].value<int>();

            int sleep_sec = sleep_timeout == -1 ? -1 : sleep_timeout * 60;
            int shutdown_sec = shutdown_timeout == -1 ? -1 : shutdown_timeout * 60;

            auto timer = board.GetPowerSaveTimer();
            if (timer) {
                timer->SetTimeout(sleep_sec, shutdown_sec);
            }

            Settings settings("powersave", true);
            settings.SetInt("sleep_to", sleep_sec);
            settings.SetInt("shut_to", shutdown_sec);

            char buf[200];
            std::snprintf(buf, sizeof(buf),
                          "{\"success\":true,\"message\":\"已设置休眠时间%d分钟，关机时间%d分钟\",\"sleep_timeout\":%d,\"shutdown_timeout\":%d}",
                          sleep_timeout, shutdown_timeout, sleep_timeout, shutdown_timeout);
            return std::string(buf);
        });

    server.AddTool(
        "self.powersave.get_status",
        "获取节能定时器当前状态。当用户询问当前节能设置时使用此工具。\n"
        "返回包含是否启用、休眠时间、关机时间的状态信息。",
        PropertyList(),
        [&board](const PropertyList&) -> ReturnValue {
            auto timer = board.GetPowerSaveTimer();
            if (!timer) {
                return "{\"success\":false,\"message\":\"节能定时器不可用\"}";
            }

            bool enabled = timer->IsEnabled();
            int sleep_sec = timer->GetSleepTimeout();
            int shutdown_sec = timer->GetShutdownTimeout();

            int sleep_min = sleep_sec == -1 ? -1 : sleep_sec / 60;
            int shutdown_min = shutdown_sec == -1 ? -1 : shutdown_sec / 60;

            std::string sleep_msg = sleep_min == -1 ? "未设置" : std::to_string(sleep_min) + "分钟";
            std::string shutdown_msg = shutdown_min == -1 ? "未设置" : std::to_string(shutdown_min) + "分钟";

            char buf[300];
            std::snprintf(buf, sizeof(buf),
                          "{\"success\":true,\"enabled\":%s,\"sleep_timeout_min\":%d,\"shutdown_timeout_min\":%d,\"message\":\"%s，休眠时间%s，关机时间%s\"}",
                          enabled ? "true" : "false",
                          sleep_min,
                          shutdown_min,
                          enabled ? "节能定时器已启用" : "节能定时器已禁用",
                          sleep_msg.c_str(),
                          shutdown_msg.c_str());
            return std::string(buf);
        });

    ESP_LOGD(TAG, "Registered MCP tools: self.powersave.*");
}

}  // namespace

void RegisterVibratalkieMcpTools(Board& board) {
    static bool initialized = false;
    if (initialized) {
        return;
    }
    initialized = true;

    auto& server = McpServer::GetInstance();
    RegisterPowerOffTool(server, board);
    RegisterPowerSaveTools(server, board);
}
