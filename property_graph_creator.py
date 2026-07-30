#!/usr/bin/env python3
"""
property_graph_creator.py

Turns a plain-English requirement into a property graph (nodes + edges + properties)
using a locally running DeepSeek model served by Ollama.

No cloud calls, no API keys — everything talks to http://localhost:11434.

Usage
-----
One-shot:
    python property_graph_creator.py "Make a funny Bollywood-style scooter video, 30 seconds"

Interactive:
    python property_graph_creator.py

Specify a different model / host:
    python property_graph_creator.py --model deepseek-coder:6.7b --host http://localhost:11434 "..."

Requires Ollama running locally with a DeepSeek model pulled, e.g.:
    ollama pull deepseek-coder:6.7b
    ollama serve            # usually already running as a background service
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# --------------------------------------------------------------------------
# Schema definition (derived from the VibeVideo property-graph example)
# --------------------------------------------------------------------------

# Node types we suggest to the model as defaults. The model is free to invent
# more if the requirement calls for it — this is guidance, not a hard enum.
SUGGESTED_NODE_TYPES = [
    "Project", "Scene", "Character", "Clip", "AudioTrack",
    "Subtitle", "VideoFile", "Shot", "Object", "Timeline",
]

# Relation names we suggest, taken directly from the sample document.
SUGGESTED_RELATIONS = [
    "contains", "appears_in", "follows", "needs", "synced",
    "edited", "evaluated_by", "modifies", "creates", "requires",
]

SYSTEM_PROMPT = f"""You are a property-graph generator for a video-creation pipeline (VibeVideo).

Given a user's plain-English requirement for a video, output ONLY a single JSON object
representing a property graph. No prose, no explanation, no markdown code fences —
just the raw JSON object, and nothing else.

JSON SCHEMA (follow exactly):
{{
  "meta": {{
    "goal": "<short description of the video>",
    "style": "<tone/genre, e.g. Bollywood comedy>",
    "duration_sec": <integer or null>
  }},
  "nodes": [
    {{
      "id": "<unique_snake_case_id>",
      "type": "<node type, e.g. Scene, Character, Clip, AudioTrack, Subtitle, VideoFile>",
      "properties": {{ "<key>": "<value>", ... }}
    }}
  ],
  "edges": [
    {{
      "source": "<node id>",
      "target": "<node id>",
      "relation": "<relation name, e.g. follows, contains, appears_in, needs, synced>",
      "properties": {{ "<key>": "<value>", ... }}
    }}
  ]
}}

Rules:
- Every "source" and "target" in edges MUST reference an "id" that exists in nodes.
- Node ids must be unique.
- Prefer these node types when they fit: {", ".join(SUGGESTED_NODE_TYPES)} (invent others only if genuinely needed).
- Prefer these relation names when they fit: {", ".join(SUGGESTED_RELATIONS)} (invent others only if genuinely needed).
- Break the requirement into Scene nodes chained with "follows" edges, in the order they happen.
- Attach Character, Clip, AudioTrack, Subtitle nodes to the Scenes they belong to via appropriate edges.
- Keep properties concrete and short (numbers, single words, short phrases) — not paragraphs.
- Output must be valid JSON parseable by a strict parser. Do not include comments or trailing commas.

EXAMPLE (for a "30 second funny travel reel, Bollywood comedy style, scooter breaks down in the mountains"):
{{
  "meta": {{"goal": "Funny travel reel", "style": "Bollywood comedy", "duration_sec": 30}},
  "nodes": [
    {{"id": "scene_1", "type": "Scene", "properties": {{"duration_sec": 10, "emotion": "excited", "location": "mountain_road"}}}},
    {{"id": "scene_2", "type": "Scene", "properties": {{"duration_sec": 10, "emotion": "funny", "location": "mountain_pass"}}}},
    {{"id": "scene_3", "type": "Scene", "properties": {{"duration_sec": 10, "emotion": "funny", "location": "roadside"}}}},
    {{"id": "char_traveller", "type": "Character", "properties": {{"age": "20s", "clothes": "backpacker outfit"}}}},
    {{"id": "char_goat", "type": "Character", "properties": {{"role": "comic relief mechanic"}}}},
    {{"id": "audio_engine", "type": "AudioTrack", "properties": {{"sound": "scooter engine"}}}},
    {{"id": "audio_beat", "type": "AudioTrack", "properties": {{"sound": "music beat drop"}}}}
  ],
  "edges": [
    {{"source": "scene_1", "target": "scene_2", "relation": "follows", "properties": {{}}}},
    {{"source": "scene_2", "target": "scene_3", "relation": "follows", "properties": {{}}}},
    {{"source": "char_traveller", "target": "scene_1", "relation": "appears_in", "properties": {{}}}},
    {{"source": "char_goat", "target": "scene_3", "relation": "appears_in", "properties": {{}}}},
    {{"source": "scene_1", "target": "audio_engine", "relation": "needs", "properties": {{}}}},
    {{"source": "scene_2", "target": "audio_beat", "relation": "needs", "properties": {{}}}}
  ]
}}
"""

FIX_PROMPT_TEMPLATE = """The following text was supposed to be valid JSON matching the property-graph
schema, but it failed to parse or validate. Error: {error}

Return ONLY the corrected, valid JSON object. No prose, no markdown fences.

Text to fix:
{broken}
"""


# --------------------------------------------------------------------------
# Ollama client (raw urllib — no extra dependencies required)
# --------------------------------------------------------------------------

class OllamaError(RuntimeError):
    pass


def is_ollama_up(host: str, timeout: int = 2) -> bool:
    """Quick check whether Ollama's API is reachable."""
    try:
        urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=timeout)
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


def start_ollama_serve(host: str, wait_seconds: int = 20) -> bool:
    """
    Launch 'ollama serve' as a background process and wait for the API to come up.
    Returns True if it became reachable within wait_seconds, False otherwise.
    No-op (returns True immediately) if Ollama is already up.
    """
    if is_ollama_up(host):
        return True

    print("→ Ollama not reachable, attempting to start 'ollama serve' ...")
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except FileNotFoundError:
        print("✗ Could not find the 'ollama' executable. Is Ollama installed and on your PATH?", file=sys.stderr)
        return False
    except Exception as e:
        print(f"✗ Failed to launch 'ollama serve': {e}", file=sys.stderr)
        return False

    for _ in range(wait_seconds):
        if is_ollama_up(host):
            print("✓ Ollama is up.")
            return True
        time.sleep(1)

    print(f"✗ Ollama did not become reachable within {wait_seconds}s.", file=sys.stderr)
    return False


def get_installed_models(host: str) -> list:
    """Return the list of model names installed in Ollama (from /api/tags)."""
    url = f"{host.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise OllamaError(f"Could not reach Ollama at {url} ({e}).") from e
    return [m["name"] for m in body.get("models", [])]


def choose_model(host: str, requested: str, non_interactive: bool = False) -> str:
    """
    Make sure we end up with a valid, installed model name.
    - If 'requested' is installed, use it as-is.
    - Otherwise, list installed models and let the user pick (interactive),
      or raise an error (non_interactive, e.g. piped/scripted use).
    """
    models = get_installed_models(host)

    if not models:
        raise OllamaError(
            "No models are installed in Ollama. Pull one first, e.g.:\n"
            "  ollama pull deepseek-coder:16b"
        )

    if requested in models:
        return requested

    print(f"! Model '{requested}' not found locally.")
    print("Available Ollama models:")
    for i, name in enumerate(models, start=1):
        print(f"  {i}. {name}")

    if non_interactive:
        raise OllamaError(f"Model '{requested}' not installed. Available: {', '.join(models)}")

    while True:
        choice = input(f"Select a model [1-{len(models)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        print("Invalid choice, try again.")


def call_ollama(host: str, model: str, system_prompt: str, user_prompt: str, timeout: int = 180) -> str:
    """Call Ollama's /api/chat endpoint and return the assistant's text reply."""
    url = f"{host.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise OllamaError(
            f"Could not reach Ollama at {url} ({e}). "
            f"Is 'ollama serve' running and is the model '{model}' pulled?"
        ) from e

    message = body.get("message", {}).get("content", "")
    if not message:
        raise OllamaError(f"Ollama returned no content: {body}")
    return message


# --------------------------------------------------------------------------
# JSON extraction + validation
# --------------------------------------------------------------------------

def extract_json(text: str) -> str:
    """Strip markdown fences / stray prose around a JSON object."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return brace_match.group(0)
    return text


def validate_graph(graph: dict) -> None:
    """Raise ValueError with a clear message if the graph doesn't match the schema."""
    if not isinstance(graph, dict):
        raise ValueError("Top-level JSON must be an object.")

    for key in ("meta", "nodes", "edges"):
        if key not in graph:
            raise ValueError(f"Missing required top-level key: '{key}'")

    if not isinstance(graph["nodes"], list):
        raise ValueError("'nodes' must be a list.")
    if not isinstance(graph["edges"], list):
        raise ValueError("'edges' must be a list.")

    node_ids = set()
    for i, node in enumerate(graph["nodes"]):
        for field in ("id", "type", "properties"):
            if field not in node:
                raise ValueError(f"nodes[{i}] missing '{field}'")
        if node["id"] in node_ids:
            raise ValueError(f"Duplicate node id: '{node['id']}'")
        node_ids.add(node["id"])

    for i, edge in enumerate(graph["edges"]):
        for field in ("source", "target", "relation"):
            if field not in edge:
                raise ValueError(f"edges[{i}] missing '{field}'")
        if edge["source"] not in node_ids:
            raise ValueError(f"edges[{i}] source '{edge['source']}' does not match any node id")
        if edge["target"] not in node_ids:
            raise ValueError(f"edges[{i}] target '{edge['target']}' does not match any node id")


def parse_and_validate(raw_text: str) -> dict:
    candidate = extract_json(raw_text)
    graph = json.loads(candidate)  # may raise json.JSONDecodeError
    validate_graph(graph)          # may raise ValueError
    return graph


# --------------------------------------------------------------------------
# Core generation flow (with one auto-repair retry)
# --------------------------------------------------------------------------

def generate_graph(host: str, model: str, requirement: str, verbose: bool = True) -> dict:
    if verbose:
        print(f"→ Sending requirement to {model} on {host} ...")
    raw = call_ollama(host, model, SYSTEM_PROMPT, requirement)

    try:
        return parse_and_validate(raw)
    except (json.JSONDecodeError, ValueError) as e:
        if verbose:
            print(f"  First attempt didn't validate ({e}). Asking the model to fix it ...")
        fix_prompt = FIX_PROMPT_TEMPLATE.format(error=str(e), broken=raw)
        repaired = call_ollama(host, model, SYSTEM_PROMPT, fix_prompt)
        return parse_and_validate(repaired)  # let this raise if it still fails


# --------------------------------------------------------------------------
# Pretty printing + saving
# --------------------------------------------------------------------------

def print_summary(graph: dict) -> None:
    meta = graph.get("meta", {})
    print("\n=== Project ===")
    print(f"  goal:     {meta.get('goal')}")
    print(f"  style:    {meta.get('style')}")
    print(f"  duration: {meta.get('duration_sec')}s")

    print(f"\n=== Nodes ({len(graph['nodes'])}) ===")
    for n in graph["nodes"]:
        props = ", ".join(f"{k}={v}" for k, v in n.get("properties", {}).items())
        print(f"  [{n['type']}] {n['id']}  ({props})")

    print(f"\n=== Edges ({len(graph['edges'])}) ===")
    for e in graph["edges"]:
        print(f"  {e['source']} --{e['relation']}--> {e['target']}")


def save_graph(graph: dict, out_dir: str = ".") -> Path:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    filename = f"property_graph_{int(time.time())}.json"
    path = Path(out_dir) / filename
    path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a video property graph from an English requirement.")
    parser.add_argument("requirement", nargs="?", help="The video requirement in English. If omitted, runs interactively.")
    parser.add_argument("--model", default=None, help="Ollama model tag (see 'ollama list'). If omitted or not installed, you'll get a picker.")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama server host.")
    parser.add_argument("--out", default="./graphs", help="Directory to save generated graph JSON files.")
    parser.add_argument("--autostart", action="store_true", help="Automatically run 'ollama serve' if it's not already running.")
    parser.add_argument("--list-models", action="store_true", help="List installed Ollama models and exit.")
    args = parser.parse_args()

    if args.autostart:
        if not start_ollama_serve(args.host):
            sys.exit(1)
    elif not is_ollama_up(args.host):
        print(
            f"✗ Ollama isn't reachable at {args.host}. Start it with 'ollama serve', "
            f"or re-run this script with --autostart.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.list_models:
        try:
            models = get_installed_models(args.host)
        except OllamaError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)
        print("Installed Ollama models:")
        for name in models:
            print(f"  - {name}")
        return

    try:
        # Non-interactive only when a specific requirement was given as a CLI arg
        # AND a specific model was requested — otherwise we can safely prompt.
        model = choose_model(args.host, args.model or "", non_interactive=False)
    except OllamaError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
    args.model = model

    def handle_one(requirement: str) -> None:
        try:
            graph = generate_graph(args.host, args.model, requirement)
        except OllamaError as e:
            print(f"✗ {e}", file=sys.stderr)
            return
        except (json.JSONDecodeError, ValueError) as e:
            print(f"✗ Model output still invalid after retry: {e}", file=sys.stderr)
            return

        print_summary(graph)
        path = save_graph(graph, args.out)
        print(f"\n✓ Saved to {path}")

    if args.requirement:
        handle_one(args.requirement)
        return

    print("Property Graph Creator — interactive mode (local DeepSeek via Ollama)")
    print(f"Model: {args.model}   Host: {args.host}")
    print("Type a video requirement in English, or 'quit' to exit.\n")
    while True:
        try:
            requirement = input("requirement> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if requirement.lower() in ("quit", "exit"):
            break
        if not requirement:
            continue
        handle_one(requirement)
        print()


if __name__ == "__main__":
    main()
