#!/bin/bash
# =============================================================================
# fcitx5 Qt6 平台输入上下文插件编译脚本（Zen_Studio 移植版）
# =============================================================================
# 用法：
#   chmod +x build-fcitx5-qt6-plugin.sh
#   ./build-fcitx5-qt6-plugin.sh [QT_VERSION]
#
# 示例：
#   ./build-fcitx5-qt6-plugin.sh           # 默认编译 Qt 6.11.1 版本
#   ./build-fcitx5-qt6-plugin.sh 6.11.1    # 显式指定 Qt 版本
#
# 背景：
# - PySide6 wheel 自带的 Qt 不含 fcitx 输入法平台插件，系统的
#   fcitx5-frontend-qt6 插件（基于系统 Qt 6.4.2 编译）与 wheel 内 Qt 6.11.1
#   的私有 ABI 不兼容（详见 work plans/2026-0731-1640_中文输入法fcitx5失效修复计划.md
#   与 PySide6_Linux_中文输入法失效诊断手册_V1.1.md）
# - 因此必须用与 wheel 内 Qt 完全相同的版本从源码编译 fcitx5-qt 插件
#
# 产物：
#   .build-tools/dist/libfcitx5platforminputcontextplugin.so
#   并自动部署到 .venv 的 PySide6 插件目录
#   （不归档到 building/dist，避免与 AppImage 产物目录混淆）
#
# 依赖（需预先安装）：
#   - aqtinstall      （pipx install aqtinstall）
#   - cmake / ninja / g++
#   - extra-cmake-modules
#   - libfcitx5utils-dev（提供 Fcitx5Utils CMake 宏）
#   - libxkbcommon-dev、libxcb1-dev、libx11-dev
#   - git
# =============================================================================

set -euo pipefail

# ---------- 可配置参数 ----------
QT_VERSION="${1:-6.11.1}"          # 必须与 .venv 内 PySide6 的 Qt 版本严格一致
FCITX5_QT_TAG="5.1.4"              # 与系统 fcitx5 5.1.x 兼容的 fcitx5-qt tag

# ---------- 架构名探测 ----------
# Qt 6.11 起 aqt 架构名从 gcc_64 改为 linux_gcc_64，按目标版本动态查询，
# 兼容旧版本（降级重编场景）自动回退 gcc_64
command -v aqt >/dev/null || { echo "错误: 未找到 aqt，请先 pipx install aqtinstall"; exit 1; }
QT_ARCH=$(aqt list-qt linux desktop --arch "$QT_VERSION" 2>/dev/null | tr ' ' '\n' | grep -xE 'linux_gcc_64|gcc_64' | head -1)
[ -n "$QT_ARCH" ] || { echo "错误: Qt $QT_VERSION 无 gcc_64/linux_gcc_64 桌面架构可用"; exit 1; }

# ---------- 路径 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLS_DIR="$PROJECT_ROOT/.build-tools"
QT_BASE="$TOOLS_DIR/Qt/$QT_VERSION"
WORK_DIR="$TOOLS_DIR/fcitx5-qt-build"
SRC_DIR="$WORK_DIR/fcitx5-qt"
BUILD_DIR="$WORK_DIR/build"
DIST_DIR="$TOOLS_DIR/dist"

echo "=========================================="
echo " fcitx5 Qt6 输入上下文插件编译"
echo " Qt 版本: $QT_VERSION  架构: $QT_ARCH  fcitx5-qt: $FCITX5_QT_TAG"
echo "=========================================="

# ---------- 步骤 0：校验 venv 内 Qt 版本与目标一致 ----------
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
if [ -x "$VENV_PY" ]; then
    VENV_QT_VERSION=$("$VENV_PY" -c "from PySide6.QtCore import qVersion; print(qVersion())" 2>/dev/null || echo "未知")
    echo "[校验] .venv 内 PySide6 Qt 版本: $VENV_QT_VERSION"
    if [ "$VENV_QT_VERSION" != "$QT_VERSION" ]; then
        echo "⚠️  警告: 目标 Qt 版本 ($QT_VERSION) 与 venv 内版本 ($VENV_QT_VERSION) 不一致！"
        echo "   Qt 私有 ABI 不跨版本兼容，编译出的插件将无法加载。"
        echo "   请先用 './$(basename "$0") $VENV_QT_VERSION' 重新编译，或调整 PySide6 版本。"
        exit 1
    fi
else
    echo "[提示] 未找到 .venv，跳过版本校验（仅编译，不部署）"
fi

# ---------- 步骤 1：用 aqt 安装 Qt（含私有头文件） ----------
# 注意：aqt 查询架构名（如 linux_gcc_64）与实际落盘目录名（gcc_64）可能不一致，
# 安装后按版本目录下唯一的子目录动态定位 QT_DIR
if [ -d "$QT_BASE" ] && [ -n "$(find "$QT_BASE" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)" ]; then
    echo "[跳过] Qt $QT_VERSION 已存在于 $QT_BASE"
else
    echo "[安装] 通过 aqt 下载 Qt $QT_VERSION ($QT_ARCH) ..."
    mkdir -p "$TOOLS_DIR/Qt"
    aqt install-qt linux desktop "$QT_VERSION" "$QT_ARCH" -O "$TOOLS_DIR/Qt"
fi
QT_DIR=$(find "$QT_BASE" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -n "$QT_DIR" ] || { echo "错误: 未定位到 Qt 安装目录（$QT_BASE 下无子目录）"; exit 1; }
echo "[定位] Qt 安装目录: $QT_DIR"

# 校验关键私有头文件（Qt 官方二进制包自带）
for h in qpa/qplatforminputcontext.h qpa/qwindowsysteminterface.h private/qguiapplication_p.h; do
    if [ ! -f "$QT_DIR/include/QtGui/$QT_VERSION/QtGui/$h" ]; then
        echo "错误: 缺少私有头文件 $h，该 Qt 二进制包不含完整私有头，需改用 qtbase 源码树"
        exit 1
    fi
done
echo "[校验] Qt 私有头文件齐备"

# ---------- 步骤 2：获取 fcitx5-qt 源码 ----------
if [ -d "$SRC_DIR/.git" ]; then
    echo "[跳过] fcitx5-qt 源码已存在"
else
    echo "[克隆] fcitx5-qt @ $FCITX5_QT_TAG ..."
    mkdir -p "$WORK_DIR"
    rm -rf "$SRC_DIR"
    git clone --depth 1 --branch "$FCITX5_QT_TAG" \
        https://github.com/fcitx/fcitx5-qt.git "$SRC_DIR"
fi

# ---------- 步骤 3：修补 CMake 私有组件查找（适配 Qt >= 6.10 的查找方式） ----------
# fcitx5-qt 5.1.4 使用旧式 find_package(Qt6Gui ... Private)，
# 在 Qt 6.11 下不会创建 Qt6::GuiPrivate 目标，需改为直接查找 Qt6GuiPrivate。
sed -i \
    's|find_package(Qt6Gui ${REQUIRED_QT6_VERSION} REQUIRED Private)|find_package(Qt6GuiPrivate ${REQUIRED_QT6_VERSION} CONFIG REQUIRED)|' \
    "$SRC_DIR/qt6/CMakeLists.txt"

# ---------- 步骤 4：CMake 配置 + 编译 ----------
echo "[配置] CMake（仅 Qt6、仅插件、关闭 Wayland 绕过）..."
cmake -S "$SRC_DIR" -B "$BUILD_DIR" -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$QT_DIR" \
    -DENABLE_QT4=OFF \
    -DENABLE_QT5=OFF \
    -DENABLE_QT6=ON \
    -DENABLE_QT6_WAYLAND_WORKAROUND=OFF \
    -DBUILD_ONLY_PLUGIN=ON

echo "[编译] ..."
cmake --build "$BUILD_DIR"

PLUGIN="$BUILD_DIR/qt6/platforminputcontext/libfcitx5platforminputcontextplugin.so"
[ -f "$PLUGIN" ] || { echo "错误: 未找到编译产物 $PLUGIN"; exit 1; }

# ---------- 步骤 5：校验产物符号版本 ----------
if objdump -T "$PLUGIN" | grep -q "Qt_6_PRIVATE_API"; then
    echo "[校验] 产物使用 Qt 私有 API（符合预期）"
fi

# ---------- 步骤 6：归档产物 ----------
mkdir -p "$DIST_DIR"
cp "$PLUGIN" "$DIST_DIR/"
echo "[归档] $DIST_DIR/libfcitx5platforminputcontextplugin.so"

# ---------- 步骤 7：部署到 venv ----------
if [ -x "$VENV_PY" ]; then
    PLUGIN_DEST=$(find "$PROJECT_ROOT/.venv/lib" -type d -path '*PySide6/Qt/plugins/platforminputcontexts' | head -1)
    if [ -n "$PLUGIN_DEST" ]; then
        cp "$PLUGIN" "$PLUGIN_DEST/"
        echo "[部署] 已复制到 $PLUGIN_DEST"
    else
        echo "警告: 未找到 venv 内 PySide6 插件目录，请手动复制"
    fi
fi

echo ""
echo "=========================================="
echo "✅ 编译完成"
echo "=========================================="
echo "产物: $DIST_DIR/libfcitx5platforminputcontextplugin.so"
echo "验证: QT_DEBUG_PLUGINS=1 QT_IM_MODULE=fcitx .venv/bin/python main.py"
echo "      日志应出现 'loaded library \"...libfcitx5platforminputcontextplugin.so\"'"
