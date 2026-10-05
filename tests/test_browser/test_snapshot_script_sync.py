"""快照脚本双端同源漂移测试（桌面 browserHost.ts 常量 vs Python snapshot.py）"""
from pathlib import Path
import re

from illusion.browser.snapshot import SNAPSHOT_SCRIPT


def test_snapshot_script_in_sync_with_desktop() -> None:
    ts = (Path(__file__).parents[2] / "desktop" / "src" / "browserHost.ts").read_text(encoding="utf-8")
    m = re.search(r"const SNAPSHOT_SCRIPT = (?:String\.raw)?`([\s\S]*?)`;", ts)
    assert m, "browserHost.ts 中未找到 SNAPSHOT_SCRIPT 常量"
    assert m.group(1).strip() == SNAPSHOT_SCRIPT.strip(), (
        "桌面端快照脚本与 Python 端漂移——两套运行时各自内联一份，"
        "漂移会让 ref 体系/快照行为不一致（历史 P0）。请同步两份脚本。"
    )
