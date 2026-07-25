# assets/fonts/思源黑体/ — Source Han Sans CN（UI 正文字体）

Adobe 思源黑体（中国大陆子集版），Zen Studio 的 **UI 正文/标题字体**。

## 内容

`SourceHanSansCN-*.otf` 共 7 档字重：ExtraLight / Light / Normal / Regular / Medium / Bold / Heavy。

## 运行时加载

- 注册入口：`gui/theme.py` 的 `BUNDLED_FONT_FILES`，仅注册 **Regular / Medium / Bold** 三档（足以支撑正文/标题/强调层级）。
- 注册字体族名：`Source Han Sans CN`。
- 其余四档（ExtraLight / Light / Normal / Heavy）为**备用字重**，留在目录不注册，删除需另立裁剪评估。

## 许可证

SIL Open Font License，见同目录 `LICENSE.txt`。再分发合规，许可证文件必须随字体保留。
