#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日报/周报共享运行时：环境变量、日志、JSON 提取、headless claude 调用、
multipart HTML 发信。纯标准库。两个具体报告（daily/weekly）单向依赖本模块，
本模块不引用它们。"""
import ast
import json
import os
import re
import smtplib
import subprocess
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

try:
    import winreg  # Windows 注册表回退（setx 写入的用户环境变量）
except ImportError:  # Linux / macOS
    winreg = None

# sendmail 成功完成前可安全重试的异常：连接/握手/认证/发送数据阶段。
# OSError 覆盖 socket.error、TimeoutError 及 SMTPServerDisconnected
# （3.9+ 起 SMTPException 继承 OSError）。sendmail 一旦成功返回即定局，
# 此后的异常（断连/quit 失败等）不在此列，禁止重试以免重复发信。
_RETRIABLE_MAIL_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPNotSupportedError,
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPDataError,
    OSError,
)


class ReportError(Exception):
    """共享运行时可预期错误基类。"""


class JsonExtractError(ReportError):
    """三级提取均失败。reason=最近一次 schema 校验错误字符串（纯垃圾时为 None）。"""
    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def get_env(name, default=""):
    """读环境变量，Windows 下回退 HKCU\\Environment（setx 写入处）。"""
    v = os.environ.get(name)
    if v:
        return v
    if winreg is None:
        return default
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val or default
    except OSError:
        return default


def make_logger(log_path):
    """返回绑定到固定文件的 log(msg)；写盘失败静默（不影响主流程）。"""
    def log(msg):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (ts, msg))
        except OSError:
            pass
    return log


def extract_json(raw, validator=None):
    """从 claude 原始输出提取对象。容忍 ANSI、```json 围栏、正文前噪声
    （警告/遥测小 JSON）、Python repr（单引号）。

    validator(obj) 给定：每个语法合法候选都过它，通过即返回其（规范化）结果；
    validator 抛异常则记录 str(异常) 作为最近原因并继续找下一候选。
    无 validator：返回首个语法合法对象。全部失败抛 JsonExtractError(reason=...)。
    """
    s = strip_ansi(raw).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)

    last_reason = None

    def _check(obj):
        if validator is None:
            return obj
        try:
            return validator(obj)
        except Exception as e:
            nonlocal last_reason
            last_reason = str(e)
            return None

    # 1) JSON 游标扫描：raw_decode 成功后游标跳到该值末尾（跳过嵌套与前置噪声）
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        i = s.find("{", pos)
        if i < 0:
            break
        try:
            obj, end = decoder.raw_decode(s, i)
        except Exception:
            pos = i + 1
            continue
        got = _check(obj)
        if got is not None:
            return got
        pos = end

    # 2) 逐行 Python 字面量（repr 周报/日报独占整行的真实形态）
    for line in s.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = ast.literal_eval(line)
        except Exception:
            continue
        got = _check(obj)
        if got is not None:
            return got

    # 3) 整段 Python 字面量：首 '{' 到末 '}'
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            obj = ast.literal_eval(s[a:b + 1])
        except Exception:
            pass
        else:
            got = _check(obj)
            if got is not None:
                return got

    raise JsonExtractError("no valid json object", reason=last_reason)


# 子 claude 的白名单环境基座：系统定位、用户目录、代理。
# claude 的登录态在 ~/.claude/.credentials.json，模型/网关配置在
# ~/.claude/settings.json，只需这些变量即可正常工作。
_CHILD_ENV_BASE = (
    "PATH", "PATHEXT", "COMSPEC", "OS",
    "SystemRoot", "SYSTEMROOT", "WINDIR", "windir",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "TEMP", "TMP", "TMPDIR",
    "USER", "USERNAME", "LOGNAME", "SHELL", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)

# 用户自己的 claude 端点/密钥配置（仅非宿主会话透传，见 _child_env）
_CHILD_ENV_AUTH = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                   "ANTHROPIC_API_KEY")


def _child_env():
    """子 claude 的白名单环境。

    在 Claude 桌面端/Agent 会话里手动触发时，父进程环境带大量宿主会话变量
    （ANTHROPIC_BASE_URL 指向宿主本地网关、CLAUDE_CODE_*、BAGGAGE 等），
    子 claude 继承后会误报 "Not logged in"。黑名单剥不净（变量随版本增长），
    故用白名单：只透传系统基座变量；用户自己的 ANTHROPIC_* 端点配置
    仅在非宿主会话（计划任务/cron/普通终端）透传——宿主会话里同名变量
    是宿主网关的，不是用户的，必须丢弃，子进程回退本机登录态。
    检测标记：CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST。"""
    env = {k: os.environ[k] for k in _CHILD_ENV_BASE if k in os.environ}
    if not os.environ.get("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"):
        env.update({k: os.environ[k] for k in _CHILD_ENV_AUTH if k in os.environ})
    return env


def run_claude(prompt, timeout, extra_tools=("WebSearch", "WebFetch"),
               claude_exe=None):
    """UTF-8 stdin 注入调 claude CLI。返回三元组 (returncode, stdout, stderr)：
    stdout/stderr 分流——CLI 把模型目录警告、[claude-code:*] 遥测打到 stderr，
    并入 stdout 会让 JSON 提取先命中遥测小 JSON。两者 decode(utf-8,replace) 并剥 ANSI。

    claude_exe 为 None 时经 config.resolve_claude_exe 自动发现。"""
    if claude_exe is None:
        from . import config as _config
        claude_exe = _config.resolve_claude_exe(_config.get_config())
    args = [claude_exe, "-p"]
    if extra_tools:
        args += ["--allowedTools", ",".join(extra_tools)]
    proc = subprocess.run(
        args,
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=_child_env(),
    )
    out = strip_ansi(proc.stdout.decode("utf-8", errors="replace"))
    err = strip_ansi(proc.stderr.decode("utf-8", errors="replace"))
    return proc.returncode, out, err


def send_html_mail(host, port, user, pw, to, subject, text_body, html_body,
                   sleep_fn=time.sleep, smtp_factory=smtplib.SMTP, logger=None):
    """发 multipart/alternative。host/port/user/pw/to 由调用方解析传入（本函数
    不读环境变量，以便调用方所在模块的 get_env 可被 monkeypatch）。重试 3 次、
    退避 15/45（末次不等待）；host 必须构造时传入以保证 STARTTLS SNI 正确。
    重试以 sendmail 是否成功完成为界：连接/握手/认证/发送数据阶段的
    _RETRIABLE_MAIL_ERRORS 才重试；sendmail 成功返回后发生的任何异常
    （断连/quit 失败）都按成功返回、不重试，best-effort quit 并记 warning
    说明邮件可能已发出。未预期异常不重试、直接抛 ReportError。全败抛 ReportError。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw = msg.as_string()

    def _log(m):
        if logger is None:
            return
        try:
            logger(m)
        except Exception:
            pass

    delays = [15, 45]
    last_err = None
    for attempt in range(3):
        s = None
        try:
            s = smtp_factory(host, port, timeout=30)  # 构造即连接，self._host 正确
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pw)
            s.sendmail(user, [to], raw)
        except _RETRIABLE_MAIL_ERRORS as e:
            # sendmail 未成功完成（服务端未确认接收），重试不会重复发信
            last_err = e
            _log("WARN mail attempt %d failed: %s" % (attempt + 1, e))
            try:
                if s is not None:
                    s.quit()
            except Exception:
                pass
            if attempt < 2:
                sleep_fn(delays[attempt])
            continue
        except Exception as e:
            # 未预期异常：状态不明，不重试（避免对未知状态重发）；
            # 保持本函数对外只抛 ReportError 的契约
            _log("WARN mail attempt %d unexpected error, no retry: %s" % (attempt + 1, e))
            try:
                if s is not None:
                    s.quit()
            except Exception:
                pass
            raise ReportError("未预期错误（不重试）: %s" % e) from e
        # sendmail 已成功返回：邮件已被服务端接收，此后任何失败均不重试
        try:
            s.quit()
        except Exception as e:
            _log("WARN 邮件可能已发出，但 sendmail 成功后断连/quit 失败"
                 "（按成功处理，不重试）: %s" % e)
        _log("mail sent (direct, attempt %d) to %s" % (attempt + 1, to))
        return
    raise ReportError("3 次尝试均失败: %s" % last_err)
