"""publish.yml logic against in-memory fakes of the GitHub API."""
import json
import os
import tempfile
import unittest

import helpers
import catalog
import leafdocs
import package
import publish
from test_catalog import record as make_record

REPO = "Utility-Muffin-Research-Kitchen/leaf-themes"


class FakeReleases:
    repository = REPO

    def __init__(self):
        self.list, self.blobs, self.next_id = [], {}, 1

    def _id(self):
        self.next_id += 1
        return self.next_id

    def release_by_tag(self, tag, draft):
        return next((r for r in self.list if r["tag_name"] == tag and r["draft"] == draft), None)

    def create_release(self, tag, name, body, target, draft):
        release = {"id": self._id(), "tag_name": tag, "draft": draft, "assets": [],
                   "html_url": f"https://github.com/{REPO}/releases/tag/{tag}"}
        self.list.append(release)
        return release

    def update_release(self, release_id, fields):
        release = next(r for r in self.list if r["id"] == release_id)
        release.update({k: v for k, v in fields.items() if k in ("draft", "name", "body")})
        return release

    def delete_release(self, release_id):
        self.list = [r for r in self.list if r["id"] != release_id]

    def release_assets(self, release_id):
        release = next(r for r in self.list if r["id"] == release_id)
        return [dict(a) for a in release["assets"]]

    def upload_asset(self, release_id, name, data, content_type):
        release = next(r for r in self.list if r["id"] == release_id)
        asset = {"id": self._id(), "name": name, "size": len(data),
                 "digest": f"sha256:{package.sha256(data)}"}
        release["assets"].append(asset)
        self.blobs[asset["id"]] = data
        return dict(asset)

    def download_asset(self, asset_id, destination, cap):
        data = self.blobs[asset_id]
        helpers.write(destination, data)
        return len(data), package.sha256(data)


def staged_record(temp):
    """A real canonical zip + preview and the record describing them."""
    source = helpers.write(os.path.join(temp, "src.zip"),
                           helpers.theme_zip(helpers.theme_files()))
    rebuilt = package.rebuild(source)
    preview = package.read_member(rebuilt, "neon-nights/preview.png")
    rec = make_record()
    rec["artifact"].update(size=len(rebuilt), sha256=package.sha256(rebuilt),
                           installed_size=package.installed_size(rebuilt))
    rec["preview"].update(size=len(preview), sha256=package.sha256(preview))
    return rec, rebuilt, preview


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.gh = FakeReleases()
        self.record, self.zip, self.preview = staged_record(self.temp.name)
        draft = self.gh.create_release("staging-neon-nights-v1.0.0", "s", "", "main", True)
        self.gh.upload_asset(draft["id"], "neon-nights-1.0.0.zip", self.zip, "application/zip")
        self.gh.upload_asset(draft["id"], "neon-nights-1.0.0.preview.png", self.preview,
                             "image/png")

    def tearDown(self):
        self.temp.cleanup()

    def publish(self):
        return publish.publish_release(self.gh, self.record, "a" * 40, self.temp.name,
                                       log=lambda *_: None)

    def test_publish_then_rerun(self):
        url = self.publish()
        self.assertTrue(url.endswith("/neon-nights-v1.0.0"))
        self.assertEqual([(r["tag_name"], r["draft"]) for r in self.gh.list],
                         [("neon-nights-v1.0.0", False)])
        published = self.gh.list[0]
        self.assertEqual(sorted(a["name"] for a in published["assets"]),
                         ["neon-nights-1.0.0.preview.png", "neon-nights-1.0.0.zip"])
        # Rerun: nothing new is created.
        self.assertEqual(self.publish(), url)
        self.assertEqual(len(self.gh.list), 1)

    def test_existing_release_with_different_assets_fails_loudly(self):
        self.publish()
        asset = self.gh.list[0]["assets"][0]
        asset["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(publish.PublishError):
            self.publish()

    def test_tampered_draft_is_refused(self):
        draft = self.gh.list[0]
        zip_asset = next(a for a in draft["assets"] if a["name"].endswith(".zip"))
        middle = len(self.zip) // 2
        flipped = bytes([self.zip[middle] ^ 1])
        self.gh.blobs[zip_asset["id"]] = self.zip[:middle] + flipped + self.zip[middle + 1:]
        with self.assertRaises(publish.PublishError):
            self.publish()
        self.assertIsNone(self.gh.release_by_tag("neon-nights-v1.0.0", False))

    def test_missing_draft_is_refused(self):
        self.gh.list = []
        with self.assertRaises(publish.PublishError):
            self.publish()

    def test_leftover_final_draft_is_replaced(self):
        self.gh.create_release("neon-nights-v1.0.0", "x", "", "main", True)
        self.publish()
        self.assertEqual([(r["tag_name"], r["draft"]) for r in self.gh.list],
                         [("neon-nights-v1.0.0", False)])

    def test_added_metadata_filter(self):
        files = [{"filename": "themes/neon-nights/1.0.0.json", "status": "added"},
                 {"filename": "themes/neon-nights/0.9.0.json", "status": "modified"},
                 {"filename": "themes/neon-nights/withdrawn.json", "status": "added"},
                 {"filename": "themes/README.md", "status": "added"},
                 {"filename": "themes/Bad/1.0.0.json", "status": "added"},
                 {"filename": "owners.json", "status": "modified"}]
        self.assertEqual(publish.added_metadata(files), ["themes/neon-nights/1.0.0.json"])

    def test_load_record_checks_owner(self):
        rec = make_record()
        repo = helpers.repo_copy(self.temp.name, owners={"neon-nights": rec["owner"]},
                                 versions={"themes/neon-nights/1.0.0.json": rec})
        self.assertEqual(publish.load_record(repo, "themes/neon-nights/1.0.0.json"), rec)
        with open(os.path.join(repo, "owners.json"), "w") as handle:
            json.dump({"neon-nights": {"github_id": 5, "login": "x"}}, handle)
        with self.assertRaises(publish.PublishError):
            publish.load_record(repo, "themes/neon-nights/1.0.0.json")


class FakeDocs:
    repository = leafdocs.REPOSITORY

    def __init__(self, catalog_obj, verdicts=("success",), merges=(200,)):
        self.commits = {"base": {"files": {leafdocs.CATALOG_PATH:
                                           catalog.dumps(catalog_obj).encode()}}}
        self.branches = {"main": "base"}
        self.prs, self.verdicts, self.merges, self.counter = [], list(verdicts), list(merges), 0

    def branch_sha(self, branch):
        return self.branches.get(branch)

    def file_at(self, path, sha):
        return self.commits[sha]["files"].get(path)

    def commit_files(self, branch, base, files, message, force=True):
        self.counter += 1
        sha = f"c{self.counter}"
        merged = dict(self.commits[base]["files"])
        merged.update(files)
        self.commits[sha] = {"files": merged, "message": message}
        self.branches[branch] = sha
        return sha

    def delete_branch(self, branch):
        self.branches.pop(branch, None)

    def pulls(self, state="open", head_branch=None):
        return [p for p in self.prs if p["state"] == state and p["branch"] == head_branch]

    def create_pull(self, title, head, base, body):
        pr = {"number": 40 + len(self.prs), "branch": head, "state": "open", "title": title,
              "html_url": f"https://github.com/{self.repository}/pull/{40 + len(self.prs)}"}
        self.prs.append(pr)
        return pr

    def update_pull(self, number, fields):
        pr = next(p for p in self.prs if p["number"] == number)
        pr.update(fields)
        return pr

    def check_runs(self, sha, name):
        verdict = self.verdicts.pop(0) if self.verdicts else "success"
        if verdict == "pending":
            return [{"status": "in_progress"}]
        return [{"status": "completed", "conclusion": verdict}]

    def merge_pull(self, number, sha, title, message):
        status = self.merges.pop(0) if self.merges else 200
        if status != 200:
            return status, {"message": "Head branch was modified"}
        pr = next(p for p in self.prs if p["number"] == number)
        pr["state"] = "closed"
        self.merged_message = (title, message)
        self.branches["main"] = sha
        return 200, {"merged": True}


class LeafDocsTest(unittest.TestCase):
    base = {"schema": 1, "product": "pak-rat", "catalog_revision": "prod-1",
            "generated_at": "2026-09-12T21:07:21Z", "apps": [], "content": []}

    def run_publish(self, docs, rec, timeout=5):
        return leafdocs.publish_change(
            docs, branch="themes/neon-nights-v1.0.0",
            edit=lambda current: catalog.add_version(current, rec),
            commit_message="Publish", pr_title="Publish", pr_body="body",
            merge_title="Publish theme", merge_message="Release: x",
            leaf_docs_dir=None, timeout=timeout, log=lambda *_: None)

    def main_catalog(self, docs):
        return json.loads(docs.file_at(leafdocs.CATALOG_PATH, docs.branches["main"]))

    def test_merge_when_green_then_rerun_is_a_no_op(self):
        docs = FakeDocs(self.base)
        rec = make_record()
        outcome = self.run_publish(docs, rec)
        self.assertTrue(outcome.merged)
        self.assertEqual(docs.merged_message, ("Publish theme (#40)", "Release: x"))
        self.assertTrue(catalog.has_version(self.main_catalog(docs), rec))
        self.assertNotIn("themes/neon-nights-v1.0.0", docs.branches)

        again = self.run_publish(docs, rec)
        self.assertTrue(again.already)
        self.assertEqual(len(docs.prs), 1)

    def test_red_check_leaves_the_pr_open(self):
        docs = FakeDocs(self.base, verdicts=["pending", "failure"])
        leafdocs_sleep = leafdocs.time.sleep
        leafdocs.time.sleep = lambda _: None
        try:
            with self.assertRaises(leafdocs.CatalogPRFailed) as caught:
                self.run_publish(docs, make_record())
        finally:
            leafdocs.time.sleep = leafdocs_sleep
        self.assertEqual(caught.exception.pr_url, docs.prs[0]["html_url"])
        self.assertEqual(docs.prs[0]["state"], "open")
        self.assertNotIn("themes", self.main_catalog(docs))

    def test_timeout(self):
        docs = FakeDocs(self.base, verdicts=["pending"] * 100)
        clock = iter(range(0, 10000, 30))
        verdict = leafdocs.wait_for_check(docs, "sha", 60, sleep=lambda _: None,
                                          clock=lambda: next(clock))
        self.assertEqual(verdict, "timeout")

    def test_refused_merge_rebuilds_on_current_main(self):
        docs = FakeDocs(self.base, merges=[405, 200])
        outcome = self.run_publish(docs, make_record())
        self.assertTrue(outcome.merged)
        self.assertEqual(len(docs.prs), 1)
        self.assertEqual(docs.counter, 2)


class DiscordTest(unittest.TestCase):
    def setUp(self):
        import discord
        self.discord = discord
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_new_and_updated_wording(self):
        rec = make_record()
        new = self.discord.payload(rec, REPO, True)
        updated = self.discord.payload(rec, REPO, False)
        self.assertTrue(new["content"].startswith("New theme in Pak Rat"))
        self.assertTrue(updated["content"].startswith("Updated theme in Pak Rat"))
        for body in (new, updated):
            self.assertEqual(body["allowed_mentions"], {"parse": []})
            embed = body["embeds"][0]
            self.assertEqual(embed["image"]["url"], rec["preview"]["url"])
            self.assertEqual(embed["url"],
                             f"https://github.com/{REPO}/releases/tag/neon-nights-v1.0.0")
            fields = {f["name"]: f["value"] for f in embed["fields"]}
            self.assertEqual(fields["License"], "CC BY 4.0")
            self.assertEqual(fields["Version"], "1.0.0")
        json.dumps(new)   # serializable as-is

    def test_malicious_text_stays_inert(self):
        rec = make_record()
        rec["name"] = "@everyone <@&123> [x](https://evil.example)"
        rec["author"] = "@here\n# big"
        rec["summary"] = "<@456> **bold** `code`"
        body = self.discord.payload(rec, REPO, True)
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        text = json.dumps(body)
        self.assertNotIn("<@&123>", body["content"])
        self.assertIn("\\@everyone \\<\\@&123\\>", body["content"])
        self.assertIn("\\[x\\]\\(https", body["content"])
        fields = {f["name"]: f["value"] for f in body["embeds"][0]["fields"]}
        self.assertEqual(fields["Author"], "\\@here # big")
        self.assertNotIn("<@456>", body["embeds"][0]["description"])
        self.assertNotIn("\n", body["content"])
        self.assertIn("allowed_mentions", text)

    def test_skip_when_unset_and_warn_on_failure(self):
        rec = make_record()
        repo = helpers.repo_copy(self.temp.name, versions={"themes/neon-nights/1.0.0.json": rec})
        entries = [{"path": "themes/neon-nights/1.0.0.json", "new_theme": True}]
        logs, sent = [], []
        for webhook in (None, ""):
            posted = self.discord.announce(entries, REPO, webhook, repo,
                                           sender=lambda *a: sent.append(a) or (True, "ok"),
                                           log=logs.append)
            self.assertEqual(posted, 0)
        self.assertEqual(sent, [])
        self.assertTrue(logs[0].startswith("::notice::"))

        logs = []
        posted = self.discord.announce(entries, REPO, "https://discord.invalid/hook", repo,
                                       sender=lambda *a: (False, "HTTP 500"), log=logs.append)
        self.assertEqual(posted, 0)
        self.assertTrue(logs[-1].startswith("::warning::"))
        self.assertNotIn("discord.invalid", logs[-1])

        posted = self.discord.announce(entries + [{"path": "../etc/passwd"}], REPO,
                                       "https://discord.invalid/hook", repo,
                                       sender=lambda *a: (True, "HTTP 204"), log=logs.append)
        self.assertEqual(posted, 1)


if __name__ == "__main__":
    unittest.main()
