# 私人影院：Debian All-in-One 安装脚本

一个安装文件部署 Prowlarr 搜索、Sonarr 追剧、qBittorrent 下载做种、OpenList 上传、Emby 电影墙与严格直连播放层。应用之间的配置由脚本接线；小型 Python 上传程序负责“确认完成后才发布”。适用 Debian 12/13，amd64/arm64。

**已在 Debian 12 测试 VPS 完成部署、PT 搜索接线和真实影片云端发布；长时间播放及第二台全新 VPS 重装仍待验收。** 不把容器运行当成业务验收。它是一键安装多服务，不是离线镜像包；部署和操作分别见 [新 VPS 安装说明](docs/NEW-VPS.md)与[使用说明](docs/DISCOVER.md)。

**新增找片与追剧页面 `/discover/`**：追剧项目合并、暂停/取消/选择本地删除、免二次密码进入 Emby、中央搜索、默认 1080p（无匹配时显示全部）、体积升序、清晰度/编码/站点筛选、一键下载、电影／剧集综艺两个资源入口、按选中版本持续追更、中文剧名与自动加载选集、Emby/找片页全局亮色主题、阿里云盘 Token 引导、下载器自动配置，以及 VPS 磁盘/内存/CPU。默认下载完成 7 天后清理已确认云端发布的本地文件与做种任务，0 关闭；云端影片保留。已部署到测试 VPS，真实 HTTPS 登录、资源数据、站点搜索、画质筛选和原生接线通过；已有真实影片发布入库，自行生成资源的取消、重启恢复、外挂字幕和保留期清理通过；长时间播放仍待验收。首次连接、清理边界和已有部署更新命令见 [定制页面说明](docs/DISCOVER.md)。

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
2. 上传单文件安装器和 SHA256，在 VPS 校验后运行：

```bash
cd /root
sha256sum -c cinema-all-in-one.sh.sha256
bash cinema-all-in-one.sh
```

3. Windows 双击 `Open.cmd`，输入新的 `用户@VPS地址`；保持隧道窗口开启，完成 Emby、OpenList、Prowlarr、Sonarr 和 qB 首次账号设置。
4. 打开 `http://127.0.0.1:8097/discover/`，使用 Emby 管理员登录，首次登录自动创建电影、电视剧媒体库并应用中文刮削默认设置；填写 Open Refresh Token，自动连接云盘和下载器，再通过“配置 Indexer”添加自己的站点。首次填写 OpenList 和 qB 各自的账号；无需与 Emby 一致，已连接后沿用服务凭据。
5. 在 VPS 构建播放入口并开启 HTTPS：

```bash
cd /opt/cinema
docker compose --profile connected up -d --build relay uploader
bash install.sh public
bash install.sh status
```

默认源码和运行目录为 `/opt/cinema`。安装器不覆盖旧配置、不卸载冲突软件、不调整防火墙；依赖和容器镜像需要联网下载。Debian 12 amd64 已实测，其余受支持版本/架构未做真机安装验收。

`https://你的域名/` 是电影墙，`/discover/` 是找片与追剧，`/search/`、`/sonarr/`、`/qb/`、`/openlist/` 保留原生管理入口。日常使用 HTTPS 不需要隧道；原生应用分别认证，定制页使用 Emby 管理员。

需要不同 OpenList 管理员账号或原生命令行接线时，按 [SETUP.md](docs/SETUP.md) 完成挂载后执行 `bash install.sh setup`。不要把旧服务器的私有状态当作通用部署包。

## 日常操作与数据边界

- 手动电影：在 Prowlarr 搜索下载，qB 分类 `cinema-movies`，保存到 `/media/downloads/movies`。
- 独立剧集／综艺：定制页“下载此资源”只下载所选资源，不操作追剧；分类 `cinema-tv`，保存到 `/media/downloads/tv-manual`，校验后归档 TV。
- 删除追剧：停止监控并从列表移除，保留文件及正在进行的下载。
- 自动追剧：在 Sonarr 添加、监控并搜索，剧库 `/media/library/tv`；qB 分类 `sonarr-tv`，保留 `/media/downloads/tv` 做种副本。
- 上传程序处理上述已完成视频，并把同目录中与视频匹配的外挂字幕（.srt、.ass、.ssa）随该视频归档；字幕尚未发布时不发布视频。不处理 NFO、海报或蓝光目录，不增加电影自动改名/升级服务。
- 上传先隔离到 `/aliyun/.cinema-upload`，明确成功并校验后才移到 `/aliyun/Movies` 或 `/aliyun/TV`。默认 7 天后只清理已归档且复核通过的本地源及做种任务；云端成品和失败暂存不自动删除。
- **Movies/TV 必须仅由这一个上传程序写入。** OpenList 阿里移动接口缺少原子同名保护；程序会检查已有冲突，但不能与人工、CloudDrive 或其他同步器并发整理同一目录。
- 断线但没有成功凭证的暂存文件不会发布；下一次会新建尝试目录，旧暂存可能占空间，核查后人工清理。发布请求结果不明时先查任务，不能靠清空 SQLite 强制重来。
- Emby 只挂载云端虚拟索引，不能访问实际下载目录。默认每 10 分钟刷新索引，再由 Emby 扫描。原画不兼容时可选阿里云端转码，仍需实际客户端验收。

```bash
sudo bash install.sh status
sudo docker compose logs --tail=100 uploader sonarr prowlarr
sudo bash install.sh stop
```

`stop` 保留所有数据，不执行删除或 `down -v`。重新运行 `install` 启动基础服务，`setup` 启动已配置的播放和上传，`public` 恢复公网入口。更新前停止容器并备份 `.env`、整个 `state/`（包括上传数据库）、媒体及当前源码；本脚本没有自动升级或覆盖旧部署功能。

## 源码与验证

- `install.sh`：安装、接线、启动、状态与停止入口。
- `configure.py`、`integration.py`：新目录初始化、私有凭据文件和原生 API 接线。
- `uploader/uploader.py`：标准库 HTTP 流式上传、SQLite 完成凭证、发布确认与重试。
- `compose.yaml`：管理服务、定制页面、上传与播放及 HTTPS 网关；没有 Docker Socket、特权模式、FUSE 或额外数据库服务。
- `relay/strict-direct.patch`：固定上游源码上的播放授权与禁止视频回源补丁；保留上游 GPL 许可。
- `scripts/build_all_in_one.py`：仅打包白名单源码，生成内嵌校验的单文件安装器及 SHA256，不含 `.env`、状态、媒体或开发工具。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash -n install.sh
# 以下是开发/重新打包操作：
python3 scripts/build_all_in_one.py
# 可选，宿主正式运行无需 Node：
node tests/search-entry.test.cjs
node tests/watch-handoff.test.cjs
node tests/task-controls.test.cjs
```

验证涵盖目录保护、私有文件权限、原生接口接线逻辑、HTTP 流式上传与失败恢复、单文件安全解包、Bash 语法、Compose 配置解析、中文刮削配置和搜索导航。Python 回归检查是 `python3 -m unittest discover -s tests -p 'test_*.py'`，项数以该命令当次打印的结果为准。Windows 通过刮削、登录、安装包和任务界面相关检查。镜像构建、网关配置及浏览器交互通过。真实生成资源验证了上传取消、重启恢复、字幕、归档和本地保留期清理。另一台全新 VPS、长时间播放及磁盘压力测试尚未执行。失败恢复和播放边界见 [docs/SETUP.md](docs/SETUP.md)，版本及许可见 [SOURCES.md](SOURCES.md)。
