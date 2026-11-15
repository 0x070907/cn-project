# QoS results — methodology and report scaffold

> **Status:** the measurement and analysis pipeline is implemented and tested
> against the practice data in `experiments/test/` and
> `experiments/receiver_test/`. The final numbers are **not available yet**:
> the dynamic controller, its policy log, and the five final experiment folders
> are still pending. Do not quote practice rows as final results.

This document is the place to paste the generated tables and figures after the
final runs. The pipeline writes them to `analysis/output/` (git-ignored); the
commands are at the bottom of this file.

## 1. Experiment matrix

| Report name | Scenario | Controller / policy | Link setup | Repeats |
|---|---|---|---|---|
| `baseline` | `no_congestion` (HIGH + MEDIUM, 3 Mbps demand) | none | TCLink 5 Mbps | 3 |
| `baseline_ryu` | `congestion` (all four, 10 Mbps demand) | forwarding-only Ryu | TCLink 5 Mbps | 3 |
| `static` | `congestion` | static port→queue classification | OVS QoS, 5 Mbps | 3 |
| `dynamic` | `surge` (BACKGROUND joins at t≈10 s) | dynamic queue policy | OVS QoS, 5 Mbps | 3 |
| `static_surge` | `surge` | static port→queue classification | OVS QoS, 5 Mbps | 3 |

`static_surge` is the reference that isolates the effect of *changing* the
policy from the effect of simply having static queues. The four main comparison
charts use `baseline`, `baseline_ryu`, `static`, and `dynamic`.

Run IDs are generated automatically from `time.time()`; each repeat in a batch
increments the ID by one. Do not assign them by hand. For each of the five
reportable folder/scenario pairs, the analysis selects the latest three run IDs
numerically and checks that those three IDs are consecutive. Older runs are
ignored, even when they are not consecutive.

The analysis treats `static` + `congestion` and `static` + `surge` as separate
report groups, so reusing a selected run ID across those groups contaminates the
tables and fails `--final --strict`. Practice folders are excluded from final
tables, figures, and validation; use `--practice` for the separate practice
view.

## 2. Measurement method

Every sender packet is a 28-byte application header (flow ID, sequence number,
run ID, and a `time.monotonic_ns()` send timestamp) followed by payload. The
receiver:

1. Tracks each `(run_id, flow_id)` independently across ports 5001–5004.
2. Counts unique sequence numbers; duplicates do not count as delivered.
3. Records `recv_ts_ns − send_ts_ns` for latency.
4. Uses the sender's START/END packet count to compute loss.
5. Sums payload bytes for goodput and complete data datagrams (header +
   payload) for throughput, for the same unique delivered packets.
6. Writes one row per flow per run to `results/metrics.csv` and one fairness
   row per run to `results/fairness.csv`.
7. Writes one row per flow per second to `results/per_second.csv` for the surge
   timeline.

Both rates use the same measurement duration: the first data packet arrival to
the sender's END message. Duplicate datagrams and the UDP/IP headers added by
the kernel are not included in either byte count. Consequently throughput is
higher than goodput by exactly the QoS-header share, and both are zero when the
duration is zero.

The runner copies the metric rows to
`experiments/<label>/summary.csv` and the event log to
`experiments/<label>/events_<run_id>.csv`.

## 3. Metrics

For a flow with `E` expected packets, `U` unique packets, `B` payload bytes,
`R` complete received application bytes, duration `T`, and per-packet latencies
`L`:

```text
loss %      = max(0, E − U) / E × 100
goodput     = B × 8 / T / 1,000,000              (Mbps, QoS header excluded)
throughput  = R × 8 / T / 1,000,000              (Mbps, QoS header included)
avg latency = mean(L)                            (ms)
p95 latency = nearest-rank 95th percentile(L)    (ms)
Jain index  = (Σ goodput)² / (n × Σ goodput²)
```

`R` is the sum of `len(packet)` for the unique delivered packets, so for packets
of size `S` bytes it is `B + 28 × U`. Fairness remains based on delivered
goodput, because that is the useful application rate.

The report table takes the **median across the latest three selected runs** for
each flow, including median throughput. The fairness table takes the median Jain
index across those runs and shows the min–max spread.

`per_second.csv` is a timeline view, not a second independent measurement. Its
per-second expected count is an even-spread estimate because the sender does
not publish a per-second schedule. The exact totals remain in `metrics.csv`.
Each per-second row has both `goodput_mbps` and `throughput_mbps`; the surge
timeline plots both so the QoS-header overhead is visible over time.

## 4. Expected dynamic behaviour (hypothesis, not a result)

The surge run starts HIGH, MEDIUM, and LOW; BACKGROUND joins around t = 10 s.
The four-flow demand is 10 Mbps against a 5 Mbps bottleneck. The dynamic
controller polls queue/port statistics every 2 s, detects overload (for example,
total rate near 90% of 5 Mbps with rising drops), and changes `SetQueue` rules
so HIGH and MEDIUM remain protected. The policy log should show one or more
changes shortly after the background join. The timeline graph puts those
changes on the same seconds axis as goodput, loss, and p95 latency.

This section must be replaced with what the actual logs show. If the controller
never logs a change, that is a finding to report, not something to hide.

## 5. Results tables

Paste the generated contents of:

- `analysis/output/results_tables.md` (per-flow goodput/throughput medians +
  Jain fairness)
- `analysis/output/tables/per_flow_medians.csv` (includes
  `throughput_mbps_median`)
- `analysis/output/tables/fairness.csv`
- `analysis/output/tables/fairness_by_run.csv`

Expected interpretation:

- **Baseline vs baseline_ryu:** both should show the raw congestion problem
  (all four flows lose packets and see high latency). This establishes that the
  forwarding-only controller does not add QoS.
- **Baseline_ryu vs static:** static queues should protect HIGH and MEDIUM at
  the expense of LOW/BACKGROUND. Loss and p95 latency for the protected flows
  should fall.
- **Static vs dynamic on the surge:** dynamic should react shortly after the
  background join rather than applying one fixed policy for the whole run.
- **Fairness:** a prioritising policy deliberately lowers Jain fairness
  compared with equal sharing. Report that trade-off explicitly; a lower Jain
  index is not automatically a failure when priority is the goal.
- **No-congestion baseline:** only HIGH and MEDIUM run, so its fairness index is
  computed over two flows and is not directly comparable with the four-flow
  congestion runs.

## 6. Figures

| Figure | What to look for |
|---|---|
| `comparison_goodput.png` | protected flows keep their share under static/dynamic QoS |
| `comparison_throughput.png` | same comparison for full application bytes, across all five reportable pairs |
| `comparison_loss.png` | loss drops for HIGH/MEDIUM and moves to LOW/BACKGROUND |
| `comparison_p95_latency.png` | tail latency falls for protected flows |
| `comparison_avg_latency.png` | mean latency trend, less sensitive to outliers |
| `surge_goodput.png`, `surge_throughput.png` | static-surge reference vs dynamic surge |
| `surge_loss.png`, `surge_p95_latency.png` | static-surge reference vs dynamic surge |
| `timeline_dynamic_<run_id>.png` | per-second throughput/goodput/loss/p95 with event and policy lines for each selected run |
| `timeline_dynamic_median.png` | median timeline across the three selected dynamic runs |

The timeline uses the recovered run start from `events.csv`. The policy log must
use `time.monotonic_ns()` for the vertical lines to align.

## 7. Comparability notes

- The baseline uses TCLink's single 5 Mbps queue; static and dynamic use OVS QoS
  on `s1-eth4`. Both are configured with a 100-packet queue to keep loss and
  delay comparable, but they are not the same queue implementation. State this
  limitation in the report.
- The OVS queue minimum rates in `mininet/topology.py` are the values actually
  configured. The report should quote those actual values rather than the stale
  placeholder table in `plan.md`/earlier drafts.
- Sender and receiver timestamps rely on `time.monotonic_ns()` sharing a clock.
  This holds on one machine and inside the Mininet host namespace on this setup;
  if that assumption changes, latency numbers need re-validation.
- Per-second loss is estimated from the even-spread expected count, so treat
  single-second spikes cautiously and use the run totals for headline numbers.
- Goodput and throughput differ by the 28-byte application header on every
  delivered packet. With the configured packet sizes, that gap is larger for
  small packets; quote both labels explicitly rather than treating them as
  interchangeable.
- Throughput uses `len(packet)` as returned by `recvfrom`, so it excludes the
  UDP/IP headers added by the kernel. It is an application-level rate, not a
  link-level or interface-counter rate.

## 8. Reproduce the analysis

```bash
# Validate the files currently on disk.
python3 analysis/validate.py

# Regenerate tables and figures from committed summaries.
python3 -m analysis.analyze

# Final pre-submission gate: five reportable pairs, latest three time-generated
# run IDs per pair, those three consecutive, no duplicate rows, no missing
# flows, fairness present, per-second data present.
python3 -m analysis.analyze --final --strict

# Practice-only figures (clearly marked "not final results").
python3 -m analysis.analyze --practice --output /tmp/qos-practice
```

Run `python3 -m unittest discover -s analysis/tests -v` before committing any
analysis change.
