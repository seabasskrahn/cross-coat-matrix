"""Talk to the Cross Coat Matrix live. Run: python chat.py   (type 'quit' to stop)"""
from matrix import config, runner
from matrix.graph import build_graph


def show(result: dict):
    for line in result["handoff_log"]:
        print("   ", line)
    if result.get("reply"):
        print("\nMATRIX:", result["reply"], "\n")
    else:
        print("   STATUS:", result["status"], "\n")


def main():
    print(f"Cross Coat Matrix live chat (brain: {config.LLM_PROVIDER}). Type 'quit' to stop.\n")
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
                print(f"APPROVAL NEEDED: {result['question']}")
                answer = input("Approve? (yes/no): ").strip().lower()
                result = runner.resume(graph, result["thread_id"], answer.startswith("y"))
                show(result)
        except Exception as err:  # keep chatting even if one message fails
            print(f"   (That message failed: {err})\n")
    print("Goodbye, sir.")


if __name__ == "__main__":
    main()
