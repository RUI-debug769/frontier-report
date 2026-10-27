#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frontier-report 配置加载。

配置优先级（高 → 低）：
  CLI 参数 > 环境变量 > config.toml > 默认值

config.toml 搜索顺序（命中即止）：
  --config 指定 > $FRONTIER_REPORT_CONFIG > ./config.toml > <data_dir>/config.toml
  （此处的 data_dir 为 $FRONTIER_REPORT_DATA_DIR 或默认 ~/.frontier-report）

SMTP 字段（[smtp] 段）加载后以 os.environ.setdefault 注入进程环境，
因此 get_env（环境变量 → Windows 注册表回退）仍是唯一读取路径；
已存在的环境变量永不被覆盖。纯标准库。
"""
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_DATA_DIR = os.path.join("~", ".frontier-report")

# config.toml [smtp] 键 -> 环境变量名
_SMTP_ENV_MAP = {
    "host": "SMTP_HOST",
    "port": "SMTP_PORT",
    "user": "SMTP_USER",
    "pass": "SMTP_PASS",
    "to_email": "REPORT_TO_EMAIL",
}


@dataclass
class Config:
    data_dir: str = field(default_factory=lambda: _expand(DEFAULT_DATA_DIR))
    config_path: Optional[str] = None       # 实际命中的配置文件（未命中为 None）
    claude_exe: Optional[str] = None        # None -> 自动发现
    daily_timeout: int = 1800               # 秒（Paper+概念板块检索多，30 分钟）
    weekly_timeout: int = 1500              # 秒
    daily_prompt: Optional[str] = None      # 日报 prompt 覆盖路径
    weekly_prompt: Optional[str] = None     # 周报 prompt 覆盖路径

    @property
    def daily_dir(self):
        return os.path.join(self.data_dir, "daily")

    @property
    def weekly_dir(self):
        return os.path.join(self.data_dir, "weekly")

    def ensure_dirs(self):
        os.makedirs(self.daily_dir, exist_ok=True)
        os.makedirs(self.weekly_dir, exist_ok=True)


def _expand(p):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(p)))


def _find_config_file(explicit=None):
    """返回命中的 config.toml 路径，无命中返回 None。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get("FRONTIER_REPORT_CONFIG")
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(os.getcwd(), "config.toml"))
    base = os.environ.get("FRONTIER_REPORT_DATA_DIR") or DEFAULT_DATA_DIR
    candidates.append(os.path.join(_expand(base), "config.toml"))
    for c in candidates:
        c = _expand(c)
        if os.path.isfile(c):
            return c
    return None


def load_config(config_path=None, data_dir=None):
    """加载配置。config_path / data_dir 来自 CLI 参数（最高优先级）。

    副作用：toml [smtp] 中的值经 setdefault 注入 os.environ（见模块 docstring）。
    """
    path = _find_config_file(config_path)
    doc = {}
    if path:
        with open(path, "rb") as f:
            doc = tomllib.load(f)

    resolved_data_dir = (
        data_dir
        or os.environ.get("FRONTIER_REPORT_DATA_DIR")
        or doc.get("data_dir")
        or DEFAULT_DATA_DIR
    )

    cfg = Config(
        data_dir=_expand(resolved_data_dir),
        config_path=path,
        claude_exe=doc.get("claude_exe") or None,
        daily_timeout=int(doc.get("daily_timeout", 1800)),
        weekly_timeout=int(doc.get("weekly_timeout", 1500)),
        daily_prompt=_expand(doc["daily_prompt"]) if doc.get("daily_prompt") else None,
        weekly_prompt=_expand(doc["weekly_prompt"]) if doc.get("weekly_prompt") else None,
    )

    smtp = doc.get("smtp") or {}
    for key, env_name in _SMTP_ENV_MAP.items():
        v = smtp.get(key)
        if v is not None and str(v) != "":
            os.environ.setdefault(env_name, str(v))
    return cfg


_config = None


def get_config():
    """惰性单例：未显式 set_config 时按默认规则加载。"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg):
    global _config
    _config = cfg


def resolve_claude_exe(cfg=None):
    """claude CLI 发现链：config > $FRONTIER_CLAUDE_EXE > PATH > 裸命令名回退。

    始终返回非空字符串；最终回退 "claude" 交由 subprocess 在 PATH 中解析，
    找不到时在调用处表现为 FileNotFoundError（由报告链路按生成失败处理）。
    """
    if cfg is not None and cfg.claude_exe:
        return cfg.claude_exe
    env_exe = os.environ.get("FRONTIER_CLAUDE_EXE")
    if env_exe:
        return env_exe
    for name in ("claude", "claude.exe", "claude.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return "claude"
