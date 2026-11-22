# 私人影院开发交接

更新于 2026-11-23（北京时间）。项目把 PT 搜索、下载做种、云盘归档和 Emby 播放串在一起。`/discover/` 是管理员操作台，域名根路径是 Emby 电影墙。Python 服务使用标准库，前端直接提供静态文件，relay 通过固定版本的 Go 上游和本仓库补丁构建。

## 2026-11-23 全面复审与修复（分支 `movie-cli-review-20261122`）

本节由真实 Claude CLI（Claude Code，模型 Claude Opus 5.5）生成，会话 ID `95dc92bb-8644-477e-8ecf-09a34ead5ae6`；非机密引用：`movie-cli-review-20261122 @ base 0796cfd7e816ef1878fe4d9a4a589a3ba327eb5e`。工作只在隔离目录 `/workspace/cli-reviews/movie-20261122` 内进行。没有合并、没有部署、没有接触生产数据或凭据，也没有访问真实 PT/网盘。提交 SHA 和 PR 链接以 GitHub 上该分支的实际记录为准，本文不预填。

**范围。** 基线是 `main` 的 `0796cfd`。复审覆盖 portal 后端与前端、uploader/cleanup/archive、relay 补丁与播放记录、Caddy 网关、Compose、安装/升级脚本和文档。uploader、relay/网关、前端三块由本会话派出的只读子审查并行通读，结论由主会话逐条复核后再改代码。下表的“证据”都是本会话在本机运行的结果（作者/控制方证据），**不是独立 QA**。

### 发现与处理

| # | 严重度 | 发现 | 处理 | 回归证据 |
| --- | --- | --- | --- | --- |
| 1 | 高 | **自动清理会删除从未归档的文件。** `cleanup.plan` 只核对视频和 srt/ass/ssa；同一种子里的 `.sup`、`.idx/.sub`、`.mka`、`.rmvb/.wmv/.vob` 等随 `deleteFiles=true` 一起删掉，名字含 sample 的正片也被当作样片跳过。 | **已修复**：`uploader/cleanup.py` 新增 `UNARCHIVED_MEDIA`，命中即 `cleanup_unarchived_media`，保留整个种子，每天复查。名称含 sample 且超过 1 GiB 的视频按正片要求归档。NFO、图片、字体照旧随种子清理。设置页显示原因，[DISCOVER.md](docs/DISCOVER.md#自动清理) 已更新。 | `test_cleanup` 新增 2 项：修复前 2 项 FAIL，修复后通过 |
| 2 | 中 | **relay 播放记录可被匿名伪造和冲掉。** 记录中间件在 `ApiKeyChecker` 之前运行，任意随机 token 的请求（Emby 回 401）也写入日志。约 1.6 万次请求可轮转掉真实记录，100 条伪造行可占满 `/discover/` 列表。 | **已修复**：`relay/playback.go` 只记录已获逐项授权的媒体请求和 Emby 接受（2xx）的会话上报。`portal/playback.py` 同时丢弃非 2xx 会话事件，旧 relay 留下的伪造行也不再显示。relay 镜像标签改为 `cinema-relay:9b049b8-strict5`。 | Go `TestStrictPlaybackIgnoresUnauthorizedEvidence`：旧 `playback.go` FAIL，新代码 PASS；Python `test_rejected_session_reports...`：修复前 FAIL |
| 3 | 中 | **Emby 不可达时无法退出登录。** 退出先走 Emby 管理员复核（503），且先调 Emby `/Sessions/Logout` 再删本地会话，失败时 cookie 仍有效 8 小时。 | **已修复**：`App.logout` 校验 CSRF 后先撤销本地会话，Emby 注销改为尽力而为；退出接口不再依赖 Emby 复核。 | `test_logout_revokes_local_session_even_when_emby_is_unreachable`：修复前 503≠200 |
| 4 | 中 | **portal 无客户端超时。** `ThreadingHTTPServer` 默认无超时，半截请求可永久占住线程。 | **已修复**：`Handler.timeout = 30`。 | `test_stalled_client_does_not_pin_a_worker_thread`：修复前 FAIL |
| 5 | 中 | **待处理的取消/删除每 5 秒重做全部重活。** 等 Emby 扫描等情况下，每轮都会停种、对全部本地文件重算 SHA1、刷新云端列表，而且是持锁执行。 | **已修复（进程内）**：同一请求同一等待码连续出现时，前 3 次照常立即重试，之后指数退避，最长 5 分钟。新请求（`at` 变化）或换了等待码时立即处理。重启会清零。 | `ActionBackoffTests`：修复前 10 次调用都执行（`10 != 4`） |
| 6 | 低-中 | **前端状态问题。** 会话过期或退出后，上次的搜索结果和任务列表仍留在页面上，ID 已失效，点了只会报错。启动时遇到 503 或断网只剩空白页。网关返回非 JSON 的 401 时页面不会退出登录。追更范围可选第 0 季，服务端必然拒绝。窗口每次获得焦点都会覆盖正在编辑的保留天数和凭据面板。重新上传/重新检查按钮在 10 秒轮询后丢失键盘焦点。操作后的刷新若遇上进行中的轮询会被丢弃。播放记录失败时每 10 秒弹一次提示。“下载此资源”之后同一行的“按此版本追更”报“结果已过期”，重新渲染后又能重复下载。 | **已修复**：均在 `portal/app.js`。服务端对 TV 结果改为标记 `_downloaded` 并保留，供追更使用，重复下载会被拒绝；电影结果仍在下载后移除。`#results` 去掉整列表 `aria-live`，由结果数播报。 | 新增 `tests/portal-resilience.test.cjs`：旧代码 exit 1；`test_versions` 更新后验证重复下载被拒 |
| 7 | 低 | 登录密码错误只显示通用错误。 | **已修复**：Emby 401 时显示“用户名或密码不正确”。 | `test_wrong_emby_password_gets_a_specific_message`：修复前 FAIL |
| 8 | 低 | **网关加固。** Emby 根路径会收到管理员 `cinema` cookie，没有 HSTS，Emby 页面可被跨站嵌入框架。relay 是唯一直接面向公网的解析服务，却没有 `cap_drop`。 | **已修复**：relay 路由用锚定正则去掉 `cinema` cookie（不影响 `notcinema=` 等其他 cookie），并加 `X-Frame-Options: SAMEORIGIN`。全站加 `Strict-Transport-Security: max-age=31536000`，不含 includeSubDomains/preload。relay 加 `cap_drop: [ALL]` 和 `no-new-privileges`（本来就以 1000 用户运行）。 | `tests/test_gateway.py`（修复前 2 项 FAIL）；本机 Caddy `validate` 与实际转发测试见下 |
| 9 | 文档 | README 写“本脚本没有自动升级”，与 `scripts/update_portal.sh` 矛盾；“120 项回归”已过时。 | **已修复**。 | — |

**保留未改（经审查设计合理）。** 前端统一用 `node()`/`textContent` 构建 DOM，配合严格 CSP，未发现 XSS。CSRF 由 Origin、JSON 类型、大小和常量时间比较的 token 共同把关，cookie 为 HttpOnly + SameSite=Strict。删除按完整清单哈希确认，清单变化返回 409。relay 逐资源向 Emby 授权、失败即拒绝，日志只存 token 哈希和主机名。uploader 本地路径逐级不跟随符号链接，`moving` 先落库再移动，暂存目录不复用。安装包解包会校验路径、隐藏文件和大小。

### 延后项（需要决策或现场验证）

| 项 | 原因与建议 |
| --- | --- |
| uploader：移动返回 OpenList code 500 后任务停在 `moving/move_pending`，对应的取消/删除也一直 pending | 现有代码刻意“只观察、不重放”，防止与网盘侧移动冲突。是否在“暂存仍在、目标不存在、移动任务为空并静默一段时间”后退回 `uploaded`，需要所有者结合 OpenList/阿里云实际语义决定。建议先在测试环境复现 500。 |
| uploader：按任务名恢复的上传在任务成功后只做大小校验，网盘不返回 SHA1 时会发布 | 是否真能发生取决于阿里云是否返回 `hash_info`，本次未确认。建议：按名称恢复的任务要求 SHA1，或在 `put()` 检测到 `source_changed` 时保留任务 ID 并取消该任务。 |
| uploader：失败或孤立的字幕子任务会让父视频一直显示 `subtitle_pending` | 用户可在任务页重试字幕；是否允许跳过字幕先发布视频属于产品决策。 |
| uploader/cleanup 性能：每轮重建清单、逐任务打开 SQLite、8 MiB 响应上限可能被大型 Emby `/Items` 响应超过 | 需要真实大库数据评估，建议分页（100–200 条）并缓存每轮的文件列表。 |
| relay：日志写入是同步的，在全局锁内进行；改名失败后会静默停止记录；`Bytes` 字段只是首块大小 | 建议改为单个写入 goroutine 加有界队列。 |
| 架构：Emby 与各管理后台同源（`/` 与 `/discover/`、`/sonarr/` 等） | 一旦 Emby web 出现 XSS，就能借同源调用管理接口。根本解决是把管理入口迁到独立子域名，这是**部署形态的破坏性变更**，需要所有者决定。本次只去掉了发往 Emby 的管理员 cookie。 |
| gateway 容器 `cap_drop`、relay `read_only` | 无法在本机运行容器验证上游写路径，未改。 |
| 前端：320px 下“更多”菜单可能左侧溢出；“看片”复用命名窗口会打断正在播放的标签页 | 需要浏览器实测，未改。 |

### 本次实际运行的命令与结果

环境：Linux 6.12，Python 3.13.5，Node v22.23.5（`/workspace/toolchains/node-22`），Go 1.27（`/usr/local/bin/go`）。本机**没有 Docker 和浏览器**。时间均为 UTC（系统时钟）。

- **基线**（2026-11-22T15:50Z，未改代码）：`python3 -m unittest discover -s tests -p 'test_*.py'` 共 162 项，OK（1 项跳过）。三项 Node 检查通过，三个 Bash 脚本语法检查通过。
- **逐项修复前后对照**：用 `git stash push <源文件>` 只回退被修文件，再跑新测试，结果见上表“回归证据”。
  - 第一次对照误把测试文件一起 stash 了，结果无效，已改为只回退源文件后重做。
  - `test_reliability` 直接用模块名运行时报 ImportError（它依赖 discover 的路径），改用 `-s tests -p test_reliability.py -k ActionBackoff`。
  - 退避测试第一版在旧代码上是因为缺少类常量而报错，不是行为失败。已改为字面期望值，现在旧代码上因行为失败（`10 != 4`）。
- **relay 真实补丁链路**：在隔离目录拉取上游 `go-emby2openlist@9b049b885388108c61f234244c1249f9a52a8dd6`，按 Dockerfile 顺序 `git apply` 三个补丁并复制 `playback.go`/`playback_test.go`，然后运行 `go test ./internal/web ./internal/service/emby ./internal/service/m3u8 ./internal/service/openlist -run TestStrict -count=1`。18 个 PASS，`go vet ./internal/service/emby` 无输出。`CGO_ENABLED=0 go build -tags=goexperiment.jsonv2 -trimpath` 成功。换回旧 `playback.go` 时新测试 FAIL。
  - 首次运行时 `web/dist` 目录不存在导致 cp 失败，mkdir 后重跑通过。
  - 这里用的是本机 Go 1.27，不是 Docker 镜像内的 `golang:1.27-alpine`，也没有执行 `docker compose build relay`。
- **Caddy**：本地 `go install` 构建了 Caddy v2.10.2，但它在 Go 1.27 下对**原始未改的** Caddyfile 执行 `validate` 也会 panic，属工具链问题。改用 `caddy@latest`（`caddy version` 显示 `unknown`）后，原始和修改后的 Caddyfile 都显示 `Valid configuration`（`MOVIE_DOMAIN=cinema.example.invalid`）。
  - 本地实际转发测试：用 relay 同款 handle 块指向本机 echo 服务验证 cookie 处理，`cinema=S` 变为空，`cinema=S; emby=1` 变为 `emby=1`，`a=1; cinema=S; b=2` 变为 `a=1;b=2`，`notcinema=keep; x=1` 不变。响应头含 HSTS 和 `X-Frame-Options`。
  - 第一版正则会把 `notcinema=` 改坏，已换成锚定正则并重测。
  - 生产镜像里的 Caddy 版本与本地不同，部署时仍需 `caddy validate`；`update_portal.sh` 本来就会执行这一步。
- **最终全量**（2026-11-22T16:08Z）：Python 171 项 OK（1 项跳过）。`node --test tests/*.test.cjs` 共 4 项通过。`bash -n install.sh scripts/*.sh` 通过，`node --check portal/app.js` 通过。`python3 scripts/build_all_in_one.py --output …` 后 `sha256sum -c` 为 OK，解包得到 76 个文件，解包目录内 Python 171 项 OK、Node 4 项通过。
- **未运行或受阻**：
  - `docker compose --profile '*' config --quiet` 和 `docker compose --profile connected build relay`：本机无 Docker，交给 GitHub Actions。
  - `node tests/check_ui.cjs`：无浏览器/Playwright，未运行。
  - 真实 Emby/qB/OpenList/PT/阿里云盘流程：按授权限制未触碰。
  - 端用户浏览器流程：**未验证**。单元测试和 Node 切片测试不能代替浏览器验收。

### 兼容性、迁移与回滚

- **没有数据库迁移**，也没有新增依赖或新的持久化字段。退避状态只保存在内存里。
- **行为变化（需要知会所有者）**：
  - 含未归档媒体的种子不再自动清理，磁盘占用可能上升；设置页会显示原因，可手动删除。
  - TV 搜索结果下载后仍可用于追更，重复下载会被拒绝。
  - 旧 relay 留下的“请求失败”会话类行不再显示。
  - 浏览器会对域名记住 HSTS 一年。这是网关新增的不可快速撤销项；只针对本域名，不含子域名。
- **回滚**：回退本分支提交即可。服务端回滚需要重新构建 portal/uploader/relay，`update_portal.sh` 带备份与恢复。HSTS 已经下发的浏览器在 max-age 内仍会强制 HTTPS，本站本来就只走 HTTPS，影响有限。

### 发布状态

本地已有 5 个候选提交（含记录本段的提交），位于 `movie-cli-review-20261122`，基于 `0796cfd`。2026-11-22T16:13Z 尝试发布时失败：
- `git push -u origin movie-cli-review-20261122` 返回 `fatal: could not read Username for 'https://github.com': No such device or address`
- `gh auth status` 返回 `You are not logged into any GitHub hosts.`

本会话没有 GitHub 凭据，按授权限制停止重试，也**没有创建 PR**。完整补丁保存在隔离目录：`patches/0001…0005-*.patch`（`git format-patch` 格式）和 `movie-cli-review-20261122.patch`（含新文件的完整 diff）。PR 描述草稿在 `PR-BODY.md`。

### 接手步骤（下一位负责人：项目所有者或其指定的维护/QA）

0. 由有推送权限的人在本工作副本执行 `git push -u origin movie-cli-review-20261122`，再执行 `gh pr create --base main --head movie-cli-review-20261122 --title "Review fixes: cleanup safety, portal resilience, playback evidence, gateway hardening" --body-file ../PR-BODY.md`。只开 PR，不合并。也可以在 `main@0796cfd` 上执行 `git am patches/*.patch`。

1. 在 GitHub 查看本分支 PR 的 Actions 结果，重点是 Compose 配置检查和 relay Docker 构建（含 `TestStrict`）。这两项本机没有运行。
2. 有浏览器的环境运行 `NODE_PATH=<playwright> node tests/check_ui.cjs`，并手动检查以下内容。`check_ui.cjs` 会在 `http://127.0.0.1:<随机端口>/discover/` 自建模拟 API 服务（`server.listen(0,'127.0.0.1')`），手动检查可在其中加断点或改用固定端口：
   - Emby 停止时退出登录
   - 会话过期后列表清空
   - 设置页切窗口不覆盖正在编辑的值
   - 320/390px 宽度下的任务页和“更多”菜单
3. 在独立测试 VPS 上用 `sudo bash /新源码目录/scripts/update_portal.sh /opt/cinema` 升级（不要直接在生产上做），然后核对：
   - relay 能用 `cap_drop` 启动、`caddy validate` 通过、Emby 播放和播放记录正常
   - 用自建媒体（附带 `.sup` 或 `.mka`）确认保留期清理会保留整个种子并显示原因
4. 由所有者决定上面“延后项”中标为需要决策的条目，尤其是 `moving` 卡死的处理策略和是否拆分管理子域名。
5. 合并前需要独立复审，本节结果属于作者自测证据。

## 2026-11-22 交付范围

本批修复从 `02eb4cd` 开始，在 `codex/cinema-review-fixes` 完成后并入 `main`。源码回归、安装包解包回归和模拟部署已完成，真实 VPS 升级与实际客户端验收待执行。接手从 `main` 取得源码，GitHub 回归结果按提交查看。[修复提交](https://github.com/WenLiCG/movie/commit/58c4b6c65aa44eb12691819bed507528e33835c2) · [修复分支回归](https://github.com/WenLiCG/movie/actions/runs/45018426641) · [main 回归](https://github.com/WenLiCG/movie/actions?query=branch%3Amain)

| 范围 | 接手时需要知道的行为 |
| --- | --- |
| 手动删除 | Portal 用完整任务清单生成确认标识，清单变化时返回 409 并要求再次确认。后台仅处理已确认的文件和种子实例，共用或已被替换的文件会保留。升级前未保存确认清单的待执行请求会暂停，可重新预览确认，或选择“保留剩余文件并结束”。 |
| 自动清理 | 保留期关联种子加入时间和完成时间。同哈希重下会重新计算。手动保留意图优先于旧清理计划。qB 删除响应丢失后，后台按已持久化的执行状态复核文件。 |
| 下载、追更和发布 | 无法确认的上传继续占用磁盘预留，其他任务可在容量允许时推进。第 0 集可写入归档记录，损坏的任务记录按单项报错。已确认终止的未发布记录可释放目标路径。重试继续下载会保留原 Sonarr 监控状态。 |
| 门户 | Emby 临时故障使当次校验返回 503，后续可继续用原会话。确认管理员权限失效时要求重新登录。文本预览未命中后仍可按 TVDB ID 查中文名。任务页保留键盘焦点并标记刷新失败。 |
| 升级和交付 | 更新器先核对 Compose 容器归属，然后按发行文件清单更新和回滚。升级会将旧 OpenList 播放令牌复制到 relay 专用目录，门户轮换时仍以原子替换写入。安装包包含两份 Emby 4.10.2 测试夹具。 |

## 接手当天

1. **取得源码。** 从 GitHub 克隆 `WenLiCG/movie` 的 `main`，构建安装包前记录 `git rev-parse HEAD`。打包范围见 [scripts/build_all_in_one.py](scripts/build_all_in_one.py)。
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
node tests/portal-resilience.test.cjs
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
