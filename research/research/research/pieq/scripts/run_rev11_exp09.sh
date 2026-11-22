#!/bin/bash
# EXP-09 — repeated paired agent-count comparison (preregistered: 5 repetitions,
# randomized condition order per repetition, heldout36, hardened gate unchanged).
set -u
cd "$(dirname "$0")/.."
PY=./.venv-rev9/bin/python
mkdir -p results/rev11/exp09
for rep in 1 2 3 4 5; do
  # deterministic per-rep shuffle (seeded by rep for reproducibility of the ORDER only)
  order=$($PY -c "import random; cs=['S1','H1','E1']; random.Random($rep).shuffle(cs); print(' '.join(cs))")
  echo "rep $rep order: $order"
  for cond in $order; do
    tr="results/rev11/exp09/rep${rep}_${cond}.jsonl"
    if [ -f "$tr" ] && [ "$(grep -c case_evaluation "$tr")" -ge 36 ]; then
      echo "skip complete $tr"; continue
    fi
    rm -f "$tr"  # remove partial trace from an interrupted attempt
    $PY scripts/run_rev10_hardened_live_eval.py --condition "$cond" \
      --trace "results/rev11/exp09/rep${rep}_${cond}.jsonl" \
      > "/tmp/exp09_rep${rep}_${cond}.log" 2>&1 \
      || echo "rep $rep $cond FAILED" >> /tmp/exp09_failures.log
  done
done
echo EXP09_ALL_DONE
