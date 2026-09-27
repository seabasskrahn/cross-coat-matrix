"""Offline demo: run `python demo.py` (add --auto to answer 'yes' automatically)."""
import sys

from matrix import config, runner
from matrix.graph import build_graph

MESSAGES = [
    "Prep tomorrow's tailgate briefing for the crew at the Peters basement job, 7:30 am start",
    "Send the Friesen invoice reminder, they still owe for the garage board and tape",
]


def show(result: dict):
    for line in result["handoff_log"]:
        print("   ", line)
    print("   STATUS:", result["status"])
    if result.get("reply"):
        print("   REPLY:", result["reply"])


def main():
    auto = "--auto" in sys.argv
    print(f"Stuart demo (LLM_PROVIDER={config.LLM_PROVIDER})\n")
    graph = build_graph()
    for text in MESSAGES:
        print(f"> MESSAGE: {text}")
        result = runner.start(graph, text, source="demo")
        show(result)
        if result["status"] == "needs_approval":
            print(f"   APPROVAL NEEDED: {result['question']}")
            answer = "yes" if auto else input("   Approve? (yes/no): ")
            result = runner.resume(graph, result["thread_id"], answer.strip().lower().startswith("y"))
            show(result)
        print()


if __name__ == "__main__":
    main()
