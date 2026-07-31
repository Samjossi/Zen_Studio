# assets/fonts/思源宋体/ — Source Han Serif CN（备用衬线字体）

Adobe 思源宋体（中国大陆子集版）。

## 状态：⚠️ 当前未注册使用 + ❌ 不打包

- `gui/theme.py` **没有**注册本族任何字重——本目录是留库备用的**非分发资产**（未来正文衬线排版等场景）。
- 文件共 7 档字重：ExtraLight / Light / Regular / Medium / SemiBold / Bold / Heavy，约 87M，是字体体积中最大的一族。
- 因完全未注册且体积最大，经决策**不随包分发**（文档/修改记录/2026-0725-1053 计划修订二）：`building/zen-studio.spec` 的 `datas` 按族收编，不含本目录。文件留库不删；未来启用（注册 + 重新入包）另立计划，届时需同步补齐 `LICENSE.txt`。

## 若需启用

在 `gui/theme.py` 增加注册常量与加载逻辑（仿照 `BUNDLED_FONT_FILES` 的模式），注册字体族名为 `Source Han Serif CN`。

## 许可证

SIL Open Font License。注意：本目录当前**缺少** `LICENSE.txt`（其他两族均有），如需长期保留请补齐。
