#!/usr/bin/env bash
set -e
git filter-branch --force --index-filter "git ls-files -z | xargs -0 -r sed -i 's#https://yx\\.aningai\\.xyz/sub?token=\\.\\.\\.#https://example.invalid/sub?token=REPLACE_ME#g'" -- main
