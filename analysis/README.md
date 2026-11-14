# Measurement analysis pipeline

This directory turns the raw CSVs into the Demo 2 tables and figures:

- `analysis/validate.py` — checks schemas, formulas, duplicate runs, missing
  flows, reused run IDs, and the final five-experiment layout.
- `analysis/loader.py` — reads `experiments/*/summary.csv`, `results/fairness.csv`,
  `results/manifest.csv`, `results/per_second.csv`, `events.csv`, and the
  controller policy log.
- `analysis/aggregate.py` — takes the **median of the three repeats** for each
  flow and builds the Jain fairness table.
- `analysis/plots.py` — comparison bar charts and the dynamic surge timeline.
- `analysis/analyze.py` — runs the whole pipeline in one command.

The pipeline uses only the Python standard library plus `matplotlib` (already
installed on the project machine). It does **not** use pandas.

## Quick start

From the repository root:

```bash
# 1. Validate what is currently on disk (warnings allowed).
python3 analysis/validate.py

# 2. Run tables + figures from the committed summaries.
python3 -m analysis.analyze

# 3. Exercise the figures with the existing practice data.
#    These outputs are explicitly marked "not final results".
python3 -m analysis.analyze --practice

# 4. Pre-submission gate: five experiments x three consecutive runs.
python3 -m analysis.analyze --final --strict

# Module tests (no Mininet, no network traffic).
python3 -m unittest discover -s analysis/tests -v
```

Useful options:

```text
--results DIR       raw CSV directory (default: results/)
--experiments DIR   summary directory (default: experiments/)
--output DIR        output directory (default: analysis/output/)
--policy-log FILE   explicit dynamic-controller policy CSV
--practice          also plot non-canonical practice folders
--skip-plots        write only CSV/Markdown tables
--strict            treat validation warnings as errors
--final             require the five final experiments and 3 consecutive runs
--force             keep generating outputs even if strict validation fails
```

Generated files are ignored by git and go under `analysis/output/`:

```text
analysis/output/
├── validation_report.txt
├── results_tables.md
├── practice_tables.md          # only with --practice
├── pipeline_summary.md
├── tables/
│   ├── per_flow_medians.csv
│   ├── fairness.csv
│   └── fairness_by_run.csv
└── figures/
    ├── comparison_goodput.png
    ├── comparison_loss.png
    ├── comparison_p95_latency.png
    ├── comparison_avg_latency.png
    ├── surge_goodput.png
    ├── surge_loss.png
    ├── surge_p95_latency.png
    ├── timeline_dynamic_<run_id>.png
    └── timeline_dynamic_median.png
```

## Experiment identity

The reportable combinations are:

| Report name | Summary folder | Scenario |
|---|---|---|
| `baseline` | `experiments/baseline/` | `no_congestion` |
| `baseline_ryu` | `experiments/baseline_ryu/` | `congestion` |
| `static` | `experiments/static/` | `congestion` |
| `dynamic` | `experiments/dynamic/` | `surge` |
| `static_surge` | `experiments/static/` | `surge` |

`run_all.py` uses the label `static` for both the static congested run and the
extra static-policy surge run. The analysis distinguishes them by scenario.
Their run IDs must therefore be different; `--final --strict` rejects a reused
ID.

Practice folders such as `experiments/test/` and
`experiments/receiver_test/` stay visible as `<folder>/<scenario>` and are never
mixed into the four main comparison groups. `--practice` draws them for
pipeline testing only.

## Metric definitions

`results/metrics.csv` remains the authoritative per-run table. The analysis
takes the median across runs for each flow:

- **Goodput** = payload bytes × 8 / duration / 10^6 (Mbps, headers excluded).
- **Packet loss** = max(0, expected − unique received) / expected × 100.
- **Average latency** = mean of `recv_ts_ns − send_ts_ns`.
- **p95 latency** = nearest-rank 95th percentile.
- **Jain fairness** = (Σx)² / (n × Σx²), computed over the flows that ran in
  that scenario.

`results/per_second.csv` is a timeline view produced by the receiver. It has
one row per flow per second:

```text
experiment_id,run_id,flow,second,bucket_seconds,expected_packets,
unique_received,lost_packets,packet_loss_pct,payload_bytes,
goodput_mbps,avg_latency_ms,p95_latency_ms
```

`second=0` is the second in which that flow's first data packet arrived.
`bucket_seconds` is normally 1.0 and is smaller for the final partial second;
it is used so goodput is not inflated in the last bucket. The sender does not
publish a per-second schedule, so `expected_packets` in this file is an
even-spread **estimate** over the measured duration. Use `metrics.csv` for
exact run totals and `per_second.csv` for the shape of the timeline.

## Dynamic-surge integration contract

The receiver and analysis side are complete. The remaining integration points
belong to the dynamic-controller/runner owner:

1. **Policy log.** Write one CSV per dynamic run, preferably
   `experiments/dynamic/policy_<run_id>.csv` (or
   `results/surge_<run_id>/policy.csv`). Required columns:

   ```text
   ts,flow_id,old_queue,new_queue,reason
   ```

   `ts` must be `time.monotonic_ns()` — the same clock as `events.csv` and the
   packet timestamps. `flow_id` may be `1..4` or `high/medium/low/background`.
   A shared `results/policy.csv` is also accepted if it has a `run_id` column;
   without `run_id`, separate per-run files are safer.

   Example:

   ```csv
   ts,flow_id,old_queue,new_queue,reason
   1234567890000000,high,0,1,overload detected: total rate above 90% and drops rising
   ```

2. **Timeline alignment.** `events.csv` currently logs only delayed flow
   starts. The analysis recovers the run start from the background line
   `joined 10s into the run`. A future `run_start` event is used directly if
   added. Policy timestamps use the same recovered start.

3. **Final data collection.** `run_all.py` currently has no `--run-id` option
   and does not include `static_surge` by default. Until the runner owner adds
   those options, use explicit IDs with `run_experiment.py`:

   ```bash
   # Baseline: no controller, TCLink cap.
   sudo python3 mininet/run_experiment.py no_congestion \
       --mode none --cap tclink --label baseline --run-id 2101 --repeat 3

   # baseline_ryu needs Ryu running externally with baseline_controller.py.
   sudo python3 mininet/run_experiment.py congestion \
       --mode remote --cap tclink --label baseline_ryu --run-id 2104 --repeat 3

   # Static QoS on the congested scenario.
   sudo python3 mininet/run_experiment.py congestion \
       --mode remote --cap none --queues --label static --run-id 2107 --repeat 3

   # Static QoS on the surge (reference for dynamic).
   sudo python3 mininet/run_experiment.py surge \
       --mode remote --cap none --queues --label static --run-id 2110 --repeat 3

   # Dynamic QoS on the surge.
   sudo python3 mininet/run_experiment.py surge \
       --mode remote --cap none --queues --label dynamic --run-id 2113 --repeat 3
   ```

   Then run the final gate:

   ```bash
   python3 -m analysis.analyze --final --strict
   ```

4. **Per-second data.** The receiver must be the upgraded version in this
   branch. No runner change is needed: it writes `results/per_second.csv`
   automatically for every completed flow.

## What is still genuinely blocked

- The dynamic controller file and its policy log do not exist yet, so
  `timeline_dynamic_*.png` cannot show policy-change lines until that teammate
  produces them. The timeline infrastructure and the fallback event-only figure
  already work.
- The five final experiment folders do not exist yet. The current
  `experiments/test/` and `experiments/receiver_test/` data is practice data
  and is never presented as final.
- `results/` is git-ignored, so `metrics.csv`, `per_second.csv`, events, and
  policy logs from the final runs must be preserved locally (or copied into the
  committed `experiments/<label>/` folders by the runner) before the report is
  generated.
