"""Keep bearer tokens inside the GitHub API boundary; reject bad responses."""

import datetime as dt
import io
import urllib.request
from unittest.mock import patch

import pytest

from radar import __main__ as cli
from radar import github, report, scout


@pytest.mark.parametrize('url', [
    'https://example.invalid/next', 'https://api.github.com.example.invalid/',
    'https://user@api.github.com/', 'http://api.github.com/',
])
def test_foreign_destinations_rejected_before_token_or_network(url):
    with (
        patch.object(github, 'token', side_effect=AssertionError('token accessed')),
        pytest.raises(github.GitHubError),
    ):
        github.request(url)


def test_redirect_cannot_copy_authorization_to_foreign_host():
    request = urllib.request.Request('https://api.github.com/repos/a/b')
    request.add_header('Authorization', 'Bearer synthetic-test-value')
    with pytest.raises(github.GitHubError):
        github.APIOnlyRedirect().redirect_request(
            request, io.BytesIO(), 302, 'redirect', {}, 'https://example.invalid/next')


def test_naive_timestamp_does_not_crash_pruning():
    now = dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc)
    assert cli.prune_seen({'u': '2026-10-06T00:00:00'}, now) == {}


def test_invalid_nested_state_is_discarded(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{"seen_issues": [], "repos": {"bad": []}}')
    assert cli.load_state(path) == {}


def test_failed_searches_do_not_report_a_quiet_day(monkeypatch):
    def unavailable(*args, **kwargs):
        raise github.GitHubError('offline')

    monkeypatch.setattr(github, 'search_issues', unavailable)
    result = scout.find(['Python'])
    text = report.render(watch_results=[], scout_result=result, news={})
    assert result['search_errors'] == 3
    assert 'incomplete' in text
    assert 'Nothing needs you today' not in text
    assert 'incomplete' in report.render_short(watch_results=[], scout_result=result)


@pytest.mark.parametrize('body', [b'{bad', b'\xff', b'7'])
def test_invalid_success_response_is_a_github_error(monkeypatch, body):
    monkeypatch.setenv('GH_RADAR_TOKEN', 'synthetic-test-value')
    response = io.BytesIO(body)
    response.headers = {}
    with patch.object(urllib.request, 'build_opener') as factory:
        factory.return_value.open.return_value = response
        with pytest.raises(github.GitHubError):
            github.request('/user')


def test_valid_response_and_bounded_case_insensitive_pagination(monkeypatch):
    monkeypatch.setenv('GH_RADAR_TOKEN', 'synthetic-test-value')
    response = io.BytesIO(b'{"login":"demo"}')
    response.headers = {}
    with patch.object(urllib.request, 'build_opener') as factory:
        factory.return_value.open.return_value = response
        assert github.request('/user')[0] == {'login': 'demo'}
        request = factory.return_value.open.call_args.args[0]
        assert request.get_header('Authorization') == 'Bearer synthetic-test-value'

    with patch.object(github, 'request', side_effect=[
        ([1], {'link': '<https://api.github.com/next>; rel="next"'}), ([2, 3], {}),
    ]) as request:
        assert github.paged('/items', limit=2) == [1, 2]
        assert request.call_count == 2
