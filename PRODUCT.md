# 私人影院

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users and purpose
私人影院管理员通过统一网页找电影、选择资源、自动追剧、连接阿里云盘并查看 VPS 状态。观看由现有 Emby 完成。

## Capabilities and constraints
复用 Prowlarr、Sonarr、qBittorrent、OpenList 和现有上传器。上传校验及发布完成后才能清理本地文件。默认下载完成 7 天后清理，可关闭；云端影片保留。PT 凭据仍由原生 Prowlarr 管理。只允许 Emby 管理员使用定制界面。

## Product principles
- 搜索优先，结果出现后再展示筛选。
- 自动填写已知配置，错误给出可操作提示。
- 显示真实服务状态，不把下载完成等同于可以播放。
- 本地清理要有持久化上传凭证和云端复核。
