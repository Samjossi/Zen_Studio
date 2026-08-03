# assets/fonts/Noto彩色Emoji/ — Noto Color Emoji（彩色 emoji 字体）

Google Noto Color Emoji（CBDT 彩色位图格式），Zen Studio 的 **emoji 兜底字体**：
思源黑体不覆盖 emoji 字形，注册本族后 Qt 回退链自动拼接——🧠📎🖼 等
彩色渲染、跨机器一致，不再依赖系统字体施舍（0807 计划 D2-C 拍板）。

## 内容

- `NotoColorEmoji.ttf` 单文件（官方发布名，**禁止改名**——加载常量按此名引用）。
- 来源：https://github.com/googlefonts/noto-emoji（`fonts/NotoColorEmoji.ttf`，main 分支）。

## 运行时加载

- 注册入口：`gui/theme.py` 的 `EMOJI_FONT_FILES`。
- 注册字体族名：`Noto Color Emoji`；**不设为任何控件的显式字体**——仅作
  Qt 回退链兜底（主字体缺字形时自动选用）。
- 已知怪癖：`QRawFont.supportsCharacter` 对 CBDT 位图字体误报 False，
  实际光栅化正常——字形覆盖探测方法勿用于本族（0807 计划 §4.1 实证）。

## 许可证

SIL Open Font License 1.1，见同目录 `LICENSE.txt`，必须随字体保留。
