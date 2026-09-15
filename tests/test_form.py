import unittest

import helpers  # noqa: F401  (sets the import path)
import form


class AttachmentUrlTest(unittest.TestCase):
    def test_accepts_github_file_attachment(self):
        for url in (
            "https://github.com/user-attachments/files/31649486/theme.zip",
            "https://github.com/user-attachments/files/1/My-Theme_v1.2.ZIP",
            "https://github.com/user-attachments/files/16038712/caf%C3%A9.zip",
        ):
            self.assertTrue(form.is_attachment_url(url), url)

    def test_refuses_everything_else(self):
        for url in (
            "http://github.com/user-attachments/files/1/theme.zip",
            "https://github.com.evil.example/user-attachments/files/1/theme.zip",
            "https://github.com@evil.example/user-attachments/files/1/theme.zip",
            "https://user@github.com/user-attachments/files/1/theme.zip",
            "https://github.com:443/user-attachments/files/1/theme.zip",
            "https://GITHUB.COM/user-attachments/files/1/theme.zip",
            "https://evil.example/user-attachments/files/1/theme.zip",
            "https://github.com/user-attachments/assets/0f7c1e9e-1111/theme.zip",
            "https://github.com/someone/repo/files/1/theme.zip",
            "https://github.com/someone/repo/releases/download/v1/theme.zip",
            "https://objects.githubusercontent.com/github-production-repository-file-5c1aeb/1/2",
            "https://raw.githubusercontent.com/a/b/main/theme.zip",
            "https://github.com/user-attachments/files/1/theme.zip?x=1",
            "https://github.com/user-attachments/files/1/theme.zip#frag",
            "https://github.com/user-attachments/files/abc/theme.zip",
            "https://github.com/user-attachments/files/1/theme.tar.gz",
            "https://github.com/user-attachments/files/1/../../evil/theme.zip",
            "https://github.com/user-attachments/files/1/%2e%2e%2ftheme.zip",
            "https://github.com/user-attachments/files/1/a%2Fb.zip",
            "https://github.com/user-attachments/files/1/a%00.zip",
            "https://github.com/user-attachments/files/1/a\\b.zip",
            "https://github.com/user-attachments/files/1/" + "a" * 600 + ".zip",
            "javascript:alert(1)",
            "",
            None,
        ):
            self.assertFalse(form.is_attachment_url(url), url)


class ParseTest(unittest.TestCase):
    def test_valid_body(self):
        parsed = form.parse(helpers.form_body())
        self.assertEqual(parsed.reasons, [])
        self.assertEqual(parsed.attachment_url,
                         "https://github.com/user-attachments/files/123456/neon-nights.zip")
        self.assertEqual(parsed.license, "CC-BY-4.0")
        self.assertEqual(parsed.summary, "Pink and cyan on black.")

    def test_crlf_and_every_license(self):
        for label, value in form.LICENSE_CHOICES.items():
            body = helpers.form_body(license_label=label).replace("\n", "\r\n")
            parsed = form.parse(body)
            self.assertEqual(parsed.reasons, [], label)
            self.assertEqual(parsed.license, value)

    def test_license_not_on_list(self):
        for label in ("MIT", "CC-BY-4.0", "cc by 4.0", "_No response_", "CC BY 4.0 "):
            parsed = form.parse(helpers.form_body(license_label=label))
            if label == "CC BY 4.0 ":
                # Trailing spaces are trimmed like any form value.
                self.assertEqual(parsed.license, "CC-BY-4.0")
                continue
            self.assertIn("submission-license-missing", parsed.reasons, label)
            self.assertIsNone(parsed.license)

    def test_missing_and_foreign_attachment(self):
        self.assertIn("submission-missing-attachment",
                      form.parse(helpers.form_body(url=None)).reasons)
        self.assertIn("submission-attachment-url",
                      form.parse(helpers.form_body(url="https://evil.example/t.zip")).reasons)
        body = helpers.form_body().replace(
            "(https://github.com/user-attachments/files/123456/neon-nights.zip)",
            "(https://github.com/user-attachments/files/1/a.zip) "
            "[b](https://github.com/user-attachments/files/2/b.zip)")
        self.assertIn("submission-multiple-attachments", form.parse(body).reasons)

    def test_attachment_only_read_from_its_own_section(self):
        body = helpers.form_body(url=None).replace(
            "Pink and cyan on black.",
            "https://github.com/user-attachments/files/9/sneaky.zip")
        parsed = form.parse(body)
        self.assertIsNone(parsed.attachment_url)
        self.assertIn("submission-missing-attachment", parsed.reasons)

    def test_unchecked_box(self):
        parsed = form.parse(helpers.form_body(checked=(True, False, True)))
        self.assertIn("submission-confirmations", parsed.reasons)

    def test_confirmation_text_must_match(self):
        body = helpers.form_body().replace("I read the theme rules", "I skimmed the theme rules")
        self.assertIn("submission-confirmations", form.parse(body).reasons)

    def test_duplicate_heading_is_malformed(self):
        body = helpers.form_body() + "\n### License\n\nCC0\n"
        parsed = form.parse(body)
        self.assertIn("submission-form-malformed", parsed.reasons)

    def test_missing_section_is_malformed(self):
        body = helpers.form_body().replace("### Summary", "### Something else")
        parsed = form.parse(body)
        self.assertIn("submission-form-malformed", parsed.reasons)
        self.assertIn("submission-summary-invalid", parsed.reasons)

    def test_malicious_summaries(self):
        for summary in (
            "",
            "_No response_",
            "   ",
            "x" * 101,
            "Nice\u202etheme",          # right-to-left override
            "tab\there",
            "bell\x07",
            "private \ue000 use",
        ):
            parsed = form.parse(helpers.form_body(summary=summary))
            self.assertIn("submission-summary-invalid", parsed.reasons, repr(summary))
            self.assertIsNone(parsed.summary)

    def test_hostile_but_printable_summary_is_kept_verbatim(self):
        # Shell and workflow syntax is only text to the parser; it never
        # reaches a shell (see test_workflows).
        summary = "${{ secrets.TOKEN }} $(rm -rf /) `x` <img src=x> @someone"
        parsed = form.parse(helpers.form_body(summary=summary))
        self.assertEqual(parsed.reasons, [])
        self.assertEqual(parsed.summary, summary)

    def test_oversized_or_non_text_body(self):
        self.assertEqual(form.parse("x" * 70000).reasons, ["submission-form-malformed"])
        self.assertEqual(form.parse(None).reasons, ["submission-form-malformed"])
        self.assertIn("submission-form-malformed", form.parse("").reasons)


if __name__ == "__main__":
    unittest.main()
