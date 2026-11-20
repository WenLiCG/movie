# movie 停止开发交接（2026-11-19）

整理时间：2026-11-19，America/Los_Angeles（PST，UTC−8）。
方法：software-handoff v1.3。本文件是停止归档说明，不是新的质量复验，不是合并批准，也不是恢复开发的授权。

## 项目 / 任务 / 阶段 / 接手人

- 项目：movie
- 仓库：https://github.com/WenLiCG/movie
- 用户原话（movie 项目群，2026-11-19）：停止开发，完成handoff后更新到GitHub。
- 当前阶段：开发已停止。产品代码已在 Draft PR #7 中，本次只缺此次停止交接的 GitHub 归档。本包只提供可交既有入口的安全文档附件，不重复提交源码。
- 既有唯一执行协调 Project：https://cursor.com/agents/bc-858ea07c-4801-5290-bd8e-72ada2a25bc9
- 下一责任人：PM 将本附件交上述既有 Project，仅做停止交接归档。不新建 Project，不合并，不部署，不恢复开发或测试。

## 目标、范围与非目标

目标：把已有的 QA 停止交接、两轮独立失败/未测证据，以及停止指令之前的作者修复交接，整理成最小安全附件，供统一归档到 GitHub。

非目标：不改源码、不克隆、不测试、不远程登录、不写 GitHub、不对外发消息、不打包补丁或产品树、不操作现场/DNS/隧道/Access。

## repo / PR / 固定候选

下列 SHA 来自 QA 停止交接与第二轮独立报告，以及此前只读核对。本包整理过程没有重新 fetch 或复测。

- Draft [PR #7](https://github.com/WenLiCG/movie/pull/7)，分支 `mv-9b-movie3`，open / draft，未合并。
- 冻结候选 head：`0aabaaede8ba37b70607416a6fde53a4c2992ce1`
- 冻结候选 tree：`f1617efc5a0645fdd1a9791024c565950e880ad9`
- base：`mv-9-emby-public` / `c792c3fe3312f77c7022aa7f5fcd30f419dbab44`
- 旧远程 head（第一轮 no-go，为本冻结提交的父提交）：`9d6c23fd733b447ff708fcf974cf3687cbb19b62`，tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`
- 默认分支 `main` 最近提交（历史，不是本次停止归档）：`23d9c08eff2b8facfa8b4784ad47b47ff9c7595c`（2026-11-13）。仓库根目录已有的 `HANDOFF.md` 是 2026-11-09 的历史通用开发交接，与此次停止交接不是同一份正文。
- 作者本地修复提交（停止指令之前）：`3baab01849f4350f55081fedc709705202c83695`，tree 与冻结候选相同（`f1617efc…`），父提交是同 tree 的本地对象 `686b92a2…`，不是远程 `9d6c23fd`。远程已按同一 tree 入库为 `0aabaaed`。不要再提交源码或补丁。

PR #5、PR #6 的当前远程状态：本包未再次实时核对，保持未知；QA 停止交接要求由开发/PM 原记录单列，不以本包补写结论。

## 生成方式

- 冻结候选实现：grok 程序员（原生 Grok）。作者自测不是独立 QA。
- 独立审查线程未参与实现，也没有修复后自审。
- 本包：只从共享电脑已有目录和本日群交接消息收录已有正文，经敏感检查后复制或脱敏。没有编造缺失项，没有重跑测试。

## 已确认结论边界

独立 QA 第二轮（绑定冻结 head `0aabaaed` / tree `f1617efc`）：**fail / no-go**。完整报告见 `evidence/mv9b-pr7-rereview.md`（包内为脱敏副本）。

- 通过项（历史，不是本次复测）：两个前缀上空 `Token=` 为网关 403、上游 0 hits；原占位伪 token 假通过已修；`verify_a` 能拒绝空 token 含 `LocalAddress` 的 200 及 Forbidden 后的 `LocalAddress`；旧主机空 200 文案不再写成服务关闭；本地 92 项单测通过。本地单测不能代替现场。
- 残留缺陷仍在：
  1. 中：`scripts/verify_movie3.py` 对占位表以外的伪 token `forged-not-a-session` 拿到含 `LocalAddress` 的 200 仍判通过（验证脚本漏报）。
  2. 低：`scripts/verify_a.py` 对 403 正文仅 `Forbidden` 加 `OperatingSystem=linux` 仍判通过。`OperatingSystem` 是系统信息，不是内部地址。
- 上述残留由离线桩证明，不是已证实的 A 现场泄漏。网关不验证真实会话，是当时约定，不是本包授权去扩范围。

独立 QA 第一轮（绑定旧 head `9d6c23fd` / tree `566ba6ed`）：**no-go**，结论保留。完整报告见 `evidence/mv9b-pr7-review.md`。旧对象本身的空 `Token=` 反代和脚本假通过没有在旧 tree 里消失。

作者 2026-11-19 07:16 PT 的修复交接（停止指令之前，不是停止交接正文）见 `author/DELIVERY-MV-9b-PR7-NOGO-FIX-20261119-0716.md`。其中“由 Project 应用补丁并交 QA 在 A 复验”等下一步，已被随后的停止指令取代，不得再执行。

## 未找到或保持未知

- 用户发出停止指令之后，共享电脑 `/workspace/movie` 下除 QA 于 11:23 PST 封存的 `qa-stop-handoff-20261119/` 外，没有找到一份已完成的作者停止交接正文或冻结 zip。群内作者消息写过“冻结交接资料已开始整理，并已请 PM 协调 GitHub 归档……GitHub 更新尚未完成”。作者停止交接包本身：**未知 / 未找到**。不以作者修复交付改写成停止交接。
- 作者当时工作区未提交 diff、是否另有未入库文件：未知。本包未打开工作区源码或密钥目录核实。
- 本包整理时未再次实时核对 GitHub 远程。若归档前远程 head/tree 与上表不同，只保留本历史证据，不得沿用为当前结论。

## 未测与授权边界

A 现场为 blocked。真实签名 Range 206、WebSocket 101、官方 App、Infuse、滑条、公网层均为 unrun。没有现场凭据或真实会话信息被收入本包。

恢复开发、第三轮修复、复测、合并、部署、DNS、隧道、Access 或现场操作，都须另有用户明确授权。本交接和本附件都不是该授权。不得把 QA no-go 改成通过，也不得以归档提交暗示功能验收完成。

## 敏感检查与脱敏

已阅读拟打包文本，并扫描密码、token、私钥、主机账号与恢复通道等模式。

- QA 停止交接、第一轮报告、作者修复交接及其 SHA256SUMS：未发现密码、令牌、私钥或账号口令；与源文件字节一致。原件未改。
- 第二轮报告原文有一处历史主机标识出现在用例名中。包内使用脱敏副本，将该标识替换为 `[redacted-legacy-host]`，并在 `MANIFEST.md` 记录。原文仍留在 `/workspace/movie/qa-stop-handoff-20261119/mv9b-pr7-rereview.md`，未改。
- 报告中的 `10.0.0.8`、`203.0.113.9`、`127.0.0.1`、占位串 `fake-admin-token-for-gateway-test` / `forged-not-a-session`，以及 `movie.example.com` / `movie3.example.com`，属于测试桩或文档示例，不是真实凭据，原文保留。
- 明确不收录：源码与补丁、仓库工作树、现场或隧道 Caddy、日志、HAR、备份、回滚/恢复步骤、密钥与 `.env`、数据库，以及写有测试机绝对路径和恢复通道的 `ROLLBACK-MV-9b.md`。这些原件仍在原路径，未改。

## 下一步与完成判据

- 下一步（仅归档）：PM 将本 zip、`MANIFEST.md` 与 `HANDOFF.md` 作为真实附件交给既有 Project `bc-858ea07c-4801-5290-bd8e-72ada2a25bc9`，在 GitHub 形成可访问路径和提交 SHA/链接；保留 fail/no-go 与未测项。不要把共享电脑绝对路径当作唯一来源。
- 建议技能（若日后另有授权再恢复）：software-handoff v1.3、software-release-gate；重验必须绑定当时候选，不能沿用作者自测或本包中的历史结论。
- 本包完成判据：最小安全附件和清单已生成，哈希已计算，敏感项已排除或脱敏，原件未改，整理者未写 GitHub、未恢复开发。
