import { requestJSON } from './api.js';
import { restoreForm } from './form.js';

const API_BASE = '/api';

const state = {
  providers: [],
  selectedJobId: null,
  selectedBatchId: null,
  jobsEventSource: null,
  selectedJobEventSource: null,
  batchesEventSource: null,
  selectedBatchEventSource: null,
  lastBatchPreview: null,
  lastPreflightResult: null,
  uiMode: 'single',
};

function $(id) {
  return document.getElementById(id);
}

// Disable button and show loading label while async fn runs, restore on completion.
function withLoading(btn, loadingLabel, asyncFn) {
  return async (...args) => {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = loadingLabel;
    try {
      return await asyncFn(...args);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  };
}

function getSelectedWorkload() {
  const workload = $('workload')?.value || 'mail';
  const dav = ['calendar', 'contacts', 'tasks', 'notes'];
  return dav.includes(workload) ? workload : 'mail';
}

function getDestinationProtocolForWorkload(workload) {
  if (workload === 'calendar' || workload === 'tasks' || workload === 'notes') return 'caldav';
  if (workload === 'contacts') return 'carddav';
  return 'imap';
}

function getSourceFallbackPort(protocol) {
  if (protocol === 'imap') return 993;
  if (protocol === 'pop3') return 995;
  return 443;
}

function getDestinationFallbackPort(protocol) {
  return protocol === 'imap' ? 993 : 443;
}

function splitLines(value) {
  return value.split(/\r?\n|,/g).map((line) => line.trim()).filter(Boolean);
}

function parseFolderMapping(rawText) {
  const mapping = {};
  for (const line of rawText.split(/\r?\n/g)) {
    const clean = line.trim();
    if (!clean) continue;
    const separator = clean.includes('=>') ? '=>' : '=';
    const chunks = clean.split(separator).map((part) => part.trim());
    if (chunks.length !== 2 || !chunks[0] || !chunks[1]) {
      throw new Error(`Invalid folder mapping line: "${line}"`);
    }
    mapping[chunks[0]] = chunks[1];
  }
  return mapping;
}

function normalizePortValue(value, fallbackPort) {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed) || parsed <= 0) return fallbackPort;
  return parsed;
}

function statusClass(status, running) {
  if (running && status !== 'completed' && status !== 'failed') return 'running';
  return status;
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function setFeedback(message, kind = 'info') {
  const node = $('form-feedback');
  node.textContent = message;
  node.className = `feedback ${kind === 'error' ? 'error' : kind === 'success' ? 'success' : ''}`;
}

function setBatchFeedback(message, kind = 'info') {
  const node = $('batch-feedback');
  node.textContent = message;
  node.className = `feedback ${kind === 'error' ? 'error' : kind === 'success' ? 'success' : ''}`;
}

function setLiveIndicator(text, kind = 'neutral') {
  const node = $('live-indicator');
  node.textContent = text;
  node.classList.toggle('live-ok', kind === 'ok');
  node.classList.toggle('live-error', kind === 'error');
}

function setBatchLiveIndicator(text, kind = 'neutral') {
  const node = $('batch-live-indicator');
  node.textContent = text;
  node.classList.toggle('live-ok', kind === 'ok');
  node.classList.toggle('live-error', kind === 'error');
}

function setUIMode(mode) {
  const normalizedMode = mode === 'batch' ? 'batch' : 'single';
  state.uiMode = normalizedMode;
  document.querySelectorAll('[data-mode-tab]').forEach((button) => {
    const isActive = button.getAttribute('data-mode') === normalizedMode;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', isActive ? 'true' : 'false');
    button.setAttribute('tabindex', isActive ? '0' : '-1');
  });
  document.querySelectorAll('[data-mode-panel]').forEach((panel) => {
    const panelMode = panel.getAttribute('data-mode-panel');
    const isActive = panelMode === normalizedMode;
    panel.hidden = !isActive;
    panel.setAttribute('aria-hidden', isActive ? 'false' : 'true');
  });
}

function setDisclosureState(toggleButton, panel, isOpen, showLabel, hideLabel) {
  panel.hidden = !isOpen;
  panel.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
  toggleButton.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  toggleButton.textContent = isOpen ? hideLabel : showLabel;
}

function wireDisclosureToggle(toggleId, panelId, showLabel, hideLabel) {
  const toggleButton = $(toggleId);
  const panel = $(panelId);
  if (!toggleButton || !panel) return;
  setDisclosureState(toggleButton, panel, false, showLabel, hideLabel);
  toggleButton.addEventListener('click', () => {
    const isOpen = toggleButton.getAttribute('aria-expanded') === 'true';
    setDisclosureState(toggleButton, panel, !isOpen, showLabel, hideLabel);
  });
}

function findProvider(providerId) {
  return state.providers.find((provider) => provider.id === providerId) || null;
}

function getCompatibleSourceProtocols(workload) {
  if (workload === 'mail' || workload === 'all') return ['imap', 'pop3', 'ews'];
  if (workload === 'calendar' || workload === 'tasks') return ['caldav', 'ews'];
  if (workload === 'contacts') return ['carddav', 'ews'];
  return ['caldav']; // notes
}

function populateProviderSelect(selectId, protocols = null, side = 'source') {
  const select = $(selectId);
  const currentValue = select.value;
  select.innerHTML = '';
  for (const provider of state.providers) {
    if (protocols && provider.id !== 'custom') {
      const defaults = side === 'source' ? provider.source_defaults : provider.destination_defaults;
      if (!protocols.some((p) => defaults?.[p])) continue;
    }
    const option = document.createElement('option');
    option.value = provider.id;
    option.textContent = provider.name;
    select.appendChild(option);
  }
  if ([...select.options].some((o) => o.value === currentValue)) {
    select.value = currentValue;
  } else {
    select.value = 'custom';
  }
}

function renderGuidance(targetId, provider, protocol) {
  const node = $(targetId);
  if (!provider || !provider.auth_guidance) {
    node.innerHTML = '<strong>Auth guidance:</strong> No provider guidance available.';
    return;
  }
  const guidance = provider.auth_guidance;
  const steps = Array.isArray(guidance.steps) ? guidance.steps : [];
  const summary = guidance.summary || "Follow your provider's security/auth documentation.";
  const url = guidance.reference_url || '';
  node.innerHTML = `
    <strong>Auth guidance (${provider.name} ${protocol.toUpperCase()}):</strong>
    <div>${summary}</div>
    ${steps.length ? `<ul>${steps.map((step) => `<li>${step}</li>`).join('')}</ul>` : ''}
    ${url ? `<div><a href="${url}" target="_blank" rel="noopener noreferrer">Provider reference</a></div>` : ''}
  `;
}

const MULTI_WORKLOAD_PROTOCOLS = new Set(['ews']);

function getProviderPreferredMultiProtocol() {
  const provider = findProvider($('source-provider').value);
  const primary = provider?.primary_source_protocol;
  return primary && MULTI_WORKLOAD_PROTOCOLS.has(primary) ? primary : null;
}

function syncProtocolWithWorkload() {
  const rawWorkload = $('workload')?.value || 'mail';
  const workload = rawWorkload;
  const sourceProtocolSelect = $('source-protocol');
  const pop3DestinationField = $('pop3-destination-mailbox')?.closest('label');
  if (!sourceProtocolSelect) return;
  const current = sourceProtocolSelect.value;

  // Show "All workloads" option only when EWS is selected
  const allOption = document.querySelector('#workload option[value="all"]');
  if (allOption) {
    const isEws = current === 'ews';
    allOption.hidden = !isEws;
    if (!isEws && rawWorkload === 'all') $('workload').value = 'mail';
  }

  if (rawWorkload === 'all') {
    sourceProtocolSelect.disabled = false;
    if (pop3DestinationField) pop3DestinationField.hidden = true;
    return;
  }

  if (workload === 'mail') {
    sourceProtocolSelect.disabled = false;
    if (current === 'caldav' || current === 'carddav') {
      sourceProtocolSelect.value = getProviderPreferredMultiProtocol() || 'imap';
    }
    if (pop3DestinationField) pop3DestinationField.hidden = false;
  } else if (workload === 'calendar' || workload === 'tasks') {
    if (MULTI_WORKLOAD_PROTOCOLS.has(current)) {
      sourceProtocolSelect.disabled = false;
    } else {
      const preferred = getProviderPreferredMultiProtocol();
      if (preferred) {
        sourceProtocolSelect.value = preferred;
        sourceProtocolSelect.disabled = false;
      } else {
        sourceProtocolSelect.value = 'caldav';
        sourceProtocolSelect.disabled = true;
      }
    }
    if (pop3DestinationField) pop3DestinationField.hidden = true;
  } else if (workload === 'notes') {
    sourceProtocolSelect.value = 'caldav';
    sourceProtocolSelect.disabled = true;
    if (pop3DestinationField) pop3DestinationField.hidden = true;
  } else if (workload === 'contacts') {
    if (MULTI_WORKLOAD_PROTOCOLS.has(current)) {
      sourceProtocolSelect.disabled = false;
    } else {
      const preferred = getProviderPreferredMultiProtocol();
      if (preferred) {
        sourceProtocolSelect.value = preferred;
        sourceProtocolSelect.disabled = false;
      } else {
        sourceProtocolSelect.value = 'carddav';
        sourceProtocolSelect.disabled = true;
      }
    }
    if (pop3DestinationField) pop3DestinationField.hidden = true;
  }
}

function applySourceProviderDefaults() {
  const provider = findProvider($('source-provider').value);
  if (!provider) return;
  const sourceProtocolSelect = $('source-protocol');
  // Auto-switch to the provider's preferred protocol (e.g. EWS for Exchange on-premises)
  if (provider.primary_source_protocol) {
    const primary = provider.primary_source_protocol;
    if ([...sourceProtocolSelect.options].some((o) => o.value === primary)) {
      sourceProtocolSelect.value = primary;
      sourceProtocolSelect.disabled = false;
      refreshProviderSelects();
    }
  }
  const protocol = sourceProtocolSelect.value;
  const defaults = provider.source_defaults?.[protocol];
  if (!defaults) {
    renderGuidance('source-auth-guidance', provider, protocol);
    return;
  }
  $('source-host').value = defaults.host || $('source-host').value;
  $('source-port').value = defaults.port || $('source-port').value;
  $('source-ssl').checked = Boolean(defaults.use_ssl);
  $('source-tls-profile').value = defaults.tls_profile || 'modern';
  $('source-auth-mode').value = defaults.auth_mode === 'oauth2' ? 'oauth2' : 'password';
  if (Object.prototype.hasOwnProperty.call(defaults, 'oauth_token_url')) {
    $('source-oauth-token-url').value = defaults.oauth_token_url || '';
  }
  if (Object.prototype.hasOwnProperty.call(defaults, 'oauth_scope')) {
    $('source-oauth-scope').value = defaults.oauth_scope || '';
  }
  setOAuthFieldVisibility('source');
  renderGuidance('source-auth-guidance', provider, protocol);
}

function applyDestinationProviderDefaults() {
  const provider = findProvider($('destination-provider').value);
  const protocol = getDestinationProtocolForWorkload(getSelectedWorkload());
  if (!provider) return;
  const defaults = provider.destination_defaults?.[protocol];
  if (!defaults) {
    renderGuidance('destination-auth-guidance', provider, protocol);
    return;
  }
  $('destination-host').value = defaults.host || $('destination-host').value;
  $('destination-port').value = defaults.port || $('destination-port').value;
  $('destination-ssl').checked = Boolean(defaults.use_ssl);
  $('destination-tls-profile').value = defaults.tls_profile || 'modern';
  $('destination-auth-mode').value = defaults.auth_mode === 'oauth2' ? 'oauth2' : 'password';
  if (Object.prototype.hasOwnProperty.call(defaults, 'oauth_token_url')) {
    $('destination-oauth-token-url').value = defaults.oauth_token_url || '';
  }
  if (Object.prototype.hasOwnProperty.call(defaults, 'oauth_scope')) {
    $('destination-oauth-scope').value = defaults.oauth_scope || '';
  }
  setOAuthFieldVisibility('destination');
  renderGuidance('destination-auth-guidance', provider, protocol);
}

function setOAuthFieldVisibility(prefix) {
  const authMode = $(`${prefix}-auth-mode`).value;
  const oauthFields = $(`${prefix}-oauth-fields`);
  if (!oauthFields) return;
  const oauthSelected = authMode === 'oauth2';
  oauthFields.hidden = !oauthSelected;
  oauthFields.setAttribute('aria-hidden', oauthSelected ? 'false' : 'true');
}

function setSyncModeVisibility() {
  const syncMode = $('sync-mode').value === 'incremental' ? 'incremental' : 'full';
  const baseJobGroup = $('incremental-base-job-group');
  if (!baseJobGroup) return;
  const incrementalSelected = syncMode === 'incremental';
  baseJobGroup.hidden = !incrementalSelected;
  baseJobGroup.setAttribute('aria-hidden', incrementalSelected ? 'false' : 'true');
}

function applyOAuthConnectionFields(connection, prefix) {
  const accessToken = $(`${prefix}-oauth-access-token`).value.trim();
  const refreshToken = $(`${prefix}-oauth-refresh-token`).value.trim();
  const clientId = $(`${prefix}-oauth-client-id`).value.trim();
  const clientSecret = $(`${prefix}-oauth-client-secret`).value.trim();
  const tokenUrl = $(`${prefix}-oauth-token-url`).value.trim();
  const scope = $(`${prefix}-oauth-scope`).value.trim();
  if (accessToken) connection.oauth_access_token = accessToken;
  if (refreshToken) connection.oauth_refresh_token = refreshToken;
  if (clientId) connection.oauth_client_id = clientId;
  if (clientSecret) connection.oauth_client_secret = clientSecret;
  if (tokenUrl) connection.oauth_token_url = tokenUrl;
  if (scope) connection.oauth_scope = scope;
}

function buildRequestPayload(workloadOverride = null) {
  const workload = workloadOverride || getSelectedWorkload();
  if (!workloadOverride) syncProtocolWithWorkload();
  const sourceProtocol = $('source-protocol').value;
  const destinationProtocol = getDestinationProtocolForWorkload(workload);
  const sourceFallbackPort = getSourceFallbackPort(sourceProtocol);
  const destinationFallbackPort = getDestinationFallbackPort(destinationProtocol);
  const syncMode = $('sync-mode').value === 'incremental' ? 'incremental' : 'full';
  const incrementalBaseJobId = $('incremental-base-job-id').value.trim();
  const sourceAuthMode = $('source-auth-mode').value === 'oauth2' ? 'oauth2' : 'password';
  const destinationAuthMode = $('destination-auth-mode').value === 'oauth2' ? 'oauth2' : 'password';
  const includeCollections = splitLines($('include-mailboxes').value);
  const collectionMapping = parseFolderMapping($('folder-mapping').value);
  // When called per-workload for a bundle job, use the protocol's own port default
  // rather than the form port (which was set for IMAP and would be wrong for CalDAV)
  const destPort = workloadOverride
    ? destinationFallbackPort
    : normalizePortValue($('destination-port').value, destinationFallbackPort);

  const payload = {
    job_name: $('job-name').value.trim() || null,
    workload,
    source: {
      protocol: sourceProtocol,
      provider_id: $('source-provider').value,
      connection: {
        host: $('source-host').value.trim(),
        port: normalizePortValue($('source-port').value, sourceFallbackPort),
        username: $('source-username').value.trim(),
        use_ssl: $('source-ssl').checked,
        tls_profile: $('source-tls-profile').value,
        auth_mode: sourceAuthMode,
      },
    },
    destination: {
      protocol: destinationProtocol,
      provider_id: $('destination-provider').value,
      connection: {
        host: $('destination-host').value.trim(),
        port: destPort,
        username: $('destination-username').value.trim(),
        use_ssl: $('destination-ssl').checked,
        tls_profile: $('destination-tls-profile').value,
        auth_mode: destinationAuthMode,
      },
      root_collection: $('destination-root-mailbox').value.trim() || 'Migrated',
    },
    collection_mapping: collectionMapping,
    folder_mapping: collectionMapping,
    options: {
      sync_mode: syncMode,
      dry_run: $('dry-run').checked,
      max_errors: normalizePortValue($('max-errors').value, 25),
      max_retries: Math.max(0, parseInt($('max-retries')?.value || '0', 10)),
      pop3_destination_mailbox: $('pop3-destination-mailbox').value.trim() || 'POP3-Inbox',
    },
  };
  if (syncMode === 'incremental' && incrementalBaseJobId) {
    payload.options.incremental_base_job_id = incrementalBaseJobId;
  }
  if (sourceAuthMode === 'oauth2') {
    applyOAuthConnectionFields(payload.source.connection, 'source');
  } else {
    payload.source.connection.password = $('source-password').value;
  }
  if (destinationAuthMode === 'oauth2') {
    applyOAuthConnectionFields(payload.destination.connection, 'destination');
  } else {
    payload.destination.connection.password = $('destination-password').value;
  }
  if (includeCollections.length > 0) {
    payload.source.include_collections = includeCollections;
    payload.source.include_mailboxes = includeCollections;
  }
  if (!payload.job_name) delete payload.job_name;
  if (Object.keys(payload.collection_mapping).length === 0) {
    delete payload.collection_mapping;
    delete payload.folder_mapping;
  }
  return payload;
}

function buildBatchPayload() {
  return {
    batch_name: $('batch-name').value.trim() || null,
    allow_partial: $('batch-allow-partial').checked,
    base_request: buildRequestPayload(),
    csv_content: $('batch-csv-content').value,
  };
}

function renderPlan(plan) {
  const summary = $('plan-summary');
  const output = $('plan-output');
  const total = Number(plan.total_estimated_items ?? plan.total_estimated_messages ?? 0);
  const items = Array.isArray(plan.items) ? plan.items : [];
  summary.textContent = `${items.length} collection(s), ${total} estimated item(s)`;
  if (items.length === 0) {
    output.innerHTML = `<div class="plan-item"><div class="plan-paths">No collections returned from source.</div></div>`;
    return;
  }
  output.innerHTML = items.map((item) => `
    <div class="plan-item">
      <div class="plan-paths">
        <strong>${item.source_collection || item.source_mailbox}</strong> → ${item.destination_collection || item.destination_mailbox}
      </div>
      <div class="plan-count">${item.estimated_items ?? item.estimated_messages} items</div>
    </div>
  `).join('');
}

function renderPreflightResult(result) {
  const output = $('preflight-output');
  if (!result) {
    output.className = 'preflight-output empty';
    output.innerHTML = 'No preflight run yet.';
    return;
  }
  const sourceOk = Boolean(result.source?.ok);
  const destinationOk = Boolean(result.destination?.ok);
  const planOk = Boolean(result.plan?.ok);
  const overallOk = Boolean(result.overall_ok);
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const checkedAt = formatDate(result.checked_at);
  const sourceError = result.source?.error || '';
  const destinationError = result.destination?.error || '';
  const planError = result.plan?.error || '';
  const workload = result.workload || 'mail';
  const collections = Number(result.plan?.collections ?? result.plan?.mailboxes ?? 0);
  const estimated = Number(result.plan?.total_estimated_items ?? result.plan?.total_estimated_messages ?? 0);
  const incrementalMode = result.incremental?.mode || 'full';
  const incrementalError = result.incremental?.error || '';
  const cursorCollections = Number(result.incremental?.resolved_cursor_collections ?? result.incremental?.resolved_cursor_mailboxes ?? 0);
  const resolutionSource = result.incremental?.resolution_source || 'disabled';
  const baseJobId = result.incremental?.base_job_id || '';
  const incrementalOk = incrementalMode !== 'incremental' || !incrementalError;
  output.className = `preflight-output ${overallOk ? 'success' : 'error'}`;
  output.innerHTML = `
    <div class="preflight-head">
      <strong>Preflight ${overallOk ? 'passed' : 'failed'}</strong>
      <span>${checkedAt}</span>
    </div>
    <div><strong>Workload:</strong> ${workload}</div>
    <div class="preflight-grid">
      <div class="preflight-item ${sourceOk ? 'ok' : 'failed'}">
        <strong>Source</strong><span>${sourceOk ? 'OK' : 'FAILED'}</span>
        ${sourceOk ? '' : `<div class="preflight-error">${sourceError || 'Validation failed.'}</div>`}
      </div>
      <div class="preflight-item ${destinationOk ? 'ok' : 'failed'}">
        <strong>Destination</strong><span>${destinationOk ? 'OK' : 'FAILED'}</span>
        ${destinationOk ? '' : `<div class="preflight-error">${destinationError || 'Validation failed.'}</div>`}
      </div>
      <div class="preflight-item ${planOk ? 'ok' : 'failed'}">
        <strong>Plan</strong><span>${planOk ? 'OK' : 'FAILED'}</span>
        <div>${collections} collection(s), ${estimated} estimated item(s)</div>
        ${planOk ? '' : `<div class="preflight-error">${planError || 'Plan generation failed.'}</div>`}
      </div>
      <div class="preflight-item ${incrementalOk ? 'ok' : 'failed'}">
        <strong>Sync</strong><span>${incrementalMode.toUpperCase()}</span>
        <div>${incrementalMode === 'incremental' ? `${cursorCollections} cursor collection(s) from ${resolutionSource}.` : 'Full sync selected.'}</div>
        ${baseJobId ? `<div>Base job: ${baseJobId}</div>` : ''}
        ${incrementalError ? `<div class="preflight-error">${incrementalError}</div>` : ''}
      </div>
    </div>
    ${warnings.length ? `<div class="preflight-warnings"><strong>Warnings:</strong><ul>${warnings.map((w) => `<li>${w}</li>`).join('')}</ul></div>` : ''}
  `;
}

function renderBatchPreview(preview) {
  const panel = $('batch-preview');
  if (!preview || !Array.isArray(preview.items)) {
    panel.className = 'batch-preview empty';
    panel.innerHTML = 'No CSV preview yet.';
    return;
  }
  if (preview.items.length === 0) {
    panel.className = 'batch-preview empty';
    panel.innerHTML = 'CSV has no data rows.';
    return;
  }
  const hasPreflight = Object.prototype.hasOwnProperty.call(preview, 'checked_rows');
  const checkedRows = Number(preview.checked_rows || 0);
  const okRows = Number(preview.ok_rows || 0);
  const failedRows = Number(preview.failed_rows || 0);
  panel.className = 'batch-preview';
  panel.innerHTML = `
    ${hasPreflight ? `<div class="batch-preview-summary">Preflight checked ${checkedRows} row(s): ${okRows} ok / ${failedRows} failed.</div>` : ''}
    ${preview.items.map((item) => {
      const hasRowPreflight = Object.prototype.hasOwnProperty.call(item, 'preflight') || Object.prototype.hasOwnProperty.call(item, 'preflight_skipped');
      const preflight = item.preflight || null;
      const preflightSkipped = Boolean(item.preflight_skipped);
      const preflightFailed = Boolean(preflight && !preflight.overall_ok);
      const rowClass = item.valid && !preflightFailed ? 'batch-preview-row' : 'batch-preview-row invalid';
      const status = item.valid ? 'valid' : 'invalid';
      let preflightBadge = '';
      let preflightError = '';
      if (hasRowPreflight) {
        if (preflightSkipped) {
          preflightBadge = `<span class="preflight-chip skipped">preflight skipped</span>`;
        } else if (preflight && preflight.overall_ok) {
          preflightBadge = `<span class="preflight-chip ok">preflight ok</span>`;
        } else if (preflight) {
          preflightBadge = `<span class="preflight-chip failed">preflight failed</span>`;
          const errorParts = [];
          if (preflight.source && !preflight.source.ok && preflight.source.error) errorParts.push(`source: ${preflight.source.error}`);
          if (preflight.destination && !preflight.destination.ok && preflight.destination.error) errorParts.push(`destination: ${preflight.destination.error}`);
          if (preflight.plan && !preflight.plan.ok && preflight.plan.error) errorParts.push(`plan: ${preflight.plan.error}`);
          preflightError = errorParts.length ? `<div class="preview-error">${errorParts.join(' | ')}</div>` : '';
        } else {
          preflightBadge = `<span class="preflight-chip skipped">preflight not run</span>`;
        }
      }
      return `
        <div class="${rowClass}">
          <span>#${item.row_number}</span>
          <span>${item.source_username || '—'}</span>
          <span>${item.destination_username || '—'}</span>
          <span>${status}</span>
          ${preflightBadge}
          ${item.error ? `<div class="preview-error">${item.error}</div>` : ''}
          ${preflightError}
        </div>
      `;
    }).join('')}
  `;
}

async function loadRecentEvents(jobId) {
  try {
    const payload = await requestJSON(`${API_BASE}/jobs/${jobId}/events?limit=6`);
    const events = payload.items || [];
    const container = document.querySelector('[data-role="events"]');
    if (!container) return;
    if (!events.length) {
      container.innerHTML = '<em>No audit events yet.</em>';
      return;
    }
    container.innerHTML = events.map((event) => {
      const createdAt = formatDate(event.created_at);
      return `<div><strong>${event.event_type}</strong> <span class="job-id">(${createdAt})</span></div>`;
    }).join('');
  } catch (_error) {
    // non-blocking
  }
}

function setReportButtonsEnabled(enabled) {
  $('download-report-json').disabled = !enabled;
  $('download-report-csv').disabled = !enabled;
}

function renderJobDetails(job) {
  const panel = $('job-details');
  if (!job) {
    panel.classList.add('empty');
    panel.innerHTML = 'No job selected.';
    setReportButtonsEnabled(false);
    return;
  }
  panel.classList.remove('empty');
  const status = statusClass(job.status, job.running);
  const estimated = Number(job.plan_summary?.total_estimated_items ?? job.plan_summary?.total_estimated_messages ?? 0);
  const workload = job.workload || 'mail';
  const canCancel = job.status === 'running' || job.status === 'pending';
  panel.innerHTML = `
    <div class="job-head">
      <div>
        <div><strong>${job.job_name || 'Unnamed job'}</strong></div>
        <div class="job-id">${job.job_id}</div>
      </div>
      <span class="status ${status}">${status}</span>
    </div>
    <div class="metric-grid">
      <div class="metric"><strong>Migrated</strong><span>${job.migrated_count}</span></div>
      <div class="metric"><strong>Skipped</strong><span>${job.skipped_count}</span></div>
      <div class="metric"><strong>Failed</strong><span>${job.failed_count}</span></div>
      <div class="metric"><strong>Workload</strong><span>${workload}</span></div>
      <div class="metric"><strong>Estimated total</strong><span>${estimated}</span></div>
      <div class="metric"><strong>Started</strong><span>${formatDate(job.started_at)}</span></div>
      <div class="metric"><strong>Finished</strong><span>${formatDate(job.finished_at)}</span></div>
    </div>
    <div class="metric"><strong>Recent audit events</strong><span data-role="events">Loading events…</span></div>
    ${job.last_error ? `<div class="error-box"><strong>Last error:</strong><br>${job.last_error}</div>` : ''}
    ${canCancel ? `<div style="margin-top:10px"><button id="cancel-job-btn" class="btn secondary" style="font-size:0.82rem;padding:5px 14px">Cancel job</button></div>` : ''}
  `;
  setReportButtonsEnabled(true);
  loadRecentEvents(job.job_id);
  const cancelBtn = document.getElementById('cancel-job-btn');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
      if (!confirm('Cancel this job?')) return;
      cancelBtn.disabled = true;
      cancelBtn.textContent = 'Cancelling…';
      try {
        await requestJSON(`${API_BASE}/jobs/${job.job_id}/cancel`, { method: 'POST' });
      } catch (err) {
        cancelBtn.disabled = false;
        cancelBtn.textContent = 'Cancel job';
        alert(err.message || 'Failed to cancel job.');
      }
    });
  }
}

function renderJobsList(items) {
  const list = $('jobs-list');
  if (!Array.isArray(items) || items.length === 0) {
    list.innerHTML = `<div class="job-card">No jobs yet.</div>`;
    return;
  }
  list.innerHTML = items.map((job) => {
    const status = statusClass(job.status, job.running);
    const isActive = state.selectedJobId === job.job_id;
    const classes = ['job-card'];
    if (isActive) classes.push('active');
    return `
      <button class="${classes.join(' ')}" type="button" data-job-id="${job.job_id}">
        <div class="job-head">
          <div>
            <div>${job.job_name || 'Unnamed job'}</div>
            <div class="job-id">${job.job_id}</div>
          </div>
          <span class="status ${status}">${status}</span>
        </div>
        <div class="job-metrics">
          <span>${job.workload || 'mail'}</span>
          <span>M:${job.migrated_count}</span>
          <span>S:${job.skipped_count}</span>
          <span>F:${job.failed_count}</span>
        </div>
      </button>
    `;
  }).join('');
  list.querySelectorAll('[data-job-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const jobId = button.getAttribute('data-job-id');
      if (!jobId) return;
      state.selectedJobId = jobId;
      renderJobsList(items);
      connectSelectedJobStream();
      loadSelectedJobSnapshot();
    });
  });
}

function renderBatchesList(items) {
  const list = $('batches-list');
  if (!Array.isArray(items) || items.length === 0) {
    list.innerHTML = `<div class="job-card">No batches yet.</div>`;
    return;
  }
  list.innerHTML = items.map((batch) => {
    const status = statusClass(batch.status, batch.running_rows > 0);
    const isActive = state.selectedBatchId === batch.batch_id;
    const classes = ['job-card'];
    if (isActive) classes.push('active');
    return `
      <button class="${classes.join(' ')}" type="button" data-batch-id="${batch.batch_id}">
        <div class="job-head">
          <div>
            <div>${batch.batch_name || 'Unnamed batch'}</div>
            <div class="job-id">${batch.batch_id}</div>
          </div>
          <span class="status ${status}">${status}</span>
        </div>
        <div class="job-metrics">
          <span>Rows:${batch.total_rows}</span>
          <span>Done:${batch.completed_rows}</span>
          <span>Fail:${batch.failed_rows}</span>
        </div>
      </button>
    `;
  }).join('');
  list.querySelectorAll('[data-batch-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const batchId = button.getAttribute('data-batch-id');
      if (!batchId) return;
      state.selectedBatchId = batchId;
      renderBatchesList(items);
      connectSelectedBatchStream();
      loadSelectedBatchSnapshot();
    });
  });
}

function renderBatchDetails(batch) {
  const panel = $('batch-details');
  if (!batch) {
    panel.classList.add('empty');
    panel.innerHTML = 'No batch selected.';
    return;
  }
  panel.classList.remove('empty');
  const status = statusClass(batch.status, batch.running_rows > 0);
  const items = Array.isArray(batch.items) ? batch.items : [];
  panel.innerHTML = `
    <div class="job-head">
      <div>
        <div><strong>${batch.batch_name || 'Unnamed batch'}</strong></div>
        <div class="job-id">${batch.batch_id}</div>
      </div>
      <span class="status ${status}">${status}</span>
    </div>
    <div class="metric-grid">
      <div class="metric"><strong>Total rows</strong><span>${batch.total_rows}</span></div>
      <div class="metric"><strong>Pending</strong><span>${batch.pending_rows}</span></div>
      <div class="metric"><strong>Running</strong><span>${batch.running_rows}</span></div>
      <div class="metric"><strong>Completed</strong><span>${batch.completed_rows}</span></div>
      <div class="metric"><strong>Failed rows</strong><span>${batch.failed_rows}</span></div>
      <div class="metric"><strong>Migrated items</strong><span>${batch.migrated_count}</span></div>
    </div>
    <div class="batch-preview">
      ${items.length ? items.map((item) => `
        <div class="batch-preview-row ${item.status === 'failed' ? 'invalid' : ''}">
          <span>#${item.row_number}</span>
          <span>${item.source_username || '—'}</span>
          <span>${item.destination_username || '—'}</span>
          <span class="status ${statusClass(item.status, item.running)}">${statusClass(item.status, item.running)}</span>
          ${item.job_id ? `<div class="job-id">Job: ${item.job_id}</div>` : ''}
          ${item.last_error ? `<div class="preview-error">${item.last_error}</div>` : ''}
        </div>
      `).join('') : `<div class="batch-preview empty">No rows available.</div>`}
    </div>
  `;
}

async function refreshJobsList() {
  const payload = await requestJSON(`${API_BASE}/jobs?limit=30`);
  renderJobsList(payload.items || []);
}

async function refreshBatchesList() {
  const payload = await requestJSON(`${API_BASE}/batches?limit=30`);
  renderBatchesList(payload.items || []);
}

async function loadSelectedJobSnapshot() {
  if (!state.selectedJobId) {
    renderJobDetails(null);
    return;
  }
  const job = await requestJSON(`${API_BASE}/jobs/${state.selectedJobId}`);
  renderJobDetails(job);
}

async function loadSelectedBatchSnapshot() {
  if (!state.selectedBatchId) {
    renderBatchDetails(null);
    return;
  }
  const batch = await requestJSON(`${API_BASE}/batches/${state.selectedBatchId}`);
  renderBatchDetails(batch);
}

function closeJobsStream() {
  if (state.jobsEventSource) {
    state.jobsEventSource.close();
    state.jobsEventSource = null;
  }
}

function closeSelectedJobStream() {
  if (state.selectedJobEventSource) {
    state.selectedJobEventSource.close();
    state.selectedJobEventSource = null;
  }
}

function closeBatchesStream() {
  if (state.batchesEventSource) {
    state.batchesEventSource.close();
    state.batchesEventSource = null;
  }
}

function closeSelectedBatchStream() {
  if (state.selectedBatchEventSource) {
    state.selectedBatchEventSource.close();
    state.selectedBatchEventSource = null;
  }
}

function connectJobsStream() {
  closeJobsStream();
  const stream = new EventSource(`${API_BASE}/jobs/stream?limit=30`);
  state.jobsEventSource = stream;
  stream.addEventListener('jobs', (event) => {
    const payload = JSON.parse(event.data);
    renderJobsList(payload.items || []);
    setLiveIndicator('Live stream: connected', 'ok');
  });
  stream.onerror = () => {
    setLiveIndicator('Live stream: reconnecting…', 'error');
  };
}

function connectSelectedJobStream() {
  closeSelectedJobStream();
  if (!state.selectedJobId) return;
  const stream = new EventSource(`${API_BASE}/jobs/${state.selectedJobId}/stream`);
  state.selectedJobEventSource = stream;
  stream.addEventListener('job', (event) => {
    const payload = JSON.parse(event.data);
    renderJobDetails(payload);
  });
  stream.addEventListener('error', () => {
    renderJobDetails(null);
  });
}

function connectBatchesStream() {
  closeBatchesStream();
  const stream = new EventSource(`${API_BASE}/batches/stream?limit=30`);
  state.batchesEventSource = stream;
  stream.addEventListener('batches', (event) => {
    const payload = JSON.parse(event.data);
    renderBatchesList(payload.items || []);
    setBatchLiveIndicator('Batch stream: connected', 'ok');
  });
  stream.onerror = () => {
    setBatchLiveIndicator('Batch stream: reconnecting…', 'error');
  };
}

function connectSelectedBatchStream() {
  closeSelectedBatchStream();
  if (!state.selectedBatchId) return;
  const stream = new EventSource(`${API_BASE}/batches/${state.selectedBatchId}/stream`);
  state.selectedBatchEventSource = stream;
  stream.addEventListener('batch', (event) => {
    const payload = JSON.parse(event.data);
    renderBatchDetails(payload);
  });
  stream.addEventListener('error', () => {
    renderBatchDetails(null);
  });
}

async function buildPlan() {
  if ($('workload').value === 'all') {
    setFeedback('Select a single workload to preview the migration plan.');
    return;
  }
  try {
    setFeedback('Building migration plan...');
    const request = buildRequestPayload();
    const plan = await requestJSON(`${API_BASE}/jobs/plan`, { method: 'POST', body: JSON.stringify(request) });
    renderPlan(plan);
    setFeedback('Plan created successfully.', 'success');
  } catch (error) {
    setFeedback(`Plan failed: ${error.message}`, 'error');
  }
}

async function runJobPreflight() {
  if ($('workload').value === 'all') {
    setFeedback('Select a single workload to run preflight checks.');
    return;
  }
  try {
    setFeedback('Running preflight checks...');
    const request = buildRequestPayload();
    const result = await requestJSON(`${API_BASE}/jobs/preflight`, { method: 'POST', body: JSON.stringify(request) });
    state.lastPreflightResult = result;
    renderPreflightResult(result);
    setFeedback(
      result.overall_ok ? 'Preflight checks passed.' : 'Preflight checks completed with failures.',
      result.overall_ok ? 'success' : 'error',
    );
  } catch (error) {
    setFeedback(`Preflight failed: ${error.message}`, 'error');
  }
}

async function startAllEwsWorkloads() {
  const EWS_WORKLOADS = ['mail', 'calendar', 'contacts', 'tasks'];
  setFeedback('Starting all Exchange workload jobs…');
  const jobBaseName = $('job-name').value.trim();
  let started = 0;
  let lastJobId = null;
  for (const wl of EWS_WORKLOADS) {
    try {
      const request = buildRequestPayload(wl);
      request.job_name = jobBaseName ? `${jobBaseName} — ${wl}` : `Exchange migration — ${wl}`;
      const response = await requestJSON(`${API_BASE}/jobs/start`, { method: 'POST', body: JSON.stringify(request) });
      lastJobId = response.job_id;
      started++;
    } catch (error) {
      setFeedback(`Failed to start ${wl} job: ${error.message}`, 'error');
      break;
    }
  }
  if (lastJobId) state.selectedJobId = lastJobId;
  setFeedback(
    `Started ${started} of ${EWS_WORKLOADS.length} Exchange migration jobs.`,
    started === EWS_WORKLOADS.length ? 'success' : 'error',
  );
  await refreshJobsList();
  if (lastJobId) {
    await loadSelectedJobSnapshot();
    connectSelectedJobStream();
  }
}

async function startBackgroundJob() {
  if ($('workload').value === 'all') {
    await startAllEwsWorkloads();
    return;
  }
  try {
    setFeedback('Starting background migration job...');
    const request = buildRequestPayload();
    const response = await requestJSON(`${API_BASE}/jobs/start`, { method: 'POST', body: JSON.stringify(request) });
    state.selectedJobId = response.job_id;
    setFeedback(`Background job started: ${response.job_id}`, 'success');
    await refreshJobsList();
    await loadSelectedJobSnapshot();
    connectSelectedJobStream();
  } catch (error) {
    setFeedback(`Failed to start background job: ${error.message}`, 'error');
  }
}

async function previewBatchCsv() {
  try {
    setBatchFeedback('Validating CSV rows...');
    const payload = buildBatchPayload();
    const preview = await requestJSON(`${API_BASE}/batches/preview`, { method: 'POST', body: JSON.stringify(payload) });
    state.lastBatchPreview = preview;
    renderBatchPreview(preview);
    setBatchFeedback(
      `CSV parsed: ${preview.valid_rows} valid / ${preview.invalid_rows} invalid row(s).`,
      preview.invalid_rows > 0 ? 'error' : 'success',
    );
  } catch (error) {
    renderBatchPreview(null);
    setBatchFeedback(`CSV preview failed: ${error.message}`, 'error');
  }
}

async function startBatchMigration() {
  try {
    setBatchFeedback('Starting batch migration...');
    const payload = buildBatchPayload();
    const batch = await requestJSON(`${API_BASE}/batches/start`, { method: 'POST', body: JSON.stringify(payload) });
    state.selectedBatchId = batch.batch_id;
    renderBatchDetails(batch);
    setBatchFeedback(`Batch started: ${batch.batch_id}`, 'success');
    await refreshBatchesList();
    connectSelectedBatchStream();
  } catch (error) {
    setBatchFeedback(`Batch start failed: ${error.message}`, 'error');
  }
}

async function runBatchPreflight() {
  try {
    setBatchFeedback('Running batch preflight checks...');
    const payload = buildBatchPayload();
    const limit = Number.parseInt($('batch-preflight-limit').value, 10);
    if (!Number.isNaN(limit) && limit > 0) payload.limit = limit;
    const result = await requestJSON(`${API_BASE}/batches/preflight`, { method: 'POST', body: JSON.stringify(payload) });
    state.lastBatchPreview = result;
    renderBatchPreview(result);
    setBatchFeedback(
      `Batch preflight checked ${result.checked_rows} row(s): ${result.ok_rows} ok / ${result.failed_rows} failed.`,
      result.failed_rows > 0 ? 'error' : 'success',
    );
  } catch (error) {
    renderBatchPreview(null);
    setBatchFeedback(`Batch preflight failed: ${error.message}`, 'error');
  }
}

async function loadCsvFileToTextarea(fileInput) {
  const file = fileInput.files && fileInput.files[0];
  if (!file) return;
  const text = await file.text();
  $('batch-csv-content').value = text;
  setBatchFeedback(`Loaded CSV file: ${file.name}`, 'success');
}

function refreshProviderSelects() {
  const rawWorkload = $('workload')?.value || 'mail';
  const workload = rawWorkload === 'all' ? 'all' : getSelectedWorkload();
  const destProtocol = workload === 'all' ? 'imap' : getDestinationProtocolForWorkload(workload);
  populateProviderSelect('source-provider', getCompatibleSourceProtocols(workload), 'source');
  populateProviderSelect('destination-provider', [destProtocol], 'destination');
}

async function loadProviders() {
  const payload = await requestJSON(`${API_BASE}/providers`);
  state.providers = payload.items || [];
  syncProtocolWithWorkload();
  refreshProviderSelects();
  applySourceProviderDefaults();
  applyDestinationProviderDefaults();
}

function wireInteractions() {
  // Mode tabs
  document.querySelectorAll('[data-mode-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      const mode = button.getAttribute('data-mode') || 'single';
      setUIMode(mode);
    });
  });

  // Disclosure toggles
  wireDisclosureToggle('advanced-settings-toggle', 'advanced-settings-panel', 'Show advanced settings', 'Hide advanced settings');
  wireDisclosureToggle('batch-advanced-toggle', 'batch-advanced-panel', 'Show batch advanced settings', 'Hide batch advanced settings');

  // Single job buttons — with loading states
  const planBtn = $('plan-button');
  const preflightBtn = $('preflight-button');
  const startBtn = $('start-button');
  planBtn.addEventListener('click', withLoading(planBtn, 'Building…', buildPlan));
  preflightBtn.addEventListener('click', withLoading(preflightBtn, 'Checking…', runJobPreflight));
  startBtn.addEventListener('click', withLoading(startBtn, 'Starting…', startBackgroundJob));

  // Refresh buttons
  $('refresh-jobs').addEventListener('click', async () => {
    await refreshJobsList();
    await loadSelectedJobSnapshot();
  });

  // Report downloads
  $('download-report-json').addEventListener('click', () => {
    if (!state.selectedJobId) return;
    window.open(`${API_BASE}/jobs/${state.selectedJobId}/report?format=json`, '_blank');
  });
  $('download-report-csv').addEventListener('click', () => {
    if (!state.selectedJobId) return;
    window.open(`${API_BASE}/jobs/${state.selectedJobId}/report?format=csv`, '_blank');
  });

  // Form field change listeners
  $('workload').addEventListener('change', () => {
    syncProtocolWithWorkload();
    refreshProviderSelects();
    applySourceProviderDefaults();
    applyDestinationProviderDefaults();
  });
  $('source-protocol').addEventListener('change', () => {
    refreshProviderSelects();
    applySourceProviderDefaults();
    applyDestinationProviderDefaults();
  });
  $('source-provider').addEventListener('change', applySourceProviderDefaults);
  $('source-auth-mode').addEventListener('change', () => setOAuthFieldVisibility('source'));
  $('destination-provider').addEventListener('change', applyDestinationProviderDefaults);
  $('destination-auth-mode').addEventListener('change', () => setOAuthFieldVisibility('destination'));
  $('sync-mode').addEventListener('change', setSyncModeVisibility);

  // Batch buttons — with loading states
  const batchPreviewBtn = $('batch-preview-button');
  const batchPreflightBtn = $('batch-preflight-button');
  const batchStartBtn = $('batch-start-button');
  batchPreviewBtn.addEventListener('click', withLoading(batchPreviewBtn, 'Validating…', previewBatchCsv));
  batchPreflightBtn.addEventListener('click', withLoading(batchPreflightBtn, 'Checking…', runBatchPreflight));
  batchStartBtn.addEventListener('click', withLoading(batchStartBtn, 'Starting…', startBatchMigration));

  $('refresh-batches').addEventListener('click', async () => {
    await refreshBatchesList();
    await loadSelectedBatchSnapshot();
  });
  $('batch-csv-file').addEventListener('change', (event) => {
    loadCsvFileToTextarea(event.target).catch((error) => {
      setBatchFeedback(`Failed reading CSV file: ${error.message}`, 'error');
    });
  });
}

function wireFormPersistence() {
  restoreForm({
    'source-host': 'source_host',
    'source-port': 'source_port',
    'source-username': 'source_username',
    'source-protocol': 'source_protocol',
    'destination-host': 'destination_host',
    'destination-port': 'destination_port',
    'destination-username': 'destination_username',
    'destination-root-mailbox': 'destination_root_mailbox',
    'workload': 'workload',
    'job-name': 'job_name',
    'sync-mode': 'sync_mode',
    'max-retries': 'max_retries',
  });
}

async function bootstrap() {
  try {
    const me = await requestJSON('/auth/me');
    if (me.is_admin) {
      const adminLink = document.getElementById('admin-link');
      if (adminLink) adminLink.style.display = 'inline-block';
    }
  } catch (_) {
    // Non-critical — admin link stays hidden
  }
  wireInteractions();
  wireFormPersistence();
  setUIMode(state.uiMode);
  syncProtocolWithWorkload();
  setOAuthFieldVisibility('source');
  setOAuthFieldVisibility('destination');
  setSyncModeVisibility();
  await loadProviders();
  await refreshJobsList();
  await refreshBatchesList();
  renderPlan({ items: [], total_estimated_items: 0 });
  renderPreflightResult(state.lastPreflightResult);
  renderJobDetails(null);
  renderBatchDetails(null);
  renderBatchPreview(state.lastBatchPreview);
  connectJobsStream();
  connectBatchesStream();
}

document.addEventListener('DOMContentLoaded', () => {
  bootstrap().catch((error) => {
    setFeedback(`UI bootstrap failed: ${error.message}`, 'error');
    setBatchFeedback(`UI bootstrap failed: ${error.message}`, 'error');
    setLiveIndicator('Live stream: failed to initialize', 'error');
    setBatchLiveIndicator('Batch stream: failed to initialize', 'error');
  });
});
