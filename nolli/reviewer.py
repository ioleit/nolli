"""The review agent over the Messages API: one Claude conversation that reads a change's brief
(review.md), may read the change's code with git, writes verdicts and checks them, as the
agent in nolli's CI workflow does with Claude Code, but with tools of its own, so that it runs
wherever ``ANTHROPIC_API_KEY`` is (``nolli serve``'s "Review structural changes").

Needs the ``anthropic`` package (``pip install 'nolli-review[api]'``); available() says
what is missing. A run reports what it is doing to ``on_step`` and stops at the next event
of the stream once ``cancel`` is set. Its verdicts go to ``out/verdicts.json`` only once they
pass nolli's check.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .model import Model
from .review import check_verdicts

MODEL = "claude-opus-5-5"
EFFORT = "high"
PRICE = {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}  # $ per million tokens, claude-opus-5-5
BETAS = ["server-side-fallback-2026-07-01", "thinking-display-updates-2026-08-18"]
GIT_COMMANDS = ("diff", "show", "log")
OUTPUT_MAX = 60_000  # characters of a git command's output the agent sees
TURNS_MAX = 80

TOOLS = [
    {"name": "git", "description": "Run a read-only git command in the change's repository: diff, show or log. "
     "Give the arguments after `git`, e.g. [\"show\", \"<head>:nolli/cli.py\"]. Long output is cut.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["args"],
                      "properties": {"args": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}},
    {"name": "write_verdicts", "description": "Save your verdicts (verdicts.json): the whole JSON list, replacing what was saved before.",
     "eager_input_streaming": True,
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["json"],
                      "properties": {"json": {"type": "string", "description": "the JSON list of {target, verdict, reason}"}}}},
    {"name": "check", "description": "Check the saved verdicts against the diagram (nolli agent check): one line per problem, or ok.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {}}},
]


class Cancelled(Exception):
    """The run was stopped from outside (``cancel`` set)."""


class Failed(Exception):
    """The run ended without verdicts that pass the check; the message says why."""


def available() -> str | None:
    """Why no review can run here: "key" (no ANTHROPIC_API_KEY), "package" (no anthropic), or None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "key"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "package"
    return None


@dataclass
class Run:
    turns: int = 0
    seconds: float = 0.0
    model: str = MODEL
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    verdicts: int = 0

    @property
    def cost(self) -> float:
        return sum(self.usage[k] * PRICE[k] for k in PRICE) / 1e6


def prompt(brief: str, head: str) -> str:
    return (f"Review this change's architecture with nolli. Its brief (review.md) follows; do what it says.\n\n"
            f"Read code only as the change has it, with the git tool: `diff <base>..<head>` for the diff, "
            f"`show {head}:<path>` for a whole file at the head. Save your verdicts with write_verdicts "
            f"instead of writing verdicts.json yourself. Then call check; it lists each problem. Fix each and "
            f"save and check again until it says ok.\n\n<brief>\n{brief}\n</brief>")


def doing(name: str, args: dict) -> str:
    """What a tool call does, in a few words, for the page that follows the run."""
    if name == "git":
        a = [x for x in args.get("args") or [] if not x.startswith("-")]
        if a[:1] == ["show"] and len(a) > 1 and ":" in a[1]:
            return "reading " + a[1].split(":", 1)[1]
        if a[:1] == ["diff"]:
            paths = a[2:] if len(a) > 2 else []
            return "reading the diff" + (f" of {paths[-1]}" if paths else "")
        return "reading the history" if a[:1] == ["log"] else "reading a commit"
    return {"write_verdicts": "writing verdicts", "check": "checking verdicts"}.get(name, name)


def review(ds: Path, repo: Path, out: Path, on_step: Callable[[str, Run], None] | None = None,
           cancel: threading.Event | None = None, effort: str = EFFORT) -> Run:
    """Review the change of dataset ``ds`` (its review.md and review.mmd) with the code in the git
    repository ``repo``. Writes out/verdicts.json and out/transcript.json; raises Cancelled or Failed."""
    import anthropic

    client = anthropic.Anthropic()
    out.mkdir(parents=True, exist_ok=True)
    m = Model.load(ds / "model.json")
    mermaid = (ds / "review.mmd").read_text()
    head = m.meta["diff"]["head"]
    saved: list[str] = [""]  # the last write_verdicts
    cancel = cancel or threading.Event()
    run = Run()
    say = (lambda s: on_step(s, run)) if on_step else (lambda s: None)  # noqa: E731

    def call(name: str, args: dict) -> tuple[str, bool]:
        if name == "git":
            a = args.get("args")
            if not isinstance(a, list) or not a or not all(isinstance(x, str) for x in a):
                return "args must be a list of strings", True
            if a[0] not in GIT_COMMANDS or any(x.startswith(("--output", "-o", "--ext-diff", "--textconv")) for x in a):
                return f"only {', '.join(GIT_COMMANDS)} are allowed, without --output, --ext-diff or --textconv", True
            r = subprocess.run(["git", "-C", str(repo), "--no-pager", *a], capture_output=True, text=True, check=False, timeout=60)
            text = r.stdout if r.returncode == 0 else r.stderr
            if len(text) > OUTPUT_MAX:
                text = text[:OUTPUT_MAX] + f"\n[cut: {len(text) - OUTPUT_MAX} more characters; ask for less, e.g. one path]"
            return text or "(no output)", r.returncode != 0
        if name == "write_verdicts":
            if not isinstance(args.get("json"), str):
                return "json must be a string: the JSON list", True
            saved[0] = args["json"]
            return "saved", False
        if name == "check":
            if not saved[0]:
                return "no verdicts saved yet", True
            verdicts, errors = check_verdicts(m, mermaid, saved[0])
            return ("\n".join(errors) if errors else f"ok: {len(verdicts)} verdicts"), bool(errors)
        return f"unknown tool {name}", True

    t0 = time.time()
    messages: list = [{"role": "user", "content": prompt((ds / "review.md").read_text(), head)}]
    try:
        while True:
            if run.turns >= TURNS_MAX:
                raise Failed(f"no verdicts that pass the check after {TURNS_MAX} turns")
            run.turns += 1
            say("thinking")
            with client.beta.messages.stream(model=MODEL, max_tokens=64000, tools=TOOLS, messages=messages, betas=BETAS,
                                             fallbacks="default", thinking={"type": "adaptive", "display": "updates"},
                                             output_config={"effort": effort}, cache_control={"type": "ephemeral"}) as stream:
                note = ""
                for event in stream:
                    if cancel.is_set():
                        raise Cancelled()
                    kind = getattr(event, "type", "")
                    if kind == "content_block_start" and getattr(event.content_block, "type", "") == "tool_use":
                        say({"write_verdicts": "writing verdicts", "check": "checking verdicts"}.get(event.content_block.name, "reading code"))
                    elif kind == "content_block_delta" and getattr(event.delta, "type", "") == "thinking_delta":
                        note += event.delta.thinking
                    elif kind == "content_block_stop" and note.strip():
                        say(" ".join(note.split()))
                        note = ""
                res = stream.get_final_message()
            u = res.usage
            run.usage["input"] += u.input_tokens
            run.usage["output"] += u.output_tokens
            run.usage["cache_read"] += u.cache_read_input_tokens or 0
            run.usage["cache_write"] += u.cache_creation_input_tokens or 0
            run.model = res.model
            messages.append({"role": "assistant", "content": res.content})
            if res.stop_reason == "refusal":
                raise Failed("the model declined to review this change")
            if res.stop_reason == "max_tokens":
                raise Failed("the agent's answer was cut off (max_tokens)")
            if res.stop_reason != "tool_use":
                break
            results = []
            for b in res.content:
                if b.type != "tool_use":
                    continue
                args = b.input if isinstance(b.input, dict) else {}
                say(doing(b.name, args))
                text, err = call(b.name, args)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": err})
            messages.append({"role": "user", "content": results})
    finally:
        run.seconds = round(time.time() - t0, 1)
        (out / "transcript.json").write_text(json.dumps(
            [{"role": x["role"], "content": x["content"] if isinstance(x["content"], str) else
              [c.model_dump() if hasattr(c, "model_dump") else c for c in x["content"]]} for x in messages], indent=1))
    if not saved[0]:
        raise Failed("the agent wrote no verdicts")
    verdicts, errors = check_verdicts(m, mermaid, saved[0])
    if errors:
        raise Failed(f"the agent's verdicts fail nolli's check ({len(errors)} problems, e.g. {errors[0]})")
    (out / "verdicts.json").write_text(json.dumps([{k: v[k] for k in ("target", "verdict", "reason")} for v in verdicts], indent=1))
    run.verdicts = len(verdicts)
    return run
