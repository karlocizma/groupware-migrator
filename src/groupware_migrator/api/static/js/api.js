// Centralized fetch wrapper. Redirects to /login on 401 (session expired).
export async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (response.status === 401) {
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail !== undefined) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      } else {
        detail = JSON.stringify(body);
      }
    } catch (_err) {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return response.json();
}
