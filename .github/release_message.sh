#!/usr/bin/env bash
# Pick the previous release tag (v*), skipping non-release tags like
# archive/... so the shortlog only covers commits in this release.
previous_tag=$(git tag --sort=-creatordate --list 'v*' | sed -n 2p)
git shortlog "${previous_tag}.." | sed 's/^./    &/'
