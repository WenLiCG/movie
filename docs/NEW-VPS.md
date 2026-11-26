# 新 VPS 部署说明

推荐使用定制页面完成云盘和下载器接线；原生手动配置及排查另见 [SETUP.md](SETUP.md)。

## 1. 准备服务器和域名

- 安装器支持 Debian 12/13、amd64/arm64；实际部署验收使用 Debian 12 amd64。不要直接在已有影院数据目录覆盖安装。
- 可按已测试的 2 核、4 GB 内存规模起步。磁盘需要容纳系统、镜像、下载和 OpenList 上传缓存；上传一个未命中秒传的文件时，还需要一份完整缓存。程序提交缓存前检查“文件大小 + 2 GiB”可用空间。
- 准备一个自己的域名，例如 `movie.example.com`，A 记录指向新 VPS。若有 AAAA，也必须指向这台服务器可用的 IPv6。首次建议使用 DNS 直连模式。
- 云安全组/防火墙放行 SSH 实际端口（通常 22/TCP）、80/TCP、443/TCP、56881/TCP+UDP。各应用管理端口只监听回环，不需要公开放行。
- 服务器需要访问 Debian/Docker 仓库、GitHub、Go 模块源、PT 站点及阿里云盘接口。安装包包含项目源码和固定上游版本的构建文件，不是离线镜像包。

如果新旧服务器共用同一个云盘 Movies/TV 成品目录，在新机启用上传前先暂停旧机 uploader，确保只有一个上传器写入。此文为全新安装流程，不迁移旧追剧、账号、上传凭证或做种任务。

## 2. 上传并安装

使用 GitHub 源码时，在仓库根目录运行 `python scripts/build_all_in_one.py`，进入生成的 `dist` 目录。若已有交付包，直接进入包含 `cinema-all-in-one.sh` 的文件夹。PowerShell 执行，替换 SSH 地址：

```powershell
scp .\cinema-all-in-one.sh .\cinema-all-in-one.sh.sha256 root@你的VPS地址:/root/
ssh root@你的VPS地址
```

在 VPS 终端执行：

```bash
cd /root
sha256sum -c cinema-all-in-one.sh.sha256
bash cinema-all-in-one.sh
```

校验应显示 `OK`。按提示输入纯域名，例如 `movie.example.com`，不加 `https://` 或路径。默认安装到 `/opt/cinema`，完整项目源码也会解压到这个目录。

安装器会安装缺少的 Docker 依赖；已有 Docker 不会被卸载替换，缺 Compose v2 时会提示补齐。初次启动 Emby、OpenList、Prowlarr、Sonarr、qBittorrent、定制页面和等待配置的 uploader；此时 relay/公网入口尚未开启。安装过程不更改防火墙。

需要自定义目录时使用 `bash cinema-all-in-one.sh --dir /opt/my-cinema`，后续命令相应替换路径。相同安装包可重跑恢复安装流程，不能当作覆盖旧版本的升级命令。

## 3. 打开管理页面，完成首次账号设置

Windows 双击源码根目录或交付包里的 **Open.cmd**，输入 `root@你的VPS地址` 或实际 SSH 用户名和主机名。随后按 SSH 提示认证，并保持窗口开启。脚本不保存密码，默认 SSH 端口 22；自定义端口可在文件末尾的 ssh 命令增加 `-p 端口`。

也可在本机终端手动建立隧道：

```powershell
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8097:127.0.0.1:8097 -L 127.0.0.1:8095:127.0.0.1:8095 -L 127.0.0.1:5244:127.0.0.1:5244 -L 127.0.0.1:9696:127.0.0.1:9696 -L 127.0.0.1:8989:127.0.0.1:8989 -L 127.0.0.1:8080:127.0.0.1:8080 -L 127.0.0.1:8096:127.0.0.1:8096 root@你的VPS地址
```

在自己电脑的浏览器依次设置：

| 页面 | 首次必须完成的内容 |
| --- | --- |
| `http://127.0.0.1:8096` | 完成 Emby 向导，设置有密码的管理员。媒体库可跳过，首次登录找片页时自动创建并配置中文刮削。 |
| `http://127.0.0.1:5244/openlist/` | 创建 OpenList 管理员，并禁用游客访问。云盘挂载在下一步完成。 |
| `http://127.0.0.1:9696/search/` | 创建 Prowlarr 管理员，认证方式 Forms，Authentication Required=Enabled，不设置免认证 Trusted Networks。 |
| `http://127.0.0.1:8989/sonarr/` | 创建 Sonarr 管理员，同样启用 Forms 和始终认证。 |
| `http://127.0.0.1:8080` | 用 qB 初始 admin 和临时密码登录，设置正式用户名、密码；关闭本地地址/子网免认证。 |

新部署统一在上述五个原生页面设置本地运维用户名 `admin` 和同一个自选管理员密码。每个服务独立保存账号，日后改密须逐个更新，并在影院设置页更新 qB 下载器凭据。为普通观众另外创建 Emby 账号，PT 站点与云盘继续使用各自的授权。

qB 临时密码在 VPS 查看：

```bash
cd /opt/cinema
docker compose logs --tail=100 qbittorrent
```

日志包含初始密码，只在自己的终端查看。

## 4. 在定制页面连接云盘、站点和下载器

打开 `http://127.0.0.1:8097/discover/`，用 **Emby 管理员**登录，进入“设置”：

1. 已有正常的阿里云盘 Open 挂载时可直接选用。创建新挂载时，展开 Refresh Token 引导，访问 [OpenList 授权页](https://api.oplist.org)，选择阿里云盘应用登录，勾选使用 OpenList 提供的参数，扫码并取得阿里云盘 **Open** Refresh Token。只使用该授权方式对应的令牌，详见 [OpenList 官方说明](https://doc.oplist.org/guide/drivers/aliyundrive_open)。
2. 在设置页读取现有挂载，或填入新的顶级路径和 Refresh Token。首次验证 OpenList 和随后配置 qB 时，分别输入刚设置的管理员密码。首次连接时两个用户名留空即可沿用当前 Emby 管理员名。已有站点可填写各自的实际账号。OpenList 密码只用于本次交换管理 Token；qB 凭据供后台下载服务使用，保存于私有配置。
3. 核验已有挂载或新建并启用。默认路径 `/aliyun` 被占用时可用 `/aliyun-drive`。门户准备 Movies、TV、隔离暂存目录并将前两者加入片库。随后配置下载器与追剧，接通三个 qB 分类、Prowlarr 下载器、Sonarr 剧库和应用同步。无需复制应用 API Key，也无需手填 OpenList 内部管理 Token。
4. 点击“配置 Indexer”，在 Prowlarr 添加自己的 PT 站点，填写站点 Cookie 或 API Key，逐个 Test 后保存。在 Prowlarr Settings → Apps 确认 Sonarr 连接与索引器同步。
5. 回到“设置”刷新，黄色提示会列出未完成或异常项。保留天数默认 7；0 为关闭，仅清理已经云端发布并复核成功的本地内容和对应做种任务。

首次用 Emby 管理员登录找片页时，自动创建电影、电视剧两库，设置中文、中国地区，优先 TMDB，关闭视频截图等高消耗功能，无需额外点击。成功后保存初始化标记，后续登录及服务重启保留你调整过的配置；需要恢复推荐配置时使用“Emby 海报与简介 → 应用推荐刮削设置”。初始化失败不阻止登录，设置页显示黄色提示，可重试。已有媒体库保留名称与路径，补充缺失资料和图片，保留手工修改。元数据保存在 VPS 的 `state/emby`，不写云盘。之后新片随 Emby 扫描自动刮削。

也可按 [SETUP.md](SETUP.md) 手工挂载，再运行 `bash install.sh setup` 输入 OpenList 管理 Token 和 qB 账号。

| 下载用途 | qB 分类 | 容器内下载目录 |
| --- | --- | --- |
| 电影 | `cinema-movies` | `/media/downloads/movies` |
| 独立剧集/综艺资源 | `cinema-tv` | `/media/downloads/tv-manual` |
| Sonarr 自动追更 | `sonarr-tv` | `/media/downloads/tv` |

无需手工更改这些目录；主机对应文件位于 `/opt/cinema/media/`。

## 5. 构建播放入口并开启 HTTPS

在 VPS 执行：

```bash
cd /opt/cinema
docker compose --profile connected up -d --build relay uploader
bash install.sh public
bash install.sh status
```

第一条构建 relay，下载固定版本的 Go 上游并应用包内补丁，首次可能需要一些时间。`public` 会提示输入 Emby 管理员，检查所有启用账号都有密码，以及管理应用的认证状态，通过后启动 HTTPS 网关。该检查不会保存本次密码。

若账号检查未通过，按错误回到相应应用修正，再运行 `bash install.sh public`。若证书失败，检查域名 A/AAAA、80/443 放行和端口占用。

| 公网地址 | 用途 |
| --- | --- |
| `https://你的域名/` | 观众用普通 Emby 用户名和密码登录、看片，此地址也可填入 Emby 客户端 |
| `https://你的域名/discover/` | 找片、独立下载、追剧、设置及 VPS 状态 |
| `https://你的域名/search/` | Prowlarr 站点配置 |
| `https://你的域名/sonarr/` | Sonarr 高级管理 |
| `https://你的域名/qb/` | 下载和做种管理 |
| `https://你的域名/openlist/` | 云盘高级管理 |

管理员首次配置或排障时可用 `Open.cmd` 打开 SSH 隧道，关闭窗口后公网服务继续运行。日常观影直接访问 HTTPS 域名。公网原生后台入口先检查 `/discover/` 的 Emby 管理员会话，再由对应应用验证其原生账号。

## 6. 日常使用和验证

- 搜索电影：默认 1080p，无 1080p 则显示全部，体积升序。
- 剧集/综艺“下载此资源”：只下载选中资源，合集整包下载；不创建或修改追剧和版本规则，完成后归档 TV。
- “按此版本追更”：确认正确剧集和所有版本条件后保存规则，默认补齐已有并持续追更。同版缺失时等待。
- 只下几个单集：按此版本追更 → 仅选集下载 → 勾选集数；已有追剧可直接“选集下载”。此入口使用追剧版本规则。
- “删除追剧”：停止监控并移出列表，保留已有文件和正在下载的任务。

先用一个有权获取的小视频跑通下载、云盘校验、发布和播放。下载完成不等于已归档；后台上传成功并校验后才发布，Emby 出现影片还包含约 10 分钟索引刷新和元数据处理时间。影片流量走阿里云盘，取链失败时不回退 VPS 视频中转。原始命名不足的独立综艺可能需要在 Emby 手动匹配。

播放后在“找片与追剧 → 播放记录”核对视频响应的网盘跳转和客户端进度。Emby 的 DirectPlay/DirectStream/Transcode 标签本身不是链路证明。严格客户端验收还应查看浏览器网络面板，确认最终视频或分片响应来自网盘/CDN；服务端不能直接看见设备与 CDN 之间的全部传输情况。

```bash
cd /opt/cinema
bash install.sh status
docker compose logs --tail=80 portal uploader
```

## 7. 备份、源码及网络配置

- `/opt/cinema` 是实际完整工程：Compose、安装脚本、portal、uploader、relay 固定上游及补丁、测试和文档；交付源码 ZIP 也包含相同自定义代码。
- 第三方服务镜像和 relay 上游通过固定版本/摘要获取，不重复捆绑全部第三方仓库或 Docker 镜像；依赖与许可证见 [SOURCES.md](../SOURCES.md)。
- 新机需要重新配置自己的应用账号、PT 凭据和云盘授权。交付包不含 `.env`、`state/`、`media/`、开发工具或旧 VPS 的 BBR 覆盖配置。
- 备份时先停止本项目服务，再保存源码、`.env`、整个 `state/`、存在时的 `compose.override.yaml/.yml` 和所需 `media/`；`bash install.sh stop` 只停止服务，保留数据。`state/` 包括账号、版本规则和上传/清理凭证，应私密保存。
- 新机默认不改 TCP 算法。原测试 VPS 的 BBR 是针对实测丢包配置；上传慢时应先区分未命中秒传、链路速度和真正报错，另行诊断。

此前在 Debian 12 测试机用自建资源模拟到期验证了本地清理。当前版本仍待在第二台全新 VPS 上完成安装；Debian 13/arm64、长时间播放和磁盘压力也需现场验收。

高级管理页的公网入口需要先登录找片页；保留各应用自己的认证。SSH 隧道里的本机端口仍可用于初始化或恢复。
