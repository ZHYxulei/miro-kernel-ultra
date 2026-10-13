#!/usr/bin/env python3
"""仅解析可信来源与仓库元数据，不执行候选代码。"""
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import Mock, patch


BOT = "github-actions[bot]"
TAG = r"v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta|alpha)[0-9]+)?"


def git(*args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        env.pop(name, None)
    return subprocess.check_output(["git", *args], text=True, env=env, timeout=90).strip()


def resolve(s):
    if not re.fullmatch(r"https://(?:github.com|gitlab.com)/[\w.-]+/[\w.-]+\.git", s["url"]):
        raise ValueError("未批准的来源 URL")
    ref = s["ref"]
    if not re.fullmatch(r"refs/(heads|tags)/[\w./-]+", ref) or ".." in ref:
        raise ValueError("非法 ref")
    rows = dict((name, sha) for sha, name in (r.split() for r in git("ls-remote", s["url"], ref, ref + "^{}").splitlines()))
    sha = rows.get(ref + "^{}", rows.get(ref, ""))
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("远端 ref 不存在")
    if s.get("expected_sha", sha) != sha:
        raise ValueError("已锁定 tag 被重写，需人工核查")
    return sha


def latest_release(releases):
    # /releases/latest 排除预发布；使用完整列表并按实际发布时间选择。
    valid = [r for r in releases if not r.get("draft") and r.get("published_at")
             and re.fullmatch(TAG, r.get("tag_name", ""))]
    if not valid:
        raise ValueError("没有合法的官方 release")
    return max(valid, key=lambda r: (r["published_at"], r["id"]))["tag_name"]


def release_source(api, source, issues):
    # 即使发布了新版，也先核查已接受版本，避免 tag 重写被新版掩盖。
    resolve(source)
    repo = source["url"].removeprefix("https://github.com/").removesuffix(".git")
    tag = latest_release(api.pages("releases", repo=repo))
    ref = "refs/tags/" + tag
    pins = dict(source.get("release_pins", {}))
    pins[source["ref"]] = source["expected_sha"]
    prefix = f"<!-- upstream-release-pin:{source['path']}:"
    for issue in issues:
        if issue.get("user", {}).get("login") != BOT:
            continue
        match = re.match(re.escape(prefix) + r"([^:]+):([0-9a-f]{40}) -->", issue.get("body") or "")
        if match:
            pinned_ref, sha = match.groups()
            if pinned_ref in pins and pins[pinned_ref] != sha:
                raise ValueError("历史 release SHA 冲突")
            pins[pinned_ref] = sha
    selected = dict(source, ref=ref)
    selected.pop("expected_sha", None)
    if ref in pins:
        selected["expected_sha"] = pins[ref]
    sha = resolve(selected)
    selected["expected_sha"] = sha
    pins[ref] = sha
    selected["release_pins"] = pins
    return selected, ref not in source.get("release_pins", {}) and not any(
        (i.get("body") or "").startswith(f"{prefix}{ref}:{sha} -->")
        and i.get("user", {}).get("login") == BOT for i in issues)


def safe_update(status):
    return status in ("ahead", "identical")


def key(updates, releases):
    # 基线提交、观察 SHA 均不影响身份，避免默认分支前进导致重复 PR。
    return hashlib.sha256(json.dumps([updates, releases], sort_keys=True).encode()).hexdigest()[:20]


class API:
    def __init__(self):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.token = os.environ["GH_TOKEN"]

    def call(self, method, path, data=None, repo=None):
        url = "https://api.github.com/repos/" + (repo or self.repo)
        if path:
            url += "/" + path.lstrip("/")
        request = urllib.request.Request(url,
            method=method, data=None if data is None else json.dumps(data).encode(),
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def pages(self, path, repo=None):
        sep = "&" if "?" in path else "?"
        for page in range(1, 1000):
            rows = self.call("GET", f"{path}{sep}per_page=100&page={page}", repo=repo)
            yield from rows
            if len(rows) < 100:
                return
        raise RuntimeError("分页超过上限")


def trusted(record):
    return record.get("user", {}).get("login") == BOT


def candidate_exists(prs, marker):
    return any(trusted(p) and marker in (p.get("body") or "") for p in prs)


def dispatch(api, pr, base):
    if not trusted(pr) or not pr.get("head", {}).get("repo") or pr["head"]["repo"]["full_name"] != api.repo:
        return
    # 重新读取当前 SHA；写令牌仅用于元数据请求，不传给候选构建。
    pr = api.call("GET", f"pulls/{pr['number']}")
    if (pr["state"] != "open" or not trusted(pr) or not pr["head"].get("repo")
            or pr["head"]["repo"]["full_name"] != api.repo
            or not pr["head"]["ref"].startswith("automation/upstream-")):
        return
    number, sha = pr["number"], pr["head"]["sha"]
    comments = list(api.pages(f"issues/{number}/comments"))
    for workflow, ref, inputs in (("kernel-ci.yml", pr["head"]["ref"], {}),
                                 ("copilot-review.yml", base, {"pr_number": str(number)})):
        marker = f"<!-- upstream-dispatched:{workflow}:{sha} -->"
        if any(trusted(c) and marker in (c.get("body") or "") for c in comments):
            continue
        data = {"ref": ref}
        if inputs:
            data["inputs"] = inputs
        try:
            api.call("POST", f"actions/workflows/{workflow}/dispatches", data)
        except (urllib.error.URLError, TimeoutError):
            if workflow == "kernel-ci.yml":
                raise
            print("::warning::Copilot 调度失败，下次重试；请检查账户、规则与权限")
            continue
        api.call("POST", f"issues/{number}/comments", {"body": marker + "\n已显式请求工作流；调度成功不代表构建通过或 Copilot 已审查。"})


def main():
    api = API()
    base = api.call("GET", "")["default_branch"]
    head = api.call("GET", "git/ref/heads/" + urllib.parse.quote(base, safe=""))["object"]["sha"]
    if git("rev-parse", "HEAD") != head:
        raise RuntimeError("默认分支已前进，请重试")
    config = json.loads(Path(".github/upstreams.json").read_text())
    modules = configparser.ConfigParser()
    modules.read(".gitmodules")
    links = {e["path"]: e["sha"] for e in api.call("GET", "git/trees/" + head)["tree"] if e["mode"] == "160000"}
    issues = list(api.pages("issues?state=all&sort=created&direction=desc"))
    prs = list(api.pages("pulls?state=all"))
    updates, releases, notes, reports = {}, {}, [], []
    # 两阶段检查：任一来源失败时不创建 issue、分支、PR 或调度任务。
    for index, source in enumerate(config["submodules"]):
        section = next(x for x in modules.sections() if modules[x].get("path") == source["path"])
        if modules[section]["url"] != source["url"]:
            raise ValueError("来源与 .gitmodules 不一致")
        if source.get("strategy") == "latest-release":
            selected, new_pin = release_source(api, source, issues)
            config["submodules"][index] = selected
            sha = selected["expected_sha"]
            releases[source["path"]] = [selected["ref"], sha]
            if new_pin:
                reports.append({"title": f"[upstream] {source['path']} 锁定 {selected['ref']}",
                    "body": f"<!-- upstream-release-pin:{source['path']}:{selected['ref']}:{sha} -->\n官方 release 的已解析提交 SHA；tag 重写将停止自动更新。"})
        else:
            selected, sha = source, resolve(source)
        old = links[source["path"]]
        if sha == old:
            continue
        repo = source["url"].removeprefix("https://github.com/").removesuffix(".git")
        if not safe_update(api.call("GET", f"compare/{old}...{sha}", repo=repo)["status"]):
            raise ValueError("非快进更新，必须人工保留 fork 定制")
        updates[source["path"]] = sha
        notes.append(f"- {source['path']}: `{old}` → `{sha}` ({source['url']} {selected['ref']})")
    for source in config["observations"]:
        sha = resolve(source)
        marker = f"<!-- upstream-observation:{source['id']}:"
        records = [i for i in issues if not i.get("pull_request") and trusted(i)
                   and (i.get("body") or "").startswith(marker)]
        previous = records[0]["body"].split(marker)[1].split(" -->")[0] if records else source.get("observed_sha")
        if previous == sha:
            continue
        baseline = previous is None
        body = f"{marker}{sha} -->\n来源: {source['url']}\nref: `{source['ref']}`\n"
        body += "首次仅建立观察基线，不是已导入 SHA。" if baseline else f"观察变化 `{previous}` → `{sha}`；需要人工回移植，未导入源码。"
        body += "\n仅报告，不创建候选 PR、不启动构建、不自动覆盖源码。\n" + source.get("scope", "")
        reports.append({"title": f"[upstream] {source['id']} {'观察基线' if baseline else '需要人工回移植'} {sha[:12]}", "body": body})
    for report in reports:
        api.call("POST", "issues", report)
    pending = [p for p in prs if p["state"] == "open" and p["head"]["ref"].startswith("automation/upstream-")]
    if updates:
        identity = key(updates, releases)
        marker = f"<!-- upstream-candidate:{identity} -->"
        branch = "automation/upstream-" + identity
        if not candidate_exists(prs, marker):
            blob = api.call("POST", "git/blobs", {"content": json.dumps(config, ensure_ascii=False, indent=2) + "\n", "encoding": "utf-8"})
            entries = [{"path": ".github/upstreams.json", "mode": "100644", "type": "blob", "sha": blob["sha"]}]
            entries += [{"path": p, "mode": "160000", "type": "commit", "sha": sha} for p, sha in updates.items()]
            tree = api.call("POST", "git/trees", {"base_tree": head, "tree": entries})
            commit = api.call("POST", "git/commits", {"message": "ci: propose upstream updates", "tree": tree["sha"], "parents": [head]})
            try:
                api.call("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
            except urllib.error.HTTPError as e:
                if e.code != 422:
                    raise
                existing = api.call("GET", "git/ref/heads/" + branch)["object"]["sha"]
                if api.call("GET", "git/commits/" + existing)["tree"]["sha"] != tree["sha"]:
                    raise RuntimeError("已有候选分支被修改或基线改变，不覆盖；需人工处理")
            pending.append(api.call("POST", "pulls", {"head": branch, "base": base, "title": "ci: 审查上游子模块更新",
                "body": marker + "\n" + "\n".join(notes) + "\n\n不自动合并或发布；嵌入源码仅观察，未升级。保留 fork 定制，必须人工真机验证。"}))
    for pr in pending:
        dispatch(api, pr, base)


class OfflineTests(unittest.TestCase):
    def test_resolution(self):
        source = {"url": "https://github.com/a/b.git", "ref": "refs/tags/v1"}
        with patch(__name__ + ".git", return_value="a" * 40 + "\trefs/tags/v1\n" + "b" * 40 + "\trefs/tags/v1^{}"):
            self.assertEqual(resolve(source), "b" * 40)
            with self.assertRaises(ValueError):
                resolve(dict(source, expected_sha="a" * 40))
        with patch(__name__ + ".git", return_value=""):
            with self.assertRaises(ValueError):
                resolve(source)
        with self.assertRaises(ValueError):
            resolve(dict(source, url="https://evil.invalid/a.git"))

    def test_release_selection(self):
        rows = [{"id": 1, "tag_name": "v1.0.0", "published_at": "2026-01-01"},
                {"id": 2, "tag_name": "v1.1.0-rc1", "published_at": "2026-02-01", "prerelease": True},
                {"id": 3, "tag_name": "bad/tag", "published_at": "2026-03-01"},
                {"id": 4, "tag_name": "v2.0.0", "published_at": "2026-04-01", "draft": True}]
        self.assertEqual(latest_release(rows), "v1.1.0-rc1")
        with self.assertRaises(ValueError):
            latest_release([])

    def test_fork(self):
        self.assertTrue(safe_update("ahead"))
        for status in ("behind", "diverged", "unknown"):
            self.assertFalse(safe_update(status))

    def test_dedup(self):
        self.assertEqual(key({"a": 1, "b": 2}, {}), key({"b": 2, "a": 1}, {}))
        marker = "<!-- upstream-candidate:abc -->"
        pr = {"body": marker, "user": {"login": BOT}, "state": "closed"}
        self.assertTrue(candidate_exists([pr], marker))
        self.assertFalse(candidate_exists([dict(pr, user={"login": "someone"})], marker))

    def test_dispatch(self):
        api = Mock(repo="owner/repo")
        pr = {"number": 1, "state": "open", "user": {"login": BOT},
              "head": {"ref": "automation/upstream-test", "sha": "a" * 40, "repo": {"full_name": api.repo}}}
        api.call.return_value = pr
        api.pages.return_value = []
        dispatch(api, pr, "main")
        calls = [c.args for c in api.call.call_args_list if c.args[0] == "POST" and "dispatches" in c.args[1]]
        self.assertEqual(calls, [("POST", "actions/workflows/kernel-ci.yml/dispatches", {"ref": pr["head"]["ref"]}),
                                ("POST", "actions/workflows/copilot-review.yml/dispatches", {"ref": "main", "inputs": {"pr_number": "1"}})])
        api.reset_mock()
        api.pages.return_value = [{"user": {"login": BOT}, "body": f"<!-- upstream-dispatched:{w}:{pr['head']['sha']} -->"}
                                 for w in ("kernel-ci.yml", "copilot-review.yml")]
        dispatch(api, pr, "main")
        self.assertFalse(any(c.args[0] == "POST" for c in api.call.call_args_list))

    def test_release_pin_rewrite(self):
        api = Mock()
        api.pages.return_value = [{"id": 2, "tag_name": "v1.1.0-rc1", "published_at": "2026-02-01"}]
        source = {"path": "KernelSU", "url": "https://github.com/a/b.git", "ref": "refs/tags/v1.0.0", "expected_sha": "a" * 40}
        issue = {"user": {"login": BOT}, "body": "<!-- upstream-release-pin:KernelSU:refs/tags/v1.1.0-rc1:" + "b" * 40 + " -->"}
        def remote(*args):
            ref = args[2]
            return ("a" if ref == source["ref"] else "c") * 40 + "\t" + ref
        with patch(__name__ + ".git", side_effect=remote):
            with self.assertRaises(ValueError):
                release_source(api, source, [issue])

    def test_observation_only_no_candidate(self):
        source = {"id": "susfs", "url": "https://gitlab.com/a/b.git", "ref": "refs/heads/main", "observed_sha": None}
        config = {"submodules": [], "observations": [source]}
        api = Mock(repo="owner/repo")
        api.call.side_effect = lambda method, path, *args: ({"default_branch": "main"} if path == "" else
            {"object": {"sha": "base"}} if path.startswith("git/ref") else {"tree": []})
        api.pages.return_value = []
        with patch(__name__ + ".API", return_value=api), patch(__name__ + ".git", return_value="base"), \
             patch.object(Path, "read_text", return_value=json.dumps(config)), patch(__name__ + ".resolve", return_value="b" * 40):
            main()
            posts = [c for c in api.call.call_args_list if c.args[0] == "POST"]
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0].args[1], "issues")
            report = posts[0].args[2]
            api.reset_mock()
            api.pages.side_effect = lambda path: [dict(report, user={"login": BOT})] if path.startswith("issues") else []
            main()
            self.assertFalse(any(c.args[0] == "POST" for c in api.call.call_args_list))
            api.reset_mock()
            api.pages.side_effect = lambda path: [dict(report, body=report["body"].replace("b" * 40, "a" * 40), user={"login": BOT})] if path.startswith("issues") else []
            main()
            posts = [c for c in api.call.call_args_list if c.args[0] == "POST"]
            self.assertEqual([c.args[1] for c in posts], ["issues"])
            self.assertIn("需要人工回移植", posts[0].args[2]["body"])

    def test_dispatch_failure_not_marked(self):
        api = Mock(repo="owner/repo")
        pr = {"number": 1, "state": "open", "user": {"login": BOT},
              "head": {"ref": "automation/upstream-test", "sha": "a" * 40, "repo": {"full_name": api.repo}}}
        api.pages.return_value = []
        def call(method, path, *args):
            if method == "GET":
                return pr
            if path.endswith("dispatches"):
                raise urllib.error.URLError("离线")
        api.call.side_effect = call
        with self.assertRaises(urllib.error.URLError):
            dispatch(api, pr, "main")
        self.assertFalse(any(c.args[1].endswith("comments") for c in api.call.call_args_list))

    def test_api_root_url(self):
        api = API.__new__(API)
        api.repo, api.token = "owner/repo", "offline-token"
        for path, repo, suffix in (("", None, "owner/repo"),
                                   ("pulls", None, "owner/repo/pulls"),
                                   ("/releases", "other/repo", "other/repo/releases")):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b'{}'
            with patch.object(urllib.request, "urlopen", return_value=response) as opened:
                api.call("GET", path, repo=repo)
                self.assertEqual(opened.call_args.args[0].full_url,
                                 "https://api.github.com/repos/" + suffix)

    def test_network_failure_no_writes(self):
        api = Mock(repo="owner/repo")
        api.call.side_effect = [{"default_branch": "main"}, {"object": {"sha": "base"}},
                                {"tree": [{"path": "KernelSU", "sha": "a" * 40, "mode": "160000"}]}]
        api.pages.return_value = []
        config = {"submodules": [{"path": "KernelSU", "url": "https://github.com/Baka-SU/BakaSU.git", "ref": "refs/heads/main"}], "observations": []}
        with patch(__name__ + ".API", return_value=api), patch(__name__ + ".git", return_value="base"), \
             patch.object(Path, "read_text", return_value=json.dumps(config)), \
             patch(__name__ + ".resolve", side_effect=urllib.error.URLError("离线")):
            with self.assertRaises(urllib.error.URLError):
                main()
        self.assertTrue(all(c.args[0] == "GET" for c in api.call.call_args_list))


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[__file__])
    else:
        main()
