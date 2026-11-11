# assets/logo/正式logo/ — 正式 Logo 全套资产

本目录存放 Zen Studio 当前使用的正式 Logo，属于运行时资产，**会打包进产物**。母版源自 `assets/logo/logo候选池/配色候选/logo_VSCode_Blue.svg`。

## 内容 / 组织方式

| 文件 | 用途 |
|:---|:---|
| `logo.svg` | 母版，换标时唯一手改的文件 |
| `logo_16.png` … `logo_512.png` | 母版栅格化的八尺寸成套件（16/24/32/48/64/128/256/512） |

`main.py` 注册窗口图标，`building/zen-studio.desktop` 与 AppImage 构建使用 `logo_256.png`。

## 纪律

- 正式 Logo 使用通用文件名，不携带主题、配色、日期或版本号。
- 换标时从候选池复制并改为通用名，同名覆盖本目录文件，候选池原件保留。
- 只改 `logo.svg` 母版，再运行 `scripts/render_logo.py` 重渲染八件 PNG；禁止手改单件 PNG。
- 新增或调整需打包的资产时，同步核对 `building/zen-studio.spec` 的收编配置。
