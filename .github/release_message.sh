#!/usr/bin/env bash
# The release notes: this version's section of CHANGELOG.md, which is
# written for users. The version comes from the tag being released, or from
# the first argument when run by hand. Falls back to the commit shortlog
# since the previous release if CHANGELOG.md has no section for it.
version="${1:-${GITHUB_REF_NAME#v}}"
notes=$(awk -v heading="## [${version}]" '
  index($0, heading) == 1 { found = 1; next }
  found && /^## \[/ { exit }
  found { print }
' CHANGELOG.md)

if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
  # Skip non-release tags like archive/... so the shortlog only covers
  # commits in this release.
  previous_tag=$(git tag --sort=-creatordate --list 'v*' | sed -n 2p)
  git shortlog "${previous_tag}.." | sed 's/^./    &/'
  exit
fi

# CHANGELOG.md is hard-wrapped, and GitHub shows a newline in a release body
# as a line break, so a Markdown formatter joins each paragraph and list item
# into one line.
printf '%s\n' "$notes" | uvx --quiet mdformat==1.0.0 --wrap no -
