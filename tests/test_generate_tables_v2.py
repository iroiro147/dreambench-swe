"""Fixture-driven tests for the v2 result-table generator."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import generate_tables_v2  # noqa: E402


PENDING_MARKER = "[" + "V2-" + "PENDING]"


FIXTURE = {
    "metadata": {"fixture": True, "schema": "v2-per-condition-per-trap-per-seed"},
    "conditions": {
        "B0": {
            "backbones": {
                "codex-gpt-5.5": {
                    "traps": {
                        "trap-c1-a": {
                            "construct_label": "C1",
                            "construct_name": "Verbatim retention",
                            "seeds": {"1": {"s3_pass_at_1": False}, "2": False, "3": False},
                        },
                        "trap-c2-a": {
                            "construct_label": "C2",
                            "construct_name": "Retrieval precision under interference",
                            "seeds": {"1": False, "2": False, "3": True},
                        },
                    }
                },
                "glm-latest": {
                    "traps": {
                        "trap-c1-a": {
                            "construct_label": "C1",
                            "construct_name": "Verbatim retention",
                            "seeds": {"1": False, "2": False, "3": False},
                        },
                        "trap-c2-a": {
                            "construct_label": "C2",
                            "construct_name": "Retrieval precision under interference",
                            "seeds": {"1": False, "2": True, "3": False},
                        },
                    }
                },
            }
        },
        "B5": {
            "backbones": {
                "codex-gpt-5.5": {
                    "traps": {
                        "trap-c1-a": {
                            "construct_label": "C1",
                            "construct_name": "Verbatim retention",
                            "seeds": {"1": True, "2": True, "3": True},
                        },
                        "trap-c2-a": {
                            "construct_label": "C2",
                            "construct_name": "Retrieval precision under interference",
                            "seeds": {"1": False, "2": True, "3": False},
                        },
                    }
                },
                "glm-latest": {
                    "traps": {
                        "trap-c1-a": {
                            "construct_label": "C1",
                            "construct_name": "Verbatim retention",
                            "seeds": {"1": True, "2": True, "3": False},
                        },
                        "trap-c2-a": {
                            "construct_label": "C2",
                            "construct_name": "Retrieval precision under interference",
                            "seeds": {"1": False, "2": True, "3": False},
                        },
                    }
                },
            }
        },
        "B5-MEM0": {
            "backbone": "codex-gpt-5.5",
            "traps": {
                "trap-c1-a": {
                    "construct_label": "C1",
                    "construct_name": "Verbatim retention",
                    "seeds": {"1": {"passed": False}, "2": {"passed": True}, "3": {"passed": False}},
                }
            },
        },
        "DF-hybrid": {
            "backbones": {
                "codex-gpt-5.5": {
                    "traps": {
                        "trap-c1-a": {
                            "construct_label": "C1",
                            "construct_name": "Verbatim retention",
                            "seeds": {"1": True, "2": True, "3": True},
                        },
                        "trap-c2-a": {
                            "construct_label": "C2",
                            "construct_name": "Retrieval precision under interference",
                            "seeds": {"1": True, "2": True, "3": False},
                        },
                    }
                },
                "glm-latest": {
                    "traps": {
                        "trap-c1-a": {
                            "construct_label": "C1",
                            "construct_name": "Verbatim retention",
                            "seeds": {"1": True, "2": True, "3": True},
                        },
                        "trap-c2-a": {
                            "construct_label": "C2",
                            "construct_name": "Retrieval precision under interference",
                            "seeds": {"1": True, "2": True, "3": False},
                        },
                    }
                },
            }
        },
    },
}


def test_parse_fixture_cells_include_condition_construct_seed_and_backbone() -> None:
    cells = generate_tables_v2.parse_cells(FIXTURE)

    assert len(cells) == 39
    assert {cell.construct_label for cell in cells} == {"C1", "C2"}
    assert {cell.condition for cell in cells} == {"B0", "B5", "B5-MEM0", "DF-hybrid"}
    assert {generate_tables_v2.normalized_backbone(cell.backbone) for cell in cells} == {"codex", "glm"}


def test_all_three_tables_render_from_fixture_without_live_data() -> None:
    rendered = generate_tables_v2.build_rendered(FIXTURE)
    tex = rendered["tex"]
    main_table = generate_tables_v2.render_t1_main_ladder(rendered["t1"])
    construct_table = generate_tables_v2.render_t2_construct_strata(rendered["t2"])
    backbone_table = generate_tables_v2.render_t3_backbone_comparison(rendered["t3"])

    assert rendered["primary_backbone"] == "codex"
    assert tex.count(r"\begin{table}[t]") == 3
    assert r"\toprule" in tex
    assert r"\bottomrule" in tex
    assert r"\label{tab:v2-main-ladder}" in tex
    assert r"\label{tab:v2-construct-strata}" in tex
    assert r"\label{tab:v2-cross-backbone}" in tex
    assert "B5-MEM0 Hosted Mem0" not in main_table
    assert "B5-MEM0" not in construct_table
    assert "B5-MEM0 Hosted Mem0" not in backbone_table
    assert "Probe hybrid" in tex
    assert "Clustered 95\\% CI" in tex
    assert PENDING_MARKER in tex


def test_construct_table_lists_c1_through_c10_and_flags_small_n() -> None:
    rows = generate_tables_v2.build_rendered(FIXTURE)["t2"]

    assert [row["construct"] for row in rows] == [f"C{index}" for index in range(1, 11)]
    c1 = rows[0]
    c10 = rows[-1]
    assert c1["trap_n"] == 1
    assert c1["small_n"] is True
    assert c1["b5"]["n"] == 3
    assert c10["trap_n"] == 0
    assert c10["small_n"] is False


def test_cross_backbone_ordering_check_reports_preserved_ladder() -> None:
    table = generate_tables_v2.build_rendered(FIXTURE)["t3"]

    assert table["ordering"]["preserved"] is True
    assert table["ordering"]["reversed_pairs"] == []
    tex = generate_tables_v2.render_t3_backbone_comparison(table)
    assert "Ladder ordering preserved" in tex
    assert "yes; reversed pairs: none" in tex


def test_folded_s3_cells_can_be_enriched_from_frozen_sequence_metadata() -> None:
    payload = {
        "conditions": {
            "B5": {
                "s3_cells": {
                    "seed1:v2-c9-example": {
                        "sequence_id": "v2-c9-example",
                        "seed": 1,
                        "passed": True,
                        "source_path": "/tmp/20260707T000000Z-SLICE-gpt-5.5-abc/B5/results.json",
                    },
                    "seed1:synth-expr-precedence-derive": {
                        "sequence_id": "synth-expr-precedence-derive",
                        "seed": 1,
                        "passed": False,
                        "source_path": "/tmp/20260707T000000Z-SLICE-gpt-5.5-def/B5/results.json",
                    },
                }
            }
        }
    }

    rendered = generate_tables_v2.build_rendered(
        payload,
        sequence_metadata={"v2-c9-example": {"construct_label": "C9"}},
    )
    cells = rendered["cells"]

    assert {(cell.trap_id, cell.construct_label) for cell in cells} == {
        ("synth-expr-precedence-derive", "C4"),
        ("v2-c9-example", "C9"),
    }
    assert {generate_tables_v2.normalized_backbone(cell.backbone) for cell in cells} == {"codex"}
    assert rendered["primary_backbone"] == "codex"
    main_table = generate_tables_v2.render_t1_main_ladder(rendered["t1"])
    assert "/tmp/" not in main_table
    assert "Codex / GPT-5.5" in main_table


def test_cross_backbone_table_is_omitted_when_glm_scope_is_empty() -> None:
    payload = {
        "conditions": {
            "B5": {
                "s3_cells": {
                    "seed1:config-stale-merge": {
                        "sequence_id": "config-stale-merge",
                        "seed": 1,
                        "passed": True,
                        "source_path": "/tmp/20260707T000000Z-SLICE-gpt-5.5-abc/B5/results.json",
                    }
                }
            },
            "B5-MEM0": {
                "s3_cells": {
                    "seed1:config-stale-merge": {
                        "sequence_id": "config-stale-merge",
                        "seed": 1,
                        "passed": False,
                        "source_path": "/tmp/20260707T000000Z-SLICE-gpt-5.5-def/B5-MEM0/results.json",
                    }
                }
            },
        }
    }

    rendered = generate_tables_v2.build_rendered(payload)
    tex = rendered["tex"]

    assert r"\label{tab:v2-cross-backbone}" not in tex
    assert "GLM-5.2 backbone was scoped out" in tex
    assert "B5-MEM0 Hosted Mem0" not in tex


def test_sequence_metadata_loader_backfills_existing_construct_map(tmp_path: Path) -> None:
    sequence_path = tmp_path / "sequences.jsonl"
    sequence_path.write_text(
        "\n".join(
            [
                json.dumps({"seq_id": "config-stale-merge", "seq_type": "stale-architecture"}),
                json.dumps({"seq_id": "v2-c2-example", "construct_label": "C2"}),
            ]
        ),
        encoding="utf-8",
    )

    metadata = generate_tables_v2.load_sequence_metadata(sequence_path)

    assert metadata["config-stale-merge"]["construct_label"] == "C1"
    assert metadata["v2-c2-example"]["construct_label"] == "C2"


def test_cli_writes_tex_from_fixture(tmp_path: Path) -> None:
    input_path = tmp_path / "fold.json"
    output_path = tmp_path / "tables.tex"
    input_path.write_text(json.dumps(FIXTURE), encoding="utf-8")

    rc = generate_tables_v2.main(["--input", str(input_path), "--output", str(output_path)])

    assert rc == 0
    assert r"\label{tab:v2-main-ladder}" in output_path.read_text(encoding="utf-8")
