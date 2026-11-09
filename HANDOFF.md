# 私人影院开发交接

更新于 2026-11-09。项目把 PT 搜索、下载做种、云盘归档和 Emby 看片串成一条链。`/discover/` 是管理员操作台，域名根路径是 Emby 电影墙。开发主要涉及 Python、原生 JavaScript、Go 补丁和 Docker Compose。Python 服务使用标准库，前端直接提供静态文件。

## 接手当天

1. **取得源码。** 使用有权限的 GitHub 账号克隆 `WenLiCG/movie`，从 `main` 开始。安装包可按下文从源码构建。2026-11-09 核对现有 `dist/cinema-all-in-one.sh` 时，包内 60 个文件与当时的打包白名单源码逐一一致，SHA-256 为 `5d3fac4a47107bf2fbf216a85014d600fef933c6c5807768b7524e37a08f9d23`。打包范围见 [scripts/build_all_in_one.py](scripts/build_all_in_one.py)。
2. **跑本地回归。** 从项目根目录运行下一节的命令。做 UI 改动可先用模拟 API 跑页面，验证真实下载或播放再使用隔离测试环境。
3. **需要接管现有部署时，先取得权限。** 请项目所有者单独交接 VPS 的 SSH/sudo、域名和 Cloudflare DNS 管理、Emby 管理员、各原生应用的管理入口，以及阿里云盘和 PT 的授权途径。先在 VPS 核对当前服务和配置，再参照有日期的 [部署记录](docs/DEPLOYMENT.md)。

## 业务链路与代码入口

```text
手动电影或剧集   Portal / Prowlarr → qB 完成下载 ──────────────┐
自动追剧         Sonarr ← Prowlarr → qB 下载 → Sonarr 导入 ────┤
                                                              ↓
                              uploader → OpenList 暂存 → 校验发布到 Movies / TV
                                                              ↓
                              relay 生成虚拟索引 → Emby 扫描并展示
播放             Emby 客户端 → relay 逐资源授权 → 跳转阿里云盘/CDN 取视频
```

| 改动范围 | 入口与需要一起看的代码 |
| --- | --- |
| 找片、任务、登录、设置 | [portal/server.py](portal/server.py) 的 `Handler` 和 `App` 提供 `/discover/api/*`。界面在 [portal/app.js](portal/app.js)、[portal/index.html](portal/index.html)、[portal/style.css](portal/style.css)。主题和 Emby 搜索入口另看 [portal/theme.js](portal/theme.js) 与 [relay/custom-js](relay/custom-js)。 |
| 站点结果、中文名、按版追更 | [portal/server.py](portal/server.py) 编排 Prowlarr、Sonarr 和 Emby 调用。[portal/metadata.py](portal/metadata.py) 解析站点名称元数据，[portal/versions.py](portal/versions.py) 处理版本条件和原生 Profile 校验，[archive.py](archive.py) 共享追更范围、归档记录和锁。 |
| 上传、删除、保留期 | [uploader/uploader.py](uploader/uploader.py) 的 `Sources` 决定来源，`Uploader` 处理暂存、校验、发布和持久化任务操作。[uploader/cleanup.py](uploader/cleanup.py) 处理到期的本地清理。改任务状态时一并检查 `App.task_action`、`Uploader.process_actions` 和 [archive.py](archive.py)。 |
| 直连、虚拟索引和播放记录 | [relay/Dockerfile](relay/Dockerfile) 拉取固定的 `go-emby2openlist` 提交，依次应用 `strict-direct.patch`、`managed-token.patch` 和 `playback.patch`，编入 `playback.go` 并运行 `TestStrict`。记录由 [portal/playback.py](portal/playback.py) 读取。修改以 `relay/` 为准，本机 `.upstream/` 是开发缓存。 |
| 安装、接线和路由 | [install.sh](install.sh) 提供 `install`、`setup`、`public`、`status`、`stop`。[configure.py](configure.py) 初始化目录与私有配置，[integration.py](integration.py) 调用原生 API 接线和配置 Emby 刮削。[compose.yaml](compose.yaml) 定义容器和挂载，[gateway/Caddyfile](gateway/Caddyfile) 定义公网路由及管理认证。 |

### 改动时需要守住的边界

- **下载来源分开。** `cinema-movies` 使用 `/media/downloads/movies`，独立剧集或综艺 `cinema-tv` 使用 `/media/downloads/tv-manual`。自动追剧 `sonarr-tv` 先下载到 `/media/downloads/tv`，上传器只收 Sonarr 已导入 `/media/library/tv` 的文件。按版追更必须通过 Sonarr 原生规则，找不到同版就等待。版本状态和原生 Sonarr Profile 需要配套保留。
- **发布以持久凭证为准。** 上传先进入 `/aliyun/.cinema-upload`，经 OpenList 任务成功、远端大小和可用 SHA1 校验，完成移动和回读才记为 `published`。回执丢失先查任务与远端，保留 SQLite 状态供恢复。`Movies`、`TV` 和暂存区只能由同一个 uploader 写入，OpenList 阿里驱动缺少原子同名保护。当前支持匹配的 `srt`、`ass`、`ssa` 字幕随片归档，字幕发布成功后才发布视频。以现行代码和 [找片与归档说明](docs/DISCOVER.md) 为准，根 README 和 uploader README 的字幕描述是旧状态。
- **自动清理和云端删除是两条路径。** 自动清理默认从下载完成算 7 天，`0` 关闭。清理前复核归档记录、云端、本地哈希、路径和共享文件，追剧还要求 Sonarr 导入副本的硬链接关联。手动删除默认保留 VPS 文件。显式选“删除所有”才会按选中资源删除云端原件和字幕、移出虚拟索引、扫描并回读 Emby，随后清理对应本地文件和任务。项目级操作覆盖该剧所有资源，换单个版本用资源级操作。验收删除使用自建测试媒体。见 [删除流程](docs/DISCOVER.md#删除所有与重新下载)。
- **索引和播放授权独立验证。** Emby 的 `/library` 只挂载 `state/library` 虚拟索引。relay 逐资源校验 Emby 授权和云端路径，取链失败直接拒绝。检查播放时同时看视频或分片的云盘/CDN 响应及客户端进度。自动移除索引关闭，外部手动删片留下旧版本时，先确认云盘目录健康、旧对象确实消失，再备份移出对应索引并扫描 Emby。见 [播放记录与直连证据](docs/DISCOVER.md#播放记录与直连证据) 和 [旧索引处理](docs/DISCOVER.md#删除后换版与旧索引)。

## 本地验证与打包

从项目根目录运行。Windows 可用 `python`，Debian 通常用 `python3`。Node 仅用于开发检查。

```bash
python -m unittest discover -s tests -p 'test_*.py'
node tests/search-entry.test.cjs
node tests/watch-handoff.test.cjs
node tests/task-controls.test.cjs
bash -n install.sh
bash -n scripts/update_portal.sh
bash -n scripts/openlist-start.sh
```

- 浏览器回归 `node tests/check_ui.cjs` 使用本机 Chrome 和可被 Node 解析的 Playwright（可设 `NODE_PATH`），数据由测试中的本地 HTTP 服务模拟。界面规范见 [DESIGN.md](DESIGN.md)。
- Linux/Docker 环境下，设置 `export MOVIE_DOMAIN=cinema.example.invalid` 后，可用 `docker compose --profile '*' config --quiet` 检查 Compose，用 `docker compose --profile connected build relay` 重放 Go 补丁、执行 Go 回归并构建。源码许可及固定版本见 [SOURCES.md](SOURCES.md)。
- 发布新安装包时执行 `python scripts/build_all_in_one.py`，生成 `dist/cinema-all-in-one.sh` 与 `.sha256`，在 Debian 上用 `sha256sum -c cinema-all-in-one.sh.sha256` 校验。安装包只带白名单源码，这份交接和现场部署记录随源码目录交付。

## 部署、备份与排查

已有部署按记录位于 Debian 12 amd64 VPS 的 `/opt/cinema`。新机走 [NEW-VPS.md](docs/NEW-VPS.md)，首次管理员配置可用 [Open.cmd](Open.cmd) 建 SSH 隧道。现有部署从独立的新源码目录执行 `sudo bash /新源码目录/scripts/update_portal.sh /opt/cinema`，按 [更新说明](docs/DISCOVER.md#更新已有部署) 核对备份和运行状态。单文件安装器用于新装，同一包可重跑恢复安装流程，遇到旧版本目录会拒绝覆盖。

**备份优先级。** 保留源码、`.env`、全部 `state/`、存在时的 `compose.override.yaml` 或 `.yml`，以及仍需保留的 `media/`。`state/uploader/data/uploads.sqlite3` 是上传、归档、任务动作和本地清理凭证，`state/uploader/versions.json` 与 `state/sonarr/` 一起备份。`state/library/` 是虚拟索引，`state/relay/lib/playback/` 是滚动播放日志。升级脚本在 `backups/` 生成私有备份和 SQLite 在线快照，失败时尝试恢复旧源码及镜像，数据库仍是当前状态。需要手工恢复时按同一备份点核对原生服务数据库和上传状态。凭据通过私密渠道移交，源码包、日志和公开记录里只保留必要的状态信息。

在 VPS 部署目录先执行只读检查。

```bash
cd /opt/cinema
sudo bash install.sh status
sudo docker compose logs --tail=100 portal uploader sonarr prowlarr
```

| 现象 | 优先核对 |
| --- | --- |
| 无法登录或原生后台打不开 | Caddy 路由和 portal 管理员鉴权，再查原生应用认证。公网 `/search/`、`/sonarr/`、`/qb/`、`/openlist/` 有影院管理员前置检查，应用自身认证也保留。隧道可分别访问各回环端口，portal 为 `8097`，relay 为 `8095`。 |
| 下载 100% 但 Emby 没有影片 | qB 分类 → Sonarr 导入（自动追剧）→ `uploads.sqlite3` 状态和 uploader 错误 → OpenList 任务及发布目录 → relay 索引 → Emby 扫描。OpenList 会先在 VPS 缓存文件，遇到大文件要同时检查下载和上传缓存空间。 |
| 列表有片但打不开 | 核对选中的 Emby 媒体版本、对应云端对象和授权，再看 `/discover/` 播放记录及客户端网络。relay 的容器日志禁用，排查时避免记录完整签名 URL。 |

## 验收状态与待办

- **历史现场证据。** 2026-11-03 至 11-04 的记录覆盖真实 HTTPS 登录和 PT 搜索、真实影片云盘发布、直连跳转，以及自建视频和字幕的上传恢复、到期清理、云端联动删除与重新归档。多版本影片验证了授权和实际 307 跳转。原始截图和浏览器记录在验收工作区的 `.impeccable/review/`。详见 [部署记录顶部](docs/DEPLOYMENT.md) 和 [恢复与空间保护](docs/DISCOVER.md#恢复与空间保护)。
- **本次工作区复核。** Windows 上 Python 共运行 143 项，139 项通过、4 项平台条件跳过。3 个 Node 回归、模拟数据的 Chrome 界面回归、Bash 语法和 Compose 静态检查均通过。已有安装包的外部 SHA-256 和内部源码清单均匹配。
- **待现场验收。** 第二台空白 VPS 的完整部署，Debian 13/arm64 真机安装，真实客户端长时间播放与续播、实际视频/分片流量及手机真机，还有磁盘压力测试。长期真实 PT 追更和字幕提供方自动下载也需按实际账号及资源核验。现网服务状态以接手当天查询为准。
