---
cursor:
  subagentId: "bc-a983a9cd-2d74-5f11-8793-411056b13f10"
---

# PR #7 只读审查（software-release-gate v1.0）

这是质量建议，不是合并批准。没有沿用 `docs/mv9b-followup-review.md` 的结论。作者自测和旧审查都不算通过。文件已经进仓库，也不等于通过。

**一句话：建议 no-go。** 远程 head `9d6c23fd733b447ff708fcf974cf3687cbb19b62`、tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`、base `mv-9-emby-public` 都核对过；空的 `Token=` 仍会被网关转到上游，`scripts/verify_a.py` 仍会把空 token 的成功响应判成通过，`scripts/verify_movie3.py` 仍会把伪造 token 得到、且含内部地址的 200 判成通过。这些缺陷没有修。

PR：`https://github.com/WenLiCG/movie/pull/7`（草稿，未合并）。审查线程 `bc-a983a9cd-2d74-5f11-8793-411056b13f10`。没有改作者分支，没有 commit、push，没有在 PR 上评论或 approve，没有合并，没有部署，没有访问 A，没有读生产凭据。

## 环境

- Linux 6.12.113+ x86_64
- Python 3.12.3
- Node v22.23.4
- 本地 Caddy：官方发布包 v2.10.2，`h1:g/gTYjGMD0dec+UgMw8SnfmJ3I9+M2TdvoRL/Ovu6U8=`，放在 `/tmp/caddy-bin/caddy`。候选 `compose.yaml` 第 11 行钉的是镜像 `caddy@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e`。本次没有跑那个镜像，也没有 Docker。
- 时刻用 America/Los_Angeles（PST, UTC-8）

## 版本核对

通过，可以继续审这个 tree。没有用别的提交代替。

| 项 | 预期 | 实际 |
| --- | --- | --- |
| PR base | `mv-9-emby-public` | `mv-9-emby-public`，SHA `c792c3fe3312f77c7022aa7f5fcd30f419dbab44` |
| 远程 head | `9d6c23fd733b447ff708fcf974cf3687cbb19b62` | 相同。`refs/pull/7/head` 与 GitHub pull 对象的 head.sha 相同 |
| tree | `566ba6edc24ee1631fd35f84e9fc18418dc0cd86` | `git rev-parse HEAD^{tree}` 相同 |

命令与时刻：

1. 2026-11-19T06:39:10-0800，`git -C /workspace fetch origin 9d6c23fd733b447ff708fcf974cf3687cbb19b62 mv-9-emby-public refs/pull/7/head:refs/remotes/origin/pr-7-head`。退出码 0。随后 `git rev-parse 9d6c23fd…` 与 `git rev-parse 9d6c23fd…^{tree}` 分别是上面的 head 和 tree。`merge-base` 是 `c792c3fe…`，即 `origin/mv-9-emby-public`。
2. 2026-11-19T06:39:46-0800，`git -C /workspace worktree add --detach /tmp/movie-pr7 9d6c23fd733b447ff708fcf974cf3687cbb19b62`。退出码 0。在该目录执行 `git rev-parse HEAD` 与 `git rev-parse 'HEAD^{tree}'`，结果与上表相同。`git rev-parse --abbrev-ref HEAD` 为 `HEAD`（detached）。`git status --porcelain` 为空。
3. 2026-11-19T06:45:29-0800 再执行一次 `rev-parse`，HEAD 与 tree 未变，porcelain 仍为空。

`/workspace` 本身仍在 `main`，HEAD `23d9c08eff2b8facfa8b4784ad47b47ff9c7595c`，porcelain 为空。审查用的是 detached worktree，不是作者分支。

相对 base 有 3 个提交：`f9d3f61`、`79e34db`（tree `5c6b8f1e19fcc372f6f3c17a203e510ca029ef2b`）、`9d6c23f`（本 head）。本 head 把 `scripts/verify_a.py`、`scripts/verify_movie3.py`、`docs/VERIFY-MV-9b.md` 放进树。parent 是 `79e34db`。

## 总览

| 项 | 状态 |
| --- | --- |
| head / tree / base 一致 | passed |
| 空 token：网关拒绝且不上游 | failed |
| `verify_a.py` 空 token 仍判成功 | failed（假通过已复现） |
| `verify_movie3.py` 伪造 token 的含内部地址 200 仍判成功 | failed（假通过已复现） |
| 无 token、空查询参数、Cookie、伪造但非空的 `X-Emby-Token` 头 | 本地桩 passed（见探针）。不能代替 A |
| movie3 `/movie` 与 `/emby` 两前缀 | 本地单测 passed。A blocked |
| 仓库内先前 D1–D6 单测 | 本地单测 passed。A blocked |
| movie2 回归（管理站仍反代、`/ge2o` 404） | 本地桩 passed。A blocked |
| 管理接口 `/discover/api` 403 | 本地桩 passed。A blocked |
| 签名后的 Range 206 | unrun。单测没有断言网关返回 206 |
| WebSocket 101 | unrun。放行用例断言的是桩的 HTTP 200，拒绝用例断言不是 101 |
| A 上的 `verify_a.py` / `verify_movie3.py` | blocked |
| 带凭据的手输 viewer、播放跟随 | unrun |
| 官方 App、Infuse、真实滑条、公网层 | unrun |
| 候选 `unittest` 全绿 | passed（86，首次）。这不是 A，也不是对上面 failed 项的通过 |

## 两个假通过

脚本文件没有改。桩只在 `/tmp`，连 `127.0.0.1` 临时端口。

命令：`python3 /tmp/pr7-falsepass.py`。开始并结束于 2026-11-19T06:44:33-0800。退出码 0。导入的是 `/tmp/movie-pr7/scripts/` 里的原文件。`request()` 的默认端口在定义时绑定了 18000，夹具在调用前把 `port` 传进去，没有改脚本源码。

### `scripts/verify_a.py`：空 token 仍判成功 — failed

`check()` 第 241–244 行：`expect == "allowed"` 时，只要分类不是网关 403/404，`ok` 保持真。它不看 token 是否为空，也不看正文里有没有内部地址。

反例使用与第 412 行相同的名字和 `expect="allowed"`，头是空值：`X-Emby-Token` 为空串，`Authorization` 为 `MediaBrowser Client="EmbyWeb", Token=`。桩返回 200，正文含 `LocalAddress` 与 `WanAddress`。

- 预期：空 token 拿到含内部地址的 200 时，断言应为失败
- 实际：`ok=true`，`status=200`，`reasons=[]`
- 请求行：`GET /emby/System/Info HTTP/1.1`（`wall_path` 把 `/System/Info` 改写成 `/emby/System/Info`）
- 发出的头：`x_emby_token` 为 `""`，`authorization` 以 `Token=` 结尾、后面没有值

同一轮对照：第 415 行那个现成用例 `System/Info empty api_key`（`expect="gateway-403"`）在同样的 200 泄漏上 `ok=false`，原因是 `want gateway 403, got 200`。所以**空查询参数这条用例能抓住 HTTP 200**。假通过出在第 241–244 行的成功断言，以及下面的网关行为：空的 `Token=` 根本不会走到这条 403 用例。

第 415 行还有另一个假通过。桩返回 403，正文是 `Forbidden` 加上 `LocalAddress=…`。`classify()` 第 214–215 行只看正文是否以 `Forbidden` 开头，于是 `ok=true`，`reasons=[]`。内部地址跟在 `Forbidden` 后面时，这条用例仍判成功。

### `scripts/verify_movie3.py`：伪造 token 的 200 仍判成功 — failed

第 511–512 行就是仓库里的调用：`check("allow System/Info token /movie", "allowed", …, headers=ADMIN)`。`ADMIN`（第 14–18 行）是占位串 `fake-admin-token-for-gateway-test`，不是空串，也不是有效会话。桩返回 200，正文为 `{"LocalAddress":"10.0.0.8","WanAddress":"203.0.113.9","Id":"srv"}`。

- 预期：伪造 token 得到含内部地址的 200 时应失败
- 实际：`ok=true`，`status=200`，`cls=upstream-or-other`，`reasons=[]`
- `/emby/System/Info` 用同一 `ADMIN` 再测一次，同样 `ok=true`

第 207–210 行与 `verify_a.py` 的 `allowed` 分支相同：不检查正文。影响是，网关只要把伪造 token 转给上游并带回完整 `System/Info`，这条复验仍算通过。

## 网关探针（本地 Caddy，不是 A）

命令：`CADDY=/tmp/caddy-bin/caddy python3 /tmp/pr7-caddy-probe.py`。2026-11-19T06:44:48-0800 至 06:44:49-0800。退出码 0。探针用候选 `gateway/Caddyfile` 起本地进程和记录桩，没有改仓库。上游被打到记为 `hits=1`。

| 请求 | 状态 | hits | 正文摘录 |
| --- | --- | --- | --- |
| `/movie/System/Info`、`/emby/System/Info` 无 token | 403 | 0 | `Forbidden` |
| 空 `api_key=`、空 `X-Emby-Token=` 查询、空 `X-Emby-Token` 头 | 403 | 0 | `Forbidden` |
| `X-Emby-Token: `（一个空格）`/movie/System/Info` | 403 | 0 | `Forbidden` |
| `Authorization: MediaBrowser Client="EmbyWeb", Token=`（等号后没有值），`/movie` 与 `/emby` | **200** | **1** | `UPSTREAM:GET:…/System/Info` |
| `X-Emby-Token: forged-not-a-session` | 200 | 1 | 转到桩。网关不校验令牌是否为会话 |
| `/movie/System/Info/Public` | 200 | 1 | 匿名 Public 仍到桩 |
| `/discover/api/admin-check` | 403 | 0 | `Forbidden` |
| 管理站端口 `/Plugins` | 200 | 1 | 仍到桩，没有套用电影墙白名单 |
| 管理站端口 `/ge2o` | 404 | 0 | `Not found` |

空 `Token=` 的 200 是首次探针结果，没有重试。对应 `gateway/Caddyfile` 第 203 行：

`{http.request.header.Authorization}.matches('(?i)Token=')`

`Token=` 不要求等号后面有非空值。查询参数正则是 `=[^&]+`，所以空 `api_key=` 匹配不上，探针里也是 403。影响：只要 `Authorization` 或 `X-Emby-Authorization` 里出现 `Token=`，即使值为空，完整 `/System/Info` 也会反代，上游正文会回到客户端。`tests/test_gateway.py` 第 938–945 行的“无有效 token”用例没有这个头，所以 86 项单测全绿并没有覆盖它。

## 本地单测

命令：在 `/tmp/movie-pr7` 执行 `CADDY=/tmp/caddy-bin/caddy python3 -m unittest tests.test_gateway tests.test_movie3_gateway`。2026-11-19T06:44:48-0800 至 06:45:10-0800。退出码 0。首次即过，没有失败，没有重试。

摘录：`Ran 86 tests in 21.742s`，`OK`。

这些单测用本地 HTTPS 桩，覆盖了候选里写到的认证路径（无 token、空查询、Cookie、Bearer 不含 `Token=`、夹具 token）、`/movie` 与 `/emby`、管理页 404、`/discover/api` 403、movie2 仍反代，以及仓库里命名的 D1–D6（双斜杠与 WebSocket 的缓存、写方法、OpenList 签名、双重编码、3xx 去掉 movie2 绝对地址）。**不能把这 86 项写成 A 现场通过。** 它们也没有覆盖空 `Token=`。

Node：`node --test tests/watch-handoff.test.cjs tests/search-entry.test.cjs tests/task-controls.test.cjs`。2026-11-19T06:44:49-0800。退出码 0。摘录：`# pass 3`、`# fail 0`。与 A 无关。

`caddy validate` 含在上述 86 项的 `test_caddyfile_validates_with_official_binary` 里，随套件退出码 0。用的是 v2.10.2，不是 compose 镜像摘要。

## 文档和断言

`docs/VERIFY-MV-9b.md` 第 1–3 行把两个脚本写成复验已部署网关，并判断旧 emby 主机名是否已停止提供服务。第 33–41 行写隧道文件由模板三处派生。文档没有写：空 `Token=` 必须失败，伪造 token 若得到含 `LocalAddress` 的 200 必须失败。

实际断言：

- 成功分支不看 token 空不空，也不看内部地址（`verify_a.py` 第 241–244 行，`verify_movie3.py` 第 207–210 行）
- 无 token 的专门循环（`verify_a.py` 第 563–577 行）要求网关 403，并拒绝 `LocalAddress` 等四个词。它不发送 `Authorization: Token=`，因此抓不住第 203 行那种空 `Token=` 反代
- 旧主机负向（约第 583–627 行）在空正文 HTTP 200 上仍算“未在提供”。2026-11-19T06:46:02-0800，用脚本自己的 `request()` 打到返回空 200 的本地桩，按同一判定：`status=200`，`serving=False`，判定为通过。这只说明响应不像脚本所认的 Emby 正文，不能写成拒绝连接或公网已关闭。A 上的现场探测是 blocked

第 19 行写无 stdin 时用户路径会跳过或失败。代码把该条 `ok` 设为假，整份退出码会是 1。本次没有提供凭据，用户路径 **unrun**。

## 签名 206 与 WebSocket 101

- `tests/test_gateway.py` 第 1325–1332 行对带 `sign=test-sign` 的 `/openlist/d/…` 断言状态 **200** 且正文来自桩，不是 206。另一套桩在 `sign=good` 时会回 206（约第 1470–1473 行），没有用例断言客户端最终收到 206。真实播放器的 Range 206：**unrun**。A：**blocked**。
- `tests/test_movie3_gateway.py` 第 362–372 行对 `/embywebsocket`、`/movie/embywebsocket`、`/emby/embywebsocket` 断言状态 **200** 且正文以 `UPSTREAM:` 开头。第 1032–1047 行断言其它路径不是 101。没有观察到 101。真实 WebSocket 升级：**unrun**。A：**blocked**。

## 缺陷

1. **高。** `gateway/Caddyfile` 第 203 行。空 `Token=` 反代到上游，`/movie` 与 `/emby` 都是 200 且 `hits=1`。影响：完整 `System/Info` 在“有 `Token=` 但没有值”时离开网关。单测第 938–945 行没覆盖。未修。
2. **高。** `scripts/verify_a.py` 第 241–244 行。空 token 头加上含内部地址的 200，`ok=true`。影响：复验脚本会把这次放行判成通过。未修。
3. **高。** `scripts/verify_movie3.py` 第 207–210 行与第 511–512 行。伪造 token 的含 `LocalAddress`/`WanAddress` 的 200，两个前缀都是 `ok=true`。未修。
4. **中。** `scripts/verify_a.py` 第 210–215 行与第 415 行。403 正文以 `Forbidden` 开头时，后面的 `LocalAddress` 仍使 `System/Info empty api_key` 为 `ok=true`。
5. **中。** `scripts/verify_a.py` 旧主机判定。空 200 判为未在提供服务。不能据此声称连接被拒绝或公网已关闭。`docs/VERIFY-MV-9b.md` 第 3 行的“是否已停止提供服务”比断言更强。
6. **低。** 放行的 WebSocket 单测接受普通 HTTP 200；签名直链单测接受 200 而不是 206。真实 101 和真实滑条/206 仍未测。

## 未覆盖

- 官方 App
- Infuse
- 真实滑条
- 公网层（DNS、Cloudflare、从公网来的缓存和规则）
- 测试机 A 上的两个 verify 脚本、inode、活 Caddy
- 带凭据的手输登录、电影墙、PlaybackInfo、跟随 307 后的 206
- 真实 WebSocket 101
- compose 镜像那一枚 Caddy 摘要上的行为（本次是 v2.10.2 发布包）

A 不能从本环境到达，相关项保持 blocked，没有用 86 项单测代替。
