# 私人影院：Debian All-in-One 安装脚本

一个安装文件部署 Prowlarr 搜索、Sonarr 追剧、qBittorrent 下载做种、OpenList 上传、Emby 电影墙与严格直连播放层。应用之间的配置由脚本接线；小型 Python 上传程序负责“确认完成后才发布”。适用 Debian 12/13，amd64/arm64。

观众在浏览器打开影院域名首页，或在 Emby 客户端把同一 HTTPS 地址填为服务器地址，使用影院分配的普通 Emby 用户名和密码观看。管理员在 `/discover/` 找片、按版本追剧、查看下载与归档进度。下载完成后默认继续保种 7 天。只有通过归档复核的任务才进入本地清理，设为 0 可关闭自动清理。

Debian 12 测试 VPS 曾完成 PT 搜索接线和真实影片云端发布。自定义挂载已部署到测试站，并用公网浏览器核验管理员登录、挂载占用提示和新建表单；隔离的 Emby 4.10.2.0 已验证追加片库目录。自定义挂载的真实云盘上传、长时间播放和第二台全新 VPS 重装仍待现场验收。

```text
电影：Prowlarr 搜索 → qB 下载、保留做种 ───────────┐
追剧：Sonarr ← Prowlarr → qB → Sonarr 已导入剧库 ──┤
                                                   ↓
                                      上传程序 → OpenList → 云端暂存
                                                   ↓ 上传确认、校验、发布
                                          云端 Movies / TV
                                                   ↓
                                    relay 虚拟索引 → Emby 电影墙
播放：观看者 ← 视频文件或分片 ← 阿里云盘（失败不回退 VPS 视频中转）
```

## 新 VPS 部署

完整操作步骤见 **[新 VPS 部署说明](docs/NEW-VPS.md)**，包含 Windows 上传/隧道、首次账号、云盘向导、站点接线及 HTTPS。

1. 准备 Debian 12/13 VPS 和自己的域名，放行 SSH、80/443 和 BT 56881/TCP+UDP。
2. 从源码交付时，先在仓库根目录运行 `python scripts/build_all_in_one.py`。将 `dist/` 下生成的安装器与同名 `.sha256` 文件上传到 VPS 的 `/root`，校验后运行：

```bash
cd /root
sha256sum -c cinema-all-in-one.sh.sha256
sudo bash cinema-all-in-one.sh
```

3. Windows 双击 `Open.cmd`，输入新的 `用户@VPS地址`；保持隧道窗口开启，在 Emby、OpenList、Prowlarr、Sonarr 和 qB 的原生页面分别将本地运维管理员设为 `admin`，填写同一个自选管理员密码。五个服务分别保存账号，修改一处密码后须逐个更新。普通观众使用独立 Emby 账号，PT 站点和云盘使用各自的授权。
4. 打开 `http://127.0.0.1:8097/discover/`，使用 Emby 管理员登录，首次登录会创建电影和电视剧媒体库。在设置中读取已有 OpenList 挂载，选择阿里云盘 Open 挂载并启用，也可输入新路径（如 `/aliyun-drive`）和 Open Refresh Token 创建挂载。随后配置下载器与追剧，通过“配置 Indexer”添加站点。首次连接时分别填写 OpenList 和 qB 管理员密码。后续连接沿用已保存的服务凭据，可在设置页刷新状态。
5. 在 VPS 构建播放入口并开启 HTTPS：

```bash
cd /opt/cinema
sudo docker compose --profile connected up -d --build relay uploader
sudo bash install.sh public
sudo bash install.sh status
```

默认源码和运行目录为 `/opt/cinema`。安装器不覆盖旧配置、不卸载冲突软件、不调整防火墙；依赖和容器镜像需要联网下载。Debian 12 amd64 已实测，其余受支持版本/架构未做真机安装验收。

电影墙由 Emby 验证账号和片库权限。使用 Cloudflare Tunnel 时也可以保留 Emby 用户名和密码登录。如果整个观影主机启用了 Cloudflare Access，浏览器会先要求额外身份验证，Emby 客户端也可能无法连接。

管理员用 Emby 管理员账号登录 `/discover/`。公网的 `/search/`、`/sonarr/`、`/qb/`、`/openlist/` 先由门户验证 Emby 管理员会话，再进入原生应用各自的认证。网关转发时会移除门户会话 Cookie，并保留原生应用自己的登录凭据。

需要从命令行接线或轮换已保存的后台凭据时，按 [SETUP.md](docs/SETUP.md) 完成挂载后执行 `bash install.sh setup`。不要把旧服务器的私有状态当作通用部署包。

## 日常操作与数据边界

- 手动电影：在 Prowlarr 搜索下载，qB 分类 `cinema-movies`，保存到 `/media/downloads/movies`。
- 独立剧集／综艺：定制页“下载此资源”只下载所选资源，不操作追剧；分类 `cinema-tv`，保存到 `/media/downloads/tv-manual`，校验后归档 TV。
- 删除追剧：停止监控并从列表移除，保留文件及正在进行的下载。
- 自动追剧：在 Sonarr 添加、监控并搜索，剧库 `/media/library/tv`；qB 分类 `sonarr-tv`，保留 `/media/downloads/tv` 做种副本。
- 操作一条追剧下载时，打开「下载与归档」中的「资源详情与操作」，即使项目只有一个种子也可单独处理。项目按钮会作用于整个追剧。单资源取消后再继续，只恢复该操作实际关闭过的剧集监控。旧任务缺少记录时只恢复下载。
- 上传程序处理上述已完成视频及可关联的 `.srt`、`.ass`、`.ssa` 字幕。NFO、海报和蓝光目录不进入上传队列；多文件任务详情中将其标记为“不自动归档”。
- 上传先进入所选挂载下的 `.cinema-upload`，校验后移到同一挂载的 `Movies` 或 `TV`。切换路径只影响随后登记的归档任务；旧任务、Emby 已有影片和本地下载继续使用原路径。保留仍有影片或任务的旧挂载。默认 7 天后只清理已归档且复核通过的本地源及做种任务；云端成品和失败暂存不自动删除。
- Sonarr 可把文件名不同或位于子目录的字幕关联到剧集。程序删除导入副本前会检查整部剧的目录，有无法核实的附件时保留本地资源。
- **Movies/TV 必须仅由这一个上传程序写入。** OpenList 阿里移动接口缺少原子同名保护；程序会检查已有冲突，但不能与人工、CloudDrive 或其他同步器并发整理同一目录。
- 上传断线后先核对原生任务和暂存哈希。结果未确认时保留源文件，并给暂存预留磁盘空间。确认旧传输结束后，才可重传到新暂存目录。发布请求结果不明时先查任务，保留 SQLite 中的发布凭证。
- 单个上传已确认传输结束，但暂存文件未通过校验时，后台保留该文件继续核实，让其他影片继续上传。任务页区分下载暂停、归档失败、部分已发布及等待核对。
- Emby 只挂载云端虚拟索引，不能访问实际下载目录。默认每 10 分钟刷新索引，再由 Emby 扫描。原画不兼容时可选阿里云端转码，仍需实际客户端验收。

```bash
sudo bash install.sh status
sudo docker compose logs --tail=100 uploader sonarr prowlarr
sudo bash install.sh stop
```

页面提示“本地应用 Sonarr（或 Emby、Prowlarr、qBittorrent、OpenList）暂时无法连接”时，用 `sudo docker compose ps` 确认该服务在运行，再看 `sudo docker compose logs --tail=100 <服务名>`。其他现象的排查顺序见[开发交接](HANDOFF.md#部署备份与排查)。

`stop` 保留所有数据，不执行删除或 `down -v`。重新运行 `install` 启动基础服务，`setup` 启动已配置的播放和上传，`public` 恢复公网入口。`install.sh` 本身不升级或覆盖旧部署；已有部署从独立的新源码目录运行 `sudo bash scripts/update_portal.sh /opt/cinema` 升级，脚本先备份源码、配置状态和 SQLite 快照，失败时恢复旧源码与镜像。仍建议事先另行备份 `.env`、整个 `state/`（包括上传数据库）和媒体，详见 [现有部署升级](docs/DISCOVER.md#更新已有部署)。

## 源码与验证

- `install.sh`：安装、接线、启动、状态与停止入口。
- `configure.py`、`integration.py`：新目录初始化、私有凭据文件和原生 API 接线。
- `uploader/uploader.py`：标准库 HTTP 流式上传、SQLite 完成凭证、发布确认与重试。
- `compose.yaml`：管理服务、定制页面、上传与播放及 HTTPS 网关；没有 Docker Socket、特权模式、FUSE 或额外数据库服务。
- `relay/strict-direct.patch`：固定上游源码上的播放授权与禁止视频回源补丁；保留上游 GPL 许可。
- `scripts/build_all_in_one.py`：仅打包白名单源码，生成内嵌校验的单文件安装器及 SHA256，不含 `.env`、状态、媒体或开发工具。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
NODE_PATH=<playwright目录> node tests/check_ui.cjs
NODE_PATH=<playwright目录> node tests/independent-ui-flows.cjs
for script in install.sh scripts/*.sh; do bash -n "$script"; done
python3 scripts/build_all_in_one.py
```

安装器内含运行源码、回归用例与操作文档。GitHub CI 使用独立测试目录安装 Playwright，运行桌面与手机尺寸的 Chrome 回归，再从解包目录构建并启动容器。每次交付的测试环境、结果和现场待验收项见[开发交接](https://github.com/WenLiCG/movie/blob/main/HANDOFF.md)。故障恢复与播放验收见[操作说明](docs/SETUP.md)，镜像版本和上游许可见[来源清单](SOURCES.md)。
