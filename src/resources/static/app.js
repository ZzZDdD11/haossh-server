const API = location.origin + '/api/v1';
const state = {
  connectionId: null,
  conversationId: null,
  savedConnectionId: null,
  connected: false,
  streaming: false,
};

const $ = id => document.getElementById(id);
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

function now() {
  const d = new Date();
  return d.toTimeString().slice(0, 8) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2600);
}

function setStatus(s) {
  const dot = $('statusDot'), text = $('statusText');
  dot.className = 'status-dot';
  text.className = 'status-text';
  if (s === 'connected') { dot.classList.add('connected'); text.classList.add('connected'); text.textContent = 'connected'; }
  else if (s === 'connecting') { dot.classList.add('connecting'); text.classList.add('connecting'); text.textContent = 'connecting...'; }
  else { text.textContent = 'disconnected'; }
  $('info-state').textContent = s;
}

function log(tag, content, type = '') {
  const body = $('logBody');
  const empty = body.querySelector('.log-empty');
  if (empty) empty.remove();

  const entry = el('div', 'log-entry ' + (type || tag.toLowerCase()));
  const time = el('span', 'log-time'); time.textContent = now();
  const ttag = el('span', 'log-tag'); ttag.textContent = tag;
  entry.appendChild(time); entry.appendChild(ttag);

  if (content) {
    const c = el('div', 'log-content');
    if (tag === 'STREAM') {
      c.innerHTML = '<span class="data-prefix">data: </span><span class="data-text"></span>';
      c.querySelector('.data-text').textContent = content;
    } else if (content === '[DONE]') {
      c.innerHTML = '<span class="data-prefix">data: </span><span class="data-done">[DONE]</span>';
    } else {
      c.textContent = content;
    }
    entry.appendChild(c);
  }
  body.appendChild(entry);
  body.scrollTop = body.scrollHeight;
}

$('clearLog').onclick = () => {
  $('logBody').innerHTML = '<div class="log-empty">等待事件流...</div>';
};

// ── 历史连接 ─────────────────────────────────
let _savedConnections = [];

async function loadConnections() {
  try {
    const res = await fetch(`${API}/ssh/connection_list?userId=default`);
    const data = await res.json();
    if (data.code === '0000' && data.data && data.data.length > 0) {
      _savedConnections = data.data;
      const sel = $('savedConnections');
      data.data.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.connectionId;
        opt.textContent = `${c.connectionName} (${c.host})`;
        sel.appendChild(opt);
      });
      $('savedConnectionsGroup').style.display = '';
    }
  } catch (e) { /* 静默失败，不影响手动填写 */ }
}

$('savedConnections').onchange = function() {
  const cid = this.value;
  state.savedConnectionId = cid || null;
  if (cid) {
    const conn = _savedConnections.find(c => c.connectionId === cid);
    if (conn) {
      $('host').value = conn.host;
      $('port').value = conn.port;
      $('username').value = conn.username;
      $('password').value = '';
      $('password').placeholder = '已保存，无需重填';
    }
  }
};

// ── 连接管理 ─────────────────────────────────
async function connect() {
  // 选了历史连接且密码为空 → 用 connectionId 连接（后端从 DB 读密码）
  if (state.savedConnectionId && !$('password').value) {
    const conn = _savedConnections.find(c => c.connectionId === state.savedConnectionId);
    const host = conn ? conn.host : '';
    const username = conn ? conn.username : '';
    const port = conn ? conn.port : 22;
    setStatus('connecting');
    $('connectBtn').disabled = true;
    log('REQ', `POST /ssh/connect?connectionId=${state.savedConnectionId}`, 'req');
    try {
      const res = await fetch(`${API}/ssh/connect?connectionId=${state.savedConnectionId}`, { method: 'POST' });
      const data = await res.json();
      if (data.code === '0000' && data.data && data.data.connectionId) {
        state.connectionId = data.data.connectionId;
        localStorage.setItem('lastConnectionId', state.connectionId);
        state.connected = true;
        setStatus('connected');
        $('connIdDisplay').style.display = '';
        $('connIdVal').textContent = state.connectionId.slice(0, 12) + '...';
        $('info-host').textContent = host;
        $('info-user').textContent = username;
        $('info-port').textContent = port;
        $('connectBtn').disabled = false;
        $('disconnectBtn').disabled = false;
        $('chatInput').focus();
        log('RES', `connectionId=${state.connectionId}`, 'res');
        log('SYS', 'SSH 连接已建立（从历史记录）', 'sys');
      } else {
        throw new Error(data.info || '连接失败');
      }
    } catch (e) {
      setStatus('disconnected');
      $('connectBtn').disabled = false;
      log('ERR', String(e.message || e), 'err');
      toast('连接失败：' + (e.message || e));
    }
    return;
  }

  const host = $('host').value.trim();
  const port = parseInt($('port').value) || 22;
  const username = $('username').value.trim();
  const password = $('password').value;

  if (!host || !username || !password) {
    toast('请填写 host / username / password');
    return;
  }

  setStatus('connecting');
  $('connectBtn').disabled = true;
  log('REQ', `POST /ssh/connect  host=${host}:${port} user=${username}`, 'req');

  try {
    const res = await fetch(`${API}/ssh/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host, port, username, password }),
    });
    const data = await res.json();
    if (data.code === '0000' && data.data && data.data.connectionId) {
      state.connectionId = data.data.connectionId;
      state.connected = true;
      setStatus('connected');
      $('connIdDisplay').style.display = '';
      $('connIdVal').textContent = state.connectionId.slice(0, 12) + '...';
      $('info-host').textContent = host;
      $('info-user').textContent = username;
      $('info-port').textContent = port;
      $('connectBtn').disabled = false;
      $('disconnectBtn').disabled = false;
      $('chatInput').disabled = false;
      $('sendBtn').disabled = false;
      $('chatInput').focus();
      log('RES', `connectionId=${state.connectionId}`, 'res');
      log('SYS', 'SSH 连接已建立，工具已对 Agent 可见', 'sys');
    } else {
      throw new Error(data.info || '连接失败');
    }
  } catch (e) {
    setStatus('disconnected');
    $('connectBtn').disabled = false;
    log('ERR', String(e.message || e), 'err');
    toast('连接失败：' + (e.message || e));
  }
}

async function disconnect() {
  if (!state.connectionId) return;
  log('REQ', `POST /ssh/disconnect?connectionId=${state.connectionId}`, 'req');
  try {
    await fetch(`${API}/ssh/disconnect?connectionId=${state.connectionId}`, { method: 'POST' });
    log('RES', 'disconnected', 'res');
  } catch (e) {
    log('ERR', String(e), 'err');
  }
  resetConn();
}

function resetConn() {
  state.connectionId = null;
  localStorage.removeItem('lastConnectionId');
  state.connected = false;
  setStatus('disconnected');
  $('connIdDisplay').style.display = 'none';
  $('connectBtn').disabled = false;
  $('disconnectBtn').disabled = true;
  $('info-host').textContent = '—';
  $('info-user').textContent = '—';
  $('info-port').textContent = '—';
}

$('connectBtn').onclick = connect;
$('disconnectBtn').onclick = disconnect;

let abortController = null;

// ── 对话 ─────────────────────────────────────
function appendMessage(role, text) {
  $('emptyState')?.remove();
  const msgs = $('messages');
  const m = el('div', 'message ' + role);
  const meta = el('div', 'message-meta');
  const roleSpan = el('span', 'role'); roleSpan.textContent = role === 'user' ? 'YOU' : 'AGENT';
  const timeSpan = el('span', 'time'); timeSpan.textContent = now();
  meta.appendChild(roleSpan); meta.appendChild(timeSpan);
  const bubble = el('div', 'message-bubble'); bubble.textContent = text;
  m.appendChild(meta); m.appendChild(bubble);
  msgs.appendChild(m);
  msgs.scrollTop = msgs.scrollHeight;
  return bubble;
}

// 创建 AI 消息（空 bubble，后续按事件增量填充）
function createAiMessage() {
  $('emptyState')?.remove();
  const msgs = $('messages');
  const m = el('div', 'message ai streaming');
  const meta = el('div', 'message-meta');
  const roleSpan = el('span', 'role'); roleSpan.textContent = 'AGENT';
  const timeSpan = el('span', 'time'); timeSpan.textContent = now();
  meta.appendChild(roleSpan); meta.appendChild(timeSpan);
  const bubble = el('div', 'message-bubble');
  m.appendChild(meta); m.appendChild(bubble);
  msgs.appendChild(m);
  return { m, bubble };
}

async function sendMessage() {
  const input = $('chatInput');
  const text = input.value.trim();
  if (!text || state.streaming) return;

  appendMessage('user', text);
  input.value = '';
  input.style.height = 'auto';
  state.streaming = true;
  $('sendBtn').textContent = 'stop';
  $('sendBtn').classList.add('danger');
  abortController = new AbortController();

  const { m, bubble } = createAiMessage();
  const msgs = $('messages');

  // 渲染状态：维护当前文本/思考块指针，遇到工具调用则另起新块
  let currentText = null;
  let currentThink = null;
  const toolCards = {};  // call_id -> card element

  const endText = () => { currentText = null; };
  const endThink = () => { currentThink = null; };

  const ensureText = () => {
    if (!currentText) {
      currentText = el('div', 'text-content');
      bubble.appendChild(currentText);
    }
    return currentText;
  };
  const ensureThink = () => {
    if (!currentThink) {
      currentThink = el('details', 'thinking-block');
      const lbl = el('summary', 'thinking-label'); lbl.textContent = 'thinking';
      const c = el('div', 'thinking-content');
      currentThink.appendChild(lbl); currentThink.appendChild(c);
      currentThink._c = c;
      bubble.appendChild(currentThink);
    }
    return currentThink;
  };

  log('REQ', `POST /chat_stream  message="${text.slice(0, 80)}${text.length > 80 ? '...' : ''}"`, 'req');

  try {
    const res = await fetch(`${API}/chat_stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.connectionId || '',
        conversation_id: state.conversationId,
        message: text,
        terminal_session_id: null,
      }),
      signal: abortController.signal,
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data: ')) continue;
        const raw = trimmed.slice(6);
        let evt;
        try { evt = JSON.parse(raw); } catch { continue; }

        // 非 text/thinking 事件结束当前文本/思考块（保证工具卡片独立成块）
        if (evt.type !== 'text') endText();
        if (evt.type !== 'thinking') endThink();

        if (evt.type === 'text') {
          ensureText().textContent += evt.delta;
          log('STREAM', `text: ${evt.delta}`);
        } else if (evt.type === 'thinking') {
          ensureThink()._c.textContent += evt.delta;
          log('STREAM', `think: ${evt.delta.slice(0, 40)}`);
        } else if (evt.type === 'tool_call') {
          // 工具调用卡片
          const card = el('details', 'tool-card');
          const hdr = el('summary', 'tool-card-header');
          hdr.innerHTML = `<span class="tool-icon">▶</span><span class="tool-name">${evt.tool}</span><span class="tool-status running">running</span>`;
          const body = el('div', 'tool-card-body');
          const sec = el('div', 'tool-card-section');
          sec.innerHTML = '<div class="tool-card-label">args</div>';
          const v = el('div', 'tool-card-value args');
          v.textContent = typeof evt.args === 'string' ? evt.args : JSON.stringify(evt.args, null, 2);
          sec.appendChild(v); body.appendChild(sec);
          card.appendChild(hdr); card.appendChild(body);
          bubble.appendChild(card);
          toolCards[evt.call_id] = card;
          log('TOOL', `${evt.tool}(${JSON.stringify(evt.args).slice(0, 80)})`, 'sys');
        } else if (evt.type === 'tool_result') {
          const card = toolCards[evt.call_id];
          const ok = evt.outcome === 'success';
          if (card) {
            // 更新已有卡片状态 + 追加 result 区块
            const st = card.querySelector('.tool-status');
            st.textContent = evt.outcome || 'done';
            st.className = 'tool-status ' + (ok ? 'done' : 'failed');
            const body = card.querySelector('.tool-card-body');
            const sec = el('div', 'tool-card-section');
            sec.innerHTML = '<div class="tool-card-label">result</div>';
            const v = el('div', 'tool-card-value result' + (ok ? '' : ' failed'));
            v.textContent = evt.result;
            sec.appendChild(v); body.appendChild(sec);
          } else {
            // 无对应 call 卡片，单独建结果卡片
            const c2 = el('details', 'tool-card');
            const h2 = el('summary', 'tool-card-header');
            h2.innerHTML = `<span class="tool-icon">◀</span><span class="tool-name">${evt.tool}</span><span class="tool-status ${ok ? 'done' : 'failed'}">${evt.outcome}</span>`;
            const b2 = el('div', 'tool-card-body');
            const s2 = el('div', 'tool-card-section');
            s2.innerHTML = '<div class="tool-card-label">result</div>';
            const v2 = el('div', 'tool-card-value result' + (ok ? '' : ' failed'));
            v2.textContent = evt.result;
            s2.appendChild(v2); b2.appendChild(s2);
            c2.appendChild(h2); c2.appendChild(b2);
            bubble.appendChild(c2);
          }
          log('TOOL', `${evt.tool} → ${(evt.result || '').slice(0, 100)}`, ok ? 'res' : 'err');
        } else if (evt.type === 'done') {
          if (evt.conversation_id) {
            state.conversationId = evt.conversation_id;
            localStorage.setItem('lastConversationId', evt.conversation_id);
          }
          log('STREAM', '[DONE]', 'res');
        } else if (evt.type === 'error') {
          ensureText().textContent += '⚠ ' + evt.message;
          log('ERR', evt.message, 'err');
        }
        msgs.scrollTop = msgs.scrollHeight;
      }
    }

    // 空响应兜底
    if (!bubble.querySelector('.text-content, .tool-card, .thinking-block')) {
      bubble.textContent = '(空响应)';
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      // 用户主动停止，不算错误
      if (!bubble.querySelector('.text-content, .tool-card')) {
        bubble.textContent = '（已停止）';
      }
    } else {
      if (!bubble.querySelector('.text-content, .tool-card')) {
        bubble.textContent = '⚠ ' + (e.message || e);
      }
      log('ERR', String(e.message || e), 'err');
      toast('请求失败：' + (e.message || e));
    }
  } finally {
    m.classList.remove('streaming');
    state.streaming = false;
    $('sendBtn').textContent = 'send';
    $('sendBtn').classList.remove('danger');
    $('sendBtn').disabled = false;
    $('chatInput').focus();
  }
}

$('sendBtn').onclick = function() {
  if (state.streaming && abortController) {
    abortController.abort();
    log('SYS', '已停止生成', 'sys');
  } else {
    sendMessage();
  }
};
$('chatInput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
$('chatInput').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

// 页面加载：拉取历史连接列表
loadConnections();

// 页面加载：恢复上次 SSH 连接状态
async function loadLastConnection() {
  const lastId = localStorage.getItem('lastConnectionId');
  if (!lastId) return;
  try {
    const res = await fetch(`${API}/ssh/is_connected?connectionId=${lastId}`);
    const data = await res.json();
    if (data.code === '0000' && data.data?.connected) {
      state.connectionId = lastId;
      state.savedConnectionId = lastId;
      state.connected = true;
      setStatus('connected');
      $('connIdDisplay').style.display = '';
      $('connIdVal').textContent = lastId.slice(0, 12) + '...';
      $('disconnectBtn').disabled = false;
      // 获取连接信息填充 UI
      const connRes = await fetch(`${API}/ssh/get_connection?connectionId=${lastId}`);
      const connData = await connRes.json();
      if (connData.code === '0000' && connData.data) {
        $('info-host').textContent = connData.data.host;
        $('info-user').textContent = connData.data.username;
        $('info-port').textContent = connData.data.port;
      }
      log('SYS', 'SSH 连接已恢复', 'sys');
    }
  } catch (e) { /* 静默失败 */ }
}

loadLastConnection();

// 页面加载：恢复上次对话
async function loadLastConversation() {
  const lastId = localStorage.getItem('lastConversationId');
  if (!lastId) return;
  try {
    const res = await fetch(`${API}/conversation/${lastId}/messages`);
    const data = await res.json();
    if (data.code === '0000' && data.data?.messages?.length > 0) {
      state.conversationId = lastId;
      renderHistory(data.data.messages);
      log('SYS', `已恢复上次对话（${data.data.messages.length} 条消息）`, 'sys');
    }
  } catch (e) { /* 静默失败，不影响新对话 */ }
}

function renderHistory(messages) {
  $('emptyState')?.remove();
  const msgs = $('messages');
  for (const msg of messages) {
    if (msg.role === 'user') {
      appendMessage('user', msg.content);
    } else {
      const { m, bubble } = createAiMessage();
      if (msg.content) {
        const text = el('div', 'text-content');
        text.textContent = msg.content;
        bubble.appendChild(text);
      }
      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          const card = el('details', 'tool-card');
          const hdr = el('summary', 'tool-card-header');
          const hasResult = !!tc.result;
          hdr.innerHTML = `<span class="tool-icon">▶</span><span class="tool-name">${tc.tool}</span><span class="tool-status ${hasResult ? 'done' : 'running'}">${hasResult ? 'done' : 'running'}</span>`;
          const body = el('div', 'tool-card-body');
          const argsSec = el('div', 'tool-card-section');
          argsSec.innerHTML = '<div class="tool-card-label">args</div>';
          const argsVal = el('div', 'tool-card-value args');
          argsVal.textContent = typeof tc.args === 'string' ? tc.args : JSON.stringify(tc.args, null, 2);
          argsSec.appendChild(argsVal); body.appendChild(argsSec);
          if (tc.result) {
            const rSec = el('div', 'tool-card-section');
            rSec.innerHTML = '<div class="tool-card-label">result</div>';
            const rVal = el('div', 'tool-card-value result');
            rVal.textContent = tc.result;
            rSec.appendChild(rVal); body.appendChild(rSec);
          }
          card.appendChild(hdr); card.appendChild(body);
          bubble.appendChild(card);
        }
      }
    }
  }
  msgs.scrollTop = msgs.scrollHeight;
}

loadLastConversation();

// 初始日志
log('SYS', '调试控制台已就绪。填写左侧连接信息建立 SSH 会话。', 'sys');
