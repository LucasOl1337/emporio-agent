"""Run a scripted list of customer messages through the real agent and save
the transcript (with the tool calls it made) as Markdown.

Usage: python scripts/record_conversation.py conversations/scripts/01.txt [--today 2026-11-03T10:00]
The script file has a title on line 1 and one customer message per line.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from emporio.cli import build_agent  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", type=Path)
    ap.add_argument("--today")
    args = ap.parse_args()

    lines = [l.strip() for l in args.script.read_text(encoding="utf-8").splitlines() if l.strip()]
    title, turns = lines[0], lines[1:]
    agent = build_agent(ROOT / "data", args.today)
    out = [
        f"# {title}",
        "",
        f"Recorded {datetime.now().strftime('%Y-%m-%d %H:%M')} with model `{agent.model}`; "
        f"store clock set to {agent.tools.now().strftime('%Y-%m-%d %H:%M')} (America/Campo_Grande).",
        "Tool calls are shown as the agent made them; replies are unedited.",
        "",
    ]
    for text in turns:
        seen = len(agent.trace)
        reply = agent.ask(text)
        out += [f"**Cliente:** {text}", ""]
        for t in agent.trace[seen:]:
            out.append(f"> `{t['tool']}({t['args']})`")
        if len(agent.trace) > seen:
            out.append("")
        out += [f"**Mestre:** {reply}", ""]
        print(f"> {text}\n< {reply[:120]}...\n", file=sys.stderr)
    dest = ROOT / "conversations" / (args.script.stem + ".md")
    dest.write_text("\n".join(out), encoding="utf-8")
    print(dest)


if __name__ == "__main__":
    main()
