"""pytest 公共配置：项目根入 sys.path，GUI 用例默认 offscreen 平台。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
