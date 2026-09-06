# 城管台账

个人用的社区城管巡查台账：一台 NAS 部署，手机上随手拍照上报、跟踪整改销号，电脑端导出材料。

## 主要功能

- **单账号**：一个 4 位密码登录（默认 `0000`，登录后在「修改密码」改掉）。连续输错 10 次锁 5 分钟，防暴力试
- **巡查记录**：选乡镇（饶州街道 / 鄱阳镇）→ 选小区（自动补全）→ 点选问题分类（13 类）→ 写描述 → 传照片（可多张、原图保留；电脑端可从微信复制图片直接 Ctrl+V 贴进来）；待处理 → 整改销号，现场照与整改照前后对比留痕
- **数据统计**：按乡镇 / 问题分类 / 小区排行，选起止日期，并可切换时间口径 —— 智能（默认，已结案按结案时间、未结案按录入时间）、按录入时间、按结案时间；每项都能点进去看明细
- **导出**：台账导出页按时间段 / 乡镇 / 分类 / 小区组合筛选，改完条件预览和下载立刻刷新，下载单个 Excel（表格内嵌原图，材料可用）
- **离线可记**：弱网提交失败自动暂存手机本地（含照片），网络恢复一键补传
- **PWA**：浏览器「添加到主屏幕」后像 App 一样全屏使用，登录状态常记 180 天

## NAS 部署

```bash
# 1. 把本项目拷到 NAS 上，例如 /volume1/docker/chengguan
cd /volume1/docker/chengguan
# 2. 一键构建并启动
docker compose up -d --build
```

- 访问地址：`http://NAS的IP:8755`（手机在同一局域网直接用浏览器打开）
- 数据都在 `data/`（数据库）和 `uploads/`（照片）两个目录，换容器、升级镜像数据不丢，**备份拷这两个目录即可**
- 容器异常自动重启（restart: unless-stopped）

## APK 与在线更新

- **APK 不在代码仓库里**（GitHub 公开仓库不放安装包），由 NAS 本地目录分发：
  1. NAS 建文件夹 `chengguan-updates`，放入 `chengguan.apk` 和 `latest.json` 两个文件
  2. 容器 `chengguan` 新增存储卷映射：宿主机 `chengguan-updates` 目录 → 容器内 `/app/static/apk`
  3. 重启容器后，手机 App 自动到 `/static/apk/latest.json` 检查更新，有新版本会提示下载安装
- 以后 APK 出新版：把新 `chengguan.apk` + `latest.json` 替换进 NAS 文件夹即可，**不用重建容器/镜像**

## 升级

```bash
cd /volume1/docker/chengguan
git pull
docker compose up -d --build
```

或直接用 Docker Hub 现成镜像：

```bash
docker run -d --name chengguan \
  -p 8755:5000 \
  -v /volume1/docker/chengguan/data:/app/data \
  -v /volume1/docker/chengguan/uploads:/app/uploads \
  --restart unless-stopped \
  piggecn/chengguan:latest
```

## 配置项

| 环境变量 | 说明 |
|---|---|
| `OWNER_PIN` | 主账号的 4 位密码。留空就是 `0000`，登录后到「修改密码」改掉 |
| `SECRET_KEY` | 会话签名密钥。留空自动生成并写到 `data/.secret_key`（权限 600）；改值会让已登录的会话失效 |
| `DATA_DIR` | 数据库目录，容器内默认 `/app/data` |
| `UPLOADS_DIR` | 照片目录，容器内默认 `/app/uploads` |

## 首次使用

1. 打开站点，输 4 位密码（默认 `0000`）登录
2. 右上角「主账号」→「修改密码」改掉初始密码
3. 手机上「添加到主屏幕」，即可像 App 一样随手使用
4. 弱网 / 断网也能先记下，恢复网络后自动补传
