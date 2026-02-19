We need to show people what we have achieved during some period of time.
This should be a high-level summary, easy to understand to non-technical
users. We also want to keep it short.

You are given a list of GitHub pull requests organized in a two-level
grouped structure. The outer keys are groups and the inner keys are
subgroups. PRs may be categorized as 'authored' (user's own work),
'contributed' (commits on someone else's PR), 'reviewed' (someone
else's PR that the user only reviewed), or 'waiting_for_review'.

Use all provided details — descriptions, file paths, and diff sizes — to
understand the substance and scope of each PR.
If a PR description is missing or vague, infer intent from the file paths,
diff stats, and PR title.

For each group, merge similar items into 2-7 concise bullet points. If at
least one of the merged items is not merged yet, assume the whole bullet
point is still in progress.

Do NOT just repeat PR titles — explain the GOALS, MOTIVATIONS, and VALUE
of each piece of work. Why was this PR needed? What problem does it solve?
What value does it deliver to users, developers, or the system?

Focus on authored and contributed PRs as the user's primary work.
Use correct grammar forms to distinguish between things that are done (merged),
and that are in progress.

Never return more than 7 items per subgroup.
You can drop less significant items if you can't fit in the 5 items limit.

Reviewed PRs are NOT the user's own work, skip them when processing data.
