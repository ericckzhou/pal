"""Gmail query writing: does the local model search when mail would help, stay out when it wouldn't, and write
queries that keep the message's names, dates and operators without inventing any?

Each case in eval/gmail/cases.jsonl is a made-up chat message that already passed pal's mail trigger:
  {"text": ..., "search": true|false, "all": [...], "any": [...], "none": [...]}
- search  whether pal should search at all
- all     strings the query must contain (case-insensitive)
- any     at least one of these must appear (when given)
- none    strings it must not contain (invented senders, unknown operators)
The query is scored after pal's own clean_query, exactly what would be sent to Gmail. Nothing touches Gmail.

Usage:  uv run --no-sync python eval/gmail_eval.py [--repeat N] [--model NAME]
Needs:  Ollama running locally. No network beyond loopback.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from pal import mail  # noqa: E402
from pal.llm import OllamaGateway  # noqa: E402

NOW = datetime(2026, 12, 1, 10, 0)  # a Tuesday


def score(case: dict, query: str | None) -> list[str]:
    """Problems with one answer (empty when it passes)."""
    if not case["search"]:
        return [] if query is None else [f"searched: {query!r}"]
    if query is None:
        return ["did not search"]
    q = query.lower()
    problems = [f"missing {s!r}" for s in case.get("all", []) if s.lower() not in q]
    if case.get("any") and not any(s.lower() in q for s in case["any"]):
        problems.append(f"none of {case['any']}")
    problems += [f"has {s!r}" for s in case.get("none", []) if s.lower() in q]
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default="qwen3.5:4b")
    args = parser.parse_args()
    cases = [json.loads(line) for line in (ROOT / "gmail" / "cases.jsonl").read_text("utf-8").splitlines() if line]
    llm = OllamaGateway("http://127.0.0.1:11434", args.model, 8192, extra_models=(args.model,))
    installed = llm.installed_models()  # raises when Ollama is down: a fallback-only run would score nothing real
    if args.model not in installed:
        sys.exit(f"{args.model} isn't installed in Ollama (have: {', '.join(installed)}). Pass --model.")
    model_failures = 0

    class Counting(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            nonlocal model_failures
            model_failures += 1

    logging.getLogger("pal.mail").addHandler(Counting(logging.WARNING))
    logging.getLogger("pal.mail").propagate = False
    runs = []
    for r in range(args.repeat):
        passed, timings = 0, []
        for case in cases:
            assert mail.asks_about_mail(case["text"]), f"not a mail message per the trigger: {case['text']}"
            start = time.perf_counter()
            query = mail.write_query(llm, case["text"], NOW)
            timings.append(time.perf_counter() - start)
            problems = score(case, query)
            passed += not problems
            if problems and r == 0:
                print(f"FAIL {case['text']!r} -> {query!r}: {'; '.join(problems)}")
        runs.append(passed)
        timings.sort()
        print(f"run {r + 1}: {passed}/{len(cases)} passed, median {timings[len(timings) // 2]:.2f}s per query, "
              f"{model_failures} model failures (keyword fallback) so far")
    if args.repeat > 1:
        print(f"range: {min(runs)}–{max(runs)} of {len(cases)}")


if __name__ == "__main__":
    main()
