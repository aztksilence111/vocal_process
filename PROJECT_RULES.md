# Project Rules

## Standing Rules

1. Conversation and implementation notes must be kept in `CONVERSATION_LOG.md`.
2. Project direction, architecture decisions, risks, and verification results must be kept in `PROJECT_ANALYSIS.md`.
3. User-facing conversation should be in Chinese unless the user requests otherwise.
4. Before a new feature/change round starts, work must be isolated on a Git branch.
5. After major verified changes, automatically commit and push the corresponding repository updates to GitHub unless the user explicitly asks to keep the work local.
6. Every time `main` is updated, `README.md` must include an update log directly after the download instructions. This update log is the second-priority README section and should summarize what changed, release assets, and manual-test impact.
7. Code changes must prioritize the long-term project architecture, maintainability, and manual-test reliability over the smallest immediately shippable patch. Each implementation round should fit the durable roadmap, reduce future rework, and avoid creating avoidable manual-test failures.

## Branch Promotion Rule

This project does not use long-lived feature branches as the final delivery state. When a new work branch is completed, verified, and uploaded, promotion to `main` is a mandatory closeout step, not an optional follow-up. A task is not fully finished until this rule has either been completed or explicitly blocked in the final report.

1. Back up the current `main` branch to a separate archive/backup branch before promotion.
2. Promote the completed work branch to replace `main`.
3. Keep the completed work branch name available as the backup label for that main state when the next main replacement happens.
4. Push the backup branch, the promoted `main`, and the work branch to the remote repository.
5. If replacing `main` cannot be done as a fast-forward, stop and make the required force-push risk explicit before changing the remote.
6. Before any final response after a completed feature branch, run or report the equivalent of `git status --short --branch`, confirm the backup branch name, and confirm whether `origin/main` now points at the completed work.

Recommended backup branch name:

```text
archive/main-before-<content-summary>
```

Use a content summary such as `python311-uvr-vst3-runtime`, not a date-driven name.
Dates may be appended only when they disambiguate two backups with the same content summary.
