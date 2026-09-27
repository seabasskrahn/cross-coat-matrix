"""Talk to Stuart live. Run: python chat.py   (type 'quit' to stop)
Set SHOW_ROUTING=1 in .env to also see the behind-the-scenes routing lines."""
import os
import re
from matrix import agents, runner
from matrix.graph import build_graph

NAME = "Stuart"
# Internal staff names are hidden from what you see on screen.
_INTERNAL = sorted(set(agents.SENIOR_STAFF) | set(agents.SPECIALISTS) | {"SUNDAY", "MATRIX", "Matrix"},
                   key=len, reverse=True)
_PATTERN = re.compile(r"\[?\b(" + "|".join(map(re.escape, _INTERNAL)) + r")\b( mock)?\]?")


def clean(text: str) -> str:
    """Swap any internal staff name for Stuart so only one speaker shows."""
    out = _PATTERN.sub(NAME, str(text))
    return re.sub(rf"\b(I'?m|I am) {NAME}[.,]?\s*", "", out).strip()


def show(result: dict):
    if os.getenv("SHOW_ROUTING") == "1":
        for line in result["handoff_log"]:
            print("   ", line)
    if result.get("reply"):
        print(f"\n{NAME}:", clean(result["reply"]), "\n")
    else:
        print(f"\n{NAME}: ({clean(result['status'])})\n")


def main():
    print(f"{NAME} is here. Type 'quit' to stop.\n")
    graph = build_graph()
    while True:
        try:
            text = input("YOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit", "bye"):
            break
        try:
            result = runner.start(graph, text, source="terminal")
            show(result)
            if result["status"] == "needs_approval":
                question = clean(result["question"]).removeprefix("Approve?").strip()
                print(f"{NAME}: I need your yes first. {question}")
                answer = input("Approve? (yes/no): ").strip().lower()
                result = runner.resume(graph, result["thread_id"], answer.startswith("y"))
                show(result)
        except Exception as err:  # keep chatting even if one message fails
            print(f"\n{NAME}: That one failed ({clean(err)}).\n")
    print(f"{NAME}: Goodbye, sir.")


if __name__ == "__main__":
    main()