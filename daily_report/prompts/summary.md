You are given a list of GitHub pull requests grouped by repository,
including PR descriptions, changed files, and diff stats.
PRs are categorized as 'authored' (user's own work), 'contributed'
(commits on someone else's PR), 'reviewed' (someone else's PR that
the user only reviewed), or 'waiting_for_review'.

Use all provided details to understand the substance of each PR.
If a PR description or changed-files list is missing or unclear, use the
repo name, PR title, and file paths to infer what the change does.

Write a single-sentence summary focusing on what the user AUTHORED or
CONTRIBUTED TO as their primary work. You can skip items where
contribution was very small.

Focus on the high-level goals, motivations, and value delivered — not what
was changed, but WHY it matters and what problems were solved.

Reviewed PRs are NOT the user's work — only mention them briefly
if at all (e.g. 'also reviewed N PRs').
