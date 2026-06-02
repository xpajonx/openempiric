## Proactive Orchestration (harness-orchestrator)
You have tools to spawn child opencode sessions. Use them proactively.

### When to fork
- **Parallel work**: User asks for multiple changes? Fork each as a child session instead of doing them one-by-one
- **Self-healing**: Detect lint/test/type errors in your own output? Fork a child to fix them while you keep working
- **Research**: Need to check something while doing main work? Fork a research session
- **Refactoring**: Large refactor? Fork it into a child, review the diff when done

### How to use
- `harness_run_opencode(prompt, workdir)` — one-shot child session
- `harness_run_tasks(tasks, workdir)` — multiple independent tasks, results returned together
- `harness_list_projects()` — see available project dirs

### Examples
- `harness_run_opencode(prompt="Fix all TypeScript errors in src/", workdir="/home/user/projects/myapp")`
- `harness_run_tasks(tasks='[{"prompt": "add tests for auth.ts"}, {"prompt": "lint all files"}]', workdir="...")`

### Guidelines
- Always use `--dangerously-skip-permissions` (it's the default in the tool)
- Set reasonable timeouts per task
- Don't fork for trivial things the user can see in real time
- Do fork for anything that would interrupt your flow or take >30s
