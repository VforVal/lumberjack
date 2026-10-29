// These are deliberately illustrative repository states. No local files are read.
const $ = selector => document.querySelector(selector);
const settings = { cleanup: 'prune', docs: true, scope: 'bundle', grace: 7, gate: true };
const state = { scenario: 'merged', stage: 0 };
const TRASH_STAMP = '20261105T090000Z';

function currentFlow(stage = state.stage) {
  const landed = stage > 0 && state.scenario !== 'unverified';
  const clean = state.scenario !== 'dirty';
  const removed = landed && clean && settings.cleanup === 'prune';
  const graceElapsed = stage >= 2 || (stage >= 1 && settings.grace === 0);
  const docsMoved = removed && settings.docs && graceElapsed;
  return { landed, clean, removed, docsMoved, purged: docsMoved && stage === 3, graceElapsed };
}

function renderWorktree() {
  const { landed, clean, removed, docsMoved, purged } = currentFlow();
  const removable = landed && clean;
  $('#target-row').classList.toggle('removed', removed);
  $('#target-row').classList.toggle('blocked', state.stage > 0 && !removable);
  $('#target-row').setAttribute('aria-hidden', String(removed));
  $('#empty-slot').hidden = !removed;
  $('#target-status').textContent = state.stage === 0 ? 'in progress' : !landed ? 'unverified' : clean ? 'merged · clean' : 'merged · dirty';
  const evidence = [
    [landed, landed ? 'Exact tip verified landed' : state.stage === 0 ? 'Work has not landed yet' : 'No proof this tip landed'],
    [clean, clean ? 'No uncommitted files' : 'Uncommitted changes in this worktree'],
    [true, 'Tip pushed; no untracked docs'],
  ];
  $('#evidence-list').innerHTML = evidence.map(([ok, text]) => `<li class="${ok ? '' : 'failed'}"><span aria-hidden="true">${ok ? '✓' : '×'}</span>${text}</li>`).join('');
  let note = 'Work in progress. The worktree and its documents stay.';
  let output = 'Waiting for the merge.';
  if (state.stage > 0 && !removable) note = clean ? 'No landing proof. The worktree and its docs stay live.' : 'Merged, but dirty. The worktree and its docs stay live.';
  if (settings.cleanup === 'report') note += ' Report mode: nothing is removed.';
  if (settings.cleanup === 'keep') note = 'Cleanup is set to keep. Everything stays.';
  if (state.stage > 0) {
    const tag = 'codex/eng-42-login ~/worktrees/app-login';
    const prunes = settings.cleanup !== 'keep';
    const blocked = prunes && landed && !clean;
    const trashedNow = docsMoved && !currentFlow(state.stage - 1).docsMoved;
    const lines = [summaryLine({ cleaned: removed && state.stage === 1 ? 1 : 0, landed: landed ? 1 : 0, blocked: blocked ? 1 : 0, unverified: landed ? 0 : 1, docs: trashedNow ? 1 : 0 })];
    if (settings.cleanup === 'report') lines.push(`sweep would: remove ${landed && clean ? 1 : 0} worktree${landed && clean ? '' : 's'}, delete 0 orphans, trash 0 bundles, purge 0 trash folders`);
    if (blocked) lines.push(`blocked ${tag}: dirty`);
    else if (prunes && landed && settings.cleanup === 'report') { lines.push(`landed ${tag}: would remove`); note = 'Report mode: the worktree stays on disk, keeping its docs live.'; }
    else if (removed) {
      if (state.stage === 1) lines.push(`cleaned ${tag} (local branch kept)`);
      if (trashedNow) lines.push(`cleared docs/work/eng-42-login: trashed ${TRASH_STAMP}`);
      if (purged) lines.push(`purged ${TRASH_STAMP}`);
      note = 'The landed worktree is gone. Follow its docs in the next example.';
    }
    output = lines.join('\n');
  }
  $('#worktree-note').textContent = note;
  $('#worktree-output').textContent = output;
  const steps = ['Merge & sweep →', 'Advance past grace →', 'Advance past retention →', 'Start over ↺'];
  $('#run-worktree').textContent = steps[state.stage];
  $('#worktree-announcement').textContent = state.stage > 0 ? `${note} ${output}` : '';
}

function summaryLine({ cleaned, landed, blocked, unverified, docs }) {
  return `sweep: cleaned ${cleaned}, landed ${landed}, dependents 0, blocked ${blocked}, unverified ${unverified}, orphans 0, docs ${docs}`;
}

function bundle(title, tag, files, extra = '') {
  return `<div class="file-bundle ${extra}"><strong>${title}/</strong><small>${tag}</small>${files.length ? `<span class="file-list">${files.join('<br>')}</span>` : ''}</div>`;
}

function renderDocs() {
  const { landed, removed, graceElapsed, docsMoved: moved, purged } = currentFlow();
  const markdownOnly = settings.scope === 'markdown';
  const candidateFiles = markdownOnly ? ['handoff.md', 'plan.md'] : ['handoff.md', 'plan.md', 'run.log'];
  let working = '';
  if (!moved) working += bundle('eng-42-login', !removed ? 'live · worktree still present' : !settings.docs ? 'kept · no strategy' : `linked · inside ${settings.grace}-day grace`, ['handoff.md', 'plan.md', 'run.log']);
  else if (markdownOnly) working += bundle('eng-42-login', 'non-Markdown file stays', ['run.log']);
  working += bundle('eng-42-research', 'reference · kept', ['findings.md'], 'reference-bundle');
  working += bundle('eng-43-search', 'live · another ticket still open', ['handoff.md'], 'reference-bundle');
  $('#working-files').innerHTML = working;
  $('#trash-files').innerHTML = moved && !purged ? bundle('eng-42-login', 'trashed · retained for 30 days', candidateFiles) : '<p class="empty-files">Nothing here.</p>';
  $('#purged-files').innerHTML = purged ? bundle('eng-42-login', 'permanently deleted from trash', candidateFiles) : '<p class="empty-files">Nothing past retention.</p>';
  let description;
  if (settings.cleanup === 'keep') description = 'Cleanup is set to keep. Documents stay where they are; the sweep neither trashes nor purges them.';
  else if (!settings.docs) description = 'No document strategy is saved. Nothing is trashed or purged, even after the ticket lands.';
  else if (!removed) description = state.stage === 0 ? 'eng-42 is still in progress. Its worktree and work documents stay. Research and the open eng-43 handoff stay, too.' : settings.cleanup === 'report' ? 'Report mode would remove the landed worktree but leaves it on disk. That worktree still carries eng-42, so its documents stay live and are not offered for trash.' : !landed ? 'The worktree has no verified landing. Its eng-42 documents stay live with it, even as time passes.' : 'The dirty worktree still carries eng-42. Its documents stay live until that worktree can be removed; grace has not started.';
  else if (!graceElapsed) description = `The sweep first sees eng-42 linked with no live work. Its ${settings.grace}-day grace period starts here. Nothing moves yet.`;
  else if (purged) description = `After more than 30 days in trash, the next sweep purges ${candidateFiles.length} files. ${markdownOnly ? 'run.log stays in the doc root. ' : ''}Research and the live handoff are still kept.`;
  else description = `${candidateFiles.length} files move to .git/lumberjack-trash after the grace period. ${markdownOnly ? 'Markdown scope leaves run.log in place. ' : 'Bundle scope includes run.log. '}They can be recovered until a later sweep purges trash past the 30-day retention.`;
  $('#docs-stage-description').textContent = description;
  $('#docs-scenario').textContent = ({ merged: 'Clean worktree', dirty: 'Dirty worktree', unverified: 'No landing proof' })[state.scenario];
}

function renderConfig() {
  const lines = [
    '# Example repository settings',
    'owners = ["claude", "codex"]',
    'ticket_prefix = "eng-"',
    'primary_branch = "main"',
    'doc_roots = ["docs/work"]',
    `cleanup = "${settings.cleanup}"`,
    `stop_gate = ${settings.gate}`,
  ];
  if (settings.docs) lines.push('', '[doc_strategy]', `scope = "${settings.scope}"`, `grace_days = ${settings.grace}`, 'trash_days = 30');
  else lines.push('', '# No doc_strategy saved: keep docs');
  $('#config-code').textContent = lines.join('\n');
  const mode = settings.cleanup === 'prune' ? 'Eligible worktrees are removed; eligible work documents move to trash.' : settings.cleanup === 'report' ? 'The sweep prints what it would do. Nothing is removed, trashed, or purged.' : 'Automatic cleanup keeps worktrees and documents in place.';
  const docs = !settings.docs ? 'No saved document strategy: no doc trashing or purging.' : `${settings.scope === 'markdown' ? 'Only .md files move; run.log stays.' : 'Whole bundles move, including run.log.'} Grace: ${settings.grace} days. Trash retention: 30 days.`;
  const gate = settings.gate ? 'Stop gate: a misnamed branch or off-branch primary checkout blocks the agent’s first Stop attempt.' : 'Stop gate is off. It does not block the agent’s Stop attempt.';
  $('#config-effects').innerHTML = `<p>${mode}</p><p>${docs}</p><p>${gate}</p>`;
  $('#doc-scope').disabled = !settings.docs;
  $('#grace-days').disabled = !settings.docs;
  const { removed, docsMoved, purged } = currentFlow();
  let impact = 'Worktree and documents stay: the task is still in progress.';
  if (state.stage > 0) {
    if (!removed) impact = 'Worktree stays. Its documents stay live, so their grace period has not started.';
    else if (!settings.docs) impact = 'Worktree removed. Documents stay because no document strategy is saved.';
    else if (purged) impact = 'Worktree removed. Work documents purged after trash retention. Research stays.';
    else if (docsMoved) impact = `Worktree removed. ${settings.scope === 'markdown' ? 'Two Markdown files move to trash; run.log stays.' : 'Three files move to trash.'} Research stays.`;
    else impact = `Worktree removed. Work documents wait through their ${settings.grace}-day grace period.`;
  }
  $('#settings-impact').textContent = impact;
  document.querySelectorAll('[data-open-settings]').forEach(button => { button.title = `Shared settings: ${settings.cleanup}; ${settings.docs ? settings.scope + ', ' + settings.grace + '-day grace' : 'no document strategy'}`; });
}

function render() {
  const captions = ['Work in progress', 'First sweep after landing', `After ${settings.grace}-day grace`, 'Trash older than 30 days'];
  document.querySelectorAll('[data-lifecycle]').forEach(slider => { slider.value = state.stage; slider.setAttribute('aria-valuetext', captions[state.stage]); });
  document.querySelectorAll('[data-stage-caption]').forEach(caption => { caption.textContent = captions[state.stage]; });
  renderWorktree(); renderDocs(); renderConfig();
}
$('#run-worktree').addEventListener('click', () => { state.stage = (state.stage + 1) % 4; render(); });
document.querySelectorAll('[data-lifecycle]').forEach(slider => slider.addEventListener('input', event => { state.stage = Number(event.target.value); render(); }));
const controls = { 'cleanup-mode': 'cleanup', 'docs-enabled': 'docs', 'doc-scope': 'scope', 'grace-days': 'grace', 'stop-gate-toggle': 'gate' };
Object.entries(controls).forEach(([id, key]) => {
  $(`#${id}`).addEventListener('change', event => {
    settings[key] = key === 'docs' ? event.target.value === 'yes' : key === 'grace' ? Number(event.target.value) : key === 'gate' ? event.target.checked : event.target.value;
    render();
  });
});
$('#reset-settings').addEventListener('click', () => {
  Object.assign(settings, { cleanup: 'prune', docs: true, scope: 'bundle', grace: 7, gate: true });
  $('#cleanup-mode').value = 'prune'; $('#docs-enabled').value = 'yes'; $('#doc-scope').value = 'bundle'; $('#grace-days').value = '7'; $('#stop-gate-toggle').checked = true;
  render();
});

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
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); select(tabs[next], true); }
    });
  });
}
wireTabs('[data-scenario]', tab => {
  state.scenario = tab.dataset.scenario;
  $('#worktree-example').setAttribute('aria-labelledby', tab.id);
  render();
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
$('#settings-form').addEventListener('submit', event => event.preventDefault());

const popover = $('#example-settings');
const nativePopover = Object.hasOwn(HTMLElement.prototype, 'popover');
let settingsTrigger;
function settingsOpen() {
  return nativePopover ? popover.matches(':popover-open') : !popover.hidden;
}
function positionSettings() {
  if (!settingsTrigger || !settingsOpen()) return;
  const anchor = settingsTrigger.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  popover.style.maxWidth = `${viewportWidth - 24}px`;
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  popover.style.left = `${Math.max(12, Math.min(anchor.right - width, viewportWidth - width - 12))}px`;
  popover.style.top = `${Math.max(12, Math.min(anchor.bottom + 8, window.innerHeight - height - 12))}px`;
}
document.querySelectorAll('[data-open-settings]').forEach(button => button.addEventListener('click', () => {
  settingsTrigger = button;
  $('#settings-context').textContent = button.dataset.openSettings === 'documents' ? 'DOCUMENTS + WORKTREES' : 'WORKTREES + DOCUMENTS';
}));
popover.addEventListener('toggle', event => { if (event.newState === 'open') positionSettings(); });
// Browsers without the Popover API (Safari before 17, Firefox before 125) get the same open and close.
if (!nativePopover) {
  popover.hidden = true;
  document.querySelectorAll('[popovertarget]').forEach(button => button.addEventListener('click', () => {
    popover.hidden = button.getAttribute('popovertargetaction') === 'hide' || !popover.hidden;
    positionSettings();
  }));
  document.addEventListener('keydown', event => { if (event.key === 'Escape') popover.hidden = true; });
}
window.addEventListener('resize', positionSettings);
new ResizeObserver(positionSettings).observe(popover);
render();
