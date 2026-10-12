#!/usr/bin/env python3
"""标准库 Telegram 通知器。"""
import datetime
import hashlib
import html
import io
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

MIB = 1048576
SUMS = ('SHA256SUMS-standard', 'SHA256SUMS-kpatch-exp', 'SHA256SUMS-debug', 'SHA256SUMS-debug-kpatch-exp')
PACKAGE = re.compile(r'miro-kernel-ultra-[A-Za-z0-9_.-]+\.zip\Z')

class Error(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def http(url, headers=None, data=None, limit=4*MIB, redirect=False):
    # 手动处理签名存储跳转，GitHub token 不传给存储服务。
    try:
        with urllib.request.build_opener(NoRedirect()).open(
                urllib.request.Request(url, headers=headers or {}, data=data), timeout=120) as response:
            if int(response.headers.get('Content-Length', '0')) > limit:
                raise Error('HTTP 响应大小超限')
            body = response.read(limit + 1)
            if len(body) > limit:
                raise Error('HTTP 响应大小超限')
            return body
    except urllib.error.HTTPError as exc:
        if redirect and exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get('Location', '')
            target = urllib.parse.urlsplit(location)
            if (target.scheme != 'https' or target.username or target.password
                    or target.port not in (None, 443) or not (target.hostname or '').endswith(
                        ('.blob.core.windows.net', '.githubusercontent.com'))):
                raise Error('拒绝不受信任的下载跳转') from None
            return http(location, limit=limit)
        # 不打印请求 URL、响应或原始异常，避免泄露 bot token。
        raise Error(f'HTTP 请求失败，状态码 {exc.code}') from None
    except (urllib.error.URLError, OSError, ValueError):
        raise Error('HTTP 连接、超时或格式错误') from None

class GitHub:
    def __init__(self, repo, token):
        self.base = 'https://api.github.com/repos/' + repo
        self.headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28'}
    def get(self, path):
        return json.loads(http(self.base + path, self.headers))
    def pages(self, path, key):
        result = []
        for page in range(1, 21):
            items = self.get(f'{path}?per_page=100&page={page}')[key]
            result.extend(items)
            if len(items) < 100:
                return result
        raise Error('GitHub 分页超过安全限制')
    def download(self, artifact_id):
        return http(f'{self.base}/actions/artifacts/{int(artifact_id)}/zip', self.headers,
                    limit=200*MIB, redirect=True)

def esc(value, limit=240):
    text = str(value)
    return html.escape(text if len(text) <= limit else text[:limit-1] + '…', quote=True)

def link(url, label):
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'

class Telegram:
    def __init__(self, token, chat, thread):
        self.base = 'https://api.telegram.org/bot' + token + '/'
        self.fields = {'chat_id': chat}
        if thread:
            if not thread.isdecimal() or int(thread) <= 0:
                raise Error('TELEGRAM_THREAD_ID 必须为正整数')
            self.fields['message_thread_id'] = thread
    def call(self, method, body, content_type):
        response = json.loads(http(self.base + method, {'Content-Type': content_type}, body))
        if not response.get('ok'):
            raise Error('Telegram API 拒绝请求，请检查频道权限与 topic 配置')
    def message(self, text):
        fields = dict(self.fields, text=text, parse_mode='HTML', disable_web_page_preview='true')
        self.call('sendMessage', urllib.parse.urlencode(fields).encode(), 'application/x-www-form-urlencoded')
    def document(self, name, content):
        if len(content) > 50*MIB:
            raise Error('Telegram 文件超过 50 MiB')
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                 for key, value in self.fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        parts.extend((content, f'\r\n--{boundary}--\r\n'.encode()))
        self.call('sendDocument', b''.join(parts), 'multipart/form-data; boundary=' + boundary)

def package_files(data):
    # 只读取外层扁平 ZIP；内层安装包为不透明数据，不解压、不执行。
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            items = archive.infolist()
            if len(items) > 32 or sum(x.file_size for x in items) > 512*MIB:
                raise Error('artifact 成员数量或展开大小超限')
            seen = set()
            for item in items:
                name = item.filename
                if (not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..') or name in seen
                        or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1
                        or item.file_size > 200*MIB or item.file_size > max(item.compress_size, 1)*200):
                    raise Error('artifact 路径、类型或压缩数据不安全')
                seen.add(name)
                if name.endswith('.zip') and not PACKAGE.fullmatch(name):
                    raise Error('拒绝非安装包嵌套 ZIP')
            selected = sorted((x for x in items if x.filename in SUMS or PACKAGE.fullmatch(x.filename)),
                              key=lambda x: (x.filename not in SUMS, x.filename))
            files, skipped, hashes = [], [], {}
            for item in selected:
                if item.file_size > 50*MIB:
                    skipped.append(item.filename)
                    continue
                if item.filename in SUMS and item.file_size > 65536:
                    raise Error('校验文件过大')
                content = archive.read(item)
                if item.filename in SUMS:
                    for line in content.decode('ascii').splitlines():
                        match = re.fullmatch(r'([a-fA-F0-9]{64}) [ *]([A-Za-z0-9_.-]+)', line)
                        if not match:
                            raise Error('SHA256 校验格式错误')
                        digest, name = match.groups()
                        if name in hashes and hashes[name] != digest.lower():
                            raise Error('SHA256 记录冲突')
                        hashes[name] = digest.lower()
                elif hashes.get(item.filename) != hashlib.sha256(content).hexdigest():
                    raise Error('安装包 SHA256 缺失或校验失败')
                files.append((item.filename, content))
            return files, skipped
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, NotImplementedError):
        raise Error('artifact ZIP 或校验文件无效') from None

def trusted(run, repo, default_branch):
    return (run.get('name') == 'Kernel CI/CD' and run.get('status') == 'completed'
            and bool(default_branch) and run.get('head_branch') == default_branch
            and run.get('event') in ('push', 'schedule', 'workflow_dispatch') and not run.get('pull_requests')
            and (run.get('head_repository') or {}).get('full_name') == repo
            and run.get('conclusion') in ('success', 'failure', 'cancelled'))

def notify(event, repo, gh, tg):
    run = event['workflow_run']
    if not trusted(run, repo, event.get('repository', {}).get('default_branch')):
        print('跳过不受信任或无需通知的运行')
        return
    run_id, attempt = int(run['id']), int(run.get('run_attempt', 1))
    sha = run['head_sha']
    if not re.fullmatch(r'[a-fA-F0-9]{40}', sha):
        raise Error('运行 SHA 无效')
    base = 'https://github.com/' + repo
    run_url = f'{base}/actions/runs/{run_id}/attempts/{attempt}'
    commit = gh.get('/commits/' + sha)
    jobs = gh.pages(f'/actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs')
    artifacts = gh.pages(f'/actions/runs/{run_id}/artifacts', 'artifacts')
    meta = commit['commit']
    parent = commit.get('parents', [])
    compare = f"{base}/compare/{parent[0]['sha']}...{sha}" if parent else f'{base}/commit/{sha}'
    start = datetime.datetime.fromisoformat(run['run_started_at'].replace('Z', '+00:00'))
    end = datetime.datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
    seconds = max(0, int((end-start).total_seconds()))
    status = {'success': '成功 / SUCCESS', 'failure': '失败 / FAILURE', 'cancelled': '取消 / CANCELLED'}[run['conclusion']]
    text = (f'<b>Kernel CI/CD — {status}</b>\n<b>仓库 / Repository:</b> {esc(repo)}\n'
            f"<b>分支 / Branch:</b> {esc(run.get('head_branch') or 'tag', 100)}\n"
            f'<b>运行 / Run:</b> {link(run_url, str(run_id))} · attempt {attempt}\n'
            f'<b>耗时 / Duration:</b> {seconds//60}m {seconds%60}s\n\n<b>提交 / Commit</b>\n'
            f"{esc(meta['message'].splitlines()[0], 300)}\n<b>作者 / Author:</b> {esc(meta['author']['name'], 100)}\n"
            f'<code>{sha}</code>\n{link(base + "/commit/" + sha, "Commit")} · '
            f'{link(compare, "比较 / Compare (parent → HEAD)")}\n\n<b>错误摘要 / Failure summary</b>\n')
    # 不下载日志正文，仅报告 job/step 名称和状态，避免转发敏感日志。
    failed = [j for j in jobs if j.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'startup_failure', 'action_required')]
    for job in failed[:4]:
        job_url = f"{base}/actions/runs/{run_id}/job/{int(job['id'])}"
        text += f"• {link(job_url, job['name'])}: {esc(job['conclusion'], 30)}\n"
        for step in [s for s in job.get('steps', []) if s.get('conclusion') in ('failure', 'cancelled')][:3]:
            text += f"  ↳ {esc(step['name'], 100)} ({esc(step['conclusion'], 30)})\n"
    if not failed:
        text += '无失败任务 / No failed jobs\n'
    tg.message(text + '日志原文不转发 / Raw logs withheld\n' + link(run_url, '完整日志 / Logs'))
    tg.message('<b>构建产物 / Artifacts</b>\n安装包与 SHA256 优先上传；符号、日志、原始镜像仅链接。\nGitHub 下载需要登录并受保留期限限制 / Sign-in and retention apply.')
    count = 0
    for artifact in artifacts:
        url = f"{base}/actions/runs/{run_id}/artifacts/{int(artifact['id'])}"
        expired = artifact.get('expired', False)
        tg.message(f"• {link(url, artifact['name'])}\n大小 / Size: {int(artifact['size_in_bytes'])/MIB:.1f} MiB\n"
                   f"过期 / Expired: {'是 / yes' if expired else '否 / no'} · {esc(artifact.get('expires_at', 'unknown'), 40)}")
        if (run['conclusion'] != 'success' or expired or artifact['name'] not in
                (f'miro-kernel-ultra-release-{run_id}', f'miro-kernel-ultra-debug-{run_id}')):
            continue
        if artifact['size_in_bytes'] > 200*MIB:
            tg.message('发布包 artifact 超过 200 MiB 下载上限，仅提供链接 / Download limit exceeded.')
            continue
        files, skipped = package_files(gh.download(artifact['id']))
        for name, content in files:
            tg.document(name, content)
            count += bool(PACKAGE.fullmatch(name))
        for name in skipped:
            tg.message(f'{esc(name)} 超过 50 MiB，仅提供 {link(url, "下载 / Download")}。')
    if run['conclusion'] == 'success':
        tg.message(f'<b>安装包上传 / Packages uploaded:</b> {count}/4\n缺失、过期或超限的包请查看 artifact 链接 / See artifact links for remaining packages.')

def main():
    if sys.argv[1:] == ['--self-test']:
        import pathlib
        import runpy
        runpy.run_path(str(pathlib.Path(__file__).with_name('test-telegram-notify.py')), run_name='__main__')
        return
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not chat:
        print('::warning::未配置 vars.TELEGRAM_CHAT_ID，跳过 Telegram 通知；不会猜测目标频道')
        return
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        print('未配置 TELEGRAM_BOT_TOKEN，跳过通知')
        return
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Error('GITHUB_REPOSITORY 无效')
    with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as source:
        event = json.load(source)
    notify(event, repo, GitHub(repo, os.environ['GITHUB_TOKEN']), Telegram(
        token, chat, os.environ.get('TELEGRAM_THREAD_ID', '')))

if __name__ == '__main__':
    try:
        main()
    except Error as exc:
        print('通知失败: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('通知失败: 意外数据或环境错误（详情隐藏以保护密钥）', file=sys.stderr)
        sys.exit(1)
