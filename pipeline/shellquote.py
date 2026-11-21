#!/usr/bin/env python3.14
"""shellquote: list the variable expansions of a sh script that the shell would split at a space.

  python3.14 -I pipeline/shellquote.py FILE ...      one line per file with hits: LINE:TOKEN ...

An install runs recipe scripts from wherever the kit is: the user's installer volume is "/Volumes/M27 1", and an
unquoted "$P/..." split there stopped an install. This finds each $NAME, $1 or ${...} that is outside double quotes.
Not reported: anything in single quotes, comments or heredoc bodies; arithmetic $((...)); the value of a plain
assignment (NAME=$X/$Y is one word); the word after `case`. Inside $(...) the shell starts a new quoting level, and so
does this. It is a lexer, not a parser: a deliberate split (a list, ${X:+--flag "$X"}) is reported too and is
allowed by its caller with a reason.
"""
from pathlib import Path
import re
import sys

NAME = re.compile(r'[A-Za-z_]\w*=')
TOKEN = re.compile(r'\$(\{[^}]*\}|[A-Za-z_]\w*|[0-9])')
HEREDOC = re.compile(r'<<-?\s*([\'"]?)(\w+)\1')
WORD_END = ' \t;&|<>'


def unquoted(text):
    """[(line, token)] for each expansion outside double quotes (see the module doc for what is skipped)."""
    out = []
    # a frame per quoting level: kind 'code' (the script, or the inside of $(...)) or 'dq'
    stack = [{'kind': 'code', 'sub': False, 'depth': 0, 'assign': False}]
    i, line, L = 0, 1, len(text)
    heredocs = []
    start = True                                   # at the start of a word (code frames)
    while i < L:
        c = text[i]
        top = stack[-1]
        if c == '\n':
            line += 1
            i += 1
            if top['kind'] == 'code':
                top['assign'] = False
                start = True
            for word in heredocs:                  # skip each body, up to and including its terminator line
                while i < L:
                    e = text.find('\n', i)
                    e = L if e < 0 else e
                    body = text[i:e]
                    i = e + 1
                    line += 1
                    if body.strip() == word:
                        break
            heredocs = []
            continue
        if c == '\\':
            if i + 1 < L and text[i + 1] == '\n':
                line += 1
            i += 2
            start = False
            continue
        if c == '$':
            if text.startswith('$((', i):          # arithmetic: no splitting of its result's operands
                depth, j = 0, i + 1
                while j < L:
                    if text[j] == '(':
                        depth += 1
                    elif text[j] == ')':
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                i = j + 1
                start = False
                continue
            if text.startswith('$(', i):           # command substitution: a new quoting level
                stack.append({'kind': 'code', 'sub': True, 'depth': 0, 'assign': False})
                i += 2
                start = True
                continue
            m = TOKEN.match(text, i)
            if m:
                if top['kind'] == 'code' and not top['assign']:
                    before = text[text.rfind('\n', 0, i) + 1:i]
                    if not re.search(r'\bcase\s+$', before):
                        out.append((line, m.group(0)))
                i = m.end()
                start = False
                continue
            i += 1
            start = False
            continue
        if top['kind'] == 'dq':
            if c == '"':
                stack.pop()
            i += 1
            continue
        # code frame
        if c == "'":
            j = text.find("'", i + 1)
            j = L if j < 0 else j
            line += text.count('\n', i, j)
            i = j + 1
            start = False
            continue
        if c == '"':
            stack.append({'kind': 'dq'})
            i += 1
            start = False
            continue
        if c == '#' and start:
            e = text.find('\n', i)
            i = L if e < 0 else e
            continue
        if c in WORD_END:
            top['assign'] = False
            m = HEREDOC.match(text, i) if c == '<' else None
            if m:
                heredocs.append(m.group(2))
                i = m.end()
                start = False
                continue
            start = True
            i += 1
            continue
        if c == '(':
            top['depth'] += 1
            start = True
            i += 1
            continue
        if c == ')':
            if top['depth']:
                top['depth'] -= 1
            elif top['sub']:
                stack.pop()
            i += 1
            start = False
            continue
        if start:
            m = NAME.match(text, i)
            if m:
                top['assign'] = True
                i = m.end()
                start = False
                continue
        start = False
        i += 1
    return out


if __name__ == '__main__':
    total = 0
    for f in sys.argv[1:]:
        hits = unquoted(Path(f).read_text())
        if hits:
            total += len(hits)
            print('%s: %s' % (f, ' '.join('%d:%s' % h for h in hits)))
    print('total', total)
    sys.exit(1 if total else 0)
