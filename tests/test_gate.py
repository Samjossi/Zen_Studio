import os
import sys
from pathlib import Path


def test_pytest_gate_environment():
    src = Path(__file__).resolve().parents[1] / "src"

    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    assert str(src) in sys.path
