# assets/readme/ — 根 README 文档素材

根目录 `README.md` 引用的界面截图与演示 GIF，**非运行时资产**，不参与打包。

## 当前文件

| 文件 | 用途 |
|:---|:---|
| `demo_chat.gif` | 演示：让 agent 创建贪吃蛇游戏（README 顶部主演示） |
| `screenshot_overview.png` | 主界面总览 |
| `screenshot_chat.png` | 聊天面板：对话与工具卡片 |
| `screenshot_code.png` | 只读查看器看代码 |

## 纪律

- **不打包**：本目录**禁止**加入 `building/zen-studio.spec` 的 `datas`。
- 更新截图/GIF 时保持**文件名不变**（README 按名引用），换图即覆盖同名文件。
- 截图注意隐私：不含真实 API Key、私人路径、未公开对话内容（参见 `scripts/privacy_scan.sh` 门禁）。
