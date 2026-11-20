# 2026-11-19 停止交接归档说明（本线程）

归档动作时间：2026-11-19 16:08 PST（2026-11-20 00:08 UTC）。America/Los_Angeles，UTC−8。

按 software-handoff v1.3 / workflow v1.4 摘要执行，技能文件未在本线程加载。

本文件只记录这次文档归档时的只读核对和必须保留的边界。它不是新的质量复验，不是 QA 通过，不是合并批准，也不是部署或恢复开发的授权。下面凡是来自停止前报告的结论，都标明为历史记录。本线程没有跑测试，没有把历史结果写成当前 QA。

仓库原先没有 `docs/archive/`。本次新建 `docs/archive/2026-11-19-stop/` 存放安全包正文。包内文件按解包字节写入，未改写。

## 目标与非目标

目标：把已核对的停止交接安全包正文放进仓库文档，在根 `HANDOFF.md` 增加 2026-11-19 PST 停止段，并用 Draft PR 指向 `main`。保留两轮 no-go、未测项，以及“未合并、未部署”。

非目标：不恢复开发，不改产品源码，不跑测试，不登录 VPS，不改配置，不合并，不部署，不评论既有 PR，不改产品分支，不 force push，不补传未脱敏原文、现场配置、密钥、日志、HAR、ROLLBACK 恢复步骤、补丁或测试实现。

## 唯一资料

- 安全包：`movie-stop-handoff-20261119.zip`。本线程再算：55560 字节，SHA256 `b35a9b7907e55c13153ae00d5aa48b8829fc8d76bd4a838f6102f18a2ec731ca`。与给定值一致后才解包。
- 包外清单：同目录 `MANIFEST.md`。本线程再算 SHA256 `139ca273745ce5b46164f9cc280e4e4791a1a1bee680cece36a8ecbddb7a32de`。与给定值一致。写入本目录为 `MANIFEST-OUTER.md`，字节未改。
- 包内清单：`MANIFEST.md`，SHA256 `e7e285d609bcca783dfc4229a3e253fb5da24fb23d89fd0fc50b1eafbea8577f`。与包外清单所记的包内哈希一致。
- 解包后逐文件 SHA256 与包内清单一致，见下表。
- 本线程只读 `git fetch` 与 `gh pr list` / `gh` 返回的 PR 引用。没有 checkout 产品分支去改它们。
- 仓库根已有的 `HANDOFF.md` 仍是 2026-11-09 历史交接。本次只在文末追加停止段，不改该日段落的事实。
- 用户停止原话与追问，按本任务书写明、且写明 PM 已核实，本线程没有另行打开私聊复核：用户原话（position 326）“停止开发，完成handoff后更新到GitHub”。用户 2026-11-19 16:01 PST 在 PM 私聊追问 movie，PM 正在统一收尾。
- 作者停止指令之后的停止交接正文：安全包写明在共享电脑现有目录中未找到。本线程没有另找、没有编造。保持未知。
- 作者停止后的工作区状态：未知。本线程没有打开作者工作区。

## 生成方式

安全包内封面、QA 交接、两份证据、作者交付与两份清单，是复制已有正文。本说明由上述正文加上本线程的只读远程核对写成。没有调用未加载的技能文件，没有重跑历史命令，没有补写缺失的测试输出。缺失项写未知。

## 两份清单的差异

包内 `MANIFEST.md` 与包外 `MANIFEST-OUTER.md` 都保留。

差异只有包外多出的“本包 zip”一节：zip 路径、55560 字节、zip SHA256、包内 `MANIFEST.md` SHA256、包内 `HANDOFF.md` SHA256。其余段落相同。包外清单写于打包之后，所以它能记下 zip 与包内清单的哈希；包内清单不能包含它自己的最终哈希。

## 安全包文件（解包后复核）

| 本目录路径 | SHA256 | 字节 |
| --- | --- | --- |
| HANDOFF.md | `a1a96f97f0178dc36422b61bd3ecbdffec044e31b52d8ef1948434f396986a3e` | 7634 |
| MANIFEST.md | `e7e285d609bcca783dfc4229a3e253fb5da24fb23d89fd0fc50b1eafbea8577f` | 4816 |
| MANIFEST-OUTER.md | `139ca273745ce5b46164f9cc280e4e4791a1a1bee680cece36a8ecbddb7a32de` | 5229 |
| QA-HANDOFF-STOP-20261119.md | `04b72284d12b7bbbaa5ba0647d88ade95a9f117c13f83ea76da5da05ab8d303c` | 3317 |
| evidence/mv9b-pr7-rereview.md | `f99ec3bab90f443b4f776085fb4593be60201629a6aa4f78f4268ac4bf075b18` | 11484 |
| evidence/mv9b-pr7-review.md | `dd0e937b8e28541f16a8e5a1a681afb202b6ff0a83f7a70a7e781a3a0e8ee739` | 13479 |
| author/DELIVERY-MV-9b-PR7-NOGO-FIX-20261119-0716.md | `9e4f7e85294ff8570dd08b5ddd778036b9d70a87cd31788f41121067ce0d4eb9` | 11558 |
| author/SHA256SUMS-PR7-NOGO-FIX-20261119-0716 | `f9042ac04df26841679385bd6974e0e57b36bcf8ad1ebf9ddc7e7633f42c5f5c` | 1928 |

`evidence/mv9b-pr7-rereview.md` 是脱敏副本，历史主机标识 1 处写成 `[redacted-legacy-host]`。原未脱敏报告未放入本仓库。原报告未改哈希仍是 `4f8ec8550bb658df73cd7f299d24fbd8a270b5fea06d3ef93e26489054374671`（11474 字节）。`QA-HANDOFF-STOP-20261119.md` 引用的是这个原报告哈希，不是脱敏副本哈希。两处都保留，不把 QA 交接改写成脱敏副本的哈希。

`author/SHA256SUMS-PR7-NOGO-FIX-20261119-0716` 指向未打包的补丁、日志和当时工作树文件。那些文件不在本归档内。该清单不能当作本归档自身的校验，也不能当作远程产品树的校验。

## base / head / tree（本线程只读核对）

核对方式：`git fetch origin` 后 `git rev-parse` 提交与 `^{tree}`，并用 `gh pr list` 读取 PR 的 base/head 引用。时刻为上面的归档动作时间。已知开着的快照与实时值一致，没有出现第二套不同的 head。因此这里同时记下实时值；历史 no-go 仍只绑定当时的 head/tree，不改写成当前 QA。

`main`：`23d9c08eff2b8facfa8b4784ad47b47ff9c7595c`，tree `ba154d38633c84b7d55fd9484fd85ee3bd379ab0`。这是本次文档分支的起点，不是产品候选。

PR #7，Draft，未合并：

- base `mv-9-emby-public` `c792c3fe3312f77c7022aa7f5fcd30f419dbab44`，tree `072633ae9c9a705ef6b862f2c8bb380be7feaeb3`
- head `mv-9b-movie3` `0aabaaede8ba37b70607416a6fde53a4c2992ce1`，tree `f1617efc5a0645fdd1a9791024c565950e880ad9`
- 父提交 `9d6c23fd733b447ff708fcf974cf3687cbb19b62`，tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`

实时 head 与冻结候选 `0aabaaed` / tree `f1617efc` 相同，base 也相同。历史第二轮 fail/no-go 仍绑定这一组。第一轮 no-go 仍绑定父提交 `9d6c23fd` / tree `566ba6ed`。两轮结论都保留。本线程没有用实时“SHA 未变”代替质量通过。

PR #6，Draft，未合并：

- base `cursor/combo-pr1-pr3-pr5-b01e` `07a4e9d07a5d9b92386a50f6202f03022cf276b8`，tree `4c08cc919427b84871aa58d599443b5f304ee312`
- head `mv-9-emby-public` `c792c3fe3312f77c7022aa7f5fcd30f419dbab44`，tree `072633ae9c9a705ef6b862f2c8bb380be7feaeb3`

与已知快照一致。本线程不给 PR #6 新的质量结论。

PR #5，Open，不是 Draft：

- base `cursor/portal-tests-temp-state-5492` `aae83312ceca60432e3d5692f9a47765ecdf9bae`，tree `35f93d6b524dd661f6fb98e5c8158a658fb51763`
- head `cursor/gateway-no-store-header-1d54` `48f952f158f55920ab1e6350663d051c26b220b5`，tree `2db0b8102cb7dca68bad1ad9c06fd43a6d1c0483`

base/head 引用与已知快照一致。已知快照没有给出 PR #5 的 tree，上表 tree 是本线程读到的实际值。本线程不给 PR #5 新的质量结论。

PR #2 不在本任务范围。只读看到它仍开着，head `cursor/readme-subtitle-docs-d519` `7a5cbadd04ab08198b581bb7ff3966c7c12eb383`。没有改它，也不把它算进本次质量结论。

本线程开始时，开放 PR 里没有停止交接或本次归档范围的文档 PR。PR #2 的“归档”是 README 字幕说明，不是本范围。

## 真实历史测试、首次失败与重试

以下全部是停止前已写入安全包的历史记录。本线程未重跑。不能当成当前 QA，也不能当成 A 现场结果。

作者修复交接（2026-11-19 07:16 PT 之前，本地/桩，作者自测，不是独立 QA）。日志与补丁未收入本归档：

- 首次 RED，07:10:40 PT，退出码 1。6 tests，failures=13，errors=5。空 `Token=` 打到桩得到 200；脚本测试因默认端口 Connection refused。随后改的是测试夹具。
- RED 重试，07:11:11 PT，退出码 1。6 tests，18 failures，0 errors。记录的失败原因是空 `Token=` 200、空 token 与占位伪 token 的内部地址响应仍被判通过，以及旧文案。
- 首次 GREEN，07:13:58 PT，退出码 1。网关与脚本反例已过，文档仍含“拒绝连接”子串。
- GREEN 重试，07:14:10 PT，退出码 0。12 tests OK。
- 全量 unittest，07:14:18 PT，退出码 0。Ran 239 tests，OK (skipped=1)。这是作者自测里的 skipped，不是独立 QA 的 skipped。
- 本地网关探针与 `caddy validate` 随后退出码 0。作者写明官方 App、Infuse、真实滑条、Range 206、WebSocket 101、公网与测试机 A 未测。
- 作者交接里“由 Project 应用补丁并交 QA 在 A 复验”等下一步，已被用户停止指令取代，不得再执行。

独立 QA 第一轮，绑定旧 head `9d6c23fd` / tree `566ba6ed`，结论 no-go，保留。报告写明空 `Token=` 的 200 是首次探针结果，没有重试。候选 unittest 86 项首次通过。报告明确这不是 A，也不是对失败项的通过。

独立 QA 第二轮，绑定冻结 head `0aabaaed` / tree `f1617efc`，结论 fail/no-go，保留。报告写明没有失败后的重试，每条都是首次结果。旧 head 上先前失败反例再次出现。新 head 上 92 项单测首次通过，退出码 0，不能代替 A，也没有覆盖占位表以外的伪 token。

独立 QA 两轮的总览都没有单独的 skipped 行。除作者自测那 1 项 skipped 外，独立 QA 是否另有跳过用例：未知。

## passed / failed / blocked / skipped / unrun

本线程自己的测试：passed、failed、blocked、skipped、unrun 里，只有 unrun。只做了只读引用核对，没有质量测试。

历史第二轮总览（只绑定 `0aabaaed` / `f1617efc`，不是当前 QA）：

- passed：head/tree/base 核对；两个前缀上空 `Token=` 为 403 且上游 0 hits；非空伪 token 仍转上游且没有会话校验；`verify_a.py` 拒绝空 token 含内部地址的 200；`verify_a.py` 认出 `Forbidden` 后的 `LocalAddress`；`verify_movie3.py` 拒绝上次那个 ADMIN 占位伪 token 的泄漏 200；旧主机空 200 不再写成服务已关闭；本地 92 项单测首次通过。这些 passed 不是 A，也不是合并批准。
- failed：旧 head 上先前两个假通过仍然成立；`verify_movie3.py` 对占位表以外的伪 token 拿到含内部地址的 200 仍判通过；仅含 `OperatingSystem` 的 403 正文仍被判通过。
- blocked：A 现场。
- skipped：总览未列。未知。
- unrun：真实签名 Range 206、WebSocket 101、官方 App、Infuse、滑条、公网层。

历史第一轮总览（只绑定 `9d6c23fd` / `566ba6ed`，不是当前 QA）：

- passed：head/tree/base 核对；若干本地桩探针与本地单测（无 token、空查询、Cookie、非空伪造头、两前缀、D1–D6、movie2 回归、`/discover/api` 403）；86 项 unittest 首次通过。报告写明本地桩 passed 不能代替 A。
- failed：空 token 网关仍转到上游；`verify_a.py` 把空 token 成功响应判成通过；`verify_movie3.py` 把伪造 token 的含内部地址 200 判成通过。
- blocked：A 现场，以及 A 上的两个 verify 脚本。
- skipped：总览未列。未知。
- unrun：签名后的 Range 206、WebSocket 101、带凭据的手输 viewer 与播放跟随、官方 App、Infuse、真实滑条、公网层。

残留验证脚本误判是离线桩证据，不能写成 A 现场泄漏。

## 风险

- 冻结候选仍是 no-go。中：验证脚本对占位表以外的伪 token 漏报。低：403 正文只有 `OperatingSystem` 时仍判通过。`OperatingSystem` 在原报告中写明不是内部地址。
- 第一轮 no-go 的对象没有被第二轮覆盖成通过。旧 tree 上的失败仍保留。
- A 现场 blocked。播放与公网相关项 unrun。没有现场通过证据。
- 作者自测和作者交接不能代替独立 QA。
- 作者哈希清单引用了未打包文件。用它校验本归档或远程树会得到错误结论。
- 作者停止后工作区是否还有未入库改动：未知。
- 停止指令之后的作者停止交接正文：未知 / 未找到。
- 把本次文档提交或 Draft PR 当成已合并、已部署或功能验收，会和事实相反。本 PR 禁止合并。

## 授权边界

用户停止指令取代作者交接中的“继续修复 / 应用 / 在 A 复验”。本次授权只到：完成停止交接并更新到 GitHub。实现范围是文档归档的 Draft PR。

不授权第三轮修复、复测、合并、部署、DNS、隧道、Access 或现场操作。恢复任何一项都要另有明确授权，并且必须绑定当时的新候选，不能沿用本归档里的历史结果当作通过。

## 敏感检查

本线程对安全包文本检索了 password、token、私钥、主机账号和恢复通道。命中的是占位伪 token、测试桩地址 `10.0.0.8`、文档示例网段 `203.0.113.9`、`127.0.0.1` 和 `*.example.com`。包内说明已把这些标成非真实凭据。脱敏副本只有 `[redacted-legacy-host]` 这一处历史主机替换。未发现可提交的真实密码、令牌、私钥或账号口令。

未纳入：原未脱敏报告、现场配置、密钥、日志、HAR、ROLLBACK 恢复步骤、产品源码、补丁、测试实现。本说明没有补写这些内容。

## 下一步判据

本次归档完成的判据：相对 `main` 的 diff 只有文档；产品 PR 的 head 在推送后仍与推送前相同；本 PR 仍是 Draft；正文仍写 no-go、未测、未合并、未部署。满足这些只说明文档已放到 Draft PR，不说明质量通过。

此后若要再动产品：需要新的用户授权、新的候选 SHA，以及针对该候选的独立 QA。在那之前，停止状态保持不变。
