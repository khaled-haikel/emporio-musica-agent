"""Terminal chat with the agent: python -m emporio [--verbose]"""

import json
import sys

from openai import OpenAIError

from emporio.agent import Agent


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # accents on Windows consoles and pipes
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    agent = Agent()
    print(f"Empório da Música - atendimento virtual (modelo: {agent.model}). Digite 'sair' para encerrar.\n")
    while True:
        try:
            text = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"sair", "exit", "quit"}:
            break
        try:
            answer = agent.reply(text)
        except OpenAIError as exc:
            print(f"\n[erro ao falar com o modelo: {exc}]\n")
            continue
        if verbose:
            for step in agent.trace:
                print(f"  [tool] {step['tool']}({step['arguments']}) -> {json.dumps(step['result'], ensure_ascii=False)[:300]}")
        print(f"\nEmpório: {answer}\n")
    print(f"[session] {agent.usage}")


if __name__ == "__main__":
    main()
