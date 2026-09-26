# ADR 0003: provider interface, and why the default backend is the local CLI

Status: proposed, pending Danilo's review. **Provenance**: written by Claude
Code; see the note in ADR 0001.

## Context

The brief requires the agent's LLM calls to go through a swappable provider
interface: the default is the local Claude Code CLI on the owner's own
account, but anyone else running this repo should be able to use
`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` instead, with no secret ever
hardcoded, printed, or committed.

## Decision

`procurement_agent/providers/base.py` defines one abstract method,
`complete(prompt, system=None, json_schema=None) -> LLMResponse`. Four
implementations:

- `claude_cli.ClaudeCLIProvider` — shells out to `claude --safe-mode --model
  <m> --effort <e> -p --output-format json [--json-schema ...]`. This is the
  default and the backend behind every number in EVALS.md.
- `anthropic_api.AnthropicAPIProvider` / `openai_api.OpenAIAPIProvider` —
  lazy-import their SDKs, read the key from the environment only, and raise
  a clear error if the key is missing. Neither searches the filesystem for a
  key.
- `cassette.CassetteProvider` — replays recorded responses from a JSONL file
  keyed by a hash of `(system, prompt)`; a cache miss is a hard `KeyError`,
  not a silent fallback, so a stale cassette fails loudly instead of quietly
  grading against last month's prompt.

## Why `--safe-mode` on the CLI backend

Without it, the CLI auto-discovers this machine's `CLAUDE.md` files (there
are two multi-hundred-line ones on this box) and injects them as context on
every single-turn extraction call — measured at ~$0.15–0.17/call on a
20k-token cache-creation hit versus ~$0.01/call with `--safe-mode`, an
order of magnitude difference for identical output. `--safe-mode` also
disables hooks, skills, and MCP servers, so the extraction call is exactly
"one prompt in, one JSON blob out" — no tool access, no side effects,
regardless of what an injected email says.

## Why cost is reported as $0 for the two direct-API backends

Anthropic's and OpenAI's Messages/Chat Completions APIs don't return a
dollar cost in the response the way the Claude Code CLI's `--output-format
json` does (it returns token counts, which I could multiply by a hardcoded
price table — but list prices change, and a stale hardcoded table would
silently under- or over-report cost forever). I chose to report `0.0` and
say so in the code and here, rather than ship a number that looks precise
and quietly goes wrong. `EVALS.md`'s cost figures are all from the CLI
backend, which is the one actually used for every measured run in this repo.

## Consequence

Switching backends is a one-line change (`get_provider("anthropic", model=...)`
instead of `get_provider("claude-cli", model=...)`), and CI runs the same
code path against `cassette` with zero network access.
