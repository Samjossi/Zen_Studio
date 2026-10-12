# assets/sounds/备选池/ — 提示音备用库存

> **状态**：已确认
> **范围**：`assets/sounds/备选池/` — 备用声音资产的存档与启用纪律

本目录是**备用声音资产的库存**，仅作存档，**运行时不消费、不打包进产物**。

## 启用方式

1. 从本目录挑选想要的 wav 文件；
2. **复制**（不是移动）到 `assets/sounds/正在使用/`；
3. 在设置中心「通知」页的下拉框中挑选启用。

## 纪律

- 本目录文件**禁止**被运行时代码直接引用，消费侧只认 `正在使用/`；
- 本目录**禁止**加入 `building/zen-studio.spec` 的 `datas`（`assets/README.md` 已登记）；
- 资产源自 kilocode（MIT 许可，`参考代码/kilocode-main/LICENSE`），全量 45 个 wav
  按语义分五族（同名 mp3 副本在 kilocode `packages/ui`，本池只收 wav）：

| 族 | 数量 | 语义 |
|:---|:---:|:---|
| `alert-*` | 10 | 提醒（需要用户注意） |
| `bip-bop-*` | 10 | 完成 |
| `staplebops-*` | 7 | 轻快 |
| `nope-*` | 12 | 失败 |
| `yup-*` | 6 | 确认 |
