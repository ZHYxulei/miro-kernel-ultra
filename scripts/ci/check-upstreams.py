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


def component_records(issues, prefix, sha_count):
    pattern = re.escape(prefix) + ":".join([r"[0-9a-f]{40}"] * sha_count) + r" -->"
    records = [i for i in issues if not i.get("pull_request") and trusted(i)
               and re.match(pattern, i.get("body") or "")]
    return sorted(records, key=lambda i: (i.get("updated_at", i.get("created_at", "")), i.get("number", 0)), reverse=True)


def plan_report(reports, records, marker, report):
    opened = next((i for i in records if i.get("state") == "open"), None)
    if opened:
        if not (opened.get("body") or "").startswith(marker):
            reports.append(("PATCH", f"issues/{opened['number']}", report))
    elif not any((i.get("body") or "").startswith(marker) for i in records):
        reports.append(("POST", "issues", report))


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
                reports.append(("POST", "issues", {"title": f"[upstream] {source['path']} 锁定 {selected['ref']}",
                    "body": f"<!-- upstream-release-pin:{source['path']}:{selected['ref']}:{sha} -->\n官方 release 的已解析提交 SHA；tag 重写将停止自动更新。"}))
        else:
            selected, sha = source, resolve(source)
        old = links[source["path"]]
        if sha == old:
            continue
        repo = source["url"].removeprefix("https://github.com/").removesuffix(".git")
        status = api.call("GET", f"compare/{old}...{sha}", repo=repo)["status"]
        if status == "behind":
            print(f"::notice::{source['path']} 当前版本已包含上游（behind），保留当前版本，不需人工适配")
            continue
        if not safe_update(status):
            prefix = f"<!-- upstream-non-ff:{source['path']}:"
            marker = f"{prefix}{old}:{sha} -->"
            records = component_records(issues, prefix, 2)
            plan_report(reports, records, marker, {
                "title": f"[upstream] {source['path']} 非快进更新需人工适配 {sha[:12]}",
                "body": marker + f"\n来源: {source['url']}\nref: `{selected['ref']}`\n"
                + f"当前: `{old}`\n上游: `{sha}`\n比较状态: `{status}`\n"
                + "保留当前 fork 定制，不创建该子模块更新候选；需人工合并、编译和设备验证。"})
            print(f"::warning::{source['path']} 非快进更新（{status}），保留当前版本并报告人工处理")
            continue
        updates[source["path"]] = sha
        notes.append(f"- {source['path']}: `{old}` → `{sha}` ({source['url']} {selected['ref']})")
    for source in config["observations"]:
        sha = resolve(source)
        prefix = f"<!-- upstream-observation:{source['id']}:"
        records = component_records(issues, prefix, 1)
        previous = records[0]["body"][len(prefix):].split(" -->")[0] if records else source.get("observed_sha")
        if previous == sha:
            continue
        if previous is None:
            print(f"::notice::{source['id']} 首次观察 baseline `{sha}`；仅输出基线，未持久化，不是未完成移植，不创建 issue")
            continue
        marker = f"{prefix}{sha} -->"
        body = marker + f"\n来源: {source['url']}\nref: `{source['ref']}`\n"
        body += f"观察变化 `{previous}` → `{sha}`；需要人工回移植，未导入源码。"
        body += "\n仅报告，不创建候选 PR、不启动构建、不自动覆盖源码。\n" + source.get("scope", "")
        plan_report(reports, records, marker, {
            "title": f"[upstream] {source['id']} 需要人工回移植 {sha[:12]}", "body": body})
    for method, path, report in reports:
        api.call(method, path, report)
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

    def run_checker(self, config, issues=(), status="diverged", fail_after=None):
        api = Mock(repo="owner/repo")
        def call(method, path, *args, **kwargs):
            if method != "GET":
                return {}
            if path == "":
                return {"default_branch": "main"}
            if path.startswith("git/ref"):
                return {"object": {"sha": "base"}}
            if path.startswith("compare/"):
                return {"status": status}
            return {"tree": [{"path": s["path"], "sha": "a" * 40, "mode": "160000"} for s in config["submodules"]]}
        api.call.side_effect = call
        api.pages.side_effect = lambda path: issues if path.startswith("issues") else []
        with patch(__name__ + ".API", return_value=api), patch(__name__ + ".git", return_value="base"), \
             patch.object(Path, "read_text", return_value=json.dumps(config)), \
             patch(__name__ + ".resolve", side_effect=fail_after, return_value="b" * 40), \
             patch("builtins.print") as output:
            if fail_after:
                with self.assertRaises(urllib.error.URLError):
                    main()
            else:
                main()
        return [c.args for c in api.call.call_args_list if c.args[0] != "GET"], output

    def observation_config(self):
        return {"submodules": [], "observations": [{"id": "susfs", "url": "https://gitlab.com/a/b.git",
                "ref": "refs/heads/main", "observed_sha": None}]}

    def observation_issue(self, sha="a", state="open", number=8):
        return {"number": number, "state": state, "user": {"login": BOT},
                "body": "<!-- upstream-observation:susfs:" + sha * 40 + " -->\n首次仅建立观察基线，不是已导入 SHA。"}

    def submodule_config(self):
        return {"submodules": [{"path": "KPatch-Next", "url": "https://github.com/ZHYxulei/KPatch-Next-EXP.git",
                "ref": "refs/heads/main"}], "observations": []}

    def test_first_baseline_no_writes(self):
        config = self.observation_config()
        for _ in range(2):
            writes, output = self.run_checker(config)
            self.assertEqual(writes, [])
            self.assertIn("首次观察 baseline", output.call_args.args[0])
            self.assertIn("未持久化", output.call_args.args[0])
        self.assertIsNone(config["observations"][0]["observed_sha"])

    def test_observation_component_reuse_and_idempotence(self):
        config = self.observation_config()
        issue = self.observation_issue()
        writes, _ = self.run_checker(config, [issue])
        self.assertEqual([(w[0], w[1]) for w in writes], [("PATCH", "issues/8")])
        self.assertIn("需要人工回移植", writes[0][2]["title"])
        self.assertIn("`" + "a" * 40 + "` → `" + "b" * 40, writes[0][2]["body"])
        updated = dict(issue, **writes[0][2], updated_at="2026-10-16")
        newer_number = self.observation_issue("a", state="closed", number=14)
        writes, _ = self.run_checker(config, [newer_number, updated])
        self.assertEqual(writes, [])
        writes, _ = self.run_checker(config, [dict(updated, state="closed")])
        self.assertEqual(writes, [])
        writes, _ = self.run_checker(config, [dict(issue, state="closed")])
        self.assertEqual([(w[0], w[1]) for w in writes], [("POST", "issues")])
        created = dict(issue, **writes[0][2], number=15)
        writes, _ = self.run_checker(config, [created, dict(issue, state="closed")])
        self.assertEqual(writes, [])

    def test_observation_trusted_history_only(self):
        issue = self.observation_issue()
        for invalid in (dict(issue, user={"login": "someone"}), dict(issue, pull_request={"url": "pr"}),
                        dict(issue, body="<!-- upstream-observation:susfs:invalid -->")):
            writes, output = self.run_checker(self.observation_config(), [invalid])
            self.assertEqual(writes, [])
            self.assertIn("首次观察 baseline", output.call_args.args[0])
        config = self.observation_config()
        config["observations"][0]["observed_sha"] = "a" * 40
        writes, _ = self.run_checker(config)
        self.assertEqual([(w[0], w[1]) for w in writes], [("POST", "issues")])

    def test_behind_no_alert_or_candidate(self):
        issue = {"number": 7, "state": "open", "user": {"login": BOT},
                 "body": "<!-- upstream-non-ff:KPatch-Next:" + "a" * 40 + ":" + "b" * 40 + " -->"}
        for issues in ([], [issue]):
            writes, output = self.run_checker(self.submodule_config(), issues, status="behind")
            self.assertEqual(writes, [])
            self.assertIn("::notice::", output.call_args.args[0])
            self.assertIn("保留当前版本", output.call_args.args[0])
            self.assertNotIn("::warning::", output.call_args.args[0])

    def test_non_ff_component_reuse_and_idempotence(self):
        config = self.submodule_config()
        writes, _ = self.run_checker(config)
        self.assertEqual([(w[0], w[1]) for w in writes], [("POST", "issues")])
        issue = dict(writes[0][2], number=7, state="open", user={"login": BOT})
        writes, _ = self.run_checker(config, [issue])
        self.assertEqual(writes, [])
        old = dict(issue, body=issue["body"].replace("b" * 40, "c" * 40))
        writes, _ = self.run_checker(config, [old])
        self.assertEqual([(w[0], w[1]) for w in writes], [("PATCH", "issues/7")])
        self.assertEqual(set(writes[0][2]), {"title", "body"})
        writes, _ = self.run_checker(config, [dict(issue, state="closed")])
        self.assertEqual(writes, [])
        writes, _ = self.run_checker(config, [dict(old, state="closed")])
        self.assertEqual([(w[0], w[1]) for w in writes], [("POST", "issues")])

    def test_network_failure_no_writes(self):
        config = self.submodule_config()
        config["observations"] = self.observation_config()["observations"]
        old = self.observation_issue()
        for issues in ([], [old]):
            writes, _ = self.run_checker(config, issues, fail_after=["b" * 40, urllib.error.URLError("离线")])
            self.assertEqual(writes, [])
        config = self.observation_config()
        config["observations"].append(dict(config["observations"][0], id="other"))
        writes, _ = self.run_checker(config, [old], fail_after=["b" * 40, urllib.error.URLError("离线")])
        self.assertEqual(writes, [])

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


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[__file__])
    else:
        main()
