# movie 独立QA停止开发交接

日期：2026-11-19 11:22 PST（America/Los_Angeles）。按用户本群要求停止开发、完成handoff后更新GitHub。本次仅封存/交接，不修复、不测试、不操作现场。
接手：PM汇总，由既有项目唯一更新负责人归档GitHub；QA不修改作者分支，不重复推送。

## 固定版本与范围
[PR7](https://github.com/WenLiCG/movie/pull/7) MV-9b，最后独立核实Draft、未合并，分支mv-9b-movie3，base mv-9-emby-public / `c792c3fe3312f77c7022aa7f5fcd30f419dbab44`。
候选head `0aabaaede8ba37b70607416a6fde53a4c2992ce1`，tree `f1617efc5a0645fdd1a9791024c565950e880ad9`。
旧head `9d6c23fd733b447ff708fcf974cf3687cbb19b62`，tree `566ba6edc24ee1631fd35f84e9fc18418dc0cd86`。
唯一Project：https://cursor.com/agents/bc-858ea07c-4801-5290-bd8e-72ada2a25bc9 。原独立审查线程未参与实现；QA没有修复后自审。开发生成方式为grok程序员原生Grok，作者自测不作为独立结论。

## 独立QA第二轮：fail / no-go
通过：新候选两个前缀空Token=均403、上游0hits；旧占位伪token假通过已修；verify_a能拒绝空token含LocalAddress的200及Forbidden后LocalAddress；旧Host空200文案不再声称服务关闭；本地92单测通过。
残留：
1. 中，scripts/verify_movie3.py行264–272及177–182：占位表以外伪token forged-not-a-session得到含LocalAddress的200仍ok=true，是验证脚本漏报。
2. 低，scripts/verify_a.py行211：403正文仅Forbidden加OperatingSystem=linux仍ok=true，是系统信息检测漏报，OperatingSystem不是内部地址。
旧head先复现原反例，首次失败保留；新head结果绑定上述tree，没有用作者239例代替独立QA。
边界：残留由离线桩证明，不是已证明A现场泄漏；网关不做真实会话验证为现有约定，不扩为新增网关会话校验。

## 证据、环境与未测
完整报告同目录 `mv9b-pr7-rereview.md`，SHA256 `4f8ec8550bb658df73cd7f299d24fbd8a270b5fea06d3ef93e26489054374671`；原报告位于既有Project store docs/mv9b-pr7-rereview.md。
报告包含全部命令、PST执行时间（07:34–07:37）、版本核对、预期实际、退出码及旧新反例输出。环境Linux6.12.113+、Python3.12.3、本地Caddy2.10.2；没有运行compose所钉镜像、没有Docker。没有为了交接重跑测试。
A现场blocked；真实签名Range206、WebSocket101、官方App/Infuse、滑条、公网层均unrun。没有现场凭据或真实会话信息。
两轮不收敛已交用户；本次停止指令不授权第三轮，不授权合并/部署/DNS/现场操作，恢复须另有明确授权。

## 归档要求和完成判据
保留首轮和第二轮报告、作者交接、已提交候选及经脱敏的必要证据；在既有项目正式记录形成可访问路径和提交SHA/链接，不能只给共享电脑绝对路径。不得将QA no-go改成通过，也不能以归档提交变化暗示功能复验完成。作者分支/diff和PR5/PR6状态由开发/PM原记录单列，本QA不做未核实结论。
本文件只证明QA交接已写好，GitHub尚未由QA更新或核实。后续负责人只做用户授权的归档，不续开发/复测。
方法为software-handoff v1.3和software-release-gate v1.0；不表示远端工具已安装。
