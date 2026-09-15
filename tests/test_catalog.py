import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

import helpers
import catalog
import metadata
import policy

BASE = {"schema": 1, "product": "pak-rat", "catalog_revision": "prod-20260912T210721Z",
        "generated_at": "2026-09-12T21:07:21Z", "apps": [], "content": []}


def record(theme_id="neon-nights", version="1.0.0", owner_id=1001, seed=0, **extra):
    manifest = helpers.manifest(theme_id, version, **extra)
    digest = f"{seed:x}".rjust(64, "a")
    return metadata.build(
        manifest=manifest, license_value=manifest["license"], summary="Pink and cyan.",
        zip_bytes_size=4096 + seed, zip_sha256=digest, installed_size=8192 + seed,
        preview_size=1234 + seed, preview_sha256=digest[::-1], owner={
            "github_id": owner_id, "login": "alice"},
        submitter={"github_id": owner_id, "login": "alice"}, issue=7,
        source_sha256="b" * 64, source_size=5000)


class MetadataTest(unittest.TestCase):
    def test_record_is_valid_and_path_bound(self):
        rec = record()
        self.assertEqual(metadata.problems(rec, "themes/neon-nights/1.0.0.json"), [])
        self.assertTrue(metadata.problems(rec, "themes/neon-nights/1.0.1.json"))
        self.assertEqual(rec["artifact"]["url"],
                         "https://github.com/Utility-Muffin-Research-Kitchen/leaf-themes/"
                         "releases/download/neon-nights-v1.0.0/neon-nights-1.0.0.zip")
        broken = copy.deepcopy(rec)
        broken["artifact"]["url"] = "https://evil.example/neon-nights-1.0.0.zip"
        broken["owner"]["github_id"] = True
        self.assertEqual(len(metadata.problems(broken)), 2)

    def test_blank_description_is_left_out(self):
        self.assertNotIn("description", record(description="  "))
        self.assertNotIn("description", record(description=None))

    def test_owners_update_is_sorted_and_never_overwrites(self):
        owners = {"zeta": {"github_id": 1, "login": "z"}}
        updated = metadata.owners_with(owners, "alpha", {"github_id": 2, "login": "a"})
        self.assertEqual(list(updated), ["alpha", "zeta"])
        again = metadata.owners_with(updated, "alpha", {"github_id": 3, "login": "x"})
        self.assertEqual(again["alpha"]["github_id"], 2)


class CatalogTest(unittest.TestCase):
    def test_first_version(self):
        new, changed = catalog.add_version(BASE, record())
        self.assertTrue(changed)
        entry = new["themes"][0]
        self.assertEqual(list(entry), [
            "id", "name", "author", "owner_github_id", "summary", "description", "license",
            "preview", "version", "min_leaf_version", "install_name", "artifact", "versions",
            "withdrawn"])
        self.assertEqual(entry["install_name"], "neon-nights")
        self.assertEqual(entry["artifact"], entry["versions"][0]["artifact"])
        self.assertIs(entry["withdrawn"], False)
        self.assertNotIn("themes", BASE)   # the input is not modified

    def test_second_version_newest_first(self):
        first, _ = catalog.add_version(BASE, record())
        second, changed = catalog.add_version(first, record(version="1.1.0", seed=1,
                                                            description=None))
        self.assertTrue(changed)
        entry = second["themes"][0]
        self.assertEqual([v["version"] for v in entry["versions"]], ["1.1.0", "1.0.0"])
        self.assertEqual(entry["version"], "1.1.0")
        self.assertNotIn("description", entry)
        self.assertEqual(entry["versions"][1], first["themes"][0]["versions"][0])

    def test_rerun_is_a_no_op_and_conflicts_are_refused(self):
        first, _ = catalog.add_version(BASE, record())
        same, changed = catalog.add_version(first, record())
        self.assertFalse(changed)
        self.assertEqual(same, first)
        self.assertTrue(catalog.has_version(first, record()))
        with self.assertRaises(catalog.CatalogError):
            catalog.add_version(first, record(seed=5))           # same version, new bytes
        second, _ = catalog.add_version(first, record(version="2.0.0", seed=2))
        with self.assertRaises(catalog.CatalogError):
            catalog.add_version(second, record(version="1.5.0", seed=3))

    def test_withdrawn_is_preserved(self):
        first, _ = catalog.add_version(BASE, record())
        withdrawn, changed = catalog.withdraw(first, "neon-nights")
        self.assertTrue(changed)
        self.assertFalse(catalog.withdraw(withdrawn, "neon-nights")[1])
        updated, _ = catalog.add_version(withdrawn, record(version="1.0.1", seed=1))
        self.assertIs(updated["themes"][0]["withdrawn"], True)
        with self.assertRaises(catalog.CatalogError):
            catalog.withdraw(first, "missing")

    def test_sixteen_version_cap(self):
        current = copy.deepcopy(BASE)
        for i in range(16):
            current, _ = catalog.add_version(current, record(version=f"1.0.{i}", seed=i))
        self.assertEqual(len(current["themes"][0]["versions"]), policy.MAX_VERSIONS)
        with self.assertRaises(catalog.CatalogError):
            catalog.add_version(current, record(version="1.0.16", seed=16))

    def test_cross_lane_collision(self):
        with_app = copy.deepcopy(BASE)
        with_app["content"] = [{"id": "neon-nights"}]
        with self.assertRaises(catalog.CatalogError):
            catalog.add_version(with_app, record())

    def test_stamp_format(self):
        stamped = copy.deepcopy(BASE)
        catalog.stamp(stamped, datetime(2026, 9, 15, 4, 5, 6, 789, tzinfo=timezone.utc))
        self.assertEqual(stamped["catalog_revision"], "prod-20260915T040506Z")
        self.assertEqual(stamped["generated_at"], "2026-09-15T04:05:06Z")

    def test_dump_matches_committed_format(self):
        path = os.path.join(helpers.leaf_docs_dir(), "public", "pakrat", "v1",
                            "storefront.json")
        if not os.path.exists(path):
            self.skipTest("leaf-docs checkout not found")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(catalog.dumps(json.loads(text)), text)


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class LeafDocsValidatorTest(unittest.TestCase):
    """Generated catalogs must pass leaf-docs' own validator, history rules included."""

    @classmethod
    def setUpClass(cls):
        cls.script = os.path.join(helpers.leaf_docs_dir(), "scripts",
                                  "validate-pakrat-catalog.mjs")
        if not os.path.exists(cls.script):
            raise unittest.SkipTest("leaf-docs checkout not found")
        with open(os.path.join(helpers.leaf_docs_dir(), "public", "pakrat", "v1",
                               "storefront.json"), encoding="utf-8") as handle:
            cls.live = json.load(handle)

    def validate(self, current, previous):
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for name, obj in (("current", current), ("previous", previous)):
                path = os.path.join(temp, f"{name}.json")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(catalog.dumps(obj))
                paths.append(path)
            env = dict(os.environ)
            from contracts import contracts_dir
            env["LEAF_CONTRACTS_DIR"] = contracts_dir()
            done = subprocess.run(["node", self.script, "--catalog", paths[0],
                                   "--previous-catalog", paths[1]],
                                  capture_output=True, text=True, env=env, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_run_validator_with_a_relative_checkout(self):
        # publish.yml passes `.leaf-docs`, relative to the workspace. This goes
        # through leafdocs.run_validator itself, the way the workflow does.
        import leafdocs
        docs = helpers.leaf_docs_dir()
        parent, name = os.path.dirname(docs), os.path.basename(docs)
        first, _ = catalog.add_version(self.live, record())
        catalog.stamp(first)
        cwd = os.getcwd()
        try:
            os.chdir(parent)
            leafdocs.run_validator(name, self.live, first)
        finally:
            os.chdir(cwd)

    def test_publish_update_withdraw_update(self):
        live = self.live
        first, _ = catalog.add_version(live, record())
        catalog.stamp(first)
        self.validate(first, live)
        second, _ = catalog.add_version(first, record(version="1.1.0", seed=1))
        catalog.stamp(second)
        self.validate(second, first)
        withdrawn, _ = catalog.withdraw(second, "neon-nights")
        self.validate(withdrawn, second)
        third, _ = catalog.add_version(withdrawn, record(version="1.2.0", seed=2))
        self.validate(third, withdrawn)
        other, _ = catalog.add_version(third, record(theme_id="mono", owner_id=2002, seed=9))
        self.validate(other, third)

    def test_sixteen_versions_pass(self):
        current = self.live
        for i in range(16):
            current, _ = catalog.add_version(current, record(version=f"2.0.{i}", seed=i))
        self.validate(current, self.live)


if __name__ == "__main__":
    unittest.main()
