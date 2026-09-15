import json
import os
import tempfile
import unittest

import helpers
import policy

ALICE = {"github_id": 1001, "login": "alice"}
BOB = {"github_id": 2002, "login": "bob"}
CATALOG = {
    "schema": 1, "product": "pak-rat", "apps": [{"id": "org.umrk.itchio"}, {"id": "clock"}],
    "content": [{"id": "org.umrk.scummvm"}],
    "themes": [{"id": "hand-added", "owner_github_id": 3003,
                "versions": [{"version": "1.0.0"}, {"version": "0.9.0"}]}],
}


class OwnershipTest(unittest.TestCase):
    def test_new_id_is_claimed_by_submitter(self):
        decision = policy.decide_ownership("neon", ALICE, False, {}, CATALOG)
        self.assertTrue(decision.ok)
        self.assertTrue(decision.new_id)
        self.assertEqual(decision.owner, ALICE)

    def test_owner_can_update(self):
        owners = {"neon": {"github_id": 1001, "login": "old-login"}}
        renamed = {"github_id": 1001, "login": "alice-renamed"}
        decision = policy.decide_ownership("neon", renamed, False, owners, CATALOG)
        self.assertTrue(decision.ok)
        self.assertFalse(decision.new_id)
        self.assertEqual(decision.owner["github_id"], 1001)

    def test_non_owner_rejected_even_with_same_login(self):
        owners = {"neon": ALICE}
        impostor = {"github_id": 9999, "login": "alice"}
        self.assertFalse(policy.decide_ownership("neon", impostor, False, owners, CATALOG).ok)
        self.assertFalse(policy.decide_ownership("neon", BOB, False, owners, CATALOG).ok)

    def test_maintainer_override_keeps_the_owner(self):
        owners = {"neon": ALICE}
        decision = policy.decide_ownership("neon", BOB, True, owners, CATALOG)
        self.assertTrue(decision.ok)
        self.assertTrue(decision.maintainer_override)
        self.assertEqual(decision.owner, ALICE)

    def test_catalog_owner_counts_when_owners_json_lacks_the_id(self):
        self.assertFalse(policy.decide_ownership("hand-added", ALICE, False, {}, CATALOG).ok)
        owner = {"github_id": 3003, "login": "carol"}
        self.assertTrue(policy.decide_ownership("hand-added", owner, False, {}, CATALOG).ok)

    def test_maintainer_permission(self):
        for payload in ({"permission": "admin", "role_name": "admin"},
                        {"permission": "write", "role_name": "maintain"},
                        {"permission": "write", "role_name": "write"},
                        {"permission": "write", "role_name": "custom-writer"}):
            self.assertTrue(policy.is_maintainer_permission(payload), payload)
        for payload in ({"permission": "read", "role_name": "triage"},
                        {"permission": "read", "role_name": "read"},
                        {"permission": "none"}, {}, None, "admin"):
            self.assertFalse(policy.is_maintainer_permission(payload), payload)


class CatalogRulesTest(unittest.TestCase):
    def test_cross_lane_collision(self):
        self.assertEqual(policy.id_collision(CATALOG, "clock"), "apps")
        self.assertEqual(policy.id_collision(CATALOG, "CLOCK"), "apps")
        self.assertIsNone(policy.id_collision(CATALOG, "hand-added"))
        self.assertIsNone(policy.id_collision(CATALOG, "neon"))
        self.assertIsNone(policy.id_collision({"apps": "junk"}, "neon"))


class VersionTest(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(policy.is_newer("1.0.0", []))
        self.assertTrue(policy.is_newer("1.0.10", ["1.0.9", "0.12.0"]))
        self.assertTrue(policy.is_newer("10.0.0", ["9.9999.9999"]))
        self.assertFalse(policy.is_newer("1.0.0", ["1.0.0"]))
        self.assertFalse(policy.is_newer("1.0.0", ["1.0.1"]))
        self.assertFalse(policy.is_newer("1.0", []))
        self.assertFalse(policy.is_newer("01.0.0", []))

    def test_published_versions_merge_repo_and_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            for version in ("1.1.0", "1.0.0"):
                helpers.write(os.path.join(temp, "hand-added", f"{version}.json"), b"{}")
            helpers.write(os.path.join(temp, "hand-added", "README.md"), b"")
            helpers.write(os.path.join(temp, "hand-added", "latest.json"), b"{}")
            versions = policy.published_versions(temp, "hand-added", CATALOG)
            self.assertEqual(versions, ["0.9.0", "1.0.0", "1.1.0"])
            self.assertFalse(policy.is_newer("1.0.5", versions))
            self.assertTrue(policy.is_newer("1.2.0", versions))

    def test_limit(self):
        self.assertFalse(policy.at_version_limit([f"1.0.{i}" for i in range(15)]))
        self.assertTrue(policy.at_version_limit([f"1.0.{i}" for i in range(16)]))


class LicenseTest(unittest.TestCase):
    def test_form_choices_match_the_catalog_and_contract(self):
        import form
        from contracts import theme_model
        self.assertEqual(sorted(form.LICENSE_CHOICES.values()),
                         sorted(theme_model().LICENSES))
        validator = os.path.join(helpers.leaf_docs_dir(), "scripts",
                                 "validate-pakrat-catalog.mjs")
        if not os.path.exists(validator):
            self.skipTest("leaf-docs checkout not found")
        with open(validator, encoding="utf-8") as handle:
            text = handle.read()
        line = next(l for l in text.splitlines() if l.startswith("const THEME_LICENSES"))
        listed = json.loads(line.split("=", 1)[1].strip().rstrip(";").replace("'", '"'))
        self.assertEqual(sorted(listed), sorted(form.LICENSE_CHOICES.values()))


if __name__ == "__main__":
    unittest.main()
