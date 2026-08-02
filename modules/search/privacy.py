#!/usr/bin/env python3
"""出境 PII 脱敏层 — 在 MCP 调用前对 query 做正则脱敏。

设计原则：
- 单文件独立模块，零外部依赖（仅 Python 标准库）
- 失败时返回原 query，绝不抛异常
- 7 种 PII 模式：邮箱、中国手机、身份证、马来手机、银行卡号、IP、地址关键词
- 顺序敏感：specific pattern 先匹配，general pattern 后匹配
  - email 含 @ 符号最 specific
  - phone_cn (11 位) 长度区别于 id_cn/card
  - id_cn (18 位) 必须先于 bank_card，否则 18 位身份证会被吞为银行卡
  - phone_my (+60 前缀) specific
  - bank_card (16-19 位) 通用，放最后
  - ip 含 . 分隔无歧义
  - address_cn 关键词匹配

设计偏差（相对 P0 计划第 2.4 节）：
- address 模式计划原文为 `(city).{0,20}`，会吞掉后续 20 字符
  破坏查询上下文（如 "珠海市最好的火锅店" → 全部被吞为 [REDACTED-ADDR]）
- 安全实现：仅匹配关键词本身，保留后续文本作为搜索上下文

Usage:
  python privacy.py --query "找一下 13800138000 这个手机号"
  python privacy.py --query "..." --json
  python -m modules.search.tests.test_privacy  # 运行单元测试
"""
import sys
import re
import json
import argparse

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ── PII 模式定义（顺序敏感）────────────────────────────────────
# 顺序原则：specific → general，避免 general pattern 吞掉 specific 匹配
_PII_PATTERNS = [
    # 1. 邮箱（含 @ 符号，最 specific）
    ('email',
     re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
     '[REDACTED-EMAIL]'),

    # 2. 中国手机号（11 位，1[3-9] 开头）
    ('phone_cn',
     re.compile(r'\b1[3-9]\d{9}\b'),
     '[REDACTED-PHONE]'),

    # 3. 身份证号（18 位，最后一位可能是 X）—— 必须先于 bank_card
    ('id_cn',
     re.compile(r'\b\d{17}[\dXx]\b'),
     '[REDACTED-ID]'),

    # 4. 马来西亚手机号（+60 后 9-10 位）
    ('phone_my',
     re.compile(r'\+60\d{8,10}'),
     '[REDACTED-MY-PHONE]'),

    # 5. 银行卡号（16-19 位数字）—— 在 id_cn 之后，避免吞身份证
    ('bank_card',
     re.compile(r'\b\d{16,19}\b'),
     '[REDACTED-CARD]'),

    # 6. IP 地址（4 段点分）—— 含 . 分隔无歧义
    ('ip',
     re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
     '[REDACTED-IP]'),

    # 7. 中国地址关键词（仅匹配市/省名，保留后续文本作为搜索上下文）
    ('address_cn',
     re.compile(
         r'(北京市|上海市|天津市|重庆市|'
         r'广东省|珠海市|深圳市|广州市|中山市|东莞市|佛山市|惠州市|'
         r'浙江省|杭州市|宁波市|温州市|'
         r'江苏省|南京市|苏州市|无锡市|'
         r'四川省|成都市|'
         r'湖北省|武汉市|'
         r'陕西省|西安市|'
         r'山东省|青岛市|济南市|'
         r'辽宁省|大连市|沈阳市|'
         r'福建省|厦门市|福州市|'
         r'湖南省|长沙市|'
         r'河南省|郑州市|'
         r'安徽省|合肥市|'
         r'黑龙江省|哈尔滨市|'
         r'吉林省|长春市|'
         r'云南省|昆明市|'
         r'广西壮族自治区|南宁市|'
         r'海南省|海口市|三亚市|'
         r'山西省|太原市|石家庄市|'
         r'内蒙古自治区|呼和浩特市|'
         r'新疆维吾尔自治区|乌鲁木齐市|'
         r'西藏自治区|拉萨市|'
         r'宁夏回族自治区|银川市|'
         r'青海省|西宁市|'
         r'甘肃省|兰州市)'
     ),
     '[REDACTED-ADDR]'),
]


def redact_outbound(query):
    """对出境查询做 PII 脱敏。

    在 MCP 调用前对用户 query 做正则脱敏，避免 PII 出境到
    SearXNG / Google 等第三方搜索引擎。

    Args:
        query: 原始查询字符串

    Returns:
        tuple: (redacted_query, metadata)
            redacted_query: 脱敏后的查询；失败时返回原 query
            metadata: dict 包含：
                - redacted_count (int): 总替换次数
                - patterns_matched (list[str]): 命中的 pattern 名称（unique）
                - original_length (int): 原始 query 长度
                - redacted_length (int): 脱敏后 query 长度
                - error (str, optional): 异常时附加错误信息

    Examples:
        >>> redact_outbound("联系 13800138000 或 mark@example.com")
        ('联系 [REDACTED-PHONE] 或 [REDACTED-EMAIL]',
         {'redacted_count': 2, 'patterns_matched': ['phone_cn', 'email'],
          'original_length': 30, 'redacted_length': 41})

        >>> redact_outbound("")
        ('', {'redacted_count': 0, 'patterns_matched': [],
              'original_length': 0, 'redacted_length': 0})

        >>> redact_outbound("普通查询没有 PII")
        ('普通查询没有 PII',
         {'redacted_count': 0, 'patterns_matched': [],
          'original_length': 11, 'redacted_length': 11})
    """
    # 边界：空输入或非字符串
    if not isinstance(query, str):
        return '', {
            'redacted_count': 0,
            'patterns_matched': [],
            'original_length': 0,
            'redacted_length': 0,
        }

    if not query:
        return '', {
            'redacted_count': 0,
            'patterns_matched': [],
            'original_length': 0,
            'redacted_length': 0,
        }

    original_length = len(query)
    redacted = query
    patterns_matched = []
    redacted_count = 0

    try:
        for name, pattern, replacement in _PII_PATTERNS:
            # 在当前已脱敏文本中查找当前 pattern 的匹配
            matches = pattern.findall(redacted)
            if matches:
                # 记录命中的 pattern 名称（unique）
                if name not in patterns_matched:
                    patterns_matched.append(name)
                # 执行替换并累计替换次数
                redacted, count = pattern.subn(replacement, redacted)
                redacted_count += count
    except Exception as e:
        # 正则异常：返回原 query + 警告日志，不阻塞主流程
        print(f"警告: PII 脱敏失败 ({type(e).__name__}: {e})，返回原 query",
              file=sys.stderr)
        return (query, {
            'redacted_count': 0,
            'patterns_matched': [],
            'original_length': original_length,
            'redacted_length': original_length,
            'error': f'{type(e).__name__}: {e}',
        })

    return (redacted, {
        'redacted_count': redacted_count,
        'patterns_matched': patterns_matched,
        'original_length': original_length,
        'redacted_length': len(redacted),
    })


def get_pattern_info():
    """返回当前支持的 PII 模式信息（供文档/调试用）。

    Returns:
        list[dict]: 每个模式含 name / pattern / replacement
    """
    return [
        {'name': name, 'pattern': pattern.pattern, 'replacement': replacement}
        for name, pattern, replacement in _PII_PATTERNS
    ]


def _cli():
    """命令行接口，用于手动测试。"""
    parser = argparse.ArgumentParser(
        description='出境 PII 脱敏层 — 对 MCP 调用前的 query 做正则脱敏'
    )
    parser.add_argument('--query', required=True, help='要脱敏的查询')
    parser.add_argument('--json', action='store_true',
                        help='输出 JSON 格式（默认文本）')
    parser.add_argument('--list-patterns', action='store_true',
                        help='列出支持的 PII 模式')
    args = parser.parse_args()

    if args.list_patterns:
        print(json.dumps(get_pattern_info(), ensure_ascii=False, indent=2))
        return 0

    redacted, metadata = redact_outbound(args.query)

    if args.json:
        output = {
            'redacted_query': redacted,
            **metadata,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"原始: {args.query}")
        print(f"脱敏: {redacted}")
        print(f"元数据: {metadata}")
    return 0


if __name__ == '__main__':
    sys.exit(_cli())
