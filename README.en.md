# frontier-report

**Automated AI-frontier daily & weekly email reports**: a headless Claude CLI
researches the day's (or week's) AI news → output is validated against a strict
JSON contract → rendered as warm-toned dark HTML (+ plain-text alternative) →
delivered via SMTP. The daily also tracks 2–4 trending/theoretical papers and
teaches one AI/math/CS concept per issue, with past concepts re-pushed on a
simplified half-life spaced-repetition schedule (inspired by MaiMemo's KDD'22
paper; state in `concepts.json`).

Pure Python standard library. **Zero third-party dependencies.**
Runs on Windows, Linux and macOS. Reports are written in Simplified Chinese
(the pipeline itself is language-agnostic — swap the prompts to change the
topic or language, see [docs/custom-prompts.md](docs/custom-prompts.md)).

[中文文档](README.md)

## How it works

```
cron / systemd timer / Windows Task Scheduler
   → frontier-report <daily|weekly>
   → prompt built (date injected / last 7 dailies aggregated)
   → headless `claude -p` (WebSearch / WebFetch deep research)
   → 3-stage tolerant JSON extraction (repr / fences / telemetry noise)
   → strict schema validation (failure = exit 1, no email sent)
   → plain text + dark HTML render (table layout, inline CSS, 640px)
   → archive .md → SMTP STARTTLS delivery (3 retries, 15s/45s backoff)
```

## Quick start

Requirements: Python ≥ 3.11, a logged-in [Claude Code CLI](https://claude.com/claude-code),
and an SMTP account.

```bash
pip install git+https://github.com/RUI-debug769/frontier-report.git
frontier-report doctor     # checks claude CLI, SMTP config, data dir
frontier-report sample     # render samples locally (no email, no model call)
frontier-report daily      # ~4-6 min, sends a real email
frontier-report weekly
```

Configuration precedence: **CLI args > env vars > config.toml > defaults**.
See [`config.example.toml`](config.example.toml) for the fully commented file
(SMTP credentials, claude path, data dir, prompt overrides). Scheduling
examples for cron, systemd timers and Windows Task Scheduler live in
[`examples/`](examples).

Exit codes: `0` archived & sent · `1` generation/validation failed (no email,
raw model output kept in `failed-output-*.txt`) · `2` delivery/config error
(archive kept).

An optional [Claude Code skill](skill/frontier-report/SKILL.md) ships with the
repo — copy `skill/frontier-report/` into `~/.claude/skills/` to trigger or
troubleshoot the pipeline conversationally ("run today's report", "the daily
didn't arrive, help me debug").

## Development

```bash
pip install -e .
python -m unittest discover -s tests -v   # 100+ tests, stdlib unittest
```

## License

[MIT](LICENSE)
