#!/bin/sh
set -eu
i=0
while [ "$i" -lt 2600 ]; do
  printf 'diagnostic sequence=%04d account_id=decoy-%04d region=unknown retention_days=0 payload=abcdefghijklmnopqrstuvwxyz0123456789\n' "$i" "$i"
  if [ "$i" -eq 120 ]; then
    printf 'AUTHORITATIVE account_id=acct-7f31c9\n'
  fi
  if [ "$i" -eq 1330 ]; then
    printf 'AUTHORITATIVE region=ap-southeast-3\n'
  fi
  if [ "$i" -eq 2510 ]; then
    printf 'AUTHORITATIVE retention_days=47\n'
  fi
  i=$((i + 1))
done
