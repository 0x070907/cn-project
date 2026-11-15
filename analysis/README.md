# Measurement analysis pipeline

This directory turns the raw CSVs into the Demo 2 tables and figures:

- `analysis/selection.py` — defines the five reportable folder/scenario pairs
  and selects the latest three run IDs for each one.
- `analysis/validate.py` — checks schemas, formulas, duplicate runs, missing
  flows, reused run IDs, and the final five-experiment layout.
- `analysis/loader.py` — reads `experiments/*/summary.csv`, `results/fairness.csv`,
  `results/manifest.csv`, `results/per_second.csv`, `events.csv`, and the
  controller policy log.
- `analysis/aggregate.py` — takes the **median of the latest three selected
  runs** for each flow and builds the Jain fairness table.
- `analysis/plots.py` — comparison bar charts and the dynamic surge timeline.
- `analysis/analyze.py` — runs the whole pipeline in one command.

The pipeline uses only the Python standard library plus `matplotlib` (already
installed on the project machine). It does **not** use pandas.

## Quick start

From the repository root:

```bash
# 1. Validate what is currently on disk (warnings allowed).
python3 analysis/validate.py

# 2. Run tables + figures from the five reportable pairs, selecting the latest
#    three run IDs for each pair and ignoring older runs and practice folders.
python3 -m analysis.analyze

# 3. Exercise the figures with the existing practice data.
#    These outputs are explicitly marked "not final results".
python3 -m analysis.analyze --practice

# 4. Pre-submission gate: latest three time-generated run IDs per pair, with
#    those three IDs consecutive.
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
--final             require the five reportable pairs; check that their latest
                    three time-generated run IDs are consecutive
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
    ├── comparison_throughput.png
    ├── comparison_loss.png
    ├── comparison_p95_latency.png
    ├── comparison_avg_latency.png
    ├── surge_goodput.png
    ├── surge_throughput.png
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
ID among the selected runs.

For each folder/scenario pair the pipeline sorts the available run IDs
numerically (the runner generates them from `time.time()`), keeps the latest
three, and uses only those three for tables, fairness, validation, comparison
charts, and the dynamic surge timelines. Older runs are ignored, even if they
are not consecutive. The three selected IDs must themselves be consecutive;
`--final --strict` fails otherwise.

The main comparison charts cover `baseline`, `baseline_ryu`, `static`, and
`dynamic`. The throughput chart (`comparison_throughput.png`) additionally
includes the `static_surge` reference, so it compares all five reportable
folder/scenario pairs under the same latest-three-run selection.

Practice folders such as `experiments/test/` and
`experiments/receiver_test/` are excluded from the final tables, figures, and
`--final` validation. `--practice` adds a separate practice table and figures
for pipeline testing only. They are never presented as final results.

## Metric definitions

`results/metrics.csv` remains the authoritative per-run table. The analysis
takes the median across the latest three selected runs for each flow:

- **Goodput** = payload bytes × 8 / duration / 10^6 (Mbps, QoS header excluded).
- **Throughput** = total received application bytes × 8 / duration / 10^6
  (Mbps, QoS header included). The byte total is the sum of the complete
  application datagrams for the unique delivered packets: the 28-byte QoS
  header plus payload. UDP/IP headers added by the kernel and duplicate
  datagrams are not included. Both rates use the same first-data-packet → END
  duration, so a zero duration produces zero for both.
- **Packet loss** = max(0, expected − unique received) / expected × 100.
- **Average latency** = mean of `recv_ts_ns − send_ts_ns`.
- **p95 latency** = nearest-rank 95th percentile.
- **Jain fairness** = (Σx)² / (n × Σx²), computed over the flows that ran in
  that scenario.

`results/per_second.csv` is a timeline view produced by the receiver. It has
one row per flow per second:

```text
experiment_id,run_id,flow,second,bucket_seconds,expected_packets,
unique_received,lost_packets,packet_loss_pct,payload_bytes,received_bytes,
goodput_mbps,throughput_mbps,avg_latency_ms,p95_latency_ms
```

`second=0` is the second in which that flow's first data packet arrived.
`bucket_seconds` is normally 1.0 and is smaller for the final partial second;
it is used so goodput and throughput are not inflated in the last bucket. The
sender does not publish a per-second schedule, so `expected_packets` in this
file is an even-spread **estimate** over the measured duration. Use
`metrics.csv` for exact run totals and `per_second.csv` for the shape of the
timeline. The surge timeline shows throughput and goodput together so the
header overhead is visible second by second.

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

3. **Final data collection.** Run IDs are generated automatically by
   `mininet/run_experiment.py` from the current time, and each repeat in a batch
   increments the ID by one. Do not assign manual IDs. `run_all.py` runs the
   four default groups; `static_surge` must be requested separately:

   ```bash
   # Baseline, baseline_ryu, static/congestion and dynamic/surge.
   sudo python3 mininet/run_all.py --repeat 3

   # Static QoS on the surge (reference for dynamic).
   sudo python3 mininet/run_all.py --only static_surge --repeat 3
   ```

   If a group is rerun, its three newest run IDs become the selected repeats.
   Older runs can stay in place; the analysis ignores them. Then run the final
   gate:

   ```bash
   python3 -m analysis.analyze --final --strict
   ```

4. **Per-second and throughput data.** The receiver must be the upgraded
   version in this branch. No runner change is needed: it writes
   `results/per_second.csv` automatically for every completed flow and adds
   `received_bytes`/`throughput_mbps` to both CSVs. `--final --strict` requires
   those throughput columns in every selected run, while legacy practice data
   without them remains readable outside the final gate.

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
