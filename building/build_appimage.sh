#!/bin/bash
# building/build_appimage.sh — Zen Studio AppImage 唯一构建入口
# （Linux x86_64 单平台，见 文档/修改记录/2026-0725-1053 计划 §5.3）
#
# 流程：前置自查 → PyInstaller 构建 → AppDir 组装 → appimagetool → 冒烟验证
# 用法：./building/build_appimage.sh（从任意目录调用均可，不依赖 CWD）
set -euo pipefail

# --- 路径锚定（不假设 CWD） -------------------------------------------------
BUILDING_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PROJECT_ROOT="$(dirname "$BUILDING_DIR")"
cd "$PROJECT_ROOT"

DIST_DIR="building/dist"
WORK_DIR="building/build"
ONEDIR="$DIST_DIR/zen-studio"
APPDIR="$WORK_DIR/Zen_Studio.AppDir"
APPIMAGE="$DIST_DIR/Zen_Studio-x86_64.AppImage"
TOOL="$BUILDING_DIR/tools/appimagetool"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
#: appimagetool 基线哈希（2026-07-25 首下记录，防 continuous 通道漂移/投毒；
#: 有意升级时取新哈希更新本值——审计 W4）
TOOL_SHA256="a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0"

echo "==> [1/5] 前置自查"
[[ -f building/zen-studio.spec ]] || { echo "❌ spec 缺失：building/zen-studio.spec"; exit 1; }
# api_key/ 严禁入包：断言 spec datas 不收编（仅防明文意外，不防变体——
# 产物层另有 find 全深度扫描兜底）
grep -q "api_key" building/zen-studio.spec \
    && { echo "❌ spec 中出现 api_key 收编，中止"; exit 1; }
mkdir -p "$BUILDING_DIR/tools" "$WORK_DIR"
if [[ ! -x "$TOOL" ]]; then
    echo "    appimagetool 未就位，下载：$TOOL_URL"
    curl -fL --retry 3 "$TOOL_URL" -o "$TOOL"
    chmod +x "$TOOL"
fi
# 完整性校验：哈希不符即中止（漂移或投毒），打印实测哈希供有意升级时核对
ACTUAL_SHA256="$(sha256sum "$TOOL" | cut -d' ' -f1)"
[[ "$ACTUAL_SHA256" == "$TOOL_SHA256" ]] || {
    echo "❌ appimagetool 哈希不符（预期 $TOOL_SHA256，实测 $ACTUAL_SHA256）"
    echo "   若系官方更新，请人工核验后更新脚本 TOOL_SHA256 基线"
    exit 1
}

echo "==> [2/5] PyInstaller 构建（onedir）"
# 清 Analysis 缓存：PyInstaller 6.21 复用 workpath 缓存，不感知 venv 内
# 已安装文件的盘内变更（实证：fcitx5 插件 patchelf 改写 RUNPATH 后
# 缓存未失效，插件持续漏收编），发布构建必须全量重算
rm -rf "$WORK_DIR/zen-studio"
uv run pyinstaller building/zen-studio.spec \
    --distpath "$DIST_DIR" --workpath "$WORK_DIR" --noconfirm
[[ -x "$ONEDIR/zen-studio" ]] || { echo "❌ onedir 产物缺失"; exit 1; }
INTERNAL="$ONEDIR/_internal"
# 悬空符号链接清理（审计 W1）：spec a.binaries 过滤剔除真实 .so 后，
# _internal 根下指向它们的同名 symlink 残留（dangling），删除并计数公示
DANGLING="$(find "$ONEDIR" -xtype l -print -delete | wc -l)"
(( DANGLING == 0 )) || echo "    清理悬空符号链接 ${DANGLING} 个（spec 过滤残留）"
# 不该打包项断言（计划 §4.2 清单）
for banned in "assets/fonts/思源宋体" "assets/logo候选池" "参考代码"; do
    [[ -e "$INTERNAL/$banned" ]] && { echo "❌ 禁打包项混入产物：$banned"; exit 1; }
done
# config 白名单断言：仅 version.json（版本单一来源，spec datas 第五条收编）
# 允许入包，用户配置（settings 等）严禁混入——2026-07-31 起 version.json
# 为打包态必需，缺失即打包失败
[[ -f "$INTERNAL/config/version.json" ]] \
    || { echo "❌ 版本文件缺失：_internal/config/version.json 未入包"; exit 1; }
find "$INTERNAL/config" -mindepth 1 ! -name "version.json" -print -quit | grep -q . \
    && { echo "❌ config 目录混入 version.json 以外内容"; exit 1; }
# api_key 全深度扫描（防改名/嵌套变体——审计 W7 补强的兜底层）
find "$INTERNAL" -iname "*api_key*" -print -quit | grep -q . \
    && { echo "❌ 产物内发现 api_key 痕迹（全深度扫描）"; exit 1; }
echo "    禁打包项断言通过（思源宋体/logo候选池/参考代码/api_key 均缺席，config 仅 version.json）"

echo "==> [3/5] 组装 AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a "$ONEDIR/zen-studio" "$ONEDIR/_internal" "$APPDIR/usr/bin/"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/zen-studio" "$@"
EOF
chmod +x "$APPDIR/AppRun"
# AppDir 版 desktop（喂 appimagetool；与 building/zen-studio.desktop 手动模板分途维护）
cat > "$APPDIR/zen-studio.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Zen Studio
Comment=AI-first 桌面 IDE（本机 agent CLI 统一后端）
Exec=zen-studio
Icon=zen-studio
Terminal=false
Categories=Development;IDE;
StartupWMClass=zen-studio
StartupNotify=true
EOF
cp "assets/logo/logo_256.png" "$APPDIR/zen-studio.png"

echo "==> [4/5] appimagetool 打包"
export ARCH=x86_64
if ! "$TOOL" "$APPDIR" "$APPIMAGE"; then
    echo "    直接运行失败，尝试 extract-and-run 兜底（无 FUSE 环境）"
    APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --appimage-extract-and-run \
        "$APPDIR" "$APPIMAGE"
fi
chmod +x "$APPIMAGE"

echo "==> [5/5] 冒烟验证（--appimage-extract 解包核对）"
SMOKE_DIR="$(mktemp -d -p "$WORK_DIR" smoke.XXXXXX)"
# 失败路径兜底清理（审计 W2：断言 exit 时残留百 M 级解包）
trap 'rm -rf "$SMOKE_DIR"' EXIT
( cd "$SMOKE_DIR" && "$PROJECT_ROOT/$APPIMAGE" --appimage-extract >/dev/null )
SQ="$SMOKE_DIR/squashfs-root"
[[ -x "$SQ/usr/bin/zen-studio" ]] || { echo "❌ 冒烟：usr/bin/zen-studio 缺失或无可执行位"; exit 1; }
for want in "AppRun" "zen-studio.desktop" "zen-studio.png" \
            "usr/bin/_internal/assets/themes/base.qss" \
            "usr/bin/_internal/assets/fonts/思源黑体/LICENSE.txt" \
            "usr/bin/_internal/assets/fonts/更纱黑体/LICENSE.txt" \
            "usr/bin/_internal/assets/fonts/Noto彩色Emoji/LICENSE.txt" \
            "usr/bin/_internal/assets/fonts/Noto彩色Emoji/NotoColorEmoji.ttf" \
            "usr/bin/_internal/assets/logo/logo_256.png" \
            "usr/bin/_internal/config/version.json" \
            "usr/bin/_internal/PySide6/Qt/plugins/platforms/libqxcb.so" \
            "usr/bin/_internal/PySide6/Qt/plugins/platforms/libqwayland.so" \
            "usr/bin/_internal/PySide6/Qt/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so"; do
    [[ -e "$SQ/$want" ]] || { echo "❌ 冒烟缺失：$want"; exit 1; }
done
for banned in "usr/bin/_internal/assets/fonts/思源宋体" \
              "usr/bin/_internal/assets/logo候选池" \
              "usr/bin/_internal/参考代码"; do
    [[ -e "$SQ/$banned" ]] && { echo "❌ 冒烟发现禁打包项：$banned"; exit 1; }
done
# config 白名单（解包层复核）：仅 version.json 允许，其余内容禁止混入
find "$SQ/usr/bin/_internal/config" -mindepth 1 ! -name "version.json" -print -quit | grep -q . \
    && { echo "❌ 冒烟：config 目录混入 version.json 以外内容"; exit 1; }
# 解包层兜底：api_key 全深度扫描 + 悬空链接零残留（审计 W1/W7）
find "$SQ" -iname "*api_key*" -print -quit | grep -q . \
    && { echo "❌ 冒烟：AppImage 内发现 api_key 痕迹"; exit 1; }
find "$SQ" -xtype l -print -quit | grep -q . \
    && { echo "❌ 冒烟：AppImage 内存在悬空符号链接"; exit 1; }
rm -rf "$SMOKE_DIR"
trap - EXIT

SIZE_ONEDIR="$(du -sh "$ONEDIR" | cut -f1)"
SIZE_APPIMAGE="$(du -sh "$APPIMAGE" | cut -f1)"
echo "✅ 构建完成：$APPIMAGE"
echo "   onedir 体积 $SIZE_ONEDIR → AppImage 体积 $SIZE_APPIMAGE"
