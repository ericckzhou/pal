# M0 model comparison

Pass bar for the local model: chat avg >= 3.5, memory >= 15/20, actions >= 16/20.

## Run 20261124-135919

| Candidate | Chat avg (1-5) | Memory /20 | Actions /20 | Median latency (s) |
|---|---|---|---|---|
| ollama:qwen3.5:4b | unjudged | 19 | 19 | 0.71 |

## Run 20261124-140152

| Candidate | Chat avg (1-5) | Memory /20 | Actions /20 | Median latency (s) |
|---|---|---|---|---|
| ollama:qwen3.5:4b | unjudged | 19 | 19 | 0.9 |

### Chat grading (by hand, no Claude key available)
The first run averaged about 3.45. The prompt caused fake `[m1]` citations without context and refusals of general questions.
After the fix that limits citation and "don't know" rules to turns that include context, the second run averages **4.3/5** (20 answers, graded by Claude Code against each rubric).
Remaining weakness: multi-step arithmetic with `think: false`. chat-08 answered 14 months instead of 15, and chat-15 used 3.5 h of tasks in a 3 h budget.

**Decision:** `qwen3.5:4b` passes all three bars (chat 4.3, memory 19/20, actions 19/20) and is pal's default model.
Follow-ups for M1/M4: drop citation handles that aren't in context in code; pass the known project names into action proposals (act-04).
