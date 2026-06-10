const PREFIX = 'gm_form_';

export function saveField(key, value) {
  try { localStorage.setItem(PREFIX + key, value); } catch (_) {}
}

export function loadField(key, fallback = '') {
  try { return localStorage.getItem(PREFIX + key) ?? fallback; } catch (_) { return fallback; }
}

// Bind an input/select/textarea element to localStorage.
// Restores value on call and saves on input/change events.
export function persistField(element, key) {
  if (!element) return;
  const stored = localStorage.getItem(PREFIX + key);
  if (stored !== null) element.value = stored;
  const save = () => saveField(key, element.value);
  element.addEventListener('input', save);
  element.addEventListener('change', save);
}

// Restore a set of fields from localStorage.
// fieldMap: { elementId: storageKey }
export function restoreForm(fieldMap) {
  for (const [id, key] of Object.entries(fieldMap)) {
    persistField(document.getElementById(id), key);
  }
}
