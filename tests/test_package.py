import io
import os
import tempfile
import unittest
import zipfile

import helpers
import package
from contracts import theme_model


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def path(self, name, data):
        return helpers.write(os.path.join(self.temp.name, name), data)

    def test_valid_theme_passes_with_warning(self):
        source = self.path("a.zip", helpers.theme_zip(helpers.theme_files()))
        inspection = package.inspect(source)
        self.assertEqual(inspection.reasons, [])
        self.assertEqual(inspection.warnings, ["theme-icon-off-size"])
        self.assertEqual(inspection.manifest["id"], "neon-nights")

    def test_reasons_come_from_the_reference_validator(self):
        files = helpers.theme_files()
        files["neon-nights/preview.png"] = helpers.png(800, 600)
        files["neon-nights/.DS_Store"] = b"junk"
        inspection = package.inspect(self.path("bad.zip", helpers.theme_zip(files)))
        self.assertEqual(inspection.reasons, ["theme-hidden-file", "theme-image-dimensions"])
        # A hidden file makes theme.json untrustworthy for the id checks.
        self.assertIsNone(inspection.manifest)

        files = helpers.theme_files()
        files["neon-nights/preview.png"] = helpers.png(800, 600)
        inspection = package.inspect(self.path("dims.zip", helpers.theme_zip(files)))
        self.assertEqual(inspection.reasons, ["theme-image-dimensions"])
        self.assertEqual(inspection.manifest["version"], "1.0.0")

    def test_every_theme_reason_has_an_explanation(self):
        import reasons
        for slug in theme_model().REASONS:
            self.assertIn(slug, reasons.THEME_REASONS)
        for slug in theme_model().WARNINGS:
            self.assertIn(slug, reasons.THEME_WARNINGS)
        for text in list(reasons.THEME_REASONS.values()) + \
                list(reasons.SUBMISSION_REASONS.values()):
            self.assertNotIn("\u2014", text)
            self.assertNotIn("|", text)

    def test_rebuild_is_deterministic_and_canonical(self):
        files = helpers.theme_files()
        first = self.path("first.zip", helpers.theme_zip(files))
        # Same files, different order, timestamps, directory entries, method.
        second = self.path("second.zip", helpers.theme_zip(
            files, order=sorted(files, reverse=True), dirs=False,
            date=(2001, 2, 3, 4, 5, 6), compression=zipfile.ZIP_STORED))
        a, b = package.rebuild(first), package.rebuild(second)
        self.assertEqual(a, b)
        self.assertEqual(a, package.rebuild(first))

        with zipfile.ZipFile(io.BytesIO(a)) as bundle:
            infos = bundle.infolist()
            self.assertEqual([i.filename for i in infos], sorted(files))
            for info in infos:
                self.assertEqual(info.date_time, package.FIXED_DATE_TIME)
                self.assertEqual(info.external_attr >> 16, 0o100644)
                self.assertEqual(info.create_system, 3)
                self.assertIn(info.compress_type, (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED))
                self.assertEqual(bundle.read(info), files[info.filename])

        rebuilt = self.path("rebuilt.zip", a)
        inspection = package.inspect(rebuilt)
        self.assertEqual(inspection.reasons, [])
        self.assertEqual(package.installed_size(a), sum(len(v) for v in files.values()))

    def test_rebuild_keeps_every_entry_under_the_ratio_limit(self):
        files = helpers.theme_files()
        # Whitespace compresses far past 100:1 under deflate level 9.
        files["neon-nights/theme.json"] = files["neon-nights/theme.json"] + b" " * 60000
        source = self.path("ratio.zip", helpers.theme_zip(files, compression=zipfile.ZIP_STORED))
        self.assertEqual(package.inspect(source).reasons, [])
        rebuilt = self.path("ratio-rebuilt.zip", package.rebuild(source))
        self.assertEqual(package.inspect(rebuilt).reasons, [])

    def test_contract_fixtures_agree(self):
        base = os.path.join(os.path.dirname(theme_model().__file__), "..", "fixtures")
        for name in sorted(os.listdir(os.path.join(base, "valid"))):
            source = os.path.join(base, "valid", name)
            self.assertEqual(package.inspect(source).reasons, [], name)
            rebuilt = self.path(f"fixture-{name}", package.rebuild(source))
            self.assertEqual(package.inspect(rebuilt).reasons, [], name)


if __name__ == "__main__":
    unittest.main()
