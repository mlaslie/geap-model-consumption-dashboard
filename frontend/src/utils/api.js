const TOKEN_KEY = 'portal_api_token';

export function setApiToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function getApiToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export async function apiFetch(path, options = {}) {
  const token = getApiToken();
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('portal-auth-required'));
    throw new Error('Unauthorized');
  }
  return response;
}
