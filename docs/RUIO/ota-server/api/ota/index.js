/*
 * RUIO OTA 版本检查服务
 *
 * 设备启动时 POST 到此接口，携带设备系统信息 JSON。
 * 服务端比较版本号，如果有新版本则返回固件下载地址。
 *
 * ═══════════════════════════════════════════════
 *   只需修改下面 CONFIG 区域即可，其余代码不用动
 * ═══════════════════════════════════════════════
 */

// ══════════ CONFIG 开始 ══════════

const CONFIG = {
  // 最新固件版本号（与 esp_app_desc 中的版本一致）
  LATEST_VERSION: "1.0.1",

  // 固件 bin 下载地址
  // GitHub Releases 示例:
  //   https://github.com/ruiofang/ruio-xiaozhi-esp32-2.0.5/releases/download/v1.0.1/firmware.bin
  // Gitee Releases 示例:
  //   https://gitee.com/ruiofang/ruio-xiaozhi-esp32-2.0.5/releases/download/v1.0.1/firmware.bin
  // 阿里云 OSS 示例:
  //   https://your-bucket.oss-cn-shenzhen.aliyuncs.com/ota/v1.0.1/firmware.bin
  FIRMWARE_URL: "https://github.com/ruiofang/ruio-xiaozhi-esp32-2.0.5/releases/download/v1.0.1/firmware.bin",

  // 是否强制升级（0=不强制，1=强制，即使版本相同也会升级）
  FORCE_UPDATE: 0,

  // 时区偏移（分钟），UTC+8 = 480
  TIMEZONE_OFFSET: 480,
};

// ══════════ CONFIG 结束 ══════════

function compareVersions(latest, current) {
  const l = latest.split(".").map(Number);
  const c = current.split(".").map(Number);
  const len = Math.max(l.length, c.length);
  for (let i = 0; i < len; i++) {
    const lv = l[i] || 0;
    const cv = c[i] || 0;
    if (lv > cv) return 1;
    if (lv < cv) return -1;
  }
  return 0;
}

export default function handler(req, res) {
  // 支持 GET 和 POST
  let deviceInfo = {};
  if (req.method === "POST" && req.body) {
    deviceInfo = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
  }

  const currentVersion =
    deviceInfo?.application?.version || req.headers["user-agent"]?.match(/xiaozhi\/(\S+)/)?.[1] || "0.0.0";

  const deviceId = req.headers["device-id"] || "unknown";

  console.log(`[OTA] Device: ${deviceId}, current: ${currentVersion}, latest: ${CONFIG.LATEST_VERSION}`);

  const response = {};

  // 版本比较
  if (compareVersions(CONFIG.LATEST_VERSION, currentVersion) > 0) {
    response.firmware = {
      version: CONFIG.LATEST_VERSION,
      url: CONFIG.FIRMWARE_URL,
      force: CONFIG.FORCE_UPDATE,
    };
    console.log(`[OTA] New version available for ${deviceId}`);
  } else {
    console.log(`[OTA] Device ${deviceId} is up to date`);
  }

  // 返回服务器时间
  response.server_time = {
    timestamp: Date.now(),
    timezone_offset: CONFIG.TIMEZONE_OFFSET,
  };

  res.setHeader("Content-Type", "application/json");
  res.status(200).json(response);
}
