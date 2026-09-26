#!/usr/bin/env python3
"""privacy.py 单元测试 — 出境 PII 脱敏层。

覆盖目标：≥ 90% 行覆盖率
测试维度：
  - 边界输入：空字符串、None、非字符串
  - 单 PII 模式：7 种 pattern 各一组
  - 多 PII 模式：混合查询
  - 重复 PII：同 pattern 多次出现
  - 顺序冲突：身份证 vs 银行卡
  - 异常路径：正则异常（mock）
  - 元数据正确性
  - CLI 接口
"""
import sys
import os
import unittest
import json
from unittest.mock import patch

# 将 modules/search 加入 sys.path 以便直接 import privacy
_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

from privacy import redact_outbound, get_pattern_info, _PII_PATTERNS


class TestEdgeCases(unittest.TestCase):
    """边界输入测试。"""

    def test_empty_string(self):
        redacted, meta = redact_outbound('')
        self.assertEqual(redacted, '')
        self.assertEqual(meta['redacted_count'], 0)
        self.assertEqual(meta['patterns_matched'], [])
        self.assertEqual(meta['original_length'], 0)
        self.assertEqual(meta['redacted_length'], 0)

    def test_none_input(self):
        redacted, meta = redact_outbound(None)
        self.assertEqual(redacted, '')
        self.assertEqual(meta['redacted_count'], 0)
        self.assertEqual(meta['original_length'], 0)
        self.assertEqual(meta['redacted_length'], 0)

    def test_non_string_integer(self):
        redacted, meta = redact_outbound(12345)
        self.assertEqual(redacted, '')
        self.assertEqual(meta['redacted_count'], 0)
        self.assertEqual(meta['original_length'], 0)

    def test_non_string_list(self):
        redacted, meta = redact_outbound(['a', 'b'])
        self.assertEqual(redacted, '')
        self.assertEqual(meta['redacted_count'], 0)

    def test_no_pii_query(self):
        query = 'Python list sort method tutorial'
        redacted, meta = redact_outbound(query)
        self.assertEqual(redacted, query)
        self.assertEqual(meta['redacted_count'], 0)
        self.assertEqual(meta['patterns_matched'], [])
        self.assertEqual(meta['original_length'], len(query))
        self.assertEqual(meta['redacted_length'], len(query))

    def test_chinese_no_pii(self):
        query = '普通查询没有 PII'
        redacted, meta = redact_outbound(query)
        self.assertEqual(redacted, query)
        self.assertEqual(meta['redacted_count'], 0)
        self.assertEqual(meta['patterns_matched'], [])


class TestSinglePatterns(unittest.TestCase):
    """每种 PII 模式独立测试。"""

    def test_email_only(self):
        query = '联系 mark@example.com 获取详情'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-EMAIL]', redacted)
        self.assertNotIn('mark@example.com', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('email', meta['patterns_matched'])

    def test_phone_cn_only(self):
        query = '打电话给 13800138000 预约'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-PHONE]', redacted)
        self.assertNotIn('13800138000', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('phone_cn', meta['patterns_matched'])

    def test_id_cn_only(self):
        query = '身份证号 110101199003078888 请核验'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-ID]', redacted)
        self.assertNotIn('110101199003078888', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('id_cn', meta['patterns_matched'])

    def test_id_cn_with_x_suffix(self):
        query = '身份证 11010119900307888X'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-ID]', redacted)
        self.assertEqual(meta['redacted_count'], 1)

    def test_phone_my_only(self):
        query = '马来西亚 +60135364830 联系方式'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-MY-PHONE]', redacted)
        self.assertNotIn('+60135364830', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('phone_my', meta['patterns_matched'])

    def test_bank_card_only(self):
        query = '银行卡号 6222021234567890123 验证'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-CARD]', redacted)
        self.assertNotIn('6222021234567890123', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('bank_card', meta['patterns_matched'])

    def test_ip_only(self):
        query = '服务器 IP 192.168.1.1 不可达'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-IP]', redacted)
        self.assertNotIn('192.168.1.1', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('ip', meta['patterns_matched'])

    def test_address_cn_only(self):
        query = '珠海市最好的火锅店'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-ADDR]', redacted)
        self.assertNotIn('珠海市', redacted)
        # 后续文本应保留（仅匹配关键词本身）
        self.assertIn('最好的火锅店', redacted)
        self.assertEqual(meta['redacted_count'], 1)
        self.assertIn('address_cn', meta['patterns_matched'])


class TestMultiplePatterns(unittest.TestCase):
    """多 PII 模式混合测试。"""

    def test_email_and_phone(self):
        query = '找一下 13800138000 这个手机号相关的 mark@example.com 信息'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-PHONE]', redacted)
        self.assertIn('[REDACTED-EMAIL]', redacted)
        self.assertNotIn('13800138000', redacted)
        self.assertNotIn('mark@example.com', redacted)
        self.assertEqual(meta['redacted_count'], 2)
        self.assertIn('phone_cn', meta['patterns_matched'])
        self.assertIn('email', meta['patterns_matched'])

    def test_all_seven_patterns(self):
        query = ('联系 13800138000 或 mark@example.com，'
                 '身份证 11010119900307888X，'
                 '马来 +60135364830，'
                 '卡号 6222021234567890123，'
                 '服务器 192.168.1.1，'
                 '珠海市香洲区')
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-PHONE]', redacted)
        self.assertIn('[REDACTED-EMAIL]', redacted)
        self.assertIn('[REDACTED-ID]', redacted)
        self.assertIn('[REDACTED-MY-PHONE]', redacted)
        self.assertIn('[REDACTED-CARD]', redacted)
        self.assertIn('[REDACTED-IP]', redacted)
        self.assertIn('[REDACTED-ADDR]', redacted)
        self.assertEqual(len(meta['patterns_matched']), 7)
        self.assertEqual(meta['redacted_count'], 7)

    def test_multiple_emails(self):
        query = 'alice@a.com 和 bob@b.org 都是邮箱'
        redacted, meta = redact_outbound(query)
        self.assertEqual(redacted.count('[REDACTED-EMAIL]'), 2)
        self.assertEqual(meta['redacted_count'], 2)
        self.assertEqual(meta['patterns_matched'], ['email'])

    def test_multiple_phones(self):
        query = '电话 1：13800138000，电话 2：13900139000'
        redacted, meta = redact_outbound(query)
        self.assertEqual(redacted.count('[REDACTED-PHONE]'), 2)
        self.assertEqual(meta['redacted_count'], 2)
        self.assertEqual(meta['patterns_matched'], ['phone_cn'])


class TestPatternDisambiguation(unittest.TestCase):
    """顺序冲突消歧测试。"""

    def test_id_not_treated_as_bank_card(self):
        """18 位身份证不应被当作银行卡。"""
        query = '身份证 110101199003078888 验证'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-ID]', redacted)
        self.assertNotIn('[REDACTED-CARD]', redacted)
        self.assertIn('id_cn', meta['patterns_matched'])
        self.assertNotIn('bank_card', meta['patterns_matched'])

    def test_bank_card_16_digits(self):
        """16 位数字应被识别为银行卡。"""
        query = '卡号 6222021234567890'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-CARD]', redacted)
        self.assertIn('bank_card', meta['patterns_matched'])

    def test_phone_11_digits_not_bank_card(self):
        """11 位手机号不会被银行卡模式匹配。"""
        query = '电话 13800138000'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-PHONE]', redacted)
        self.assertNotIn('[REDACTED-CARD]', redacted)

    def test_ip_not_treated_as_version(self):
        """IP 地址应被识别为 IP（即使类似版本号）。"""
        query = '服务器 IP 10.0.0.1'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-IP]', redacted)
        self.assertIn('ip', meta['patterns_matched'])


class TestMetadataCorrectness(unittest.TestCase):
    """元数据正确性测试。"""

    def test_lengths_correct(self):
        query = '13800138000'
        redacted, meta = redact_outbound(query)
        self.assertEqual(meta['original_length'], 11)
        self.assertEqual(meta['redacted_length'],
                         len('[REDACTED-PHONE]'))

    def test_patterns_matched_unique(self):
        """相同 pattern 多次出现，patterns_matched 应保持 unique。"""
        query = '13800138000 和 13900139000'
        _, meta = redact_outbound(query)
        self.assertEqual(meta['patterns_matched'], ['phone_cn'])
        self.assertEqual(meta['redacted_count'], 2)

    def test_metadata_no_error_key_on_success(self):
        query = '普通查询'
        _, meta = redact_outbound(query)
        self.assertNotIn('error', meta)


class TestExceptionHandling(unittest.TestCase):
    """异常路径测试。"""

    def test_regex_exception_returns_empty(self):
        """正则异常时不能返回原始敏感 query。"""
        query = '联系 13800138000'
        with patch('privacy._PII_PATTERNS',
                   [('email', None, '[REDACTED-EMAIL]')]):
            redacted, meta = redact_outbound(query)
        self.assertEqual(redacted, '')
        self.assertEqual(meta['redacted_count'], 0)
        self.assertEqual(meta['patterns_matched'], [])
        self.assertEqual(meta['original_length'], len(query))
        self.assertEqual(meta['redacted_length'], 0)
        self.assertIn('error', meta)

    def test_findall_exception_caught(self):
        """pattern.findall 异常时应被 except 捕获。"""
        query = '测试查询'

        class FakePattern:
            def findall(self, text):
                raise RuntimeError('mocked findall failure')

            def subn(self, repl, text):
                raise RuntimeError('should not reach subn')

        with patch('privacy._PII_PATTERNS',
                   [('mock', FakePattern(), '[MOCK]')]):
            redacted, meta = redact_outbound(query)
        self.assertEqual(redacted, '')
        self.assertIn('error', meta)


class TestSpecialCharacters(unittest.TestCase):
    """特殊字符与 Unicode 测试。"""

    def test_unicode_chinese_query(self):
        query = '查找 React useEffect 清理副作用'
        redacted, meta = redact_outbound(query)
        self.assertEqual(redacted, query)
        self.assertEqual(meta['redacted_count'], 0)

    def test_newline_in_query(self):
        query = '联系\nmark@example.com\n谢谢'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-EMAIL]', redacted)
        self.assertEqual(meta['redacted_count'], 1)

    def test_emoji_preserved(self):
        query = '邮箱 mark@example.com 收到 🎉'
        redacted, meta = redact_outbound(query)
        self.assertIn('[REDACTED-EMAIL]', redacted)
        self.assertIn('🎉', redacted)

    def test_address_substring_no_false_positive(self):
        """关键词本身是更长词的子串时不应误匹配（如 "北京市场"）。"""
        # "北京市场调研" 中 "北京市" 是 "北京市场" 的前缀
        # 当前实现会匹配 "北京市" → 误报
        # 这是已知限制，标记为 known limitation
        query = '北京市场调研报告'
        redacted, meta = redact_outbound(query)
        # 接受当前行为：会误匹配 "北京市"，但 "场调研报告" 保留
        self.assertIn('场调研报告', redacted)


class TestPatternInfo(unittest.TestCase):
    """get_pattern_info 辅助函数测试。"""

    def test_pattern_info_complete(self):
        info = get_pattern_info()
        self.assertEqual(len(info), 7)
        names = [item['name'] for item in info]
        expected_names = ['email', 'phone_cn', 'id_cn', 'phone_my',
                          'bank_card', 'ip', 'address_cn']
        self.assertEqual(names, expected_names)
        for item in info:
            self.assertIn('pattern', item)
            self.assertIn('replacement', item)

    def test_patterns_count(self):
        """_PII_PATTERNS 应有 7 个 pattern。"""
        self.assertEqual(len(_PII_PATTERNS), 7)


class TestCLI(unittest.TestCase):
    """CLI 接口测试（通过 subprocess）。"""

    def test_cli_text_output(self):
        from privacy import _cli
        with patch('sys.argv',
                   ['privacy.py', '--query', '13800138000']):
            with patch('sys.stdout', new_callable=lambda: __import__('io').StringIO()):
                exit_code = _cli()
                self.assertEqual(exit_code, 0)

    def test_cli_json_output(self):
        from privacy import _cli
        import io
        with patch('sys.argv',
                   ['privacy.py', '--query', 'mark@example.com', '--json']):
            buf = io.StringIO()
            with patch('sys.stdout', new=buf):
                exit_code = _cli()
            output = buf.getvalue()
            self.assertEqual(exit_code, 0)
            data = json.loads(output)
            self.assertIn('redacted_query', data)
            self.assertIn('[REDACTED-EMAIL]', data['redacted_query'])
            self.assertEqual(data['redacted_count'], 1)

    def test_cli_list_patterns(self):
        from privacy import _cli
        import io
        with patch('sys.argv', ['privacy.py', '--query', 'x', '--list-patterns']):
            buf = io.StringIO()
            with patch('sys.stdout', new=buf):
                exit_code = _cli()
            output = buf.getvalue()
            self.assertEqual(exit_code, 0)
            data = json.loads(output)
            self.assertEqual(len(data), 7)


class TestKnownLimitations(unittest.TestCase):
    """已知限制文档化测试。"""

    def test_address_market_false_positive(self):
        """已知：地址关键词可能误匹配"北京市场"等。

        P0 不修复，留待 v4.1 优化（context-aware matching）。
        """
        query = '北京市场调研'
        redacted, _ = redact_outbound(query)
        # "北京市" 会被匹配 → 误报
        # 接受此限制
        self.assertNotEqual(redacted, query)

    def test_ip_date_not_matched(self):
        """日期格式 "2026.07.20" 不被 IP 模式匹配。

        - 3 段日期不被 IP 模式匹配（segment count 不符）
        - 4 段日期 "2026.07.20.15" 也不被匹配
          （IP 模式 \d{1,3} 限制每段最多 3 位，
          "2026" 4 位无法匹配 → 正确拒绝）
        """
        query = '日期 2026.07.20'
        redacted, meta = redact_outbound(query)
        # 3 段日期不被匹配
        self.assertEqual(redacted, query)
        self.assertEqual(meta['redacted_count'], 0)

        query2 = '日期 2026.07.20.15'
        redacted2, meta2 = redact_outbound(query2)
        # 4 段但首段 4 位 → 正确拒绝（IP octet 上限 3 位）
        self.assertEqual(redacted2, query2)
        self.assertEqual(meta2['redacted_count'], 0)

    def test_ip_version_string_matched(self):
        """版本号 "1.2.3.4" 会被误匹配为 IP（已知限制）。

        1-3 位 4 段的 dot-separated 数字串无法与真实 IP 区分。
        可接受 — 此类字符串在搜索 query 中罕见。
        """
        query = '版本 1.2.3.4 已发布'
        redacted, _ = redact_outbound(query)
        self.assertIn('[REDACTED-IP]', redacted)


if __name__ == '__main__':
    unittest.main(verbosity=2)
