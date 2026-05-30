---
name: hello-world
description: Verify GitHub CLI skill installation and agent integration with a lightweight greeting and system report.
---
# Hello World Skill

A lightweight verification skill to test that your agent skill integration and installation works perfectly.

## Instructions

When the user asks you to test the setup or trigger a hello-world greeting:
1. Print:
   ```text
   Hello from the handlename/agent-skills repository!
   ```
2. Inspect the current environment and print a short report:
   - **OS/Platform**: (Determine the OS, e.g., macOS, Linux)
   - **Current Workspace**: (Determine the current working directory)
   - **Active Agent**: (Identify yourself, e.g., Gemini CLI, Claude Code)
3. Offer to run a simple workspace status check if the user wants further verification.
