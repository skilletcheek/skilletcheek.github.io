#!/usr/bin/env bash
# Push, rebasing onto whatever landed while this job was running.
#
# Two workflows push to main. fetch-events.yml runs at 09:00/17:00 UTC and
# social-post.yml at 18:30, so they do not normally collide -- but a manual
# "Run workflow" on either, or a delayed scheduled run (GitHub drops and
# defers these under load), puts them on top of each other. A rejected push
# here would strand a card that the Instagram call is about to ask GitHub
# Pages for, so it is worth retrying rather than failing.
set -euo pipefail
for attempt in 1 2 3 4 5; do
  if git push; then
    exit 0
  fi
  echo "push rejected (attempt ${attempt}); rebasing onto origin/main"
  git pull --rebase --autostash origin main
  sleep $(( attempt * 5 ))
done
echo "could not push after 5 attempts" >&2
exit 1
