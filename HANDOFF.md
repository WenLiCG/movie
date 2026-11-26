# 私人影院开发交接

更新于 2026-11-26（北京时间）。项目把 PT 搜索、下载做种、云盘归档和 Emby 播放串在一起。`/discover/` 是管理员操作台，域名根路径是 Emby 电影墙。Python 服务使用标准库，前端直接提供静态文件，relay 通过固定版本的 Go 上游和本仓库补丁构建。

## 唯一管理员账号

**整套影院只使用一套管理员账号和密码。用户名统一为 `admin`，所有管理入口使用同一个管理员密码。** `/discover/` 通过 Emby 验证身份。直接登录 Emby、OpenList、Indexer 管理后台（Prowlarr）、Sonarr 或 qBittorrent 时，都使用这套凭据。首次部署、重装和交接时沿用这项约定。

五个原生服务各自保存凭据。改密时，在五处把原有 `admin` 更新为同一个新密码，然后到 `/discover/` 的设置页用新密码重新配置下载器与追剧。交接时通过私密渠道传递密码。

普通观众使用独立的 Emby 观影账号。VPS 的 SSH 登录、第三方 PT 站点凭据、云盘授权和服务间 API 令牌各有用途，分别管理。

## 2026-11-26 交付复审（基线 `ee40e86`）

本机原有 `main@d961963`、另一个旧版工作树均干净。获取远端后，本地主线独有 0 个提交、远端独有 1 个提交，15 个文件有差异。此次从已合并 PR #17 的 `ee40e86` 快进开分支，保留原工作树、历史分支及本机忽略的运维文件。应用修复提交为 `cd10d91`。[交付 PR #18](https://github.com/WenLiCG/movie/pull/18)

| 范围 | 可复现问题及处理 |
| --- | --- |
| 高 · 云盘和本地清理 | 靠 SHA1 找回的上传，移动响应丢失后曾可仅凭大小标记为已发布。现等待最终对象提供匹配的 SHA1，并保留源文件。OpenList 列表缺名、同页或跨页重名、总数改变时停止本轮判断，避免把清单异常当成文件已消失。 |
| 高 · 下载控制 | 整组手动暂停时，旧逻辑只移除第一个匹配的磁盘暂停标记。现在移除所选项目全部标记；其他项目继续接受空间保护。任务操作核对完整归档历史，超过列表显示上限的旧记录也能随项目恢复。 |
| 中 · 操作反馈 | 追更与选集提交显示进行中，可关闭对话框继续浏览。旧请求结束时只更新发起该请求的对话框。空间不足提示按 MiB 向上报告还需释放的容量。 |
| 中 · 身份与服务 | 登录先预留会话名额，避免已满时向 Emby 申请新令牌。电影墙只在当前账号身份与管理员策略一致时展示管理入口。relay 移除固定上游额外开启的容器网络调试监听。 |
| 中 · 更新与回滚 | 管理脚本对同一部署目录加互斥锁。升级后从新门户容器内部请求匿名会话接口，持续未返回 401 会触发回滚。原先运行的门户恢复后也必须响应检查，失败会提示从备份核查。Linux 用临时目录和模拟 Docker 覆盖互斥、新旧门户启动失败及网关切换失败。 |
| 性能 | qB 文件与归档记录在每个种子内按两个源路径一次建立索引，重名保留原有首个匹配。1000 文件、1000 条记录的本地匹配微基准由约 213 ms 降至 10 ms，结果逐项一致。该数值仅覆盖此段匹配。 |

**本次验证**

- Windows Python 3.14.3 全量 240 项，235 通过、5 项平台跳过。WSL Linux Python 3.14.4 源码与安装器解包目录各运行 240 项，各通过 239 项，跳过 Windows 专用启动器。安装器 SHA-256、内嵌校验与 81 个发行文件通过核对。Bash、Python、JavaScript 语法、Ruff 语法和未定义名等检查、mypy 默认模式及全 profile Compose 配置均通过。mypy 使用 uploader 实际模块路径，未启用全项目严格类型模式。
- 4 组 Node 测试和两套 Chrome 回归通过，覆盖 320–1440px。实际启动门户，用 Chrome 登录、搜索、提交测试下载、暂停和恢复、读取任务、保存保留期、退出，再验证会话返回 401。此流程使用本机 HTTP 模拟上游和临时状态。另在窄屏、深色主题下核对页面，覆盖选集及追更慢响应。
- 固定上游与全部补丁在 Go 1.27.4 通过模块校验、五包严格测试、vet 与 Linux/amd64 构建。实际启动 relay，通过模拟 Emby 请求获得 200，监听清单仅有配置的应用端口。发布二进制 govulncheck 报告 0 项可调用漏洞，另列有 2 条包级与 1 条模块级未调用公告。CI 使用的 Playwright 1.65.1 经 npm audit 返回 0 项。
- Bandit 中高等级扫描退出码为 1。复核 12 项告警，包括 5 个容器监听或端口检查、2 处读取服务本地 XML、3 处 SQL 构造（固定片段、内部列名或绑定占位符），以及 2 处与 OpenList 对接的 SHA1 文件校验。
- 本机没有 Docker daemon。GitHub 在 `cd10d91` 的独立 runner 上从安装包解包构建 relay、portal、uploader，启动 portal/uploader，核对匿名会话 401、等待配置状态及 Caddy 配置，推送和 PR 两次运行均通过。[推送回归](https://github.com/WenLiCG/movie/actions/runs/45844497554) · [PR 回归](https://github.com/WenLiCG/movie/actions/runs/45844533998)

交付范围为仓库源码和可构建的安装包。此次未操作测试 VPS、真实 PT 站点或云盘。现场版本与上次实测记录见下节。接手后，优先用有权使用的小视频验证当前自定义挂载的下载、上传哈希、CDN 取流，再在独立 Debian 环境演练升级回滚。数据库结构和服务凭据格式沿用现有版本。

## 当前状态与待决事项（2026-11-26 汇总）

本节保留上一次现场核验结果。最新源码修复和交付证据见上节，后面的历次记录对应各自注明的版本。

- **在设置页切换归档挂载。** 以 GitHub 与本地共同提交 `6674df3` 为基线。管理员在 `/discover/` 读取 OpenList 挂载，核验已有的阿里云盘 Open 挂载，或在空闲的顶级路径创建一个。默认路径仍为 `/aliyun`。先准备远端 Movies、TV、.cinema-upload，再把两个成品目录追加至原 Emby 媒体库，最后发布 relay 扫描清单并保存新的归档路径。重复提交可以恢复部分完成的片库设置。旧路径及其任务继续可用，qB 本地保存路径和做种状态独立于云盘选择。同一云盘目录通过两个名字挂载仍共享实际文件，应选择独立归档目录。

- **运行与验证。** `state/uploader/config.json` 保存当前挂载 `cloud_mount` 和历史挂载 `cloud_mounts`，旧配置缺省为 `/aliyun`。relay 在每次扫描开始时读取 `state/relay-token/cinema-mounts.json`；升级需一起重建 portal、uploader、relay，并给 portal 挂载原有的 `state/library:/library`。之后在网页选择新盘即可。回归覆盖同名路径冲突、并发页面、旧任务与字幕归属、上传回执、Emby 追加路径以及删除范围。真实阿里云盘授权、上传和播放需在具备授权的站点验证。

- **测试站发布记录。** VPS 运行 [PR #16](https://github.com/WenLiCG/movie/pull/16) 的应用版本 `0a16b24`。验收结束后，提交 `b619075` 的 README 和 HANDOFF 已同步到 VPS。升级只替换 portal、uploader 和 relay，核对了另外五个容器的 ID、旧媒体索引和只读媒体挂载。该次升级完成时有八个服务运行，重启计数均为 0。该次升级的备份位置记录在 VPS 对应发布目录的 `backup.path`。

- **挂载功能发布时的公网实测。** Chrome 使用 Emby 管理员登录 `/discover/` 并读取真实 OpenList 挂载清单，两次请求均返回 HTTP 200；匿名访问会话接口返回 401。验收时唯一的 `/aliyun` 使用 Local 驱动，设置页将它标为已占用。输入空闲的顶级路径可看到新建表单，本轮未提交云盘授权或切换。

- **当前下载与追剧状态（11 月 26 日）。** 管理员启用阿里云盘 Open 挂载后，配置下载器时出现“本地应用 qBittorrent 暂时无法连接或响应超时”。现场 Compose 覆盖文件将 qB 限定在 `disabled` profile，容器未启动。移除该服务的停用配置后，公网设置页已成功接线；九个服务运行，qB WebUI 仅在容器网络内可达，公网原生管理入口经影院网关鉴权。门户成功登录 qB 并回读三个下载分类，Sonarr 已同步两个站点，设置页七项检查全部通过。验收时种子列表为空，实际下载、云端归档与播放仍待用授权测试影片验证。

- **本地和 CI 验证。** WSL Linux 源码和安装包解包后各运行 234 项 Python 测试，各通过 233 项、按平台跳过 1 项。安装包 SHA-256、4 套 Node 检查、两套 Chrome 桌面与移动流程、Compose 配置及 Go 严格播放和扫描测试均通过。隔离的 Emby 4.10.2.0 追加目录返回 HTTP 204，同一媒体库 ID 回读到新旧两个路径。发布使用的 Go 1.27.4 relay 二进制经 govulncheck 检查，可调用漏洞为 0 项。

- **管理员登录验收（11 月 26 日）。** 测试站已在原用户 ID 上统一 Emby、OpenList、Prowlarr 和 Sonarr 的管理员登录；qBittorrent 5.3.0 也已使用同一套凭据完成原生 API 登录。普通观众可登录电影墙，管理门户仅允许 Emby 管理员访问。

- **主线与回归。** `main` 已合并 [PR #15 的复审修复](https://github.com/WenLiCG/movie/pull/15)、PR #16 的自定义挂载改动以及 2026-11-26 公网双账号实测后的体验修复。GitHub Actions 的 `Regression` 验证源码、安装包、浏览器流程、镜像构建、服务启动和 Caddy 配置；VPS 部署单独执行。[应用版本 CI](https://github.com/WenLiCG/movie/actions/runs/45822372966) · [现场验收文档 CI](https://github.com/WenLiCG/movie/actions/runs/45825759567)
- **适用范围。** 面向个人或小规模使用。安全措施按此规模设计：管理员会话、CSRF、严格直连、上传凭证和删除确认清单。
- **观影入口。** 普通观众通过域名根路径使用自己的 Emby 用户名和密码，登录后不显示管理员专用的“找片 / 追剧”入口。验收时用无痕浏览器或全新客户端访问该地址，应直接到达 Emby 登录界面。若先进入 Cloudflare Access 的邮箱或身份提供方登录页，应核对覆盖观影主机的 Access 应用。管理员仍须在 `/discover/` 使用 Emby 管理员账号。SSH 隧道用于首次配置、维护及回环端口访问。
- **公网双账号实测与待决项（11 月 26 日）。** 使用普通观众账号与管理员账号在测试站完成了非破坏性全流程实测：普通观众在 `/discover/` 被正确拦截（HTTP 400 内联提示），Emby 电影墙登录正常，`/search/`、`/sonarr/`、`/qb/`、`/openlist/` 全部受管理员鉴权拦截。测试站既有 `/aliyun` 上的占位片（`MV4-Test-Short`/`MV4-Test-Long`）因对应 Local 驱动返回 `HTTP 307 → 主站点` 而无法播放，播放记录准确归类为“直连尚未确认”。管理员端热种推荐、电影/剧集搜索、筛选器、追更弹窗、设置页 7 项检查、VPS 指标与亮/深色及 390px 布局均通过；实测暴露的 Emby 主题按钮无底色边框、非管理员可见“找片 / 追剧”、Emby 改写 hash 导致“看片”交接超时、搜索分类尾随逗号、自定义挂载错误前缀和设置页列失衡均已在本轮源码修复（待按常规 `update_portal.sh` 发布到 VPS）。
- **尚未现场验收。** 全新 Debian VPS 重装、本次升级备份的实际回滚、真实 PT 下载并归档至 `/aliyun2` 后的阿里云盘 CDN 直连播放、真实 PT 追更、大文件上传、阿里云盘真实删除、物理客户端长时间播放。

需要所有者决定或现场数据才能处理的事项（均未改变现有行为）：

| 事项 | 当前行为 | 处理建议 |
| --- | --- | --- |
| OpenList 移动返回 500 等非授权错误 | 任务停在 `moving`（显示“正在等待云端移动任务确认”），只观察不重放，避免与网盘侧移动冲突 | 先在测试环境复现。是否在“暂存仍在、目标不存在、无移动任务并静默一段时间”后退回 `uploaded`，需要结合 OpenList/阿里云实际语义决定 |
| 字幕子任务失败 | 父视频保持 `subtitle_pending`，任务页可重新上传字幕 | 是否允许跳过字幕先发布视频属于产品决策 |
| 管理入口与 Emby 同源 | 发往 Emby 的 `cinema` Cookie 已剥离；同源 XSS 理论上仍可调用管理路径 | 根治需要把 `/discover/` 和原生后台迁到独立子域，属于部署形态变更 |
| 超大片库性能 | uploader/cleanup 每轮重建候选并逐项打开 SQLite；单次响应上限 8 MiB | 需要真实大库数据评估后再做分页和缓存 |
| relay 播放日志 | 同步写入、全局锁内执行，改名失败后静默停止记录 | 低优先级；可改为单写入 goroutine |
| 网关容器 `cap_drop`、relay `read_only` | 未启用 | 需要能运行容器的环境验证上游写路径 |

已在后续提交中解决、旧记录里仍列为待办的两项：按任务名找回的上传现在要求云端 SHA1 与源文件一致才发布（`uploader.py` 的 `poll_upload`）；自动清理改为白名单，只有 NFO、常见图片和字体可不经归档随种子删除，`.mks`、`.ttml`、样片等未归档文件会保留整个种子（`cleanup.py` 的 `needs_archive`）。

行为提示：HSTS 一年（不含子域，回滚服务端无法立即撤销）；含未归档文件的种子不自动清理，磁盘可能增长；取消/删除的退避只保存在内存中，重启后清零。

## 2026-11-26 公网双账号实测与体验修复

**测试基线。** 以 `main@d961963`（含 PR #16 自定义云盘挂载与统一管理员账号）为基线，对已部署测试站执行非破坏性真机端到端实测（桌面 1280×900 与移动 390×844、亮/深色主题、普通观众与管理员双账号，不触发任何下载、修改或删除）。

| 严重度 | 实测 / 复审发现 | 修复方案 | 回归证据 |
| --- | --- | --- | --- |
| 高 | 点击门户顶部“看片”时，Emby 4.10.2.0 的 SPA 路由在 `custom.js` 加载前即将 `location.hash` 改写为 `#!/startup/manuallogin.html`，导致 `search.js` 仅检查 `location.hash` 时漏掉 `cinemaHandoff` 并让按钮挂起 25 秒超时；浏览器拦截弹窗时亦无直接跳转兜底 | `relay/custom-js/search.js` 同时检查 `location.search` 与 `location.hash`；`portal/app.js` 在弹窗被拦截或交接失败时提供“直接前往电影墙”按钮 | `tests/watch-handoff.test.cjs`、`tests/independent-ui-flows.cjs` |
| 高 | PR #16 切换自定义挂载（非 `/aliyun`）后，`integration.py` 抛出的“必须使用阿里云盘 Open 驱动”错误以挂载路径开头，未命中 `portal/server.py` 的 `USER_ERROR_PREFIXES` 而被吞成泛化提示 | 错误文案统一加上“云盘挂载 ”前缀，稳定命中白名单 | `tests/test_portal.py` |
| 中 | 普通观影账号登录 Emby 电影墙后，顶栏仍显示管理员专用的“找片 / 追剧”入口，点击进入 `/discover/` 会被 400 拒绝 | `relay/custom-js/search.js` 调用 `ApiClient.getCurrentUser()` 校验 `Policy.IsAdministrator === true`，非管理员隐藏该入口 | `tests/search-entry.test.cjs` |
| 中 | Emby 顶栏注入的主题切换按钮缺少背景/文字/边框样式，在深色/亮色顶栏上均呈原生 3D 灰白按钮，且“深色/亮色”文案易与当前状态混淆 | `relay/custom-css/search.css` 补齐暗/亮两套配色、边框与悬停态；`portal/theme.js` 文案改为“切至深色 / 切至亮色”并设置 `aria-label` | `tests/search-entry.test.cjs`、`tests/check_ui.cjs` |
| 低-中 | 搜索结果卡片中 Prowlarr 返回空名子分类导致 `HDSky · Movies,` 尾随逗号；PR #16 新增挂载表单后设置页左列明显高于右列；`1280px` 含滚动条时顶栏偶发折行；孤立上传任务 `dest` 路径段不足时 `parts[3]` 潜在越界 | `portal/server.py` 过滤空名并去重分类、为 `parts[3]` 增加安全回退；`portal/index.html` 将“高级管理”迁回右列平衡为左 2 右 4；`portal/style.css` 微调顶栏间距保持单行 | `tests/test_portal.py`、`tests/independent-ui-flows.cjs` |

- **验证**：Windows（`compileall`、`node --check`、4 组 Node 测试、2 套 Chrome Playwright、235 项 Python 测试：230 通过 / 5 跳过）与 WSL Linux（81 个发行文件解包后 235 项 Python 测试：234 通过 / 1 跳过）全部通过；Docker / Compose / `caddy validate` 由 GitHub Actions `Regression` 执行。
- **现网说明**：测试站旧占位片（`/aliyun` 下 Local 驱动）播放返回 `307 → 主站点` 属于存储驱动非阿里云盘 Open 导致，待归档一部测试影片到当前 `/aliyun2` 挂载后可完成真实 CDN 直连播放验收；此外 Cloudflare Web Analytics 注入的 `beacon.min.js` 被严格 CSP 拦截属于预期保护（需在 Cloudflare 后台按需关闭统计，不放宽 `script-src 'self'`）。

## 2026-11-26 端到端复审与体验优化

**版本对比。** 本地工作区 `main` 停在 `1f5f17c`（干净，无未提交修改），`origin/main` 领先 4 个提交（`b73a226`、`f3c45dc`、`2916078`、`5cfbd8b`）；所有近期远程分支均已合并进 `origin/main`。经 `git merge --ff-only origin/main` 以 `5cfbd8b` 为基线展开审查与修改。

**审查方式。** 通读全部文档、配置、脚本、源码与 22 份测试；启动真实 `portal/server.py` 连接本地模拟上游服务，在 Chrome 1280×900（亮/深色）与 390×844 下以真实用户视角走完登录、找片筛选、按版追更、我的追剧、下载与归档、播放记录、设置及退出，并做前后视觉截图对照。

| 严重度 | 发现的问题 | 本轮修复 | 回归证据 |
| --- | --- | --- | --- |
| 高 | 全新部署连接阿里云盘时，OpenList（Go）在空列表时返回 `"content": null`，`App.configure()` 直接迭代导致 `TypeError` 崩溃 | `storage/list` 与 `/api/fs/list` 兼容 `null` 切片并校验字典结构；校验 `qb_password` 控制字符且保留原始密码 | `test_portal.py` 新增回归 |
| 高 | 门户使用 `http.cookies.SimpleCookie` 解析 `Cookie` 请求头，同域下其他服务（Emby/qB/Sonarr/Prowlarr/Cloudflare）任一非标准 Cookie 即可导致 `CookieError` 并使管理员会话失效 | 改为安全提取 `cinema` Cookie 的 `session_cookie()`，容忍同域畸形兄弟 Cookie | `test_portal.py` 新增回归 |
| 高 | `scripts/update_files.py` 在 `umask 077` 下仅对 `portal`、`relay/custom-js`、`relay/custom-css` 修正目录 `0o755`，遗漏父目录 `relay`、`scripts`、`gateway`，可能导致非 root 容器（UID 1000）无法穿透挂载目录 | 扩展目录权限修正至 `relay`、`scripts`、`gateway` | `test_installer.py` 断言 |
| 高 | `scripts/update_portal.sh` 在 `instance-id` 缺失且尚未完成首次云盘接线（无 `state/uploader/config.json`）时直接读取配置抛出 `FileNotFoundError` | 升级检查先判断 `state/uploader/config.json` 是否存在，并校验 OpenList 响应结构 | `test_installer.py` 新增回归 |
| 中 | qBittorrent 不可达时 `/api/status` 复用默认长超时，导致设置页和登录后检查卡顿约 90 秒；任一上游返回畸形类型可拖垮整接口 | `App.qb(timeout=None)` 在 `status()` 中使用 10 秒短超时；4 项探针统一校验返回类型并隔离异常 | `test_portal.py` 新增回归 |
| 中 | `uploader/uploader.py` 中 `Sources.tv()` 依赖 `downloads()` 顺带登录 qB，`downloads()` 失败时追剧发现会连锁失败；热重载配置未做字段校验且主发现循环未捕获 `OSError` | `Sources.tv()` 按需独立登录 qB；抽出 `load_config()` 供启动与热重载共用并在异常时安全延后；发现循环补齐 `OSError` 及任务结构校验 | `test_uploader.py` 新增回归 |
| 中 | 门户边界健壮性：`App.login` 非管理员登出清理失败会掩盖真实提示；`App.playbacks` 在 Emby 返回异常结构时 400 崩溃；`App.episodes` 将 Sonarr 故障吞掉误判为 `version_ready=False`；`session['results'/'series'/'version_previews']` 缺并发锁或大小上限 | 分别做容错降级、透传 Sonarr 故障、使用 `series['tvdbId']`、加锁并用 `trim_mapping` 按最旧淘汰；补全 `USER_ERROR_PREFIXES`；`delete_files=False` 返回准确提示 | `test_portal.py`、`test_tasks.py` |
| 中（UI/UX） | 搜索筛选条 `.filters select { display: block }` 导致 Chrome 下拉箭头换行、高度翻倍；按此版本追更弹窗在 900px 桌面视口超出首屏需滚动；“我的追剧”3 个操作按钮折行且待选版本行出现无用的禁用“恢复追更”；“删除追剧”使用浏览器原生 `window.confirm()` | 改为 flex 列布局使筛选下拉框恢复单行（实测 46px）；主版本字段 3 列紧凑排版使弹窗确认按钮完全落入 900px 首屏（bottom 822px）；操作区加宽至 380px 单行对齐并隐藏无效禁用按钮，接入统一风格 `<dialog id="confirm-dialog">` | `independent-ui-flows.cjs` + 实机 Chrome 截图验证 |
| 低-中（UI/UX） | 登录失败仅显示底部 toast；下载任务多文件详情中 `.nfo`/图片等附件显示“等待导入或上传”，已完成文件显示“下载中 100.0%”，且未展示 `retry_at` 重试时间；超过 1 小时的播放记录显示 `83:45`；设置页仍显示冗余“点击设置处理”黄条且左右列卡片不均衡；删除清单预览失败无重试按钮且出现双句号 | 登录表单新增就近内联 `#login-error`；后端补充文件 `role` 字段，前端区分“不自动归档 / 下载已完成 / 等待导入 / 等待上传”并显示“下次重试 HH:MM:SS”；播放时长支持 `H:MM:SS`；设置页隐藏自身冗余提示并平衡为 3+3 列；新增预览“重新核对”按钮与各视图加载状态、`series` 更新时间及菜单状态保持；`watch` 弹窗关闭即释放按钮 | `test_tasks.py`、`independent-ui-flows.cjs` + 实机 Chrome 验证 |

### 本轮验证

环境：Windows 11（Python 3.14.3、Node 24.14.0、Google Chrome + 独立 scratch 目录 Playwright 1.65.1）与 WSL Linux（Python 3.14.4、bash）。本机无 Docker、Caddy、Ruff、mypy、Bandit；Docker Compose、容器镜像构建、容器启动与 `caddy validate` 由 GitHub Actions `Regression` 工作流执行。

- **语法与静态检查**：`python -m compileall -q .`、`node --check`（全部 5 个前端/注入 JS 文件）、WSL `bash -n install.sh scripts/*.sh` 以及 `.sh`/`.py` 纯 LF 换行校验全部通过。
- **Windows 自动化测试**：Python `unittest` **223 项**（218 通过、5 项 POSIX 权限测试跳过）；`node --test tests/*.test.cjs` **4/4 通过**；Chrome Playwright `tests/check_ui.cjs` 与 `tests/independent-ui-flows.cjs` **全部通过**。
- **WSL Linux 与安装包验证**：源码目录 Python `unittest` **223 项**（222 通过、1 项 Windows 启动器测试跳过）；`python3 scripts/build_all_in_one.py` 生成单文件安装器并通过 `sha256sum -c`，按 CI 方式解包得到 **79 个发行文件**，解包目录内 Python `unittest` **223 项**（222 通过、1 跳过）。
- **真实门户实机验证**：以真实 `portal/server.py` + 本地模拟上游启动服务，Chrome 1280×900（亮色）与 390×844（深色）端到端验证内联登录报错、单行筛选下拉框（46px）、追更弹窗首屏完整展示（bottom 822px）、追剧单行按钮对齐、播放时长 `1:23:45`、设置页 3+3 布局及零控制台/页面异常，并逐张核对截图。
- **未在本机执行**：未直接接触真实 VPS、PT 站点、阿里云盘或真实 Emby；本轮未改动 `relay/` Go 源码与补丁（仍为 `5cfbd8b` 既有版本）。

## 2026-11-25 第三轮复审（PR #13，分支 `review/2026-11-25-round3`）

**版本对比。** 本机没有以 GitHub 为 origin 的最新工作副本。`/workspace/cli-reviews/movie-20261122/repo` 是 2026-11-22 复审的克隆，停在 `0796cfd` 之上 5 个未推送提交，工作区干净；其内容已由 PR #9 合并进主线（逐文件对比只差 PR #9 额外的独立复核与修正）。`/workspace/movie/mv9`、`mv9b` 是 Draft PR #6/#7 的旧快照，`mv9b` 与 `origin/mv-9b-movie3` 的文件树相同，`mv9` 更旧，均无未提交修改。因此以 GitHub `main@b73a226`（PR #12 合并后，CI 通过）为基线，重新克隆到独立目录工作，没有改动上述旧目录。

**审查方式。** 通读文档、配置、测试和全部源码；用真实 `portal/server.py` 连接本机 HTTP 上游替身（Emby、Prowlarr、Sonarr、qB、OpenList，均为合成数据），在 Chrome 中按 1280px 和 390px 走完登录、搜索、下载、按版追更、我的追剧、任务、播放记录、设置和退出，并逐项检查服务故障时的提示。

| 严重度 | 用户可能遇到的情况 | 本轮修复 | 回归证据 |
| --- | --- | --- | --- |
| 中 | 窄屏打开“更多”菜单时，`<details>` 的 toggle 事件异步触发，菜单先按右对齐画到屏幕外（320px 实测 x=−79），随后才移回。现有 320px 浏览器回归因此时过时不过：同一环境下未改动的基线连续 3 次失败 | 在 summary 的 click（展开之前）决定对齐方向 | 修复后同环境连续 3 次通过 |
| 中 | 上游返回空正文或非对象 JSON 时，`App.ol()` 抛出未捕获的 AttributeError，工作线程直接断开连接，浏览器显示“无法连接影院服务，请检查网络”，把服务端问题误导为网络问题 | `ol()` 校验响应结构；请求处理增加兜底，返回 JSON 500“服务处理请求时出错”，stderr 只记录方法、接口路径和异常类名 | 新增 2 项测试，旧代码报错 |
| 中 | Emby 停止或重启时登录，提示是“请检查输入…”（像输入错误）或“稍后重试 setup”（把人引向命令行） | 区分三种情况：密码错误、Emby 未正常响应（附 HTTP 状态）、无法连接 Emby | 新增测试，旧代码失败 |
| 低-中 | 任一内部服务不可达时，提示不说明是哪个服务，在网页中还要求“重试 setup” | `integration.Client` 按容器名或回环端口指明 Emby/OpenList/qBittorrent/Sonarr/Prowlarr，不包含地址或查询参数；文字同时适用于网页与 `install.sh setup` | 新增测试，旧代码失败 |
| 中 | 在“我的追剧”保存追更范围或版本后列表不刷新（只有“同步集数中”时才轮询），状态停留在旧值 | 保存后立即刷新列表 | 浏览器回归新增断言；旧 `app.js` 等待新行超时 |
| 中 | 从未选择版本的剧显示红色“规则异常”；唯一的修复入口“选择版本”藏在“更多”菜单里，可见的“选集下载”却是禁用状态 | `/api/series` 返回 `reason`（`missing` 或 `invalid`）。前端显示琥珀色“待选版本”或红色“规则异常”，直接显示主按钮“选择版本”，规则无效时不显示不可用的选集和范围按钮 | Python 测试与浏览器断言 |
| 低 | 下载完成后一律显示“等待导入”，电影和独立剧集并不经过 Sonarr 导入；没有可归档视频（原盘、仅附件）的资源也显示“等待导入”，看起来会无限等待。单文件独立下载在标题行下又重复一行相同的状态和进度 | 追剧显示“等待导入”，其他下载显示“等待上传”，无可归档视频显示“未发现可归档视频”；去掉重复行 | `task-controls.test.cjs` 新增 3 项，旧代码失败 |
| 低 | 搜索可能持续几十秒，期间只有一行文字；切换“电影/剧集”触发的重新搜索不禁用按钮，可重复提交 | 显示加载动效和预计耗时；切换类型与提交走相同的按钮禁用流程 | 真实门户浏览器检查 |
| 文档 | `docs/SETUP.md` 的隧道命令缺少 8097，按文档无法打开 `/discover/` | 补齐端口并加入对照表 | — |

**经审查保留的设计。** 管理员会话每次请求都回查 Emby（撤销管理员权限立即生效，现有测试要求如此）；CSRF 同时校验 Origin、JSON 类型、大小和常量时间比较的令牌，Cookie 为 HttpOnly + SameSite=Strict；前端统一用 `textContent` 构建 DOM 并配合严格 CSP；上传发布、删除确认清单、保留期清理和 relay 逐资源授权的证据链没有发现需要改动的问题。

**兼容性。** 没有数据库迁移，也没有新增依赖。`/api/series` 只新增字段，缺少该字段时前端按原来的“规则异常”处理。升级方式不变：`sudo bash /新源码目录/scripts/update_portal.sh /opt/cinema`。回滚为 revert 本轮合并提交后重新运行同一脚本。

### 本轮验证

环境：Linux 6.12，Python 3.13.5，Node 24.22.1，Google Chrome 156，Playwright 1.63，本机 Go 1.27.3，另下载 Dockerfile 固定的 Go 1.27.4。本机没有 Docker 和 Caddy。

- **基线**（未改代码）：Python 211 项通过、1 项 Windows 启动器跳过；Node 4 个文件通过；`check_ui.cjs` 通过；`independent-ui-flows.cjs` 首次通过，随后同环境 3 次均在 320px 菜单断言失败（即上表第一项）。
- **修改后**：Python 216 项通过、1 项跳过；`node --test tests/*.test.cjs` 4 个文件通过；`check_ui.cjs` 通过；`independent-ui-flows.cjs` 连续 3 次通过。所有新增断言都在对应的旧代码上确认会失败。
- **安装包**：`python3 scripts/build_all_in_one.py` 后 `sha256sum -c` 通过；按 CI 方式解包得到 77 个发行文件，被改动的文件与源码一致；解包目录内 Python 216 项通过、1 项跳过。
- **真实门户端到端**：真实 `portal/server.py` 加本机上游替身，Chrome 1280px 与 390px 走完登录（错误密码与正确密码）、电影与剧集搜索、提交下载、按版追更弹窗、我的追剧、任务、播放记录、设置和退出。故障路径：Emby 502 或不可达时登录、Prowlarr 不可达时搜索、OpenList 返回空正文、Sonarr 队列结构异常，都返回明确提示，没有断开连接。
- **静态检查**：Ruff E9/F（源码与测试）通过；mypy 按门户和 uploader 镜像两种模块布局检查 11 个文件，无问题；Bandit 中高置信度 2 高 10 中，与上一轮相同，逐项确认为 OpenList SHA1 文件校验协议、固定列名的 SQL、本地服务 XML 和容器内监听。
- **relay**：按 `relay/Dockerfile` 的顺序在固定上游 `9b049b8` 上重放四个补丁。Go 1.27.4 下 14 项 TestStrict、四个包的 `go vet`、`go mod verify` 和 Linux amd64 构建均通过；govulncheck 可达漏洞为 0（另有 2 项导入包级、1 项模块级未调用提示）。本机 Go 1.27.3 会报告 GO-2026-6859，该问题在 Dockerfile 固定的 1.27.4 中已修复。
- **未在本机运行**：Docker Compose 配置、镜像构建、容器启动和 `caddy validate` 由 GitHub Actions 执行；没有接触任何 VPS、PT 站点、阿里云盘或真实 Emby。

## 接手当天

1. **取得源码。** 从 GitHub 克隆 `WenLiCG/movie` 的 `main`，构建安装包前记录 `git rev-parse HEAD`。打包范围见 [scripts/build_all_in_one.py](scripts/build_all_in_one.py)。
2. **跑本地回归。** 从项目根目录运行下一节的命令。做 UI 改动可先用模拟 API 跑页面，验证真实下载或播放再使用隔离测试环境。
3. **接管测试 VPS。** 上次使用已核对的主机指纹和项目所有者授权的 SSH 密码登录。默认密钥被拒时，核对该主机既有的登录方式。升级前确认正在使用的 Compose 项目、部署覆盖文件、隧道和已运行服务，通过私密渠道交接相应凭据。[部署记录](docs/DEPLOYMENT.md)
4. **继续现场验收。** 在设置页刷新状态，确认当前云盘挂载、下载器和追剧连接，随后用一条有权使用的小视频验证下载、归档发布、Emby 入库和云盘直连播放。切换归档空间时保留承载旧片与旧任务的原挂载。[设置与归档流程](docs/DISCOVER.md)

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
- **发布以持久凭证为准。** 上传先进入任务所属挂载的 `.cinema-upload`，经 OpenList 任务成功、远端大小和可用 SHA1 校验，完成移动和回读才记为 `published`。回执丢失先查任务和强哈希证据，保留 SQLite 状态供恢复。`Movies`、`TV` 和暂存区只能由同一个 uploader 写入。匹配的 `srt`、`ass`、`ssa` 字幕先于视频发布。[找片与归档说明](docs/DISCOVER.md)
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

- 浏览器回归 `node tests/check_ui.cjs` 与 `node tests/independent-ui-flows.cjs` 使用本机 Chrome 和可被 Node 解析的 Playwright（可设 `NODE_PATH`），数据由测试中的本地 HTTP 服务模拟。界面规范见 [DESIGN.md](DESIGN.md)。
- Linux/Docker 环境下，设置 `export MOVIE_DOMAIN=cinema.example.invalid` 后，可用 `docker compose --profile '*' config --quiet` 检查 Compose，用 `docker compose --profile connected build relay` 重放 Go 补丁、执行 Go 回归并构建。源码许可及固定版本见 [SOURCES.md](SOURCES.md)。
- 发布新安装包时执行 `python scripts/build_all_in_one.py`，生成 `dist/cinema-all-in-one.sh` 与 `.sha256`，在 Debian 上用 `sha256sum -c cinema-all-in-one.sh.sha256` 校验。每个包使用同次构建生成的校验文件。GitHub Actions 在推送后构建并解包回归，源码交接由仓库提供。[打包清单](scripts/build_all_in_one.py) · [回归工作流](.github/workflows/regression.yml)
- 没有 Docker 时，可按 `relay/Dockerfile` 的顺序在固定上游提交上 `git apply` 四个补丁并复制 `playback.go`、`playback_test.go`，再用与 Dockerfile 相同版本的 Go 运行 `go test ./internal/web ./internal/service/emby ./internal/service/m3u8 ./internal/service/openlist -run TestStrict` 和 govulncheck。本机 Go 较旧时出现的标准库漏洞提示，以 Dockerfile 固定的版本复扫为准。

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
| 页面提示“本地应用 X 暂时无法连接或响应超时” | 用 `sudo docker compose ps` 确认该服务在运行，再看 `sudo docker compose logs --tail=100 <服务名>`。服务重启期间短暂出现属正常。 |
| 页面提示“服务处理请求时出错” | 多为上游返回了意外格式。`sudo docker compose logs --tail=100 portal` 中会有一行 `portal <方法> <接口> <异常类型>`，据此确定接口，再检查对应的上游服务。 |

---

以下为历次交付记录，按时间从新到旧排列，保留当时的证据和结论；与文首“当前状态”不一致时以文首和代码为准。

## 2026-11-26 公网登录入口验收（PR #14）

应用版本 `2916078` 部署后，公网 HTTP 跳转 HTTPS，全新 Chrome 会话直达 Emby 用户名密码框。本机与 VPS 分别通过 7 项匿名入口检查。该次检查未提交观众密码，也未验证登录后播放。[PR #14](https://github.com/WenLiCG/movie/pull/14)

## 2026-11-25 下载恢复、上传队列与网关交付复审（PR #12）

本轮本地 `main` 为 `e204041`，工作区干净。获取远端后，`origin/main` 领先一个提交 `1f5f17c`（PR #11，16 个文件有差异）。本地 PR #10 分支与其合并提交的文件树相同，更早的审查分支已包含于主线。本轮以 `1f5f17c` 建立审查分支。另一工作树、历史 PR 和本机已有安装包保留。

| 影响 | 原有行为 | 当前行为 |
| --- | --- | --- |
| 高 · 用户监控设置 | 单资源取消后继续，可能打开原本未监控的集数。同一哈希重新添加后也可能沿用旧记录 | 按下载实例和剧集归属核对；在写 Sonarr 前持久化本次实际要关闭的集数。上游响应丢失或门户中断后可重试。旧记录缺少依据时只恢复下载。跨页队列出现重叠会要求刷新。 |
| 中 · 上传可用性 | 一个原生上传已经完成、暂存内容校验失败的任务，可以长期占住传输名额 | 保留源文件和暂存，独立继续复核；其他影片可传输。任务 ID 消失或重新匹配时清除旧终态，避免误用上一次的完成记录。磁盘守护只在暂停清单发生变化时同步写盘。 |
| 中 · 管理操作 | 单种子追剧隐藏资源操作；晚到的旧请求可能干扰新登录；部分发布、失败、原生暂停与阻塞状态容易混淆 | 项目和资源按钮标明作用范围，冲突操作暂时禁用；即使旧种子已消失，也能结束受阻请求并保留文件。退出后建立新的页面上下文。详情按已选文件和实际上传进度展示。 |
| 中 · 网关与升级 | 原生服务可收到门户 Cookie，认证子请求也携带原生 Cookie 和 Authorization。升级脚本只重载已有网关进程 | 管理入口单独转发认证所需的 Cookie；原生应用仍收到自身的会话和 Authorization。Caddy 固定为 2.11.9。运行中的网关先用发行镜像验证配置，再应用镜像，等待确认重载。失败时进入已有回滚流程。 |

**兼容与部署。** 本次不需要迁移数据库。任务快照使用现有 SQLite 表的 JSON 字段。已有部署使用独立源码目录下的 `sudo bash scripts/update_portal.sh /opt/cinema`。更换网关镜像时公网连接可能短暂中断。保留升级备份，真实云盘的 Movies/TV 和暂存区继续由单个 uploader 独占写入。[升级与恢复说明](docs/DISCOVER.md#更新已有部署)

**本轮验证。** 以下检查使用临时状态和自建测试数据。

- 基线 Linux 运行 202 项，201 通过、1 项平台跳过。最终源码的 Python 回归共 211 项，Windows 206 通过、5 项 POSIX 专属跳过，WSL Linux 210 通过、1 项 Windows 启动器跳过。单文件安装器通过 SHA256 校验，解包后的 77 个发行文件逐一与本地受控源码核对，同一套 211 项回归在解包目录再次得到 210 通过、1 项平台跳过。
- 新增回归覆盖同一哈希的不同下载实例、监控写入中断、状态不明的任务重新绑定，以及网关加载失败后的旧版恢复。「等待归档复核」的重新检查按钮会保留原生任务，只提前下一次核验。
- Chrome 驱动真实 Python 门户，通过本机 HTTP 上游替身完成登录、搜索、提交下载、任务暂停与继续、保留天数保存和退出后的 401。另以真实门户和上传 worker 验证单资源取消和恢复。两套浏览器回归覆盖 320–1440px、深浅主题、资源详情、延迟返回和受阻任务恢复；Node 的四个回归文件通过。CI 在临时目录安装 Playwright 1.65.1，本机使用该版本重跑通过，对测试依赖的 npm audit 返回 0 项。
- Ruff（运行源码 E9/F）、按两个镜像模块布局运行的 mypy、Python 编译、JavaScript 与 Bash 语法检查通过。mypy 覆盖 12 个源文件，现有未注解函数限制其检查深度。
- WSL 使用与 Dockerfile 一致的 Go 1.27.4 和上游提交，重放四个补丁，14 项 TestStrict 顶层测试、四包 go vet、go mod verify 和 Linux amd64 构建通过。源码和二进制 govulncheck 都报告 0 项可达漏洞，另有导入包级 2 项、模块级 1 项提示。首次扫描器由旧 Go 编译，改用同版 Go 重建后重扫。
- Bandit 的中高置信度检查报告 2 项高、10 项中。两处 SHA1 用于 OpenList 的文件校验协议，SQL 拼接来自固定语句或程序字段名，数据值按参数绑定，XML 来自本地服务配置，监听项来自容器内门户和端口预检。仓库的 Dependabot 告警接口返回 403 并注明未启用。本轮未扫描全部第三方容器的操作系统软件包。
- 全部 Compose profile 配置通过。本机从固定镜像提取并校验了 Caddy 二进制，实际发送 HTTP 请求检查四个原生入口的认证、凭据分流和电影墙转发。Caddy 配置验证通过，有沿用的缩进格式提示。升级脚本用 Docker 命令替身覆盖新网关延迟就绪和失败恢复旧版本。真实容器的构建、启动及网关配置由 [GitHub Actions](https://github.com/WenLiCG/movie/actions/workflows/regression.yml) 检查。

**现场待验。** 此次交付到 GitHub，未操作生产 VPS、真实 PT 站点或阿里云盘。独立新 VPS 重装、现场升级回滚、大文件上传以及物理客户端长时间播放仍需实机验证。对不确定的云端移动仍先核实任务与文件再处理。

## 2026-11-25 上一轮端到端复审（PR #11）

本轮开始时本地工作目录为空且无其他本地副本，直接克隆 `WenLiCG/movie` 最新 `origin/main`（`e204041`，已含合并后的 PR #10）作为工作基线。历史开放分支 PR #2、#5 及 Draft PR #6、#7 保持原状未动，2026-11-19 停止交接归档目录完整保留。

| 严重度 | 发现的问题 | 本轮修复 |
| --- | --- | --- |
| 中 | `Handler` 错误前缀白名单过窄，吞没了 `emby_defaults` 全部 7 条原因提示、`playback_records`、`download_episodes`、`connect_services`（OpenList/qB）等中文诊断信息；OpenList 管理员登录失败被误报为挂载异常 | 补齐受控中文诊断前缀并显式排除 `APIError`/内部异常回显；OpenList `/api/auth/login` 失败单独提示管理员用户名或密码错误；校验空/布尔型剧集 ID 与资源 ID。 |
| 中 | 浏览器切换或取消请求导致 `Handler.reply()` 写入中断（`ConnectionAbortedError` / `BrokenPipeError`）时，外层 `except OSError` 再次向已关闭连接写 400 响应头并抛出未捕获异常 | 在 `Handler.reply()` 及 `/api/admin-check` 跳转中安全吸收客户端断连异常，避免二次写响应报错。 |
| 中 | `versions.json`、`watch-requests.json` 等文件以 UTF-8（`ensure_ascii=False`）写入，但多处 `.read_text()` 未指定 `encoding='utf-8'`，在 Windows 等非 UTF-8 默认编码环境读写中文版本标记（如`中字`）会乱码或解码失败 | 统一 `portal/server.py`、`archive.py`、`uploader/uploader.py`、`uploader/cleanup.py` 与 `scripts/update_portal.sh` 的 UTF-8 读取编码。 |
| 中 | `App.localize()` 丢失 `None` 与空列表区分，导致确认无中文名的剧集仅缓存 5 分钟（与文档 24 小时不符），且未加锁、无容量上限；`/api/tasks` 每 10 秒轮询会对全部 Sonarr 剧集发起本地化查询；`/api/series` 按剧逐次重开 SQLite 和重读配置 | 区分暂态失败（5 分钟重试）与确认无中文元数据（24 小时缓存），全程加锁并限制缓存上限；`tasks()` 仅查询当前活动任务关联的剧集；`/api/series` 合并配置读取与单次 SQLite 归档计数查询；回环地址 `X-Forwarded-For` 短路跳过 `gateway` DNS 解析。 |
| 低-中 | `_disk_guard` 每 10 秒读取 SQLite 未使用 `closing()` 关闭连接且未检查 `jobs` 表是否存在；`retry_upload` 与 `update_portal.sh` 缺少库文件或 `jobs` 表前置判断；`configure.py` `check_public` 默认管理员名硬编码个人账号 | 补齐 `closing()` 与 `sqlite_master` 表存在检查，避免空库误建或升级前缺表报错；当时的默认提示为 `cinema-admin`；现行用户名见文首“唯一管理员账号”。 |
| 中（UI/UX） | 320px 手机宽度下“我的追剧 → 更多”菜单向左溢出视口外（实测 `x = -79px`），且点击外部/按 Esc/点击菜单项不自动收起；“看片”复用固定命名窗口会覆盖正在播放的标签页 | “更多”菜单增加边框与视口自适应左右对齐（320px 实测 `x = 31px` 完全在视口内），支持单开互斥、点击外部与 Esc 关闭；“看片”改为每次打开新标签页交接会话。 |
| 低-中（UI/UX） | 追更弹窗切换候选剧集时清空用户刚填写的版本条件，未识别提示不随输入更新，条件未变仍显示换版勾选；选集弹窗按钮 0px 贴合且默认停在无可选集的第 1 季；搜索被默认 1080p 过滤后不显示总数且缺少一键重置；任务总览缺少体积与实时下载速度；设置页缺少手动刷新按钮 | 追更弹窗保留用户输入、实时更新提示且仅在条件实际改变时显示并要求勾选换版；选集弹窗增加按钮间距、默认选中首个有可下载集数的季并联动“全选/取消本季全选”；搜索显示“显示 X / 共 Y 个结果”与一键重置筛选；任务行展示总大小与实时速度，下载提示支持一键跳转任务页且常规提示 8 秒自动收起；追剧 `同步集数中` 自动轮询；设置页补齐顶部“刷新”按钮。 |

### 2026-11-25 复审验证结果

- **Python 回归**：Windows（Python 3.14.3）运行 `python -m unittest discover -s tests -p "test_*.py"` 共 **202 项通过**（4 项 Unix 专属条件跳过）；WSL Linux（Python 3.14.4）在源码及单文件安装包解包目录运行共 **202 项通过**（1 项 Windows 启动器跳过）。`python -m compileall -q .` 通过。
- **安装包与脚本**：`python3 scripts/build_all_in_one.py` 生成单文件安装器并通过 `sha256sum -c` 校验，安全解包 79 个发行文件；`bash -n install.sh scripts/update_portal.sh scripts/openlist-start.sh` 及 `node --check portal/app.js portal/theme.js portal/paint.js` 全部通过。
- **Node 与真实浏览器多视口测试**：`node --test tests/*.test.cjs` 4 套全部通过；本机 Google Chrome + Playwright 执行 `tests/check_ui.cjs` 与扩展后的 `tests/independent-ui-flows.cjs` 全部通过（覆盖 1440/1024/768/390/320px 五档视口、深浅主题对比度、Emby 主题同步、存储禁用降级、筛选计数与重置、追更输入保留与换版门控、选集季定位、320px 菜单防溢出、任务速率展示及设置刷新）。额外启动真实 `portal/server.py` 配合本地 HTTP 上游替身完成端到端截图与交互核验。
- **Go relay 构建与漏洞复查**：在 WSL 隔离目录拉取固定上游 `9b049b885388108c61f234244c1249f9a52a8dd6`，按 `relay/Dockerfile` 顺序重放全部补丁，4 个包的 `TestStrict`、`go vet`、`go mod verify` 及 `GOOS=linux GOARCH=amd64` 静态构建全部通过；`govulncheck` 确认第三方依赖可达漏洞为 0（WSL 宿主机自带 Go 1.26.8 报告的标准库旧版提示在 `relay/Dockerfile` 固定的 `golang:1.27.4-alpine` 镜像中已修复）。
- **未在本机执行项**：本机与 WSL 未安装 Docker 引擎及 Caddy 二进制，`docker compose --profile '*' config`、容器镜像构建与 `caddy validate` 由推送后的 GitHub Actions（`regression.yml`）自动执行；真实 VPS、PT 站点、阿里云盘及多端长时间播放仍需现场验收。

## 2026-11-25 主线审查与交付（PR #10）

基线为 PR #9 合并后的 `cd2c455`。获取远端历史后，本地与远端 main 相差 0 个提交、文件差异为空，两棵工作树均无未提交修改。较早的 #2、#5、#6、#7 各有独立范围，尤其 #7 的公网方案有历史验收阻塞。本轮沿用单域名和现有服务架构。候选提交、检查与最终合并记录见 [PR #10](https://github.com/WenLiCG/movie/pull/10)。

| 严重度 | 用户可能遇到的情况 | 本轮修复 |
| --- | --- | --- |
| 高 | 多部剧共用下载包时，项目删除或暂停可能选中另一部剧的资源 | 汇总 Sonarr 全部队列页，跨剧或归属不明的包保持独立。队列页数、计数或单资源归属发生异常时停止相应写操作。 |
| 高 | Sonarr 删除剧集文件时可一并删除导入目录里新增的字幕 | 删除前检查所选剧的完整目录，包含异名及子目录字幕。归属、归档或确认范围不足时保留；其他剧目录独立。判断条件见 [uploader 说明](uploader/README.md)。 |
| 高 | relay 的旧 Go 工具链及网络依赖命中可达漏洞公告 | 固定 Go 1.27.4 镜像及更新后的模块校验和，镜像标签升至 strict6。源码和 Linux amd64 二进制复扫均为 0 项可达。 |
| 中 | 首次登录在 Emby 刚建库时断开、上传器首次启动前无法恢复任务、非法季数先改变追更配置 | 建库结果延迟可在设置页重试，兼容仅有操作表的 SQLite，先核实季数再修改监控。设置页和提交下载共用下载器配置校验。 |
| 中 | 一条失效下载路径会阻塞同批其他剧集或缺失字幕关联；清理逐文件探测整个归档历史 | 按单个视频及其字幕组处理，复核先按字节大小缩小候选，再比较文件与哈希。 |
| 中 | 切换管理员后残留云盘凭据草稿，筛选后下载按钮可能重复提交，旧播放记录和资源指标看起来仍是最新 | 退出及会话失效清空草稿，按钮跟随提交状态；清空搜索回到推荐，刷新失败显示反馈，隐藏过期仪表值。小资源按 B、KiB、MiB 等单位显示。 |
| 中 | 私有 umask 可能导致升级后容器读不到文件，SQLite 在线快照继承临时副本的属主和权限 | 按发行文件类型设置权限，快照保存原库属主和权限。硬链接数据库逐个保存为可独立读取的普通文件，检查备份包含所有已发现的库。 |

### 验证记录

- Windows 和 WSL 都运行了 Python 全量发现测试；Windows 还覆盖了 SSH 启动器，Linux 在原生临时目录覆盖 UID、文件模式和硬链接。平台跳过项由现有测试条件决定。Windows 基线共 188 项，4 项跳过；修改后的两端共各 195 项，分别跳过 4 和 1 项，其余通过。
- Node 的 4 个回归文件通过，两套本地 Chrome 浏览器检查覆盖 320–1440px、明暗主题、键盘、凭据草稿清空、下载提交、刷新失败及恢复。真实 portal 配本机 HTTP 上游替身验证登录、搜索、下载、任务暂停与恢复、退出及 401；浏览器也通过同一 portal 提交下载并查看任务。
- Python 的 Ruff E9、F 检查通过。mypy 按 portal/运维脚本及 uploader 镜像各自的模块布局运行，12 个源码文件通过；项目大部分函数尚无类型注解。Bash、JavaScript 语法、Compose 全 profile 配置通过。
- 固定上游重放补丁后，四个 Go 包的 TestStrict、go vet、go mod verify、Linux amd64 编译通过。govulncheck 源码与二进制扫描从基线的 6 项可达公告降至 0；复扫仍有 2 项导入包、1 项依赖模块层面的未调用提示。Python/浏览器产品代码使用标准库及原生 API。仓库 Dependabot 告警接口未启用，第三方服务镜像的全量 OS 漏洞未扫描。
- Windows 上传恢复测试曾间歇出现 network_or_protocol 或停在 queued。复核发现本机 OpenList 测试替身在读取任务轮询请求体前就响应并断开；调整为读完请求体再响应，继续保留原有发布及防误删断言。首次 mypy 将部署为平级模块的 uploader 当作命名空间包；按镜像布局重跑。首次 PowerShell 构建和扫描因带点的 Go tag 参数被拆开退出，修正命令后重跑通过。
- CI 在 Ubuntu 运行源码及解包目录的 Python 测试、Node 和 Bash 检查及安装器 SHA256 校验。容器阶段从解包目录构建 portal、uploader 和 relay，启动前两项，检查首页、未登录会话、上传器等待接线以及 Caddy 配置。一次扩展附件检查的中间版本把未登记的视频也列入附件范围，原有删除范围回归失败。按固定 Sonarr 的非视频附件规则修正，保留相邻及新到的视频。首轮容器检查在容器刚启动、监听尚未就绪时遇到连接重置；现有就绪探测已覆盖该错误，仍须收到真实首页及预期会话响应才算通过。当前提交的运行结果见 [GitHub Actions](https://github.com/WenLiCG/movie/actions/workflows/regression.yml)。

### 使用与现场边界

安装与已有部署更新见 [README](README.md) 和 [更新已有部署](docs/DISCOVER.md#更新已有部署)。本轮不需要数据库迁移；源码回退可还原本轮合并提交。SQLite 快照人工恢复前须停止对应服务，保留当前状态备份，再在空目录验库。部署前先确认能为当前镜像建立回滚标签。

本轮没有更新 VPS。实际 PT 下发、阿里云盘上传、Emby 客户端长时间播放、物理手机及全新 Debian 的整机重装仍需现场验证。Movies/TV 需要单个写入者，OpenList 移动结果不明时继续保留源文件。下面各节保留当时的证据与结论，PR #9 的最终合并状态以本节基线为准。

## 2026-11-23 独立导入与复核（Cursor 云端）

本节由 Cursor 云端代理在作者记录之后追加。上面的「2026-11-23 全面复审与修复」仍是 Claude CLI 作者记录，不是本节的复测结论。本节不改产品代码。

### 源、导入与当前 main

| 角色 | SHA |
| --- | --- |
| 源 main / 当前 `origin/main` | `0796cfd7e816ef1878fe4d9a4a589a3ba327eb5e` |
| 本地候选（5 个源提交的合并结果，未在本仓库重放成 5 个提交） | `d93f6cd6232526b4cb79b74174ca68f8642e67aa` |
| 补丁 SHA256 | `192c9e48a0317dccd285c6e8c1b15bff2f01249a73613e7b9b023a08db986377` |
| 导入后的 git tree | `1d28e35916c3620d192f5d9e35e19e2c83ad1680` |
| 导入提交（作者 Cursor Agent，只应用该 diff） | `21c817c0296a0b4bed9b498e5ed3c5bbc4d1b8e1` |
| 独立测试提交 | `5e4f3029f6db4d633fbe4c420f28188a8645a6e9` |

`git fetch origin main` 后 `origin/main` 仍是 `0796cfd7e816ef1878fe4d9a4a589a3ba327eb5e`。没有新的 main 提交，没有冲突，也没有丢弃任一侧的改动。补丁是普通 git diff，不是 mbox。在该基线上 `git apply --index` 后、提交前，`git write-tree` 等于 `1d28e35916c3620d192f5d9e35e19e2c83ad1680`。导入提交的 tree 与此相同。5 个本地源提交没有被重造成新的作者身份。发布环境可能在提交说明末尾追加 `Co-authored-by`；以提交说明正文中的 SHA 为准。包含本节的提交是独立测试提交的子提交，分支头用 `git rev-parse HEAD` 核对。

导入的产品与作者测试文件：`HANDOFF.md`、`README.md`、`compose.yaml`、`docs/DISCOVER.md`、`gateway/Caddyfile`、`portal/app.js`、`portal/index.html`、`portal/playback.py`、`portal/server.py`、`relay/playback.go`、`relay/playback_test.go`、`tests/portal-resilience.test.cjs`、`tests/test_cleanup.py`、`tests/test_gateway.py`、`tests/test_playback.py`、`tests/test_portal.py`、`tests/test_reliability.py`、`tests/test_versions.py`、`uploader/cleanup.py`、`uploader/uploader.py`。

独立追加：`tests/test_independent_boundaries.py`、`tests/independent-ui-flows.cjs`，以及本节。

### 环境

2026-11-22T23:43:35Z 记录：Linux 6.12.113+ x86_64，Python 3.12.3，Node v22.23.4，Google Chrome 157.0.8103.50，Playwright 1.49.1，Docker 29.1.3，Compose 2.40.3，宿主机 Go 1.22.2（未用于 TestStrict）。relay 测试在镜像构建里的 `golang:1.27-alpine` 中运行。没有真实 Emby、OpenList、PT、阿里云或生产流量。

### 命令与退出码（独立执行）

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `sha256sum` 附件补丁 | 0 | 与上表 SHA256 一致 |
| `git apply --check` 后 `git apply --index`，再 `git write-tree` | 0 | tree 完全匹配 |
| `git fetch origin main` | 0 | 与源基线相同 |
| 源基线上只换入候选新增测试后，逐项运行清理/播放记录/退出/密码/超时/退避/网关/重复下载与 `node tests/portal-resilience.test.cjs` | 全部 1 | 修复前失败：sidecar 会删正片、大 sample 被删、401 会话挤成 100 条、退出 503、密码文案是通用错误、`Handler.timeout` 为 None、退避 10 次而不是 4、Caddy 没有 HSTS、重复下载报「结果已过期」、旧前端不处理断网 |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 0 | 184 项，1 项跳过。其中 13 项是本节新增，其余对应该候选在 171 项基础上的作者测试 |
| `python3 -m unittest discover -s tests -p 'test_independent_boundaries.py'` | 0 | 13 项 OK |
| `node --test tests/*.test.cjs` | 0 | 4 项通过 |
| `bash -n install.sh` 与 `bash -n scripts/*.sh`，`node --check portal/app.js` | 0 | 通过 |
| `NODE_PATH=/tmp/pw/node_modules node tests/check_ui.cjs` | 0 | 本机 Chrome 跑完五个视图 |
| `NODE_PATH=/tmp/pw/node_modules node tests/independent-ui-flows.cjs` | 0 | 启动 503 显示登录、退出清空旧结果、保留天数编辑不被焦点刷新覆盖、重试按钮在 `tasks()` 后保持焦点、追更范围没有第 0 季 |
| `python3 scripts/build_all_in_one.py --output /tmp/cinema-independent.sh` 后 `sha256sum -c` | 0 | OK。未在解包目录再跑测试 |
| `sudo -E docker compose --profile '*' config --quiet` | 0 | 真实 Compose 解析。relay 为 `cinema-relay:9b049b8-strict5`，`cap_drop: [ALL]`，`no-new-privileges:true`，只绑定 `127.0.0.1:8095` |
| `sudo -E docker compose --profile connected build relay` | 0 | 镜像构建成功。Dockerfile 内 `go test -run TestStrict` 四个包均为 `ok`，随后 release 构建成功。日志没有 `-v`，本节不把作者的「18 项」写成独立计数 |
| 固定摘要 `caddy@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e` 对仓库 Caddyfile `validate` | 0 | `Valid configuration`。有格式警告，不是配置错误 |
| 同一 Caddy 镜像加本机 echo，请求 `https://localhost/` | 0 | `cinema=SECRET` 到上游为空；`cinema=SECRET; emby=1` 变为 `emby=1`；`a=1; cinema=SECRET; b=2` 变为 `a=1;b=2`；`notcinema` 与 `x=notcinema=1` 不变。响应含 `strict-transport-security: max-age=31536000`（无 includeSubDomains/preload）和 `x-frame-options: SAMEORIGIN` |
| `docker run --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true` 挂示例配置 | 进程保持运行，手动停止 | 监听 8095。访问返回 500，日志是 `lookup emby ... no such host`。没有连上真实 Emby |

`.github/workflows/regression.yml` 只在 `push` 和 `pull_request` 上跑测试、安装包和解包、Compose 配置与 relay 镜像构建。没有部署、发布或预览工作流。打开 PR 会启动这条回归，不会部署。

### 独立结论与未改的产品边界

作者声称的清理保留、未授权播放记录、退出、超时、错误密码、退避、前端焦点和网关头，在本机用上述失败对照和通过测试复现。产品代码没有被改写。

仍保留、需要所有者知道的行为：

- 含未归档媒体的种子不再自动清理，磁盘可能持续增长；任务页仍可手动删除。
- HSTS 一年，且不含子域。浏览器记住后不能靠回滚服务端马上撤销。
- OpenList 返回 500 后停在 `moving` 的任务仍不自动重试。本节没有改这条策略，也没有连真实 OpenList。
- 按任务名恢复且云端没有 SHA1 时，仍可能只按大小发布。本节没有改，也没有用真实云盘验证。
- 管理入口和 Emby 仍在同一主机。发往 Emby 的 `cinema` cookie 已在真实 Caddy 上剥掉，但同源 XSS 仍能碰到管理路径。`X-Frame-Options` 加在 Emby 那条 handle 上，不是整个 `/discover/`。
- 名称像 sample、qB 报告大小小于或等于 1 GiB 的视频仍会随已归档正片删除。大于 1 GiB 才要求先归档。缺 `size` 时在删除前的字段比较抛错，状态留在 `pending` / `cleanup_check_failed`，本轮不删文件；这是失败关闭，不是明确的保留原因。
- `.mks` 和 `.ttml` 不在未归档媒体名单里，也不属于会归档的 srt/ass/ssa。正片已发布时，自动清理仍会删除它们。`test_sidecar_extensions_outside_the_allow_list_are_still_deleted` 记录了这个现状。本节没有扩大名单。
- 门户会隐藏非 2xx 的会话上报。历史媒体行只要 `kind=media` 且状态为 401，仍会占满最近 100 条。新的未授权请求由 relay 不再写入；旧媒体失败行与真实播放失败在日志里没有单独的授权标记。
- 退避只在内存里，重启即清零，单次等待最长 5 分钟。等待期间动作保持 `pending`，不会被标成完成。另一个 id 的动作不会被第一条的退避挡住。

### 未测

真实 Emby、qB、OpenList、PT、阿里云和公网客户端。安装包只做了校验和，没有在解包目录重跑 184 项。没有 `docker compose up` 整套影院。relay 的 Go 测试没有在宿主机 Go 1.22 上重跑。网关容器自身的 `cap_drop` 仍按作者延后项未改。

### 回滚与继续

未部署，没有数据库迁移。回滚这个分支不会改变已经下发的 HSTS，因为本节没有发布。代码回滚是revert导入提交 `21c817c0296a0b4bed9b498e5ed3c5bbc4d1b8e1`、独立测试提交 `5e4f3029f6db4d633fbe4c420f28188a8645a6e9` 和包含本节的提交，或直接关闭 PR。不要 force-push，不要改已经推送的提交。

### GitHub 回归终态

核对时间 2026-11-22T23:50Z。head `b134f6712e193880bc71946d5cd099a802b41065` 上有两次同名 `regression`，都是 Checks API 的 `completed` / `success`。工作流同时监听 `push` 和 `pull_request`，所以一次推送会跑两遍；两边都只构建并测试，没有部署。

| 触发 | 运行 | 作业 | 开始 / 结束 (UTC) | 结论 |
| --- | --- | --- | --- | --- |
| push | [45085166225](https://github.com/WenLiCG/movie/actions/runs/45085166225) | [137402009591](https://github.com/WenLiCG/movie/actions/runs/45085166225/job/137402009591) | 23:47:55 / 23:49:46 | success |
| pull_request | [45085182699](https://github.com/WenLiCG/movie/actions/runs/45085182699) | [137402061701](https://github.com/WenLiCG/movie/actions/runs/45085182699/job/137402061701) | 23:48:06 / 23:49:57 | success |

旧的 combined commit status 没有状态项，`state` 仍显示 `pending`。以 Checks API 的这两次 success 为终态。没有合并，没有部署。

继续由所有者决定 `.mks`/`.ttml`、`moving` 重试、无 SHA1 发布，以及是否把管理入口拆到独立子域。本节不合并、不部署。

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
