# 完成视频上传器

Python 标准库实现，容器以 `1000:1000` 运行。读取 `/config/config.json`，`/media` 和 `/library` 挂载可写，供已确认的本地清理与索引备份使用。SQLite 状态和进程锁保存在 `/state`。通常每 60 秒检查一次，有后台上传任务时每 5 秒查询进度；`--once` 可运行一轮。失败按 1 分钟至 1 小时退避。日志只含任务 ID 与固定状态码，不打印响应正文、文件路径、账号或令牌。

配置字段：`openlist_url`、`openlist_token`、`qb_url`、`qb_username`、`qb_password`、`sonarr_url`、`sonarr_api_key`。URL 只接受 HTTP(S)，禁止内嵌账号、查询参数和片段；任何 HTTP 重定向均不跟随。OpenList 挂载点必须为 `/aliyun`，使用 AliyundriveOpen 驱动。

- 电影：只读取 qBittorrent 的 `cinema-movies` 分类完成任务，要求全部已选视频 `progress=1`；文件必须位于 `/media/downloads/movies`。输出 `Movies/<安全的种子名称>/<文件相对路径>`；种子文件名已有同名顶层目录时去掉重复一层。
- 独立剧集/综艺：读取 qB 的 `cinema-tv` 完成任务，根目录 `/media/downloads/tv-manual`，复用相同文件筛选和校验，输出 `TV/<安全的种子名称>/<文件相对路径>`，不访问或修改追剧。
- 自动追更剧集：只接受 Sonarr `/api/v3/series` 与 `/api/v3/episodefile` 返回的已入库视频，且位于 `/media/library/tv`。输出 `TV/<媒体库相对路径>`。按 Sonarr 的文件记录发现视频，并在对应导入目录查找同名字幕。已失效的文件记录单独延后，其他有效记录继续归档。qB 文件清单暂不可读时，等待恢复后再关联下载端字幕。
- 上传常见视频及可关联的 SRT、ASS、SSA 字幕。NFO、海报、种子和原盘目录留在原处。上传保留做种副本，清理阶段只处理已确认归档的到期任务。

`cleanup.py` 默认下载完成 7 天后清理本包分类的本地下载及做种任务，`/config/settings.json` 中 `retention_days=0` 关闭。重新验证发布记录、本地 SHA1、远端大小/可用 SHA1 和路径归属后，才调用原生 qB 删除；电视剧需确认硬链接关联，取消该集监控并调用 Sonarr 删除导入副本。执行删除前会检查导入目录中以剧集文件名开头、用点、下划线或连字符分隔的附属文件。除已列明的元数据、图片和字体外，自动清理须核实归档与本地内容，手动删除须能对应到已确认的范围。自定义工具使用其他命名或目录布局时，应先核对 Sonarr 管理的附属文件。关联不明、云端不可用或上传未完成时保留。清理计划持久化并逐次复核，自动保留期清理保留云端文件。完整规则见 [定制页面与清理](../docs/DISCOVER.md)。

上传先在本地分块计算 SHA1，再用固定大小块 `PUT /api/fs/put`，设置 `As-Task:true`、`Overwrite:false`、`X-File-Sha1`。OpenList 先在 VPS 缓存完整文件，再返回原生任务 ID；数据库记录该 ID 并查询后台状态，重启不重新传输已入队文件。提交前要求状态文件所在盘可用空间不少于文件大小加 2 GiB（默认与 OpenList 缓存同盘）。目标暂存位置为 `/aliyun/.cinema-upload/<任务ID>-<尝试次数>/<原文件名>`。仅当业务 `code=200`、文件状态未改变、原生任务状态为成功，且刷新远端目录后大小一致、远端提供的 SHA1 一致，才提交持久化成功凭证。单纯入队不生成成功凭证。随后记录移动意图，调用 `move`，刷新核对最终文件和暂存源消失后，才标记 `published`。HTTP 200 本身不代表成功。

**`/aliyun/Movies`、`/aliyun/TV` 和 `.cinema-upload` 必须由本上传器独占写入。** OpenList v4.2.9 的 Aliyun Open 驱动移动时使用 `check_name_mode: ignore` 并清理同名对象；API 的 `overwrite:false` 只是事前检查。上传器在上传和移动前刷新检查，拒绝任何已存在且没有本任务发布凭证的同名文件（即使大小、SHA1 相同），但无法给并发外部写入提供原子保护。不要同时用其他客户端整理这些目录。需要第二写入者时，先改为独立发布命名空间或修补上游驱动。

恢复与人工处理：

- 上传响应丢失后，程序刷新暂存文件并核对 SHA1。通过名称找回的原生任务还需确认成功状态，才可恢复发布凭证。未确认的任务保留源文件和旧暂存，并为它预留 VPS 缓存空间；空间足够时其他文件可继续上传。确认旧传输结束后才重传到新的尝试目录。
- 已有上传凭证可在重启后继续发布。移动响应丢失但最终文件验证成功、暂存源消失时，可自动恢复为成功；不会再次上传或重复提交移动。
- `move_pending` 表示结果尚不确定，程序只检查，不盲目重发可能仍在运行的移动。如果持续不变，先停上传器并查看 OpenList 的移动任务。确认无活动任务后，可由管理员在 OpenList 将该任务暂存文件移动到对应最终空目录；恢复上传器后会重新核对。已有目标冲突时保留双方，不自动覆盖。
- 保留并备份 `/state`。删除数据库会丢失文件归属和上传凭证；未知同名目标会因此按冲突处理。失败任务的 `error`、`state`、`stage`、`dest` 可以用只读 SQLite 查询检查，不要为了重试随意改状态。
- 持有本次 PUT 直接返回的成功任务回执时，远端缺 SHA1 可按大小校验。恢复旧任务或手动删除云端文件时要求匹配的 SHA1。当前以单进程、串行任务降低复杂度；大视频会有一次预读计算哈希。目录列表有分页和响应上限，超限或异常响应会拒绝继续。持久化使用 SQLite WAL、`synchronous=FULL`，依赖正常工作的持久卷。

本地检查：`python -m unittest discover -s tests -p test_uploader.py -v`（从项目根目录）。覆盖真实本机 HTTP 流式传输、上传中断、业务错误、大小/哈希验证、已有目标冲突、移动与重启恢复、路径安全、来源筛选、重定向及进程锁。它们是模拟服务检查，不是阿里云账号、真实大文件或 VPS 部署验收；Linux 容器的 `O_NOFOLLOW` 逐段打开也应在部署环境执行测试。

接口依据（2026-11-02 核对）：

- [OpenList v4.2.9 上传处理](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/fsup.go)：后台 PUT、业务响应、上传哈希和禁止覆盖字段；[完整本地缓存及后台任务](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/internal/fs/put.go)、[任务状态接口](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/task.go)。
- [OpenList v4.2.9 目录读取](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/fsread.go)：刷新、分页、`hash_info`；采用 list 元数据，不索取文件下载直链。
- [OpenList v4.2.9 移动处理](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/fsmanage.go)：移动可返回后台任务。
- [Aliyun Open 驱动](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/driver.go)、[哈希映射](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/types.go)：同名移动边界及 SHA1 元数据。
- [qBittorrent 5.0+ 官方 Web API](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29)：SID 登录、完成任务、文件相对路径、大小、优先级和进度。
- [Sonarr v4.0.20.3014 官方 OpenAPI v3](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/Sonarr.Api.V3/openapi.json)：series、episodefile、relativePath、path、size。

本目录程序为本项目自写代码，无运行时第三方 Python 依赖；只调用上述服务 API，没有复制它们的实现。上游服务及 Python 基础镜像分别遵守其自身许可证，见项目根目录的 `SOURCES.md`。
