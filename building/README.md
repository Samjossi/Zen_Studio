# building/ — 打包专区

> **状态**：已实施
> **范围**：`building/` — PyInstaller + AppImage 打包构建
> **时间**：2026-07-31 01:30（UTC+8）

---

## 1. 定位

Zen Studio 全部打包构建活动的专属目录（2026-07-25 收编，见 work plans/2026-0725-1053 计划）。构建链：**PyInstaller onedir → AppDir 组装 → appimagetool → AppImage**，Linux x86_64 单平台。

## 2. 文件结构

| 文件/目录 | 说明 | 是否入库 |
|:---|:---|:---:|
| `building/build_appimage.sh` | **AppImage 唯一构建入口**：前置自查 → PyInstaller 构建 → AppDir 组装 → appimagetool → 冒烟验证；从任意目录调用均可（脚本内路径锚定，不依赖 CWD） | ✅ |
| `building/zen-studio.spec` | PyInstaller spec：onedir 模式，`datas` 按子目录收编资产（`fonts/更纱黑体`、`fonts/思源黑体`、`themes`、`logo`、`config/version.json` 五条，详见 `assets/README.md` 收编纪律） | ✅ |
| `building/zen-studio.desktop` | AppDir 桌面入口文件（AppImage 集成用） | ✅ |
| `building/build-fcitx5-qt6-plugin.sh` | fcitx5 Qt6 输入法插件编译脚本：编译与 wheel 内 Qt 同版本的插件并部署进 `.venv`（背景见 `work plans/2026-0731-1640_中文输入法fcitx5失效修复计划.md` 与诊断手册 V1.1） | ✅ |
| `.build-tools/` | 插件编译工具链（aqt 下载的 Qt、fcitx5-qt 源码/构建区、产物归档 `dist/`） | ❌ gitignored |
| `building/build/` | PyInstaller 工作目录 + AppDir 组装区 | ❌ gitignored |
| `building/dist/` | 构建产物：`zen-studio/`（onedir 中间产物）与 `Zen_Studio-x86_64.AppImage` | ❌ gitignored |
| `building/tools/` | 打包工具链（`appimagetool`，缺失时构建脚本自动下载） | ❌ gitignored |

## 3. 用法

```bash
# 重新打包（唯一构建入口）
bash building/build_appimage.sh

# 运行打包程序（AppImage）
building/dist/Zen_Studio-x86_64.AppImage

# 运行打包程序（onedir 中间产物）
building/dist/zen-studio/zen-studio
```

## 4. 纪律

- ❌ 禁止裸跑 `pyinstaller`（不带 `--distpath`/`--workpath`）——根级 `build/`、`dist/` 已 gitignore 防产物外溢（审计 W8）；
- ⚠️ **升级 PySide6 必须同步重编 fcitx5 输入法插件**（插件绑定 Qt 私有 ABI，不跨版本兼容）：`bash building/build-fcitx5-qt6-plugin.sh`，脚本内置 venv 版本校验，不一致会拒绝编译；
- 新增需要打包的运行时资产时，必须同时在 `building/zen-studio.spec` 的 `datas` 补收编记录（联动纪律见 `assets/README.md`）；
- 打包态路径行为（`sys._MEIPASS` 检测、XDG 用户数据目录）收口于 `core/paths.py`，本目录不做路径推导。
