---
inclusion: manual
---

# Post-Doc (pd) — Document Chat Answers to cdocs

When the user types "pd", "pdoc", or "postdoc", document the most recent useful answer from this conversation into a new markdown file in the `cdocs/` directory.

## Rules

1. Create a new `.md` file in `cdocs/` with a short descriptive kebab-case filename based on the topic (e.g., `cdocs/airllm-slow-inference-explained.md`).
2. The file should contain:
   - A clear title as an H1 heading
   - The date (from current context)
   - A concise, well-formatted version of the answer (not a raw copy-paste of the chat)
   - Code blocks where relevant
   - Keep it practical and reference-worthy
3. Do NOT duplicate an existing doc — check `cdocs/` first.
4. Confirm the filename after creating it.
