# building/zen-studio.spec
"""PyInstaller 构建配置（Linux x86_64 onedir，仅此单平台，禁止跨平台分支）。

构建入口：building/build_appimage.sh（唯一入口，AppImage 编排）
手动构建：uv run pyinstaller building/zen-studio.spec \
             --distpath building/dist --workpath building/build --noconfirm
产物：building/dist/zen-studio/zen-studio

要点：
- 路径经 SPECPATH 推导项目根，从任意目录调用行为一致（不依赖 CWD）
- datas 按子目录收编五条（字体两族 + Logo 全套 + 主题模板 + 版本文件）；
  思源宋体（87M 未注册备用族）与 logo候选池（设计草稿）明确不打包——
  见 文档/修改记录/2026-0725-1053 计划 §4 打包内容清单
- 未用 Qt 库经 a.binaries 显式过滤兜底——⚠️ 模块级 excludes 实证无效
  已回退：PySide6 6.x 钩子无视模块排除；过滤当前为防御性兜底
  （钩子按 import 依赖收编，误加模块进 import 链时过滤生效）
- Linux 下 icon= 参数不生效（ELF 无可执行文件图标概念），
  产物图标由 building/zen-studio.desktop 承担
"""
import os

#: 项目根：SPECPATH = 本 spec 所在目录（building/），上一级即项目根
PROJECT_ROOT = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(PROJECT_ROOT, "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[
        # 运行时只读资源（对应 core/paths.py ASSETS_DIR；一族一目录，
        # 新增需打包资产须同步在此补一条——assets/README.md 纪律）
        (os.path.join(PROJECT_ROOT, "assets/fonts/更纱黑体"), "assets/fonts/更纱黑体"),
        (os.path.join(PROJECT_ROOT, "assets/fonts/思源黑体"), "assets/fonts/思源黑体"),
        # Noto 彩色 Emoji（0807 计划 D2-C：思源缺 emoji 字形的内置回退，
        # Qt 回退链自动拼接；OFL-1.1，约 10M）
        (os.path.join(PROJECT_ROOT, "assets/fonts/Noto彩色Emoji"), "assets/fonts/Noto彩色Emoji"),
        (os.path.join(PROJECT_ROOT, "assets/logo"), "assets/logo"),
        # QSS 主题模板（gui/theme.py THEME_TEMPLATE_FILE 消费的只读资源）
        (os.path.join(PROJECT_ROOT, "assets/themes"), "assets/themes"),
        # 版本单一来源（core/version.py 加载器消费；2026-07-31 起版本号
        # 不再硬编码进 PYZ 源码，必须随 datas 收编否则打包态读不到）
        (os.path.join(PROJECT_ROOT, "config/version.json"), "config"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# 未用 Qt 库二进制过滤（项目仅用 Core/Gui/Widgets/Svg/Pdf/Network/DBus/Wayland/Xcb）。
# 模块级 excludes 对 PySide6 6.x 钩子无效，故在 COLLECT 前对 a.binaries 显式
# 过滤兜底；命中即整条剔除，回归验证失败则移除对应条目。
_QT_UNUSED_LIBS = (
    "Qt6Qml",            # libQt6Qml / QmlMeta / QmlModels / QmlWorkerScript
    "Qt6Quick",
    "Qt6VirtualKeyboard",
    "Qt6EglFS",          # EglFSDeviceIntegration（嵌入式设备集成，桌面不需要）
    "Qt6EglFsKms",       # EglFsKmsSupport（同上）
)
a.binaries = [
    b for b in a.binaries
    if not any(k in b[0] for k in _QT_UNUSED_LIBS)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="zen-studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="zen-studio",
)
