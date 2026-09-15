"""Static checks on the workflows and the issue form."""
import glob
import os
import re
import shutil
import subprocess
import unittest

import helpers
import form

WORKFLOWS = sorted(glob.glob(os.path.join(helpers.REPO_ROOT, ".github", "workflows", "*.yml")))
TEMPLATES = sorted(glob.glob(os.path.join(helpers.REPO_ROOT, ".github", "ISSUE_TEMPLATE",
                                          "*.yml")))


def run_blocks(text: str):
    """(line number, script text) for every run: step."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
        if match:
            indent = len(match.group(1))
            start, body = i + 1, [match.group(2)]
            if match.group(2).strip() in ("|", ">", "|-", ">-"):
                i += 1
                while i < len(lines) and (not lines[i].strip() or
                                          len(lines[i]) - len(lines[i].lstrip()) > indent):
                    body.append(lines[i])
                    i += 1
                yield start, "\n".join(body)
                continue
            yield start, "\n".join(body)
        i += 1


class WorkflowTest(unittest.TestCase):
    def test_found(self):
        names = [os.path.basename(p) for p in WORKFLOWS]
        self.assertEqual(names, ["publish.yml", "submission.yml", "takedown.yml", "tests.yml"])

    def test_no_expressions_in_scripts(self):
        for path in WORKFLOWS:
            with open(path) as handle:
                text = handle.read()
            blocks = list(run_blocks(text))
            self.assertTrue(blocks, path)
            for line, script in blocks:
                self.assertNotIn("${{", script, f"{os.path.basename(path)}:{line}")

    def test_actions_pinned_to_full_sha(self):
        for path in WORKFLOWS:
            with open(path) as handle:
                for number, line in enumerate(handle, start=1):
                    if re.match(r"^\s*(- )?uses:", line):
                        self.assertRegex(line, r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d",
                                         f"{os.path.basename(path)}:{number}")

    def test_permissions_default_to_none_and_every_job_declares_its_own(self):
        for path in WORKFLOWS:
            with open(path) as handle:
                text = handle.read()
            self.assertRegex(text, r"(?m)^permissions: \{\}$", path)
            jobs = re.findall(r"(?m)^  ([a-z-]+):\n", text.split("\njobs:\n", 1)[1])
            for job in jobs:
                section = text.split(f"\n  {job}:\n", 1)[1]
                section = re.split(r"\n  [a-z-]+:\n", section, maxsplit=1)[0]
                self.assertIn("    permissions:", section, f"{path} job {job}")

    def test_contracts_pin_matches_leaf_docs(self):
        pins = set()
        for path in WORKFLOWS:
            with open(path) as handle:
                pins.update(re.findall(r"LEAF_CONTRACTS_SHA: ([0-9a-f]{40})", handle.read()))
        self.assertEqual(pins, {"a0a2a06e2eb6e80d02daec7a02aaba58d3687ce6"})
        docs = os.path.join(helpers.leaf_docs_dir(), ".github", "workflows",
                            "pakrat-catalog.yml")
        if os.path.exists(docs):
            with open(docs) as handle:
                self.assertIn(pins.pop(), handle.read())

    @unittest.skipIf(shutil.which("ruby") is None, "ruby is not installed")
    def test_yaml_parses(self):
        for path in WORKFLOWS + TEMPLATES:
            done = subprocess.run(["ruby", "-ryaml", "-e", "YAML.safe_load(File.read(ARGV[0]))",
                                   path], capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, f"{path}: {done.stderr}")


class IssueFormTest(unittest.TestCase):
    def test_labels_match_the_parser(self):
        with open(os.path.join(helpers.REPO_ROOT, ".github", "ISSUE_TEMPLATE",
                               "submit-theme.yml")) as handle:
            text = handle.read()
        labels = re.findall(r"(?m)^\s+(?:- )?label: (.+)$", text)
        self.assertEqual(labels[:4], list(form.KNOWN_LABELS))
        self.assertEqual(labels[4:], list(form.CONFIRMATIONS))
        options = re.findall(r"(?m)^\s+- (CC.*|All rights.*)$", text)
        self.assertEqual(options, list(form.LICENSE_CHOICES))
        self.assertIn('labels: ["theme-submission"]', text)

    def test_user_facing_prose_style(self):
        paths = [os.path.join(helpers.REPO_ROOT, "README.md"),
                 os.path.join(helpers.REPO_ROOT, "themes", "README.md")] + TEMPLATES
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for bad in ("\u2014", "\u2013", "\u2026", "\u2018", "\u2019", "\u201c", "\u201d"):
                self.assertNotIn(bad, text, path)


if __name__ == "__main__":
    unittest.main()
