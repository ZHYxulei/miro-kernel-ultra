#!/usr/bin/env python3
"""标准库 Telegram 通知器。"""
import base64
import datetime
import hashlib
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

class HTTPError(Error):
    def __init__(self, status):
        self.status = status
        super().__init__(f'HTTP 请求失败，状态码 {status}')

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
        raise HTTPError(exc.code) from None
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
    def rich(self, blocks, files):
        # Bot API 10.3 InputRichMessage / InputRichBlockDocument / RichBlockTableCell.
        if len(files) > 2:
            raise Error('通知最多上传两个 release 文件')
        payload = {'blocks': blocks, 'skip_entity_detection': True}
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode('utf-8')) > 32768:
            raise Error('Telegram 富消息超过安全文本预算')
        self.multipart('sendRichMessage', {'rich_message': encoded},
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

def source_snapshot(gh, sha):
    # 所有读取均固定到被通知的 SHA；绝不读取通知工作流 checkout 的配置。
    def content(path):
        try:
            result = gh.get('/contents/' + path + '?ref=' + sha)
            if not isinstance(result, dict):
                return {}, ''
            text = (base64.b64decode(result['content']).decode('utf-8')
                    if result.get('encoding') == 'base64' else '')
            return result, text
        except HTTPError as exc:
            if exc.status != 404:
                raise
            return {}, ''

    _, makefile = content('Makefile')
    version = dict(re.findall(r'^(VERSION|PATCHLEVEL|SUBLEVEL|EXTRAVERSION)[ \t]*=[ \t]*(.*?)[ \t]*$', makefile, re.M))
    kernel = ('.'.join(version[key] for key in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL'))
              + version.get('EXTRAVERSION', '') if all(key in version for key in
                ('VERSION', 'PATCHLEVEL', 'SUBLEVEL')) else '未知')
    config, complete = {}, True
    for path in ('arch/arm64/configs/gki_defconfig', 'arch/arm64/configs/vendor/sun_perf.config',
                 'arch/arm64/configs/vendor/miro_perf.config', 'arch/arm64/configs/droidspaces.fragment'):
        _, text = content(path)
        complete = complete and bool(text)
        for line in text.splitlines():
            match = re.fullmatch(r'(CONFIG_\w+)=(.*)|# (CONFIG_\w+) is not set', line)
            if match:
                key, value, disabled = match.groups()
                key, value = key or disabled, value if key else 'n'
                # build.sh 使用 merge_config.sh -y，内建配置优先于模块。
                if config.get(key) != 'y' or value != 'm':
                    config[key] = value
    def state(key):
        value = config.get(key) if complete else None
        return {'y': '启用 (y)', 'm': '模块 (m)', 'n': '禁用 (n)'}.get(value, '未知')
    submodule, _ = content('KernelSU')
    baka_sha = submodule.get('sha', '')
    baka = (baka_sha if re.fullmatch(r'[a-fA-F0-9]{40}', baka_sha) else '')
    kpatch, _ = content('KPatch-Next')
    kpatch_sha = kpatch.get('sha', '')
    _, susfs = content('include/linux/susfs.h')
    susfs_version = re.search(r'^#define\s+SUSFS_VERSION\s+"([^"]+)"', susfs, re.M)
    return {'kernel': kernel, 'lto': ('ThinLTO' if config.get('CONFIG_LTO_CLANG_THIN') == 'y'
            else 'Full LTO' if config.get('CONFIG_LTO_CLANG_FULL') == 'y'
            else '无 LTO（显式配置）' if config.get('CONFIG_LTO_NONE') == 'y' else '未显式配置')
            if complete else '未知（配置缺失）',
            'features': [(name, state(key)) for name, key in (
                ('BakaSU', 'CONFIG_KSU'), ('SUSFS', 'CONFIG_KSU_SUSFS'),
                ('Re:Kernel', 'CONFIG_REKERNEL'), ('NTsync', 'CONFIG_NTSYNC'),
                ('MGLRU', 'CONFIG_LRU_GEN'), ('MGLRU 默认启用', 'CONFIG_LRU_GEN_ENABLED'))],
            'kpm': kpatch_sha if re.fullmatch(r'[a-fA-F0-9]{40}', kpatch_sha) else '',
            'baka': baka, 'susfs': susfs_version.group(1) if susfs_version else '未知'}


def rich_link(url, text):
    return {'type': 'url', 'text': truncate(str(text), 160), 'url': url}


def rich_status(value):
    style = ('success' if value in ('启用 (y)', '模块 (m)') else
             'danger' if value == '禁用 (n)' else None)
    if style:
        return {'type': 'button', 'button': {'text': value, 'style': style, 'disabled': {}}}
    return value


def rich_table(rows, header=False):
    return {'type': 'table', 'is_bordered': True, 'is_compact': True,
            'cells': [[dict({'align': 'left', 'valign': 'top',
                'text': truncate(value, 80) if isinstance(value, str) else value},
                **({'is_header': True} if header and i == 0 else {}))
                for value in row] for i, row in enumerate(rows)]}


def notification_blocks(run, repo, commit, jobs, artifacts, files, notes, snapshot):
    run_id, attempt = int(run['id']), int(run.get('run_attempt', 1))
    sha = run['head_sha']
    base = 'https://github.com/' + repo
    run_url = f'{base}/actions/runs/{run_id}/attempts/{attempt}'
    parent = commit.get('parents', [])
    compare = f"{base}/compare/{parent[0]['sha']}...{sha}" if parent else f'{base}/commit/{sha}'
    # 附件在最前；普通版排在 KPatch 实验版之前。
    blocks = [{'type': 'document', 'document': {'type': 'document', 'media': f'attach://file{i}'}}
              for i in range(len(files))]
    blocks.append(rich_table([
        ('Kernel', snapshot['kernel']), ('Source', rich_link(f'{base}/commit/{sha}', sha[:12])),
        ('Toolchain', '未记录'), ('LTO', snapshot['lto'])]))
    kpm = (['仅修补版 · ', {'type': 'marked', 'text': '实验'}] if snapshot['kpm'] else '未知')
    blocks.append(rich_table([('功能', '状态'),
        *((name, rich_status(value)) for name, value in snapshot['features']), ('KPM', kpm)], header=True))
    baka = snapshot['baka']
    blocks.extend([
        {'type': 'paragraph', 'text': rich_link('https://github.com/Baka-SU/BakaSU' +
            ('/tree/' + baka if baka else ''), 'BakaSU: ' + (baka[:12] if baka else '未知'))},
        {'type': 'paragraph', 'text': rich_link(f'{base}/blob/{sha}/include/linux/susfs.h',
                                               'SUSFS: ' + snapshot['susfs'])}])
    start = datetime.datetime.fromisoformat(run['run_started_at'].replace('Z', '+00:00'))
    end = datetime.datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
    seconds = max(0, int((end - start).total_seconds()))
    status = {'success': '成功 / SUCCESS', 'failure': '失败 / FAILURE', 'cancelled': '取消 / CANCELLED'}[run['conclusion']]
    blocks.append({'type': 'paragraph', 'text':
        f"Branch: {truncate(run.get('head_branch') or '未知', 60)} · CI #{run_id}/{attempt} · {status} · "
        f'{seconds//60}m {seconds%60}s · {len(files)}/2 release'})
    meta = commit['commit']
    blocks.append({'type': 'pre', 'text': f"Commit: {sha}\nAuthor: " +
                   truncate(str(meta.get('author', {}).get('name', '未知')), 100) + '\n\n' +
                   truncate(meta['message'], 1800)})
    blocks.extend([{'type': 'paragraph', 'text': rich_link(compare, 'Compare')},
                   {'type': 'paragraph', 'text': rich_link(run_url, 'Workflow / Logs')}])
    if snapshot['kpm']:
        blocks.append({'type': 'paragraph', 'text': rich_link(
            'https://github.com/ZHYxulei/KPatch-Next-EXP/tree/' + snapshot['kpm'],
            'KPatch: ' + snapshot['kpm'][:12])})
    # 大量产物时保留统一入口和 debug 链接，限制消息总大小。
    selected = artifacts if len(artifacts) <= 12 else [a for a in artifacts
                if a['name'] == f'miro-kernel-ultra-debug-{run_id}'][:1]
    blocks.append({'type': 'paragraph', 'text': rich_link(f'{base}/actions/runs/{run_id}#artifacts',
                                                       '全部产物 / All artifacts')})
    for item in selected:
        state = ' · 仅下载链接'
        if item.get('expired'):
            state += ' · expired'
        if int(item['size_in_bytes']) > 200*MIB:
            state += ' · oversize'
        blocks.append({'type': 'paragraph', 'text': rich_link(
            f"{base}/actions/runs/{run_id}/artifacts/{int(item['id'])}", truncate(item['name'], 100) + state)})
    blocks.append({'type': 'paragraph', 'text': '源码配置非最终 .config，非启动验证；普通版无 KPM，修补版实验。未知含未显式配置或记录缺失；工具链见日志。Debug/SHA256/symbols/logs 仅链接，下载需登录且有保留期限。'})
    for job in [j for j in jobs if j.get('conclusion') in
                ('failure', 'cancelled', 'timed_out', 'startup_failure', 'action_required')][:4]:
        blocks.append({'type': 'paragraph', 'text': rich_link(
            f"{base}/actions/runs/{run_id}/job/{int(job['id'])}", f"{job['name']}: {job['conclusion']}")})
        for step in [s for s in job.get('steps', []) if s.get('conclusion') in ('failure', 'cancelled')][:3]:
            blocks.append({'type': 'paragraph', 'text': truncate(f"{step['name']}: {step['conclusion']}", 160)})
    blocks.extend({'type': 'paragraph', 'text': truncate(note, 240)} for note in notes[:4])
    return blocks


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
                (f'miro-kernel-ultra-release-{run_id}',)
                or int(artifact['size_in_bytes']) > 200*MIB or len(files) >= 2):
            continue
        packages, skipped = package_files(gh.download(artifact['id']))
        for name, content in packages:
            if name not in seen and 'debug' not in name.lower() and len(files) < 2:
                files.append((name, content))
                seen.add(name)
        for name in skipped:
            notes.append(f'{name} 超过 50 MiB，仅提供 artifact 链接')
    # 留出 multipart 开销；总请求过大时不拆分刷屏，统一使用下载链接。
    if sum(len(content) for _, content in files) > 49*MIB:
        files = []
        notes.append('安装包合计超过上传预算，仅提供 artifact 下载链接')
    files.sort(key=lambda item: ('kpatch' in item[0].lower(), item[0]))
    snapshot = source_snapshot(gh, sha)
    blocks = notification_blocks(run, repo, commit, jobs, artifacts, files, notes, snapshot)
    try:
        tg.rich(blocks, files)
    except HTTPError as exc:
        if exc.status != 413 or not files:
            raise
        print('::warning::Telegram 文件上传被拒绝（413），改发单条下载链接富消息')
        notes.append('文件上传请求过大，仅提供 artifact 下载链接')
        tg.rich(notification_blocks(run, repo, commit, jobs, artifacts, [], notes, snapshot), [])

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
