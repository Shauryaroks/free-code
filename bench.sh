#!/usr/bin/env bash
# Run a pipeline N times alternating parallel/serial; archive each run under .orch-runs/bench/.
# usage: ./bench.sh boltons.json 3        (3 of each mode)
set -u
PIPE=${1:-boltons.json}; N=${2:-3}
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.opencode/bin:$HOME/.antigravity/bin:$PATH"
REPO=$(python3 -c "import json;print(json.load(open('$PIPE'))['repo'])")
BASE=$(git -C "$REPO" rev-parse HEAD~0)          # caller sets the target to its base commit first
mkdir -p .orch-runs/bench
i=0
for n in $(seq 1 "$N"); do
  for mode in parallel serial; do
    i=$((i+1)); tag=$(printf "%02d-%s" "$i" "$mode")
    # reset target: base commit, no leftovers
    git -C "$REPO" reset -q --hard "$BASE"; git -C "$REPO" clean -qfd
    git -C "$REPO" worktree prune; rm -rf "$(dirname "$REPO")"/.orch-wt-*
    for b in $(git -C "$REPO" branch --list 'orch/*' | tr -d ' *'); do git -C "$REPO" branch -qD "$b"; done
    rm -f .orch-runs/*.diff .orch-runs/*.out .orch-runs/summary.json
    echo "=== run $tag $(date +%H:%M:%S)"
    flag=""; [ "$mode" = serial ] && flag="--serial"
    python3 orch.py "$PIPE" $flag > .orch-runs/run.log 2>&1; echo "EXIT=$?" >> .orch-runs/run.log
    d=.orch-runs/bench/$tag; mkdir -p "$d"
    mv .orch-runs/*.diff .orch-runs/*.out .orch-runs/summary.json .orch-runs/run.log "$d"/ 2>/dev/null
    cp .orch-state.json "$d"/state.json; cp .orch-stats.json "$d"/stats.json 2>/dev/null
    tail -1 "$d"/run.log
  done
done
echo "bench done $(date +%H:%M:%S)"
