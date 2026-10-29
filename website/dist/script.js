// An illustrative repository. No local files are read; the output lines follow
// scripts/sweep_worktrees.py so the example matches what the sweep prints.
const $ = selector => document.querySelector(selector);
const DEFAULTS = { cleanup: 'prune', docs: true, scope: 'bundle', grace: 7 };
const settings = { ...DEFAULTS };
const state = { stage: 0 };
const DAYS = [0, 1, 8, 38];
const STAMPS = { 1: '20261030T090000Z', 2: '20261106T090000Z' };
const NEXT_LABELS = ['Merge two branches, then sweep →', 'Wait out the grace period →', 'Wait out the trash retention →', 'Start over ↺'];
const LOGIN = 'codex/eng-42-login ~/worktrees/app-login';
const DEPENDENT = 'dependent codex/eng-45-ui of codex/eng-42-login: git rebase --onto main 3f9c2ab41d07 codex/eng-45-ui';

// Worktree rows: kind drives the chip colour; chip and reason are what the reader sees.
function worktrees(stage) {
  const merged = stage >= 1;
  const mode = settings.cleanup;
  const row = (id, name, branch, kind, chip, reason) => ({ id, name, branch, kind, chip, reason });
  const login = !merged ? row('login', 'app-login/', 'codex/eng-42-login', 'live', 'in progress', 'Not merged yet.')
    : mode === 'prune' ? row('login', 'app-login/', 'codex/eng-42-login', 'gone', 'removed', stage === 1 ? 'Squash-merged, clean and pushed, so the sweep removed it. The branch stays.' : 'Removed on day 1. Its branch codex/eng-42-login is still there.')
    : mode === 'report' ? row('login', 'app-login/', 'codex/eng-42-login', 'warn', 'would remove', 'Landed. Report mode lists it and leaves it on disk.')
    : row('login', 'app-login/', 'codex/eng-42-login', 'kept', 'kept', 'Landed, but cleanup = "keep" leaves it.');
  const billing = !merged ? row('billing', 'app-billing/', 'claude/eng-44-billing', 'live', 'in progress', 'Not merged yet. It has uncommitted files.')
    : mode === 'keep' ? row('billing', 'app-billing/', 'claude/eng-44-billing', 'kept', 'kept', 'Landed, but cleanup = "keep" leaves it.')
    : row('billing', 'app-billing/', 'claude/eng-44-billing', 'blocked', 'blocked: dirty', 'Merged, but its uncommitted files would be lost. It stays until they are committed or deleted.');
  const ui = !merged || mode === 'keep' ? row('ui', 'app-ui/', 'codex/eng-45-ui', 'live', 'in progress', 'Built on top of codex/eng-42-login.')
    : row('ui', 'app-ui/', 'codex/eng-45-ui', 'warn', 'rebase needed', 'Its base landed as a squash. The sweep prints the rebase to run before pushing.');
  const docs = stage === 0 ? row('docs', 'app-docs/', 'claude/eng-46-docs', 'absent', 'not created yet', 'An agent opens this worktree on day 1.')
    : stage === 1 ? (mode === 'keep' ? row('docs', 'app-docs/', 'claude/eng-46-docs', 'kept', 'kept', 'New from main two hours ago; cleanup = "keep" leaves it.')
      : row('docs', 'app-docs/', 'claude/eng-46-docs', 'blocked', 'blocked: fresh', 'New from main two hours ago, so it looks landed. Worktrees under 24 hours old whose branch has not moved are kept.'))
    : row('docs', 'app-docs/', 'claude/eng-46-docs', 'live', 'in progress', 'Has its own commits now. Not merged.');
  return [
    row('main', 'app/', 'main', 'kept', 'primary', 'The primary checkout. Never touched.'),
    login,
    row('search', 'app-search/', 'claude/eng-43-search', 'live', 'in progress', 'No pull request merged yet.'),
    billing,
    ui,
    docs,
    row('spike', 'app-spike/', 'prototype/cache-spike', 'kept', 'prototype', 'Prototype branches are never cleaned.'),
  ];
}

// When the eng-42 bundle moves to trash, or undefined when it never does.
function trashStage() {
  if (settings.cleanup !== 'prune' || !settings.docs) return undefined;
  return settings.grace === 0 ? 1 : 2;
}

function loginFiles() {
  return settings.scope === 'markdown' ? ['handoff.md', 'plan.md'] : ['handoff.md', 'plan.md', 'run.log'];
}

function docBundles(stage) {
  const bundle = (id, name, files, kind, chip, reason) => ({ id, name, files, kind, chip, reason });
  const trashed = trashStage();
  const moved = loginFiles();
  let login;
  if (stage === 0) login = bundle('login', 'eng-42-login/', ['handoff.md', 'plan.md', 'run.log'], 'live', 'live', 'Its worktree app-login is open.');
  else if (settings.cleanup !== 'prune') login = bundle('login', 'eng-42-login/', ['handoff.md', 'plan.md', 'run.log'], 'live', 'live', 'Its worktree is still on disk, so the docs stay live.');
  else if (!settings.docs) login = bundle('login', 'eng-42-login/', ['handoff.md', 'plan.md', 'run.log'], 'kept', 'linked · kept', 'eng-42 landed, but no doc_strategy is saved, so nothing moves.');
  else if (stage < trashed) login = bundle('login', 'eng-42-login/', ['handoff.md', 'plan.md', 'run.log'], 'warn', 'linked · grace', `eng-42 landed and no worktree carries it. Day 0 of ${settings.grace} days of grace.`);
  else if (stage < 3) login = bundle('login', 'eng-42-login/', moved, 'trash', 'in trash', `Moved to .git/lumberjack-trash/${STAMPS[trashed]}/. Recoverable for 30 days.${moved.length < 3 ? ' run.log stays in docs/work.' : ''}`);
  else login = bundle('login', 'eng-42-login/', moved, 'gone', 'purged', `Deleted from the trash after 30 days.${moved.length < 3 ? ' run.log is still in docs/work.' : ''}`);
  const billing = stage === 0 ? bundle('billing', 'eng-44-billing/', ['plan.md'], 'live', 'live', 'Its worktree app-billing is open.')
    : bundle('billing', 'eng-44-billing/', ['plan.md'], 'live', 'live', 'eng-44 landed, but its blocked worktree still carries the ticket.');
  return [
    login,
    bundle('research', 'eng-42-research/', ['findings.md'], 'kept', 'reference', 'Research is reference material. It is never trashed.'),
    bundle('search', 'eng-43-search/', ['handoff.md'], 'live', 'live', 'Worktree app-search carries eng-43.'),
    billing,
    bundle('ui', 'eng-45-ui/', ['handoff.md'], 'live', 'live', 'Worktree app-ui carries eng-45.'),
    bundle('notes', 'scratch-notes/', ['ideas.md'], 'kept', 'unlinked', 'No ticket in its name or a Tickets: line, so nothing links it to landed work.'),
  ];
}

function sweepLines(stage) {
  const mode = settings.cleanup;
  const merged = stage >= 1;
  const counts = { cleaned: 0, landed: 0, dependents: 0, blocked: 0, unverified: 0, docs: 0 };
  const events = [];
  if (!merged) counts.unverified = 4;
  else {
    counts.landed = stage === 1 ? 3 : 2;
    counts.unverified = stage === 1 ? 2 : 3;
    if (mode !== 'keep') {
      counts.dependents = 1;
      events.push(DEPENDENT);
      if (mode === 'prune' && stage === 1) { counts.cleaned = 1; events.push(`cleaned ${LOGIN} (local branch kept)`); }
      if (mode === 'report') events.push(`landed ${LOGIN}: would remove`);
      events.push('blocked claude/eng-44-billing ~/worktrees/app-billing: dirty');
      counts.blocked = 1;
      if (stage === 1) { events.push('blocked claude/eng-46-docs ~/worktrees/app-docs: fresh'); counts.blocked = 2; }
    }
  }
  const trashed = trashStage();
  if (merged && mode === 'prune') {
    if (!settings.docs || stage < trashed) events.push('doc ephemeral docs/work/eng-42-login: tickets eng-42; 14.2 KB');
    if (stage === trashed) { counts.docs = 1; events.push(`cleared docs/work/eng-42-login: trashed ${STAMPS[trashed]}`); }
    if (trashed !== undefined && stage === 3) events.push(`purged ${STAMPS[trashed]}`);
  }
  const summary = `sweep: cleaned ${counts.cleaned}, landed ${counts.landed}, dependents ${counts.dependents}, blocked ${counts.blocked}, unverified ${counts.unverified}, orphans 0, docs ${counts.docs}`;
  const would = mode === 'report' ? [`sweep would: remove ${merged ? '1 worktree' : '0 worktrees'}, delete 0 orphans, trash 0 bundles, purge 0 trash folders`] : [];
  return [summary, ...would, ...events];
}

function story(stage) {
  const mode = settings.cleanup;
  if (stage === 0) return 'Day 0. Six worktrees are open. Nothing has merged, so the sweep removes nothing.';
  if (mode === 'keep') return `Day ${DAYS[stage]}. cleanup = "keep": the sweep counts what landed and changes nothing.`;
  if (mode === 'report') return `Day ${DAYS[stage]}. cleanup = "report": the sweep prints what it would remove and leaves everything on disk. The eng-42 docs stay live because their worktree is still there.`;
  const trashed = trashStage();
  if (stage === 1) {
    const docs = trashed === 1 ? 'With 0 days of grace, its docs go straight to the trash.' : settings.docs ? `Its docs start ${settings.grace} days of grace.` : 'No doc_strategy is saved, so its docs stay.';
    return `Day 1. eng-42 was squash-merged and eng-44 merged. The sweep removes app-login and keeps its branch. app-billing is dirty and app-docs is brand new, so both stay. ${docs}`;
  }
  if (stage === 2) return trashed === 2 ? 'Day 8. Grace is over: the eng-42 docs move to the recoverable trash inside .git/.' : trashed === 1 ? 'Day 8. The eng-42 docs have been in the trash since day 1. app-docs now has its own commits.' : 'Day 8. No doc_strategy is saved, so the eng-42 docs are still listed and still in place.';
  return trashed !== undefined ? 'Day 38. The trash is older than 30 days, so the sweep purges it. Everything else is unchanged.' : 'Day 38. Nothing new to clean: unfinished work, research and unlinked notes stay.';
}

function changedIds(now, before) {
  if (!before) return new Set();
  const old = new Map(before.map(item => [item.id, item.chip + item.files]));
  return new Set(now.filter(item => old.get(item.id) !== item.chip + item.files).map(item => item.id));
}

function rowHtml(item, changed, detail) {
  return `<li class="sim-row kind-${item.kind}${changed ? ' changed' : ''}"><div class="row-main"><code class="row-name">${item.name}</code><span class="chip">${item.chip}</span>${changed ? '<span class="changed-mark">changed</span>' : ''}</div><div class="row-detail">${detail}</div><p class="row-reason">${item.reason}</p></li>`;
}

function renderSimulation() {
  const stage = state.stage;
  const trees = worktrees(stage);
  const docs = docBundles(stage);
  const treeChanges = changedIds(trees, stage ? worktrees(stage - 1) : undefined);
  const docChanges = changedIds(docs, stage ? docBundles(stage - 1) : undefined);
  $('#tree-rows').innerHTML = trees.map(t => rowHtml(t, treeChanges.has(t.id), `<code>${t.branch}</code>`)).join('');
  $('#doc-rows').innerHTML = docs.map(d => rowHtml(d, docChanges.has(d.id), `<code>${d.files.join('  ')}</code>`)).join('');
  $('#trees-changed').textContent = treeChanges.size ? `· ${treeChanges.size} changed` : '';
  $('#docs-changed').textContent = docChanges.size ? `· ${docChanges.size} changed` : '';
  const lines = sweepLines(stage);
  $('#sweep-log').innerHTML = lines.map(line => `<span class="log-${line.split(/[ :]/)[0]}">${line}</span>`).join('\n');
  $('#log-title').textContent = `SESSION START, DAY ${DAYS[stage]}`;
  $('#sim-story').textContent = story(stage);
  $('#next-sweep').textContent = NEXT_LABELS[stage];
  document.querySelectorAll('[data-stage]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.stage) === stage)));
  $('#settings-summary').textContent = `${settings.cleanup}${settings.docs ? ` · ${settings.grace}-day grace${settings.scope === 'markdown' ? ' · .md only' : ''}` : ' · no doc strategy'}`;
  $('#doc-scope').disabled = !settings.docs;
  $('#grace-days').disabled = !settings.docs;
}

function showStage(stage) {
  state.stage = stage;
  renderSimulation();
  $('#sim-announcement').textContent = story(stage);
}

$('#next-sweep').addEventListener('click', () => showStage((state.stage + 1) % 4));
document.querySelectorAll('[data-stage]').forEach(button => button.addEventListener('click', () => showStage(Number(button.dataset.stage))));

const controls = { 'cleanup-mode': 'cleanup', 'docs-enabled': 'docs', 'doc-scope': 'scope', 'grace-days': 'grace' };
const readControl = { cleanup: v => v, docs: v => v === 'yes', scope: v => v, grace: Number };
Object.entries(controls).forEach(([id, key]) => {
  $(`#${id}`).addEventListener('change', event => { settings[key] = readControl[key](event.target.value); renderSimulation(); });
});
$('#reset-settings').addEventListener('click', () => {
  Object.assign(settings, DEFAULTS);
  $('#cleanup-mode').value = 'prune'; $('#docs-enabled').value = 'yes'; $('#doc-scope').value = 'bundle'; $('#grace-days').value = '7';
  renderSimulation();
});
$('#settings-form').addEventListener('submit', event => event.preventDefault());

// The Stop gate example: the reasons are the gate's own lines for owners = ["claude", "codex"].
const GATE_REASONS = {
  named: [],
  misnamed: ['gate: branch fix-login violates the lumberjack skill naming; rename it: git branch -m <owner>/eng-<n>-<slug>'],
  primary: ['gate: the primary checkout is on codex/eng-42-login; move the work to a worktree and return the primary to main'],
};
const gate = { where: 'named' };
function renderGate() {
  const reasons = $('#gate-enabled').checked ? GATE_REASONS[gate.where] : [];
  const text = reasons.length
    ? `Stop attempt 1: blocked\n${reasons.join('\n')}\n\nStop attempt 2: allowed. The gate blocks once per turn, so the agent fixes it or tells you.`
    : $('#gate-enabled').checked ? 'Stop attempt 1: allowed. The branch is well named and the work is in a worktree.'
      : 'Stop attempt 1: allowed. stop_gate = false turns the gate off.';
  $('#gate-output').textContent = text;
  $('#gate-announcement').textContent = text.split('\n')[0];
  document.querySelector('.gate-toggle code').textContent = `stop_gate = ${$('#gate-enabled').checked}`;
}
$('#gate-enabled').addEventListener('change', renderGate);

function wireTabs(selector, activate, selectedAttribute = 'aria-selected') {
  const tabs = [...document.querySelectorAll(selector)];
  function select(tab, focus = false) {
    tabs.forEach(item => { const active = item === tab; item.setAttribute(selectedAttribute, String(active)); item.tabIndex = active ? 0 : -1; });
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
wireTabs('[data-gate]', tab => { gate.where = tab.dataset.gate; renderGate(); }, 'aria-checked');
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
renderGate();
