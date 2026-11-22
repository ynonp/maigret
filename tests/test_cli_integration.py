import pytest


@pytest.mark.parametrize('username', ['Dr. Evil', 'Alice'])
def test_run_maigret_searches_only_the_top_site(run_maigret, username):
    output = run_maigret(username, '--top-sites=1')

    assert f'[*] Checking username {username} on:' in output.splitlines()
    assert '[+] Example Profiles:' in output
    assert 'Example Forum' not in output


def test_run_maigret_uses_canned_http_responses(run_maigret):
    output = run_maigret(
        'alice', '--top-sites=2',
        responses={'https://profiles.example.test/users/alice': (404, '')},
    )

    assert '[+] Example Forum:' in output
    assert '[+] Example Profiles:' not in output
    assert 'Search by username alice returned 1 accounts.' in output.splitlines()


def test_run_maigret_fails_on_unmocked_requests(run_maigret):
    with pytest.raises(pytest.fail.Exception, match=r'Unexpected network access.*icanhazip\.com'):
        run_maigret('alice', '--top-sites=1', '--debug')
