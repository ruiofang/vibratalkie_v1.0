/*
 * RUIO OTA 设备激活接口（可选）
 *
 * 仅在使用 eFuse 序列号 + HMAC 激活方案时需要。
 * 如果不需要设备激活功能，可以删除此文件。
 */

export default function handler(req, res) {
  // 简单实现：直接返回激活成功
  // 如需真正的激活验证，参考 tenclass 的激活流程实现 HMAC 校验
  res.setHeader("Content-Type", "application/json");
  res.status(200).json({ success: true });
}
