const API = location.origin + '/api/v1';
const $ = id => document.getElementById(id);

const tabLogin = $('tabLogin'), tabRegister = $('tabRegister');
const loginForm = $('loginForm'), registerForm = $('registerForm');
const errorBox = $('loginError');

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.style.display = '';
}
function clearError() {
  errorBox.style.display = 'none';
}

function switchTab(tab) {
  clearError();
  if (tab === 'login') {
    tabLogin.classList.add('active'); tabRegister.classList.remove('active');
    loginForm.style.display = ''; registerForm.style.display = 'none';
  } else {
    tabRegister.classList.add('active'); tabLogin.classList.remove('active');
    registerForm.style.display = ''; loginForm.style.display = 'none';
  }
}
tabLogin.onclick = () => switchTab('login');
tabRegister.onclick = () => switchTab('register');

// 已登录则直接跳主界面，不用重新走一次登录表单
(async function redirectIfAlreadyLoggedIn() {
  try {
    const res = await fetch(`${API}/auth/me`);
    const data = await res.json();
    if (data.code === '0000') location.href = '/';
  } catch (e) { /* 忽略，留在登录页 */ }
})();

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearError();
  const email = $('loginEmail').value.trim();
  const password = $('loginPassword').value;
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (data.code === '0000') {
      location.href = '/';
    } else {
      showError(data.info || '登录失败');
    }
  } catch (e) {
    showError('请求失败：' + (e.message || e));
  }
});

registerForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearError();
  const orgName = $('regOrgName').value.trim();
  const email = $('regEmail').value.trim();
  const password = $('regPassword').value;
  if (password.length < 8) { showError('密码至少 8 位'); return; }
  try {
    const res = await fetch(`${API}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, orgName }),
    });
    const data = await res.json();
    if (data.code === '0000') {
      location.href = '/';
    } else {
      showError(data.info || '注册失败');
    }
  } catch (e) {
    showError('请求失败：' + (e.message || e));
  }
});
