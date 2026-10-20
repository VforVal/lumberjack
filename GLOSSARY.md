# Glossary

**integrated**: the branch tip is already part of the primary branch's history, or merging
it would change nothing there. Safe to remove.

**landed**: proven to be in the primary branch, either integrated or through a merged
pull request with this exact tip. Only landed worktrees are removed automatically.

**probable**: looks landed because its changes match recent primary history, but nothing
proves it. Reported; removed only by confirmed removal.

**unverified**: no evidence the work reached the primary branch. Counted; removed only by
confirmed removal.

**dependent**: a branch built on top of a landed branch. It needs a rebase onto the
primary branch before it is pushed, or its pull request carries the squashed commits.

**fresh**: a worktree added less than 24 hours ago whose branch has not moved since it was
created (its reflog has only the creation entry, or none). Kept, even though it looks
integrated; after 24 hours the usual rules apply.

**locked**: a worktree locked with `git worktree lock`. Never removed by lumberjack.

**doc root**: a folder named in `doc_roots` that holds work documents (handoffs, plans,
research, run output). A landed worktree with an untracked or ignored file under one stays,
reason `docs`; documents go in a folder named for the ticket or branch slug.

**bundle**: the outermost untracked file or folder under a doc root. Its tickets come from its
folder name (via `ticket_prefix`) and a `Ticket:`/`Tickets:` line in its top file. It is
`ephemeral` or `reference` (research and prototypes), and `live` (a worktree or an unlanded
local branch carries one of its tickets, or a tracked file or an unlanded branch's commit
message names its path or ticketed folder name), `linked` (tickets, none live) or `unlinked`.
With a strategy saved, session start trashes each linked ephemeral bundle once it has stayed linked
for `grace_days` (`docs N` counts them);
reference, unlinked, live and keep-root bundles stay.

**strategy**: the saved `doc_strategy`: scope (Markdown only or whole bundles), the trash
retention in days (`trash_days`), the days a bundle must stay linked before it is trashed
(`grace_days`, counted from the first session start that saw it linked) and, optionally, keep-roots (`roots`): doc roots whose bundles
are always kept. Ephemeral bundles are trashed at session start and reference bundles promoted first.
`/lumberjack:cleanup` asks for it once; with none saved nothing is removed.

**trash**: `lumberjack-trash/` inside the git directory, where `--clear` and session start move bundle files into a
UTC-timestamped folder; clearing never deletes outright. A folder older than `trash_days` is
purged at session start, within the time budget (a large one is left partly deleted and finished
by a later run), or at once by `--purge-trash` in cleanup.

**preview**: `--preview`, a read-only run of the next session start's cleanup in report mode,
with file counts: what it would remove, trash and purge.

**tally**: `--tally`, the count of bundles trashed and files purged since the last session start
and in all, from `lumberjack-tally.jsonl` in the git directory, plus what the trash still holds.

**promote**: save a reference bundle's findings as a document tracked on the primary branch or on
a pushed prototype branch. Only then is the bundle cleared (into the trash).

**cleanup**: removing a landed worktree at session start while keeping its branch.

**confirmed removal**: removing one worktree the maintainer named and approved in
`/lumberjack:cleanup`, whatever its tier, while keeping its branch.

**orphan**: a directory in a worktree root that git no longer lists as a worktree.

**primary checkout**: the original clone, which stays on the primary branch while work
happens in worktrees.
