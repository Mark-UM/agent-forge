#!/usr/bin/env python3
"""临时覆盖率测量脚本 — quality.py。"""
import sys
import os
import trace
import unittest
import io
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, PROJECT)

TARGET_FILE = os.path.join(PROJECT, 'modules', 'search', 'quality.py')

tracer = trace.Trace(count=True, trace=False)
code = (
    'import unittest\n'
    'loader = unittest.TestLoader()\n'
    'suite = loader.loadTestsFromName("modules.search.tests.test_quality")\n'
    'runner = unittest.TextTestRunner(verbosity=0)\n'
    'result_holder["result"] = runner.run(suite)\n'
)
glb = {'result_holder': {}}
tracer.runctx(code, glb, glb)
result = glb['result_holder']['result']

results = tracer.results()
counts = results.counts

with open(TARGET_FILE, 'r', encoding='utf-8') as f:
    source = f.read()
    lines = source.splitlines(True)

# 找出所有多行字符串行号
docstring_lines = set()
try:
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    for tok in tokens:
        if tok.type == tokenize.STRING:
            start_line = tok.start[0]
            end_line = tok.end[0]
            for ln in range(start_line, end_line + 1):
                docstring_lines.add(ln)
except tokenize.TokenizeError:
    pass

total_lines = 0
executed_lines = 0
missed_lines = []

for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if not stripped:
        continue
    if stripped.startswith('#'):
        continue
    if i in docstring_lines:
        continue
    if '__name__' in stripped and '__main__' in stripped:
        continue
    if i > 1 and '__name__' in lines[i - 2] and '__main__' in lines[i - 2]:
        continue
    if stripped.startswith("r'") or stripped.startswith('r"'):
        continue

    total_lines += 1
    hit = False
    for k, v in counts.items():
        path = k[0].replace('\\', '/')
        lineno = k[1]
        if lineno == i and v > 0 and 'quality.py' in path:
            hit = True
            break
    if hit:
        executed_lines += 1
    else:
        missed_lines.append((i, stripped))

pct = (executed_lines / total_lines * 100) if total_lines > 0 else 0

print()
print('=' * 70)
print(f'Coverage Report: modules/search/quality.py')
print('=' * 70)
print(f'Total executable lines: {total_lines}')
print(f'Executed lines:         {executed_lines}')
print(f'Missed lines:           {len(missed_lines)}')
print(f'Coverage:               {pct:.2f}%')

if missed_lines:
    print()
    print('Missed lines:')
    for lineno, content in missed_lines:
        print(f'  L{lineno}: {content}')

print()
print('Test result:')
print(f'  Tests run:  {result.testsRun}')
print(f'  Failures:   {len(result.failures)}')
print(f'  Errors:     {len(result.errors)}')
print(f'  Success:    {result.wasSuccessful()}')

sys.exit(0 if result.wasSuccessful() else 1)
