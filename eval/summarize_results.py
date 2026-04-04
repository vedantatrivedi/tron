from __future__ import annotations

"""Aggregate tron benchmark result files into compact reviewable summaries."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_results(path: Path) -> list[dict]:
    resolved = path if path.is_absolute() else ROOT / path
    return [
        json.loads(line)
        for line in resolved.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def steps_to_first_improvement(row: dict) -> int | None:
    baseline = row.get("initial_service_score", 0.0)
    for step in row.get("steps", []):
        if step.get("service_score", baseline) > baseline:
            return int(step["index"])
    return None


def steps_to_full_recovery(row: dict) -> int | None:
    for step in row.get("steps", []):
        if step.get("service_score", 0.0) >= 1.0:
            return int(step["index"])
    return None


def count_actions(row: dict) -> tuple[int, int]:
    diagnostic = 0
    destructive = 0
    for step in row.get("steps", []):
        if step.get("action_class") == "diagnostic":
            diagnostic += 1
        else:
            destructive += 1
    return diagnostic, destructive


def count_repeated_ineffective_actions(row: dict) -> int:
    repeated = 0
    previous_command = None
    previous_score = row.get("initial_service_score", 0.0)
    for step in row.get("steps", []):
        current_command = step.get("command")
        current_score = step.get("service_score", previous_score)
        if current_command == previous_command and current_score <= previous_score:
            repeated += 1
        previous_command = current_command
        previous_score = current_score
    return repeated


def build_summary(rows: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["agent"]].append(row)

    grouped["overall"] = rows

    for label, items in grouped.items():
        if not items:
            continue
        diagnostic_count = 0
        destructive_count = 0
        repeated_ineffective = 0
        partial_recovery = 0
        full_recovery = 0
        first_improvements: list[int] = []
        full_recoveries: list[int] = []
        verdicts = Counter(item["oracle"]["verdict"] for item in items)

        for item in items:
            diagnostic, destructive = count_actions(item)
            diagnostic_count += diagnostic
            destructive_count += destructive
            repeated_ineffective += count_repeated_ineffective_actions(item)
            if item.get("final_service_score", 0.0) >= 0.7:
                partial_recovery += 1
            if item.get("final_service_score", 0.0) >= 1.0:
                full_recovery += 1

            first_improvement = steps_to_first_improvement(item)
            if first_improvement is not None:
                first_improvements.append(first_improvement)

            full_recovery_step = steps_to_full_recovery(item)
            if full_recovery_step is not None:
                full_recoveries.append(full_recovery_step)

        summary[label] = {
            "runs": len(items),
            "avg_oracle_score": round(
                sum(item["oracle"]["score"] for item in items) / len(items),
                3,
            ),
            "avg_total_reward": round(
                sum(item.get("total_reward", 0.0) for item in items) / len(items),
                3,
            ),
            "avg_steps_to_first_improvement": round(
                sum(first_improvements) / len(first_improvements),
                2,
            )
            if first_improvements
            else None,
            "avg_steps_to_full_recovery": round(
                sum(full_recoveries) / len(full_recoveries),
                2,
            )
            if full_recoveries
            else None,
            "diagnostic_actions": diagnostic_count,
            "destructive_actions": destructive_count,
            "repeated_ineffective_actions": repeated_ineffective,
            "partial_recovery_rate": round(partial_recovery / len(items), 3),
            "full_recovery_rate": round(full_recovery / len(items), 3),
            "verdicts": dict(sorted(verdicts.items())),
        }
    return summary


def print_summary(summary: dict) -> None:
    for label in ["overall", *sorted(key for key in summary.keys() if key != "overall")]:
        if label not in summary:
            continue
        row = summary[label]
        print(
            f"[{label}] runs={row['runs']} oracle={row['avg_oracle_score']:.2f} "
            f"reward={row['avg_total_reward']:.2f} partial={row['partial_recovery_rate']:.2f} "
            f"full={row['full_recovery_rate']:.2f}"
        )
        print(
            f"  first_improvement={row['avg_steps_to_first_improvement']} "
            f"full_recovery={row['avg_steps_to_full_recovery']} "
            f"diagnostic={row['diagnostic_actions']} destructive={row['destructive_actions']} "
            f"repeated_ineffective={row['repeated_ineffective_actions']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize tron eval results.")
    parser.add_argument("results", nargs="?", default="eval/results.jsonl")
    args = parser.parse_args()

    rows = load_results(Path(args.results))
    if not rows:
        print("no results")
        return
    summary = build_summary(rows)
    print_summary(summary)


if __name__ == "__main__":
    main()
