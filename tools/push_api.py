"""push_api.py —— 当 github.com:443 被墙/重置、但 api.github.com 可通时，用 REST API 推送。

背景：本机实测 `git push` 常报
    fatal: unable to access 'https://github.com/...': Recv failure: Connection was reset
而 `curl https://api.github.com/` 是 200。这时用本脚本把本地新提交"搬"上去。

用法（需要已登录 `gh`）：
    python tools/push_api.py                # 推当前分支到同名远端分支
    python tools/push_api.py --dry-run      # 只看要推哪些文件

原理：Git Data API —— 建 blob → 建 tree（base_tree=远端 HEAD 的 tree）→ 建 commit →
      更新 ref。author/committer/时间戳都照抄本地，所以**新 commit 的 SHA 通常与本地完全一致**。

限制：
- 只处理文件内容变更（新增/修改/删除），不处理子模块、LFS。
- 单文件 < 100MB（API 限制），本仓库远小于此。
- 网络恢复后建议仍跑一次 `git fetch` 确认一致。
"""
import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def run(*args, check=True):
    p = subprocess.run(args, capture_output=True)
    if check and p.returncode != 0:
        sys.exit(f"命令失败: {' '.join(args)}\n{p.stderr.decode('utf-8', 'replace')}")
    return p.stdout


def git(*args, check=True):
    # core.quotepath=false：否则中文路径会被转成 "\350\256..." 带引号的形式
    return run("git", "-c", "core.quotepath=false", *args, check=check).decode("utf-8", "replace")


def api(path, method="GET", body=None, token=""):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "kidbox-push-api",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"API {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")


def gh_token():
    for cmd in (["gh", "auth", "token"], [r"C:\Program Files\GitHub CLI\gh.exe", "auth", "token"]):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except FileNotFoundError:
            continue
    sys.exit("拿不到 GitHub token，请先 `gh auth login`")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    token = gh_token()
    branch = a.branch or git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "HEAD":
        sys.exit("当前是 detached HEAD，请先切到分支")

    url = git("remote", "get-url", a.remote).strip()
    # https://github.com/<owner>/<repo>.git
    parts = url.rstrip("/").removesuffix(".git").split("/")
    owner, repo = parts[-2], parts[-1]
    print(f"仓库: {owner}/{repo}  分支: {branch}")

    head = git("rev-parse", "HEAD").strip()
    base = git("rev-parse", "HEAD~1", check=False).strip()
    msg = git("log", "-1", "--format=%B").strip()
    ident = git("log", "-1", "--format=%an|%ae|%aI|%cn|%ce|%cI").strip().split("|")

    try:
        ref = api(f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token=token)
        remote_head = ref["object"]["sha"]
    except SystemExit:
        sys.exit(f"远端没有分支 {branch}，请先用 git push 建分支")

    print(f"本地 HEAD  : {head[:9]}")
    print(f"远端 HEAD  : {remote_head[:9]}")
    if remote_head == head:
        print("已经一致，无需推送"); return
    if remote_head != base:
        # 两边分叉（常见于上次走 API 推送导致 SHA 不同）：改用"全量树"模式——
        # 直接把本地 HEAD 的完整快照作为一个新提交接到远端 HEAD 后面。
        print("远端 HEAD 与本地父提交不同 → 使用全量树模式（内容以本地为准）")
        full_tree = True
    else:
        full_tree = False

    changed = [x for x in git("diff", "--name-only", "--diff-filter=ACMR", base, head).split("\n") if x] if not full_tree else []
    deleted = [x for x in git("diff", "--name-only", "--diff-filter=D", base, head).split("\n") if x] if not full_tree else []
    if full_tree:
        listing = [l for l in git("ls-tree", "-r", "HEAD").split("\n") if l.strip()]
        changed = [l.split("\t", 1)[1] for l in listing]
        print(f"全量文件: {len(changed)}")
    else:
        print(f"待推: 改 {len(changed)} / 删 {len(deleted)}")
    for p in changed:
        print("  +", p)
    for p in deleted:
        print("  -", p)
    if a.dry_run:
        return

    tree_sha = api(f"/repos/{owner}/{repo}/git/commits/{remote_head}", token=token)["tree"]["sha"]
    entries = []
    for p in changed:
        data = run("git", "-c", "core.quotepath=false", "show", f"HEAD:{p}")
        blob = api(f"/repos/{owner}/{repo}/git/blobs", "POST",
                   {"content": base64.b64encode(data).decode(), "encoding": "base64"}, token)
        mode = git("ls-tree", "HEAD", "--", p).split()[0]   # 100644 / 100755
        entries.append({"path": p, "mode": mode, "type": "blob", "sha": blob["sha"]})
    for p in deleted:
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    new_tree = api(f"/repos/{owner}/{repo}/git/trees", "POST",
                   {"base_tree": tree_sha, "tree": entries}, token)["sha"]
    commit = api(f"/repos/{owner}/{repo}/git/commits", "POST", {
        "message": msg, "tree": new_tree, "parents": [remote_head],
        "author": {"name": ident[0], "email": ident[1], "date": ident[2]},
        "committer": {"name": ident[3], "email": ident[4], "date": ident[5]},
    }, token)
    api(f"/repos/{owner}/{repo}/git/refs/heads/{branch}", "PATCH",
        {"sha": commit["sha"], "force": False}, token)

    print(f"已推送: {commit['sha'][:9]}")
    print("与本地 SHA 一致:", commit["sha"] == head)
    if commit["sha"] != head:
        print("（内容一致但 SHA 不同，通常是 file mode 差异；本机 commit 保留，等网络恢复后 "
              "git fetch && git reset --hard origin/" + branch + " 对齐）")


if __name__ == "__main__":
    main()
