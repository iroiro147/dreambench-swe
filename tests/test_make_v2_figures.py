"""Tests for scripts/make_v2_figures.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


_MPLCONFIG = tempfile.TemporaryDirectory(prefix="dreamforge-mplconfig-")
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", _MPLCONFIG.name)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import make_v2_figures  # noqa: E402


def assert_pdf(path: Path) -> None:
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")
    assert path.stat().st_size > 100


def test_static_figures_render_from_fixture_counts(tmp_path: Path) -> None:
    counts = {
        "C1": {"total": 1, "anti_hoarding": 0},
        "C2": {"total": 2, "anti_hoarding": 1},
        "C10": {"total": 1, "anti_hoarding": 1},
    }

    coverage = make_v2_figures.render_construct_coverage(output_dir=tmp_path, counts=counts)
    contrast = make_v2_figures.render_verbatim_vs_synthesis(output_dir=tmp_path)

    assert coverage.name == "v2_construct_coverage.pdf"
    assert contrast.name == "v2_verbatim_vs_synthesis.pdf"
    assert_pdf(coverage)
    assert_pdf(contrast)


def test_default_construct_coverage_uses_final_frozen_quota() -> None:
    coverage = make_v2_figures.normalize_construct_counts(make_v2_figures.FINAL_V2_CONSTRUCT_COUNTS)

    assert sum(item["total"] for item in coverage.values()) == 60
    assert sum(item["anti_hoarding"] for item in coverage.values()) == 30
    assert coverage["C1"] == {"total": 18, "anti_hoarding": 0}
    assert coverage["C10"] == {"total": 2, "anti_hoarding": 2}
    assert make_v2_figures.ANTI_HOARDING_LABEL == "preregistered anti-hoarding-design quota"
    assert "C9 and C10 fail post-fold B0 headroom" in make_v2_figures.ANTI_HOARDING_NOTE


def test_ladder_plot_renders_from_fold_fixture(tmp_path: Path) -> None:
    fold = {
        "conditions": {
            "UNIT-A": {
                "label": "Unit A",
                "pass_at_1_rate": {
                    "value": 0.5,
                    "display_ci_95": [0.2, 0.8],
                    "numerator": 2,
                    "denominator": 4,
                },
            },
            "UNIT-B": {
                "label": "Unit B",
                "pass_at_1_rate": {
                    "value": 0.75,
                    "display_ci_95": [0.4, 0.95],
                    "numerator": 3,
                    "denominator": 4,
                },
            },
        }
    }
    fold_path = tmp_path / "fold.json"
    fold_path.write_text(json.dumps(fold), encoding="utf-8")

    ladder = make_v2_figures.render_ladder_plot(
        fold_path,
        output_dir=tmp_path,
        conditions=("UNIT-A", "UNIT-B"),
    )

    assert ladder.name == "v2_ladder.pdf"
    assert_pdf(ladder)


def test_v1_b5_stratum_points_use_one_coherent_fold() -> None:
    points = make_v2_figures.b5_v1_stratum_points()

    assert points == [
        {"label": "B5 recall-verbatim", "passed": 48, "n": 54, "rate": 48 / 54},
        {"label": "B5 synthesis/apply", "passed": 0, "n": 12, "rate": 0.0},
    ]
    assert "not a powered comparison" in make_v2_figures.V1_STRATUM_NOTE
