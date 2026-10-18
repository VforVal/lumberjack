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

**fresh**: a worktree added less than 24 hours ago whose branch still points at the commit
it was created at. Kept, even though it looks integrated; after 24 hours the usual rules apply.

**locked**: a worktree locked with `git worktree lock`. Never removed by lumberjack.

**cleanup**: removing a landed worktree at session start while keeping its branch.

**confirmed removal**: removing one worktree the maintainer named and approved in
`/lumberjack:cleanup`, whatever its tier, while keeping its branch.

**orphan**: a directory in a worktree root that git no longer lists as a worktree.

**primary checkout**: the original clone, which stays on the primary branch while work
happens in worktrees.
