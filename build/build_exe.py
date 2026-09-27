#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地打包脚本：把 src/video_sorter_web.py 打成单文件 EXE

用法：
  python build/build_exe.py

前置：
  pip install pyinstaller

产物：
  dist/VideoSorter.exe     （Windows）
  dist/VideoSorter         （macOS / Linux）
"""

import shutil
import subprocess
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "video_sorter_web.py"
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "_pyi"   # PyInstaller 中间产物，跟 build/ 区分开


def main() -> int:
    if not SRC.is_file():
        print(f"[错误] 找不到源码：{SRC}", file=sys.stderr)
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[错误] 未安装 PyInstaller，请先执行：\n    pip install pyinstaller", file=sys.stderr)
        return 1

    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",              # 单文件
        "--console",              # 保留控制台：能看到启动 URL 和错误
        "--name", "VideoSorter",
        "--clean",
        "--noconfirm",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        # 以下三个是 tkinter 的隐式依赖，PyInstaller 有时会漏，显式加上稳妥
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "tkinter.ttk",
        str(SRC),
    ]

    print("[打包] 执行：", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print("[错误] PyInstaller 打包失败", file=sys.stderr)
        return rc

    exe_name = "VideoSorter.exe" if sys.platform.startswith("win") else "VideoSorter"
    out = DIST / exe_name
    if out.exists():
        size_mb = out.stat().st_size / 1024 / 1024
        print(f"\n✅ 打包完成：{out}  ({size_mb:.1f} MB)")
    else:
        print("[警告] 打包命令返回成功，但没找到产物，请检查 dist/ 目录", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())