#!/bin/bash
# building/build_appimage.sh — Zen Studio AppImage 唯一构建入口
# （Linux x86_64 单平台，见 work plans/2026-0725-1053 计划 §5.3）
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

echo "==> [1/5] 前置自查"
# api_key/ 严禁入包：断言 spec datas 不收编、产物不含（构建后二次断言）
grep -q "api_key" building/zen-studio.spec \
    && { echo "❌ spec 中出现 api_key 收编，中止"; exit 1; }
mkdir -p "$BUILDING_DIR/tools" "$WORK_DIR"
if [[ ! -x "$TOOL" ]]; then
    echo "    appimagetool 未就位，下载：$TOOL_URL"
    curl -fL "$TOOL_URL" -o "$TOOL"
    chmod +x "$TOOL"
fi

echo "==> [2/5] PyInstaller 构建（onedir）"
uv run pyinstaller building/zen-studio.spec \
    --distpath "$DIST_DIR" --workpath "$WORK_DIR" --noconfirm
[[ -x "$ONEDIR/zen-studio" ]] || { echo "❌ onedir 产物缺失"; exit 1; }
# 不该打包项断言（计划 §4.2 清单）
INTERNAL="$ONEDIR/_internal"
for banned in "assets/fonts/思源宋体" "assets/logo候选池" "config" "参考代码" "api_key"; do
    [[ -e "$INTERNAL/$banned" ]] && { echo "❌ 禁打包项混入产物：$banned"; exit 1; }
done
echo "    禁打包项断言通过（思源宋体/logo候选池/config/参考代码/api_key 均缺席）"

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
( cd "$SMOKE_DIR" && "$PROJECT_ROOT/$APPIMAGE" --appimage-extract >/dev/null )
SQ="$SMOKE_DIR/squashfs-root"
for want in "AppRun" "zen-studio.desktop" "zen-studio.png" \
            "usr/bin/zen-studio" "usr/bin/_internal/assets/themes/base.qss" \
            "usr/bin/_internal/assets/fonts/思源黑体" \
            "usr/bin/_internal/assets/fonts/更纱黑体" \
            "usr/bin/_internal/assets/logo/logo_256.png"; do
    [[ -e "$SQ/$want" ]] || { echo "❌ 冒烟缺失：$want"; exit 1; }
done
for banned in "usr/bin/_internal/assets/fonts/思源宋体" \
              "usr/bin/_internal/assets/logo候选池" \
              "usr/bin/_internal/config" \
              "usr/bin/_internal/参考代码" \
              "usr/bin/_internal/api_key"; do
    [[ -e "$SQ/$banned" ]] && { echo "❌ 冒烟发现禁打包项：$banned"; exit 1; }
done
rm -rf "$SMOKE_DIR"

SIZE_ONEDIR="$(du -sh "$ONEDIR" | cut -f1)"
SIZE_APPIMAGE="$(du -sh "$APPIMAGE" | cut -f1)"
echo "✅ 构建完成：$APPIMAGE"
echo "   onedir 体积 $SIZE_ONEDIR → AppImage 体积 $SIZE_APPIMAGE"
