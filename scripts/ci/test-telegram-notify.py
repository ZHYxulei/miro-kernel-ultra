#!/usr/bin/env python3
import contextlib
import copy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('notify', Path(__file__).with_name('telegram-notify.py'))
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
REPO = 'owner/kernel'
NAME = 'miro-kernel-ultra-test.zip'
PAYLOAD = b'opaque installation package'


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, data in entries:
            z.writestr(name, data)
    return out.getvalue()


def package(digest=None):
    digest = digest or hashlib.sha256(PAYLOAD).hexdigest()
    return archive([('SHA256SUMS-standard', f'{digest}  {NAME}\n'), (NAME, PAYLOAD)])


def event(conclusion='success'):
    return {'repository': {'default_branch': 'main'}, 'workflow_run': {
        'id': 12, 'run_attempt': 2, 'head_sha': 'a' * 40, 'name': 'Kernel CI/CD',
        'head_branch': 'main', 'status': 'completed', 'event': 'push', 'pull_requests': [],
        'head_repository': {'full_name': REPO}, 'conclusion': conclusion,
        'run_started_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:01:02Z'}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(n.urllib.request, 'build_opener', side_effect=AssertionError('offline test attempted network'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_html_and_topic(self):
        self.assertEqual(n.esc('<b>"&'), '&lt;b&gt;&quot;&amp;')
        self.assertIn('&quot;', n.link('https://example.test/"', '<script>'))
        tg = n.Telegram('dummy-offline', '-123', '42')
        self.assertEqual(tg.fields['message_thread_id'], '42')
        for thread in ('-1', '0', 'abc'):
            with self.assertRaises(n.Error):
                n.Telegram('dummy-offline', '-123', thread)
        with patch.object(tg, 'call') as call:
            tg.document(NAME, PAYLOAD)
            self.assertIn(b'name="message_thread_id"', call.call_args.args[1])

    def test_http_errors_hide_secrets(self):
        secret_url = 'https://api.telegram.org/botDUMMY-OFFLINE-SECRET/sendMessage'
        for error in (urllib.error.HTTPError(secret_url, 401, secret_url, {}, None),
                      urllib.error.URLError(secret_url), ValueError(secret_url)):
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(n.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(n.Error) as caught:
                    n.http(secret_url)
            self.assertNotIn('DUMMY-OFFLINE-SECRET', str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    def test_redirect_drops_authorization(self):
        location = 'https://store.blob.core.windows.net/package'
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://api.github.com', 302, '', {'Location': location}, None)
        response = Mock()
        response.headers = {}
        response.read.return_value = b'zip'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        second = Mock()
        second.open.return_value = response
        with patch.object(n.urllib.request, 'build_opener', side_effect=[opener, second]):
            self.assertEqual(n.http('https://api.github.com', {'Authorization': 'Bearer dummy'}, redirect=True), b'zip')
        self.assertFalse(second.open.call_args.args[0].has_header('Authorization'))
        for location in ('http://store.blob.core.windows.net/a', 'https://evil.test/a', 'https://user@store.blob.core.windows.net/a'):
            opener.open.side_effect = urllib.error.HTTPError('', 302, '', {'Location': location}, None)
            with patch.object(n.urllib.request, 'build_opener', return_value=opener), self.assertRaises(n.Error):
                n.http('https://api.github.com', redirect=True)

    def test_http_response_limits(self):
        for headers, body in (({'Content-Length': '9'}, b''), ({}, b'12345')):
            response = Mock()
            response.headers = headers
            response.read.return_value = body
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            opener = Mock()
            opener.open.return_value = response
            with patch.object(n.urllib.request, 'build_opener', return_value=opener), self.assertRaises(n.Error):
                n.http('https://example.test', limit=4)

    def test_artifact_validation(self):
        files, skipped = n.package_files(package())
        self.assertEqual(files[-1], (NAME, PAYLOAD))
        self.assertEqual(skipped, [])
        symlink = zipfile.ZipInfo('link')
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for data in (package('0' * 64), archive([(NAME, PAYLOAD)]),
                     archive([('../escape', b'x')]), archive([(symlink, b'target')]),
                     archive([('other.zip', b'x')]), archive([('SHA256SUMS-standard', b'bad')]), b'not zip'):
            with self.assertRaises(n.Error):
                n.package_files(data)

    def test_success_failure_cancelled(self):
        for conclusion in ('success', 'failure', 'cancelled'):
            with self.subTest(conclusion=conclusion):
                gh, tg = Mock(), Mock()
                gh.get.return_value = {'commit': {'message': '<script>commit\nbody', 'author': {'name': 'A&B'}}, 'parents': []}
                gh.pages.side_effect = [[{'id': 99, 'name': '<bad job>', 'conclusion': 'failure',
                    'steps': [{'name': '<bad step>', 'conclusion': 'failure'}]}],
                    [{'id': 10, 'name': 'miro-kernel-ultra-release-12', 'size_in_bytes': 100, 'expired': False}]]
                gh.download.return_value = package()
                n.notify(event(conclusion), REPO, gh, tg)
                text = '\n'.join(c.args[0] for c in tg.message.call_args_list)
                self.assertIn('&lt;script&gt;commit', text)
                self.assertIn('&lt;bad step&gt;', text)
                self.assertIn('/job/99', text)
                self.assertIn('Logs', text)
                self.assertIn('/artifacts/10', text)
                self.assertNotIn('<script>', text)
                self.assertEqual(tg.document.call_count, 2 if conclusion == 'success' else 0)
                self.assertEqual(gh.download.call_count, 1 if conclusion == 'success' else 0)

    def test_expired_oversize_and_nonpackage_artifacts(self):
        for changes in ({'expired': True}, {'size_in_bytes': 201*n.MIB}, {'name': 'build-logs-12'}):
            gh, tg = Mock(), Mock()
            gh.get.return_value = {'commit': {'message': 'commit', 'author': {'name': 'author'}}, 'parents': []}
            artifact = {'id': 10, 'name': 'miro-kernel-ultra-release-12', 'size_in_bytes': 100, 'expired': False}
            artifact.update(changes)
            gh.pages.side_effect = [[], [artifact]]
            n.notify(event(), REPO, gh, tg)
            gh.download.assert_not_called()
            tg.document.assert_not_called()

    def test_trust_boundary(self):
        baseline = event()['workflow_run']
        self.assertTrue(n.trusted(baseline, REPO, 'main'))
        for changes in ({'event': 'pull_request'}, {'head_branch': 'upstream-candidate'},
                        {'head_repository': {'full_name': 'fork/kernel'}}, {'pull_requests': [{'number': 1}]},
                        {'conclusion': 'skipped'}):
            run = copy.deepcopy(baseline)
            run.update(changes)
            self.assertFalse(n.trusted(run, REPO, 'main'))
        gh, tg = Mock(), Mock()
        candidate = event()
        candidate['workflow_run']['head_branch'] = 'upstream-candidate'
        n.notify(candidate, REPO, gh, tg)
        self.assertEqual(gh.mock_calls, [])
        self.assertEqual(tg.mock_calls, [])

    def test_missing_configuration_fails(self):
        for env, missing in (({}, 'TELEGRAM_CHAT_ID'),
                             ({'TELEGRAM_CHAT_ID': '-123'}, 'TELEGRAM_BOT_TOKEN')):
            with self.subTest(missing=missing), patch.dict(os.environ, env, clear=True), patch.object(n.sys, 'argv', ['telegram-notify.py']):
                with self.assertRaises(n.Error) as caught:
                    n.main()
                self.assertIn(missing, str(caught.exception))
                self.assertIn('Telegram Bot', str(caught.exception))


if __name__ == '__main__':
    unittest.main(argv=['test-telegram-notify'], verbosity=2)
