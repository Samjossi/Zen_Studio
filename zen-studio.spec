# zen-studio.spec
"""PyInstaller 构建配置（Linux onedir）。

构建：uv run pyinstaller zen-studio.spec
产物：dist/zen-studio/zen-studio

要点：
- datas 整目录收编 assets/（字体 + Logo，缺一不可）——字体加载是最高风险回归点
- Linux 下 icon= 参数不生效（ELF 无可执行文件图标概念），
  产物图标由 packaging/zen-studio.desktop 承担
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

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
