# RUIO OTA 自建升级服务部署教程

> 使用 GitHub Releases 存放固件 + Vercel 免费部署版本检查 API

---

## 前提条件

- 一个 GitHub 账号（已有：ruiofang）
- 一个 Vercel 账号（用 GitHub 登录即可，免费）

---

## 第一步：上传固件到 GitHub Releases

### 1.1 编译固件

```bash
cd /home/phi/123/小智/AI_Board/ruio-xiaozhi-esp32-2.0.5
idf.py build
```

产物在 `build/xiaozhi.bin`（或 `build/<项目名>.bin`）。

### 1.2 创建 Release

1. 进入仓库页面：`https://github.com/ruiofang/ruio-xiaozhi-esp32-2.0.5`
2. 点击右侧 **"Releases"** → **"Create a new release"**
3. 填写：
   - **Tag**: `v1.0.1`（与固件版本号一致）
   - **Title**: `v1.0.1`
   - **Description**: 更新说明
4. 在 **"Attach binaries"** 区域上传 `xiaozhi.bin`，重命名为 `firmware.bin`
5. 点击 **"Publish release"**

上传后固件的下载地址格式为：
```
https://github.com/ruiofang/ruio-xiaozhi-esp32-2.0.5/releases/download/v1.0.1/firmware.bin
```

> **Gitee 也可以**：操作类似，地址格式为 `https://gitee.com/用户名/仓库名/releases/download/tag/firmware.bin`

---

## 第二步：部署 Vercel 版本检查服务

### 方式 A：命令行部署（推荐）

#### 2A.1 安装 Vercel CLI

```bash
npm install -g vercel
```

#### 2A.2 登录

```bash
vercel login
```
按提示用 GitHub 账号登录。

#### 2A.3 修改配置

编辑 `docs/RUIO/ota-server/api/ota/index.js`，修改顶部 CONFIG 区域：

```javascript
const CONFIG = {
  LATEST_VERSION: "1.0.1",                    // ← 改为你的版本号
  FIRMWARE_URL: "https://github.com/ruiofang/ruio-xiaozhi-esp32-2.0.5/releases/download/v1.0.1/firmware.bin",  // ← 改为你的固件地址
  FORCE_UPDATE: 0,
  TIMEZONE_OFFSET: 480,
};
```

#### 2A.4 部署

```bash
cd docs/RUIO/ota-server
vercel --prod
```

部署成功后会显示地址，类似：
```
✅ Production: https://ruio-ota-server.vercel.app
```

你的 OTA URL 就是：
```
https://ruio-ota-server.vercel.app/xiaozhi/ota/
```

### 方式 B：网页导入部署

1. 把 `docs/RUIO/ota-server/` 目录推送到一个 GitHub 仓库（可以是独立仓库）
2. 打开 https://vercel.com/new
3. 点击 **"Import Git Repository"**，选择该仓库
4. 点击 **"Deploy"**
5. 完成后在项目 Settings → Domains 中查看你的域名

---

## 第三步：配置设备使用自建 OTA

### 方法 1：WiFi 配网页面修改

设备进入配网模式后，在配网页面的 "Custom OTA URL" 中填入：
```
https://ruio-ota-server.vercel.app/xiaozhi/ota/
```

### 方法 2：修改默认值（编译时写死）

编辑 `main/Kconfig.projbuild`：
```
config OTA_URL
    string "Default OTA URL"
    default "https://ruio-ota-server.vercel.app/xiaozhi/ota/"
```

然后 `idf.py build flash`。

---

## 第四步：验证

### 4.1 用 curl 模拟设备请求

```bash
curl -X POST https://ruio-ota-server.vercel.app/xiaozhi/ota/ \
  -H "Content-Type: application/json" \
  -H "Device-Id: AA:BB:CC:DD:EE:FF" \
  -d '{"application":{"version":"0.0.1"}}' | python3 -m json.tool
```

预期输出：
```json
{
    "firmware": {
        "version": "1.0.1",
        "url": "https://github.com/.../firmware.bin",
        "force": 0
    },
    "server_time": {
        "timestamp": 1711699200000,
        "timezone_offset": 480
    }
}
```

### 4.2 模拟已是最新版本

```bash
curl -X POST https://ruio-ota-server.vercel.app/xiaozhi/ota/ \
  -H "Content-Type: application/json" \
  -d '{"application":{"version":"1.0.1"}}' | python3 -m json.tool
```

预期输出（无 firmware 字段）：
```json
{
    "server_time": {
        "timestamp": 1711699200000,
        "timezone_offset": 480
    }
}
```

---

## 后续更新固件

每次发布新版本只需两步：

1. **上传固件**：在 GitHub 创建新 Release（如 `v1.0.2`），上传 `firmware.bin`
2. **更新配置**：修改 `api/ota/index.js` 中的 `LATEST_VERSION` 和 `FIRMWARE_URL`，重新部署：
   ```bash
   cd docs/RUIO/ota-server
   vercel --prod
   ```

---

## 项目文件结构

```
ota-server/
├── vercel.json           # Vercel 路由配置
├── package.json          # Node.js 包描述
└── api/
    └── ota/
        ├── index.js      # 版本检查接口（POST /xiaozhi/ota/）
        └── activate.js   # 设备激活接口（可选）
```

---

## 常见问题

### Q: GitHub Releases 下载慢？

可以用以下替代方案存放 bin 文件：
- **Gitee Releases**：国内速度快
- **阿里云 OSS**：稳定可控，按流量计费
- **Cloudflare R2**：免费 10GB 存储 + 免出站流量

只需修改 `CONFIG.FIRMWARE_URL` 指向对应地址即可。

### Q: Vercel 有调用限制吗？

免费计划：每月 100GB 带宽，Serverless 函数 100 万次调用。对于 OTA 版本检查完全够用。

### Q: 设备提示 HTTPS 证书错误？

ESP-IDF 默认信任主流 CA。GitHub/Vercel/阿里云 OSS 的证书都是受信任的，不需要额外配置。

### Q: 能不能自定义域名？

Vercel 支持绑定自己的域名。在 Vercel 项目 Settings → Domains 中添加，然后 DNS CNAME 指向 `cname.vercel-dns.com` 即可。
