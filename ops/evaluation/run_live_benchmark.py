"""CLI entrypoint for repeated OpenAI-compatible LLM benchmark runs."""

from devops_agent_platform.evaluation.live_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
