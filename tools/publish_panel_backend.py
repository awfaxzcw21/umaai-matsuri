#!/usr/bin/env python3
"""把引擎后端发布到黑板面板的 `rust/` 目录。

面板侧约定（`E:/HJM/jidianbeiAI0.22/paths.py`）：
- `rust_exe()` 只找 `MATSURI_EXE_DIR` 与面板 `rust/` 目录；
- 面板以 `cwd=<面板>/rust/` 启动后端，exe 按**相对路径** `data/` 读剧本资产，
  所以发布目录必须同时含 exe 与 `data/`（与 `_ref01/matsuri-ref-01/rust/` 同布局）。

面板调用协议（`blackboard_panel.py`）：
- `matsuri-mccli.exe <state.json> <result.json> <n>`，env `MC_PLY/MC_K/MC_SEED`
- `matsuri-dist.exe  <state.json> <result.json>`

用法：
    python tools/publish_panel_backend.py [--panel-dir E:/HJM/jidianbeiAI0.22] [--dry-run]
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 面板后端用到的 exe
EXES = ("matsuri-mccli.exe", "matsuri-dist.exe")

# 剧本资产 + 引擎静态表（与 `_ref01/matsuri-ref-01/rust/data/` 同清单）
DATA_FILES = (
    "ai_config.toml",
    "cardDB.json",
    "constants.json",
    "events.json",
    "manifest.json",
    "matsuri_config.toml",
    "scenario_matsuri.json",
    "text_data_dict.json",
    "umaDB.json",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="发布引擎后端到面板 rust/ 目录")
    ap.add_argument("--panel-dir", default=r"E:\HJM\jidianbeiAI0.22")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    panel = Path(args.panel_dir)
    rust_dir = panel / "rust"
    data_out = rust_dir / "data"

    src_exe = REPO_ROOT / "target" / "release"
    missing = [n for n in EXES if not (src_exe / n).is_file()]
    if missing:
        print(f"错误：缺少构建产物 {missing}；先跑 cargo build --release -p matsuri-cli")
        return 2
    missing_data = [n for n in DATA_FILES if not (REPO_ROOT / "data" / n).is_file()]
    if missing_data:
        print(f"错误：数据根缺少 {missing_data}")
        return 2

    print(f"面板目录   {panel}")
    print(f"发布目录   {rust_dir}")
    print(f"引擎仓     {git('rev-parse', '--short', 'HEAD')}（{git('rev-parse', '--abbrev-ref', 'HEAD')}）")

    if args.dry_run:
        for n in EXES:
            print(f"  [dry] {n}")
        for n in DATA_FILES:
            print(f"  [dry] data/{n}")
        return 0

    rust_dir.mkdir(parents=True, exist_ok=True)
    data_out.mkdir(parents=True, exist_ok=True)

    manifest = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for n in EXES:
        src = src_exe / n
        dst = rust_dir / n
        shutil.copy2(src, dst)
        manifest[n] = {"sha256": sha256(dst), "size": dst.stat().st_size}
        print(f"  → {dst.name}（{dst.stat().st_size} 字节）")

    for n in DATA_FILES:
        src = REPO_ROOT / "data" / n
        dst = data_out / n
        shutil.copy2(src, dst)
        print(f"  → data/{n}（{dst.stat().st_size} 字节）")

    manifest["git_commit"] = git("rev-parse", "HEAD")
    manifest["git_branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
    (rust_dir / "backend_build.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → backend_build.json（记录了本次发布的 commit 与 exe 校验和）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
