#!/usr/bin/env python3
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
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
KPATCH = 'miro-kernel-ultra-kpatch-exp-test.zip'
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
    commit = {'commit': {'message': message, 'author': {'name': 'A&B'}},
              'parents': [{'sha': 'b' * 40}]}
    def get(path):
        if path.startswith('/commits/'):
            return commit
        assert path.endswith('?ref=' + 'a' * 40), path
        if '/KernelSU?' in path:
            return {'sha': 'c' * 40, 'type': 'submodule'}
        if '/KPatch-Next?' in path:
            return {'sha': 'd' * 40, 'type': 'submodule'}
        text = ('VERSION = 6\nPATCHLEVEL = 6\nSUBLEVEL = 30\nEXTRAVERSION =\n'
                if '/Makefile?' in path else '#define SUSFS_VERSION "v2.3.0"\n'
                if '/susfs.h?' in path else
                'CONFIG_KSU=y\nCONFIG_KSU_SUSFS=y\nCONFIG_REKERNEL=y\nCONFIG_NTSYNC=y\n'
                'CONFIG_LRU_GEN=y\nCONFIG_LRU_GEN_ENABLED=y\nCONFIG_LTO_CLANG_THIN=y\n')
        return {'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()}
    gh.get.side_effect = get
    gh.pages.side_effect = [jobs or [], artifacts]
    gh.download.return_value = package()
    return gh


def multipart(call):
    method, body, content_type = call.args
    parsed = BytesParser(policy=default).parsebytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + body)
    return method, {p.get_param('name', header='content-disposition'): p for p in parsed.iter_parts()}


def text(blocks):
    return json.dumps(blocks, ensure_ascii=False)


class Tests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(n.urllib.request, 'build_opener', side_effect=AssertionError('offline test attempted network'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_rich_multipart_order_and_native_text(self):
        gh = github([artifact(), artifact(11, 'miro-kernel-ultra-debug-12')])
        gh.download.return_value = package(names=[KPATCH, NAME])
        tg = n.Telegram('dummy-offline', '-123', ' 42 \n')
        with patch.object(tg, 'call') as call:
            n.notify(event(), REPO, gh, tg)
        call.assert_called_once()
        method, parts = multipart(call.call_args)
        self.assertEqual(method, 'sendRichMessage')
        self.assertEqual(parts['message_thread_id'].get_payload(decode=True), b'42')
        payload = json.loads(parts['rich_message'].get_payload(decode=True))
        self.assertTrue(payload['skip_entity_detection'])
        blocks = payload['blocks']
        self.assertEqual([b['type'] for b in blocks[:8]],
                         ['document', 'document', 'table', 'table', 'paragraph', 'paragraph', 'paragraph', 'pre'])
        for i, name in enumerate([NAME, KPATCH]):
            self.assertEqual(blocks[i]['document'], {'type': 'document', 'media': f'attach://file{i}'})
            self.assertEqual(parts[f'file{i}'].get_filename(), name)
            self.assertEqual(parts[f'file{i}'].get_payload(decode=True), PAYLOAD)
        self.assertEqual([row[0]['text'] for row in blocks[2]['cells']], ['Kernel', 'Source', 'Toolchain', 'LTO'])
        self.assertEqual(blocks[2]['cells'][0][1]['text'], '6.6.30')
        self.assertEqual(blocks[2]['cells'][1][1]['text'],
                         {'type': 'url', 'text': 'a' * 12, 'url': f'https://github.com/{REPO}/commit/' + 'a' * 40})
        self.assertEqual(blocks[2]['cells'][2][1]['text'], '未记录')
        self.assertEqual(blocks[2]['cells'][3][1]['text'], 'ThinLTO')
        self.assertEqual([cell['text'] for cell in blocks[3]['cells'][0]], ['功能', '状态'])
        self.assertTrue(all(cell['is_header'] for cell in blocks[3]['cells'][0]))
        self.assertEqual(blocks[3]['cells'][1][1]['text'], {'type': 'button', 'button':
                         {'text': '启用 (y)', 'style': 'success', 'disabled': {}}})
        self.assertEqual(blocks[3]['cells'][-1][1]['text'],
                         ['仅修补版 · ', {'type': 'marked', 'text': '实验'}])
        self.assertEqual(blocks[10]['text'], {'type': 'url', 'text': 'KPatch: ' + 'd' * 12,
                         'url': 'https://github.com/ZHYxulei/KPatch-Next-EXP/tree/' + 'd' * 40})
        for block in blocks[2:4]:
            self.assertTrue(block['is_bordered'])
            self.assertTrue(block['is_compact'])
            for row in block['cells']:
                self.assertEqual(len(row), 2)
                for cell in row:
                    self.assertEqual((cell['align'], cell['valign']), ('left', 'top'))
        self.assertIn('<script>commit', blocks[7]['text'])
        self.assertIn('A&B', blocks[7]['text'])
        self.assertNotIn('&lt;', text(blocks))
        self.assertEqual(blocks[8]['text']['text'], 'Compare')
        self.assertEqual(blocks[9]['text']['text'], 'Workflow / Logs')
        self.assertIn('/artifacts/11', text(blocks))
        self.assertIn('非启动验证', text(blocks))
        self.assertIn('c' * 40, blocks[4]['text']['url'])
        gh.download.assert_called_once_with(10)

    def test_zero_single_failure_and_debug_link_only(self):
        jobs = [{'id': 99, 'name': '<bad job>', 'conclusion': 'failure',
                 'steps': [{'name': '<bad step>', 'conclusion': 'failure'}]}]
        for conclusion, artifacts in [('success', []), ('success', [artifact()]),
                ('success', [artifact(11, 'miro-kernel-ultra-debug-12')]),
                ('failure', [artifact()]), ('cancelled', [artifact()])]:
            gh, tg = github(artifacts, jobs), Mock()
            n.notify(event(conclusion), REPO, gh, tg)
            tg.rich.assert_called_once()
            blocks, files = tg.rich.call_args.args
            self.assertIn(conclusion.upper(), text(blocks))
            self.assertIn('<bad step>', text(blocks))
            if conclusion == 'success' and artifacts and artifacts[0]['id'] == 10:
                self.assertEqual(len(files), 1)
            else:
                self.assertEqual(files, [])
                gh.download.assert_not_called()

    def test_413_fallback_once_and_other_errors(self):
        gh, tg = github([artifact()]), Mock()
        tg.rich.side_effect = [n.HTTPError(413), None]
        n.notify(event(), REPO, gh, tg)
        self.assertEqual(tg.rich.call_count, 2)
        blocks, files = tg.rich.call_args.args
        self.assertEqual(files, [])
        self.assertNotIn('document', [b['type'] for b in blocks])
        self.assertIn('/artifacts/10', text(blocks))
        for status, artifacts in [(403, [artifact()]), (413, [])]:
            tg = Mock()
            tg.rich.side_effect = n.HTTPError(status)
            with self.assertRaises(n.HTTPError):
                n.notify(event(), REPO, github(artifacts), tg)
            tg.rich.assert_called_once()

    def test_debug_packages_cap_and_deduplication(self):
        gh, tg = github([artifact(), artifact(11, 'miro-kernel-ultra-debug-12')]), Mock()
        gh.download.return_value = package(names=[NAME, KPATCH, 'miro-kernel-ultra-debug-test.zip',
                                                 'miro-kernel-ultra-kpatch-exp-debug-test.zip'])
        n.notify(event(), REPO, gh, tg)
        blocks, files = tg.rich.call_args.args
        self.assertEqual([name for name, _ in files], [NAME, KPATCH])
        self.assertIn('/artifacts/11', text(blocks))
        gh.download.assert_called_once_with(10)

    def test_expired_oversize_nonrelease_and_total_budget(self):
        for changes in ({'expired': True}, {'size_in_bytes': 201*n.MIB}, {'name': 'build-logs-12'}):
            gh, tg = github([artifact(**changes)]), Mock()
            n.notify(event(), REPO, gh, tg)
            gh.download.assert_not_called()
            blocks, files = tg.rich.call_args.args
            self.assertEqual(files, [])
            self.assertIn('/artifacts/10', text(blocks))
            if changes.get('expired'):
                self.assertIn('expired', text(blocks))
            if 'size_in_bytes' in changes:
                self.assertIn('oversize', text(blocks))
        gh, tg = github([artifact()]), Mock()
        with patch.object(n, 'package_files', return_value=([(NAME, b'x' * (50*n.MIB))], [])):
            n.notify(event(), REPO, gh, tg)
        self.assertEqual(tg.rich.call_args.args[1], [])

    def test_snapshot_missing_and_merge_builtin_precedence(self):
        gh = github([])
        snapshot = n.source_snapshot(gh, 'a' * 40)
        self.assertEqual(snapshot['kernel'], '6.6.30')
        self.assertEqual(snapshot['baka'], 'c' * 40)
        self.assertEqual(snapshot['susfs'], 'v2.3.0')
        self.assertTrue(all(call.args[0].endswith('?ref=' + 'a' * 40) for call in gh.get.call_args_list))
        real_get = gh.get.side_effect
        def get(path):
            if 'miro_perf.config' in path:
                return {'encoding': 'base64', 'content': base64.b64encode(b'CONFIG_KSU=m\n').decode()}
            return real_get(path)
        gh.get.side_effect = get
        self.assertEqual(dict(n.source_snapshot(gh, 'a' * 40)['features'])['BakaSU'], '启用 (y)')
        gh.get.side_effect = n.HTTPError(404)
        snapshot = n.source_snapshot(gh, 'a' * 40)
        self.assertEqual(snapshot['kernel'], '未知')
        self.assertEqual(snapshot['baka'], '')
        self.assertTrue(all('未知' in value for _, value in snapshot['features']))
        gh.get.side_effect = n.HTTPError(403)
        with self.assertRaises(n.HTTPError):
            n.source_snapshot(gh, 'a' * 40)

    def test_status_styles_and_unconfigured_lto(self):
        for value, style in [('启用 (y)', 'success'), ('模块 (m)', 'success'), ('禁用 (n)', 'danger')]:
            self.assertEqual(n.rich_status(value), {'type': 'button', 'button':
                             {'text': value, 'style': style, 'disabled': {}}})
        self.assertEqual(n.rich_status('未知'), '未知')
        gh = github([])
        real_get = gh.get.side_effect
        def get(path):
            result = real_get(path)
            if '/configs/' in path:
                result = {'encoding': 'base64', 'content': base64.b64encode(
                    b'CONFIG_KSU=m\n# CONFIG_KSU_SUSFS is not set\n').decode()}
            return result
        gh.get.side_effect = get
        snapshot = n.source_snapshot(gh, 'a' * 40)
        self.assertEqual(snapshot['lto'], '未显式配置')
        self.assertEqual(dict(snapshot['features'])['SUSFS'], '禁用 (n)')
        self.assertEqual(dict(snapshot['features'])['Re:Kernel'], '未知')
        self.assertEqual(dict(snapshot['features'])['BakaSU'], '模块 (m)')
        gh.get.side_effect = n.HTTPError(404)
        snapshot = n.source_snapshot(gh, 'a' * 40)
        self.assertEqual(snapshot['kpm'], '')
        self.assertIn('配置缺失', snapshot['lto'])

    def test_rich_size_topic_and_file_limits(self):
        tg = n.Telegram('dummy', '-123', '')
        self.assertNotIn('message_thread_id', tg.fields)
        for thread in ('-1', '0', 'abc'):
            with self.assertRaises(n.Error):
                n.Telegram('dummy', '-123', thread)
        with patch.object(tg, 'call') as call:
            tg.rich([{'type': 'paragraph', 'text': '<b>&😀'}], [])
            for blocks, files in [([{'type': 'pre', 'text': '😀' * 20000}], []),
                                  ([], [(NAME, PAYLOAD)] * 3), ([], [('../x.zip', PAYLOAD)])]:
                with self.assertRaises(n.Error):
                    tg.rich(blocks, files)
            with patch.object(n, 'MIB', 10):
                tg.rich([], [(NAME, b'x' * 500)])
                with self.assertRaises(n.Error):
                    tg.rich([], [(NAME, b'x' * 501)])
            self.assertEqual(call.call_count, 2)
        self.assertEqual(n.truncate('😀😀', 3), '😀…')

    def test_long_text_artifact_budget(self):
        malicious = '<b>&"😀' * 2000
        gh, tg = github([artifact(i, malicious) for i in range(100)] +
                         [artifact(101, 'miro-kernel-ultra-debug-12')], message=malicious), n.Telegram('dummy', '-123', '')
        run_event = event()
        run_event['workflow_run']['head_branch'] = malicious
        run_event['repository']['default_branch'] = malicious
        with patch.object(tg, 'call') as call:
            n.notify(run_event, REPO, gh, tg)
        _, parts = multipart(call.call_args)
        payload = parts['rich_message'].get_payload(decode=True)
        self.assertLessEqual(len(payload), 32768)
        self.assertIn(b'/artifacts/101', payload)
        self.assertIn(b'/actions/runs/12#artifacts', payload)
        self.assertIn(('a' * 40).encode(), payload)
        blocks = json.loads(payload)['blocks']
        summary = next(b['text'] for b in blocks if b['type'] == 'paragraph' and
                       isinstance(b['text'], str) and b['text'].startswith('Branch:'))
        self.assertIn('CI #12/2', summary)
        self.assertIn('SUCCESS', summary)
        self.assertIn('1m 2s', summary)
        self.assertLess(n.utf16_len(summary), 150)
        source = blocks[0]['cells'][1][1]['text']
        self.assertEqual(source['text'], 'a' * 12)
        self.assertTrue(source['url'].endswith('a' * 40))
        self.assertIn('源码配置非最终 .config', text(blocks))

    def test_http_errors_hide_secrets(self):
        secret_url = 'https://api.telegram.org/botDUMMY-OFFLINE-SECRET/sendRichMessage'
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
        self.assertEqual(n.package_files(package()), ([(NAME, PAYLOAD)], []))
        symlink = zipfile.ZipInfo('link')
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for data in (package('0' * 64), archive([(NAME, PAYLOAD)]),
                     archive([('../escape', b'x')]), archive([(symlink, b'target')]),
                     archive([('other.zip', b'x')]), archive([('SHA256SUMS-standard', b'bad')]), b'not zip'):
            with self.assertRaises(n.Error):
                n.package_files(data)

    def test_50mib_package_link_only(self):
        gh, tg = github([artifact()]), Mock()
        payload = b'x' * 501
        data = archive([('SHA256SUMS-standard', f'{hashlib.sha256(payload).hexdigest()}  {NAME}\n'), (NAME, payload)])
        with patch.object(n, 'MIB', 10):
            self.assertEqual(n.package_files(data), ([], [NAME]))
            gh.download.return_value = data
            n.notify(event(), REPO, gh, tg)
        blocks, files = tg.rich.call_args.args
        self.assertEqual(files, [])
        self.assertIn('50 MiB', text(blocks))
        self.assertIn('/artifacts/10', text(blocks))

    def test_trust_boundary_and_invalid_sha(self):
        baseline = event()['workflow_run']
        self.assertTrue(n.trusted(baseline, REPO, 'main'))
        for changes in ({'event': 'pull_request'}, {'head_branch': 'upstream-candidate'},
                        {'head_repository': {'full_name': 'fork/kernel'}}, {'pull_requests': [{'number': 1}]},
                        {'conclusion': 'skipped'}):
            run = copy.deepcopy(baseline)
            run.update(changes)
            self.assertFalse(n.trusted(run, REPO, 'main'))
            candidate = event()
            candidate['workflow_run'] = run
            gh, tg = Mock(), Mock()
            n.notify(candidate, REPO, gh, tg)
            self.assertEqual(gh.mock_calls, [])
            self.assertEqual(tg.mock_calls, [])
        invalid = event()
        invalid['workflow_run']['head_sha'] = '../main'
        with self.assertRaises(n.Error):
            n.notify(invalid, REPO, Mock(), Mock())

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
