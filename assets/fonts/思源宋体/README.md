# assets/fonts/思源宋体/ — Source Han Serif CN（备用衬线字体）

Adobe 思源宋体（中国大陆子集版）。

## 状态：⚠️ 当前未注册使用

- `gui/theme.py` **没有**注册本族任何字重——本目录是随包分发的**备用资产**（留作未来正文衬线排版等场景）。
- 文件共 7 档字重：ExtraLight / Light / Regular / Medium / SemiBold / Bold / Heavy，约 87M，是字体体积中最大的一族。
- 因打包时 `assets/fonts/` 整目录收编，本族会进入产物。是否保留/裁剪/子集化属**另立计划**的评估项，请勿随手删除。

## 若需启用

在 `gui/theme.py` 增加注册常量与加载逻辑（仿照 `BUNDLED_FONT_FILES` 的模式），注册字体族名为 `Source Han Serif CN`。

## 许可证

SIL Open Font License。注意：本目录当前**缺少** `LICENSE.txt`（其他两族均有），如需长期保留请补齐。
