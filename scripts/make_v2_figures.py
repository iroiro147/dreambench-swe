#!/usr/bin/env python3
"""Generate v2 paper figures as PDF files.

The v2 outcome fold is intentionally not embedded here. Live v2 rates are read
from an explicit fold JSON when available.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: E402
    from matplotlib.ticker import MaxNLocator  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - exercised only in lean local envs
    matplotlib = None
    plt = None
    MaxNLocator = None


ROOT = Path(__file__).resolve().parents[1]
FIGURES_DIR = ROOT / "paper" / "figures"
V2_TRAP_ROOT = ROOT / "experiments" / "env" / "v2-traps"

CONSTRUCTS = tuple(f"C{index}" for index in range(1, 11))
ANTI_HOARDING_LABEL = "guaranteed anti-hoarding quota"
FINAL_V2_CONSTRUCT_COUNTS = {
    "C1": {"total": 18, "anti_hoarding": 0},
    "C2": {"total": 6, "anti_hoarding": 6},
    "C3": {"total": 7, "anti_hoarding": 7},
    "C4": {"total": 1, "anti_hoarding": 0},
    "C5": {"total": 6, "anti_hoarding": 6},
    "C6": {"total": 5, "anti_hoarding": 5},
    "C7": {"total": 7, "anti_hoarding": 0},
    "C8": {"total": 4, "anti_hoarding": 0},
    "C9": {"total": 4, "anti_hoarding": 4},
    "C10": {"total": 2, "anti_hoarding": 2},
}
V1_VERBATIM_SYNTHESIS = (
    ("B5 verbatim", 0.727),
    ("B5 synthesis", 0.250),
)
CONDITION_LABELS = {
    "B0": "B0 no memory",
    "B1": "B1 raw episodic",
    "B2": "B2 vector traces",
    "B3": "B3 reflection-only",
    "B4": "B4 untyped summary",
    "B5": "B5 verbatim event-memory",
    "B5-MEM0": "B5 live Mem0",
    "B6": "B6 subtask memory",
    "B7": "B7 task tracker",
    "DF": "Probe typed-only",
    "DF-RAW-ONLY": "Probe raw-only",
    "DF-HYBRID": "Probe hybrid",
    "DF-STRICT": "Probe strict",
    "A0": "A0 episodic-only",
    "A2": "A2 no contradiction repair",
    "A4": "A4 no counterfactual replay",
    "A5": "A5 no stale suppression",
    "A6": "A6 no retrieval gate",
    "A11": "A11 forced consolidation",
}
CONDITION_ORDER = (
    "B0",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B5-MEM0",
    "B6",
    "B7",
    "DF",
    "DF-raw-only",
    "DF-hybrid",
    "DF-strict",
    "A0",
    "A2",
    "A4",
    "A5",
    "A6",
    "A11",
)
WILSON_Z_95 = 1.959963984540054


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    trap_root = Path(args.trap_root)
    construct_counts = collect_construct_counts(trap_root) if args.use_trap_root_counts else None
    paths = [
        render_construct_coverage(output_dir=output_dir, trap_root=trap_root, counts=construct_counts),
        render_verbatim_vs_synthesis(output_dir=output_dir, fold_json=args.fold_json),
    ]
    if args.fold_json:
        paths.insert(1, render_ladder_plot(args.fold_json, output_dir=output_dir))
    else:
        print("Skipping ladder plot: --fold-json not supplied; v2 live results require an explicit fold JSON.")

    for path in paths:
        print(f"Wrote {display_path(path)}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate DreamBench-SWE v2 PDF figures.")
    parser.add_argument("--fold-json", type=Path, default=None, help="Fold JSON for the v2 ladder plot.")
    parser.add_argument("--output-dir", type=Path, default=FIGURES_DIR, help="Directory for emitted PDFs.")
    parser.add_argument(
        "--trap-root",
        type=Path,
        default=V2_TRAP_ROOT,
        help="Directory containing v2 trap sequence.json files when --use-trap-root-counts is set.",
    )
    parser.add_argument(
        "--use-trap-root-counts",
        action="store_true",
        help="Render the authored trap-root counts instead of the frozen final v2 construct quota.",
    )
    return parser.parse_args(argv)


def render_construct_coverage(
    *,
    output_dir: Path = FIGURES_DIR,
    trap_root: Path = V2_TRAP_ROOT,
    counts: Mapping[str, Any] | None = None,
    filename: str = "v2_construct_coverage.pdf",
) -> Path:
    """Render the frozen final C1-C10 v2 construct quota with anti-hoarding highlighted."""

    coverage = normalize_construct_counts(counts) if counts is not None else normalize_construct_counts(FINAL_V2_CONSTRUCT_COUNTS)
    labels = list(CONSTRUCTS)
    totals = [coverage[label]["total"] for label in labels]
    anti = [coverage[label]["anti_hoarding"] for label in labels]
    path = output_dir / filename
    return save_construct_coverage_fallback(path, labels, totals, anti)


def collect_construct_counts(trap_root: Path = V2_TRAP_ROOT) -> dict[str, dict[str, int]]:
    """Count C1-C10 trap definitions from repository sequence JSON files."""

    trap_root = Path(trap_root)
    if not trap_root.exists():
        raise FileNotFoundError(f"trap root does not exist: {trap_root}")

    counts = empty_construct_counts()
    sequence_paths = sorted(trap_root.glob("*/sequence.json"))
    if not sequence_paths:
        raise ValueError(f"no sequence.json files found under {trap_root}")

    for sequence_path in sequence_paths:
        payload = read_json(sequence_path)
        construct = str(payload.get("construct_label") or "").upper()
        if construct not in counts:
            raise ValueError(f"{sequence_path} has unsupported construct_label={construct!r}")
        counts[construct]["total"] += 1
        if payload.get("anti_hoarding") is True:
            counts[construct]["anti_hoarding"] += 1
    return counts


def render_ladder_plot(
    fold_json: Path | Mapping[str, Any],
    *,
    output_dir: Path = FIGURES_DIR,
    conditions: Sequence[str] | None = None,
    filename: str = "v2_ladder.pdf",
) -> Path:
    """Render pass@1 by condition from an explicit fold JSON."""

    payload = read_json(fold_json) if isinstance(fold_json, (str, Path)) else fold_json
    points = extract_ladder_points(payload, conditions=conditions)
    if not points:
        raise ValueError("fold JSON did not contain any plottable pass@1 condition entries")

    labels = [point["label"] for point in points]
    rates = [point["rate"] for point in points]
    ci_low = [point["ci_low"] for point in points]
    ci_high = [point["ci_high"] for point in points]
    path = output_dir / filename
    if plt is None:
        return save_ladder_fallback(path, labels, rates, ci_low, ci_high, points)
    lower_errors = [rate - low for rate, low in zip(rates, ci_low)]
    upper_errors = [high - rate for rate, high in zip(rates, ci_high)]

    fig, ax = plt.subplots(figsize=(max(6.8, 1.35 * len(points)), 3.8))
    x_values = list(range(len(points)))
    ax.plot(x_values, rates, color="#2f5d62", marker="o", linewidth=1.8, markersize=5.5)
    ax.errorbar(
        x_values,
        rates,
        yerr=[lower_errors, upper_errors],
        fmt="none",
        ecolor="#22333b",
        elinewidth=1.2,
        capsize=4,
        capthick=1.0,
    )

    for x_value, point in zip(x_values, points):
        n_suffix = f" n={point['n']}" if point.get("n") is not None else ""
        ax.text(
            x_value,
            min(1.02, point["ci_high"] + 0.035),
            f"{point['rate']:.3f}{n_suffix}",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    ax.set_title("v2 pass@1 ladder from fold JSON")
    ax.set_ylabel("pass@1")
    ax.set_xticks(x_values)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_ylim(0, 1.08)
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return save_pdf(fig, path)


def extract_ladder_points(
    payload: Mapping[str, Any],
    *,
    conditions: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract condition rates and CIs from supported fold JSON shapes."""

    selected = {condition for condition in conditions} if conditions else None
    entries = fold_condition_entries(payload)
    points: list[dict[str, Any]] = []
    for condition, summary in entries:
        if selected is not None and condition not in selected:
            continue
        rate_blob = find_rate_blob(summary)
        if rate_blob is None:
            continue
        point = point_from_rate_blob(condition, summary, rate_blob)
        if point is not None:
            points.append(point)

    if selected is not None:
        order = {condition: index for index, condition in enumerate(conditions)}
        points.sort(key=lambda point: order[point["condition"]])
    else:
        points.sort(key=lambda point: condition_sort_key(point["condition"]))
    return points


def render_verbatim_vs_synthesis(
    *,
    output_dir: Path = FIGURES_DIR,
    filename: str = "v2_verbatim_vs_synthesis.pdf",
    fold_json: Path | None = None,
) -> Path:
    """Render the real v1 B5 verbatim-vs-synthesis contrast."""

    labels = [label for label, _rate in V1_VERBATIM_SYNTHESIS]
    rates = [rate for _label, rate in V1_VERBATIM_SYNTHESIS]
    colors = ["#5b677a", "#d4772f"]
    path = output_dir / filename
    note = verbatim_synthesis_note(fold_json)
    if plt is None:
        return save_verbatim_synthesis_fallback(path, labels, rates, note)

    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    x_values = list(range(len(labels)))
    ax.bar(x_values, rates, width=0.58, color=colors, edgecolor="#2d2f33", linewidth=0.9)
    for x_value, rate in zip(x_values, rates):
        ax.text(x_value, rate + 0.025, f"{rate:.3f}", ha="center", va="bottom", fontsize=9)

    ax.set_title("B5 stratum contrast, v1 fold")
    ax.set_ylabel("pass@1")
    ax.set_xticks(x_values)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.0)
    ax.text(
        0.5,
        -0.23,
        note,
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=8,
        color="#555",
    )
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    return save_pdf(fig, path)


def verbatim_synthesis_note(fold_json: Path | None = None) -> str:
    if fold_json is None:
        return "v1 observed rates; v2 stratum validity is reported from the fold JSON"
    try:
        payload = read_json(fold_json)
    except (OSError, json.JSONDecodeError):
        return "v1 observed rates; v2 stratum validity unavailable from supplied fold JSON"
    cells = (
        payload.get("conditions", {})
        .get("B0", {})
        .get("s3_cells", {})
        if isinstance(payload.get("conditions"), Mapping)
        else {}
    )
    if not isinstance(cells, Mapping):
        return "v1 observed rates; v2 stratum validity unavailable from supplied fold JSON"
    headroom = []
    for stratum in ("c9", "c10"):
        stratum_cells = [
            item
            for item in cells.values()
            if isinstance(item, Mapping)
            and str(item.get("sequence_id") or "").lower().startswith(f"v2-{stratum}-")
        ]
        if not stratum_cells:
            continue
        passed = sum(1 for item in stratum_cells if bool(item.get("passed")))
        headroom.append(f"{stratum.upper()} B0 {passed}/{len(stratum_cells)}")
    if not headroom:
        return "v1 observed rates; v2 stratum validity is reported from the fold JSON"
    return "v1 observed rates; v2 headroom: " + ", ".join(headroom)


def fold_condition_entries(payload: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    ladder = payload.get("ladder")
    if isinstance(ladder, list):
        entries = []
        for item in ladder:
            if isinstance(item, Mapping):
                condition = str(item.get("condition") or "").strip()
                if condition:
                    entries.append((condition, item))
        if entries:
            return entries

    ablation_ladder = payload.get("ablation_ladder")
    if isinstance(ablation_ladder, Mapping):
        return [
            (str(condition), summary)
            for condition, summary in ablation_ladder.items()
            if isinstance(summary, Mapping)
        ]

    conditions = payload.get("conditions")
    if isinstance(conditions, Mapping):
        return [
            (str(condition), summary)
            for condition, summary in conditions.items()
            if isinstance(summary, Mapping)
        ]

    return [
        (str(condition), summary)
        for condition, summary in payload.items()
        if isinstance(summary, Mapping) and looks_like_condition_key(str(condition))
    ]


def find_rate_blob(summary: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("pass_at_1_rate", "pooled_s3", "pooled", "pass_at_1"):
        value = summary.get(key)
        if isinstance(value, Mapping):
            return value
    if any(key in summary for key in ("value", "rate", "numerator", "denominator", "n", "passed")):
        return summary
    return None


def point_from_rate_blob(
    condition: str,
    summary: Mapping[str, Any],
    blob: Mapping[str, Any],
) -> dict[str, Any] | None:
    rate = first_float(blob, ("value", "rate", "pass_at_1"))
    numerator = first_int(blob, ("numerator", "passed", "successes"))
    denominator = first_int(blob, ("denominator", "n", "total"))
    if rate is None and numerator is not None and denominator:
        rate = numerator / denominator
    if rate is None:
        return None

    ci = first_ci(blob, ("display_ci_95", "wilson_ci_95", "exact_binomial_ci_95", "bootstrap_ci_95", "ci_95"))
    if ci is None and numerator is not None and denominator:
        ci = wilson_ci(numerator, denominator)
    if ci is None:
        raise ValueError(f"{condition} has pass@1={rate} but no CI or numerator/denominator")

    label = str(summary.get("label") or CONDITION_LABELS.get(condition.upper()) or condition)
    return {
        "condition": condition,
        "label": label,
        "rate": clamp01(rate),
        "ci_low": clamp01(ci[0]),
        "ci_high": clamp01(ci[1]),
        "n": denominator,
        "numerator": numerator,
    }


def normalize_construct_counts(counts: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    normalized = empty_construct_counts()
    for construct, value in counts.items():
        key = str(construct).upper()
        if key not in normalized:
            raise ValueError(f"unsupported construct label: {construct!r}")
        if isinstance(value, Mapping):
            total = coerce_int(value.get("total"))
            anti_hoarding = coerce_int(value.get("anti_hoarding", value.get("anti", 0)))
        else:
            total = coerce_int(value)
            anti_hoarding = 0
        if total is None or anti_hoarding is None:
            raise ValueError(f"non-integer count for {construct!r}: {value!r}")
        if total < 0 or anti_hoarding < 0 or anti_hoarding > total:
            raise ValueError(f"invalid count for {construct!r}: {value!r}")
        normalized[key] = {"total": total, "anti_hoarding": anti_hoarding}
    return normalized


def empty_construct_counts() -> dict[str, dict[str, int]]:
    return {construct: {"total": 0, "anti_hoarding": 0} for construct in CONSTRUCTS}


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_pdf(fig: Any, path: Path) -> Path:
    if plt is None:
        raise RuntimeError("matplotlib is required for save_pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="pdf", bbox_inches="tight")
    plt.close(fig)
    return path


def save_construct_coverage_fallback(path: Path, labels: Sequence[str], totals: Sequence[int], anti: Sequence[int]) -> Path:
    width, height = 612.0, 360.0
    left, bottom, plot_width, plot_height = 58.0, 62.0, 490.0, 220.0
    max_y = max([1, *totals]) + 1
    bar_width = plot_width / len(labels) * 0.58
    commands: list[str] = [
        text_command("v2 construct quota for final 60-trap fold", 58, 322, 14),
        text_command("trap count", 18, 180, 9),
        line_command(left, bottom, left + plot_width, bottom, "#333333", 0.8),
        line_command(left, bottom, left, bottom + plot_height, "#333333", 0.8),
        text_command("final 60-trap quota", 430, 323, 8),
        rect_command(410, 317, 14, 8, "#d8dde6", "#4d5565"),
        text_command(ANTI_HOARDING_LABEL, 430, 305, 8),
        rect_command(410, 299, 14, 8, "#f2a900", "#3f3420"),
    ]
    for tick in range(max_y + 1):
        y = bottom + plot_height * tick / max_y
        commands.append(line_command(left, y, left + plot_width, y, "#e5e7eb", 0.35))
        commands.append(text_command(str(tick), left - 18, y - 3, 7))

    for index, label in enumerate(labels):
        center = left + (index + 0.5) * plot_width / len(labels)
        total_height = plot_height * totals[index] / max_y
        anti_height = plot_height * anti[index] / max_y
        x = center - bar_width / 2
        commands.append(rect_command(x, bottom, bar_width, total_height, "#d8dde6", "#4d5565"))
        if anti_height > 0:
            commands.append(rect_command(x, bottom, bar_width, anti_height, "#f2a900", "#3f3420"))
            commands.extend(hatch_commands(x, bottom, bar_width, anti_height))
        commands.append(text_command(label, center - 7, bottom - 18, 8))
        commands.append(text_command(str(totals[index]), center - 3, bottom + total_height + 8, 8))
    return write_basic_pdf(path, "\n".join(commands), width=width, height=height)


def save_ladder_fallback(
    path: Path,
    labels: Sequence[str],
    rates: Sequence[float],
    ci_low: Sequence[float],
    ci_high: Sequence[float],
    points: Sequence[Mapping[str, Any]],
) -> Path:
    width, height = max(612.0, 86.0 * len(labels)), 372.0
    left, bottom, plot_width, plot_height = 60.0, 80.0, width - 105.0, 220.0
    commands: list[str] = [
        text_command("v2 pass@1 ladder from fold JSON", left, 334, 14),
        text_command("pass@1", 20, 190, 9),
        line_command(left, bottom, left + plot_width, bottom, "#333333", 0.8),
        line_command(left, bottom, left, bottom + plot_height, "#333333", 0.8),
    ]
    for tick in range(6):
        value = tick / 5
        y = bottom + plot_height * value
        commands.append(line_command(left, y, left + plot_width, y, "#e5e7eb", 0.35))
        commands.append(text_command(f"{value:.1f}", left - 28, y - 3, 7))

    xs = []
    for index in range(len(labels)):
        if len(labels) == 1:
            xs.append(left + plot_width / 2)
        else:
            xs.append(left + index * plot_width / (len(labels) - 1))

    for index in range(1, len(labels)):
        commands.append(
            line_command(
                xs[index - 1],
                bottom + plot_height * rates[index - 1],
                xs[index],
                bottom + plot_height * rates[index],
                "#2f5d62",
                1.4,
            )
        )

    for x, label, rate, low, high, point in zip(xs, labels, rates, ci_low, ci_high, points):
        y = bottom + plot_height * rate
        low_y = bottom + plot_height * low
        high_y = bottom + plot_height * high
        commands.append(line_command(x, low_y, x, high_y, "#22333b", 1.0))
        commands.append(line_command(x - 5, low_y, x + 5, low_y, "#22333b", 1.0))
        commands.append(line_command(x - 5, high_y, x + 5, high_y, "#22333b", 1.0))
        commands.append(circle_command(x, y, 3.2, "#2f5d62", "#2f5d62"))
        n_suffix = f" n={point['n']}" if point.get("n") is not None else ""
        commands.append(text_command(f"{rate:.3f}{n_suffix}", x - 20, min(height - 34, high_y + 13), 8))
        commands.append(text_command(label[:22], x - 32, bottom - 30, 7))
    return write_basic_pdf(path, "\n".join(commands), width=width, height=height)


def save_verbatim_synthesis_fallback(
    path: Path,
    labels: Sequence[str],
    rates: Sequence[float],
    note: str,
) -> Path:
    width, height = 468.0, 330.0
    left, bottom, plot_width, plot_height = 70.0, 78.0, 320.0, 205.0
    bar_width = 72.0
    colors = ("#5b677a", "#d4772f")
    commands: list[str] = [
        text_command("B5 stratum contrast, v1 fold", 70, 296, 14),
        text_command("pass@1", 27, 180, 9),
        line_command(left, bottom, left + plot_width, bottom, "#333333", 0.8),
        line_command(left, bottom, left, bottom + plot_height, "#333333", 0.8),
        text_command(note, 78, 34, 8),
    ]
    for tick in range(6):
        value = tick / 5
        y = bottom + plot_height * value
        commands.append(line_command(left, y, left + plot_width, y, "#e5e7eb", 0.35))
        commands.append(text_command(f"{value:.1f}", left - 28, y - 3, 7))

    for index, (label, rate) in enumerate(zip(labels, rates)):
        center = left + (index + 1) * plot_width / 3
        height_value = plot_height * rate
        commands.append(
            rect_command(center - bar_width / 2, bottom, bar_width, height_value, colors[index], "#2d2f33")
        )
        commands.append(text_command(f"{rate:.3f}", center - 15, bottom + height_value + 12, 9))
        commands.append(text_command(label, center - 42, bottom - 25, 8))
    return write_basic_pdf(path, "\n".join(commands), width=width, height=height)


def write_basic_pdf(path: Path, stream_text: str, *, width: float, height: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = stream_text.encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.1f} {height:.1f}] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{index} 0 obj\n".encode("ascii"))
        parts.append(obj)
        parts.append(b"\nendobj\n")
    xref_offset = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(b"".join(parts))
    return path


def text_command(text: str, x: float, y: float, size: float) -> str:
    return f"0.067 0.094 0.153 rg BT /F1 {size:.1f} Tf {x:.1f} {y:.1f} Td ({escape_pdf_text(text)}) Tj ET"


def line_command(x1: float, y1: float, x2: float, y2: float, color: str, width: float) -> str:
    r, g, b = rgb(color)
    return f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S"


def rect_command(x: float, y: float, width: float, height: float, fill: str, stroke: str) -> str:
    fr, fg, fb = rgb(fill)
    sr, sg, sb = rgb(stroke)
    return (
        f"{fr:.3f} {fg:.3f} {fb:.3f} rg {sr:.3f} {sg:.3f} {sb:.3f} RG "
        f"0.70 w {x:.1f} {y:.1f} {width:.1f} {max(0.0, height):.1f} re B"
    )


def circle_command(x: float, y: float, radius: float, fill: str, stroke: str) -> str:
    k = 0.5522847498 * radius
    fr, fg, fb = rgb(fill)
    sr, sg, sb = rgb(stroke)
    return (
        f"{fr:.3f} {fg:.3f} {fb:.3f} rg {sr:.3f} {sg:.3f} {sb:.3f} RG "
        f"{x + radius:.1f} {y:.1f} m "
        f"{x + radius:.1f} {y + k:.1f} {x + k:.1f} {y + radius:.1f} {x:.1f} {y + radius:.1f} c "
        f"{x - k:.1f} {y + radius:.1f} {x - radius:.1f} {y + k:.1f} {x - radius:.1f} {y:.1f} c "
        f"{x - radius:.1f} {y - k:.1f} {x - k:.1f} {y - radius:.1f} {x:.1f} {y - radius:.1f} c "
        f"{x + k:.1f} {y - radius:.1f} {x + radius:.1f} {y - k:.1f} {x + radius:.1f} {y:.1f} c B"
    )


def hatch_commands(x: float, y: float, width: float, height: float) -> list[str]:
    commands = []
    offset = -height
    while offset < width:
        x1 = x + max(0.0, offset)
        y1 = y + max(0.0, -offset)
        x2 = x + min(width, offset + height)
        y2 = y + min(height, width - offset)
        commands.append(line_command(x1, y1, x2, y2, "#6b4a00", 0.35))
        offset += 8
    return commands


def rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def first_float(mapping: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def first_int(mapping: Mapping[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        value = coerce_int(mapping.get(key))
        if value is not None:
            return value
    return None


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def first_ci(mapping: Mapping[str, Any], keys: Sequence[str]) -> tuple[float, float] | None:
    for key in keys:
        value = mapping.get(key)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
            continue
        low, high = value
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            if math.isfinite(float(low)) and math.isfinite(float(high)):
                return (float(low), float(high))
    return None


def wilson_ci(successes: int, n: int, z: float = WILSON_Z_95) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("Wilson CI requires n > 0")
    phat = successes / n
    denominator = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denominator
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n) / denominator
    return (center - margin, center + margin)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def condition_sort_key(condition: str) -> tuple[int, str]:
    try:
        return (CONDITION_ORDER.index(condition), condition)
    except ValueError:
        return (len(CONDITION_ORDER), condition)


def looks_like_condition_key(value: str) -> bool:
    upper = value.upper()
    return upper in CONDITION_LABELS or upper.startswith("B") or upper.startswith("DF") or upper.startswith("A")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
