# PySide6 Linux 中文输入法失效诊断手册

> **版本**：V1.1  
> **日期**：2026-07-29  
> **适用场景**：PySide6（pip wheel 版）在 Linux（Ubuntu/Debian 系）下无法使用 fcitx5/ibus 输入中文，但粘贴正常  
> **核心约束**：**不动全局系统包**，优先在 venv / 应用层 / 环境变量层面解决

---

## 1. 问题速查卡（发给工程师的第一句话）

请直接复制以下模板，把 `[ ]` 里的内容填上后发给对方：

> 我在 **Ubuntu [版本号]** / **[X11/Wayland]** 下使用 **pip wheel 版 PySide6 [版本号]**（venv 运行），`QLineEdit` / 搜索框无法使用 **[fcitx5/ibus]** 输入中文（无预编辑、无候选窗），但粘贴中文正常。
> 
> 系统已装 `fcitx5-frontend-qt6` [版本号]，但 wheel 自带 Qt [版本号] 与系统插件 ABI 不兼容（`dlopen` 报 `Qt_6_PRIVATE_API` 符号缺失）。
> 
> **约束**：必须在 venv 或应用层解决，**不接受** `sudo apt install` 全局改动。
> 
> **需求**：求最轻量的修复路径（venv 内降级 / 编译匹配插件）。

---

## 2. 环境快照（必贴命令）

在终端依次执行，把输出直接贴给工程师：

### 2.1 venv 内 PySide6 版本（最关键，必须先查）

```bash
source .venv/bin/activate
python -c "import PySide6; from PySide6.QtCore import qVersion; print('PySide6:', PySide6.__version__, 'Qt:', qVersion())"
```

**预期输出示例**：`PySide6: 6.11.1 Qt: 6.11.1`

> ⚠️ 不要凭印象/文档假设版本。实测教训：某项目计划按 Qt 6.11.1 编译插件，
> 实际 venv 里是 6.6.3，插件被运行时拒绝，全部返工。**先查版本，再定方案。**

### 2.2 wheel 插件目录现状（判定可走哪些协议）

```bash
ls .venv/lib/python*/site-packages/PySide6/Qt/plugins/platforminputcontexts/
```

**预期输出示例**：`libcomposeplatforminputcontextplugin.so  libibusplatforminputcontextplugin.so`

> 这份清单决定你能用什么输入法框架：**Qt6 wheel 只自带 ibus 和 compose 两个插件，
> 没有 fcitx，也没有 xim**（Qt6 已移除 XIM 插件，详见 §7 误区表）。

### 2.3 系统 fcitx5 插件与 Qt 版本（供对比）

```bash
dpkg -l | grep fcitx5-frontend-qt6
dpkg -l libqt6core6t64 2>/dev/null || dpkg -l | grep libqt6core6
```

**预期输出示例**：fcitx5-frontend-qt6 `5.1.4-1build5`；系统 Qt `6.4.2`

### 2.4 当前输入法环境变量与守护进程

```bash
echo "QT_IM_MODULE=$QT_IM_MODULE, GTK_IM_MODULE=$GTK_IM_MODULE, XMODIFIERS=$XMODIFIERS"
pgrep -a fcitx5 || pgrep -a ibus-daemon
```

### 2.5 插件加载失败的具体原因（区分两类失败）

```bash
QT_DEBUG_PLUGINS=1 QT_IM_MODULE=fcitx python main.py 2>&1 | grep -iE 'fcitx|incompatible|cannot load'
```

对照判读：

| 日志关键词 | 含义 | 对策 |
|:---|:---|:---|
| `Cannot load library ... undefined symbol ... Qt_6_PRIVATE_API` | 插件为**别的 Qt 大/小版本**编译，私有符号签名已变（如系统 6.4.2 插件 vs wheel 6.11） | 版本对齐后重编，或降级 |
| `uses incompatible Qt library. (6.x.y)` | 插件构建版本的 **minor 高于** 运行时 Qt（如 6.11 插件 vs 6.6 运行时） | 版本对齐后重编，或升级 PySide6 |
| `loaded library "...libfcitx5..."` | 插件加载成功，问题在别处 | 查 fcitx5 守护进程与环境变量 |

---

## 3. 根因判定树

根据 2.1 与 2.3 的版本对比定位：

```
venv PySide6 Qt 版本  vs  系统 Qt 版本（系统插件的编译基准）
│
├─ 一致（如都是 6.4.x）
│   └─ 问题不在 ABI 兼容，检查：
│       ├─ QT_IM_MODULE 是否设置正确？
│       ├─ fcitx5/ibus 守护进程是否在运行（见 2.4）？
│       └─ 是否 Wayland 会话下 XWayland 配置异常？
│
└─ 不一致（如 venv 6.11.1 vs 系统 6.4.2）
    └─ 🔴 根因确认：wheel 自带 Qt 与系统输入法插件 ABI 不兼容
        ├─ 系统插件基于系统 Qt 编译，依赖 Qt_6_PRIVATE_API
        ├─ Qt 私有 ABI 不跨版本兼容（连 patch 级都不保证）
        ├─ Qt 默认不搜索系统插件目录，且即使搜到也 dlopen 失败
        └─ 粘贴正常的原因：粘贴走剪贴板通道，不经过输入法
```

**结论**：版本不一致时，**不要尝试** `sudo apt install python3-pyside6`（系统根本没有这个包，或装了也改变不了 wheel 行为），**不要尝试**追加系统插件路径到 `QT_PLUGIN_PATH`（会报符号缺失）。

---

## 4. 修复方案（按场景直接选择，无需逐层试错）

### 4.0 先回答两个问题

```
Q1: 用户实际运行的是 ibus 还是 fcitx5？（见 2.4 pgrep 结果）
Q2: 这是开发调试场景，还是发行打包（AppImage）场景？
```

### 方案 ①：ibus 用户 —— 零改动（30 秒验证）

**仅当用户真跑 ibus 守护进程时适用。** PySide6 wheel 自带 ibus 平台插件：

```bash
source .venv/bin/activate
QT_IM_MODULE=ibus python main.py
```

- 测试搜索框打字。**若成功**：固化 `QT_IM_MODULE=ibus`，结束。
- ⚠️ **fcitx5 用户不要试这条路**：Ubuntu 的 fcitx5 不提供 IBus D-Bus 接口仿真
  （`fcitx5-frontend-*` 只有 gtk2/3/4、qt5、qt6），`QT_IM_MODULE=ibus` 连不上后端。
  fcitx5 用户直接去方案 ② 或 ③。

### 方案 ②：fcitx5 用户 · 开发调试期 —— venv 内降级 PySide6（5 分钟）

将 venv 里的 PySide6 降到与系统插件编译基准**严格同版本**
（系统 Qt 6.4.2 → 装 PySide6 6.4.2.x 系列；私有 ABI 连 patch 级都不保证，越贴近越好）：

```bash
source .venv/bin/activate
pip install "PySide6==6.4.2.*"
QT_PLUGIN_PATH=/usr/lib/x86_64-linux-gnu/qt6/plugins python main.py
```

- **若成功**：检查应用功能是否因降级受损（剪贴板、快捷键、主题等），
  并在 `requirements.txt` 锁定版本防意外升级。结束。
- **若失败 / 功能受损 / 必须用高版本 Qt**：进方案 ③。

### 方案 ③：fcitx5 用户 · 发行打包 / 必须用高版本 Qt —— 编译匹配插件（1~2 小时）

用与 wheel 内 Qt **完全相同**的版本编译 fcitx5-qt 插件。
本项目已固化一键脚本（含下述全部补丁与校验），可直接参考/复用：
`打包脚本/build-fcitx5-qt6-plugin.sh`

**步骤概要**：

1. 用 `aqtinstall` 安装与 wheel 同版本 Qt 到 `.build-tools/Qt/`。
   ✅ **aqt 官方二进制包自带完整私有头**（`qpa/qplatforminputcontext.h`、
   `qpa/qwindowsysteminterface.h`、`private/qguiapplication_p.h` 等），
   **无需**下载 qtbase 源码树。
2. 克隆 `fcitx/fcitx5-qt`（tag 与系统 fcitx5 匹配，如 5.1.x → 5.1.4）。
3. **必要的 CMake 补丁**（Qt ≥ 6.10 必打，否则报 `Qt6::GuiPrivate not found`）：

   ```bash
   sed -i 's|find_package(Qt6Gui ${REQUIRED_QT6_VERSION} REQUIRED Private)|find_package(Qt6GuiPrivate ${REQUIRED_QT6_VERSION} CONFIG REQUIRED)|' qt6/CMakeLists.txt
   ```

4. CMake 配置（最小化，跳过无关依赖）：

   ```bash
   cmake -GNinja -DCMAKE_BUILD_TYPE=Release \
     -DCMAKE_PREFIX_PATH=<aqt Qt 路径> \
     -DENABLE_QT4=OFF -DENABLE_QT5=OFF -DENABLE_QT6=ON \
     -DBUILD_ONLY_PLUGIN=ON \            # 跳过 Gettext 等无关依赖
     -DENABLE_QT6_WAYLAND_WORKAROUND=OFF # 纯 X11 场景可关，跳过 Wayland 私有组件
   ```

5. 编译产物 `libfcitx5platforminputcontextplugin.so`（约 1 MB）。
   ✅ `Fcitx5Qt6DBusAddons` 会**静态链接**进插件，无额外私有库需要打包；
   其余依赖（libxkbcommon、libX11 等）与 Qt xcb 平台插件相同，发行包无需额外处理。
6. 复制到 venv 的 `PySide6/Qt/plugins/platforminputcontexts/`（AppDir 同路径）。
7. 验证：`QT_DEBUG_PLUGINS=1 QT_IM_MODULE=fcitx python main.py`，
   按 §2.5 判读日志。

**维护约束**：插件绑定 Qt 版本。**每次升级 PySide6 都必须重编插件**
（脚本内置版本一致性校验，不一致会拒绝编译）。

---

## 5. 工程师必读约束（防止绕远路）

| 约束 | 说明 |
|:---|:---|
| 🚫 **不动全局系统** | 不接受 `sudo apt install` 任何系统级包，不接受修改 `/usr/lib` 下任何文件 |
| 🚫 **不追加系统插件路径** | 禁止在 `AppRun` 或启动脚本里把 `/usr/lib/x86_64-linux-gnu/qt6/plugins` 塞进 `QT_PLUGIN_PATH`，会引入版本冲突 |
| 🚫 **不试 XIM** | Qt6 已移除 XIM 平台插件，`QT_IM_MODULE=xim` 在 Qt6 下无效，纯属浪费时间 |
| ✅ **先查版本再动手** | 第一件事永远是 §2.1 查 venv 内 PySide6/Qt 实际版本，不凭文档假设 |
| ✅ **区分输入法后端** | fcitx5 用户没有 ibus 捷径；ibus 用户才有零改动方案 |
| ✅ **区分场景** | 开发调试优先 venv 降级；发行打包（AppImage）直接编译插件内置 |
| ✅ **版本锁定** | 无论降级还是编译，都在 `requirements.txt` 锁定 PySide6 版本号 |

---

## 6. 验证清单（修复后必做）

| # | 检查项 | 通过标准 |
|:--|:---|:---|
| 1 | 搜索框 fcitx5 拼音输入 | 出现预编辑字符串 + 候选窗，上屏后过滤结果正确 |
| 2 | 英文/数字输入 | 无异常，无重复字符 |
| 3 | 粘贴中文 | 正常显示并参与过滤 |
| 4 | 删除/退格 | 输入法预编辑状态下 Backspace 行为正常 |
| 5 | ibus 兜底 | `QT_IM_MODULE=ibus` 启动，确认自带 ibus 插件路径仍正常 |
| 6 | 应用回归 | 剪贴板监控、全局快捷键、系统托盘、主题切换无异常；跑一遍 pytest |
| 7 | AppImage 形态 | 打包产物同样通过 #1（插件随 site-packages 进入 AppDir） |

---

## 7. 常见误区速查

| 误区 | 真相 |
|:---|:---|
| "装系统 `python3-pyside6` 就能解决" | Ubuntu 24.04 没有 `python3-pyside6` 这个包名；即使有，wheel 版应用也不会去用系统 Python 的包 |
| "把系统插件复制进 venv 就行" | Qt 私有 ABI 不跨版本兼容，复制后 `dlopen` 会报符号缺失（实测证实） |
| "`QT_PLUGIN_PATH` 追加系统目录就行" | 同上，会加载到错误版本的插件，且污染插件搜索路径 |
| "`QT_IM_MODULE=xim` 回退到 X 协议就行" | **Qt6 已移除 XIM 平台输入上下文插件**（Qt5 才有）。Qt6 wheel 只有 ibus/compose 两个插件，xim 不存在 |
| "`QT_IM_MODULE=ibus` 让 fcitx5 仿真 ibus 就行" | fcitx5 不提供 IBus D-Bus 接口仿真（Ubuntu 24.04 无此前端包），只有真跑 ibus 守护进程的用户才适用 |
| "这是 PySide6 的 bug" | 不是 bug，是 wheel 版 Qt 与系统输入法插件的版本错配，属于部署/环境问题 |
| "必须先试遍轻量方案才能编译" | Qt6+纯 fcitx5 场景没有真正可用的轻量捷径：开发期靠降级，发行期编译是唯一正解，可直接上手 |

---

## 8. 版本记录

| 版本 | 日期 | 变更内容 |
|:---|:---|:---|
| V1.0 | 2026-07-29 | 初始版本 |
| V1.1 | 2026-07-29 | 实测修正：删除 XIM 方案（Qt6 已移除该插件）；ibus 方案限定真 ibus 用户（fcitx5 不仿真 ibus）；新增 §2.2 插件目录快照与 §2.5 日志判读表；补充编译期五条实战经验（aqt 自带私有头、Qt≥6.10 CMake 补丁、BUILD_ONLY_PLUGIN、DBusAddons 静态链接、版本校验）；修正系统 Qt 版本查询命令；方案选择改为按场景直接路由 |

---

*本文档用于直接转发给工程师，避免沟通歧义与绕远路。*
