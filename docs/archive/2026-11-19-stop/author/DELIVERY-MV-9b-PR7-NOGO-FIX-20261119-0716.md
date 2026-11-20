# MV-9b PR7 no-go 修复交接

- 项目：movie（既有 Project `bc-858ea07c-4801-5290-bd8e-72ada2a25bc9`）
- 任务：BRIEF-MV-9b-PR7-NOGO-FIX-20261119-2303.md；Draft PR #7 分支 `mv-9b-movie3`
- 阶段：作者已在隔离工作范围完成修复、本地闭环验证与单一补丁；未推送、未开新 PR、未改 PR #5/#6、未部署、未接触 A/现场/DNS/隧道/Access/凭据
- 下一责任人：parent 将本补丁作为真实附件交给上述唯一既有 Project，由其更新同一 Draft PR #7 分支 `mv-9b-movie3`。独立 QA 复验。作者通过不是独立 QA。
- 生成方式：grok 程序员（原生 Grok）实现；未调用 Codex；未请技术顾问（本轮 PM 明确由原写者以原生 Grok 续修，不调用 Codex）

所有时间均为 America/Los_Angeles（PT，当时 UTC-8），除非另标时区。

## 目标、范围与验收

授权票要求：完整 `GET/HEAD /System/Info`（非 `/Public`）在 `/movie` 与 `/emby` 上，`Authorization` / `X-Emby-Authorization` 仅带空 `Token=` 时，网关必须 403 且上游 hits=0。网关不验证真实会话，非空伪 token 仍交上游。`verify_a` 的 `allowed` 分支对空 token 拿到含内部地址的 200 不得 PASS，403 `Forbidden` 后仍带 `LocalAddress` 等也不得 PASS，并新增空 `Authorization Token=` 负向用例。`verify_movie3` 对伪 token（含 ADMIN 占位）拿到含 `LocalAddress`/`WanAddress` 的 200 不得 PASS。旧主机空 200 不可表述为已停服/拒绝连接。验收需本地真实网关探针、脚本真实失败反例和既有回归，不只静态字符串断言。

本轮只做本地/桩闭环。官方 App、Infuse、真实滑条、Range 206、WebSocket 101、公网与测试机 A 均保持未测。

## repo / 候选绑定

- 授权远程 PR #7 head：`9d6c23fd733b447ff708fcf974cf3687cbb19b62`，tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`
- 授权远程 base `mv-9-emby-public`：`c792c3fe3312f77c7022aa7f5fcd30f419dbab44`
- 远程 commit `9d6c23fd` 与 base `c792c3fe` 本地均不存在；本轮未 fetch、未 clone。不能声称本地拥有这两个 commit 对象。
- 起点是与授权远程 tree 精确一致的已有对象：tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`，本地残留提交 `686b92a2ed1ac516b6624ab5733a91f2c4639a64`（Task B 制作 0003 时的一次性提交，父提交 `0c877f86`）。隔离工作树 `/workspace/movie/mv9b/repo-pr7-nogo` 分支 `mv-9b-pr7-nogo-fix` 从此 tree 起步。
- 新本地 head：`3baab01849f4350f55081fedc709705202c83695`
- 新本地 tree：`f1617efc5a0645fdd1a9791024c565950e880ad9`
- 父提交：`686b92a2ed1ac516b6624ab5733a91f2c4639a64`（tree `566ba6ed…`）
- 作者：grok 程序员 `<grok-programmer@local>`；提交时间 2026-11-19 07:16:21 AM PT（git 记录 2026-11-19 23:16:21 +0800）
- 旧仓库 `/workspace/movie/mv9/repo` 保持原样：分支 `mv-9b-movie3`，HEAD `0c877f86a8b0529c2b41cd209b82f8c27fca3260`，tree `5c6b8f1e19fcc372f6f3c17a203e510ca029ef2b`，工作区干净。旧证据与 QA 候选三元绑定未改动。
- 补丁按 tree `566ba6ed` 制作，可应用到远程 head `9d6c23fd`（同 tree）。已在该 tree 的归档副本上 `git apply --check` 通过，并在新 HEAD 上 `git apply --reverse --check` 通过。

## 独立评审报告

- 任务票第 11 行路径 `/cursor/stores/user/grok-bot/agents/4fff1be1-b61a-47ef-8045-b7dba1742b53/assets/01M7T4D3XYQV35Z42JW0P622P4.md`：不可读（目录不存在）。已标记，未再探其他私聊。
- 明确交接的第二份报告 `/cursor/stores/bc-858ea07c-4801-5290-bd8e-72ada2a25bc9/docs/mv9b-pr7-review.md`：已读。QA no-go 要点与本修复一一对应。

## 关键改动

1. `gateway/Caddyfile`：完整 System/Info 的 header 匹配从 `(?i)Token=` 改为要求非空值 `(?i)Token="?[^,& "]+"?`（Caddyfile 中转义为 `Token=\"?[^,& \"]+\"?`）。空 `Token=` / 空查询仍不放行；非空 token 仍转发，网关不验证会话。
2. `scripts/verify_a.py` / `scripts/verify_movie3.py`：`classify()` 对 403 但正文含 `LocalAddress`/`WanAddress`/`ProgramDataPath` 不再判为干净的 `gateway-403`。`allowed` 在空 token 或占位伪 token（含 `ADMIN`、`fake-admin-token-for-gateway-test`）拿到这些内部地址时不得 PASS。新增空 `Authorization Token=`（verify_a 另增空 `X-Emby-Authorization Token=`；verify_movie3 在两前缀上增空 Authorization 负向用例）。
3. 旧主机探测名称/expect/文档不再把空 200 写成已停服或拒绝连接；改为“不是 Emby 公开载荷”。`docs/VERIFY-MV-9b.md`、`docs/SETUP.md` 同步说明空 `Token=` 与非空伪 token 行为。
4. 测试：`tests/test_gateway.py`、`tests/test_movie3_gateway.py` 增加真实 Caddy+桩用例（403 且上游 hits=0）；新建 `tests/test_verify_scripts.py` 用本地 HTTP 桩复现脚本假通过反例。

## 证据（命令、环境、时间、结果）

环境：工作目录 `/workspace/movie/mv9b/repo-pr7-nogo`；`CADDY=/workspace/movie/mv9/tools/caddy`（v2.11.7）；Python 3.13.5。未使用凭据，未连 A。

| 步骤 | 日志 | 时间 (PT) | 退出码 | 结果 |
|---|---|---|---|---|
| 首次 RED（脚本桩未注入 port，5 个 ERROR + 网关 200 失败） | `logs/40-nogo-red-20261119T071040-0800.txt` sha256 `cf66f433d16b397091bc9a96850c66461d1161cf3ba105325a516f4ec7536c58` | 2026-11-19 07:10:40 PT | 1 | 6 tests，failures=13，errors=5。网关空 Token= 为 200 且 body 为 `UPSTREAM:…`（已打到桩）。脚本测试因 `request()` 默认 port 绑定 18000 而 Connection refused。已修测试夹具，不改生产代码。 |
| RED 重试（正确失败原因） | `logs/41-nogo-red-retry-20261119T071111-0800.txt` sha256 `83a49312d1fbcade3f4c8a8990bd1762b0253fe8defa0e7725db4e026923e51c` | 2026-11-19 07:11:11 PT | 1 | 6 tests，18 failures，0 errors。空 Token= → 200 `UPSTREAM`；`verify_a` 空 token 200+LocalAddress ok=True；403 Forbidden+LocalAddress ok=True；文档含“已停止提供服务”；`verify_movie3` ADMIN 占位 200+内部地址 ok=True。 |
| 首次 GREEN | `logs/42-nogo-green-20261119T071358-0800.txt` sha256 `d0d7ca9be40b91f3c54f83091c136aff9fccbdc9f8a0eaf650ad543458cdbdc1` | 2026-11-19 07:13:58 PT | 1 | 网关与脚本反例已绿；文档禁止句仍含“拒绝连接”子串。已改表述后重跑。 |
| GREEN 重试 | `logs/43-nogo-green-retry-20261119T071410-0800.txt` sha256 `88b86eaa38af93e764b2dc8c7337301caed527a0ad1319b11c978ea429eaf069` | 2026-11-19 07:14:10 PT | 0 | 12 tests OK（含相关 System/Info 回归：无 token、无效 token、非空 token 到达上游、匿名 Public）。 |
| 全量 unittest | `logs/44-nogo-full-unittest-20261119T071418-0800.txt` sha256 `5d4c39441328a26bed01dd4adf12573e9c051dcf44072ec4ee3ed47f4f0eb95a` | 2026-11-19 07:14:18 PT | 0 | `python3 -m unittest discover -s tests -v`：Ran 239 tests in 41.161s，OK (skipped=1)。 |
| 独立本地网关探针 | `logs/45-nogo-local-gateway-probe-20261119T071522-0800.txt` sha256 `f5a4032aa82dae1abc3b5b4658e2ec52f5adf22f2493936f37989bac29ecf677` | 2026-11-19 07:15:22–07:15:23 PT | 0 | `PROBE_ALL_OK True`。`/movie` 与 `/emby` 的空 `Authorization Token=` 与空 `X-Emby-Authorization Token=`：status=403，body=`Forbidden`，upstream hit_count=0。非空伪 token 与匿名 `/System/Info/Public`：status=200，hit_count=1。 |
| Caddy validate | `logs/46-caddy-validate-20261119T071614-0800.txt` sha256 `4f004cbecfc31e4b275d33401dd1d7b3289f0de6dc087a8d68a521669bef3927` | 2026-11-19 07:16:14 PT | 0 | `MOVIE_DOMAIN=movie.example.com MOVIE3_DOMAIN=movie3.example.com` 下 `Valid configuration`。仍有既有 fmt 警告（line 2），未运行 `caddy fmt --overwrite`。无环境变量时对未替换模板 validate 会在 line 8 报 `unrecognized global option: encode`，属既有现象，不是本补丁引入。 |

## 补丁与产品文件

- 单一补丁绝对路径：`/workspace/movie/mv9b/patches/0004-MV-9b-PR7-nogo-empty-Token-System-Info.patch`
- 补丁 sha256：`b10329dd3f73b5123e4e0b410ffb9a2a75f9f4be78f3b55673839a64c578f77a`
- 建议用法（由 Project 在 PR #7 分支 `mv-9b-movie3`、当前远程 head `9d6c23fd` 上应用，不改 PR #5/#6）：`git am` 或 `git apply` 该补丁。

提交后文件 sha256：

| 仓库路径 | sha256 |
|---|---|
| gateway/Caddyfile | `67e0746ede2049d20812ab58ae7c64c19c6e1e7efe5b80fc37b495a0ca6e149e` |
| docs/SETUP.md | `adac817911ec03456e58d12714fac3eef7d911611f8edd900ff872e9808b3b5f` |
| docs/VERIFY-MV-9b.md | `39ed83517af4145df924e265231d33e0166bddee4faf695751bdd0ec1f30bd7a` |
| scripts/verify_a.py | `f4545f0a6810b96867ed1c6034ba96e4dfd80ffdc43833880ed4c82d1deab0d6` |
| scripts/verify_movie3.py | `9da487c310b0aa0987a11f7047134afa0e31af4fb54a7eb64f76b7cf2d970c1e` |
| tests/test_gateway.py | `76323671cc5b191223efd25eff74fbdfc8f76195c8e36a9f679d2a82f2c70ab8` |
| tests/test_movie3_gateway.py | `b214642e43d4e96579df157af8928b5f896391cf81e197102d29fc47926faa31` |
| tests/test_verify_scripts.py | `71a1d754348071a4122806704a00012b63e107c0ef1e134e4056d93f474fda99` |

补丁不含秘密、用户名口令、敏感 env 或机器管理凭据。日志与本交付为证据，不必纳入产品 PR。

## 作者自审（不是独立 QA）

已按 requesting-code-review 做作者对照检查，但未另派独立审查子代理；本自审不能替代 QA。

- 根因与修复对齐：空 `Token=` 曾被 `.matches('(?i)Token=')` 放行；现要求非空值。本地 Caddy v2.11.7 + 桩已实测 403/0 hits，非空伪 token 仍 200/1 hit。
- 脚本断言从“网关未 403 即通过”改为对空/占位 token 泄露内部地址失败关闭；`classify` 不再把 `Forbidden`+内部地址当成干净网关 403。
- 注意：应用本补丁后，若测试机 A 上真实 Emby 对 ADMIN/占位伪 token 仍返回带 `LocalAddress`/`WanAddress` 的 200，则既有用例 `allow System/Info token` / `System/Info with token header` 将由脚本记失败。这是票面要求，不是网关开始验证会话。真实登录会话拿到这些字段仍可通过；上游因无效 token 返回不含内部地址的 401 时，`allowed` 仍表示“网关已放行”。
- 两脚本各有一份相同辅助函数，保持彼此独立、无新共享模块。
- 未改响应正文，未做会话校验，未接触现场配置。

## 缺陷、未测与风险

- 未测：官方 App、Infuse、真实滑条、Range 206、WebSocket 101、公网、测试机 A、DNS/隧道/Access。本轮明确只做本地/桩闭环。
- 远程 commit `9d6c23fd` 与 base `c792c3fe` 未在本地出现；一致性只核到 tree `566ba6ed`。若 Project 侧该 tree 与声明不符，应先停止应用并回报。
- A 上活 Caddy 仍是旧配置，本轮未部署。QA 需在应用补丁并按既有三规则派生/部署后再在 A 上复验。
- 既有 `caddy fmt` 警告（line 2）未处理。
- 占位 token 集合是精确匹配（`ADMIN` / `admin` / `fake-admin-token-for-gateway-test` / 空）；不会把真实会话 token 误判为占位。

## 授权边界与下一步

- 不 clone、不 push、不开新 PR、不合并、不部署、不接触 A/DNS/隧道/Access/凭据。作者不自行联系 Project。
- 下一步完成判据：Project 将本补丁应用到 Draft PR #7 的 `mv-9b-movie3`（head `9d6c23fd`）；独立 QA 在新 head 上重跑本地网关/脚本用例，并在授权范围内于 A 复验。新 head 必须重验，不能沿用旧候选 `0c877f86` / tree `5c6b8f1e` 的 A 证据。
- 建议技能：software-release-gate（独立验收）；verification-before-completion。
