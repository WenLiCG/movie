# 首次配置与完整流程验收

全新部署推荐先按 [NEW-VPS.md](NEW-VPS.md) 操作。本文保留原生页面/命令行接线及故障排查流程；最简云盘接线可在定制页面完成。

本包用于 Debian VPS，默认目录 `/opt/cinema`，组合 Prowlarr、Sonarr、qBittorrent、OpenList、Emby 和播放中间件。少量上传协调代码负责完成检查与发布，不重新实现搜索、追剧或播放器。

电影流程：Prowlarr 手动搜索 → qB 下载并做种 → 上传协调器 → 阿里云盘 → Emby 电影墙。电视剧流程：Sonarr 监控并通过 Prowlarr 搜索 → qB 下载 → Sonarr 导入本地剧库 → 上传协调器 → 阿里云盘 → Emby。

**当前代码已在 Debian 12 测试 VPS 完成 PT 搜索、原生接线、真实影片上传发布与直连取流验证。测试生成资源的上传恢复、字幕归档和保留期清理也已通过；另一台全新 VPS 与长时间播放尚未重新验收。** 具体边界见 [DISCOVER.md](DISCOVER.md)。

## 1. 上传并运行一体安装文件

在自己的 Windows 终端上传实际交付的文件；替换 SSH 目标，并沿用自己的端口和私钥参数：

```powershell
scp .\dist\cinema-all-in-one.sh SSH用户@VPS地址:/tmp/cinema-all-in-one.sh
ssh SSH用户@VPS地址
```

在 VPS 执行：

```sh
sudo bash /tmp/cinema-all-in-one.sh
```

也可用 `--dir /你的新目录` 指定位置。按提示填写域名；后续命令均在安装目录执行。不要指向已有应用的数据目录。本包不替换已有 Docker、应用配置或防火墙；缺少的运行依赖按安装器检查结果处理。文件已经包含本包源码，但下载依赖、容器镜像和构建 relay 仍需要网络。

首次启动 OpenList、Emby、Prowlarr、Sonarr、qB；relay/uploader 接线后启动，公网入口最后单独开启。管理端口绑定 VPS 回环地址。qB 的 `56881/TCP+UDP` 用于 BT 通信；有防火墙或云安全组时按自己的网络配置放行，不要把网页管理端口全部开放到公网。

## 2. 本机 SSH 隧道与首次管理员设置

另开 Windows 终端建立隧道，保持它运行：

```powershell
ssh -N -L 5244:127.0.0.1:5244 -L 8096:127.0.0.1:8096 -L 9696:127.0.0.1:9696 -L 8989:127.0.0.1:8989 -L 8080:127.0.0.1:8080 -L 8095:127.0.0.1:8095 SSH用户@VPS地址
```

| 用途 | 本机浏览器入口 | 容器之间的地址 |
| --- | --- | --- |
| OpenList 授权与网盘管理 | http://127.0.0.1:5244/openlist | `http://openlist:5244/openlist` |
| Emby 首次设置 | http://127.0.0.1:8096 | `http://emby:8096` |
| Prowlarr 站点与手动搜索 | http://127.0.0.1:9696/search | `http://prowlarr:9696/search` |
| Sonarr 自动追剧 | http://127.0.0.1:8989/sonarr | `http://sonarr:8989/sonarr` |
| qB 下载管理 | http://127.0.0.1:8080 | `http://qbittorrent:8080` |
| relay 播放验收，接线后可用 | http://127.0.0.1:8095 | `http://relay:8095` |

容器配置不能把右列改成 `127.0.0.1`，它在容器内指向容器自己。

按界面建立各应用管理员，用户名可用 `jsjcjsjc`，密码在初始化页面输入。OpenList v4.2.9 首次访问通过安装向导创建管理员，不再依赖日志中的随机管理员密码；qB 临时密码可运行 `sudo docker compose logs --tail=100 qbittorrent` 取得，登录后保存正式用户名、密码，不公开日志中的密码。

Prowlarr 与 Sonarr 都启用登录认证。Prowlarr 建议选表单认证，认证要求设为始终启用；不选本地地址免认证。公网开启检查要求 Prowlarr 已启用认证、Emby 已完成向导。

## 3. OpenList 授权与目录

1. 添加阿里云盘 Open 驱动，用自己的账号完成官方授权，选择实际存储盘及根目录。
2. 挂载路径设为 `/aliyun`，在其中创建 `Movies` 和 `TV`，确认能打开 `/aliyun/Movies`、`/aliyun/TV`。云盘真实根目录不必也叫 `aliyun`。
3. `/aliyun/.cinema-upload` 留给协调器隔离暂存。不要把它加入 Emby，也不要把 relay 扫描范围扩成整个 `/aliyun`。
4. 关闭该存储的 Web 代理，不填下载代理地址。若另行使用 WebDAV，下载策略选 `302 redirect`；本包上传使用 API，不依赖 WebDAV/FUSE。
5. 保留签名及登录控制，关闭游客访问；从管理页面取得服务端 Token，稍后在 `setup` 提示中输入。

**Movies/TV 成品目录必须由本包的单个 uploader 独占写入。** OpenList v4.2.9 阿里驱动的移动操作没有可依赖的原子“不覆盖同名文件”保证。协调器在发布前检查冲突并拒绝已存在目标，但检查与移动之间仍有时间窗口。不要让人工、CloudDrive、其他同步程序或第二个 uploader 同时写入这两个目录；本包未修改 OpenList 来消除这个并发限制。

阿里 refresh token 只保存在 OpenList。其他组件使用 OpenList Token，不复制阿里凭据。服务端 Token 可能有较大权限，仅保存于安装目录私有配置，不放进浏览器脚本、跳转链接或公开日志。这个独立 OpenList 实例只挂载影院所需存储。

在 OpenList 后台「设置 → 全局」将「链接过期时间」（`link_expiration`，单位小时，0 表示永不过期）设为 6，`sign_all` 保持开启。修改只影响之后签发的链接；此前签发的永不过期链接（`sign` 以 `:0` 结尾）仍有效，要作废需重置 OpenList 令牌。重置后须重新运行 `configure.py --token` 或 `setup`，让 relay、uploader 使用新令牌。网关对本站全部响应统一加 `Cache-Control: private, no-store`，CDN 不得缓存本站。

## 4. Emby 向导与媒体库

在 `http://127.0.0.1:8096` 完成管理员向导。首次用 Emby 管理员登录找片页时自动建立以下媒体库并配置中文刮削，无需额外点击或逐项勾选。成功后仅初始化一次，后续登录及重启保留手动配置；失败时设置页显示黄色提示，可点击“应用推荐刮削设置”重试：

| 媒体库 | 类型 | 容器内目录 |
| --- | --- | --- |
| 电影 | 电影 | `/library/aliyun/Movies` |
| 电视剧 | 电视节目 | `/library/aliyun/TV` |

现在目录为空是正常的，接线和上传后才生成索引。不要加入下载目录、Sonarr 本地剧库或暂存区，避免提前展示未上传完整的文件。

推荐设置：中文、中国地区；TMDB 优先、TVDB 等已安装的默认来源补充；预先下载海报。关闭视频截图、章节缩略图和片头检测。资料和海报保存在 VPS 的 `state/emby`，不向只读云端索引写 NFO 或图片。该目录应随配置备份。

设置仅覆盖本包两个独立媒体库；其他媒体库、已有条目标题、观看记录和手工海报不改写。应用后提交“仅补充缺失资料和图片”，不强制替换。之后新增影片随 Emby 扫描自动获取资料；站点副标题不直接当作 Emby 元数据。识别错误时在 Emby 使用“识别”，按 TMDB/TVDB ID 修正。详见 [Emby 元数据管理](https://emby.media/support/articles/Metadata-manager.html)。

开启支持的实时监控，保留定时“扫描媒体库”任务。创建独立普通观看账号，关闭服务器端音频、视频转码权限。

## 5. 一次接线

完成以上授权、向导，以及 Prowlarr/Sonarr 管理员和 qB 正式密码后，在 VPS 执行：

```sh
cd /opt/cinema
sudo bash install.sh setup
```

按提示输入 qB 用户名；密码和 OpenList Token 使用隐藏输入。脚本读取本包 Prowlarr/Sonarr 配置中的 API Key，设置分类、下载器、剧库和 Prowlarr→Sonarr 连接，成功后构建并启动 relay/uploader。失败时按错误修复，再运行 `setup`；容器运行不代表接线成功。

| 设置 | 预期结果 |
| --- | --- |
| qB 默认保存目录 | `/media/downloads` |
| qB 未完成目录 | `/media/incomplete`，启用未完成扩展名 |
| 电影分类 | `cinema-movies` → `/media/downloads/movies` |
| 独立剧集分类 | `cinema-tv` → `/media/downloads/tv-manual` |
| 追剧分类 | `sonarr-tv` → `/media/downloads/tv` |
| Sonarr 根目录 | `/media/library/tv` |
| Sonarr qB 下载器 | `qbittorrent:8080`，分类 `sonarr-tv` |
| Prowlarr qB 下载器 | `Cinema qB` → `cinema-movies`；`Cinema qB TV` → `cinema-tv`，均连接 `qbittorrent:8080` |
| Prowlarr Sonarr 应用 | Sonarr 地址 `http://sonarr:8989/sonarr`，Prowlarr 地址 `http://prowlarr:9696/search`，完整同步 |

保留 Sonarr 的完成下载处理，关闭该下载器的“移除已完成下载”和“移除失败下载”，导入后分类留空。启用可用时使用硬链接：下载目录与剧库同属 `/media` 挂载和同一文件系统时可少占一份空间，否则需要复制及额外空间。都应保留 qB 原路径和种子，不改名做种文件。

qB 不设置达到分享率后删除文件，不增加自动清理插件。分享率、做种时长按 PT 站要求设置；上传完成不是删种或释放本地空间的条件。

## 6. 添加三个 PT 站点

在 Prowlarr 的 Indexers 页面逐个添加并执行 Test：

| 站点 | 适配器 | 填写凭据 |
| --- | --- | --- |
| HDSky | HDSky | 当前账号 Cookie |
| M-Team | M-Team - TP | 当前账号 API Key |
| SpringSunday | SpringSunday | 当前账号 Cookie |

按界面选择实际地址和所需分类。公开适配器存在不代表账号、VPS IP 或 Cookie 一定可用，以实际测试和搜索为准。凭据只填在管理页面，不粘贴到日志或仓库。认证失败先检查有效期和站点要求。

保存后在 Settings → Apps 确认 Sonarr 测试成功并执行同步；Sonarr 的 Indexers 中应出现支持电视剧分类的站点。完整同步的配置在 Prowlarr 维护，不在两边分别改动。本方案不使用 MoviePilot，也没有其独立用户认证前置。

## 7. 日常电影搜索与追剧

**电影：**Prowlarr Search 筛选电影分类并搜索，选资源后交给 qB 下载器。确认任务分类 `cinema-movies`、保存目录 `/media/downloads/movies`。不要用这个默认电影下载器直接下载电视剧。

**电视剧：**在 Sonarr 添加剧，根目录选 `/media/library/tv`，设质量配置和监控季/集，选择监控并搜索，或主动搜索缺失集。只开启监控不代表立即回搜全部历史资源。新集由 Sonarr 的索引器同步和监控处理，Activity 可查看下载、拒绝和导入原因。

剧集导入后位于 `/media/library/tv`，原下载仍在 `/media/downloads/tv` 做种。电影保留下载内容的目录结构；本包未增加电影自动重命名、质量升级和版本管理服务，识别不准时检查资源命名。

## 8. 上传确认后发布

协调器处理已完成的电影/独立剧集下载与 Sonarr 已导入剧集视频，先写入 `/aliyun/.cinema-upload` 的隔离尝试目录。OpenList 缓存完整文件后返回原生任务编号，程序查询后台任务；只有成功并刷新核对远端大小/可用 SHA1 后才提交上传凭证。入队成功不代表归档成功。

发布移动请求也需要确认最终对象匹配和暂存源消失，之后才标记 `published`。relay 只扫描 Movies/TV，每 10 分钟刷新虚拟索引，再由 Emby 扫描。失败或未知完整性的旧暂存保留隔离，源文件不因上传失败而删除。

到期清理独立执行，默认下载完成 7 天，0 关闭。仅处理已发布且本地哈希、路径、云端对象等再次复核通过的任务；Sonarr 导入副本需要硬链接等关联证据。清理只移除本地文件和相应做种任务，保留云端影片。完整条件和边界见 [DISCOVER.md](DISCOVER.md)。

## 9. 一个域名、多个 HTTPS 路径与权限

完成各应用管理员设置后，实际域名指向 VPS、80/443 可用时执行。可以先开启 HTTPS 再从网页完成云盘授权和 PT 设置；云盘未挂载时不会启动上传器。

```sh
sudo bash install.sh public
```

按提示输入 Emby 管理员用户名和密码。此步骤临时登录、读取全部用户（包括隐藏用户），拒绝存在无密码或允许免密的启用账号，然后注销检查会话；不保存本次 Emby 密码。只完成首次向导不能证明每个账号都有密码，因此需要此检查。

- `https://movie.example.com`：Emby 登录、电影墙和播放，经过严格直连 relay。
- `https://movie.example.com/search/`：Prowlarr 站点管理和手动搜索，**仅管理员使用**。
- `https://movie.example.com/sonarr/`：追剧管理。
- `https://movie.example.com/qb/`：下载与做种管理。
- `https://movie.example.com/openlist/`：云盘授权与存储管理。

域名以安装时填写值为准。`public` 还会检查 Sonarr 登录、OpenList 初始化及游客禁用、qB 匿名 API 被拒绝。原始服务端口继续只绑定回环地址；Emby 管理也可从根路径进入。普通观看用户不应获得管理账号或 PT Cookie。

电影墙“找片 / 追剧”进入 `/discover/`，使用 Emby 管理员登录；Prowlarr 保留为站点和高级搜索管理入口，各应用分别认证。

## 10. 真实任务验收

使用你有权获取的小资源检查，再用跨越扫描周期的文件测试中断：

1. 三站分别完成 Test 和搜索；电影进 `cinema-movies`，独立剧集资源进 `cinema-tv`，Sonarr 追更发起的剧集进 `sonarr-tv`。
2. 下载/检查期间不发布。完成后原文件和 qB 任务仍保留；剧集在 Sonarr 导入成功。
3. 上传中只在隔离区，电影墙不新增。主动中断一次，确认失败尝试不发布；恢复后重试且不删除源。
4. 成品大小正确并能实际读取。同名目标冲突停止发布，不覆盖已有成品；移动未完成不报告成功。
5. relay 生成虚拟目录，Emby 扫描后显示标题和集数。延迟包含约 10 分钟扫描间隔及 Emby 刮削耗时。
6. 从 relay/公网电影墙播放、快进、换片。浏览器中实际视频/HLS 分片最终来自阿里云盘/CDN；播放器显示直接播放或一次 302 都不足以证明流量不经 VPS。
7. 测试原画、阿里云端转码及常用编码/字幕/音轨。云端未就绪、浏览器不兼容或取链失败时停止，不回退到 VPS 视频代理/转码。
8. 退出登录、禁用用户、撤销会话、取消库权限后，重试新的播放/分片请求，确认不能取得新直链。已发出的阿里临时 URL 仍受上游有效期控制，不能即时撤销。
9. 暂停 OpenList，确认播放报错、VPS 不持续传视频、索引和源文件不清空；恢复后重新扫描。

真正完成是：**下载完成 → 上传确认 → 成品发布确认 → 虚拟目录生成 → Emby 入库并可播放**。离线检查不覆盖真实站点风控、阿里权益/限速、客户端兼容性和未知 Emby 响应类型。

## 11. 状态与恢复

在安装目录运行：

```sh
sudo bash install.sh status
sudo docker compose logs --tail=100 uploader sonarr prowlarr openlist qbittorrent
```

| 现象 | 检查与处理 |
| --- | --- |
| 搜索没有结果 | Prowlarr Test、凭据、分类、站点日志 |
| 自动追剧没有下载 | Sonarr 监控范围、质量拒绝、站点同步和搜索结果 |
| 本地下完未上传 | qB 分类/完成状态、Sonarr 导入状态、uploader 日志和文件权限 |
| 上传/发布失败 | OpenList 授权、空间、暂存目录及目标冲突；保留源和旧尝试，修复后重试 |
| 持续 `move_pending` | 不盲目重新提交未知状态的移动。按 `uploader/README.md` 先停 uploader，核实无活动移动任务，再把已确认完整的暂存文件人工移到空目标；重启后会确认发布。不要直接改 SQLite |
| 云端有，墙上没有 | 虚拟目录、大小写、扫描范围、Emby 扫描任务 |
| 有海报但播放失败 | 入口、HTTP 状态、鉴权、链接有效期、云端转码、浏览器兼容性 |
| 视频大流量经过 VPS | 直连验收失败，核对代理/回源；不能把回退当成功 |

relay 默认不保留 Docker 日志，以免上游诊断泄露会话 Token、签名 URL；优先看容器状态、索引和浏览器请求。其他日志也应脱敏后再分享。

`sudo bash install.sh stop` 停止本包服务，不等于删除数据。释放磁盘/网盘前另行决定保种与备份规则。备份配置及 uploader 状态，不通过删除状态数据库来重置上传历史。

接口、版本和许可见仓库根目录 `SOURCES.md`。核查日期：2026-11-02。
