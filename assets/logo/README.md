# assets/logo/ — 正式 Logo 全套资产

Zen Studio 官方 Logo 的**正式目录**（已定版，源自 `assets/logo候选池/B_禅圈.svg`，母版追加了浅色底衬圆盘）。

## 内容

| 文件 | 用途 |
|:---|:---|
| `logo.svg` | **母版**——换标时唯一手改的文件 |
| `logo_16.png` … `logo_512.png` | 母版栅格化的八尺寸成套件（16/24/32/48/64/128/256/512） |

## 各尺寸消费方

- `main.py`：窗口图标注册 16–256（512 留给 .desktop 与高分屏）。
- `building/zen-studio.desktop`：`Icon=` 指向 `logo_256.png`。
- AppImage 打包：`logo_256.png` 拷贝为 AppDir 的 `zen-studio.png`。

## 换标流程（纪律）

1. **只改** `logo.svg` 母版（候选池原件不动）。
2. 重跑 `scripts/render_logo.py`（PySide6 `QSvgRenderer` 渲染，幂等覆盖写八件 PNG）。
3. 人工目检小尺寸（16/24/32）辨识度。

**禁止**手改单件 PNG——会造成尺寸间不一致。
