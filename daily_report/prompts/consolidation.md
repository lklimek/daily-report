You are given a list of GitHub pull requests organized in a two-level
grouped structure. The outer keys are groups and the inner keys are
subgroups. PRs may be categorized as 'authored' (user's own work),
'contributed' (commits on someone else's PR), 'reviewed' (someone
else's PR that the user only reviewed), or 'waiting_for_review'.

Use all provided details — descriptions, file paths, and diff sizes — to
understand the substance and scope of each PR.
If a PR description is missing or vague, infer intent from the file paths,
diff stats, and PR title.

For each subgroup, summarize the work into 2-5 concise bullet points.
Do NOT just repeat PR titles — explain the GOALS, MOTIVATIONS, and VALUE
of each piece of work. Why was this PR needed? What problem does it solve?
What value does it deliver to users, developers, or the system?

Focus on authored and contributed PRs as the user's primary work.
Use correct grammar forms to distinguish between things that are done (merged),
and that are in progress.

Reviewed PRs are NOT the user's own work — summarize them separately if included.
