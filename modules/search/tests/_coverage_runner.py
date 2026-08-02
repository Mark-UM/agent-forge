#!/usr/bin/env python3
"""临时覆盖率测量脚本 — 用 stdlib trace 模块。"""
import sys
import os
import trace
import unittest

# 路径
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, PROJECT)

PRIVACY_FILE = os.path.join(PROJECT, 'modules', 'search', 'privacy.py')
PRIVACY_FILE_NORM = PRIVACY_FILE.replace('\\', '/')

# 创建 tracer 并运行测试（在 tracer 上下文中执行）
tracer = trace.Trace(count=True, trace=False)
code = (
    'import unittest\n'
    'loader = unittest.TestLoader()\n'
    'suite = loader.loadTestsFromName("modules.search.tests.test_privacy")\n'
    'runner = unittest.TextTestRunner(verbosity=0)\n'
    'result_holder["result"] = runner.run(suite)\n'
)
glb = {'result_holder': {}}
tracer.runctx(code, glb, glb)
result = glb['result_holder']['result']

# 收集结果
results = tracer.results()
counts = results.counts

# 解析 privacy.py 的行号
# 用 token-based 判断：跳过 docstring 内容、纯字符串行、注释、空行
import tokenize
import io

with open(PRIVACY_FILE, 'r', encoding='utf-8') as f:
    source = f.read()
    lines = source.splitlines(True)

# 用 tokenize 找出所有 docstring 行号
docstring_lines = set()
try:
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    for tok in tokens:
        if tok.type == tokenize.STRING:
            # 多行字符串：标记所有行
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

    # 跳过空行
    if not stripped:
        continue
    # 跳过纯注释
    if stripped.startswith('#'):
        continue
    # 跳过 docstring 内的所有行（包括多行字符串内容）
    if i in docstring_lines:
        continue
    # 跳过 if __name__ guard
    if '__name__' in stripped and '__main__' in stripped:
        continue
    if i > 1 and '__name__' in lines[i - 2] and '__main__' in lines[i - 2]:
        continue
    # 跳过字符串续行（多行 re.compile 中的 r'...'）
    if stripped.startswith("r'") or stripped.startswith('r"'):
        continue

    total_lines += 1
    # 在 counts 中查找（trace 可能用不同路径分隔符）
    hit = False
    for k, v in counts.items():
        path = k[0].replace('\\', '/')
        lineno = k[1]
        if lineno == i and v > 0 and 'privacy.py' in path:
            hit = True
            break
    if hit:
        executed_lines += 1
    else:
        missed_lines.append((i, stripped))

if total_lines > 0:
    pct = (executed_lines / total_lines) * 100
else:
    pct = 0

print()
print('=' * 70)
print(f'Coverage Report: modules/search/privacy.py')
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
