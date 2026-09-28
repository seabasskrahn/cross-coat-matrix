"""Talk to your assistant (default name: Stuart) live. Run: python chat.py   (type 'quit' to stop)
Set SHOW_ROUTING=1 in .env to also see the behind-the-scenes routing lines.
Change the assistant's name (and the agents' names) with: python rename_panel.py"""
import os
import re
from matrix import agents, config, envelope, names, runner
from matrix.graph import build_graph

# Internal staff names are hidden from what you see on screen.
_INTERNAL_EXTRA = {"SUNDAY", "MATRIX", "Matrix"}


def assistant() -> str:
    """The assistant's current display name (set in the rename panel; default Stuart)."""
    return names.assistant_name()


def _pattern() -> re.Pattern:
    internal = sorted(set(agents.SENIOR_STAFF) | set(agents.SPECIALISTS) | _INTERNAL_EXTRA, key=len, reverse=True)
    return re.compile(r"\[?\b(" + "|".join(map(re.escape, internal)) + r")\b( mock)?\]?")


def clean(text: str) -> str:
    """Swap any internal staff name for the assistant's name so only one speaker shows."""
    name = assistant()
    out = _pattern().sub(lambda _m: name, str(text))
    return re.sub(rf"\b(I'?m|I am) {re.escape(name)}[.,]?\s*", "", out).strip()


def show(result: dict):
    name = assistant()
    if os.getenv("SHOW_ROUTING") == "1":
        for entry in result.get("step_log", []):
            print("   ", names.pretty(envelope.describe(entry, config.TIMEZONE)))
    if result.get("reply"):
        print(f"\n{name}:", clean(result["reply"]), "\n")
    else:
        print(f"\n{name}: ({clean(result['status'])})\n")


def main():
    print(f"{assistant()} is here. Type 'quit' to stop.\n")
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
                print(f"{assistant()}: I need your yes first. {question}")
                answer = input("Approve? (yes/no): ").strip().lower()
                result = runner.resume(graph, result["thread_id"], answer.startswith("y"))
                show(result)
        except Exception as err:  # keep chatting even if one message fails
            print(f"\n{assistant()}: That one failed ({clean(err)}).\n")
    print(f"{assistant()}: Goodbye, sir.")


if __name__ == "__main__":
    main()
