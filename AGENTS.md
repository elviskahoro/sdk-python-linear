# Agent Instructions

Use GitHub Issues for all project task tracking. Create, update, label, and
close issues in the repository's GitHub project. Do not use Beads or `bd`.

## Non-Interactive Shell Commands

Always use non-interactive flags with file operations to avoid confirmation
prompts. For example, use `cp -f`, `mv -f`, `rm -f`, and `rm -rf` where the
target is explicit and the operation is intended.

For commands that may prompt, use non-interactive options such as
`BatchMode=yes` for SSH/SCP and `-y` for package installation.
