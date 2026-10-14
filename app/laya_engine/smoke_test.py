from pprint import pprint

import laya

MODEL_ID = "convaiinnovations/laya"

QUESTIONS = {
    "priority": {
        "type": "choice",
        "instructions": (
            "Classify the urgency and importance of this email "
            "for the recipient. Consider deadlines, business impact, "
            "and whether immediate action is required."
        ),
        "criteria": {
            "P0": "Critical: immediate action is necessary to prevent serious impact.",
            "P1": "High: important and requires prompt attention.",
            "P2": "Medium: relevant, but not immediately urgent.",
            "OTHER": "Other: informational, promotional, or no meaningful action needed.",
        },
    }
}

SAMPLE_EMAIL = {
    "from": "finance@example.com",
    "subject": "Urgent: payment deadline today",
    "body": (
        "Please approve the outstanding payment before 4 PM today. "
        "If it is missed, our service contract may be suspended."
    ),
}


def main():
    print("Loading Laya typed-decisions checkpoint on CPU...")
    agent = laya.load(
        MODEL_ID,
        device="cpu",
        subfolder="typed-decisions",
    )

    print("\nClassifying sample email...")
    result = agent.system_one(SAMPLE_EMAIL, QUESTIONS)

    print("\nRaw Laya result:")
    pprint(result)

    print("\nRouting note:")
    print("This is a model prediction, not an instruction to create a task.")
    print("MailMate's application policy will control any future actions.")


if __name__ == "__main__":
    main()
