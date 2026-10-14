import json
from collections import Counter, defaultdict
from pathlib import Path

import laya


ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "tests/fixtures/priority_emails.json"
REPORT_PATH = ROOT / "tests/fixtures/laya_evaluation_results.json"

MODEL_NAME = "convaiinnovations/laya"
MODEL_SUBFOLDER = "typed-decisions"


# Stage 1: Does this email require an action?
ACTION_QUESTIONS = {
    "action_required": {
        "type": "choice",
        "instructions": (
            "Decide if the recipient must do something. "
            "Answer YES only if the email asks the recipient to "
            "complete a task, reply, make a decision, fix something, "
            "or pay. Answer NO if the email only shares information, "
            "news, updates, advertisements, or meeting changes. "
            "A date, urgent wording, or meeting time alone does not "
            "mean a task is required. Do not invent tasks."
        ),
        "criteria": {
            "YES": (
                "The email asks the recipient to complete a task, "
                "reply, make a decision, fix something, or pay."
            ),
            "NO": (
                "The email only shares information, news, updates, "
                "advertisements, or meeting changes without requesting "
                "an action."
            ),
        },
    }
}


# Stage 2: What is the priority?
PRIORITY_QUESTIONS = {
    "priority": {
        "type": "choice",
        "instructions": (
            "Classify the priority of an email that requires action. "
            "P0 is ONLY a critical incident or immediate serious risk "
            "requiring intervention now, such as a confirmed production "
            "outage affecting customers or an active security breach. "
            "P1 is important work with a meaningful deadline or business "
            "impact, without an established P0-level crisis. P2 is routine "
            "work that can reasonably wait. An overdue payment, failed "
            "backup, bug report, expiring access, or deadline is not "
            "automatically P0. Do not infer urgency or criticality without "
            "supporting context. Choose the best-supported category."
        ),
        "criteria": {
            "P0": (
                "Critical incident or immediate serious risk requiring "
                "intervention now."
            ),
            "P1": (
                "Important actionable work with a meaningful deadline "
                "or business impact, but no established critical incident."
            ),
            "P2": "Routine actionable work that can reasonably wait.",
        },
    }
}

VALID_PRIORITIES = {"P0", "P1", "P2"}
VALID_ACTIONABILITY = {"YES", "NO"}


def load_dataset():
    """Load and validate the demo/evaluation dataset."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATASET_PATH}"
        )

    with DATASET_PATH.open("r", encoding="utf-8") as file:
        dataset = json.load(file)

    emails = dataset.get("emails")
    if not isinstance(emails, list) or not emails:
        raise ValueError(
            "Dataset must contain a non-empty 'emails' list."
        )

    return dataset, emails


def get_choice(result, answer_key):
    """Extract a normalized choice and its probabilities from Laya."""
    answer = result["answers"][answer_key]
    choice = str(answer["choice"]).strip().upper()
    probabilities = answer.get("probabilities", {})

    normalized_probabilities = {}
    if isinstance(probabilities, dict):
        for label, probability in probabilities.items():
            try:
                normalized_probabilities[str(label).upper()] = float(
                    probability
                )
            except (TypeError, ValueError):
                continue

    return choice, normalized_probabilities


def classify_email(agent, email):
    """Run actionability first, then priority only when action is required."""
    state = {
        "subject": email.get("subject", ""),
        "body": email.get("body", ""),
    }

    action_result = agent.system_one(state, ACTION_QUESTIONS)
    actionability, action_probabilities = get_choice(
        action_result, "action_required"
    )

    priority = "OTHER"
    priority_probabilities = {}

    if actionability == "YES":
        priority_result = agent.system_one(state, PRIORITY_QUESTIONS)
        priority, priority_probabilities = get_choice(
            priority_result, "priority"
        )

        if priority not in VALID_PRIORITIES:
            priority = "REVIEW"
    elif actionability != "NO":
        actionability = "REVIEW"
        priority = "REVIEW"

    return {
        "predicted_actionability": actionability,
        "predicted_priority": priority,
        "final_prediction": (
            priority if actionability == "YES" else
            "OTHER" if actionability == "NO" else
            "REVIEW"
        ),
        "action_probabilities": action_probabilities,
        "priority_probabilities": priority_probabilities,
    }


def format_probabilities(probabilities):
    """Format probabilities for readable terminal output."""
    if not probabilities:
        return "  (not available)"

    return "\n".join(
        f"  {label}: {value:.4f}"
        for label, value in probabilities.items()
    )


def expected_final_prediction(email):
    """Return the expected final label when the example is scoreable."""
    actionability = str(
        email.get("expected_actionability", "")
    ).upper()
    priority = str(email.get("expected_priority", "")).upper()

    if actionability == "YES" and priority in VALID_PRIORITIES:
        return priority

    if actionability == "NO":
        return "OTHER"

    return None


def evaluate(agent, emails):
    """Evaluate predictions against the dataset's expected labels."""
    results = []
    actionability_total = 0
    actionability_correct = 0
    priority_total = 0
    priority_correct = 0
    overall_total = 0
    overall_correct = 0

    confusion = Counter()
    category_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    review_cases = []

    for email in emails:
        prediction = classify_email(agent, email)

        expected_actionability = str(
            email.get("expected_actionability", "")
        ).upper()
        expected_priority = str(
            email.get("expected_priority", "")
        ).upper()
        expected_final = expected_final_prediction(email)

        predicted_actionability = prediction["predicted_actionability"]
        predicted_priority = prediction["predicted_priority"]
        predicted_final = prediction["final_prediction"]

        # Actionability metric: skip REVIEW/unknown expected labels.
        actionability_match = None
        if expected_actionability in VALID_ACTIONABILITY:
            actionability_total += 1
            actionability_match = (
                predicted_actionability == expected_actionability
            )
            actionability_correct += int(actionability_match)

        # Priority metric: score only emails expected to require action.
        priority_match = None
        if (
            expected_actionability == "YES"
            and expected_priority in VALID_PRIORITIES
        ):
            priority_total += 1
            priority_match = predicted_priority == expected_priority
            priority_correct += int(priority_match)

        # Overall metric: ambiguous expected examples are excluded.
        overall_match = None
        if expected_final is not None:
            overall_total += 1
            overall_match = predicted_final == expected_final
            overall_correct += int(overall_match)
            confusion[(expected_final, predicted_final)] += 1

            category = email.get("category", "unknown")
            category_stats[category]["total"] += 1
            category_stats[category]["correct"] += int(overall_match)

        result = {
            "id": email.get("id", "unknown"),
            "subject": email.get("subject", ""),
            "category": email.get("category", "unknown"),
            "expected_actionability": expected_actionability,
            "predicted_actionability": predicted_actionability,
            "expected_priority": expected_priority,
            "predicted_priority": predicted_priority,
            "expected_final_prediction": expected_final,
            "final_prediction": predicted_final,
            "actionability_match": actionability_match,
            "priority_match": priority_match,
            "overall_match": overall_match,
            "action_probabilities": prediction["action_probabilities"],
            "priority_probabilities": prediction["priority_probabilities"],
            "split": email.get("split", "unspecified"),
            "label_status": email.get(
                "label_status",
                "provisional_reconstructed"
            ),
        }
        results.append(result)

        # Keep mismatches and expected REVIEW cases visible.
        if (
            overall_match is False
            or expected_actionability == "REVIEW"
            or expected_priority == "REVIEW"
            or predicted_actionability == "REVIEW"
            or predicted_priority == "REVIEW"
        ):
            review_cases.append(result)

        print(f"\n--- {result['id']} ---")
        print(f"Category: {result['category']}")
        print(f"Expected actionability: {expected_actionability}")
        print(f"Predicted actionability: {predicted_actionability}")
        print(f"Expected priority: {expected_priority}")
        print(f"Predicted priority: {predicted_priority}")
        print(f"Final prediction: {predicted_final}")

        if actionability_match is not None:
            print(f"Actionability match: {actionability_match}")
        if overall_match is not None:
            print(f"Overall match: {overall_match}")
        else:
            print("Overall match: NOT SCORED (ambiguous expected label)")

        print("Action probabilities:")
        print(format_probabilities(prediction["action_probabilities"]))
        if prediction["priority_probabilities"]:
            print("Priority probabilities:")
            print(
                format_probabilities(
                    prediction["priority_probabilities"]
                )
            )

    report = {
        "model": MODEL_NAME,
        "subfolder": MODEL_SUBFOLDER,
        "dataset_name": None,
        "dataset_label_status": None,
        "metrics": {
            "overall": {
                "correct": overall_correct,
                "total": overall_total,
                "accuracy": (
                    overall_correct / overall_total
                    if overall_total else None
                ),
            },
            "actionability": {
                "correct": actionability_correct,
                "total": actionability_total,
                "accuracy": (
                    actionability_correct / actionability_total
                    if actionability_total else None
                ),
            },
            "priority": {
                "correct": priority_correct,
                "total": priority_total,
                "accuracy": (
                    priority_correct / priority_total
                    if priority_total else None
                ),
            },
        },
        "category_accuracy": {},
        "confusion_counts": [
            {
                "expected": expected,
                "predicted": predicted,
                "count": count,
            }
            for (expected, predicted), count in sorted(confusion.items())
        ],
        "review_cases": review_cases,
        "results": results,
        "safety_note": (
            "Evaluation only. Predictions do not trigger tasks, "
            "notifications, or calendar events. Scores depend on the "
            "quality and size of the supplied dataset."
        ),
    }

    for category, stats in sorted(category_stats.items()):
        report["category_accuracy"][category] = {
            "correct": stats["correct"],
            "total": stats["total"],
            "accuracy": (
                stats["correct"] / stats["total"]
                if stats["total"] else None
            ),
        }

    return report


def percent(correct, total):
    if not total:
        return "N/A"
    return f"{correct}/{total} ({correct / total * 100:.1f}%)"


def print_summary(report):
    """Print aggregate metrics and cases that need review."""
    metrics = report["metrics"]

    print("\n========== EVALUATION SUMMARY ==========")
    overall = metrics["overall"]
    actionability = metrics["actionability"]
    priority = metrics["priority"]

    print(
        "Overall classification: "
        f"{percent(overall['correct'], overall['total'])}"
    )
    print(
        "Actionability: "
        f"{percent(actionability['correct'], actionability['total'])}"
    )
    print(
        "Priority: "
        f"{percent(priority['correct'], priority['total'])}"
    )

    print("\nCategory-level overall accuracy:")
    for category, stats in report["category_accuracy"].items():
        print(
            f"  {category}: "
            f"{percent(stats['correct'], stats['total'])}"
        )

    print("\nConfusion counts (expected -> predicted):")
    if not report["confusion_counts"]:
        print("  No scoreable examples.")
    else:
        for item in report["confusion_counts"]:
            print(
                f"  {item['expected']} -> {item['predicted']}: "
                f"{item['count']}"
            )

    print(f"\nCases needing review: {len(report['review_cases'])}")
    for case in report["review_cases"]:
        print(
            f"  {case['id']}: "
            f"expected_action={case['expected_actionability']}, "
            f"predicted_action={case['predicted_actionability']}, "
            f"expected_priority={case['expected_priority']}, "
            f"predicted_priority={case['predicted_priority']}"
        )


def main():
    dataset, emails = load_dataset()

    print(f"Loading model: {MODEL_NAME}")
    agent = laya.load(
        MODEL_NAME,
        device="cpu",
        subfolder=MODEL_SUBFOLDER,
    )

    report = evaluate(agent, emails)
    report["dataset_name"] = dataset.get("dataset_name")
    report["dataset_label_status"] = dataset.get("label_status")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print_summary(report)
    print(f"\nDetailed JSON report saved to: {REPORT_PATH}")
    print(
        "\nEvaluation only: predictions do not trigger tasks, "
        "notifications, or calendar events."
    )
    print(
        "If the dataset uses reconstructed or provisional labels, "
        "do not interpret these scores as production accuracy."
    )


if __name__ == "__main__":
    main()
