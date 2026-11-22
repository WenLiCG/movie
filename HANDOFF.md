# 私人影院开发交接

更新于 2026-11-22（北京时间）。项目把 PT 搜索、下载做种、云盘归档和 Emby 播放串在一起。`/discover/` 是管理员操作台，域名根路径是 Emby 电影墙。Python 服务使用标准库，前端直接提供静态文件，relay 通过固定版本的 Go 上游和本仓库补丁构建。

## 当前修复分支

`codex/cinema-review-fixes` 基于 `main` 的 `02eb4cd`。本批覆盖全库审查中可以在代码和配置中处理的问题。源码回归、安装包解包回归和模拟部署已完成，真实 VPS 升级与实际客户端验收待执行。GitHub 上的回归结果按提交查看。[修复分支](https://github.com/WenLiCG/movie/tree/codex/cinema-review-fixes) · [分支回归](https://github.com/WenLiCG/movie/actions?query=branch%3Acodex%2Fcinema-review-fixes)

| 范围 | 接手时需要知道的行为 |
| --- | --- |
| 手动删除 | Portal 用完整任务清单生成确认标识，清单变化时返回 409 并要求再次确认。后台仅处理已确认的文件和种子实例，共用或已被替换的文件会保留。升级前未保存确认清单的待执行请求会暂停，可重新预览确认，或选择“保留剩余文件并结束”。 |
| 自动清理 | 保留期关联种子加入时间和完成时间。同哈希重下会重新计算。手动保留意图优先于旧清理计划。qB 删除响应丢失后，后台按已持久化的执行状态复核文件。 |
| 下载、追更和发布 | 无法确认的上传继续占用磁盘预留，其他任务可在容量允许时推进。第 0 集可写入归档记录，损坏的任务记录按单项报错。已确认终止的未发布记录可释放目标路径。重试继续下载会保留原 Sonarr 监控状态。 |
| 门户 | Emby 临时故障使当次校验返回 503，后续可继续用原会话。确认管理员权限失效时要求重新登录。文本预览未命中后仍可按 TVDB ID 查中文名。任务页保留键盘焦点并标记刷新失败。 |
| 升级和交付 | 更新器先核对 Compose 容器归属，然后按发行文件清单更新和回滚。升级会将旧 OpenList 播放令牌复制到 relay 专用目录，门户轮换时仍以原子替换写入。安装包包含两份 Emby 4.10.2 测试夹具。 |

## 接手当天

1. **取得源码。** 从 GitHub 克隆 `WenLiCG/movie`。复核本批修复时检出 `codex/cinema-review-fixes`，构建安装包前记录 `git rev-parse HEAD`。打包范围见 [scripts/build_all_in_one.py](scripts/build_all_in_one.py)。
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
| 安装、接线和路由 | [install.sh](install.sh) 提供 `install`、`setup`、`public`、`status`、`stop`。[configure.py](configure.py) 初始化目录与私有配置，[integration.py](integration.py) 调用原生 API。[update_portal.sh](scripts/update_portal.sh) 负责升级，[update_files.py](scripts/update_files.py) 管理发行文件和恢复。[compose.yaml](compose.yaml) 定义挂载，[gateway/Caddyfile](gateway/Caddyfile) 定义公网路由。 |

### 改动时需要守住的边界

- **下载来源分开。** `cinema-movies` 使用 `/media/downloads/movies`，独立剧集或综艺 `cinema-tv` 使用 `/media/downloads/tv-manual`。自动追剧 `sonarr-tv` 先下载到 `/media/downloads/tv`，上传器只收 Sonarr 已导入 `/media/library/tv` 的文件。按版追更必须通过 Sonarr 原生规则，找不到同版就等待。版本状态和原生 Sonarr Profile 需要配套保留。
- **发布以持久凭证为准。** 上传先进入 `/aliyun/.cinema-upload`，经 OpenList 任务成功、远端大小和可用 SHA1 校验，完成移动和回读才记为 `published`。回执丢失先查任务和强哈希证据，保留 SQLite 状态供恢复。`Movies`、`TV` 和暂存区只能由同一个 uploader 写入。匹配的 `srt`、`ass`、`ssa` 字幕先于视频发布。[找片与归档说明](docs/DISCOVER.md)
- **删除范围和保留期按实例校验。** 自动清理默认从当前种子完成算 7 天，`0` 关闭。手动删除默认保留 VPS 文件；选择“删除所有”后，由确认清单确定云端原件、字幕、Emby 索引和本地文件。换单个版本时使用资源级操作。验收删除时使用自建测试媒体。[删除流程](docs/DISCOVER.md#删除所有与重新下载)
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
- 发布新安装包时执行 `python scripts/build_all_in_one.py`，生成 `dist/cinema-all-in-one.sh` 与 `.sha256`，在 Debian 上用 `sha256sum -c cinema-all-in-one.sh.sha256` 校验。每个包使用同次构建生成的校验文件。GitHub Actions 在推送后构建并解包回归，源码交接由仓库提供。[打包清单](scripts/build_all_in_one.py) · [回归工作流](.github/workflows/regression.yml)

## 部署、备份与排查

新机按新 VPS 流程安装。已有部署先核对实际目录，随后从独立的新源码目录执行 `sudo bash /新源码目录/scripts/update_portal.sh /opt/cinema`。此处以 `/opt/cinema` 为例。首次升级旧安装时，缺少发行记录的原有文件会保留；后续升级按发行清单管理。单文件安装器用于新装，同一包可以重新运行以继续安装。[新 VPS 流程](docs/NEW-VPS.md) · [现有部署升级](docs/DISCOVER.md#更新已有部署)

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

## 2026-11-22 验证结果与现场验收

- **源码与安装包。** Windows Python 共发现 162 项，158 项通过、4 项跳过。WSL Linux 源码和解包目录各发现 162 项，均通过 161 项、跳过 Windows 专用项。Windows 首轮一次上传回归失败，单项重复 6 次与最终全量复跑通过。三组 Node 检查及 Chrome 五视图回归在源码和解包目录通过。安装包校验和匹配，解包内容含 72 个文件。
- **relay 与升级。** 上游补丁重放后的 13 个 `TestStrict` 用例通过，Linux/amd64 交叉构建成功。三个 Bash 脚本语法及全 profile Compose 配置检查通过。模拟 Docker 验证了错部署目录在动作前被拒绝，构建启动失败时恢复旧版发行文件，成功升级时迁移令牌。
- **下一次现场验收。** 在独立 Debian 环境用自建小媒体走通下载、归档、删除范围变化、保留期、重新下载、令牌轮换和升级回滚。随后在实际使用的 Emby 客户端核对原画、云端 HLS、长播和拖动进度。Debian 13/arm64 真机、实际 PT 追更和字幕提供方继续按现场条件验收。
- **历史现场证据。** 2026-11-03 至 11-04 的记录覆盖当时的 HTTPS 登录、PT 搜索、云盘发布、实际 307 跳转和自建媒体删除。这些记录可供设计本次现场用例。[历史部署记录](docs/DEPLOYMENT.md)

## 历史记录 · 2026-11-19 PST 公网候选停止交接

用户原话（PM 已核实，position 326）：“停止开发，完成handoff后更新到GitHub”。开发停在这里。本节只记录停止交接，不改上面 2026-11-09 段落的事实，也不是那些历史验收的复测。

按 software-handoff v1.3 / workflow v1.4 摘要执行，技能文件未在本线程加载。完整字段在 [docs/archive/2026-11-19-stop/ARCHIVE-NOTE.md](docs/archive/2026-11-19-stop/ARCHIVE-NOTE.md)。安全包正文在 [docs/archive/2026-11-19-stop/](docs/archive/2026-11-19-stop/)。仓库原先没有这个归档目录。

- **no-go。** 冻结候选仍是 Draft PR #7 head `0aabaaede8ba37b70607416a6fde53a4c2992ce1` / tree `f1617efc5a0645fdd1a9791024c565950e880ad9`，base `c792c3fe3312f77c7022aa7f5fcd30f419dbab44`。独立 QA 第二轮 fail/no-go。第一轮 head `9d6c23fd733b447ff708fcf974cf3687cbb19b62` / tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86` 亦 no-go。两轮结论都保留。2026-11-19 16:08 PST 只读核对时，远程 head 仍是这组 SHA；SHA 未变不是当前 QA 通过。
- **未测。** A 现场 blocked。真实签名 Range 206、WebSocket 101、官方 App、Infuse、滑条、公网层 unrun。残留验证脚本误判是离线桩证据，不能写成 A 现场泄漏。作者 07:16 PT 修复交接是停止前的唯一作者交付，其中“继续修复 / 应用 / 在 A 复验”已被停止指令取代。作者停止后工作区状态未知。
- **未合并、未部署。** PR #7 仍是 Draft。本次只加文档，不合并，不部署，不恢复开发。禁止把本段或归档提交当成功能验收。
