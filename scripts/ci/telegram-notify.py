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

def utf16_len(text):
    return len(text.encode('utf-16-le')) // 2

def truncate(text, limit):
    if utf16_len(text) <= limit:
        return text
    result, used = [], 0
    for char in text:
        size = utf16_len(char)
        if used + size > max(0, limit - 1):
            break
        result.append(char)
        used += size
    return ''.join(result) + ('…' if limit else '')

def esc(value, limit=240):
    return html.escape(truncate(str(value), limit), quote=True)

def text_len(text):
    # Telegram 限制按解析 HTML 后的 UTF-16 文本计算。
    return utf16_len(html.unescape(re.sub(r'<[^>]*>', '', text)))

def link(url, label):
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'

class Telegram:
    def __init__(self, token, chat, thread):
        self.base = 'https://api.telegram.org/bot' + token + '/'
        self.fields = {'chat_id': chat}
        thread = thread.strip()
        if thread:
            if not thread.isdecimal() or int(thread) <= 0:
                raise Error('TELEGRAM_THREAD_ID 必须为正整数')
            self.fields['message_thread_id'] = thread
    def call(self, method, body, content_type):
        response = json.loads(http(self.base + method, {'Content-Type': content_type}, body))
        if not response.get('ok'):
            raise Error('Telegram API 拒绝请求，请检查频道权限与 topic 配置')
    def message(self, text):
        if text_len(text) > 4096:
            raise Error('Telegram 消息超过 4096 UTF-16')
        fields = dict(self.fields, text=text, parse_mode='HTML', disable_web_page_preview='true')
        self.call('sendMessage', urllib.parse.urlencode(fields).encode(), 'application/x-www-form-urlencoded')
    def multipart(self, method, fields, files):
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                 for key, value in dict(self.fields, **fields).items()]
        for key, name, content in files:
            if not PACKAGE.fullmatch(name):
                raise Error('Telegram 文件名不是安装包')
            if len(content) > 50*MIB:
                raise Error('Telegram 文件超过 50 MiB')
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
            parts.extend((content, b'\r\n'))
        parts.append(f'--{boundary}--\r\n'.encode())
        self.call(method, b''.join(parts), 'multipart/form-data; boundary=' + boundary)
    def document(self, name, content, caption):
        if text_len(caption) > 1024:
            raise Error('Telegram caption 超过 1024 UTF-16')
        self.multipart('sendDocument', {'caption': caption, 'parse_mode': 'HTML'}, [('document', name, content)])
    def album(self, files, caption):
        if not 2 <= len(files) <= 10:
            raise Error('Telegram album 需要 2–10 个文件')
        if text_len(caption) > 1024:
            raise Error('Telegram caption 超过 1024 UTF-16')
        media = [{'type': 'document', 'media': f'attach://file{i}'} for i in range(len(files))]
        media[0].update(caption=caption, parse_mode='HTML')
        self.multipart('sendMediaGroup', {'media': json.dumps(media, ensure_ascii=False)},
                       [(f'file{i}', name, content) for i, (name, content) in enumerate(files)])

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
                else:
                    if hashes.get(item.filename) != hashlib.sha256(content).hexdigest():
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

def notification_text(run, repo, commit, jobs, artifacts, uploaded, notes, limit):
    run_id, attempt = int(run['id']), int(run.get('run_attempt', 1))
    sha = run['head_sha']
    base = 'https://github.com/' + repo
    run_url = f'{base}/actions/runs/{run_id}/attempts/{attempt}'
    meta = commit['commit']
    parent = commit.get('parents', [])
    compare = f"{base}/compare/{parent[0]['sha']}...{sha}" if parent else f'{base}/commit/{sha}'
    start = datetime.datetime.fromisoformat(run['run_started_at'].replace('Z', '+00:00'))
    end = datetime.datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
    seconds = max(0, int((end-start).total_seconds()))
    status = {'success': '成功 / SUCCESS', 'failure': '失败 / FAILURE', 'cancelled': '取消 / CANCELLED'}[run['conclusion']]
    core = (f'<b>Kernel CI/CD — {status}</b>\n'
            f"Branch: {esc(run.get('head_branch') or 'tag', 100)} · Duration: {seconds//60}m {seconds%60}s\n"
            f'Commit: <code>{sha}</code>\n'
            f'{link(base + "/commit/" + sha, "Commit")} · {link(compare, "Compare")} · {link(run_url, "Logs")}\n')
    artifact_url = f'{base}/actions/runs/{run_id}#artifacts'
    artifact_lines = []
    for artifact in artifacts:
        url = f"{base}/actions/runs/{run_id}/artifacts/{int(artifact['id'])}"
        state = ' · 过期 / expired' if artifact.get('expired') else ''
        if int(artifact['size_in_bytes']) > 200*MIB:
            state += ' · 超限 / oversize'
        artifact_lines.append(f"• {link(url, artifact['name'])}{state}\n")
    footer = (f'Packages: {uploaded}/4 · SHA256/symbols/logs 仅链接\n'
              'GitHub 下载需登录，受保留期限限制\n')
    artifacts_text = '<b>Artifacts</b>\n' + ''.join(artifact_lines)
    # 优先保留状态、完整 commit 与所有产物链接；无法容纳时使用运行总产物入口。
    message = meta['message'].splitlines()
    title = message[0] if message else ''
    summary_min = 'Summary: ' + esc(title, 24) + '\n'
    if text_len(core + summary_min + artifacts_text + footer) > limit:
        artifacts_text = link(artifact_url, f'全部产物 / All artifacts ({len(artifacts)})') + '\n'
    text = core + artifacts_text + footer
    remaining = limit - text_len(text)
    if remaining > len('Summary: \n'):
        summary = 'Summary: ' + esc(title, min(300, remaining - len('Summary: \n'))) + '\n'
        text = core + summary + artifacts_text + footer
    # 不下载日志正文，仅报告 job/step 名称和状态，避免转发敏感日志。
    details = list(notes)
    failed = [j for j in jobs if j.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'startup_failure', 'action_required')]
    for job in failed[:4]:
        job_url = f"{base}/actions/runs/{run_id}/job/{int(job['id'])}"
        details.append(f"• {link(job_url, job['name'])}: {esc(job['conclusion'], 30)}")
        for step in [s for s in job.get('steps', []) if s.get('conclusion') in ('failure', 'cancelled')][:3]:
            details.append(f"  ↳ {esc(step['name'], 100)} ({esc(step['conclusion'], 30)})")
    for detail in details:
        if text_len(text + detail + '\n') <= limit:
            text += detail + '\n'
    return text

def notify(event, repo, gh, tg):
    run = event['workflow_run']
    if not trusted(run, repo, event.get('repository', {}).get('default_branch')):
        print('跳过不受信任或无需通知的运行')
        return
    run_id, attempt = int(run['id']), int(run.get('run_attempt', 1))
    sha = run['head_sha']
    if not re.fullmatch(r'[a-fA-F0-9]{40}', sha):
        raise Error('运行 SHA 无效')
    commit = gh.get('/commits/' + sha)
    jobs = gh.pages(f'/actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs')
    artifacts = gh.pages(f'/actions/runs/{run_id}/artifacts', 'artifacts')
    files, notes, seen = [], [], set()
    for artifact in artifacts:
        if (run['conclusion'] != 'success' or artifact.get('expired') or artifact['name'] not in
                (f'miro-kernel-ultra-release-{run_id}', f'miro-kernel-ultra-debug-{run_id}')
                or int(artifact['size_in_bytes']) > 200*MIB or len(files) >= 4):
            continue
        packages, skipped = package_files(gh.download(artifact['id']))
        for name, content in packages:
            if name not in seen and len(files) < 4:
                files.append((name, content))
                seen.add(name)
        for name in skipped:
            notes.append(f'{esc(name)} 超过 50 MiB，仅提供 artifact 链接')
    text = notification_text(run, repo, commit, jobs, artifacts, len(files), notes, 1024 if files else 4096)
    if len(files) >= 2:
        tg.album(files, text)
    elif files:
        tg.document(*files[0], text)
    else:
        tg.message(text)

def main():
    if sys.argv[1:] == ['--self-test']:
        import pathlib
        import runpy
        runpy.run_path(str(pathlib.Path(__file__).with_name('test-telegram-notify.py')), run_name='__main__')
        return
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not chat:
        raise Error('未读取到 TELEGRAM_CHAT_ID；请检查 Telegram Bot 环境或仓库的 Variables/Secrets 配置')
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise Error('未读取到 TELEGRAM_BOT_TOKEN；请检查 Telegram Bot 环境或仓库的 Secret 配置')
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
