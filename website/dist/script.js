// An illustrative repository. No local files are read; the rules and output lines follow
// scripts/sweep_worktrees.py so the example matches what the sweep does and prints.
const $ = selector => document.querySelector(selector);
const DEFAULTS = { cleanup: 'prune', docs: true, scope: 'bundle', grace: 7 };
const settings = { ...DEFAULTS };
const state = { frame: 0, selected: '', playing: false, timer: 0 };
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

// Day 0 is the first session start after install: Monday 2 November 2026, 09:15 UTC.
const DAY0 = Date.UTC(2026, 10, 2, 9, 15);
const SWEEP_DAYS = [0, 2, 5, 9, 16, 30, 47];
const FRAME_DAYS = [undefined, ...SWEEP_DAYS];
const TRASH_DAYS = 30;
const KB = 1024;
const MB = KB * KB;
const PLAY_MS = 2600;

// Every merge is a squash merge, so a landed branch is never in main's history:
// its local branch stays and keeps counting as landed.
const ticket = (n, owner, slug, mb, created, merged, extra = {}) => ({
  id: `eng-${n}`, n, slug, mb, created, merged, branch: `${owner}/eng-${n}-${slug}`, dir: `app-eng-${n}`, ...extra,
});
const TICKETS = [
  ticket(201, 'codex', 'login', 1240, -34, -20),
  ticket(202, 'claude', 'billing', 980, -30, -18),
  ticket(203, 'claude', 'search', 1120, -27, -15, { dirty: true }),
  ticket(204, 'codex', 'auth', 1310, -25, -12),
  ticket(205, 'claude', 'ui', 1450, -22, -10),
  ticket(206, 'codex', 'email', 870, -19, -8),
  ticket(207, 'claude', 'export', 1030, -16, -6),
  ticket(208, 'codex', 'cache', 760, -14, -5),
  ticket(209, 'claude', 'api', 1180, -11, -3),
  ticket(210, 'codex', 'i18n', 940, -9, -2),
  ticket(211, 'claude', 'tokens', 1090, -8, -1),
  ticket(212, 'codex', 'search-ui', 1020, -6, 4),
  ticket(213, 'claude', 'reports', 1160, -5, 8),
  ticket(214, 'codex', 'mobile', 1380, -4, undefined),
  ticket(215, 'claude', 'audit', 890, -3, 14),
  ticket(216, 'codex', 'tokens-ui', 1050, -2, 28),
  ticket(217, 'claude', 'hotfix', 610, 2, 4),
  ticket(218, 'codex', 'invoices', 1210, 5, 12),
  ticket(219, 'claude', 'sso', 1290, 9, 20),
  ticket(220, 'codex', 'charts', 980, 16, 26),
  ticket(221, 'claude', 'onboarding', 1130, 30, 40),
  ticket(222, 'codex', 'dark-mode', 1070, 31, undefined),
];
const PROTOTYPE = { id: 'search-spike', branch: 'prototype/search-spike', dir: 'app-search-spike', mb: 640, created: -7 };
const ORPHANS = [690, 610, 720].map((mb, i) => ({ id: `eng-${188 + 3 * i}`, mb, path: `~/worktrees/app-eng-${188 + 3 * i}` }));
// eng-216 was branched from eng-211 before eng-211 was squash-merged; its owner rebases after day 0.
const DEPENDENT = { line: 'dependent codex/eng-216-tokens-ui of claude/eng-211-tokens: git rebase --onto main 8c41e07d2a95 codex/eng-216-tokens-ui', lastDay: 0 };

const ticketFiles = n => [
  { name: 'handoff.md', bytes: (5 + n % 9) * KB + n * 13 },
  { name: 'plan.md', bytes: (3 + n % 5) * KB + n * 7 },
  { name: 'run.log', bytes: (40 + (n * 37) % 300) * KB },
];
const bundle = (name, tickets, created, files, kind = 'ephemeral') => ({ id: name, path: `docs/work/${name}`, tickets, created, files, kind });
const BUNDLES = [
  ...[[195, 'onboarding'], [197, 'webhooks'], [199, 'pricing']].map(([n, slug]) => bundle(`eng-${n}-${slug}`, [n], -60, ticketFiles(n))),
  ...TICKETS.map(t => bundle(`eng-${t.n}-${t.slug}`, [t.n], t.created,
    t.n === 205 ? [...ticketFiles(t.n), { name: 'screenshots/', bytes: 96.4 * MB, count: 38 }] : ticketFiles(t.n))),
  ...[204, 209].map(n => bundle(`eng-${n}-research`, [n], TICKETS.find(t => t.n === n).created + 1,
    [{ name: 'findings.md', bytes: 18.2 * KB }, { name: 'sources.md', bytes: 6.1 * KB }], 'reference')),
  bundle('scratch-ideas', [], -40, [{ name: 'ideas.md', bytes: 4.3 * KB }]),
].sort((a, b) => a.path.localeCompare(b.path));

const isMarkdown = file => file.name.toLowerCase().endsWith('.md');
const totalBytes = files => files.reduce((sum, f) => sum + f.bytes, 0);
const fileCount = files => files.reduce((sum, f) => sum + (f.count || 1), 0);
const gb = mb => `${(mb / 1024).toFixed(1)} GB`;
const plural = (count, noun) => `${count} ${noun}${count === 1 ? '' : 's'}`;
const listNames = names => names.length < 3 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
const merged = (t, day) => t.merged !== undefined && t.merged <= day;
const daysText = day => day < 0 ? `${plural(-day, 'day')} before install` : `on day ${day}`;

function humanSize(bytes) {
  let value = bytes;
  for (const unit of ['B', 'KB', 'MB']) {
    if (value < 1024) return unit === 'B' ? `${Math.round(value)} B` : `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(1)} GB`;
}

function stamp(day) {
  return new Date(DAY0 + day * 86400000).toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
}

function dateText(day) {
  return new Date(DAY0 + day * 86400000).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
}

// The whole run under the current settings: one frame before install, then one per session start.
function simulate() {
  const run = { removed: new Map(), orphansCleaned: new Map(), since: new Map(), skip: new Set(), moved: new Map(), folders: [] };
  const frames = [describe(run, undefined, { lines: [], counts: {} })];
  SWEEP_DAYS.forEach(day => frames.push(describe(run, day, sweep(run, day))));
  return frames;
}

function existingTrees(run, day) {
  return TICKETS.filter(t => t.created <= day && !run.removed.has(t.id));
}

function sweep(run, day) {
  const mode = settings.cleanup;
  const counts = { cleaned: 0, removable: 0, landed: 0, dependents: 0, blocked: 0, unverified: 0, docs: 0 };
  const lines = [];
  const atReport = existingTrees(run, day);
  for (const t of atReport) {
    const fresh = t.created === day;
    if (!fresh && !merged(t, day)) { counts.unverified += 1; continue; }
    counts.landed += 1;
    if (mode === 'keep') continue;
    if (t.id === 'eng-211' && day <= DEPENDENT.lastDay) { counts.dependents += 1; lines.push(DEPENDENT.line); }
    const where = `${t.branch} ~/worktrees/${t.dir}`;
    if (t.dirty || fresh) { counts.blocked += 1; lines.push(`blocked ${where}: ${t.dirty ? 'dirty' : 'fresh'}`); }
    else if (mode === 'report') { counts.removable += 1; lines.push(`landed ${where}: would remove`); }
    else { counts.cleaned += 1; run.removed.set(t.id, day); lines.push(`cleaned ${where} (local branch kept)`); }
  }
  // Local branches whose worktree an earlier sweep removed: kept, and still landed.
  counts.landed += TICKETS.filter(t => run.removed.has(t.id) && run.removed.get(t.id) < day).length;
  if (mode !== 'keep' && day <= DEPENDENT.lastDay && run.removed.get('eng-211') < day) { counts.dependents += 1; lines.push(DEPENDENT.line); }
  const orphans = ORPHANS.filter(o => !run.orphansCleaned.has(o.id));
  for (const o of orphans) {
    if (mode === 'prune') { counts.cleaned += 1; run.orphansCleaned.set(o.id, day); lines.push(`cleaned orphan ${o.path}`); }
    else if (mode === 'report') lines.push(`orphan node_modules-only ${o.path}`);
  }
  const docs = sweepDocs(run, day, atReport);
  counts.docs = docs.trashed;
  const summary = `sweep: cleaned ${counts.cleaned}, landed ${counts.landed}, dependents ${counts.dependents}, blocked ${counts.blocked}, unverified ${counts.unverified}, orphans ${orphans.length}, docs ${counts.docs}`;
  if (mode === 'keep') return { lines: [summary], counts };
  const would = mode === 'report'
    ? [`sweep would: remove ${plural(counts.removable, 'worktree')}, delete ${plural(orphans.length, 'orphan')}, trash ${docs.wouldTrash}, purge 0 trash folders`] : [];
  return { lines: [summary, ...would, ...lines, ...docs.lines], counts };
}

// live: a worktree at report time, or a branch not yet landed, carries one of its tickets.
function docState(b, day, atReport) {
  const carried = new Set(atReport.map(t => t.n));
  TICKETS.filter(t => t.created <= day && !merged(t, day)).forEach(t => carried.add(t.n));
  if (b.tickets.some(n => carried.has(n))) return 'live';
  return b.tickets.length ? 'linked' : 'unlinked';
}

function remaining(run, b) {
  const moved = run.moved.get(b.id) || [];
  return b.files.filter(f => !moved.includes(f));
}

function docLine(b, files) {
  return `doc ${b.kind} ${b.path}: tickets ${b.tickets.map(n => `eng-${n}`).join(', ')}; ${humanSize(totalBytes(files))}`;
}

function sweepDocs(run, day, atReport) {
  const result = { lines: [], trashed: 0, wouldTrash: '0 bundles' };
  if (settings.cleanup === 'keep') return result;
  const present = BUNDLES.filter(b => b.created <= day && remaining(run, b).length);
  const linked = present.filter(b => docState(b, day, atReport) === 'linked');
  const marked = settings.docs ? linked.filter(b => b.kind === 'ephemeral') : [];
  linked.filter(b => !marked.includes(b)).forEach(b => result.lines.push(docLine(b, remaining(run, b))));
  if (!settings.docs) return result;
  const since = new Map(marked.map(b => [b.id, run.since.get(b.id) ?? day]));
  const waiting = marked.filter(b => day - since.get(b.id) < settings.grace);
  waiting.filter(b => !run.skip.has(b.id)).forEach(b => result.lines.push(docLine(b, remaining(run, b))));
  const due = marked.filter(b => !waiting.includes(b) && !run.skip.has(b.id));
  const inScope = b => remaining(run, b).filter(f => settings.scope === 'bundle' || isMarkdown(f));
  if (settings.cleanup === 'report') {
    const files = due.flatMap(inScope);
    due.forEach(b => result.lines.push(`doc ephemeral ${b.path}: would trash ${plural(fileCount(inScope(b)), 'file')} (${humanSize(totalBytes(inScope(b)))})`));
    if (due.length) result.wouldTrash = `${plural(due.length, 'bundle')} (${plural(fileCount(files), 'file')}, ${humanSize(totalBytes(files))})`;
  } else if (due.length) {
    const folder = { stamp: stamp(day), day, items: [] };
    for (const b of due) {
      const files = inScope(b);
      run.moved.set(b.id, [...(run.moved.get(b.id) || []), ...files]);
      folder.items.push({ bundle: b, files });
      since.delete(b.id);
      if (remaining(run, b).length) run.skip.add(b.id);
      result.trashed += 1;
      result.lines.push(`cleared ${b.path}: trashed ${folder.stamp}`);
    }
    run.folders.push(folder);
  }
  run.since = since;
  if (settings.cleanup === 'prune') {
    run.folders.filter(f => f.purged === undefined && day - f.day >= TRASH_DAYS).forEach(f => {
      f.purged = day;
      result.lines.push(`purged ${f.stamp}`);
    });
  }
  return result;
}

// The board after a sweep (or before install): tiles, document lanes, counters and story.
function describe(run, day, result) {
  const at = day ?? 0;
  const atReport = day === undefined ? existingTrees(run, 0) : TICKETS.filter(t => t.created <= at && (!run.removed.has(t.id) || run.removed.get(t.id) === at));
  const trees = [
    { key: 'primary', name: 'app', icon: 'cabin', kind: 'kept', label: 'primary', size: 'main', detail: 'The primary checkout, on main. The sweep never touches it.' },
    ...TICKETS.map(t => treeTile(run, t, day)),
    { key: PROTOTYPE.id, name: 'search-spike', icon: 'pine', kind: 'kept', label: 'prototype', size: gb(PROTOTYPE.mb), detail: `${PROTOTYPE.branch}. Prototype branches are never cleaned.` },
    ...ORPHANS.map(o => orphanTile(run, o, day)),
  ];
  const onDisk = existingTrees(run, at);
  const orphansLeft = ORPHANS.filter(o => !run.orphansCleaned.has(o.id));
  const createdTrees = TICKETS.filter(t => t.created <= at);
  const createdBundles = BUNDLES.filter(b => b.created <= at);
  const stats = {
    trees: onDisk.length + 1, treesWithout: createdTrees.length + 1, // + the prototype
    mb: [...onDisk, PROTOTYPE, ...orphansLeft].reduce((sum, x) => sum + x.mb, 0),
    mbWithout: [...createdTrees, PROTOTYPE, ...ORPHANS].reduce((sum, x) => sum + x.mb, 0),
    docs: createdBundles.filter(b => remaining(run, b).length).length, docsWithout: createdBundles.length,
    branches: TICKETS.filter(t => merged(t, at)).length,
  };
  return {
    day, trees, docs: docLanes(run, day, atReport), stats, counts: result.counts,
    lines: day === undefined ? beforeLines(stats) : result.lines,
    removedNow: TICKETS.filter(t => day !== undefined && run.removed.get(t.id) === day).map(t => t.id),
  };
}

function treeTile(run, t, day) {
  const at = day ?? 0;
  const base = { key: t.id, name: t.id, size: gb(t.mb) };
  if (t.created > at) return { ...base, icon: 'plot', kind: 'absent', label: '', size: '', detail: '' };
  const removedOn = run.removed.get(t.id);
  const mergeText = `Squash-merged ${daysText(t.merged)}`;
  if (removedOn !== undefined) {
    return removedOn === day
      ? { ...base, icon: 'felled', kind: 'removed', label: 'removed', detail: `${t.branch}. ${mergeText}, clean and pushed, so this sweep removed ~/worktrees/${t.dir}. The branch is kept.` }
      : { ...base, icon: 'stump', kind: 'gone', label: `removed day ${removedOn}`, detail: `${t.branch}. Removed on day ${removedOn}; the branch is still there.` };
  }
  const fresh = day !== undefined && t.created === day;
  if (fresh) {
    return settings.cleanup === 'keep'
      ? { ...base, icon: 'sapling', kind: 'live', label: 'new today', detail: `${t.branch}. Created today from main.` }
      : { ...base, icon: 'sapling', kind: 'blocked', label: 'blocked: fresh', detail: `${t.branch}. Created today from main with no commits yet, so it looks merged. A worktree under 24 hours old whose branch has not moved is kept.` };
  }
  if (!merged(t, at)) {
    const when = t.merged === undefined ? 'It never merges in this example.' : `It merges on day ${t.merged}.`;
    return { ...base, icon: 'pine', kind: 'live', label: 'in progress', detail: `${t.branch}. Not merged yet, so it stays. ${when}` };
  }
  if (day === undefined) return { ...base, icon: 'bare', kind: 'warn', label: 'merged', detail: `${t.branch}. ${mergeText}. Without Lumberjack it stays on disk until someone removes it.` };
  if (settings.cleanup === 'keep') return { ...base, icon: 'bare', kind: 'kept', label: 'landed · kept', detail: `${t.branch}. ${mergeText}. cleanup = "keep" counts it and leaves it.` };
  if (t.dirty) return { ...base, icon: 'bare', kind: 'blocked', label: 'blocked: dirty', detail: `${t.branch}. ${mergeText}, but it has uncommitted files. It stays until they are committed or deleted.` };
  return { ...base, icon: 'bare', kind: 'warn', label: 'would remove', detail: `${t.branch}. ${mergeText}. Report mode lists it and leaves it on disk.` };
}

function orphanTile(run, o, day) {
  const base = { key: o.id, name: o.id, size: gb(o.mb) };
  const cleanedOn = run.orphansCleaned.get(o.id);
  if (cleanedOn === day) return { ...base, icon: 'cleared', kind: 'removed', label: 'cleaned', detail: `${o.path}: its worktree entry was gone and only node_modules was left, so prune deleted it.` };
  if (cleanedOn !== undefined) return { ...base, icon: 'cleared', kind: 'gone', label: `cleaned day ${cleanedOn}`, detail: `${o.path} was deleted on day ${cleanedOn}.` };
  const why = day === undefined ? 'Nothing removes it.' : settings.cleanup === 'report' ? 'Report mode lists it; prune would delete it.' : 'cleanup = "keep" leaves it.';
  return { ...base, icon: 'debris', kind: 'warn', label: 'orphan', detail: `${o.path}: a leftover folder holding only node_modules. Its worktree entry is gone. ${why}` };
}

function docLanes(run, day, atReport) {
  const at = day ?? 0;
  const work = BUNDLES.filter(b => b.created <= at && remaining(run, b).length).map(b => workTile(run, b, day, atReport));
  const folderTile = item => ({
    key: item.files.length === item.bundle.files.length ? item.bundle.id : `${item.bundle.id}.md`,
    name: item.bundle.id, files: item.files,
  });
  const trash = run.folders.filter(f => f.purged === undefined).map(f => ({
    stamp: f.stamp, day: f.day,
    tiles: f.items.map(item => ({ ...folderTile(item), kind: 'trash', label: f.day === day ? 'trashed' : 'in trash',
      detail: `${item.files.map(x => x.name).join(', ')} moved to .git/lumberjack-trash/${f.stamp}/ on day ${f.day}. Recoverable for ${TRASH_DAYS} days.` })),
  }));
  const purged = run.folders.filter(f => f.purged !== undefined).flatMap(f => f.items.map(item => ({
    ...folderTile(item), kind: 'gone', label: f.purged === day ? 'purged' : `purged day ${f.purged}`,
    detail: `Trashed on day ${f.day}, deleted with its trash folder on day ${f.purged}.`,
  })));
  return { work, trash, purged };
}

function workTile(run, b, day, atReport) {
  const files = remaining(run, b);
  const base = { key: b.id, name: b.id, files };
  const tickets = b.tickets.map(n => `eng-${n}`).join(', ');
  if (files.length < b.files.length) return { ...base, kind: 'kept', label: `${files.map(f => f.name).join(', ')} ${files.length > 1 ? 'stay' : 'stays'}`, detail: `scope = "markdown": its .md files went to the trash; ${files.map(f => f.name).join(', ')} ${files.length > 1 ? 'stay' : 'stays'} in docs/work.` };
  if (b.kind === 'reference') return { ...base, kind: 'kept', label: 'reference', detail: 'Research is reference material. The sweep lists it and never trashes it.' };
  const linkState = docState(b, day ?? 0, atReport);
  if (linkState === 'unlinked') return { ...base, kind: 'kept', label: 'unlinked', detail: 'No ticket in its name or a Tickets: line, so nothing links it to landed work. It stays.' };
  if (linkState === 'live') return { ...base, kind: 'live', label: 'live', detail: `A worktree or an unmerged branch still carries ${tickets}, so its docs are in use.` };
  if (day === undefined) return { ...base, kind: 'warn', label: 'landed', detail: `${tickets} landed and no worktree carries it. Without Lumberjack it stays here.` };
  if (settings.cleanup === 'keep') return { ...base, kind: 'kept', label: 'landed · kept', detail: `${tickets} landed; cleanup = "keep" moves nothing.` };
  if (!settings.docs) return { ...base, kind: 'kept', label: 'landed · kept', detail: `${tickets} landed, but no doc_strategy is saved, so docs never move.` };
  const waited = day - (run.since.get(b.id) ?? day);
  if (settings.cleanup === 'report' && waited >= settings.grace) return { ...base, kind: 'warn', label: 'would trash', detail: `${tickets} landed and its grace is over. Report mode lists it and moves nothing.` };
  return { ...base, kind: 'warn', label: `grace ${waited}/${settings.grace}`, detail: `${tickets} landed and no worktree carries it. Day ${waited} of ${settings.grace} days of grace.` };
}

function beforeLines(stats) {
  return [
    '$ git worktree list | wc -l', String(stats.trees + 1),
    '$ du -sh ~/worktrees', `${Math.round(stats.mb / 1024)}G`,
    '$ ls docs/work | wc -l', String(stats.docs),
  ];
}

function story(frames, index) {
  const frame = frames[index];
  const s = frame.stats;
  if (frame.day === undefined) {
    const landed = frame.trees.filter(t => t.label === 'merged').length;
    return `Before Lumberjack: ${s.trees} worktrees and ${ORPHANS.length} leftover folders fill ${gb(s.mb)}. ${landed} of those worktrees merged days or weeks ago. docs/work holds ${s.docs} bundles.`;
  }
  const previous = frames[index - 1];
  const prevDay = previous.day ?? -Infinity;
  const parts = [];
  const mergedNow = TICKETS.filter(t => t.merged !== undefined && t.merged > prevDay && t.merged <= frame.day && prevDay !== -Infinity);
  if (mergedNow.length) parts.push(`${listNames(mergedNow.map(t => t.id))} merged.`);
  const removed = frame.removedNow.length;
  const orphans = frame.trees.filter(t => t.icon === 'cleared' && t.kind === 'removed').length;
  const freedMb = [...TICKETS.filter(t => frame.removedNow.includes(t.id)), ...ORPHANS.filter(o => frame.trees.some(t => t.key === o.id && t.kind === 'removed'))].reduce((sum, x) => sum + x.mb, 0);
  if (settings.cleanup === 'keep') parts.push(`cleanup = "keep": the sweep counts ${frame.counts.landed} landed and changes nothing.`);
  else if (settings.cleanup === 'report') parts.push(`Report mode lists ${plural(frame.counts.removable, 'worktree')} it would remove and changes nothing.`);
  else if (removed || orphans) parts.push(`The sweep removed ${plural(removed, 'worktree')}${orphans ? ` and ${orphans} leftover node_modules folders` : ''}: ${gb(freedMb)} back. Every branch stays.`);
  const fresh = frame.trees.filter(t => t.label === 'blocked: fresh').map(t => t.name);
  if (fresh.length) parts.push(`${listNames(fresh)} is brand new, so it waits.`);
  if (index === 1 && settings.cleanup !== 'keep') parts.push('eng-203 has uncommitted files, so it stays. eng-216 was built on the squash-merged eng-211, so the sweep prints its rebase.');
  const trashed = frame.counts.docs;
  const purged = frame.lines.filter(line => line.startsWith('purged')).length;
  const graceStarts = frame.docs.work.filter(d => d.label === `grace 0/${settings.grace}`).length;
  if (trashed) parts.push(`${plural(trashed, 'doc bundle')} moved to the recoverable trash.`);
  if (graceStarts && settings.grace) parts.push(`${plural(graceStarts, 'landed bundle')} ${graceStarts === 1 ? 'starts' : 'start'} ${settings.grace} days of grace.`);
  if (purged) parts.push(`${plural(purged, 'trash folder')} passed ${TRASH_DAYS} days and ${purged === 1 ? 'was' : 'were'} deleted.`);
  if (!parts.length) parts.push('Nothing new landed, so nothing changes.');
  return parts.join(' ');
}

// Rendering. A tile is marked changed when its state differs from the previous frame;
// a stump fading from "removed" to "removed day N", or a grace count ticking, is not news.
let frames = simulate();
const signature = item => item && `${item.kind}|${item.label.replace(/\d+\/\d+|day \d+/, '')}|${item.lane || ''}`;
const motion = () => !reducedMotion.matches;

function changedKeys(now, before) {
  if (!before) return new Set();
  const old = new Map(before.map(item => [item.key, item]));
  return new Set(now.filter(item => {
    const prev = old.get(item.key);
    if (prev && prev.kind === 'removed' && item.kind === 'gone') return false;
    return item.kind !== 'absent' && signature(prev) !== signature(item);
  }).map(item => item.key));
}

function flatDocs(docs) {
  return [
    ...docs.work.map(d => ({ ...d, lane: 'work' })),
    ...docs.trash.flatMap(f => f.tiles.map(d => ({ ...d, lane: 'trash' }))),
    ...docs.purged.map(d => ({ ...d, lane: 'purged' })),
  ];
}

function treeHtml(t, changed, fellFrom) {
  if (t.kind === 'absent') return '<li class="tile plot" aria-hidden="true"><svg class="tile-art"><use href="#i-plot"/></svg></li>';
  const falling = fellFrom ? `<svg class="falling" aria-hidden="true"><use href="#i-${fellFrom}"/></svg>` : '';
  return `<li class="tile kind-${t.kind}${changed ? ' changed' : ''}"><button type="button" data-select="${t.key}" aria-pressed="${state.selected === t.key}">`
    + `<span class="tile-art-wrap${fellFrom ? ' felling' : ''}">${falling}<svg class="tile-art" aria-hidden="true"><use href="#i-${t.icon}"/></svg></span>`
    + `<span class="tile-name">${t.name}</span><span class="tile-size">${t.size}</span><span class="chip">${t.label}</span></button></li>`;
}

function docHtml(d, changed) {
  const key = `doc:${d.key}`;
  return `<li class="doc kind-${d.kind}${changed ? ' changed' : ''}" data-key="${d.key}"><button type="button" data-select="${key}" aria-pressed="${state.selected === key}">`
    + `<svg class="doc-art" aria-hidden="true"><use href="#i-doc"/></svg><span class="doc-name">${d.name}</span><span class="chip">${d.label}</span></button></li>`;
}

function renderBoard(animate) {
  const frame = frames[state.frame];
  const previous = frames[state.frame - 1];
  const treeChanges = changedKeys(frame.trees, previous && previous.trees);
  const docs = flatDocs(frame.docs);
  const docChanges = changedKeys(docs, previous && flatDocs(previous.docs));
  // A tree felled this sweep falls as whatever it looked like before: bare if merged earlier, a pine if it merged since.
  const looked = new Map((previous ? previous.trees : []).map(t => [t.key, ['pine', 'bare', 'sapling'].includes(t.icon) ? t.icon : 'bare']));
  $('#tree-rows').innerHTML = frame.trees.map(t => treeHtml(t, treeChanges.has(t.key), animate && t.icon === 'felled' && motion() && (looked.get(t.key) || 'bare'))).join('');
  const lanes = $('#docs-column');
  const before = new Map([...lanes.querySelectorAll('[data-key]')].map(el => [el.dataset.key, el.getBoundingClientRect()]));
  $('#lane-work').innerHTML = frame.docs.work.map(d => docHtml(d, docChanges.has(d.key))).join('');
  $('#lane-trash').innerHTML = frame.docs.trash.length
    ? frame.docs.trash.map(f => `<div class="trash-folder"><p><code>${f.stamp}/</code> day ${f.day}</p><ul>${f.tiles.map(d => docHtml(d, docChanges.has(d.key))).join('')}</ul></div>`).join('')
    : '<p class="lane-empty">Empty</p>';
  $('#lane-purged').innerHTML = frame.docs.purged.map(d => docHtml(d, docChanges.has(d.key))).join('') || '<li class="lane-empty">Nothing yet</li>';
  $('#count-work').textContent = frame.docs.work.length;
  $('#count-trash').textContent = frame.docs.trash.reduce((sum, f) => sum + f.tiles.length, 0);
  $('#count-purged').textContent = frame.docs.purged.length;
  if (animate && motion()) flyDocs(lanes, before);
  $('#trees-changed').textContent = treeChanges.size ? `· ${treeChanges.size} changed` : '';
  $('#docs-changed').textContent = docChanges.size ? `· ${docChanges.size} changed` : '';
}

// FLIP: a bundle that changed lanes slides from where it was to where it is now.
function flyDocs(container, before) {
  container.querySelectorAll('[data-key]').forEach(el => {
    const from = before.get(el.dataset.key);
    const to = el.getBoundingClientRect();
    if (!to.width) return;
    if (!from || !from.width) { el.animate([{ opacity: 0, transform: 'scale(.85)' }, { opacity: 1, transform: 'none' }], { duration: 450, easing: 'ease-out' }); return; }
    const dx = from.left - to.left;
    const dy = from.top - to.top;
    if (Math.abs(dx) + Math.abs(dy) < 2) return;
    el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 900, easing: 'cubic-bezier(.3, .7, .2, 1)' });
  });
}

const COUNTERS = [
  ['trees', s => s.trees, s => s.treesWithout, String],
  ['disk', s => s.mb / 1024, s => s.mbWithout / 1024, v => `${v.toFixed(1)} GB`],
  ['docs', s => s.docs, s => s.docsWithout, String],
];

function renderCounters(animate) {
  const now = frames[state.frame].stats;
  const prev = state.frame ? frames[state.frame - 1].stats : now;
  for (const [name, value, without, format] of COUNTERS) {
    const change = value(now) - value(prev);
    tween($(`#stat-${name}`), value(prev), value(now), format, animate);
    $(`#without-${name}`).textContent = format(without(now));
    const delta = $(`#delta-${name}`);
    delta.textContent = Math.abs(change) < 0.05 ? '' : `${change < 0 ? '−' : '+'}${format(Math.abs(change))}`;
    delta.className = `delta ${change < 0 ? 'down' : 'up'}`;
  }
  $('#stat-branches').textContent = now.branches;
}

function tween(el, from, to, format, animate) {
  cancelAnimationFrame(el.tween);
  if (!animate || !motion() || from === to) { el.textContent = format(to); return; }
  const start = performance.now();
  const step = time => {
    const t = Math.min(1, (time - start) / 700);
    const value = from + (to - from) * (1 - (1 - t) ** 3);
    el.textContent = format(Number.isInteger(to) && Number.isInteger(from) ? Math.round(value) : value);
    if (t < 1) el.tween = requestAnimationFrame(step);
  };
  el.tween = requestAnimationFrame(step);
}

// Disk used by worktrees, with and without Lumberjack, one point per frame. Drawn at the
// element's pixel width so its text never scales below 12px.
function renderChart() {
  const svg = $('#disk-chart');
  const width = Math.max(260, Math.round(svg.getBoundingClientRect().width));
  const height = 176;
  const pad = { left: 40, right: 16, top: 26, bottom: 28 };
  const withGb = frames.map(f => f.stats.mb / 1024);
  const withoutGb = frames.map(f => f.stats.mbWithout / 1024);
  const top = Math.ceil(Math.max(...withoutGb) / 10) * 10;
  const x = i => pad.left + i * (width - pad.left - pad.right) / (frames.length - 1);
  const y = v => pad.top + (1 - v / top) * (height - pad.top - pad.bottom);
  const path = values => values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('');
  const grid = [0, top / 2, top].map(v => `<line class="grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${pad.left - 6}" y="${y(v) + 4}" text-anchor="end">${v} GB</text>`).join('');
  const ticks = FRAME_DAYS.map((day, i) => `<text class="axis" x="${x(i)}" y="${height - 8}" text-anchor="middle">${day === undefined ? 'pre' : day}</text>`).join('');
  const i = state.frame;
  const hits = frames.map((f, k) => `<rect class="hit" data-frame="${k}" x="${x(k) - 14}" y="${pad.top - 10}" width="28" height="${height - pad.top - pad.bottom + 10}"><title>${k ? `Day ${f.day}` : 'Before Lumberjack'}: ${withGb[k].toFixed(1)} GB with Lumberjack, ${withoutGb[k].toFixed(1)} GB without</title></rect>`).join('');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('height', height);
  svg.innerHTML = `${grid}${ticks}<line class="cursor" x1="${x(i)}" x2="${x(i)}" y1="${pad.top - 8}" y2="${height - pad.bottom}"/>`
    + `<path class="line-without" d="${path(withoutGb)}"/><path class="line-with" d="${path(withGb)}"/>`
    + `<circle class="dot-without" cx="${x(i)}" cy="${y(withoutGb[i])}" r="5"/><circle class="dot-with" cx="${x(i)}" cy="${y(withGb[i])}" r="5"/>`
    + `<text class="value" x="${x(i) + (i > 4 ? -9 : 9)}" y="${y(withoutGb[i]) - 9}" text-anchor="${i > 4 ? 'end' : 'start'}">${withoutGb[i].toFixed(1)} GB</text>`
    + `<text class="value value-with" x="${x(i) + (i > 4 ? -9 : 9)}" y="${y(withGb[i]) + 18}" text-anchor="${i > 4 ? 'end' : 'start'}">${withGb[i].toFixed(1)} GB</text>${hits}`;
  svg.setAttribute('aria-label', `Disk used by worktrees ${i ? `on day ${frames[i].day}` : 'before Lumberjack'}: ${withGb[i].toFixed(1)} GB with Lumberjack, ${withoutGb[i].toFixed(1)} GB without.`);
}

function renderDetail() {
  const frame = frames[state.frame];
  const docs = flatDocs(frame.docs);
  const key = state.selected;
  const item = key.startsWith('doc:') ? docs.find(d => `doc:${d.key}` === key) : frame.trees.find(t => t.key === key && t.kind !== 'absent');
  const box = $('#tile-detail');
  if (!item) { box.innerHTML = 'Select any tree or document to see why it is there.'; return; }
  const extra = item.files ? `${plural(fileCount(item.files), 'file')}, ${humanSize(totalBytes(item.files))}` : item.size;
  box.innerHTML = `<b>${item.name}</b> <span class="chip-inline">${item.label}</span> ${extra}. ${item.detail}`;
}

function renderSimulation(animate = false) {
  const frame = frames[state.frame];
  const last = frames.length - 1;
  renderBoard(animate);
  renderCounters(animate);
  renderChart();
  renderDetail();
  $('#sweep-log').innerHTML = frame.lines.map(line => `<span class="log-${line.startsWith('$') ? 'cmd' : line.split(/[ :]/)[0]}">${line}</span>`).join('\n');
  $('#log-title').textContent = frame.day === undefined ? 'BEFORE INSTALL' : `SESSION START, DAY ${frame.day}`;
  $('#day-title').textContent = frame.day === undefined ? 'Before Lumberjack' : `Day ${frame.day}`;
  $('#day-date').textContent = frame.day === undefined ? 'Installed today' : `${dateText(frame.day)} · session start`;
  $('#sim-story').textContent = story(frames, state.frame);
  $('#next-sweep').textContent = state.frame === 0 ? 'Run the first sweep →' : state.frame === last ? 'Start over ↺' : 'Next sweep →';
  const slider = $('#frame');
  slider.value = state.frame;
  slider.setAttribute('aria-valuetext', frame.day === undefined ? 'Before Lumberjack' : `Day ${frame.day}, ${dateText(frame.day)}`);
  $('#settings-summary').textContent = `${settings.cleanup}${settings.docs ? ` · ${settings.grace}-day grace${settings.scope === 'markdown' ? ' · .md only' : ''}` : ' · no doc strategy'}`;
  $('#doc-scope').disabled = !settings.docs;
  $('#grace-days').disabled = !settings.docs;
}

function showFrame(index) {
  state.frame = index;
  renderSimulation(true);
  if (!state.playing) $('#sim-announcement').textContent = `${$('#day-title').textContent}. ${$('#sim-story').textContent}`;
}

function setPlaying(on) {
  state.playing = on;
  clearInterval(state.timer);
  const button = $('#play');
  button.setAttribute('aria-pressed', String(on));
  $('#play-label').textContent = on ? 'Pause' : 'Play';
  $('#play-icon').textContent = on ? '❚❚' : '▶';
  if (!on) return;
  if (state.frame === frames.length - 1) showFrame(0);
  state.timer = setInterval(() => {
    if (state.frame >= frames.length - 1) setPlaying(false);
    else showFrame(state.frame + 1);
  }, PLAY_MS);
}

$('#play').addEventListener('click', () => setPlaying(!state.playing));
$('#next-sweep').addEventListener('click', () => { setPlaying(false); showFrame((state.frame + 1) % frames.length); });
$('#frame').addEventListener('input', event => { setPlaying(false); showFrame(Number(event.target.value)); });
$('#disk-chart').addEventListener('click', event => {
  const hit = event.target.closest('[data-frame]');
  if (hit) { setPlaying(false); showFrame(Number(hit.dataset.frame)); }
});
$('.sim-board').addEventListener('click', event => {
  const button = event.target.closest('[data-select]');
  if (!button) return;
  state.selected = state.selected === button.dataset.select ? '' : button.dataset.select;
  document.querySelectorAll('[data-select]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.select === state.selected)));
  renderDetail();
});
new ResizeObserver(renderChart).observe($('.disk-chart'));
// Press play once for the reader the first time the example scrolls into view.
new IntersectionObserver((entries, observer) => {
  if (!entries.some(entry => entry.isIntersecting)) return;
  observer.disconnect();
  if (motion() && state.frame === 0 && !state.playing) setPlaying(true);
}, { threshold: 0.4 }).observe($('.sim'));

const controls = { 'cleanup-mode': 'cleanup', 'docs-enabled': 'docs', 'doc-scope': 'scope', 'grace-days': 'grace' };
const readControl = { cleanup: v => v, docs: v => v === 'yes', scope: v => v, grace: Number };
function applySettings() {
  frames = simulate();
  renderSimulation();
}
Object.entries(controls).forEach(([id, key]) => {
  $(`#${id}`).addEventListener('change', event => { settings[key] = readControl[key](event.target.value); applySettings(); });
});
$('#reset-settings').addEventListener('click', () => {
  Object.assign(settings, DEFAULTS);
  $('#cleanup-mode').value = 'prune'; $('#docs-enabled').value = 'yes'; $('#doc-scope').value = 'bundle'; $('#grace-days').value = '7';
  applySettings();
});
$('#settings-form').addEventListener('submit', event => event.preventDefault());


function wireTabs(selector, activate) {
  const tabs = [...document.querySelectorAll(selector)];
  function select(tab, focus = false) {
    tabs.forEach(item => { const active = item === tab; item.setAttribute('aria-selected', String(active)); item.tabIndex = active ? 0 : -1; });
    activate(tab);
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); select(tabs[next], true); }
    });
  });
}
wireTabs('.view-switch [role="tab"]', selected => {
  document.querySelector('.sim-board').dataset.show = selected.id === 'show-docs' ? 'docs' : 'trees';
});
wireTabs('.install-tabs [role="tab"]', selected => {
  document.querySelectorAll('.install-tabs [role="tab"]').forEach(tab => { document.getElementById(tab.getAttribute('aria-controls')).hidden = tab !== selected; });
});

document.querySelectorAll('.copy-button').forEach(button => {
  button.addEventListener('click', async () => {
    const code = button.previousElementSibling;
    const label = button.textContent;
    try {
      await navigator.clipboard.writeText(code.textContent.trim());
      button.textContent = 'Copied ✓';
      $('#copy-status').textContent = 'Command copied.';
    } catch {
      const range = document.createRange(); range.selectNodeContents(code);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      button.textContent = 'Selected';
      $('#copy-status').textContent = 'Command selected; press Control+C or Command+C to copy.';
    }
    window.setTimeout(() => { button.textContent = label; }, 2000);
  });
});

const popover = $('#example-settings');
const nativePopover = Object.hasOwn(HTMLElement.prototype, 'popover');
function settingsOpen() {
  return nativePopover ? popover.matches(':popover-open') : !popover.hidden;
}
function positionSettings() {
  if (!settingsOpen()) return;
  const anchor = $('#open-settings').getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  popover.style.maxWidth = `${viewportWidth - 24}px`;
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  popover.style.left = `${Math.max(12, Math.min(anchor.right - width, viewportWidth - width - 12))}px`;
  popover.style.top = `${Math.max(12, Math.min(anchor.bottom + 8, window.innerHeight - height - 12))}px`;
}
popover.addEventListener('toggle', event => { if (event.newState === 'open') positionSettings(); });
window.addEventListener('resize', positionSettings);
new ResizeObserver(positionSettings).observe(popover);
// Browsers without the Popover API (Safari before 17, Firefox before 125) get the same open and close.
if (!nativePopover) {
  popover.hidden = true;
  document.querySelectorAll('[popovertarget]').forEach(button => button.addEventListener('click', () => {
    popover.hidden = button.getAttribute('popovertargetaction') === 'hide' || !popover.hidden;
    positionSettings();
  }));
  document.addEventListener('keydown', event => { if (event.key === 'Escape') popover.hidden = true; });
}

renderSimulation();

// The hero forest: bare trees are finished work, and a click clears one. Pines are still growing.
const forestTrees = [...document.querySelectorAll('.cut-tree')];
function updateForest(message) {
  const cleared = forestTrees.filter(tree => tree.classList.contains('felled')).length;
  const left = forestTrees.length - cleared;
  $('#forest-dead').textContent = left ? `${plural(left, 'finished task')}` : 'all finished work cleared';
  const hint = $('#forest-hint');
  hint.textContent = message;
  if (!left) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'text-link';
    button.textContent = 'Grow them back ↺';
    button.addEventListener('click', regrowForest);
    hint.append(' ', button);
  }
}
function fellTree(tree, index) {
  if (tree.classList.contains('felled')) return;
  tree.classList.add('felled', index % 2 ? 'fall-left' : 'fall-right');
  const hadFocus = document.activeElement === tree;
  tree.setAttribute('aria-disabled', 'true');
  tree.setAttribute('aria-label', `Finished task ${index + 1}, cleared`);
  tree.tabIndex = -1;
  const standing = forestTrees.filter(t => !t.classList.contains('felled'));
  updateForest(standing.length ? `Cleared. Its branch is kept. ${plural(standing.length, 'bare tree')} to go.` : 'All landed work is cleared. The three pines are still growing, so they stay.');
  if (hadFocus) (standing[0] || $('#forest-hint button')).focus();
}
function regrowForest() {
  forestTrees.forEach((tree, index) => {
    tree.classList.remove('felled', 'fall-left', 'fall-right');
    tree.removeAttribute('aria-disabled');
    tree.tabIndex = 0;
    tree.setAttribute('aria-label', `Clear finished task ${index + 1}`);
  });
  updateForest('Bare trees are finished work. Click one to clear it.');
  forestTrees[0].focus();
}
forestTrees.forEach((tree, index) => {
  tree.addEventListener('click', () => fellTree(tree, index));
  tree.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    fellTree(tree, index);
  });
});
document.querySelectorAll('.growing').forEach(pine => pine.addEventListener('click', () => {
  pine.classList.remove('shake');
  pine.getBoundingClientRect();
  pine.classList.add('shake');
  $('#forest-hint').textContent = 'That pine is still growing. Lumberjack never cuts unfinished work.';
}));
