"""Render a small public-events feed without inventing push counts (stdlib only)."""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


def clean(value, limit=80):
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = re.sub(r"[\\`\[\]<>*]", "", text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def mapping(value):
    return value if isinstance(value, dict) else {}


def positive_int(value):
    return type(value) is int and value > 0


def valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) and value != "0" * 40


class GitHub:
    def __init__(self, token="", compare_budget=3):
        self.token = token
        self.compare_budget = compare_budget

    def get(self, path):
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "RobGonWin-Profile-Activity/2.0",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request("https://api.github.com" + path, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    def compare(self, repo, before, head):
        if self.compare_budget <= 0:
            return None
        self.compare_budget -= 1
        try:
            return self.get(f"/repos/{repo}/compare/{before}...{head}?per_page=100")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            # Do not keep querying after permissions, rate-limit, or network failures.
            self.compare_budget = 0
            return None


def push_count(payload, repo, compare=None):
    """Only count complete payloads or a fully checked, linear comparison."""
    commits = payload.get("commits")
    size = payload.get("size")
    if positive_int(size):
        return size
    if isinstance(commits, list) and commits:
        # Events cap embedded commits at 20. At that boundary, completeness is unknown.
        if len(commits) < 20 and all(valid_sha(mapping(c).get("sha")) for c in commits):
            if "size" not in payload and len({c["sha"] for c in commits}) == len(commits):
                return len(commits)
    before, head = payload.get("before"), payload.get("head")
    if not (compare and valid_sha(before) and valid_sha(head) and before != head):
        return None
    if payload.get("forced") or payload.get("created") or payload.get("deleted"):
        return None
    data = mapping(compare(repo, before, head))
    total = data.get("total_commits")
    items = data.get("commits")
    if (data.get("status") == "ahead" and positive_int(total) and total <= 100
            and data.get("ahead_by") == total and data.get("behind_by") == 0
            and mapping(data.get("base_commit")).get("sha") == before
            and mapping(data.get("merge_base_commit")).get("sha") == before
            and isinstance(items, list) and len(items) == total
            and all(valid_sha(mapping(c).get("sha")) for c in items)
            and len({c["sha"] for c in items}) == total
            and items[-1]["sha"] == head):
        return total
    return None


def repo_link(repo):
    return f"[{repo}](https://github.com/{repo})"


def render(events, max_lines=5, compare=None):
    lines, seen = [], set()
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or event.get("public") is False:
            continue
        fingerprint = str(event.get("id") or json.dumps(event, sort_keys=True))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        repo = mapping(event.get("repo")).get("name")
        if not isinstance(repo, str) or not REPO.fullmatch(repo):
            continue
        payload = mapping(event.get("payload"))
        kind, link = event.get("type"), repo_link(repo)
        if kind == "PushEvent":
            count = push_count(payload, repo, compare)
            if count:
                line = f"Pushed {count} {'commit' if count == 1 else 'commits'} to {link}"
            else:
                line = f"Pushed updates to {link}"
        elif kind == "CreateEvent":
            ref = clean(payload.get("ref") or "")
            resource = clean(payload.get("ref_type") or "resource")
            line = f"Created {resource}" + (f" `{ref}`" if ref else "") + f" in {link}"
        elif kind in ("PullRequestEvent", "IssuesEvent", "IssueCommentEvent", "ReleaseEvent"):
            key, noun, default = {
                "PullRequestEvent": ("pull_request", "PR", "updated"),
                "IssuesEvent": ("issue", "issue", "updated"),
                "IssueCommentEvent": ("issue", "", "commented on"),
                "ReleaseEvent": ("release", "release", "published"),
            }[kind]
            item = mapping(payload.get(key))
            title = clean(item.get("tag_name" if key == "release" else "title") or key)
            url = item.get("html_url")
            if not isinstance(url, str) or not url.startswith(f"https://github.com/{repo}/") or re.search(r"[\s()<>]", url):
                url = f"https://github.com/{repo}"
            action = clean(payload.get("action") or default).capitalize()
            line = f"{action} {noun + ' ' if noun else ''}[{title}]({url}) in {link}"
        elif kind == "WatchEvent":
            line = f"Starred {link}"
        elif kind == "ForkEvent":
            fork = mapping(payload.get("forkee")).get("full_name")
            line = f"Forked {link}"
            if isinstance(fork, str) and REPO.fullmatch(fork):
                line += f" to {repo_link(fork)}"
        else:
            continue
        lines.append("- " + line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines or ["- No recent public GitHub activity found."])


def replace_block(text, name, content):
    start, end = f"<!--{name}:start-->", f"<!--{name}:end-->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
        raise ValueError(f"Expected one ordered {name} marker pair")
    return re.sub(re.escape(start) + r".*?" + re.escape(end),
                  lambda _: f"{start}\n{content}\n{end}", text, flags=re.S)


def main():
    username = os.environ["GH_USERNAME"]
    if not re.fullmatch(r"[A-Za-z0-9-]+", username):
        raise ValueError("Invalid GitHub username")
    api = GitHub(os.environ.get("GH_TOKEN", ""))
    # Always use public events, including when an authenticated token is supplied.
    events = api.get(f"/users/{username}/events/public?per_page=30")
    if not isinstance(events, list):
        raise ValueError("Invalid public events response; preserving previous README")
    generated = render(events, max(1, min(10, int(os.environ.get("MAX_LINES", "5")))), api.compare)
    path = Path(os.environ.get("TARGET_FILE", "README.md"))
    path.write_text(replace_block(path.read_text(encoding="utf-8"), "RECENT_ACTIVITY", generated), encoding="utf-8")
    print(generated)


if __name__ == "__main__":
    main()
