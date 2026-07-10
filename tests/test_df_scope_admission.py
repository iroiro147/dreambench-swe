"""Regression: DreamForge must namespace written memories by sequence_id (the
value the read query uses as repo_scope), even when the LLM judge sets repo_scope
from its scope.repo (the real repo name, e.g. "configly"). Otherwise the
retrieval gate hard-rejects every memory with repo_scope_conflict and DF silently
degrades to B0 — caught live on 2026-06-30 (DF retrieved 9, admitted 0)."""
from dream_memory.schemas import MemoryType, Provenance, new_memory
from benchmarks import baselines


def _extractor_using_real_repo(payload):
    # Mimics the LLM judge setting repo_scope to the real repo, NOT the sequence id.
    return [
        new_memory(
            content="Reviewer contract: CSV width errors use the CSV_WIDTH prefix.",
            type=MemoryType.HUMAN_FEEDBACK,
            write_reason="test",
            provenance=Provenance(
                trajectory_ids=["myseq-s01-DF"],
                task_ids=["myseq-s01"],
                file_paths=["configly/parser.py"],
                human_feedback_ids=["myseq-e1"],
            ),
            repo_scope="configly",
            file_scope=["configly/parser.py"],
        )
    ]


def _df():
    return baselines.DreamForgePolicy(
        name="DFtest", label="DF", description="test", extractor=_extractor_using_real_repo
    )


_EP = {
    "raw_episode": {
        "task_id": "myseq-s01",
        "sequence_id": "myseq",
        "repo_scope": "myseq",
        "outcome": "success",
        "observations": ["did the thing"],
    }
}


def test_df_write_normalizes_repo_scope_to_sequence_id():
    df = _df()
    df.write(_EP)
    items = df.memory_items()
    assert items, "no memory written"
    assert all(it.repo_scope == "myseq" for it in items), [it.repo_scope for it in items]


def test_df_read_admits_same_sequence_memory():
    df = _df()
    df.write(_EP)
    ctx = df.read({"id": "myseq-s02", "sequence_id": "myseq", "instruction": "add csv parsing", "files": ["configly/parser.py"]})
    assert ctx, "DF.read admitted nothing despite a same-sequence memory (repo_scope_conflict regression)"
