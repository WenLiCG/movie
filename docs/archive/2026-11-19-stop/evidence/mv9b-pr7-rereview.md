---
cursor:
  subagentId: "bc-a983a9cd-2d74-5f11-8793-411056b13f10"
---

# PR #7 新候选只读复验（software-release-gate v1.0）

这是质量建议，不是合并批准。没有沿用 `docs/mv9b-pr7-review.md` 对旧 head 的结论来给新 head 下判断。作者自测不算通过。没有改代码，没有改分支，没有评论、approve 或合并，没有部署，没有访问 A，没有读生产凭据。这轮没有完全收敛，不开始第三轮修复。

**一句话：** 旧 head 上的两个假通过这次又复现了，首轮 no-go 仍成立；新 head 上那两个具体假通过不再成立，空 `Token=` 在两个前缀上都是网关 403 且上游 0 次命中，但占位表以外的伪 token 若带回内部地址，`verify_movie3.py` 仍判通过。

## 环境

- Linux 6.12.113+ x86_64，Python 3.12.3
- 本地 Caddy v2.10.2，`h1:g/gTYjGMD0dec+UgMw8SnfmJ3I9+M2TdvoRL/Ovu6U8=`，路径 `/tmp/caddy-bin/caddy`
- 候选 `compose.yaml` 仍钉镜像 `caddy@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e`。本次没有跑那个镜像，也没有 Docker
- 时刻为 America/Los_Angeles（PST, UTC-8）
- 审查线程 `bc-a983a9cd-2d74-5f11-8793-411056b13f10`
- `/workspace` 仍在 `main`，HEAD `23d9c08eff2b8facfa8b4784ad47b47ff9c7595c`，porcelain 为空。两个 detached worktree 用完后已删除

## 版本核对

通过，随后的测量都在这两个 tree 上。没有用别的提交代替。

| 项 | 预期 | 实际 |
| --- | --- | --- |
| 分支 | `mv-9b-movie3` | `origin/mv-9b-movie3` 指向下面的 head |
| head | `0aabaaede8ba37b70607416a6fde53a4c2992ce1` | 相同。`refs/pull/7/head` 与 GitHub pull 对象 head.sha 相同。Draft，base 名是 `mv-9-emby-public` |
| tree | `f1617efc5a0645fdd1a9791024c565950e880ad9` | `git rev-parse HEAD^{tree}` 相同 |
| base | `mv-9-emby-public` | `c792c3fe3312f77c7022aa7f5fcd30f419dbab44`。`merge-base` 也是这个 SHA |
| 父提交 | 旧远程 head | `9d6c23fd733b447ff708fcf974cf3687cbb19b62`，其 tree 仍是 `566ba6edc24ee1631fd35f84e9fc18418dc0cd86` |

2026-11-19T07:34:50-0800：`git -C /workspace fetch origin mv-9b-movie3 mv-9-emby-public refs/pull/7/head:refs/remotes/origin/pr-7-head`。退出码 0。摘录：`9d6c23f..0aabaae  refs/pull/7/head`。随后 `rev-parse` 得到上表。

2026-11-19T07:34:57-0800：

```text
git worktree add --detach /tmp/movie-pr7-old 9d6c23fd733b447ff708fcf974cf3687cbb19b62
git worktree add --detach /tmp/movie-pr7-new 0aabaaede8ba37b70607416a6fde53a4c2992ce1
```

两条退出码都是 0。旧目录 `HEAD` / `HEAD^{tree}` 为 `9d6c23fd…` / `566ba6ed…`。新目录为 `0aabaae…` / `f1617efc…`。测完再次 `rev-parse`，新 head 与 tree 未变，两个 worktree 的 porcelain 都为空。

新提交相对旧 head 改了 8 个文件：`docs/SETUP.md`、`docs/VERIFY-MV-9b.md`、`gateway/Caddyfile`、两个 verify 脚本、`tests/test_gateway.py`、`tests/test_movie3_gateway.py`，并新增 `tests/test_verify_scripts.py`。

## 总览

| 项 | 状态 |
| --- | --- |
| 新 head / tree / base | passed |
| 旧 head 上先前两个假通过是否仍成立 | failed（假通过仍在，首轮 no-go 保留） |
| 新 head：两个前缀上空 `Authorization` / `X-Emby-Authorization` 的 `Token=` | passed（403，上游 0 hits） |
| 新 head：非空伪 token 仍转上游、没有会话校验 | passed |
| 新 head：`verify_a.py` 拒绝空 token 泄漏的 200 | passed |
| 新 head：`verify_a.py` 识别 `Forbidden` 正文里的 `LocalAddress` | passed |
| 新 head：`verify_movie3.py` 拒绝上次那个 ADMIN 占位伪 token 的泄漏 200 | passed |
| 新 head：`verify_movie3.py` 拒绝占位表以外的伪 token 泄漏 200 | failed |
| 旧 Host 空 200 被写成服务已关闭 | passed（新脚本文案没有这么写；空 200 仍算“不像 Emby 载荷”） |
| 仅含 `OperatingSystem` 的 403 正文 | failed |
| 新 head 本地 unittest | passed（92，首次）。不是 A |
| A 现场 | blocked |
| 真实签名 Range 206、WebSocket 101、官方 App、Infuse、滑条、公网层 | unrun |

没有失败后的重试。下面每条都是首次结果。

## 1. 旧 head 上先前失败反例仍然成立

命令：`python3 /tmp/pr7-rereview-run.py OLD /tmp/movie-pr7-old`，随后 `python3 /tmp/pr7-caddy-both.py /tmp/movie-pr7-old`。脚本时刻 2026-11-19T07:35:58-0800，退出码 0。Caddy 探针 2026-11-19T07:36:12-0800，退出码 0。导入的是旧 tree 的原文件，没有改脚本。

预期：空 token 的含内部地址 200 仍被 `verify_a.py` 判通过；`Forbidden` 后带 `LocalAddress` 仍被判通过；ADMIN 占位伪 token 的含内部地址 200 在两个前缀上仍被 `verify_movie3.py` 判通过；空 `Token=` 仍被旧 Caddy 转到上游。

实际：

- `verify_a` 空 `X-Emby-Token` 且 `Authorization` 以 `Token=` 结尾，桩返回含 `LocalAddress` 的 200：`ok=true`，`reasons=[]`，请求行 `GET /emby/System/Info`
- `verify_a` 用例 `System/Info empty api_key`，桩返回 `Forbidden\nLocalAddress=10.0.0.8`：`ok=true`，分类 `gateway-403`
- `verify_movie3` 的 `allow System/Info token /movie` 与 `/emby`，头是 `fake-admin-token-for-gateway-test`，桩返回含 `LocalAddress` 与 `WanAddress` 的 200：两条都是 `ok=true`
- 旧 Caddy，`/movie/System/Info` 与 `/emby/System/Info`，空 `Authorization` 或空 `X-Emby-Authorization` 的 `Token=`：状态 200，`hits=1`，正文 `UPSTREAM:GET:…`

首轮 no-go 因此保留。这是这次重新打出来的结果，不是引用作者自测数字。

## 2. 新 head：空 Token= 必须 403 且上游 0 hits

命令：`python3 /tmp/pr7-caddy-both.py /tmp/movie-pr7-new`。2026-11-19T07:36:13-0800 至 07:36:14-0800。退出码 0。用的是新 tree 的 `gateway/Caddyfile` 和第 204 行新表达式 `Token="?[^,& \"]+\"?`。

预期：两个前缀上，空 `Authorization` 与空 `X-Emby-Authorization` 的 `Token=` 为网关 403，记录桩命中 0。非空伪 token 仍到桩，状态 200。

实际：

| 请求 | 状态 | hits | 正文 |
| --- | --- | --- | --- |
| `/movie/System/Info`、`/emby/System/Info` 无 token | 403 | 0 | `Forbidden` |
| 两前缀，`Authorization: … Token=` | 403 | 0 | `Forbidden` |
| 两前缀，`X-Emby-Authorization: … Token=` | 403 | 0 | `Forbidden` |
| `/movie/System/Info`，`Token=""` | 403 | 0 | `Forbidden` |
| `/movie` 的 `X-Emby-Token: forged-not-a-session` | 200 | 1 | `UPSTREAM:GET:/System/Info` |
| `/emby` 的 `Authorization: … Token=forged-not-a-session` | 200 | 1 | `UPSTREAM:GET:/emby/System/Info` |
| `/movie/System/Info/Public` 无 token | 200 | 1 | 匿名 Public 仍到桩 |

补测 HEAD，2026-11-19T07:37:07-0800，退出码 0：`HEAD /movie/System/Info` 空 `Authorization Token=`，以及 `HEAD /emby/System/Info` 空 `X-Emby-Authorization Token=`，都是状态 403、`hits=0`、正文空。

状态：**passed**。没有新增会话校验：非空伪 token 仍原样转给桩。

## 3. 新 head：`verify_a.py` 拒绝空 token 泄漏，并认出 Forbidden 里的内部地址

同一条 `python3 /tmp/pr7-rereview-run.py NEW /tmp/movie-pr7-new`，2026-11-19T07:35:58-0800，退出码 0。文件是新 tree 的 `scripts/verify_a.py`，没有改源码。

预期：空 token 加上含 `LocalAddress` 的 200 时 `ok` 为假；`Forbidden` 正文后仍有 `LocalAddress` 时 `ok` 为假。

实际：

- 空 token、`expect=allowed`、200、正文含 `LocalAddress`：`ok=false`。原因：`empty or placeholder token must not receive internal addresses (status=200)`。判定在第 303–307 行
- 用例 `System/Info empty api_key`，403 正文 `Forbidden\nLocalAddress=10.0.0.8`：`ok=false`，分类变成 `upstream-or-other`。原因：`gateway 403 body leaked internal addresses`。判定在第 266–270 行与第 287–290 行

状态：**passed**。

## 4. 新 head：`verify_movie3.py` 与伪 token 泄漏

同一命令、同一时刻。预期按本轮要求：伪 token 得到含内部地址的 200 时不得判通过。

实际分两截：

- 上次那个占位串 `fake-admin-token-for-gateway-test`（`scripts/verify_movie3.py` 第 16 行的 `ADMIN`）打 `/movie/System/Info` 和 `/emby/System/Info`：两条都是 `ok=false`，原因同样是 placeholder 不得收到内部地址。第 268–272 行，占位表在第 177–182 行。**上次这个假通过不再成立。**
- 不在占位表里的 `X-Emby-Token: forged-not-a-session`，同样的 200 和 `LocalAddress`：`ok=true`，`reasons=[]`。脚本仍把这次泄漏判成通过

状态：占位串这一条 **passed**；“任意伪 token”这一条 **failed**。网关侧按第 200 行的注释本就不应校验会话，探针也确认非空伪 token 仍 `hits=1`。缺口在脚本：它只拒绝空值和四个占位串，不拒绝其它伪 token。

## 5. 旧 Host 空 200 不能写成服务已关闭

`docs/VERIFY-MV-9b.md` 第 3 行写明：空 200 只说明响应不是 Emby 公开载荷，不能推断主机已关闭或网络未接通。第 45 行重申空 token 与占位伪 token 的断言，并写明网关不验证真实会话。

2026-11-19T07:36:27-0800，对新 `scripts/verify_a.py` 跑了一次 `main()`。旧主机桩固定回空正文 200，其它请求回 `Forbidden`，所以整份退出码是 1、`passed=116 failed=77`。那是桩太粗，不是旧主机用例失败。六条旧主机用例本身都是：

- 名字：`legacy [redacted-legacy-host] not an Emby public payload …`
- `expect`：`not an Emby public payload (empty 200 is not stopped or refused)`
- `ok=true`，`status=200`，`body_head=status=200 emby_payload=False`，`reasons=[]`

状态：**passed**。空 200 被记成“不是 Emby 公开载荷”，没有写成服务已关闭。连接错误仍会让 `ok` 为真（第 704 行），那只说明探针没拿到 Emby 载荷，同样不是“已关闭”。A 上的真实旧主机没有打，标 **blocked**。

## 其余本地单测

`CADDY=/tmp/caddy-bin/caddy python3 -m unittest tests.test_gateway tests.test_movie3_gateway tests.test_verify_scripts`，工作目录 `/tmp/movie-pr7-new`。2026-11-19T07:36:27-0800 至 07:36:51-0800。退出码 0。首次即过，没有重试。摘录：`Ran 92 tests in 23.853s`，`OK`。这些单测与上面的独立反例一致覆盖了空 `Token=` 和占位串，没有覆盖占位表以外的伪 token。不能代替 A。

## 仍未测

- 测试机 A，以及 A 上的活 Caddy、inode、两个 verify 脚本
- 真实签名 Range 206
- 真实 WebSocket 101
- 官方 App
- Infuse
- 真实滑条
- 公网层

这些都是 **unrun** 或 A 为 **blocked**。没有沿用任何旧的 A 证据。

## 缺陷

1. **中，新 head 仍在。** `scripts/verify_movie3.py` 第 264–272 行，占位表第 177–182 行。`X-Emby-Token: forged-not-a-session` 加上含 `LocalAddress` 的 200，`ok=true`。影响：只要伪 token 不落在那四个字符串里，泄漏仍算通过。网关按设计仍会转发这种请求（`gateway/Caddyfile` 第 199–204 行）。
2. **低，新 head 仍在。** `scripts/verify_a.py` 第 211 行的内部地址标记没有 `OperatingSystem`。2026-11-19T07:37:07-0800，403 正文只有 `Forbidden\nOperatingSystem=linux` 时，`System/Info empty api_key` 的 `ok=true`，分类 `gateway-403`。`LocalAddress` 能被抓住，单独的 `OperatingSystem` 不能。

旧 head 上的空 `Token=` 反代和两个脚本假通过没有在旧 tree 里消失；新提交是在它上面加的修复，旧对象本身仍是当时的 no-go。
