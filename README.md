# Leaf Themes

Community themes for [Leaf](https://leaf.game), installable from Pak Rat on the device.

**Submissions are not open yet.** This repository is being set up. When it opens, you will
submit a theme by filling in an issue form and attaching a zip, with no git needed. Themes
are reviewed before anything is published to Pak Rat.

Until then, you can make a theme for your own device by following the
[themes guide](https://leaf.game/guide/themes/).

## How submitting will work

1. Build your theme and zip it: one folder, named after your theme's `id`, holding
   `theme.json`, `preview.png` and your art.
2. Open a new issue with the **Submit a theme** form. Attach the zip, pick a license, write a
   one-line summary, and check the three boxes.
3. A bot downloads your zip and checks it within a few minutes. It comments on your issue
   with the result.
   - If something needs fixing, the comment lists every problem at once. Fix them, edit the
     issue to attach the new zip in the **Theme zip** field, and a maintainer will comment
     `/recheck` to run the checks again.
   - If everything passes, the bot opens a review pull request and links it in a comment.
4. A maintainer reviews the theme. When they merge the pull request, your theme is published
   as a release in this repository and added to the Pak Rat catalog. The bot comments with
   the published version and closes your issue.

Nothing you submit is public in Pak Rat until a maintainer merges it. The zip that gets
published is rebuilt by the bot from the files that passed the checks, so every device gets
exactly what was reviewed.

## Rules

Your theme has to pass THEME-1, the Leaf theme package format. The
[themes guide](https://leaf.game/guide/themes/) covers how to make one; the full rules,
with every limit, are in the
[THEME-1 contract](https://github.com/Utility-Muffin-Research-Kitchen/leaf-contracts/blob/main/docs/themes.md).
The ones people hit most:

- The zip holds exactly one folder, and its name is your theme's `id`.
- `theme.json` has `schema`, `id`, `name`, `author`, `version`, `min_leaf_version` (at least
  `0.12.0`) and `license`. Nothing else is allowed in it besides `description`, `grid`,
  `colors` and `status_style`.
- `preview.png` is exactly 960 x 720. Icons, labels and wordmarks are PNGs up to 1024 px per
  side (icons look best at 512 x 512), and wallpapers are PNG or JPEG up to 2048 px per side.
- Only the files the guide lists. No hidden files such as `.DS_Store` or `__MACOSX`, no
  symbolic links, and nothing that runs. On a Mac, zip from the command line with
  `zip -r -X` to leave the hidden files out.
- The zip is at most 10 MiB, and at most 25 MiB once extracted.
- Your `id` is 2 to 40 lowercase letters, digits and hyphens. It can't be the name of a theme
  that ships with Leaf (`Sample`) or of anything else in Pak Rat, and it never changes.
- You made every image, or you have the right to share it under the license you pick.
  Console and system logos are allowed, the same way Leaf's bundled wordmarks use them.

The GitHub account that first publishes an `id` owns it. Only that account, or a maintainer,
can publish new versions of it.

## Licenses

Pick one when you submit. The form choice and `license` in `theme.json` must match.

| Form choice | `theme.json` value | What people can do |
| --- | --- | --- |
| CC BY 4.0 | `CC-BY-4.0` | Share and adapt, with credit to you |
| CC BY-SA 4.0 | `CC-BY-SA-4.0` | Share and adapt, with credit, under the same license |
| CC0 | `CC0-1.0` | Anything, no credit needed |
| All rights reserved, redistribution permitted | `redistribution-permitted` | Download and use it through Pak Rat; you keep every other right |

You can include the full license text as `LICENSE.txt` in your theme folder.

## Updates

To publish a new version, open a new submission from the same GitHub account with a higher
`version` in `theme.json` (for example `1.0.0` to `1.1.0`). Keep the same `id`. Earlier
versions stay published, and Pak Rat offers the update to people who installed your theme.

A theme can have up to 16 published versions.

## Takedowns

If a theme uses your work without permission, or you want your own theme removed, tell a
maintainer on the [Leaf Discord](https://discord.gg/q5F7cZ7KRp) or
[open an issue in the Leaf repository](https://github.com/Utility-Muffin-Research-Kitchen/Leaf/issues).
The theme is withdrawn first and discussed after.

A withdrawn theme disappears from Pak Rat and can't be installed. Copies already on a
device keep working.

## For maintainers

Checks and staging run in `.github/workflows/submission.yml`, publishing in `publish.yml`,
and withdrawals in `takedown.yml`. The logic lives in `tools/` (standard-library Python plus
the THEME-1 reference validator from `leaf-contracts`), with offline tests in `tests/`:

```sh
python3 -m unittest discover -s tests
```

The tests expect `leaf-contracts` (and, for the catalog validator tests, `leaf-docs` and
`node`) checked out next to this repository. Set `LEAF_CONTRACTS_DIR` or `LEAF_DOCS_DIR` to
point somewhere else.

- **Recheck a submission:** comment `/recheck` on the issue after the submitter attaches a
  fixed zip. Only maintainers can.
- **Dry run:** run the **Theme submission** workflow by hand with an issue number. It runs
  every check and writes the result to the run summary, but comments, labels and stages
  nothing. Set `as_github_id` (and `as_login`) to check ownership as another account.
- **Publish again:** if a publish run stopped partway, re-run it, or run **Publish theme** by
  hand with the `themes/<id>/<version>.json` path. Finished steps are skipped.
- **Withdraw:** run **Withdraw theme** with the id and a reason.
- **Transfer ownership:** edit `owners.json` in a reviewed pull request.

Before the first run, the repository needs:

- Labels `theme-submission`, `needs-changes` and `ready-for-review`.
- Variable `LEAF_THEMES_BOT_APP_ID` and secret `LEAF_THEMES_BOT_PRIVATE_KEY`, for the Leaf
  Themes Bot app installed on this repository only (pull requests read and write, contents
  read). It opens review pull requests, so workflow tokens never need to.
- Variable `LEAF_DOCS_APP_ID` and secret `LEAF_DOCS_APP_PRIVATE_KEY`, for the Leaf Themes
  Publisher app installed on `leaf-docs` only (contents and pull requests read and write,
  checks and statuses read). Only the publish and withdraw workflows use it.
- Secret `DISCORD_THEMES_WEBHOOK`, optional: new and updated themes are announced there.
- Variable `SUBMISSIONS_OPEN` set to `true` when submissions open. Until then, only issues
  opened by maintainers are processed.
