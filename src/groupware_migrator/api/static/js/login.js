let _totpRequired = false;
let _email = '';
let _password = '';

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submit-btn');
  const errEl = document.getElementById('error-msg');
  btn.disabled = true;
  btn.textContent = 'Signing in…';
  errEl.classList.remove('visible');

  const email = _totpRequired ? _email : document.getElementById('email').value;
  const password = _totpRequired ? _password : document.getElementById('password').value;
  const totpCode = document.getElementById('totp-code').value.trim();

  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, totp_code: totpCode }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.totp_required) {
      _totpRequired = true;
      _email = email;
      _password = password;
      document.getElementById('totp-field').style.display = '';
      document.getElementById('email').closest('.field').style.display = 'none';
      document.getElementById('password').closest('.field').style.display = 'none';
      document.getElementById('totp-code').focus();
      btn.textContent = 'Verify Code';
    } else if (res.ok) {
      window.location.href = '/';
    } else {
      errEl.textContent = data.detail || 'Invalid credentials.';
      errEl.classList.add('visible');
    }
  } catch {
    errEl.textContent = 'Network error. Please try again.';
    errEl.classList.add('visible');
  } finally {
    btn.disabled = false;
    if (!_totpRequired || document.getElementById('totp-field').style.display === 'none') {
      btn.textContent = 'Sign In';
    } else {
      btn.textContent = 'Verify Code';
    }
  }
});
