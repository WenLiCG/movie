# 来源、接口与许可

## 界面设计（2026-11-04）

视觉依据为用户指定的 Emby 浅色主题；Impeccable 用于界面优化和渲染验收。参考 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 中的 [Spotify 内容层次](https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/spotify/DESIGN.md)和 [Raycast 控件细节](https://github.com/VoltAgent/awesome-design-md/blob/main/design-md/raycast/DESIGN.md)，采用语义配色、表面层级及统一控件原则；该参考集合采用 [MIT 许可](https://github.com/VoltAgent/awesome-design-md/blob/main/LICENSE)（Copyright © 2026 VoltAgent）。项目具体规范见 DESIGN.md。SVG 图标为本项目编写，未增加生产依赖。

核查日期：2026-11-02（Asia/Shanghai）。各软件由其上游维护，本包不重新授权其代码。准确部署镜像以 `compose.yaml` 的 digest 为准。

搜索附加信息沿用 [HDSky 原生定义](https://github.com/Prowlarr/Indexers/blob/master/definitions/v11/hdsky.yml)与 [SpringSunday 原生定义](https://github.com/Prowlarr/Indexers/blob/master/definitions/v11/springsunday.yml)的页面结构和参数。`portal/metadata.py` 为自写标准库 HTML 解析器，不复制整个上游定义，不修改 Prowlarr 配置。单集搜索使用 [Sonarr EpisodeSearch](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/IndexerSearch/EpisodeSearchService.cs)；显式 EpisodeSearch 可处理未监控剧集，不需开启全剧监控。

新增定制页面与清理规则：复用 [Prowlarr 搜索/下发接口](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/Prowlarr.Api.V1/Search/SearchController.cs)、[Sonarr 剧集监控服务](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Tv/EpisodeMonitoredService.cs)、[Sonarr 集文件删除接口](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/Sonarr.Api.V3/EpisodeFiles/EpisodeFileController.cs)、[OpenList 存储接口](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/storage.go)及[驱动字段](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/meta.go)。页面和清理程序自写，仅用 Python 标准库，无新运行时依赖。`relay/managed-token.patch` 在原 GPL 上游中增加私有令牌文件读取，沿用原许可。授权引导使用 [OpenList 官方文档](https://doc.oplist.org/guide/drivers/aliyundrive_open)，不是旧版浏览器令牌提取流程。

| 组件 | 核实版本与来源 | 许可或边界 |
| --- | --- | --- |
| Prowlarr | [v2.7.0.5723](https://github.com/Prowlarr/Prowlarr/releases/tag/v2.7.0.5723)，[LinuxServer 镜像](https://docs.linuxserver.io/images/docker-prowlarr/) | GPL-3.0；镜像打包许可见其仓库 |
| Sonarr | [v4.0.20.3014](https://github.com/Sonarr/Sonarr/releases/tag/v4.0.20.3014)，[LinuxServer 镜像](https://docs.linuxserver.io/images/docker-sonarr/) | GPL-3.0；稳定版使用 API v3 |
| OpenList | [v4.2.9](https://github.com/OpenListTeam/OpenList/releases/tag/v4.2.9) | AGPL-3.0 |
| 播放中间件 | [go-emby2openlist 固定提交](https://github.com/AmbitiousJun/go-emby2openlist/tree/9b049b885388108c61f234244c1249f9a52a8dd6)，`9b049b885388108c61f234244c1249f9a52a8dd6` | GPL-3.0；补丁受上游适用许可约束，镜像保留 LICENSE |
| Emby | [emby/embyserver](https://hub.docker.com/r/emby/embyserver)，固定 digest | 自有许可及[服务条款](https://emby.media/terms.html)，不能当作完全开源软件 |
| qBittorrent | [LinuxServer 镜像](https://docs.linuxserver.io/images/docker-qbittorrent/)，固定 digest | qBittorrent GPL-2.0+；镜像许可见各自来源 |
| Caddy | [官方文档](https://caddyserver.com/docs/) | Apache-2.0 |

## 镜像证据

核查时以下版本标签与各自 latest 的多平台 manifest digest 一致，均含 amd64、arm64。这里固定的是镜像索引，而非单个平台 image ID：

- `linuxserver/prowlarr:version-2.7.0.5723`：`sha256:04be30163fbaf48d6aeee7b5893c9f6614ffbad6d853971df0d6dcdf01c7f104`。[标签接口](https://hub.docker.com/v2/repositories/linuxserver/prowlarr/tags/version-2.7.0.5723)
- `linuxserver/sonarr:version-4.0.20.3014`：`sha256:386c962ee6e260e3a5656b25df2ed056bcc9b6410e197d075f9547fd14c23bca`。[标签接口](https://hub.docker.com/v2/repositories/linuxserver/sonarr/tags/version-4.0.20.3014)

其他应用也由 Compose 固定 digest，避免 latest 后续变化自动替换版本。公开发布及镜像存在不等于目标 VPS 已运行验证。

自建服务基础镜像同样固定多平台 digest（2026-11-02 从 Docker Hub 官方标签接口核对）：

- [Python 3.14-slim](https://hub.docker.com/v2/repositories/library/python/tags/3.14-slim)：`sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8`。
- [Go 1.27-alpine](https://hub.docker.com/v2/repositories/library/golang/tags/1.27-alpine)：`sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb`。
- [Alpine 3.22](https://hub.docker.com/v2/repositories/library/alpine/tags/3.22)：`sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f`。

安装器按 [Docker 官方 Debian 安装文档](https://docs.docker.com/engine/install/debian/) 配置官方 apt 源。没有使用 `curl | sh` 安装引擎；发现冲突包会停止，不卸载已有运行时。Python 为 PSF 许可，Go 为 BSD 风格许可，Alpine 所含软件分别保留其许可。

## 单域名子目录

- Prowlarr/Sonarr 使用原生 `UrlBase`；固定版本的 [Bootstrap](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/NzbDrone.Host/Bootstrap.cs)读取 .NET 环境配置，对应 `PROWLARR__SERVER__URLBASE=/search`、`SONARR__SERVER__URLBASE=/sonarr`。内网接线和 uploader 也保留该前缀。
- OpenList 的[子目录说明](https://doc.oplist.org/faq/howto)要求 `site_url` 带 `/openlist`。当前固定 Docker 镜像的入口以 `server --no-prefix` 启动，因此 Compose 使用 `SITE_URL` 环境变量，而非 `OPENLIST_SITE_URL`；原生 API 也位于 `/openlist/api/`。
- qB 按[官方反向代理示例](https://github.com/qbittorrent/qBittorrent/wiki/NGINX-Reverse-Proxy-for-Web-UI)剥离 `/qb/` 后转发，保留 Origin/Referer 和认证保护。Caddy 统一终止 TLS。
- 单文件安装包明确包含无扩展名的 `gateway/Caddyfile`，对应打包回归检查避免再次遗漏。

## 自动接线接口

- [Prowlarr 固定版 OpenAPI](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/Prowlarr.Api.V1/openapi.json)：`/api/v1/applications`、`/downloadclient` 及各自 `/schema`、`/test`，使用 `X-Api-Key`。
- [Sonarr 应用设置](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/NzbDrone.Core/Applications/Sonarr/SonarrSettings.cs)：`prowlarrUrl`、`baseUrl`、`apiKey`、同步分类；应用完整同步值为 `fullSync`。
- [Prowlarr qB 设置](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrentSettings.cs)：主机、端口、用户密码及 `category`。接线从运行实例 schema 默认值修改。
- [Sonarr 固定版 OpenAPI](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/Sonarr.Api.V3/openapi.json)：API v3 的下载器、根目录、媒体管理和下载处理；不使用开发版 API v5。
- [Sonarr qB 设置](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrentSettings.cs)与[实现](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrent.cs)：`tvCategory`、`tvImportedCategory`；关闭 `removeCompletedDownloads` 后，不把完成种子当作可移动/移除数据。
- LinuxServer 的 [Prowlarr](https://github.com/linuxserver/docker-prowlarr/blob/master/root/etc/s6-overlay/s6-rc.d/svc-prowlarr/run)和 [Sonarr](https://github.com/linuxserver/docker-sonarr/blob/master/root/etc/s6-overlay/s6-rc.d/svc-sonarr/run)启动脚本指定 `-data=/config`；应用[配置读取器](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Configuration/ConfigFileProvider.cs)读取 XML 中 `ApiKey`。接线读取本包配置，不输出密钥。
- [qBittorrent 官方 WebUI API（5.0+）](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29)：偏好设置、分类、任务及文件列表。固定镜像实测为 v5.3.0；其[登录实现](https://github.com/qbittorrent/qBittorrent/blob/release-5.3.0/src/webui/api/authcontroller.cpp)成功时响应体为空，失败返回 HTTP 401；会话 Cookie 实测为 `QBT_SID_8080`。接线及上传器兼容旧版 `Ok.`/`SID` 和新版空响应/`QBT_SID_<端口>`，必须存在非空会话 Cookie；重复登录允许沿用现有会话。
- [OpenList v4.2.9 安装向导接口](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/setup.go)：首次访问创建管理员，`/api/public/init_status` 检查初始化、`/api/public/init/setup` 完成初始化；不再假定启动日志有初始管理员密码。

## 三站适配证据

- [HDSky](https://github.com/Prowlarr/Indexers/blob/master/definitions/v11/hdsky.yml)：Cookie 登录，支持电影及电视剧搜索。
- [SpringSunday](https://github.com/Prowlarr/Indexers/blob/master/definitions/v11/springsunday.yml)：Cookie 登录，支持电影及电视剧搜索。
- [M-Team - TP](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/NzbDrone.Core/Indexers/Definitions/MTeamTp.cs)：API Key，向站点发送 `x-api-key`。

这些仅证明适配器及字段含义；没有登录用户账号，不能保证特定 Cookie、站点权限或 VPS 出口可用。

## 上传、发布与播放

- [OpenList Docker](https://doc.oplist.org/guide/installation/docker)：当前镜像使用容器用户配置，不依靠旧 PUID/PGID。
- [阿里云盘 Open 驱动文档](https://doc.oplist.org/guide/drivers/aliyundrive_open)：授权、代理、权益及服务限制。
- [OpenList 上传接口](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/fsup.go)和[阿里上传实现](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/upload.go)：当前使用原生后台上传；[后台任务实现](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/internal/fs/put.go)先缓存完整内容，[任务接口](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/task.go)查询成功后，再刷新核对远端对象。阿里驱动仍须完成 `openFile/complete`。新挂载启用[官方秒传](https://doc.oplist.org/guide/drivers/aliyundrive_open#_3-2-3-instant-upload)，不命中则正常上传。
- [阿里列表请求](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/util.go)、[对象模型](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/types.go)和[文件操作](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/drivers/aliyundrive_open/driver.go)：目录可见不独立证明完成，移动也没有可依赖的原子不覆盖保证。
- 因此本包采用隔离暂存、明确成功凭证、远端校验和发布后确认。成品目录必须由一个 uploader 独占写入；未知完成的旧尝试保留隔离，不自动发布。仍需真实中断/重试验收。
- [中间件说明](https://github.com/AmbitiousJun/go-emby2openlist#使用说明-openlist-本地目录树生成)：虚拟媒体索引、云端转码。本包仅扫描 Movies/TV，严格直连与授权/HLS 补丁位于 `relay/strict-direct.patch`。
- [Emby 浏览器 API 客户端](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js)：搜索入口使用现有页面信息，不向浏览器发送服务端管理密钥。
- [Emby 用户认证接口](https://dev.emby.media/reference/RestAPI/UserService/postUsersAuthenticatebyname.html)及上述官方客户端：`public` 用临时管理员会话读取全部用户的 `HasPassword`、`HasConfiguredPassword`，最后调用 `Sessions/Logout`；不把向导完成等同于已设置密码。

没有把附件中的服务器地址、旧 Token、站点 Cookie 或密码写入部署包。用户自行完成 PT 凭据、阿里授权及首次账号设置。静态检查、离线回归不替代真实 Debian 启动、PT 下载、云端发布和直连播放验收。

## 按版本追更（2026-11-02）

复用 Sonarr v4.0.20.3014 的 [Release Profiles](https://github.com/Servarr/Wiki/blob/master/sonarr/settings.md#release-profiles)、[规则判断](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/DecisionEngine/Specifications/ReleaseRestrictionsSpecification.cs)、[标签作用域](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Profiles/Releases/ReleaseProfileService.cs)、[正则语法](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Profiles/Releases/PerlRegexFactory.cs)和[原生交互搜索／下载](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/Sonarr.Api.V3/Indexers/ReleaseController.cs)。一个 Profile 的 required 数组为 OR，本包自写规则生成器使用一条合取表达式；专属标签仅作用于目标剧集。追更的版本约束与独立下载分离；独立下载使用 [Prowlarr 原生 GrabRelease 的 DownloadClientId](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/Prowlarr.Api.V1/Search/SearchController.cs) 显式选择独立分类客户端，仍只下发原生缓存中所选 GUID，不依靠 Sonarr 建立追剧。仅调用这些 GPL-3.0 服务的 API，没有复制上游实现或增加运行依赖。

上传网络诊断与 VPS 专属配置依据：[Linux TCP 拥塞控制](https://docs.kernel.org/networking/ip-sysctl.html#tcp-variables)、[Compose 命名空间 sysctls](https://docs.docker.com/reference/compose-file/services/#sysctls)、[Compose 覆盖文件](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)。本次只采用现有内核模块和原生容器配置，没有导入网络代理代码或增加运行依赖；参数仅在真实生成数据上传对照后应用到 OpenList。


## 上传恢复与保留期（2026-11-03）

- [OpenList 4.2.9 上传任务初始化](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/internal/bootstrap/task.go)：上传任务不持久化；因此通过启动实例标记确认进程更换，不以任务消失的时间推断后台工作已经停止。
- [原生任务状态](https://github.com/OpenListTeam/tache/blob/v0.2.2/state.go)：取消中、失败中和内部重试继续等待；最终失败才进入有上限的重新提交。手动取消要求显式重试。
- [Sonarr 缺集搜索](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/IndexerSearch/EpisodeSearchService.cs)：保留带 seriesId 的原生 MissingEpisodeSearch，原生实现按剧筛选，并排除已排队项目；选集请求按 100 集分批提交。
- [Sonarr 额外文件导入](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Extras/ExtraService.cs)及[字幕重命名](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Extras/Subtitles/SubtitleService.cs)：启用原生字幕导入，通过内容校验关联原下载字幕和导入后的字幕，支持导入时语言后缀被标准化。
- [Caddy forward_auth](https://caddyserver.com/docs/caddyfile/directives/forward_auth)：复用影院管理员会话保护高级管理入口，保留原应用认证及 Authorization 请求头。

以上仅调用原生 API；没有复制上游实现或增加生产运行依赖。


## 项目控制与 Emby 会话交接（2026-11-03）

- [qBittorrent 5.3 原生任务接口](https://github.com/qbittorrent/qBittorrent/blob/release-5.3.0/src/webui/api/torrentscontroller.cpp)：使用 stop/start/delete，删除时显式选择 deleteFiles，保留应用现有分类和本地目录边界。
- [Emby 客户端凭据](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/credentials.js)与[连接管理](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/connectionmanager.js)：实际部署的 Emby 4.10.2 使用 servercredentials3 和服务器下的 Users 会话记录。仅交接当前用户令牌，通过已登录且校验 CSRF 的接口获取；没有复用或返回服务管理密钥，也没有复制上游实现。

## Emby 中文刮削默认设置（2026-11-03）

- [原生媒体库配置接口](https://dev.emby.media/reference/RestAPI/LibraryStructureService/postLibraryVirtualfoldersLibraryoptions.html)：从运行中服务器的 `/Libraries/AvailableOptions` 读取当前版本默认模板及可用提供方，填入中文、中国地区和云盘适用选项。TMDB 优先，保留已安装的默认补充来源；关闭 Image Capture 与视频分析功能。
- [刷新接口](https://dev.emby.media/reference/RestAPI/ItemRefreshService/postItemsByIdRefresh.html)及[元数据管理说明](https://emby.media/support/articles/Metadata-manager.html)：使用 FullRefresh，同时显式 `ReplaceAllMetadata=false`、`ReplaceAllImages=false` 补充缺失资料，不批量替换已有条目内容。没有复制上游代码或增加依赖。

首次管理员登录自动应用一次，成功后保存初始化标记；后续登录及服务重启保留手动调整。失败不阻止登录，由设置页提示重试；手动按钮可以重新应用推荐配置。

## 播放记录（2026-11-03）

- [Emby 播放方式](https://emby.media/support/articles/DirectPlay-Stream-Transcoding.html)描述格式保留、重封装和转码，不能单凭模式名称证明网络流量绕过 VPS。
- [HTTP 307](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/307)及运行中 relay 的视频响应用于判定网盘跳转；客户端原生播放进度作为另一项证据。录制逻辑、只读接口与页面为本包新增，relay 增补源码沿用上游 GPL-3.0 适用许可，没有新增服务或生产依赖。
- [查询参数的加号语义](https://developer.mozilla.org/en-US/docs/Web/API/URLSearchParams#preserving_plus_signs)：对 Emby 自定义媒体标识中的 Base64 路径做兼容解码，恢复被表单查询解码转换的 `+`；验证原始路径不变、授权资源可跳转、其他影片路径仍被拒绝。


## 中文字幕与站点推荐（2026-11-04）

- [Emby 自动字幕下载](https://emby.media/support/articles/Automatic-Subtitle-Downloads.html)：先配置字幕提供方，媒体库设置语言和来源，再由计划任务下载；要求完全哈希匹配会减少匹配结果。默认字段来自运行中 Emby 4.10.2 原生模板，应用后回读确认，保留已有语言及整组字幕偏好。
- [Prowlarr 原生搜索控制器](https://github.com/Prowlarr/Prowlarr/blob/v2.7.0.5723/src/Prowlarr.Api.V1/Search/SearchController.cs)：首页复用空查询、indexerIds、电影／电视剧分类和 limit，不额外爬取站点详情。分类与站点 ID 使用重复参数数组，只在近期返回列表内按做种人数排序。只调用原生 API，没有复制上游 GPL-3.0 实现或增加依赖。
- 首页中文名称复用项目 `portal/metadata.py` 的 HDSky、SpringSunday 列表解析：每个入选站点读取一页，按站点 ID 和资源 ID 精确对应副标题，保留原搜索词和完整资源名；没有中文资料时回退原名。只调用已配置站点，不增加翻译服务、账户或依赖。
- [Emby 下载说明](https://emby.media/support/articles/Sync.html)：Web 的单文件原件下载与客户端 Downloads & Sync 分开；本项目当前明确禁止 Emby 下载路由，使用既有 OpenList／阿里云盘入口取回原件。
- [Jellyfin](https://jellyfin.org/docs/)及其 [Open Subtitles 插件](https://jellyfin.org/docs/general/server/plugins/open-subtitles/)、[Bazarr](https://www.bazarr.media/)：用于说明替代服务器与字幕管理器的职责，本轮没有引入其代码或部署其他服务。

## Emby Web 静态模块路由修正（2026-11-04）

修正所固定的 go-emby2openlist 源码补丁：`/web/`、`/emby/web/` 的正常静态路径先于模糊媒体路由分发，避免 `videos/videos.js` 被误作需要播放授权的视频接口。路径须规范化；回源响应的媒体类型检查、实际视频鉴权及禁止 VPS 中转保持有效。路由级回归涵盖模块名、查询注入、目录穿越、无令牌媒体请求，以及伪装为静态脚本的媒体响应。另用初始化同步确保 m3u8 回调在首个调用前已发布。新增源码延续上游 GPL-3.0 许可，无新增依赖。


## 多版本影片授权（2026-11-04）

依据运行中 Emby 4.10.2 的 `/Items/{id}/PlaybackInfo` 响应及 [Emby 多版本影片说明](https://emby.media/support/articles/Movie-Naming.html#multi-version-movies)。合并影片的 MediaSources 中各版本有独立 ItemId；原先要求所有 ItemId 等于入口影片会误拒绝整组。修正为确认组内含入口影片，再按原有用户令牌及授权路径规则放行。回归覆盖合法备用版本、仅返回其他影片的异常响应及组外路径拒绝；新增补丁延续上游 GPL-3.0 许可，未增加依赖。


## 删除所有（2026-11-04）

- 复用 [OpenList 4.2.9 原生文件删除](https://github.com/OpenListTeam/OpenList/blob/v4.2.9/server/handles/fsmanage.go)，删除前回读目标大小和 SHA1，删除后从刷新成功的父级列表证明消失；不把 404/500 或目录请求失败直接当作删除证据。只调用接口，没有复制实现或增加依赖。
- 使用 [Emby 专用 API 密钥](https://dev.emby.media/doc/restapi/API-Key-Authentication.html)、[创建接口](https://dev.emby.media/reference/RestAPI/SessionsService/postAuthKeys.html)及[媒体库刷新](https://dev.emby.media/reference/RestAPI/ItemRefreshService/postItemsByIdRefresh.html)，后台扫描无需保存当前用户的登录会话。密钥仅保存在现有私有服务配置中。
- 本项目联动流程使用原有持久任务队列及共享锁，沿用 qB 和 Sonarr 的文件删除边界。OpenList 没有按对象哈希条件删除的原子接口，仍要求 Movies/TV 只有一个写入者；外部并发替换不在原子保证范围内。

该版本的 remove_empty_directory 保留传入根目录；本项目对明确选中的文件目录逐级核对为空、且没有其他归档任务占用后才删除，不向 Movies/TV 根目录执行递归清理。重复删除／重新下载的索引备份按每次操作时间分别保存，避免不同版本的占位内容覆盖旧备份。
