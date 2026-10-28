#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frontier-report 命令行入口。

子命令：
  daily    生成并投递当日 AI 前沿日报（会真实发信）
  weekly   生成并投递本周 AI 技术与思路周报（会真实发信）
  sample   用内置样例数据渲染 html/txt，不发信、不调 claude
  doctor   体检：claude CLI / SMTP 配置 / 数据目录 / 配置文件

退出码（daily / weekly）：0 成功；1 生成或 JSON 校验失败（不发信）；
2 发信失败或环境配置错误（存档保留）。
"""
import argparse
import json
import os
import subprocess
import sys
import time

from . import __version__
from . import common as rc
from . import config as _config
from . import daily as _daily
from . import weekly as _weekly


def _bind_loggers(cfg):
    _daily.log = rc.make_logger(os.path.join(cfg.daily_dir, "run.log"))
    _weekly.log = rc.make_logger(os.path.join(cfg.weekly_dir, "run.log"))


def _cmd_sample(args):
    """渲染内置样例，验证主题与渲染链路，无需任何外部依赖。"""
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    ds = time.strftime("%Y-%m-%d")
    written = []
    if args.kind in ("daily", "both"):
        data = _daily.parse_daily_json(
            json.dumps(_daily.SAMPLE_DATA, ensure_ascii=False))
        for name, content in (
                ("daily-sample.html", _daily.render_daily_html(data, ds)),
                ("daily-sample.txt", _daily.render_daily_text(data, ds))):
            p = os.path.join(out_dir, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(p)
    if args.kind in ("weekly", "both"):
        data = _weekly.parse_weekly_json(
            json.dumps(_weekly.SAMPLE_DATA, ensure_ascii=False))
        for name, content in (
                ("weekly-sample.html", _weekly.render_weekly_html(data, 1, ds)),
                ("weekly-sample.txt", _weekly.render_weekly_text(data, 1, ds))):
            p = os.path.join(out_dir, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(p)
    print("sample written:")
    for p in written:
        print("  %s" % p)
    return 0


def _check(label, ok, detail, hint=None):
    mark = "OK " if ok else "!! "
    print("[%s] %s: %s" % (mark, label, detail))
    if not ok and hint:
        print("       -> %s" % hint)
    return ok


def _cmd_doctor(cfg):
    """环境体检。返回 0 = 关键项全部通过。"""
    all_ok = True

    v = sys.version_info
    all_ok &= _check("Python", v >= (3, 11),
                     "%d.%d.%d（要求 >= 3.11）" % (v.major, v.minor, v.micro),
                     "请升级 Python 至 3.11 或更高版本")

    exe = _config.resolve_claude_exe(cfg)
    src = ("config.toml" if cfg.claude_exe and exe == cfg.claude_exe
           else "FRONTIER_CLAUDE_EXE" if os.environ.get("FRONTIER_CLAUDE_EXE")
           else "PATH 自动发现")
    claude_ok = os.path.isfile(exe) or exe == "claude"
    if _check("claude CLI", claude_ok, "%s（%s）" % (exe, src),
              "安装 Claude Code CLI 并登录，或在 config.toml 设置 claude_exe"):
        try:
            proc = subprocess.run([exe, "--version"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=20)
            ver = proc.stdout.decode("utf-8", "replace").strip().splitlines()
            print("       版本: %s" % (ver[0] if ver else "(无输出)"))
        except Exception as e:
            all_ok &= _check("claude 可执行", False, str(e),
                             "claude CLI 无法运行，请检查安装与登录状态")
    else:
        all_ok = False

    smtp_items = [("SMTP_HOST", rc.get_env("SMTP_HOST", "smtp.qq.com"), True),
                  ("SMTP_PORT", rc.get_env("SMTP_PORT", "587"), True),
                  ("SMTP_USER", rc.get_env("SMTP_USER"), False),
                  ("SMTP_PASS", rc.get_env("SMTP_PASS"), False),
                  ("REPORT_TO_EMAIL", rc.get_env("REPORT_TO_EMAIL"), False)]
    for name, val, has_default in smtp_items:
        if val:
            shown = val if name != "SMTP_PASS" else "******（已配置）"
            print("[OK ] %s: %s" % (name, shown))
        else:
            all_ok = False
            _check(name, False, "未配置",
                   "设置环境变量 %s，或写入 config.toml 的 [smtp] 段" % name
                   if not has_default else "")

    try:
        cfg.ensure_dirs()
        probe = os.path.join(cfg.data_dir, ".write-probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        _check("数据目录", True, "%s（可写）" % cfg.data_dir)
    except OSError as e:
        all_ok &= _check("数据目录", False, "%s（%s）" % (cfg.data_dir, e),
                         "检查路径权限，或用 --data-dir 指定其他目录")

    if cfg.config_path:
        print("[OK ] 配置文件: %s" % cfg.config_path)
    else:
        print("[-- ] 配置文件: 未找到（全部使用默认值与环境变量）")

    # 豆包搜索 MCP 注册状态（可选增强：有它中文信源检索更稳）
    mcp_ok = False
    try:
        import json as _json
        with open(os.path.expanduser("~/.claude.json"),
                  encoding="utf-8") as f:
            mcp_ok = "doubao-search" in (_json.load(f).get("mcpServers") or {})
    except (OSError, ValueError):
        pass
    if mcp_ok:
        print("[OK ] 豆包搜索 MCP: 已注册（doubao-search）")
    else:
        print("[-- ] 豆包搜索 MCP: 未注册（可选，实时检索将只用 WebSearch/WebFetch）")

    print()
    if all_ok:
        print("体检通过，可以运行 frontier-report daily / weekly")
        return 0
    print("存在未通过项，请按上方提示修复后重试")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="frontier-report",
        description="AI 前沿日报/周报：headless Claude 深研 -> 严格 JSON -> "
                    "暖陶暗色 HTML -> SMTP 投递")
    parser.add_argument("--version", action="version",
                        version="frontier-report %s" % __version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, help_text in (("daily", "生成并投递当日 AI 前沿日报"),
                            ("weekly", "聚合最近 7 份日报，生成并投递周报")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", help="config.toml 路径")
        p.add_argument("--data-dir", help="数据目录（默认 ~/.frontier-report）")

    ps = sub.add_parser("sample", help="渲染内置样例（不发信、不调 claude）")
    ps.add_argument("--kind", choices=["daily", "weekly", "both"],
                    default="both", help="渲染哪种样例（默认 both）")
    ps.add_argument("--out", default=".", help="输出目录（默认当前目录）")

    pd = sub.add_parser("doctor", help="环境体检")
    pd.add_argument("--config", help="config.toml 路径")
    pd.add_argument("--data-dir", help="数据目录（默认 ~/.frontier-report）")

    args = parser.parse_args(argv)

    if args.cmd == "sample":
        return _cmd_sample(args)

    cfg = _config.load_config(config_path=args.config, data_dir=args.data_dir)
    _config.set_config(cfg)

    if args.cmd == "doctor":
        return _cmd_doctor(cfg)

    cfg.ensure_dirs()
    _bind_loggers(cfg)
    if args.cmd == "daily":
        return _daily.run_daily()
    return _weekly.run_weekly()


if __name__ == "__main__":
    sys.exit(main())
