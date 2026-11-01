"""Terminal chat. Usage: python -m emporio [--trace] [--session NAME] [--today YYYY-MM-DD]"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .agent import Agent
from .policies import PolicyIndex, load_sections
from .store import Store
from .tools import STORE_TZ, Toolbox

ROOT = Path(__file__).resolve().parent.parent


def build_agent(data_dir: Path, today: str | None = None) -> Agent:
    store = Store.load(data_dir)
    policies = PolicyIndex(load_sections(data_dir / "políticas_da_loja.pdf"))
    now = None
    if today:
        now = datetime.fromisoformat(today).replace(tzinfo=STORE_TZ)
        if now.hour == 0 and "T" not in today:
            now = now.replace(hour=10)
    return Agent(Toolbox(store, policies, now=now))


def main() -> None:
    ap = argparse.ArgumentParser(description="Empório da Música customer service agent")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--trace", action="store_true", help="print tool calls")
    ap.add_argument("--session", help="persist history to sessions/NAME.json and resume it")
    ap.add_argument("--today", help="pretend the current date is YYYY-MM-DD[THH:MM] (demos)")
    args = ap.parse_args()

    agent = build_agent(args.data, args.today)
    path = ROOT / "sessions" / f"{args.session}.json" if args.session else None
    if path and path.exists():
        agent.messages = json.loads(path.read_text(encoding="utf-8"))
        print(f"(resumed {len(agent.messages) - 1} messages)")

    print("Empório da Música — digite sua mensagem (Ctrl+D para sair)\n")
    while True:
        try:
            text = input("você> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        seen = len(agent.trace)
        reply = agent.ask(text)
        if args.trace:
            for t in agent.trace[seen:]:
                print(f"  [tool] {t['tool']}({t['args']})")
        print(f"mestre> {reply}\n")
        if path:
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(agent.messages, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
