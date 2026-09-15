"""The offline half of submission.yml: fetch (with fakes) and evaluate."""
import json
import os
import tempfile
import unittest

import helpers
import download
import form
import metadata
import package
import report
import submission

ALICE = {"github_id": 1001, "login": "alice"}
BOB = {"github_id": 2002, "login": "bob"}
LIVE = {"schema": 1, "product": "pak-rat", "catalog_revision": "prod-1",
        "generated_at": "2026-09-12T21:07:21Z", "apps": [{"id": "clock"}], "content": []}


class Workdir:
    def __init__(self, temp, *, zip_bytes=None, body=None, submitter=ALICE, maintainer=False,
                 catalog=LIVE, reviews=(), fetched=None, issue=7, dry_run=False):
        self.path = os.path.join(temp, f"work-{issue}")
        os.makedirs(self.path, exist_ok=True)
        self.dump("issue.json", {"number": issue, "html_url": "https://example.invalid",
                                 "body": helpers.form_body() if body is None else body,
                                 "submitter": submitter, "submitter_is_maintainer": maintainer,
                                 "dry_run": dry_run})
        if zip_bytes is not None:
            helpers.write(os.path.join(self.path, "source.zip"), zip_bytes)
            fetched = fetched or {"download": "ok", "size": len(zip_bytes),
                                  "sha256": package.sha256(zip_bytes)}
        self.dump("fetch.json", fetched or {"download": "skipped"})
        self.dump("catalog.json", catalog)
        self.dump("open_reviews.json", list(reviews))

    def dump(self, name, obj):
        with open(os.path.join(self.path, name), "w") as handle:
            json.dump(obj, handle)


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = helpers.repo_copy(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_eval(self, work, repo=None):
        return submission.evaluate(work.path, repo or self.repo)

    def test_pass_builds_staged_files_and_metadata(self):
        source = helpers.theme_zip(helpers.theme_files())
        work = Workdir(self.temp.name, zip_bytes=source)
        result = self.run_eval(work)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["warnings"], ["theme-icon-off-size"])
        self.assertTrue(result["new_id"])
        with open(os.path.join(work.path, "metadata.json")) as handle:
            record = json.load(handle)
        self.assertEqual(metadata.problems(record, "themes/neon-nights/1.0.0.json"), [])
        with open(os.path.join(work.path, "neon-nights-1.0.0.zip"), "rb") as handle:
            staged = handle.read()
        self.assertEqual(record["artifact"]["sha256"], package.sha256(staged))
        self.assertEqual(record["artifact"]["size"], len(staged))
        self.assertEqual(record["submission"]["source_sha256"], package.sha256(source))
        self.assertEqual(record["owner"], ALICE)
        self.assertEqual(record["license"], "CC-BY-4.0")

        # Rerunning on the same input reproduces the same bytes and record.
        again = Workdir(self.temp.name, zip_bytes=source, issue=8)
        self.run_eval(again)
        with open(os.path.join(again.path, "neon-nights-1.0.0.zip"), "rb") as handle:
            self.assertEqual(handle.read(), staged)

        body = report.review_pr_body(result, record, "https://example.invalid/7",
                                     "https://raw.example/preview.png", "staging-x")
        self.assertIn(report.pr_marker(7), body)
        self.assertEqual(report.marker_issue(body), 7)
        self.assertNotIn("\u2014", body)

    def test_every_problem_in_one_report(self):
        files = helpers.theme_files(license="CC0-1.0")
        files["neon-nights/preview.png"] = helpers.png(100, 100)
        files["neon-nights/__MACOSX/x"] = b""
        body = helpers.form_body(checked=(True, True, False), summary="x" * 200)
        work = Workdir(self.temp.name, zip_bytes=helpers.theme_zip(files), body=body)
        result = self.run_eval(work)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reasons"], ["submission-summary-invalid",
                                             "submission-confirmations",
                                             "theme-hidden-file", "theme-image-dimensions"])
        comment = report.failure_comment(result)
        self.assertEqual(comment.count("| `"), 4)
        # Each file-level problem names the entry to fix.
        self.assertEqual(result["where"]["theme-image-dimensions"], ["neon-nights/preview.png"])
        self.assertEqual(result["where"]["theme-hidden-file"],
                         ["neon-nights/__MACOSX/", "neon-nights/__MACOSX/x"])
        self.assertIn("File: `neon-nights/preview.png`", comment)
        self.assertIn("Files: `neon-nights/__MACOSX/`, `neon-nights/__MACOSX/x`", comment)
        self.assertNotIn("File:", comment.split("submission-confirmations")[1].split("\n")[0])
        self.assertFalse(os.path.exists(os.path.join(work.path, "metadata.json")))

        files = helpers.theme_files(license="CC0-1.0")
        work = Workdir(self.temp.name, zip_bytes=helpers.theme_zip(files), issue=9)
        self.assertEqual(self.run_eval(work)["reasons"], ["submission-license-mismatch"])

    def test_ownership_rules(self):
        owned = helpers.repo_copy(os.path.join(self.temp.name, "owned"),
                                  owners={"neon-nights": ALICE})
        source = helpers.theme_zip(helpers.theme_files(version="1.1.0"))
        cases = [
            (ALICE, False, "pass", False),
            (BOB, False, "fail", False),
            ({"github_id": 9999, "login": "alice"}, False, "fail", False),
            (BOB, True, "pass", True),
        ]
        for number, (who, maintainer, status, override) in enumerate(cases, start=20):
            work = Workdir(self.temp.name, zip_bytes=source, submitter=who,
                           maintainer=maintainer, issue=number)
            result = self.run_eval(work, owned)
            self.assertEqual(result["status"], status, (who, result))
            self.assertEqual(result["maintainer_override"], override)
            if status == "fail":
                self.assertEqual(result["reasons"], ["submission-not-owner"])
            else:
                with open(os.path.join(work.path, "metadata.json")) as handle:
                    self.assertEqual(json.load(handle)["owner"], ALICE)

    def test_versions_reviews_and_lanes(self):
        repo = helpers.repo_copy(os.path.join(self.temp.name, "v"),
                                 owners={"neon-nights": ALICE},
                                 versions={"themes/neon-nights/1.0.0.json": {}})
        same = helpers.theme_zip(helpers.theme_files(version="1.0.0"))
        work = Workdir(self.temp.name, zip_bytes=same, issue=30)
        self.assertEqual(self.run_eval(work, repo)["reasons"], ["submission-version-not-newer"])

        newer = helpers.theme_zip(helpers.theme_files(version="1.0.1"))
        reviews = [{"number": 5, "branch": "submission/neon-nights-v1.0.1", "issue": 99}]
        work = Workdir(self.temp.name, zip_bytes=newer, reviews=reviews, issue=31)
        self.assertEqual(self.run_eval(work, repo)["reasons"], ["submission-in-review"])
        # Its own earlier review PR does not block a resubmission.
        reviews = [{"number": 5, "branch": "submission/neon-nights-v1.0.0", "issue": 32}]
        work = Workdir(self.temp.name, zip_bytes=newer, reviews=reviews, issue=32)
        self.assertEqual(self.run_eval(work, repo)["status"], "pass")

        clock = helpers.theme_zip(helpers.theme_files(theme_id="clock"))
        work = Workdir(self.temp.name, zip_bytes=clock, issue=33)
        self.assertEqual(self.run_eval(work)["reasons"], ["submission-id-taken"])

    def test_download_outcomes(self):
        work = Workdir(self.temp.name, fetched={"download": "too_large"}, issue=40)
        self.assertEqual(self.run_eval(work)["reasons"], ["theme-archive-too-large"])
        work = Workdir(self.temp.name, fetched={"download": "failed"}, issue=41)
        self.assertEqual(self.run_eval(work)["reasons"], ["submission-download-failed"])


class FakeGitHub:
    repository = "Utility-Muffin-Research-Kitchen/leaf-themes"

    def __init__(self, author, maintainers=()):
        self.author, self.maintainers = author, set(maintainers)

    def issue(self, number):
        return {"number": number, "html_url": "https://example.invalid", "state": "open",
                "user": {"id": self.author["github_id"], "login": self.author["login"]},
                "body": helpers.form_body(), "labels": [{"name": "theme-submission"}]}

    def permission(self, login):
        return {"permission": "admin" if login in self.maintainers else "read"}

    def pulls(self, state="open", head_branch=None):
        return [{"number": 3, "head": {"ref": "submission/other-v1.0.0"},
                 "body": report.pr_marker(12)},
                {"number": 4, "head": {"ref": "feature/x"}, "body": report.pr_marker(13)}]


class FetchTest(unittest.TestCase):
    """fetch with GitHub, the attachment and the catalog replaced by fakes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = (submission._github, download.download, submission.fetch_catalog)
        self.zip = helpers.theme_zip(helpers.theme_files())

        def fake_download(url, destination, cap, **kwargs):
            self.assertTrue(form.is_attachment_url(url))
            helpers.write(destination, self.zip)
            return len(self.zip), package.sha256(self.zip)

        def fake_catalog(destination, urls=None):
            helpers.write(destination, json.dumps(LIVE).encode())
            return "fake"

        download.download = fake_download
        submission.fetch_catalog = fake_catalog

    def tearDown(self):
        submission._github, download.download, submission.fetch_catalog = self.saved
        self.temp.cleanup()

    def fetch(self, gh, *extra):
        submission._github = lambda: gh
        work = os.path.join(self.temp.name, "w")
        submission.main(["fetch", "--issue", "7", "--workdir", work, *extra])
        with open(os.path.join(work, "issue.json")) as handle:
            return work, json.load(handle)

    def test_real_identity(self):
        work, issue = self.fetch(FakeGitHub(ALICE, maintainers={"alice"}))
        self.assertEqual(issue["submitter"], ALICE)
        self.assertTrue(issue["submitter_is_maintainer"])
        self.assertFalse(issue["dry_run"])
        with open(os.path.join(work, "open_reviews.json")) as handle:
            self.assertEqual(json.load(handle),
                             [{"number": 3, "branch": "submission/other-v1.0.0", "issue": 12}])

    def test_dry_run_override_is_not_a_maintainer(self):
        repo = helpers.repo_copy(self.temp.name, owners={"neon-nights": ALICE})
        work, issue = self.fetch(FakeGitHub(ALICE, maintainers={"alice"}),
                                 "--as-github-id", "2002", "--dry-run")
        self.assertEqual(issue["submitter"], {"github_id": 2002, "login": "alice"})
        self.assertFalse(issue["submitter_is_maintainer"])
        self.assertTrue(issue["dry_run"])
        result = submission.evaluate(work, repo)
        self.assertEqual(result["reasons"], ["submission-not-owner"])

        work, issue = self.fetch(FakeGitHub(ALICE, maintainers={"alice", "bob"}),
                                 "--as-github-id", "2002", "--as-login", "bob", "--dry-run")
        self.assertTrue(issue["submitter_is_maintainer"])
        result = submission.evaluate(work, repo)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["maintainer_override"])


if __name__ == "__main__":
    unittest.main()


class FileListTest(unittest.TestCase):
    """The file names in a failure comment come from the zip: untrusted text."""

    def test_names_render_inert_and_capped(self):
        hostile = "t/grid/icons/`@everyone|<b>x</b>\n[link](https://evil.invalid).png"
        where = {"theme-image-dimensions": [hostile] + [f"t/grid/icons/{n}.png" for n in range(7)]}
        result = {"reasons": ["theme-image-dimensions"], "warnings": [], "where": where,
                  "theme": None}
        row = [line for line in report.failure_comment(result).splitlines()
               if line.startswith("| `theme-image-dimensions`")][0]
        self.assertEqual(row.count(" | "), 1)          # the cell never splits
        self.assertNotIn("\n", row)
        self.assertNotIn("`@everyone", row)            # backtick replaced, stays in code
        self.assertIn("Files: ", row)
        self.assertIn("and 3 more", row)

    def test_no_files_for_archive_level_rules(self):
        result = {"reasons": ["theme-missing-preview"], "warnings": ["theme-no-art"],
                  "where": {}, "theme": None}
        self.assertNotIn("File", report.failure_comment(result))
