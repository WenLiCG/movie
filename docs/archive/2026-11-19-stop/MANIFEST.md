# movie 停止开发归档清单（安全附件）

整理时间：2026-11-19，America/Los_Angeles（PST，UTC−8）。
整理范围：只复制并敏感检查既有交接/审查正文，不改实现、不跑测试、不远程登录、不写 GitHub、不对外发消息。
方法：software-handoff v1.3。本清单是归档说明，不是新的质量结论。

## 项目与入口

- 项目：movie
- 仓库：https://github.com/WenLiCG/movie
- 既有唯一执行协调入口：https://cursor.com/agents/bc-858ea07c-4801-5290-bd8e-72ada2a25bc9
- 下一责任人：PM 将本附件交上述既有 Project，只做停止交接归档。不新建 Project，不合并，不部署，不恢复开发。
- 产品代码已在 Draft [PR #7](https://github.com/WenLiCG/movie/pull/7)（head `0aabaaede8ba37b70607416a6fde53a4c2992ce1`，tree `f1617efc5a0645fdd1a9791024c565950e880ad9`）。本包不重复提交源码。

## 必须保留的质量边界

- 冻结候选 PR #7：独立 QA 第二轮 **fail / no-go**。不是合并批准，也不是本次复测。
- 旧 head `9d6c23fd733b447ff708fcf974cf3687cbb19b62` / tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`：第一轮 **no-go**，结论保留。
- A 现场 blocked；Range 206、WebSocket 101、官方 App、Infuse、滑条、公网层 unrun。
- 作者自测不能代替独立 QA。作者修复交接写于停止指令之前，其中“继续应用补丁 / 在 A 复验”已被停止指令取代。
- 用户停止指令之后的作者停止交接正文：在共享电脑现有目录中 **未找到**，保持未知，未编造。

## 敏感检查

对拟打包文本逐份阅读，并扫描 password/token/私钥/主机账号/恢复通道等模式。

- 未发现密码、令牌、私钥或账号口令。
- 第二轮报告原文有一处历史主机标识出现在用例名中。包内 `evidence/mv9b-pr7-rereview.md` 为脱敏副本，将该标识替换为 `[redacted-legacy-host]`（1 处）。原文 SHA256 `4f8ec8550bb658df73cd7f299d24fbd8a270b5fea06d3ef93e26489054374671`（11474 字节）仍留在 `/workspace/movie/qa-stop-handoff-20261119/mv9b-pr7-rereview.md`，未改。脱敏副本 SHA256 见下表。
- 测试桩地址 `10.0.0.8`、文档示例网段 `203.0.113.9`、本机 `127.0.0.1`，以及占位伪 token / `*.example.com`，不属于真实凭据，原文保留。
- 除此一处脱敏外，其余收录文件与源文件字节一致。

## 收录文件（均已审过）

| 归档路径 | 角色 | 源路径 | SHA256 |
|---|---|---|---|
| HANDOFF.md | 本次停止交接封面 | 本包整理生成 | a1a96f97f0178dc36422b61bd3ecbdffec044e31b52d8ef1948434f396986a3e |
| QA-HANDOFF-STOP-20261119.md | QA 停止交接 | /workspace/movie/qa-stop-handoff-20261119/QA-HANDOFF-STOP-20261119.md | 04b72284d12b7bbbaa5ba0647d88ade95a9f117c13f83ea76da5da05ab8d303c |
| evidence/mv9b-pr7-rereview.md | 第二轮独立报告（脱敏副本，原 fail/no-go） | /workspace/movie/qa-stop-handoff-20261119/mv9b-pr7-rereview.md（原文未改；与 Project store docs/mv9b-pr7-rereview.md 原文哈希相同） | f99ec3bab90f443b4f776085fb4593be60201629a6aa4f78f4268ac4bf075b18 |
| evidence/mv9b-pr7-review.md | 第一轮独立报告（no-go） | /cursor/stores/bc-858ea07c-4801-5290-bd8e-72ada2a25bc9/docs/mv9b-pr7-review.md | dd0e937b8e28541f16a8e5a1a681afb202b6ff0a83f7a70a7e781a3a0e8ee739 |
| author/DELIVERY-MV-9b-PR7-NOGO-FIX-20261119-0716.md | 停止指令之前的作者修复交接，不是停止交接正文 | /workspace/movie/mv9b/DELIVERY-MV-9b-PR7-NOGO-FIX-20261119-0716.md | 9e4f7e85294ff8570dd08b5ddd778036b9d70a87cd31788f41121067ce0d4eb9 |
| author/SHA256SUMS-PR7-NOGO-FIX-20261119-0716 | 作者修复交付哈希清单（指向未打包的补丁/日志） | /workspace/movie/mv9b/SHA256SUMS-PR7-NOGO-FIX-20261119-0716 | f9042ac04df26841679385bd6974e0e57b36bcf8ad1ebf9ddc7e7633f42c5f5c |
| MANIFEST.md | 本清单 | 本归档生成 | （见包外清单或重新计算） |

## 明确不收录

未复印：产品源码、补丁、`repo-pr7-nogo` 工作树、现场或隧道 Caddy、日志、HAR、备份、`.venv`、密钥、`.env`、数据库，以及含测试机绝对路径和恢复步骤的 `/workspace/movie/mv9b/ROLLBACK-MV-9b.md`。这些原件仍在共享电脑原路径，未改。作者日志仅以交接中的路径和哈希引用，本包不附原文。更早的 follow-up 审查/交付不属于冻结候选的两轮 PR7 报告，为保持最小附件而未收录。

## 需要 PM 注明的局限

- 本包只是可上传的安全附件，整理者未上传、未操作 Cloud Project，未写 GitHub。
- 远程 head/tree 未在本轮实时核对。
- 不得把本归档写成质量通过，或把已提交当成已合并/已发布/功能验收完成。
- 作者停止交接正文在停止指令之后未找到；缺口保持未知。
