import glob
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest
from _pytest.mark import Mark
from aiohttp import web

from maigret.maigret import setup_arguments_parser
from maigret.settings import Settings
from maigret.sites import MaigretDatabase
from tests.maigret_cli import SITES


CUR_PATH = os.path.dirname(os.path.realpath(__file__))
JSON_FILE = os.path.join(CUR_PATH, '../maigret/resources/data.json')
SETTINGS_FILE = os.path.join(CUR_PATH, '../maigret/resources/settings.json')
TEST_JSON_FILE = os.path.join(CUR_PATH, 'db.json')
LOCAL_TEST_JSON_FILE = os.path.join(CUR_PATH, 'local.json')
empty_mark = Mark('', (), {})


RESULTS_EXAMPLE = {
    'Reddit': {
        'cookies': None,
        'parsing_enabled': False,
        'url_main': 'https://www.reddit.com/',
        'username': 'Skyeng',
    },
    'GooglePlayStore': {
        'cookies': None,
        'http_status': 200,
        'is_similar': False,
        'parsing_enabled': False,
        'rank': 1,
        'url_main': 'https://play.google.com/store',
        'url_user': 'https://play.google.com/store/apps/developer?id=Skyeng',
        'username': 'Skyeng',
    },
}


def by_slow_marker(item):
    return item.get_closest_marker('slow', default=empty_mark).name


def pytest_collection_modifyitems(items):
    items.sort(key=by_slow_marker, reverse=False)


def get_test_reports_filenames():
    return glob.glob(os.path.join('report_*'), recursive=False)


def remove_test_reports():
    reports_list = get_test_reports_filenames()
    for f in reports_list:
        os.remove(f)
    logging.error(f'Removed test reports {reports_list}')


@pytest.fixture(scope='session')
def default_db():
    return MaigretDatabase().load_from_file(JSON_FILE)


@pytest.fixture(scope='function')
def test_db():
    return MaigretDatabase().load_from_file(TEST_JSON_FILE)


@pytest.fixture
def run_maigret(tmp_path):
    """Run the offline Maigret CLI and return its stdout as text."""
    repo_root = str(Path(__file__).resolve().parent.parent)

    def run(*args, responses=None):
        workdir = Path(mkdtemp(dir=tmp_path))
        db = {
            'engines': {},
            'sites': {
                name: {
                    'urlMain': url,
                    'url': '{urlMain}/users/{username}',
                    'checkType': 'status_code',
                    'alexaRank': rank,
                }
                for rank, (name, url) in enumerate(SITES.items(), start=1)
            },
        }
        (workdir / 'sites.json').write_text(json.dumps(db), encoding='utf-8')
        (workdir / 'responses.json').write_text(json.dumps(responses or {}), encoding='utf-8')
        command = [
            sys.executable, '-m', 'tests.maigret_cli', 'responses.json',
            '--db', 'sites.json', '--no-autoupdate', '--dns-resolver=threaded',
            '--no-color', '--no-progressbar', *args,
        ]
        result = subprocess.run(
            command,
            cwd=workdir,
            env={
                **os.environ,
                'PYTHONPATH': repo_root,
                'PYTHONIOENCODING': 'utf-8',
                'HOME': str(workdir),
                'USERPROFILE': str(workdir),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding='utf-8',
            timeout=30,
        )
        if result.returncode:
            pytest.fail(
                f'Maigret exited with status {result.returncode}\n'
                f'Stdout:\n{result.stdout}\nStderr:\n{result.stderr}',
                pytrace=False,
            )
        return result.stdout

    return run


@pytest.fixture(scope='function')
def local_test_db(make_httpserver):
    db = MaigretDatabase().load_from_file(LOCAL_TEST_JSON_FILE)
    # tests/local.json pins its URLs to a historical fixed port; point them at
    # the ephemeral port the shared httpserver fixture actually bound.
    base = f"{make_httpserver.host}:{make_httpserver.port}"
    for site in db.sites:
        for attr in ("url", "url_main", "url_probe"):
            value = getattr(site, attr, None)
            if value and "localhost:8989" in value:
                setattr(site, attr, value.replace("localhost:8989", base))
    return db


@pytest.fixture(autouse=True)
def reports_autoclean():
    remove_test_reports()
    yield
    remove_test_reports()


@pytest.fixture(scope='session')
def settings():
    settings = Settings()
    settings.load([SETTINGS_FILE])
    return settings


@pytest.fixture(scope='session')
def argparser():
    settings = Settings()
    settings.load([SETTINGS_FILE])
    return setup_arguments_parser(settings)


@pytest.fixture(scope="session")
def httpserver_listen_address():
    # Port 0 lets the OS pick a free port: a fixed port breaks the whole suite
    # whenever anything else on the machine already occupies it.
    return ("localhost", 0)


def _unused_localhost_port() -> int:
    """A loopback port that is free right now.

    The test server must listen on the hostname ``localhost`` (the cookies
    under test are scoped to that domain, and cookie jars do not match them
    against a bare IP), so binding port 0 and reading the winner back is not
    an option: on a dual-stack machine each resolved address would get a
    different port. Picking the port first keeps every stack on the same one.
    A hardcoded port, by contrast, fails the test whenever anything else on
    the machine already listens there, which says nothing about the code.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def cookie_test_server_url():
    """Serve /cookies on a free localhost port and yield its URL."""
    async def handle_cookies(request):
        print(f"Received cookies: {request.cookies}")
        cookies_dict = {k: v for k, v in request.cookies.items()}
        return web.json_response({'cookies': cookies_dict})

    app = web.Application()
    app.router.add_get('/cookies', handle_cookies)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _unused_localhost_port()
    server = web.TCPSite(runner, host='localhost', port=port)
    await server.start()
    yield f'http://localhost:{port}/cookies'
    await runner.cleanup()
