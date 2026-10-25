'use strict';
(() => {
  const invoke = window.__TAURI__?.core.invoke;
  const $ = id => document.getElementById(id);
  const pages = ['Local devices', 'Remote devices', 'Connections', 'Activity', 'System', 'Settings'];
  let view = null, page = 0, selected = null, detail = null, detailKey = null;
  let ruleDraft = null, ruleEditor = null, preferencesForm = null;
  let waiting = null, dispatching = false, query = '', eligible = false, showDetails = false, destroyed = false;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  };
  const notice = message => { $('notice').textContent = String(message); $('notice').hidden = false; };
  const busy = () => dispatching || waiting !== null;
  async function action(command) {
    if (busy()) { notice('An accepted action is still pending. Wait for its result.'); return; }
    dispatching = true; updateControls(); $('notice').hidden = true;
    try { waiting = await invoke('gui_action', { action: command }); }
    catch (error) { notice(error); }
    finally { dispatching = false; updateControls(); }
  }
  function updateControls() {
    const disabled = busy() || !view;
    $('refresh').disabled = disabled;
    $('profile').disabled = disabled || !view?.profiles?.length;
    $('start').disabled = disabled || !view?.profile;
    $('start').textContent = view?.unresolved ? 'Resolve original start' : 'Start profile';
    document.querySelectorAll('[data-action]').forEach(button => { button.disabled = disabled || button.dataset.unavailable === 'true'; });
  }
  async function confirm(title, description, command) {
    if (busy()) return;
    $('confirm-title').textContent = title; $('confirm-description').textContent = description;
    const dialog = $('confirmation'); dialog.returnValue = 'cancel';
    dialog.showModal();
    dialog.querySelector('[value="cancel"]').focus();
    await new Promise(resolve => dialog.addEventListener('close', resolve, { once: true }));
    if (dialog.returnValue === 'confirm') { if (typeof command === 'function') command(); else await action(command); }
  }
  const button = (label, callback, className) => {
    const item = node('button', label, className); item.type = 'button'; item.dataset.action = 'true';
    item.addEventListener('click', callback); item.disabled = busy(); return item;
  };
  function table(headers, rows) {
    const result = node('table'); const head = node('thead'); const tr = node('tr');
    headers.forEach(label => { const th = node('th', label); th.scope = 'col'; tr.append(th); });
    head.append(tr); result.append(head); const body = node('tbody');
    rows.forEach(row => { const tr = node('tr'); row.forEach(cell => { const td = node('td'); td.append(cell instanceof Node ? cell : node('span', cell ?? 'Unavailable')); tr.append(td); }); body.append(tr); });
    result.append(body); return result;
  }
  const empty = text => node('p', text, 'empty');
  const json = value => { const pre = node('pre', JSON.stringify(value, null, 2), 'json-detail'); pre.tabIndex = 0; pre.setAttribute('aria-label', 'Metadata details'); return pre; };
  const matches = value => JSON.stringify(value).toLowerCase().includes(query.toLowerCase());
  function renderInventory() {
    const local = page === 0, snapshot = local ? view.local : view.remote;
    const devices = snapshot?.devices;
    const items = devices?.filter(d => matches(d) && (!local || !eligible || d.eligible));
    $('inventory-count').textContent = devices ? `${items.length} of ${devices.length} observed` : 'Not sampled';
    const content = $('content'); const layout = node('div', null, `inventory-layout${showDetails ? ' show-details' : ''}`);
    const list = node('div', null, 'table-wrap'); const panel = node('aside', null, 'detail'); panel.setAttribute('aria-label', 'Selected device details');
    if (!devices) list.append(empty(local ? 'Waiting for local inventory. See the observation status below.' : 'No remote inventory sampled. A client profile and Discover permission are required.'));
    else if (!items.length) list.append(empty(devices.length ? 'No devices match this filter.' : 'The latest completed observation contains no devices.'));
    else {
      const rows = items.map(d => {
        const key = local ? d.key : `${snapshot.instance}:${d.device_key}:${d.device.bus_id}:${d.device.device_number}`;
        const name = node('div'); const select = node('button', local ? d.name : d.device_key, 'device-name');
        select.type = 'button'; select.dataset.focus = key; select.setAttribute('aria-pressed', String(selected === key));
        select.addEventListener('click', () => { selected = key; detail = local ? null : d; detailKey = local ? null : key; renderPage(); if (local) loadDetail(key); });
        name.append(select, node('span', local ? d.vid_pid : d.device_key, 'subline'));
        const state = local ? (d.exported ? 'Exported' : d.eligible ? 'Eligible candidate' : 'Review blockers') : 'Advertised only';
        const badge = node('span', state, `badge${local && !d.eligible ? ' warn' : ''}`);
        return [name, local ? d.bus_id : d.device.bus_id, local ? `${d.speed} Mbit/s` : d.device.device_number, badge];
      });
      const grid = table(['Device', 'Bus ID', local ? 'Speed' : 'Address', 'Observation'], rows);
      [...grid.tBodies[0].rows].forEach((row, i) => { const key = local ? items[i].key : `${snapshot.instance}:${items[i].device_key}:${items[i].device.bus_id}:${items[i].device.device_number}`; row.classList.toggle('selected', selected === key); });
      list.append(grid);
    }
    panel.append(node('h2', 'Device details'));
    const current = devices?.some(d => (local ? d.key : `${snapshot.instance}:${d.device_key}:${d.device.bus_id}:${d.device.device_number}`) === selected);
    if (!current) { selected = null; detail = null; detailKey = null; panel.append(empty('Select an observed device to inspect its descriptors and generation.')); }
    else if (detailKey === selected && detail) panel.append(json(detail));
    else panel.append(empty('Reading bounded metadata…'));
    panel.append(node('p', 'Selecting a row does not change the fixed devices in the administrator’s profile.', 'hint'));
    layout.append(list, panel); content.append(layout);
    $('section-status').textContent = local ? view.local_status : view.remote_status;
    $('eligible').closest('label').hidden = !local;
  }
  async function loadDetail(key) {
    try {
      const result = await invoke('gui_detail', { key });
      if (selected === key && page === 0) { detail = result; detailKey = key; renderPage(); }
    } catch (error) { if (selected === key) { detail = { status: String(error) }; detailKey = key; renderPage(); } }
  }
  function pager(section, next) {
    const bar = node('div', null, 'pager');
    bar.append(button('First page', () => action({ action: 'page', section, next: false })), button('Next page', () => action({ action: 'page', section, next: true })));
    unavailable(bar.lastChild, !next); return bar;
  }
  function renderConnections(disclosures) {
    const content = $('content');
    for (const [title, items, section, status] of [['Jobs', view.jobs, 'jobs', view.jobs_status], ['Device sessions', view.sessions, 'sessions', view.sessions_status]]) {
      const block = node('section', null, 'block'); block.append(node('h2', title), node('p', status, 'section-note'));
      if (!items.length) block.append(empty('No records in this sampled page.'));
      else block.append(table(['Identity', 'State / evidence', 'Result / cleanup', 'Action'], items.map(item => {
        const identity = node('div', item.id, 'data-id'); identity.append(node('span', item.device_key || item.profile, 'subline'));
        const state = node('div', item.state); if (item.evidence) state.append(node('span', `${item.role} · ${item.evidence}`, 'subline'));
        const result = node('div', `Error: ${item.operation_error || 'none observed'}`); if (section === 'sessions') result.append(node('span', `Cleanup: ${item.cleanup || 'not reported'}`, 'subline'));
        const stop = button('Request stop', () => confirm('Request cooperative stop?', `Exact ${section === 'jobs' ? 'job' : 'session'} ${item.id}. Acceptance does not prove device release.`, { action: section === 'jobs' ? 'stop_job' : 'stop_session', id: item.id }));
        return [identity, state, result, stop];
      })));
      block.append(pager(section, section === 'jobs' ? view.next_job : view.next_session)); content.append(block);
    }
    const health = node('section', null, 'block'); health.append(node('h2', 'Data-link health'), node('p', 'Independent link samples; raw-v1 has no heartbeat RTT. A transport reply does not prove USB completion.', 'section-note'));
    if (!view.health.length) health.append(empty('No link-health sample available.'));
    else view.health.forEach(item => {
      const key = JSON.stringify([view.profile, item.instance_id, item.session_id, item.link?.identity?.connection ?? null]);
      const details = node('details'); details.dataset.healthKey = key; details.open = disclosures.get(key)?.open ?? false;
      const historical = item.availability === 'owner_ended' || item.availability === 'previous_epoch';
      const qualification = historical ? `retained history (${item.availability})` : item.availability;
      const linkState = item.link?.protocol === 'raw_v1' ? 'raw-v1 probes unavailable' : [item.link?.phase, item.link?.probe, item.link?.end].filter(Boolean).join(' · ');
      const summary = node('summary', `${item.session_id} · ${qualification}${linkState ? ` · ${linkState}` : ''}`);
      summary.dataset.focus = `health-summary:${key}`;
      const recorded = historical || item.link?.phase === 'ended';
      const rtt = item.link?.last_round_trip_micros == null ? 'RTT unavailable' : `${recorded ? 'Last recorded' : 'Last'} enqueue-to-reply RTT: ${item.link.last_round_trip_micros} µs${recorded ? ' (not a current responsiveness result)' : ''}`;
      const metadata = json(item); metadata.dataset.focus = `health-metadata:${key}`;
      details.append(summary, node('p', rtt, 'section-note'), metadata); health.append(details);
    });
    content.append(health); $('section-status').textContent = 'Jobs, sessions and link health are sampled independently';
    health.querySelectorAll('details[data-health-key]').forEach(details => { details.querySelector('pre').scrollTop = disclosures.get(details.dataset.healthKey)?.scrollTop ?? 0; });
  }
  function renderActivity() {
    const content = $('content'); content.append(node('p', view.audit_status, 'section-note'));
    if (!view.audit.length) content.append(empty('No audit entries in this sampled page. Audit permission is required.'));
    else content.append(table(['Sequence', 'Action', 'Actor UID', 'State / error', 'Identity'], view.audit.map(item => [item.sequence, item.action, item.actor_uid, `${item.state || '—'} / ${item.failure || '—'}`, node('span', item.session_id || item.job_id || 'No related identity', 'data-id')])));
    content.append(pager('audit', view.next_audit)); $('section-status').textContent = view.audit_status;
  }
  function renderSystem() {
    const list = node('dl', null, 'system-list');
    const facts = [['Application', 'RustUSB · native Linux console'], ['Process privilege', 'Ordinary user; the agent independently authorizes every IPC operation'], ['Agent', view.status], ['Journal', view.journal === null ? 'Not sampled' : view.journal ? 'Last response healthy' : 'Unhealthy; new admission refused'], ['Local observation', view.local_status], ['Remote observation', view.remote_status], ['Refresh', view.options.auto_refresh ? `After completion + ${view.options.refresh_seconds}s` : 'Automatic service polling paused'], ['USB ownership', 'Agent jobs continue after this window closes'], ['Compatibility', 'Discovery is metadata. Use the documented device-specific test evidence.']];
    facts.forEach(([term, value]) => list.append(node('dt', term), node('dd', value))); $('content').append(list); $('section-status').textContent = 'Current frontend observations; no hardware compatibility claim';
  }
  function unavailable(item, value) {
    item.dataset.unavailable = String(value); item.disabled = busy() || value; return item;
  }
  function ruleError(message, field) {
    const summary = $('rule-errors');
    if (summary) { summary.textContent = message; summary.hidden = false; summary.focus(); }
    else notice(message);
    if (field) field.setAttribute('aria-invalid', 'true');
  }
  function newRuleDraft(rule = null) {
    const grant = view.rules.grant;
    ruleDraft = { profile: view.profile, id: rule?.id || null, revision: rule?.revision || null,
      spec: rule ? { ...rule.spec } : { name: '', trigger: grant?.triggers[0] || 'local_profile_ready', settle_seconds: '2', rearm: grant?.rearm[0] || 'once' } };
    ruleEditor = null; renderPage(); $('rule-name')?.focus();
  }
  function renderRuleEditor() {
    if (!ruleDraft) return null;
    if (ruleEditor) return ruleEditor;
    const form = node('form', null, 'settings rule-editor'); form.noValidate = true;
    const errors = node('p', '', 'unresolved'); errors.id = 'rule-errors'; errors.dataset.focus = 'rule-errors'; errors.tabIndex = -1; errors.hidden = true; errors.setAttribute('role', 'alert');
    form.append(node('h3', ruleDraft.id ? 'Edit definition' : 'New disabled rule'),
      node('p', `Draft profile: ${ruleDraft.profile}${ruleDraft.id ? ` · Rule ${ruleDraft.id} · Original revision ${ruleDraft.revision}` : ''}. Drafts remain in this window; refresh never rebases them.`, 'section-note'), errors);
    const fields = {};
    for (const [key, title, choices] of [['name', 'Rule name (1–64 UTF-8 bytes)', null], ['trigger', 'Condition', ['local_profile_ready', 'remote_device_available']], ['settle_seconds', 'Settle interval (2–120 seconds)', null], ['rearm', 'Rearm policy', ['once', 'new_observation']]]) {
      const input = node(choices ? 'select' : 'input'); input.id = `rule-${key === 'settle_seconds' ? 'settle' : key}`; input.dataset.focus = input.id;
      if (choices) choices.forEach(value => { const option = node('option', value); option.value = value; input.append(option); });
      else { input.type = 'text'; input.maxLength = key === 'name' ? 64 : 3; if (key !== 'name') input.inputMode = 'numeric'; }
      input.value = ruleDraft.spec[key]; fields[key] = input;
      input.setAttribute('aria-describedby', 'rule-errors');
      input.addEventListener('input', () => { ruleDraft.spec[key] = input.value; input.removeAttribute('aria-invalid'); errors.hidden = true; });
      const label = node('label', title); label.htmlFor = input.id; label.append(input); form.append(label);
    }
    const submit = button(ruleDraft.id ? 'Save definition' : 'Create disabled rule', () => {
      const spec = { ...ruleDraft.spec };
      if (!spec.name || spec.name.trim() !== spec.name || new TextEncoder().encode(spec.name).length > 64 || /[\u0000-\u001f\u007f-\u009f]/u.test(spec.name)) { ruleError('Enter a name of 1–64 UTF-8 bytes, without outer spaces or control characters.', fields.name); return; }
      if (!/^[0-9]{1,3}$/.test(spec.settle_seconds) || Number(spec.settle_seconds) < 2 || Number(spec.settle_seconds) > 120) { ruleError('Settle interval must be 2–120 whole seconds.', fields.settle_seconds); return; }
      if (ruleDraft.profile !== view.profile) { ruleError('This draft belongs to another profile. Return to its profile or choose New disabled rule.'); return; }
      if (!view.rules.current) { ruleError('Rule metadata is stale. Refresh the selected profile before submitting.'); return; }
      if (ruleDraft.id && !view.rules.rules.some(r => r.id === ruleDraft.id && r.revision === ruleDraft.revision && !r.deleted)) { ruleError('The rule revision changed. Your draft is retained. Inspect the latest definition before choosing Edit definition again.'); return; }
      spec.settle_seconds = Number(spec.settle_seconds);
      const command = ruleDraft.id ? { action: 'rule_replace', id: ruleDraft.id, revision: ruleDraft.revision, spec } : { action: 'rule_create', spec };
      confirm(ruleDraft.id ? 'Replace this rule definition?' : 'Create a disabled rule?', `Complete fixed profile ${ruleDraft.profile}. ${ruleDraft.id ? `Rule ${ruleDraft.id}, revision ${ruleDraft.revision}. Replacing an enabled rule can change future triggering.` : 'The rule remains disabled until explicit Enable / rearm.'} Current jobs and their cleanup are independent.`, command);
    });
    submit.id = 'rule-submit'; form.append(submit);
    form.addEventListener('submit', event => { event.preventDefault(); submit.click(); });
    ruleEditor = form; return form;
  }
  function renderRules() {
    const meta = view.rules, content = $('content'), block = node('section', null, 'block automation');
    block.append(node('h2', 'Automatic sharing'), node('p', meta.status, 'section-note'),
      node('p', 'Rules affect the administrator’s complete fixed profile. Disable and Delete change future intent; use Connections to stop an existing job and inspect actual cleanup.', 'section-note'));
    const result = node('p', meta.result, 'section-note'); result.id = 'rule-result'; result.setAttribute('role', 'status'); block.append(result);
    if (meta.grant) {
      block.append(node('p', `Granted device pool (${meta.grant.devices.length}); quota ${meta.grant.max_rules} rules. Conditions: ${meta.grant.triggers.join(', ')}. Rearm: ${meta.grant.rearm.join(', ')}.`, 'section-note'));
      const pool = node('ul'); meta.grant.devices.forEach(key => pool.append(node('li', key, 'data-id'))); block.append(pool);
    }
    const canCreate = meta.current && meta.policy_enabled === true && !!meta.grant && !view.unresolved_rule;
    const create = button('New disabled rule', () => { if (ruleDraft) confirm('Replace the current draft?', 'The current unsaved draft will be replaced with a new disabled-rule draft for the selected profile.', () => newRuleDraft()); else newRuleDraft(); }); create.id = 'rule-new'; block.append(unavailable(create, !canCreate));
    if (ruleDraft) {
      const editor = renderRuleEditor();
      unavailable(editor.querySelector('#rule-submit'), !canCreate || ruleDraft.profile !== view.profile);
      block.append(editor);
    }
    if (!meta.rules.length) block.append(empty('No rules in this sampled page. An administrator automation grant is required to create one.'));
    for (const rule of meta.rules) {
      const row = node('section', null, 'block rule-row'); row.dataset.ruleId = rule.id;
      row.append(node('h3', rule.spec.name), node('p', `${rule.id} · revision ${rule.revision} · ${rule.status}`, 'data-id'),
        node('p', `Future intent: ${rule.deleted ? 'deleted' : rule.enabled ? 'enabled' : 'disabled'} · Manually paused: ${rule.paused_by_user} · Last job: ${rule.last_job ? `${rule.last_job.id} (${rule.last_job.state})` : 'none reported'}`, 'section-note'));
      const inspect = node('details'); inspect.append(node('summary', 'Definition, origin and last job'), json(rule)); row.append(inspect);
      const controls = node('div', null, 'settings-actions');
      const edit = button('Edit definition', () => {
        if (ruleDraft) confirm('Replace the current draft?', 'Your unsaved draft will be replaced with this sampled definition. Its exact revision will be retained.', () => newRuleDraft(rule));
        else newRuleDraft(rule);
      }); edit.dataset.ruleAction = 'edit';
      controls.append(unavailable(edit, !canCreate || rule.deleted));
      for (const [label, change, enabled] of [['Enable / rearm', 'rule_set_enabled', true], ['Disable future triggers', 'rule_set_enabled', false], ['Delete future intent', 'rule_delete', null]]) {
        const command = { action: change, id: rule.id, revision: rule.revision }; if (enabled !== null) command.enabled = enabled;
        const control = button(label, () => confirm(`${label}?`, `Complete profile ${rule.profile}, rule ${rule.id}, exact revision ${rule.revision}. ${enabled === true ? 'Explicitly rearm future consideration under the administrator grant.' : 'This does not stop or release the current job.'}`, command));
        control.dataset.ruleAction = enabled === null ? 'delete' : enabled ? 'enable' : 'disable';
        controls.append(unavailable(control, !meta.current || !!view.unresolved_rule || rule.deleted || (enabled === true && !canCreate)));
      }
      row.append(controls); block.append(row);
    }
    block.append(pager('rules', meta.next_rule), node('h3', 'Rule events'), node('p', meta.events_status, 'section-note'));
    if (meta.events.length) block.append(table(['Sequence', 'Change / revision', 'Rule', 'Original request / job'], meta.events.map(e => [e.sequence, `${e.kind} / ${e.revision}`, e.rule_id, `${e.request_id || 'none'} / ${e.job_id || 'none'}`])));
    else block.append(empty('No rule events in this sampled page.'));
    block.append(pager('rule_events', meta.next_event)); content.append(block);
  }
  function renderSettings() {
    if (!preferencesForm) {
      const form = node('form', null, 'settings');
      form.append(node('h2', 'Frontend preferences'), node('p', 'These preferences control observation only. They do not grant access, change TLS policy or start USB sharing.', 'section-note'));
      const seconds = node('input'); seconds.type = 'number'; seconds.min = '2'; seconds.max = '120'; seconds.required = true; seconds.value = view.options.refresh_seconds; seconds.dataset.focus = 'preference-seconds';
      const interval = node('label', 'Refresh delay (seconds)'); interval.append(seconds);
      const automatic = node('input'); automatic.type = 'checkbox'; automatic.checked = view.options.auto_refresh; automatic.dataset.focus = 'preference-auto';
      const auto = node('label', 'Automatic service refresh'); auto.prepend(automatic); form.append(interval, auto);
      const controls = node('div', null, 'settings-actions');
      controls.append(button('Apply', () => { if (form.reportValidity()) action({ action: 'options', refresh_seconds: Number(seconds.value), auto_refresh: automatic.checked }); }), button('Save checked preferences', () => action({ action: 'save_settings' })), button('Reload', () => { preferencesForm = null; action({ action: 'reload_settings' }); }), button('Defaults', () => { preferencesForm = null; action({ action: 'options', refresh_seconds: 2, auto_refresh: true }); }));
      form.append(controls); form.addEventListener('submit', event => event.preventDefault()); preferencesForm = form;
    }
    $('content').append(preferencesForm, node('p', view.preferences_status, 'section-note')); renderRules();
    $('section-status').textContent = 'Checked frontend preferences and separately authorized agent-owned sharing rules';
  }
  function renderPage() {
    const focusedBefore = document.activeElement;
    const focusKey = focusedBefore.dataset.focus;
    const selection = focusedBefore.tagName === 'INPUT' && focusedBefore.type === 'text' ? [focusedBefore.selectionStart, focusedBefore.selectionEnd] : null;
    const scrollTop = $('main').scrollTop;
    // Current DOM only: do not retain obsolete profiles/sessions or health samples.
    const disclosures = new Map([...$('content').querySelectorAll('details[data-health-key]')].map(details => [details.dataset.healthKey, { open: details.open, scrollTop: details.querySelector('pre').scrollTop }]));
    $('page-title').textContent = pages[page]; $('content').replaceChildren(); $('inventory-count').textContent = '';
    $('search-bar').hidden = page > 1;
    document.querySelectorAll('nav button').forEach((button, i) => { if (page === i) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    if (!view) { $('content').append(empty('Waiting for the native application bridge.')); return; }
    if (view.unresolved) {
      const warning = node('div', `Unresolved Start · ${view.unresolved.profile} · ${view.unresolved.request_id}. Only the original retry identity will be used; inspect jobs before continuing.`, 'unresolved'); $('content').append(warning);
    }
    if (view.unresolved_rule) {
      const intent = view.unresolved_rule;
      const warning = node('section', null, 'unresolved');
      warning.append(node('p', `Unresolved rule edit · ${intent.profile} · ${intent.request_id}. New edits are blocked. Query only this original UUID; an expired result does not prove that the original never committed.`),
        button('Query original rule edit', () => confirm('Query the original rule edit?', `Read only profile ${intent.profile}, request ${intent.request_id}. No rule edit is resubmitted.`, { action: 'rule_resolve' })));
      const details = node('details'); details.append(node('summary', 'Original retained edit'), json(intent.mutation)); warning.append(details); $('content').append(warning);
    }
    if (page < 2) renderInventory(); else if (page === 2) renderConnections(disclosures); else if (page === 3) renderActivity(); else if (page === 4) renderSystem(); else renderSettings();
    updateControls();
    if (focusKey) {
      const focused = [...document.querySelectorAll('[data-focus]')].find(element => element.dataset.focus === focusKey);
      focused?.focus({ preventScroll: true });
      if (focused && selection) focused.setSelectionRange(...selection);
    }
    $('main').scrollTop = scrollTop;
  }
  function apply(next) {
    const changedOptions = JSON.stringify(view?.options) !== JSON.stringify(next.options);
    const previousRevision = view?.local?.revision; const previousInstance = view?.local?.instance;
    view = next;
    if (changedOptions) preferencesForm = null;
    if (view.pending !== null) waiting = view.pending;
    if (waiting !== null && BigInt(view.acknowledged) >= BigInt(waiting)) waiting = null;
    $('status').textContent = `${view.status}${busy() ? ' · Action pending…' : ''}`;
    $('journal').textContent = view.journal === null ? 'Journal not sampled' : view.journal ? 'Journal healthy' : 'Journal unhealthy';
    const selectedProfile = $('profile').value;
    const ids = view.profiles.map(p => p.id).join('\n');
    if ($('profile').dataset.ids !== ids) {
      $('profile').replaceChildren();
      if (!view.profiles.length) { const option = node('option', 'No readable profile'); option.value = ''; $('profile').append(option); }
      view.profiles.forEach(profile => { const option = node('option', `${profile.id} · ${profile.backend}`); option.value = profile.id; $('profile').append(option); });
      $('profile').dataset.ids = ids;
    }
    $('profile').value = view.profile || '';
    if (selectedProfile && selectedProfile !== view.profile) { selected = null; detail = null; detailKey = null; }
    // Persistent form nodes retain drafts; restore focus/caret while refreshing metadata.
    renderPage();
    if (page === 0 && selected && (previousRevision !== view.local?.revision || previousInstance !== view.local?.instance)) loadDetail(selected);
  }
  async function poll() {
    while (!destroyed) {
      try { const next = await invoke('gui_snapshot'); if (next.revision !== view?.revision || next.pending !== view?.pending || waiting !== null) apply(next); }
      catch (error) { notice(`Native bridge unavailable: ${String(error)}`); }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  }
  pages.forEach((label, index) => {
    const item = node('button'); item.type = 'button'; item.append(node('span', `0${index + 1}`, 'nav-number'), node('span', label));
    item.addEventListener('click', async () => {
      if (index === page) return;
      if (busy()) { notice('Wait for the accepted action before changing pages.'); return; }
      page = index; selected = null; detail = null; detailKey = null; showDetails = false; renderPage();
      await action({ action: 'surface', remote: page === 1, rules: page === 5 });
    }); $('navigation').append(item);
  });
  $('refresh').addEventListener('click', () => action({ action: 'refresh' }));
  $('profile').addEventListener('change', event => action({ action: 'profile', id: event.target.value }));
  $('start').addEventListener('click', () => confirm(view.unresolved ? 'Resolve the original Start?' : 'Start the complete profile?', view.unresolved ? `Retry only ${view.unresolved.profile}, request ${view.unresolved.request_id}. No new Start identity will be created.` : `Start all configured devices in profile ${view.profile}. The selected device row does not change the profile. Service jobs continue after this window closes.`, { action: 'start' }));
  $('search').addEventListener('input', event => { query = event.target.value; renderPage(); });
  $('eligible').addEventListener('change', event => { eligible = event.target.checked; renderPage(); });
  $('details-toggle').addEventListener('click', () => { showDetails = !showDetails; $('details-toggle').textContent = showDetails ? 'Show list' : 'Show details'; $('details-toggle').setAttribute('aria-expanded', String(showDetails)); renderPage(); });
  window.addEventListener('pagehide', () => { destroyed = true; });
  renderPage();
  if (invoke) poll(); else notice('This interface requires the packaged native RustUSB application.');
})();
