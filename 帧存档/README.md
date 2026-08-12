# 帧存档

后端 CLI（kimi / reasonix / kilocode 等）ACP 交互的**真实帧取证存档**——
代码注释、计划文档、审计报告中"实证蓝本"指针的落点。与 `.temp/` 的
定位分野：`.temp/` 是可再生临时产物（可随手清空），本目录是**不可再生
证据**（后端升级后抓不回当年的帧形态），随仓库跟踪入库。

## 来历

2026-08-12 自 `.temp/frame_archive/` 迁入（该目录曾计划 gitignore 豁免
入库，豁免行在 .gitignore 重写中丢失；改为独立跟踪目录，杜绝反规则
脆弱性）。此日期前的计划文档/审计报告中引用的 `.temp/frame_archive/`
路径即本目录。

## 约定

- 取证脚本：`scripts/capture_tool_frames.py`（kimi 系）、
  `scripts/capture_reasonix_ask_frames.py`（reasonix 提问工具）——
  产物直接写入本目录。
- 命名：`{工具}_{后端或场景}_{YYYYMMDD_HHMMSS}.json`，关联截图同名
  `.png`。
- 引用纪律：代码注释引用蓝本时写本目录相对路径（`帧存档/xxx.json`）。
- 内容提示：帧含测试 prompt 诱导的完整交互内容，低敏但如实存档，
  入库前勿混入真实私密会话帧。
