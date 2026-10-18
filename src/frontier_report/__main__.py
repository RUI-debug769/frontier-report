# -*- coding: utf-8 -*-
"""支持 `python -m frontier_report <args>`——不依赖 exe 入口的通用调用方式，
任何 pip 安装环境（含 Scripts 未入 PATH 的 Windows）都可用。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
