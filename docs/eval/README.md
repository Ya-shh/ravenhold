# Evaluation results (raw outputs)

Everything in the main README's **Results** section comes from the files in this folder. All of them were produced on
**2026-10-18** from the code in this repository. They are kept exactly as recorded, so they call the engineer Sarah Bennett by the
name she had in the data then, Claire (`claire`); only the name changed since.

## Environment

| | |
|---|---|
| Database | FalkorDB 4.22.1 (`falkordb/falkordb:latest`, Docker), local |
| Machine | Apple Silicon laptop (arm64, macOS) |
| Models | Claude Sonnet 5.5 as the Commander (effort `low`), Claude Haiku 5.5 as the Investigator and Historian |
| Tool transport | MCP over stdio (`mcp_server.py --agents`); every graph tool call in these runs went over MCP |
| Data | The synthetic Ravenhold Pay template graph (116 nodes); each agent run uses its own fresh `GRAPH.COPY` |

Ground truth is computed from the graph (for example the real root service, the top experts from `find_experts`, the
customers `blast_radius` returns, the sources ADR-12 is `DERIVED_FROM`) or from a planted fact. Answers are never scored
on what the model says about its own work.

## Files

| File | Command | What it shows |
|---|---|---|
| `pytest.txt` | `uv run pytest -v` | 32 deterministic tests, no AI calls |
| `bench_concurrency.txt` | `uv run python scripts/bench_concurrency.py` | 100 simultaneous conversations over 5 sandboxes: latencies and integrity checks |
| `bench_scale_5000.txt`, `bench_scale_20000.txt` | `uv run python scripts/bench_scale.py --services N` | Median tool latency on generated companies of 5,000 and 20,000 services |
| `eval_agents_alert_storms_3x3.txt` | `uv run python scripts/eval_agents.py --runs 3` | 3 alert storms × 3 runs: root cause, person paged, noise alert, citations, latency, cost |
| `eval_agents_closed_loop_3x1.txt` | `uv run python scripts/eval_agents.py --runs 1` | The closed loop (decide → act → verify) on all 3 storms: root cause, person paged, noise, right fix proposed on the root service |
| `eval_tracks_first_run.txt` | `uv run python scripts/eval_tracks.py --runs 2` | First per-track run, including the two misses described below |
| `eval_tracks_impact_after_fix.txt` | `uv run python scripts/eval_tracks.py --runs 2 --only impact` | Impact check after the answer-format fix |
| `preset_paths.txt` | `uv run python scripts/preset_paths.py` | The 7 UI preset questions: tool path taken vs documented, expected facts present, cost |
| `lighthouse_mobile.txt` | Lighthouse 12, mobile | Web performance and accessibility scores of the production build (measured before the last small UI additions that day) |
| `eval_tracks_memory_after_fix.txt` | `uv run python scripts/eval_tracks.py --runs 2 --only memory` | Cross-session memory check after the `earlier_session` fix |

After the two fixes, a smaller re-check (1 run per alert storm, 1 run of each company-brain check) passed again: 3/3
alert storms on all three measures, 4/4 company-brain questions, 9/9 knowledge updates. Its summary was printed but its
raw output was not saved, so the main README reports only the runs above.

## Limits

- Small samples: 2 to 9 runs per check. Model output varies between runs, so read these as "passed in every run we
  made", not as a precise accuracy rate.
- Runs were made locally. The live site runs the same code and the same template data.
- The data is synthetic and hand-written, with facts planted so the questions have checkable answers.
- Agent runs cost about $0.02 to $0.06 each in model calls.
