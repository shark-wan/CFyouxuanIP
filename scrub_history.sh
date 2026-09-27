#!/usr/bin/env bash
set -e
git filter-branch --force --tree-filter 'for f in $(git ls-files); do if [ -f "$f" ]; then sed -i "s#https://yx\\.aningai\\.xyz/sub?token=\\.\\.\\.#https://example.invalid/sub?token=REPLACE_ME#g" "$f"; fi; done' -- main
