"""pytest conftest — 將 src/ 加入 sys.path。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
