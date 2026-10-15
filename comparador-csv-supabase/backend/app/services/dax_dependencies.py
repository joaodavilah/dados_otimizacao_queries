"""Lexical DAX references, not execution or a complete semantic DAX parser."""
import re
from collections import namedtuple

Token = namedtuple('Token', 'kind value')
TOKEN = re.compile(
    r'(?P<space>\s+)|(?P<comment>//[^\n]*|--[^\n]*|/\*.*?\*/)|'
    r'(?P<string>"(?:""|[^"])*")|(?P<table>\'(?:\'\'|[^\'])*\')|'
    r'(?P<ref>\[(?:\]\]|[^\]])+\])|(?P<word>[^\W\d]\w*)|'
    r'(?P<number>\d+(?:\.\d+)?)|(?P<symbol>.)', re.S)


def tokenize(expression):
    tokens, problems = [], []
    for match in TOKEN.finditer(expression or ''):
        kind, value = match.lastgroup, match.group()
        if kind in ('space', 'comment'):
            continue
        if kind == 'string':
            value = value[1:-1].replace('""', '"')
        elif kind == 'table':
            value = value[1:-1].replace("''", "'")
        elif kind == 'ref':
            value = value[1:-1].replace(']]', ']')
        elif kind == 'symbol' and value in ('"', "'", '[', ']'):
            problems.append('String, comentário ou identificador DAX incompleto.')
        tokens.append(Token(kind, value))
    stack = []
    for index, token in enumerate(tokens):
        if token.kind == 'symbol' and token.value in ('(', '{'):
            stack.append(token.value)
        elif token.kind == 'symbol' and token.value in (')', '}'):
            if not stack or stack.pop() != {')': '(', '}': '{'}[token.value]:
                problems.append('Delimitadores DAX não reconhecidos.')
        if token.value == '/' and index + 1 < len(tokens) and tokens[index + 1].value == '*':
            problems.append('Comentário DAX não finalizado.')
    if stack:
        problems.append('Delimitadores DAX não reconhecidos.')
    return tokens, list(dict.fromkeys(problems))


def references(expression, tables):
    tokens, problems = tokenize(expression)
    known = {t.casefold(): t for t in tables}
    variables = {tokens[i + 1].value.casefold() for i, t in enumerate(tokens[:-1])
                 if t.kind == 'word' and t.value.casefold() == 'var' and tokens[i + 1].kind == 'word'}
    virtual = set()
    # Names introduced by table constructors are not model columns. Only skip
    # them when the model has no matching object; shadowing remains ambiguous.
    calls = []
    for i, t in enumerate(tokens):
        if t.kind == 'symbol' and t.value == '(':
            function = tokens[i - 1].value.casefold() if i and tokens[i - 1].kind == 'word' else ''
            calls.append([function, 0])
        elif t.kind == 'symbol' and t.value == ')':
            if calls:
                calls.pop()
        elif t.kind == 'symbol' and t.value in (',', ';') and calls:
            calls[-1][1] += 1
        elif t.kind == 'string' and calls:
            func, arg = calls[-1]
            if ((func in ('addcolumns', 'selectcolumns') and arg >= 1 and arg % 2 == 1)
                    or (func == 'row' and arg % 2 == 0)
                    or func in ('summarize', 'summarizecolumns', 'groupby')):
                virtual.add(t.value.casefold())
    refs, whole_tables = [], set()
    keywords = {'return', 'var', 'in', 'and', 'or', 'not', 'evaluate', 'define', 'by', 'asc', 'desc'}
    for i, t in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        next_token = tokens[i + 1] if i + 1 < len(tokens) else None
        if t.kind == 'ref':
            table = None
            if prev and (prev.kind == 'table' or
                         (prev.kind == 'word' and prev.value.casefold() not in keywords)):
                table = prev.value
            refs.append((table, t.value))
        elif t.kind in ('table', 'word') and t.value.casefold() in known:
            if (next_token and (next_token.kind == 'ref' or next_token.value == '(')):
                continue
            if t.value.casefold() in variables or (prev and prev.value.casefold() == 'var'):
                continue
            # A known table identifier used as a value protects all its columns.
            whole_tables.add(known[t.value.casefold()])
    return refs, whole_tables, virtual, problems
