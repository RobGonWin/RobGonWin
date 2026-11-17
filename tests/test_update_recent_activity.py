import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update_recent_activity import GitHub, push_count, render, replace_block

BEFORE, HEAD = "a" * 40, "b" * 40


def event(kind="PushEvent", payload=None, **extra):
    return {"type": kind, "repo": {"name": "RobGonWin/example"},
            "payload": payload, **extra}


def comparison():
    return {"status": "ahead", "total_commits": 1, "ahead_by": 1, "behind_by": 0,
            "base_commit": {"sha": BEFORE}, "merge_base_commit": {"sha": BEFORE},
            "commits": [{"sha": HEAD}]}


class ActivityTests(unittest.TestCase):
    def test_one_commit(self):
        self.assertIn("Pushed 1 commit to", render([event(payload={"commits": [{"sha": HEAD}]})]))

    def test_several_commits(self):
        self.assertIn("Pushed 2 commits to", render([event(payload={"commits": [{"sha": BEFORE}, {"sha": HEAD}]})]))

    def test_explicit_size_without_array(self):
        self.assertEqual(push_count({"size": 42}, "a/b"), 42)

    def test_missing_empty_malformed_counts(self):
        for payload in (None, [], {}, {"commits": []}, {"commits": "bad"},
                        {"commits": [None]}, {"size": 0}, {"size": True}, {"size": "2"}):
            with self.subTest(payload=payload):
                result = render([event(payload=payload)])
                self.assertIn("Pushed updates to", result)
                self.assertNotIn("0 commits", result)
                self.assertNotIn("— `commit`", result)

    def test_compare_for_empty_or_missing_array(self):
        for extra in ({}, {"commits": []}):
            lookup = Mock(return_value=comparison())
            self.assertEqual(push_count({"before": BEFORE, "head": HEAD, **extra}, "a/b", lookup), 1)
            lookup.assert_called_once_with("a/b", BEFORE, HEAD)

    def test_unavailable_comparison(self):
        self.assertIsNone(push_count({"before": BEFORE, "head": HEAD}, "a/b", lambda *args: None))

    def test_ambiguous_and_truncated_comparisons(self):
        cases = [{"status": "diverged"}, {"total_commits": 101}, {"commits": []},
                 {"behind_by": 1}, {"ahead_by": 2}, {"base_commit": {}},
                 {"merge_base_commit": {}}, {"commits": [{"sha": BEFORE}]}]
        for change in cases:
            with self.subTest(change=change):
                self.assertIsNone(push_count({"before": BEFORE, "head": HEAD}, "a/b",
                                             lambda *args: {**comparison(), **change}))

    def test_unusual_pushes_skip_comparison(self):
        for change in ({"before": "0" * 40}, {"head": "main"}, {"head": BEFORE},
                       {"forced": True}, {"created": True}, {"deleted": True}):
            lookup = Mock()
            self.assertIsNone(push_count({"before": BEFORE, "head": HEAD, **change}, "a/b", lookup))
            lookup.assert_not_called()

    def test_embedded_array_limit(self):
        self.assertIsNone(push_count({"commits": [{"sha": HEAD}] * 20}, "a/b"))

    def test_request_budget_and_rate_limit(self):
        api = GitHub(compare_budget=2)
        api.get = Mock(return_value={})
        for _ in range(5):
            api.compare("a/b", BEFORE, HEAD)
        self.assertEqual(api.get.call_count, 2)
        api = GitHub()
        api.get = Mock(side_effect=OSError("rate limited"))
        self.assertIsNone(api.compare("a/b", BEFORE, HEAD))
        api.compare("a/b", BEFORE, HEAD)
        self.assertEqual(api.get.call_count, 1)

    def test_existing_event_types(self):
        for kind, text in (("CreateEvent", "Created"), ("PullRequestEvent", "PR"),
                           ("IssuesEvent", "issue"), ("IssueCommentEvent", "Commented on"),
                           ("ReleaseEvent", "release"), ("WatchEvent", "Starred"),
                           ("ForkEvent", "Forked")):
            with self.subTest(kind=kind):
                self.assertIn(text, render([event(kind)]))

    def test_no_public_activity(self):
        for events in ([], None, [event(public=False)], [event("Unknown")]):
            self.assertEqual(render(events), "- No recent public GitHub activity found.")

    def test_duplicate_malformed_events_and_bound(self):
        e = event(id="1")
        self.assertEqual(len(render([None, [], {}, {"repo": []}, e, e]).splitlines()), 1)
        self.assertEqual(len(render([event(id=str(i)) for i in range(10)]).splitlines()), 5)

    def test_markdown_escaping(self):
        result = render([event("IssuesEvent", {"issue": {"title": "[x](bad) <img>", "html_url": "https://bad"}})])
        self.assertNotIn("<img>", result)
        self.assertNotIn("https://bad", result)

    def test_marker_contract_and_literal_replacement(self):
        text = "before\n<!--RECENT_ACTIVITY:start-->old<!--RECENT_ACTIVITY:end-->\nafter"
        result = replace_block(text, "RECENT_ACTIVITY", r"literal \1")
        self.assertIn(r"literal \1", result)
        self.assertTrue(result.startswith("before\n"))
        self.assertTrue(result.endswith("\nafter"))
        for broken in ("", text + text, "<!--RECENT_ACTIVITY:end--><!--RECENT_ACTIVITY:start-->"):
            with self.assertRaises(ValueError):
                replace_block(broken, "RECENT_ACTIVITY", "new")


if __name__ == "__main__":
    unittest.main()
