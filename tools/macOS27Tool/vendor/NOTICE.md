# Third-party code in tools/macOS27Tool/vendor

| Package | Version | Licence | Source | Used for |
|---|---|---|---|---|
| rich | 13.9.4 | MIT (`rich/LICENSE`, Copyright (c) 2020 Will McGugan) | https://pypi.org/project/rich/13.9.4/ (wheel sha256 below) | the console's live lines, colours and recorded renders |

Unchanged copy of the wheel's `rich/` package. It runs without its optional dependencies (pygments, markdown-it-py):
hao does not use `rich.syntax`, `rich.markdown` or `rich.traceback`, the only modules that need them.

rich-13.9.4-py3-none-any.whl sha256 6049d5e6ec054bf2779ab3358186963bac2ea89175919d699e378b99738c2a90
