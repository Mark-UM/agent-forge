#!/usr/bin/env python3
"""quality.py 单元测试 — 双轨评分 + 缓存 + 降级。

覆盖目标：≥ 85% 行覆盖率
测试维度：
  - 边界输入：空 query、空 results、非字符串
  - 启发式评分：4 维度独立验证 + 总分范围
  - Flash 评分：mock API 调用 + JSON 解析 + 缓存
  - 降级链：Flash 失败 → heuristic
  - 缓存：命中 / 未命中 / 过期 / 写入失败
  - CLI 接口
"""
import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import quality
from quality import (
    score_results, _heuristic_score, _cache_key, _load_cache, _save_cache,
    _cache_get, _cache_store, _call_flash_api, _build_flash_prompt,
    _score_relevance, _score_authoritativeness, _score_freshness,
    _score_diversity,
)
# v4.4 P4.1.1: 权威性分层测试
from quality import (
    _classify_authority, _load_authority_whitelist, _try_promote_github_org,
    AUTHORITY_TIERS, GITHUB_ORG_WHITELIST, _AUTHORITY_MAP,
)


# ── 测试数据 ────────────────────────────────────────────────────
SAMPLE_RESULTS = [
    {
        'title': 'React useEffect Cleanup Best Practices (2026)',
        'url': 'https://react.dev/docs/hooks-effect',
        'snippet': 'useEffect cleanup function runs on unmount or deps change. 2026 guide.',
        'source': 'official-docs',
    },
    {
        'title': 'Understanding React Hooks — useEffect Tutorial',
        'url': 'https://dev.to/react/useeffect',
        'snippet': 'A deep dive into useEffect and cleanup functions',
        'source': 'blog',
    },
    {
        'title': 'Stack Overflow: useEffect cleanup',
        'url': 'https://stackoverflow.com/q/123456',
        'snippet': 'Community answer about useEffect cleanup',
        'source': 'stackoverflow',
    },
]


class TestEdgeCases(unittest.TestCase):
    """边界输入测试。"""

    def test_empty_query(self):
        result = score_results('', SAMPLE_RESULTS, mode='heuristic')
        self.assertEqual(result['score'], 0.0)
        self.assertEqual(result['mode'], 'heuristic')

    def test_none_query(self):
        result = score_results(None, SAMPLE_RESULTS, mode='heuristic')
        self.assertEqual(result['score'], 0.0)

    def test_empty_results(self):
        result = score_results('React useEffect', [], mode='heuristic')
        self.assertEqual(result['score'], 0.0)
        self.assertIn('No results', result['rationale'])

    def test_none_results(self):
        result = score_results('React useEffect', None, mode='heuristic')
        self.assertEqual(result['score'], 0.0)

    def test_non_list_results(self):
        result = score_results('React', {'not': 'a list'}, mode='heuristic')
        self.assertEqual(result['score'], 0.0)


class TestHeuristicScore(unittest.TestCase):
    """启发式评分测试。"""

    def test_score_in_range(self):
        result = _heuristic_score('React useEffect cleanup', SAMPLE_RESULTS)
        self.assertGreaterEqual(result['score'], 0.0)
        self.assertLessEqual(result['score'], 10.0)

    def test_dimensions_sum_to_score(self):
        result = _heuristic_score('React useEffect', SAMPLE_RESULTS)
        dims = result['dimensions']
        expected_sum = (dims['relevance'] + dims['authoritativeness'] +
                        dims['freshness'] + dims['diversity'])
        self.assertAlmostEqual(result['score'], round(expected_sum, 2), places=1)

    def test_dimensions_in_range(self):
        result = _heuristic_score('React useEffect', SAMPLE_RESULTS)
        dims = result['dimensions']
        self.assertGreaterEqual(dims['relevance'], 0.0)
        self.assertLessEqual(dims['relevance'], 4.0)
        self.assertGreaterEqual(dims['authoritativeness'], 0.0)
        self.assertLessEqual(dims['authoritativeness'], 3.0)
        self.assertGreaterEqual(dims['freshness'], 0.0)
        self.assertLessEqual(dims['freshness'], 2.0)
        self.assertGreaterEqual(dims['diversity'], 0.0)
        self.assertLessEqual(dims['diversity'], 1.0)

    def test_mode_is_heuristic(self):
        result = _heuristic_score('React', SAMPLE_RESULTS)
        self.assertEqual(result['mode'], 'heuristic')

    def test_rationale_present(self):
        result = _heuristic_score('React', SAMPLE_RESULTS)
        self.assertTrue(result['rationale'])


class TestRelevanceScoring(unittest.TestCase):
    """相关性维度测试。"""

    def test_high_relevance(self):
        """关键词全部命中 → 高分。"""
        results = [{
            'title': 'React useEffect cleanup',
            'url': 'https://example.com',
            'snippet': 'useEffect cleanup best practices',
            'source': 'blog',
        }]
        score = _score_relevance('React useEffect cleanup', results)
        self.assertGreater(score, 2.0)

    def test_low_relevance(self):
        """关键词零命中 → 低分。"""
        results = [{
            'title': 'Python list sort method',
            'url': 'https://example.com',
            'snippet': 'How to sort lists in Python',
            'source': 'blog',
        }]
        score = _score_relevance('React useEffect cleanup', results)
        self.assertLess(score, 1.0)

    def test_stop_words_filtered(self):
        """停用词不计入关键词。"""
        results = [{
            'title': 'the a an of and',
            'url': 'https://example.com',
            'snippet': '',
            'source': 'blog',
        }]
        # 全是停用词 → 无法提取关键词 → 返回中等分 2.0
        score = _score_relevance('the a an of and', results)
        self.assertEqual(score, 2.0)


class TestAuthoritativenessScoring(unittest.TestCase):
    """权威性维度测试。"""

    def test_official_docs_high_score(self):
        results = [{'url': 'https://react.dev/docs/x', 'source': 'official-docs'}]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 3.0)

    def test_stackoverflow_medium_score(self):
        # v4.4 P4.1.1: stackoverflow 从 L4 降到 score=1.0（不再是 2.0）
        # 理由：社区答案权威性低于官方文档，避免学生笔记 / 社区帖子
        # 覆盖官方 handbook 的同源问题。
        results = [{'url': 'https://stackoverflow.com/q/1', 'source': 'stackoverflow'}]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 1.0)

    def test_gov_domain_max_score(self):
        results = [{'url': 'https://example.gov/policy', 'source': 'unknown'}]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 3.0)

    def test_edu_domain_max_score(self):
        results = [{'url': 'https://university.edu/research', 'source': 'unknown'}]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 3.0)

    def test_docs_subdomain_max_score(self):
        results = [{'url': 'https://docs.python.org/3/', 'source': 'unknown'}]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 3.0)


# ── v4.4 P4.1.1: 权威性分层单元测试 ─────────────────────────────

class TestClassifyAuthorityL1(unittest.TestCase):
    """L1 官方权威源分层测试。"""

    def test_handbook_monash_edu(self):
        """Monash handbook 必须识别为 L1（Agent-Forge 错误的核心案例）。"""
        result = _classify_authority('https://handbook.monash.edu/2026/units/FIT2004')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_moodle_monash_edu(self):
        result = _classify_authority('https://moodle.monash.edu/course/FIT2004')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_monash_edu_root(self):
        result = _classify_authority('https://monash.edu/')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_docs_python_org(self):
        result = _classify_authority('https://docs.python.org/3/library/os.html')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_react_dev(self):
        result = _classify_authority('https://react.dev/docs/hooks-effect')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_mdn(self):
        result = _classify_authority('https://developer.mozilla.org/en-US/docs/Web/JavaScript')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_arxiv_org(self):
        """arXiv 在 L1 whitelist（学术权威源）。"""
        result = _classify_authority('https://arxiv.org/abs/1706.03762')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_semanticscholar_org(self):
        result = _classify_authority('https://www.semanticscholar.org/paper/BERT')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_gov_tld(self):
        """通用 .gov TLD → L1。"""
        result = _classify_authority('https://example.gov/policy')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)

    def test_edu_tld(self):
        """通用 .edu TLD → L1。"""
        result = _classify_authority('https://university.edu/research')
        self.assertEqual(result['tier'], 'L1')
        self.assertEqual(result['score'], 3)


class TestClassifyAuthorityL2(unittest.TestCase):
    """L2 教师 / 官方组织分层测试。"""

    def test_users_monash_edu(self):
        """教师个人页面（users.monash.edu）是 L2，不是 L1。"""
        result = _classify_authority('https://users.monash.edu/~rafael/')
        self.assertEqual(result['tier'], 'L2')
        self.assertEqual(result['score'], 2)

    def test_apache_org(self):
        result = _classify_authority('https://apache.org/')
        self.assertEqual(result['tier'], 'L2')
        self.assertEqual(result['score'], 2)

    def test_org_tld(self):
        """通用 .org TLD → L2。"""
        result = _classify_authority('https://example.org/')
        self.assertEqual(result['tier'], 'L2')
        self.assertEqual(result['score'], 2)

    def test_github_official_org_promoted_to_l2(self):
        """GitHub 官方组织白名单（如 github.com/python）→ 提升到 L2。"""
        result = _classify_authority('https://github.com/python/cpython')
        self.assertEqual(result['tier'], 'L2')
        self.assertEqual(result['score'], 2)

    def test_github_microsoft_org_promoted(self):
        result = _classify_authority('https://github.com/microsoft/TypeScript')
        self.assertEqual(result['tier'], 'L2')
        self.assertEqual(result['score'], 2)


class TestClassifyAuthorityL3(unittest.TestCase):
    """L3 学生笔记 / 个人 GitHub 分层测试（关键修复点）。"""

    def test_student_github_repo(self):
        """学生 GitHub 仓库（jenul-ferdinand）必须是 L3（Agent-Forge 错误源）。"""
        result = _classify_authority('https://github.com/jenul-ferdinand/algorithms')
        self.assertEqual(result['tier'], 'L3')
        self.assertEqual(result['score'], 1)

    def test_student_github_repo_2(self):
        """另一个学生笔记仓库 cjmlgrto/fit2004-notes。"""
        result = _classify_authority('https://github.com/cjmlgrto/fit2004-notes')
        self.assertEqual(result['tier'], 'L3')
        self.assertEqual(result['score'], 1)

    def test_handbook_scraper_github(self):
        """handbook scraper 仓库（cherryblossom000/macathon-2023）是 L3。"""
        result = _classify_authority('https://github.com/cherryblossom000/macathon-2023')
        self.assertEqual(result['tier'], 'L3')
        self.assertEqual(result['score'], 1)

    def test_github_source_field(self):
        """source=github → L3 (1 分)，不再是 3 分。"""
        result = _classify_authority('https://unknown.example.com/', source='github')
        self.assertEqual(result['tier'], 'L3')
        self.assertEqual(result['score'], 1)

    def test_personal_source_field(self):
        result = _classify_authority('https://example.com/', source='personal')
        self.assertEqual(result['tier'], 'L3')
        self.assertEqual(result['score'], 1)

    def test_gist_github(self):
        result = _classify_authority('https://gist.github.com/anonymous/abc123')
        self.assertEqual(result['tier'], 'L3')
        self.assertEqual(result['score'], 1)


class TestClassifyAuthorityL4(unittest.TestCase):
    """L4 第三方博客 / 论坛分层测试。"""

    def test_medium_com(self):
        """Medium 博客从 L4 (2 分) 降到 1 分。"""
        result = _classify_authority('https://medium.com/@user/article')
        self.assertEqual(result['tier'], 'L4')
        self.assertEqual(result['score'], 1)

    def test_dev_to(self):
        result = _classify_authority('https://dev.to/react/useeffect')
        self.assertEqual(result['tier'], 'L4')
        self.assertEqual(result['score'], 1)

    def test_reddit_com(self):
        result = _classify_authority('https://reddit.com/r/programming')
        self.assertEqual(result['tier'], 'L4')
        self.assertEqual(result['score'], 1)

    def test_stackoverflow_com(self):
        """stackoverflow.com → L4 (1 分)，不再是 2 分。"""
        result = _classify_authority('https://stackoverflow.com/q/123456')
        self.assertEqual(result['tier'], 'L4')
        self.assertEqual(result['score'], 1)

    def test_zhihu_com(self):
        result = _classify_authority('https://zhihu.com/question/123')
        self.assertEqual(result['tier'], 'L4')
        self.assertEqual(result['score'], 1)

    def test_blog_source_field(self):
        """source=blog → L4 (1 分)，不再是 2 分。"""
        result = _classify_authority('https://example.com/', source='blog')
        self.assertEqual(result['tier'], 'L4')
        self.assertEqual(result['score'], 1)


class TestClassifyAuthorityEdgeCases(unittest.TestCase):
    """_classify_authority 边界情况测试。"""

    def test_empty_url(self):
        result = _classify_authority('')
        self.assertEqual(result['tier'], 'unknown')
        self.assertEqual(result['score'], 0)

    def test_none_url(self):
        result = _classify_authority(None)
        self.assertEqual(result['tier'], 'unknown')
        self.assertEqual(result['score'], 0)

    def test_unknown_domain(self):
        result = _classify_authority('https://random.example.xyz/')
        self.assertEqual(result['tier'], 'unknown')
        self.assertEqual(result['score'], 0)

    def test_www_prefix_stripped(self):
        """www. 前缀应被正确处理。"""
        result = _classify_authority('https://www.handbook.monash.edu/')
        self.assertEqual(result['tier'], 'L1')

    def test_subdomain_of_l1(self):
        """L1 域名的子域名也是 L1。"""
        result = _classify_authority('https://sub.handbook.monash.edu/')
        self.assertEqual(result['tier'], 'L1')

    def test_url_parse_error_returns_unknown(self):
        """URL parse 异常时返回 unknown（不抛异常）。"""
        result = _classify_authority('://invalid-url', source='unknown')
        # 不应抛异常
        self.assertIn(result['tier'], ['unknown', 'L4'])

    def test_returns_reason_field(self):
        """返回值包含 reason 字段（debugging 用）。"""
        result = _classify_authority('https://handbook.monash.edu/')
        self.assertIn('reason', result)
        self.assertIsInstance(result['reason'], str)


class TestAuthorityWhitelistLoading(unittest.TestCase):
    """authority_whitelist.json 加载测试。"""

    def setUp(self):
        # 重置缓存
        import quality
        quality._WHITELIST_CACHE = {'loaded': False, 'tiers': None, 'github_orgs': None}

    def test_load_succeeds_when_file_exists(self):
        """markconfig/authority_whitelist.json 已创建，应成功加载。"""
        tiers, github_orgs = _load_authority_whitelist()
        # 文件存在 → tiers 必须非 None（v4.4: 修正 Review-Code MINOR #5 弱断言）
        self.assertIsNotNone(tiers, 'authority_whitelist.json 应存在且可解析')
        self.assertIn('L1', tiers)
        self.assertIn('L2', tiers)
        self.assertIn('L3', tiers)
        self.assertIn('L4', tiers)
        self.assertIsInstance(github_orgs, set)

    def test_cache_loaded_flag_set(self):
        """首次加载后 _WHITELIST_CACHE.loaded = True（避免重复 IO）。"""
        _load_authority_whitelist()
        import quality
        self.assertTrue(quality._WHITELIST_CACHE['loaded'])

    def test_github_org_whitelist_loaded(self):
        """github_org_whitelist 字段被正确加载为 set。"""
        tiers, github_orgs = _load_authority_whitelist()
        if github_orgs:
            self.assertIn('python', github_orgs)
            self.assertIn('microsoft', github_orgs)

    def test_corrupted_whitelist_falls_back(self):
        """whitelist 文件损坏 → fallback 到默认 AUTHORITY_TIERS（不抛异常）。"""
        import quality
        # 模拟文件损坏
        original_path = quality._WHITELIST_FILE
        quality._WHITELIST_FILE = '/nonexistent/path/authority_whitelist.json'
        quality._WHITELIST_CACHE = {'loaded': False, 'tiers': None, 'github_orgs': None}

        tiers, github_orgs = _load_authority_whitelist()
        self.assertIsNone(tiers)
        self.assertIsNone(github_orgs)

        # 恢复
        quality._WHITELIST_FILE = original_path
        quality._WHITELIST_CACHE = {'loaded': False, 'tiers': None, 'github_orgs': None}


class TestTryPromoteGithubOrg(unittest.TestCase):
    """GitHub 官方组织提升测试。"""

    def test_python_org_promoted(self):
        result = _try_promote_github_org(
            'https://github.com/python/cpython', {'python', 'microsoft'})
        self.assertIsNotNone(result)
        self.assertEqual(result['tier'], 'L2')
        self.assertEqual(result['score'], 2)

    def test_student_repo_not_promoted(self):
        """学生仓库（jenul-ferdinand）不在白名单 → 不提升。"""
        result = _try_promote_github_org(
            'https://github.com/jenul-ferdinand/algorithms',
            {'python', 'microsoft'})
        self.assertIsNone(result)

    def test_empty_whitelist_returns_none(self):
        result = _try_promote_github_org('https://github.com/python/cpython', set())
        self.assertIsNone(result)

    def test_no_path_returns_none(self):
        result = _try_promote_github_org('https://github.com/', {'python'})
        self.assertIsNone(result)

    def test_case_insensitive_org_match(self):
        """组织名大小写不敏感。"""
        result = _try_promote_github_org(
            'https://github.com/Python/cpython', {'python'})
        self.assertIsNotNone(result)
        self.assertEqual(result['tier'], 'L2')


class TestAuthorityTierVsLegacyMap(unittest.TestCase):
    """v4.4 新分层 vs 旧 _AUTHORITY_MAP 行为对比测试。"""

    def test_github_score_decreased_from_3_to_1(self):
        """github 从 3 → 1（关键修复）。"""
        # 旧 _AUTHORITY_MAP 仍保留 'github': 1 以兼容
        self.assertEqual(_AUTHORITY_MAP['github'], 1)
        # 新分层也是 1
        result = _classify_authority('https://github.com/student/notes', source='github')
        self.assertEqual(result['score'], 1)

    def test_blog_score_decreased_from_2_to_1(self):
        self.assertEqual(_AUTHORITY_MAP['blog'], 1)
        result = _classify_authority('https://medium.com/@user/post', source='blog')
        self.assertEqual(result['score'], 1)

    def test_official_docs_score_unchanged(self):
        """official-docs 仍是 3 分（保持不变）。"""
        self.assertEqual(_AUTHORITY_MAP['official-docs'], 3)
        result = _classify_authority('https://react.dev/', source='official-docs')
        self.assertEqual(result['score'], 3)


class TestScoreAuthoritativenessWithTiers(unittest.TestCase):
    """_score_authoritativeness 集成分层测试。"""

    def test_mixed_tiers_averaged(self):
        """混合 L1 + L3 → 平均分。"""
        results = [
            {'url': 'https://handbook.monash.edu/', 'source': 'unknown'},      # L1=3
            {'url': 'https://github.com/student/notes', 'source': 'github'},    # L3=1
        ]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 2.0)  # (3+1)/2

    def test_all_l1_max_score(self):
        """全 L1 → 满分 3.0。"""
        results = [
            {'url': 'https://handbook.monash.edu/', 'source': 'unknown'},
            {'url': 'https://docs.python.org/', 'source': 'unknown'},
        ]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 3.0)

    def test_all_l3_low_score(self):
        """全 L3 学生笔记 → 1.0（Agent-Forge 错误场景模拟）。"""
        results = [
            {'url': 'https://github.com/jenul-ferdinand/algorithms', 'source': 'github'},
            {'url': 'https://github.com/cjmlgrto/fit2004-notes', 'source': 'github'},
        ]
        score = _score_authoritativeness(results)
        self.assertEqual(score, 1.0)


class TestFreshnessScoring(unittest.TestCase):
    """时效性维度测试。"""

    def test_current_year_max_score(self):
        results = [{
            'url': 'https://example.com/2026/guide',
            'snippet': 'Updated in 2026',
            'source': 'blog',
        }]
        score = _score_freshness(results)
        self.assertEqual(score, 2.0)

    def test_old_year_low_score(self):
        results = [{
            'url': 'https://example.com/2015/old',
            'snippet': 'From 2015',
            'source': 'blog',
        }]
        score = _score_freshness(results)
        self.assertEqual(score, 0.0)

    def test_no_year_baseline_score(self):
        results = [{
            'url': 'https://example.com/no-date',
            'snippet': 'No year mentioned',
            'source': 'blog',
        }]
        score = _score_freshness(results)
        self.assertEqual(score, 0.5)

    def test_invalid_year_filtered(self):
        """年份 < 2010 或 > 2026 被过滤。"""
        results = [{
            'url': 'https://example.com/1999',
            'snippet': 'Year 1999 or 9999',
            'source': 'blog',
        }]
        score = _score_freshness(results)
        self.assertEqual(score, 0.5)  # 无有效年份 → 基础分


class TestDiversityScoring(unittest.TestCase):
    """多样性维度测试。"""

    def test_all_different_domains_max_score(self):
        results = [
            {'url': 'https://a.com/1'},
            {'url': 'https://b.com/2'},
            {'url': 'https://c.com/3'},
        ]
        score = _score_diversity(results)
        self.assertEqual(score, 1.0)

    def test_all_same_domain_low_score(self):
        results = [
            {'url': 'https://same.com/1'},
            {'url': 'https://same.com/2'},
            {'url': 'https://same.com/3'},
        ]
        score = _score_diversity(results)
        self.assertAlmostEqual(score, 1 / 3, places=2)


class TestFlashMode(unittest.TestCase):
    """Flash 模式评分测试（mock API）。"""

    def test_flash_success(self):
        """Flash 成功 → 返回 Flash 评分。"""
        mock_response = {
            'choices': [{
                'message': {
                    'content': json.dumps({
                        'score': 8.5,
                        'dimensions': {
                            'relevance': 3.5,
                            'authoritativeness': 2.5,
                            'freshness': 2.0,
                            'diversity': 0.5,
                        },
                        'rationale': 'High quality results with good coverage',
                    })
                }
            }]
        }
        with patch('quality.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = score_results('React useEffect', SAMPLE_RESULTS,
                                       mode='flash', use_cache=False)
        self.assertEqual(result['mode'], 'flash')
        self.assertEqual(result['score'], 8.5)
        self.assertEqual(result['dimensions']['relevance'], 3.5)

    def test_flash_markdown_fences_stripped(self):
        """模型输出 markdown fences 也能正确解析。"""
        raw_json = json.dumps({
            'score': 7.0,
            'dimensions': {'relevance': 3.0, 'authoritativeness': 2.0,
                           'freshness': 1.0, 'diversity': 1.0},
            'rationale': 'Good results',
        })
        wrapped = f'```json\n{raw_json}\n```'
        mock_response = {
            'choices': [{'message': {'content': wrapped}}]
        }
        with patch('quality.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = score_results('React', SAMPLE_RESULTS,
                                       mode='flash', use_cache=False)
        self.assertEqual(result['mode'], 'flash')
        self.assertEqual(result['score'], 7.0)

    def test_flash_no_api_key_falls_back(self):
        """无 API key → 降级到 heuristic。"""
        with patch.dict(os.environ, {}, clear=True):
            # 清空所有可能的 LLM API key 环境变量
            os.environ.pop('DEEPSEEK_API_KEY', None)
            result = score_results('React', SAMPLE_RESULTS,
                                   mode='flash', use_cache=False)
        self.assertEqual(result['mode'], 'flash-fallback-heuristic')
        self.assertIn('error', result)

    def test_flash_http_error_falls_back(self):
        """HTTP 错误 → 降级到 heuristic。"""
        import urllib.error
        with patch('quality.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError(
                       'url', 500, 'Server Error', {}, None)):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = score_results('React', SAMPLE_RESULTS,
                                       mode='flash', use_cache=False)
        self.assertEqual(result['mode'], 'flash-fallback-heuristic')
        self.assertIn('error', result)

    def test_flash_json_parse_error_falls_back(self):
        """JSON 解析失败 → 降级到 heuristic。"""
        mock_response = {
            'choices': [{'message': {'content': 'not valid json'}}]
        }
        with patch('quality.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = score_results('React', SAMPLE_RESULTS,
                                       mode='flash', use_cache=False)
        self.assertEqual(result['mode'], 'flash-fallback-heuristic')

    def test_flash_score_clamped_to_10(self):
        """score > 10 被截断到 10。"""
        mock_response = {
            'choices': [{
                'message': {
                    'content': json.dumps({
                        'score': 15.0,
                        'dimensions': {'relevance': 4.0, 'authoritativeness': 3.0,
                                       'freshness': 2.0, 'diversity': 1.0},
                        'rationale': 'Test',
                    })
                }
            }]
        }
        with patch('quality.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = score_results('React', SAMPLE_RESULTS,
                                       mode='flash', use_cache=False)
        self.assertEqual(result['score'], 10.0)

    def test_flash_dimension_clamped(self):
        """维度评分超出范围被截断。"""
        mock_response = {
            'choices': [{
                'message': {
                    'content': json.dumps({
                        'score': 10.0,
                        'dimensions': {'relevance': 10.0, 'authoritativeness': 10.0,
                                       'freshness': 10.0, 'diversity': 10.0},
                        'rationale': 'Test',
                    })
                }
            }]
        }
        with patch('quality.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = score_results('React', SAMPLE_RESULTS,
                                       mode='flash', use_cache=False)
        self.assertEqual(result['dimensions']['relevance'], 4.0)
        self.assertEqual(result['dimensions']['authoritativeness'], 3.0)
        self.assertEqual(result['dimensions']['freshness'], 2.0)
        self.assertEqual(result['dimensions']['diversity'], 1.0)


class TestCache(unittest.TestCase):
    """缓存测试。"""

    def setUp(self):
        # 每个测试前清空缓存
        with patch('quality.os.path.exists', return_value=False):
            pass

    def test_cache_key_deterministic(self):
        """相同输入产生相同 cache key。"""
        key1 = _cache_key('React', SAMPLE_RESULTS)
        key2 = _cache_key('React', SAMPLE_RESULTS)
        self.assertEqual(key1, key2)

    def test_cache_key_differs_for_different_query(self):
        key1 = _cache_key('React', SAMPLE_RESULTS)
        key2 = _cache_key('Vue', SAMPLE_RESULTS)
        self.assertNotEqual(key1, key2)

    def test_cache_store_and_get(self):
        """存储后能命中。"""
        # 用真实 cache key 测试，确保 store 和 get 一致
        real_key = _cache_key('React', SAMPLE_RESULTS)
        # Windows datetime.fromtimestamp 上限较低，用 2024 年的真实时间戳
        now_ts = 1.7e9  # ~2023-11
        stored = {
            'query': 'React',
            'result': {'score': 8.0, 'mode': 'flash',
                       'dimensions': {'relevance': 4, 'authoritativeness': 2,
                                      'freshness': 1, 'diversity': 1},
                       'rationale': 'cached'},
            'cached_at': now_ts,  # 1 小时前存入，未过期
        }
        # _cache_store 调用 _load_cache() → 返回空 dict
        # _cache_get 调用 _load_cache() → 返回含 stored 的 dict
        with patch('quality._save_cache') as mock_save, \
             patch('quality._load_cache') as mock_load, \
             patch('quality.time.time', return_value=now_ts + 3600):
            mock_load.side_effect = [{}, {real_key: stored}]
            _cache_store('React', SAMPLE_RESULTS,
                         {'score': 8.0, 'mode': 'flash'})
            cached = _cache_get('React', SAMPLE_RESULTS)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.get('cached'))
        mock_save.assert_called_once()

    def test_cache_expired_returns_none(self):
        """过期缓存返回 None 并清理。"""
        real_key = _cache_key('React', SAMPLE_RESULTS)
        past_time = 1e9  # 2001 年
        stored = {
            'query': 'React',
            'result': {'score': 8.0, 'mode': 'flash',
                       'dimensions': {}, 'rationale': ''},
            'cached_at': past_time,
        }
        # 8 天后查询 → 过期
        with patch('quality._load_cache', return_value={real_key: stored}), \
             patch('quality._save_cache') as mock_save, \
             patch('quality.time.time',
                   return_value=past_time + 8 * 24 * 3600):
            cached = _cache_get('React', SAMPLE_RESULTS)
        self.assertIsNone(cached)
        mock_save.assert_called_once()  # 触发清理

    def test_cache_miss_returns_none(self):
        with patch('quality._load_cache', return_value={}):
            cached = _cache_get('React', SAMPLE_RESULTS)
        self.assertIsNone(cached)

    def test_flash_mode_uses_cache(self):
        """flash 模式应优先查询缓存。"""
        cached_result = {
            'score': 9.0, 'mode': 'flash',
            'dimensions': {'relevance': 4, 'authoritativeness': 3,
                           'freshness': 1, 'diversity': 1},
            'rationale': 'cached', 'cached': True, 'cached_at': '2026-01-01',
        }
        with patch('quality._cache_get', return_value=cached_result) as mock_get:
            result = score_results('React', SAMPLE_RESULTS, mode='flash',
                                   use_cache=True)
        self.assertTrue(result.get('cached'))
        mock_get.assert_called_once()

    def test_heuristic_mode_skips_cache(self):
        """heuristic 模式不查询缓存。"""
        with patch('quality._cache_get') as mock_get:
            result = score_results('React', SAMPLE_RESULTS, mode='heuristic')
        mock_get.assert_not_called()
        self.assertNotIn('cached', result)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def test_cli_score_heuristic(self):
        from quality import _cli
        with patch('sys.argv', [
            'quality.py', 'score',
            '--query', 'React useEffect',
            '--results-json', json.dumps(SAMPLE_RESULTS),
            '--mode', 'heuristic',
        ]):
            buf = StringIO()
            with patch('sys.stdout', new=buf):
                exit_code = _cli()
            output = buf.getvalue()
            self.assertEqual(exit_code, 0)
            data = json.loads(output)
            self.assertEqual(data['mode'], 'heuristic')
            self.assertGreaterEqual(data['score'], 0)

    def test_cli_score_invalid_json(self):
        from quality import _cli
        with patch('sys.argv', [
            'quality.py', 'score',
            '--query', 'React',
            '--results-json', 'not-json',
        ]):
            buf = StringIO()
            with patch('sys.stderr', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 1)

    def test_cli_score_non_array_json(self):
        from quality import _cli
        with patch('sys.argv', [
            'quality.py', 'score',
            '--query', 'React',
            '--results-json', '{"not": "array"}',
        ]):
            buf = StringIO()
            with patch('sys.stderr', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 1)

    def test_cli_cache_clean_empty(self):
        from quality import _cli
        with patch('quality._load_cache', return_value={}):
            with patch('sys.argv', ['quality.py', 'cache-clean']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    exit_code = _cli()
                self.assertEqual(exit_code, 0)
                self.assertIn('缓存为空', buf.getvalue())

    def test_cli_cache_clean_with_expired(self):
        from quality import _cli
        past_time = 1e9
        cache_data = {
            'k1': {'cached_at': past_time, 'query': 'old', 'result': {}},
            'k2': {'cached_at': 1e12, 'query': 'new', 'result': {}},
        }
        with patch('quality._load_cache', return_value=cache_data), \
             patch('quality._save_cache') as mock_save, \
             patch('quality.time.time', return_value=past_time + 8 * 86400):
            with patch('sys.argv', ['quality.py', 'cache-clean']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    exit_code = _cli()
                self.assertEqual(exit_code, 0)
                self.assertIn('已清理 1 条', buf.getvalue())


class TestPromptBuilder(unittest.TestCase):
    """Flash prompt 构造测试。"""

    def test_prompt_contains_query(self):
        prompt = _build_flash_prompt('React useEffect', SAMPLE_RESULTS)
        self.assertIn('React useEffect', prompt)

    def test_prompt_contains_results(self):
        prompt = _build_flash_prompt('React', SAMPLE_RESULTS)
        self.assertIn('react.dev', prompt)
        self.assertIn('stackoverflow', prompt)

    def test_prompt_truncates_long_results(self):
        """超过 10 条结果只取前 10。"""
        long_results = [{'title': f'Title {i}', 'url': f'https://x{i}.com',
                          'snippet': 'snippet', 'source': 'blog'}
                        for i in range(20)]
        prompt = _build_flash_prompt('React', long_results)
        self.assertIn('Title 0', prompt)
        self.assertIn('Title 9', prompt)
        self.assertNotIn('Title 10', prompt)


class TestSaveCacheFailure(unittest.TestCase):
    """缓存写入失败兜底测试。"""

    def test_save_cache_failure_does_not_raise(self):
        with patch('quality.os.makedirs', side_effect=OSError('disk full')):
            # 应不抛异常，仅打印警告
            buf = StringIO()
            with patch('sys.stderr', new=buf):
                _save_cache({'k': 'v'})
            self.assertIn('警告', buf.getvalue())

    def test_load_cache_corrupted_returns_empty(self):
        """缓存文件损坏返回空 dict。"""
        with patch('quality.os.path.exists', return_value=True):
            with patch('builtins.open',
                       side_effect=json.JSONDecodeError('err', 'doc', 0)):
                # 注意：JSONDecodeError 在 json.load 阶段抛出
                pass
        with patch('quality.os.path.exists', return_value=True):
            mock_file = MagicMock()
            mock_file.__enter__.return_value.read.return_value = 'not json'
            with patch('builtins.open', return_value=mock_file):
                cache = _load_cache()
            self.assertEqual(cache, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
