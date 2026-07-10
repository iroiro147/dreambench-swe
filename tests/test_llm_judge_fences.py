"""Regression: GRID/LLM judges (glm/kimi/gpt) commonly wrap JSON in markdown
code fences (```json ... ```) and sometimes add prose. _parse_json must strip
fences and extract the JSON; otherwise DreamForge's sleep judges fail on every
call and DF silently writes zero memories (caught live, 2026-06-30)."""
from dream_memory.llm_judge import _parse_json, LLMJudge

FENCE = "`" * 3


def test_parse_json_strips_markdown_fences_array():
    fenced = FENCE + 'json\n[{"memory_type": "human_feedback", "content": "x"}]\n' + FENCE
    r = _parse_json(fenced, "T")
    assert isinstance(r, list) and r[0]["memory_type"] == "human_feedback"


def test_parse_json_strips_fences_object_and_plain():
    obj = FENCE + '\n{"relation": "contradicts", "old_status": "stale"}\n' + FENCE
    assert _parse_json(obj, "T")["relation"] == "contradicts"
    assert _parse_json('[{"a": 1}]', "T")[0]["a"] == 1


def test_parse_json_extracts_json_amid_prose():
    raw = 'Here is the result:\n[{"memory_type": "failure", "content": "y"}]\nDone.'
    assert _parse_json(raw, "T")[0]["memory_type"] == "failure"


def test_consolidation_judge_handles_fenced_output():
    canned = (
        FENCE + 'json\n[{"memory_type":"human_feedback","content":"reviewer says X",'
        '"confidence":0.9,"risk_score":0.1,"staleness_score":0.0,'
        '"scope":{"repo":"configly","files":["configly/parser.py"],"symbols":[]},'
        '"provenance":{"trajectory_id":"t1"},"retrieval_tags":["x"]}]\n' + FENCE
    )
    judge = LLMJudge(complete=lambda prompt: canned)
    out = judge.consolidation_judge(
        {"raw_episodes": [{"trajectory_id": "t1", "task_id": "s1", "repo_scope": "configly"}]}
    )
    assert len(out) == 1
