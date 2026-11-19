# MV-9b 验证脚本（A 上运行）

本文件与 `scripts/verify_a.py`、`scripts/verify_movie3.py` 一并纳入仓库，供在测试机 A 上复验公网电影墙站点（movie3），以及旧 emby 主机名是否仍返回 Emby 公开载荷。旧主机名上的空 200 只说明响应不是 Emby 公开载荷，不能据此推断主机已关闭或网络层未接通。不含口令、密钥或机器管理凭据。

## 依赖

- Python 3 标准库即可（`json`、`socket`、`sys`、`re`、`urllib.parse`、`time`）。无第三方包，无其它仓库文件依赖，两个脚本彼此独立。
- 在测试机 A 上对网关回环地址发起 HTTP：默认 `127.0.0.1:18000`，`Host` 分别为 `movie3.g60.net`、`movie2.g60.net`，以及负向探测用的 `emby.g60.net`。不要把口令放进 argv、环境变量或报告。

## 运行方式

在仓库根目录（脚本已放到 `scripts/` 之后），于测试机 A 执行：

```sh
python3 scripts/verify_a.py
python3 scripts/verify_movie3.py
```

不提供 stdin 时，网关白名单、缓存、旧主机名负向等检查仍会跑；普通用户登录/电影墙/PlaybackInfo/307/206 路径会记为跳过或失败（缺少凭据）。

如需跑普通用户路径，将已授权的测试用户凭据在**运行时**通过 stdin 传入 JSON（由平台密钥注入，不要写入仓库、补丁或日志）：

```sh
# 由调用方在内存中组装 JSON，字段名为 user 与 pw；不要把真实值写进命令历史或文件提交。
python3 scripts/verify_a.py < creds.json
python3 scripts/verify_movie3.py < creds.json
```

stdin 格式：`{"user":"<username>","pw":"<password>"}`。脚本在读入后立即丢掉口令，不打印、不写入 A 的磁盘、不放进 argv。登录成功判定要求返回的用户 `Name` 等于测试夹具名 `mv4viewer`（非管理员）；该名字是既有测试约定，不是口令。

成功时退出码 0，并打印一份已脱敏的 JSON 报告和 `SUMMARY total=… passed=… failed=…` 行。失败时退出码 1。

## 与 Caddy 配置的关系

这两个脚本验证的是**已部署的网关行为**（测试机 A 上隧道 Caddy 监听 HTTP `:18000`）。仓库模板是 `gateway/Caddyfile`（站点地址为 `{$MOVIE_DOMAIN}` / `{$MOVIE3_DOMAIN}`）。A 上的活文件由该模板按固定规则派生，不是另一套白名单：

1. 文件头部增加 `{ auto_https off }`
2. `{$MOVIE_DOMAIN} {` 改为 `http://{$MOVIE_DOMAIN} {`
3. `{$MOVIE3_DOMAIN} {` 改为 `http://movie3.g60.net {`（测试环境当前主机名）

派生后的现场副本不要当作产品仓库的第二份源；复验 A 时核对该派生文件的 sha256 与 inode，并与仓库模板对照上述三处差异。

## 完整 System/Info 的空 Token=

完整 `GET/HEAD /System/Info`（非 `/Public`）在 `/movie` 与 `/emby` 上，若 `Authorization` 或 `X-Emby-Authorization` 只带空的 `Token=`（或空的 `api_key` / `X-Emby-Token`），网关必须返回 403 且不得转给上游。网关只检查 token 值是否非空，不验证真实会话；非空伪 token 仍交上游。验证脚本的 `allowed` 分支遇到空 token 或 ADMIN 等占位伪 token 却拿到含 `LocalAddress` / `WanAddress` 的 200，或 403 `Forbidden` 正文后又出现这些内部地址，不得记为通过。
