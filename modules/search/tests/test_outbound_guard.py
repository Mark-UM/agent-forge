"""Enabled direct MCP searches must stop before network on redaction failure."""
import importlib
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize('module_name,entry_name', [
    ('serper_mcp', 'serper_search'),
    ('arxiv_mcp', 'arxiv_search'),
    ('semantic_scholar_mcp', 'search_papers'),
])
@pytest.mark.parametrize('failure', ['missing', 'reported', 'empty', 'raised'])
def test_direct_mcp_redaction_failure_stops_network(
    monkeypatch, module_name, entry_name, failure,
):
    module = importlib.import_module(f'modules.search.{module_name}')

    def bad_redaction(query):
        if failure == 'raised':
            raise RuntimeError('redactor unavailable')
        if failure == 'reported':
            return query, {'error': 'redactor unavailable'}
        return '', {'redacted_count': 0}

    monkeypatch.setattr(module, '_redact_outbound',
                        None if failure == 'missing' else bad_redaction)
    network = MagicMock()
    if module_name == 'semantic_scholar_mcp':
        monkeypatch.setattr(module, '_make_s2_request', network)
    else:
        monkeypatch.setattr(module.urllib.request, 'urlopen', network)

    options = {'api_key': 'test-key'} if module_name == 'serper_mcp' else {}
    result = getattr(module, entry_name)('contact 13800138000', **options)

    assert result['success'] is False
    assert result['query'] == ''
    assert 'redaction' in result['error'].lower()
    network.assert_not_called()
