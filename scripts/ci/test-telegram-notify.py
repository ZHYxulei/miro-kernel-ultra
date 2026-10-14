#!/usr/bin/env python3
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from html.parser import HTMLParser
import stat
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
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


def package(digest=None, names=None):
    digest = digest or hashlib.sha256(PAYLOAD).hexdigest()
    names = names or [NAME]
    return archive([('SHA256SUMS-standard', ''.join(f'{digest}  {name}\n' for name in names))]
                   + [(name, PAYLOAD) for name in names])


def event(conclusion='success'):
    return {'repository': {'default_branch': 'main'}, 'workflow_run': {
        'id': 12, 'run_attempt': 2, 'head_sha': 'a' * 40, 'name': 'Kernel CI/CD',
        'head_branch': 'main', 'status': 'completed', 'event': 'push', 'pull_requests': [],
        'head_repository': {'full_name': REPO}, 'conclusion': conclusion,
        'run_started_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:01:02Z'}}


def artifact(id=10, name='miro-kernel-ultra-release-12', **changes):
    result = {'id': id, 'name': name, 'size_in_bytes': 100, 'expired': False}
    result.update(changes)
    return result


def github(artifacts, jobs=None, message='<script>commit\nbody'):
    gh = Mock()
    gh.get.return_value = {'commit': {'message': message, 'author': {'name': 'A&B'}},
                           'parents': [{'sha': 'b' * 40}]}
    gh.pages.side_effect = [jobs or [], artifacts]
    gh.download.return_value = package()
    return gh


def multipart(call):
    method, body, content_type = call.args
    parsed = BytesParser(policy=default).parsebytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + body)
    return method, {p.get_param('name', header='content-disposition'): p for p in parsed.iter_parts()}


class SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = ''
    def handle_starttag(self, tag, attrs):
        assert tag in ('a', 'b', 'code', 'pre'), tag
        if tag == 'a':
            assert len(attrs) == 1 and attrs[0][0] == 'href'
            assert attrs[0][1].startswith('https://github.com/')
        else:
            assert not attrs
        self.stack.append(tag)
    def handle_endtag(self, tag):
        assert self.stack.pop() == tag
    def handle_data(self, data):
        self.text += data


class Tests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(n.urllib.request, 'build_opener', side_effect=AssertionError('offline test attempted network'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def assert_html(self, text, limit):
        parser = SafeHTML()
        parser.feed(text)
        parser.close()
        self.assertEqual(parser.stack, [])
        self.assertLessEqual(len(parser.text.encode('utf-16-le')) // 2, limit)
        self.assertEqual(n.text_len(text), len(parser.text.encode('utf-16-le')) // 2)

    def test_html_and_topic(self):
        self.assertEqual(n.esc('<b>"&'), '&lt;b&gt;&quot;&amp;')
        self.assertIn('&quot;', n.link('https://example.test/"', '<script>'))
        tg = n.Telegram('dummy-offline', '-123', ' 42 \n')
        self.assertEqual(tg.fields['message_thread_id'], '42')
        self.assertNotIn('message_thread_id', n.Telegram('dummy', '-123', ' \t').fields)
        for thread in ('-1', '0', 'abc'):
            with self.assertRaises(n.Error):
                n.Telegram('dummy-offline', '-123', thread)
        with patch.object(tg, 'call') as call:
            tg.document(NAME, PAYLOAD, '<b>caption</b>')
            method, parts = multipart(call.call_args)
            self.assertEqual(method, 'sendDocument')
            self.assertEqual(parts['message_thread_id'].get_payload(decode=True), b'42')
            self.assertEqual(parts['caption'].get_payload(decode=True), b'<b>caption</b>')
            self.assertEqual(parts['document'].get_payload(decode=True), PAYLOAD)

    def test_album_single_multipart_caption_once(self):
        names = [f'miro-kernel-ultra-{i}.zip' for i in range(4)]
        artifacts = [artifact(), artifact(11, 'symbols-12'), artifact(12, 'build-logs-12'), artifact(13, 'SHA256SUMS-12')]
        gh = github(artifacts)
        gh.download.return_value = package(names=names)
        tg = n.Telegram('dummy', '-123', '42')
        with patch.object(tg, 'call') as call:
            n.notify(event(), REPO, gh, tg)
        call.assert_called_once()
        method, parts = multipart(call.call_args)
        self.assertEqual(method, 'sendMediaGroup')
        media = json.loads(parts['media'].get_payload(decode=True))
        self.assertEqual(len(media), 4)
        self.assertEqual(sum('caption' in item for item in media), 1)
        self.assertEqual(media[0]['parse_mode'], 'HTML')
        self.assertNotIn('caption', parts)
        self.assertEqual(sum(p.get_filename() is not None for p in parts.values()), 4)
        for i, name in enumerate(names):
            self.assertEqual(media[i]['type'], 'document')
            self.assertEqual(media[i]['media'], f'attach://file{i}')
            self.assertEqual(parts[f'file{i}'].get_filename(), name)
            self.assertEqual(parts[f'file{i}'].get_payload(decode=True), PAYLOAD)
        text = media[0]['caption']
        for required in ('SUCCESS', 'main', '1m 2s', 'a' * 40, '&lt;script&gt;commit', 'Compare', 'Logs'):
            self.assertIn(required, text)
        for item in artifacts:
            self.assertIn(f"/artifacts/{item['id']}", text)
        self.assert_html(text, 1024)
        gh.download.assert_called_once_with(10)

    def test_table_and_commit_body_at_bottom(self):
        gh = github([artifact()], message='feat: update kernel\n\n详细的提交说明 & 验证结果')
        text = n.notification_text(event()['workflow_run'], REPO, gh.get.return_value,
                                   [], [artifact()], 4, [], 1024)
        self.assertIn('<pre>', text)
        self.assertIn('Branch    │ main', text)
        self.assertIn('Packages  │ 4/4', text)
        self.assertIn('Author: A&amp;B', text)
        self.assertIn('详细的提交说明 &amp; 验证结果', text)
        self.assertLess(text.index('<b>Artifacts</b>'), text.index('<b>Commit 信息</b>'))
        self.assert_html(text, 1024)

    def test_album_bounds(self):
        tg = n.Telegram('dummy', '-123', '')
        with patch.object(tg, 'call') as call:
            for count in (2, 10):
                tg.album([(NAME, PAYLOAD)] * count, 'caption')
                _, parts = multipart(call.call_args)
                self.assertEqual(len(json.loads(parts['media'].get_payload(decode=True))), count)
            for count in (0, 1, 11):
                with self.assertRaises(n.Error):
                    tg.album([(NAME, PAYLOAD)] * count, 'caption')

    def test_single_and_zero_fallback(self):
        for artifacts in ([artifact()], []):
            gh = github(artifacts)
            tg = n.Telegram('dummy', '-123', '')
            with patch.object(tg, 'call') as call:
                n.notify(event(), REPO, gh, tg)
            call.assert_called_once()
            if artifacts:
                method, parts = multipart(call.call_args)
                self.assertEqual(method, 'sendDocument')
                self.assertEqual(sum(p.get_filename() is not None for p in parts.values()), 1)
                self.assertEqual(parts['document'].get_filename(), NAME)
                text = parts['caption'].get_payload(decode=True).decode()
                self.assert_html(text, 1024)
            else:
                self.assertEqual(call.call_args.args[0], 'sendMessage')
                text = urllib.parse.parse_qs(call.call_args.args[1].decode())['text'][0]
                self.assert_html(text, 4096)
                gh.download.assert_not_called()
            self.assertIn('SUCCESS', text)

    def test_failure_cancelled_no_downloads(self):
        jobs = [{'id': 99, 'name': '<bad job>', 'conclusion': 'failure',
                 'steps': [{'name': '<bad step>', 'conclusion': 'failure'}]}]
        for conclusion in ('failure', 'cancelled'):
            gh = github([artifact()], jobs)
            tg = Mock()
            n.notify(event(conclusion), REPO, gh, tg)
            self.assertEqual(len(tg.mock_calls), 1)
            tg.message.assert_called_once()
            gh.download.assert_not_called()
            text = tg.message.call_args.args[0]
            for required in (conclusion.upper(), '&lt;bad step&gt;', '/job/99', 'Logs', '/artifacts/10'):
                self.assertIn(required, text)
            self.assert_html(text, 4096)

    def test_expired_oversize_and_nonpackage_artifacts(self):
        for changes in ({'expired': True}, {'size_in_bytes': 201*n.MIB}, {'name': 'build-logs-12'}):
            gh, tg = github([artifact(**changes)]), Mock()
            n.notify(event(), REPO, gh, tg)
            gh.download.assert_not_called()
            self.assertEqual(len(tg.mock_calls), 1)
            text = tg.message.call_args.args[0]
            self.assertIn('/artifacts/10', text)
            if changes.get('expired'):
                self.assertIn('expired', text)
            if 'size_in_bytes' in changes:
                self.assertIn('oversize', text)

    def test_package_cap_and_deduplication(self):
        gh, tg = github([artifact(), artifact(11, 'miro-kernel-ultra-debug-12')]), Mock()
        gh.download.side_effect = [package(names=[f'miro-kernel-ultra-{i}.zip' for i in range(3)]),
                                   package(names=[f'miro-kernel-ultra-{i}.zip' for i in range(5)])]
        n.notify(event(), REPO, gh, tg)
        self.assertEqual(len(tg.mock_calls), 1)
        files, text = tg.album.call_args.args
        self.assertEqual(len(files), 4)
        self.assertEqual(len({name for name, _ in files}), 4)
        self.assertIn('/artifacts/11', text)

    def test_lengths_html_and_artifact_fallback(self):
        malicious = '<b>&"😀' * 2000
        for limit in (1024, 4096):
            for count in (1, 100):
                artifacts = [artifact(i, malicious) for i in range(count)]
                gh = github(artifacts, message=malicious)
                run = event()['workflow_run']
                run['head_branch'] = malicious
                text = n.notification_text(run, REPO, gh.get.return_value, [], artifacts, 0, [], limit)
                self.assert_html(text, limit)
                self.assertIn('a' * 40, text)
                self.assertIn('SUCCESS', text)
                self.assertIn('Summary:', text)
                if count == 100:
                    self.assertIn('/actions/runs/12#artifacts', text)
                else:
                    self.assertIn('/artifacts/0', text)
        self.assertEqual(n.truncate('😀😀', 3), '😀…')
        self.assertEqual(n.text_len('&lt;b&gt;&amp;😀'), 6)
        tg = n.Telegram('dummy', '-123', '')
        with patch.object(tg, 'call') as call:
            tg.document(NAME, PAYLOAD, '😀' * 512)
            tg.message('😀' * 2048)
            for action in (lambda: tg.document(NAME, PAYLOAD, '😀' * 513),
                           lambda: tg.album([(NAME, PAYLOAD)] * 2, '😀' * 513),
                           lambda: tg.message('😀' * 2049)):
                with self.assertRaises(n.Error):
                    action()
            self.assertEqual(call.call_count, 2)

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
        self.assertEqual(files, [(NAME, PAYLOAD)])
        self.assertEqual(skipped, [])
        symlink = zipfile.ZipInfo('link')
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for data in (package('0' * 64), archive([(NAME, PAYLOAD)]),
                     archive([('../escape', b'x')]), archive([(symlink, b'target')]),
                     archive([('other.zip', b'x')]), archive([('SHA256SUMS-standard', b'bad')]), b'not zip'):
            with self.assertRaises(n.Error):
                n.package_files(data)

    def test_50mib_package_link_only(self):
        # 缩小 MiB 单位验证相同边界，避免离线测试分配大文件。
        gh, tg = github([artifact()]), Mock()
        payload = b'x' * 501
        data = archive([('SHA256SUMS-standard', f'{hashlib.sha256(payload).hexdigest()}  {NAME}\n'), (NAME, payload)])
        with patch.object(n, 'MIB', 10):
            self.assertEqual(n.package_files(data), ([], [NAME]))
            gh.download.return_value = data
            n.notify(event(), REPO, gh, tg)
            self.assertEqual(len(tg.mock_calls), 1)
            text = tg.message.call_args.args[0]
            self.assertIn('/artifacts/10', text)
            self.assertIn('50 MiB', text)
            telegram = n.Telegram('dummy', '-123', '')
            with patch.object(telegram, 'call') as call:
                telegram.document(NAME, b'x' * 500, 'caption')
                with self.assertRaises(n.Error):
                    telegram.document(NAME, payload, 'caption')
                call.assert_called_once()

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
