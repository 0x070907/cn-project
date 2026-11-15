"""Run the complete offline analysis pipeline.

Examples:

    python3 -m analysis.analyze                    # validate + tables + figures
    python3 -m analysis.analyze --practice         # practice figures from test/
    python3 -m analysis.analyze --final --strict   # final pre-submission gate
    python3 analysis/analyze.py --skip-plots

The pipeline never invents missing experiments.  It writes the tables and
figures that the available data supports and lists everything it skipped in
``analysis/output/pipeline_summary.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.aggregate import (  # noqa: E402
    FAIRNESS_RUN_FIELDS,
    FAIRNESS_TABLE_FIELDS,
    FLOW_TABLE_FIELDS,
    aggregate_fairness,
    aggregate_flow_medians,
    canonical_rows,
    comparison_rows,
    fairness_per_run,
    render_fairness_table,
    render_flow_table,
    write_csv,
)
from analysis.loader import (  # noqa: E402
    Dataset,
    available_canonical,
    available_practice,
    load_dataset,
)
from analysis.plots import (  # noqa: E402
    NoPlotData,
    plot_comparison,
    plot_surge_figures,
    plot_surge_reference,
)
from analysis.schema import (  # noqa: E402
    CANONICAL_EXPERIMENTS,
    COMPARISON_EXPERIMENTS,
    REPO_ROOT,
)
from analysis.validate import validate_all  # noqa: E402


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _table_markdown(
    flow_rows: list[dict],
    fairness_rows: list[dict],
    *,
    title: str,
    note: str,
) -> str:
    return (
        f"# {title}\n\n"
        f"{note}\n\n"
        "## Per-flow medians\n\n"
        f"{render_flow_table(flow_rows)}\n"
        "## Jain fairness\n\n"
        f"{render_fairness_table(fairness_rows)}\n"
    )


def run_pipeline(args: argparse.Namespace) -> int:
    results_dir: Path = args.results
    experiments_dir: Path = args.experiments
    output_dir: Path = args.output
    tables_dir = output_dir / "tables"
    figures_dir = output_dir / "figures"

    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    validation = validate_all(
        results_dir,
        experiments_dir,
        strict=args.strict,
        final=args.final,
    )
    validation_text = validation.render()
    _write_text(output_dir / "validation_report.txt", validation_text + "\n")
    print(validation_text)

    if (args.strict or args.final) and validation.errors:
        print("\nvalidation failed; stop before generating results (use --force to override)")
        if not args.force:
            return 2

    dataset: Dataset = load_dataset(
        results_dir,
        experiments_dir,
        policy_log=args.policy_log,
    )
    flow_rows = aggregate_flow_medians(dataset)
    fairness_rows = aggregate_fairness(dataset)
    fairness_runs = fairness_per_run(dataset)

    write_csv(tables_dir / "per_flow_medians.csv", FLOW_TABLE_FIELDS, flow_rows)
    write_csv(tables_dir / "fairness.csv", FAIRNESS_TABLE_FIELDS, fairness_rows)
    write_csv(tables_dir / "fairness_by_run.csv", FAIRNESS_RUN_FIELDS, fairness_runs)

    all_groups = available_canonical(dataset)
    practice_available = available_practice(dataset)
    note = (
        "Values are medians across the latest three run IDs for each "
        "folder/scenario pair. Older runs and practice folders are excluded. "
        "`n_runs` shows how many selected runs contributed; a dash means the "
        "flow was not part of that scenario or no row was recorded."
    )
    table_markdown = _table_markdown(
        flow_rows,
        fairness_rows,
        title="QoS experiment results",
        note=note,
    )
    _write_text(output_dir / "results_tables.md", table_markdown)

    figures: list[Path] = []
    skipped: list[str] = []
    warnings: list[str] = []

    if not args.skip_plots:
        main_rows = comparison_rows(flow_rows)
        main_experiments = {
            row["experiment"] for row in main_rows
            if row["experiment"] in COMPARISON_EXPERIMENTS
        }
        missing = [
            name for name in COMPARISON_EXPERIMENTS if name not in main_experiments
        ]
        if missing:
            skipped.append(
                "comparison bar charts need all four main experiments; missing: "
                + ", ".join(missing)
            )
        if main_rows:
            if missing:
                warnings.append(
                    "the comparison figures use only the experiments that exist"
                )
            for metric in ("goodput_mbps", "packet_loss_pct", "p95_latency_ms", "avg_latency_ms"):
                try:
                    figures.append(
                        plot_comparison(
                            main_rows,
                            figures_dir,
                            metric=metric,
                            title="QoS comparison — median of latest three runs",
                        )
                    )
                except NoPlotData as error:
                    skipped.append(str(error))

        # Throughput is compared across all five reportable pairs, including
        # the static-surge reference used by the dynamic experiment.
        throughput_rows = canonical_rows(flow_rows)
        throughput_found = {
            str(row["experiment"]) for row in throughput_rows
        }
        throughput_missing = [
            name for name in CANONICAL_EXPERIMENTS if name not in throughput_found
        ]
        if throughput_missing:
            skipped.append(
                "throughput comparison needs all five reportable pairs; missing: "
                + ", ".join(throughput_missing)
            )
        if throughput_rows:
            try:
                figures.append(
                    plot_comparison(
                        throughput_rows,
                        figures_dir,
                        metric="throughput_mbps",
                        title=(
                            "Throughput comparison — all five canonical experiments "
                            "(median of latest three runs)"
                        ),
                        experiments=tuple(CANONICAL_EXPERIMENTS),
                    )
                )
            except NoPlotData as error:
                skipped.append(str(error))

        surge_rows = [
            row for row in flow_rows
            if row["experiment"] in {"static_surge", "dynamic"}
        ]
        if surge_rows:
            try:
                figures.extend(plot_surge_reference(flow_rows, figures_dir))
            except NoPlotData as error:
                skipped.append(str(error))

        if "dynamic" in all_groups:
            try:
                figures.extend(
                    plot_surge_figures(
                        dataset,
                        results_dir,
                        experiments_dir,
                        figures_dir,
                        canonical="dynamic",
                        policy_log=args.policy_log,
                    )
                )
            except NoPlotData as error:
                skipped.append(str(error))
        else:
            skipped.append(
                "dynamic surge timeline needs a dynamic/surge summary and "
                "results/per_second.csv"
            )

        if args.practice:
            practice_rows = aggregate_flow_medians(dataset, practice=True)
            practice_fairness_rows = aggregate_fairness(dataset, practice=True)
            if practice_rows:
                practice_dir = figures_dir / "practice"
                practice_experiments = sorted(
                    {str(row["experiment"]) for row in practice_rows}
                )
                for metric in (
                    "goodput_mbps",
                    "throughput_mbps",
                    "packet_loss_pct",
                    "p95_latency_ms",
                ):
                    try:
                        figures.append(
                            plot_comparison(
                                practice_rows,
                                practice_dir,
                                metric=metric,
                                filename=f"practice_{metric}.png",
                                title="Practice data — not final results",
                                experiments=practice_experiments,
                            )
                        )
                    except NoPlotData as error:
                        skipped.append(str(error))
                _write_text(
                    output_dir / "practice_tables.md",
                    _table_markdown(
                        practice_rows,
                        practice_fairness_rows,
                        title="Practice data (not final results)",
                        note=(
                            "These rows come from the untracked practice folders "
                            "`experiments/test` and `experiments/receiver_test`. "
                            "They exercise the pipeline and must not be reported "
                            "as the final experiment set."
                        ),
                    ),
                )

    summary_lines = [
        "# Analysis pipeline summary",
        "",
        f"Canonical experiments found: {', '.join(all_groups) or 'none'}",
        f"Practice groups found: {', '.join(practice_available) or 'none'}",
        "",
        "## Tables",
        "",
        f"- `{tables_dir / 'per_flow_medians.csv'}`",
        f"- `{tables_dir / 'fairness.csv'}`",
        f"- `{tables_dir / 'fairness_by_run.csv'}`",
        f"- `{output_dir / 'results_tables.md'}`",
        "",
        "## Figures",
        "",
    ]
    if figures:
        summary_lines.extend(f"- `{path}`" for path in figures)
    else:
        summary_lines.append("- none generated")

    summary_lines.extend(["", "## Skipped", ""])
    if skipped:
        summary_lines.extend(f"- {item}" for item in skipped)
    else:
        summary_lines.append("- nothing")
    if warnings:
        summary_lines.extend(["", "## Warnings", ""])
        summary_lines.extend(f"- {item}" for item in warnings)
    if dataset.duplicate_rows:
        summary_lines.extend(["", "## Conflicting duplicate summary rows", ""])
        summary_lines.extend(f"- {item}" for item in dataset.duplicate_rows)

    _write_text(output_dir / "pipeline_summary.md", "\n".join(summary_lines) + "\n")

    print("\nanalysis written to", output_dir)
    print(f"tables: {len(flow_rows)} flow rows, {len(fairness_rows)} fairness rows")
    print(f"figures: {len(figures)}")
    for item in skipped:
        print("skipped:", item)
    for path in figures:
        print("figure :", path)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="run the QoS analysis pipeline")
    parser.add_argument(
        "--results",
        type=Path,
        default=REPO_ROOT / "results",
        help="raw results directory (metrics.csv, fairness.csv, per_second.csv)",
    )
    parser.add_argument(
        "--experiments",
        type=Path,
        default=REPO_ROOT / "experiments",
        help="committed summaries in experiments/<label>/summary.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "analysis" / "output",
        help="directory for generated tables and figures",
    )
    parser.add_argument(
        "--policy-log",
        type=Path,
        default=None,
        help="explicit dynamic-controller policy CSV (optional)",
    )
    parser.add_argument(
        "--practice",
        action="store_true",
        help="also draw figures from non-canonical practice folders (not final results)",
    )
    parser.add_argument(
        "--skip-plots",
        action="store_true",
        help="write only the CSV/markdown tables",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat validation warnings as errors",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help=(
            "require the five reportable pairs and check the latest three "
            "time-generated run IDs per pair"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="continue generating outputs even when strict validation fails",
    )
    args = parser.parse_args()

    return run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
