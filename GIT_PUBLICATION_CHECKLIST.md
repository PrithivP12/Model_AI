# Publication checklist

1. Review the release report and choose a project code license; review linked
   catalog/source attribution and distribution rights. This is an explicit
   outstanding public-publication decision, not a newly invented license.
2. Build the allowlisted handoff with `python scripts/build_release_handoff.py
   --output /new/private/handoff`. Review its file/hash/security manifest.
3. Do not run `git add .` in the research workspace. It contains large private
   outputs and many pre-existing edits. `.gitignore` does not untrack existing
   files or scrub old commits. The handoff excludes `.git` history entirely.
4. Run an independent secret/history review before pushing any old history.
   The release scan flags filenames only, never prints credential values. A
   clean handoff scan cannot establish that old repository history is safe.
5. Keep credentials, `.env`, user runs, logs, local caches, research binaries,
   virtualenvs and private paths out of the public repository. Review all
   changes and manifests; do not stage unrelated user edits.
6. Install/test from the handoff with only its documented files. Recheck
   dependencies and OpenAPI/client contracts on the target Python/platform.
7. Configure the actual remote yourself, inspect the diff, commit deliberately
   and push only after the owner approves publication. This mission performs
   none of those actions and does not rewrite history.

The optional handoff builder is a selection/scan tool, not a legal or exhaustive
secret guarantee. It rejects detected keys/large files, reports local-path
references for review and preserves original archives in place. Do not bypass a
finding by deleting scientific evidence or editing private credential values.
