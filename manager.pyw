import atexit
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import urllib.request

try:
    import webview
except ImportError:
    raise SystemExit("pywebview is required. Install with: pip install pywebview")

APP_TITLE = "GenericAgent Manager"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 900
PORT_LOW = 18501
PORT_HIGH = 18599

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTENDS_DIR = os.path.join(BASE_DIR, "frontends")
STAPP_PATH = os.path.join(FRONTENDS_DIR, "stapp.py")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_free_port(lo=PORT_LOW, hi=PORT_HIGH, used=None):
    used = set(used or [])
    ports = list(range(lo, hi + 1))
    random.shuffle(ports)
    for port in ports:
        if port in used:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    raise RuntimeError(f"No free port in {lo}-{hi}")


def wait_until_ready(port, timeout=30):
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if 200 <= resp.status < 500:
                    return True, ""
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    return False, last_error or "timeout"


class AgentManagerApi:
    def __init__(self):
        self._lock = threading.RLock()
        self._agents = {}
        self._next_id = 1
        self._window = None

    def set_window(self, window):
        self._window = window

    def _snapshot_locked(self):
        agents = []
        for agent_id, item in sorted(self._agents.items(), key=lambda kv: kv[0]):
            proc = item["proc"]
            alive = proc.poll() is None
            if alive:
                status = "running"
            elif item.get("stopped_by_manager"):
                status = "stopped"
            else:
                status = f"stopped ({proc.returncode})"
            agents.append({
                "id": agent_id,
                "name": item["name"],
                "port": item["port"],
                "url": item["url"],
                "pid": proc.pid,
                "status": status,
                "running": alive,
                "created_at": item["created_at"],
                "last_error": item.get("last_error", ""),
            })
        return agents

    def list_agents(self):
        with self._lock:
            return self._snapshot_locked()

    def start_agent(self, name=None, llm_no=0):
        """Start one headless Streamlit GenericAgent and return current list."""
        if not os.path.exists(STAPP_PATH):
            raise RuntimeError(f"Cannot find {STAPP_PATH}")

        with self._lock:
            used_ports = [item["port"] for item in self._agents.values()
                          if item["proc"].poll() is None]
            port = find_free_port(used=used_ports)
            agent_id = self._next_id
            self._next_id += 1
            agent_name = (name or "").strip() or f"Agent {agent_id}"

        cmd = [
            sys.executable, "-m", "streamlit", "run", STAPP_PATH,
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--server.headless", "true",
            "--server.enableCORS", "false",
            "--server.enableXsrfProtection", "false",
            "--browser.gatherUsageStats", "false",
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["GA_MANAGER_AGENT_ID"] = str(agent_id)
        env["GA_MANAGER_AGENT_NAME"] = agent_name

        stdout_path = os.path.join(BASE_DIR, "temp", f"manager_agent_{agent_id}_{port}.log")
        os.makedirs(os.path.dirname(stdout_path), exist_ok=True)
        stdout_file = open(stdout_path, "a", encoding="utf-8", errors="replace")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                stdout=stdout_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception:
            stdout_file.close()
            raise

        item = {
            "name": agent_name,
            "port": port,
            "url": f"http://127.0.0.1:{port}",
            "proc": proc,
            "stdout_file": stdout_file,
            "stdout_path": stdout_path,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_error": "",
        }
        with self._lock:
            self._agents[agent_id] = item

        def _waiter():
            ok, err = wait_until_ready(port)
            with self._lock:
                current = self._agents.get(agent_id)
                if current is not item:
                    return
                if proc.poll() is not None:
                    if not current.get("stopped_by_manager"):
                        current["last_error"] = f"process exited early: {proc.returncode}"
                elif not ok:
                    current["last_error"] = f"startup check timed out: {err}"
            self._notify_status()

        threading.Thread(target=_waiter, daemon=True).start()
        self._notify_status()
        return self.list_agents()

    def stop_agent(self, agent_id):
        agent_id = int(agent_id)
        with self._lock:
            item = self._agents.get(agent_id)
        if not item:
            return self.list_agents()
        self._stop_item(item)
        self._notify_status()
        return self.list_agents()

    def stop_all(self):
        with self._lock:
            items = list(self._agents.values())
        for item in items:
            self._stop_item(item)
        self._notify_status()
        return self.list_agents()

    def remove_stopped(self):
        with self._lock:
            for agent_id, item in list(self._agents.items()):
                if item["proc"].poll() is not None:
                    self._close_log(item)
                    del self._agents[agent_id]
            return self._snapshot_locked()

    def open_external(self, agent_id):
        agent_id = int(agent_id)
        with self._lock:
            item = self._agents.get(agent_id)
            url = item["url"] if item else ""
        if not url:
            return False
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def shutdown(self):
        self.stop_all()
        with self._lock:
            for item in self._agents.values():
                self._close_log(item)
        return True

    def _stop_item(self, item):
        proc = item["proc"]
        item["stopped_by_manager"] = True
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self._close_log(item)

    def _close_log(self, item):
        f = item.get("stdout_file")
        if f and not f.closed:
            try:
                f.flush()
                f.close()
            except Exception:
                pass

    def _notify_status(self):
        if not self._window:
            return
        try:
            self._window.evaluate_js("window.refreshAgents && window.refreshAgents()")
        except Exception:
            pass


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>GenericAgent Manager</title>
<style>
:root {
  --bg: #0f172a;
  --panel: #111827;
  --panel2: #172033;
  --text: #e5e7eb;
  --muted: #94a3b8;
  --line: #263244;
  --accent: #38bdf8;
  --danger: #f87171;
  --ok: #34d399;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; overflow: hidden; background: var(--bg); color: var(--text); font-family: "Segoe UI", Arial, sans-serif; }
#app { height: 100%; display: grid; grid-template-columns: 280px 1fr; }
#sidebar { background: var(--panel); border-right: 1px solid var(--line); padding: 14px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.brand { font-size: 20px; font-weight: 700; letter-spacing: .2px; }
.hint { font-size: 12px; color: var(--muted); line-height: 1.5; }
.actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
button { border: 0; border-radius: 8px; padding: 9px 10px; color: white; background: #334155; cursor: pointer; font-weight: 600; }
button:hover { filter: brightness(1.15); }
button:disabled { opacity: .55; cursor: not-allowed; }
.primary { background: #0284c7; }
.danger { background: #b91c1c; }
.ghost { background: #273244; }
#agentList { overflow-y: auto; flex: 1; padding-right: 2px; }
.agent { border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin-bottom: 8px; background: var(--panel2); cursor: pointer; }
.agent.active { border-color: var(--accent); box-shadow: 0 0 0 1px rgba(56,189,248,.35) inset; }
.agent .top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.agent .name { font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent .meta { color: var(--muted); font-size: 12px; margin-top: 5px; line-height: 1.4; word-break: break-all; }
.badge { font-size: 11px; border-radius: 999px; padding: 2px 7px; white-space: nowrap; }
.badge.running { background: rgba(52,211,153,.16); color: var(--ok); }
.badge.stopped { background: rgba(248,113,113,.16); color: var(--danger); }
.rowBtns { display: flex; gap: 6px; margin-top: 9px; }
.rowBtns button { padding: 6px 8px; font-size: 12px; flex: 1; }
#main { min-width: 0; display: flex; flex-direction: column; background: #020617; }
#topbar { height: 48px; display: flex; align-items: center; justify-content: space-between; padding: 0 14px; border-bottom: 1px solid var(--line); background: #0b1120; }
#title { font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#status { color: var(--muted); font-size: 12px; }
#frameWrap { flex: 1; min-height: 0; position: relative; background: #0f172a; }
iframe { width: 100%; height: 100%; border: 0; background: white; display: none; }
iframe.active { display: block; }
#empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; text-align: center; color: var(--muted); padding: 24px; }
#empty h2 { margin: 0 0 8px; color: var(--text); }
#toast { position: fixed; right: 16px; bottom: 16px; max-width: 520px; background: rgba(15,23,42,.96); color: var(--text); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; display: none; box-shadow: 0 12px 35px rgba(0,0,0,.35); white-space: pre-wrap; }
.small { font-size: 12px; color: var(--muted); }
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <div>
      <div class="brand">GenericAgent Manager</div>
      <div class="hint">在一个窗口中启动、切换和关闭多个 GenericAgent。每个 Agent 独立运行在一个本地 Streamlit 端口。</div>
    </div>
    <div class="actions">
      <button class="primary" id="newBtn" onclick="startAgent()">＋ 启动 Agent</button>
      <button class="danger" onclick="stopAll()">全部关闭</button>
      <button class="ghost" onclick="refreshAgents()">刷新</button>
      <button class="ghost" onclick="removeStopped()">清理已停</button>
    </div>
    <div id="agentList"></div>
    <div class="small">提示：关闭本管理窗口会自动关闭由它启动的所有 Agent。</div>
  </aside>
  <main id="main">
    <div id="topbar">
      <div id="title">未选择 Agent</div>
      <div id="status">ready</div>
    </div>
    <div id="frameWrap">
      <div id="empty">
        <div>
          <h2>还没有运行中的 Agent</h2>
          <div>点击左侧“启动 Agent”，新 Agent 会在这里打开，不再产生额外桌面窗口。</div>
        </div>
      </div>
    </div>
  </main>
</div>
<div id="toast"></div>
<script>
let agents = [];
let activeId = null;
let frames = new Map();

function apiReady() {
  return window.pywebview && window.pywebview.api;
}
function setStatus(text) {
  document.getElementById('status').textContent = text || '';
}
function toast(text) {
  const el = document.getElementById('toast');
  el.textContent = text;
  el.style.display = 'block';
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.style.display = 'none', 4200);
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function startAgent() {
  if (!apiReady()) return toast('管理 API 尚未就绪');
  const btn = document.getElementById('newBtn');
  btn.disabled = true;
  setStatus('starting...');
  try {
    const name = `Agent ${agents.length + 1}`;
    agents = await window.pywebview.api.start_agent(name, 0);
    pickNewestRunning();
    render();
    toast('已启动新 Agent');
  } catch (e) {
    toast('启动失败：' + (e && (e.message || e)));
  } finally {
    btn.disabled = false;
    setStatus('ready');
  }
}
async function refreshAgents() {
  if (!apiReady()) return;
  try {
    agents = await window.pywebview.api.list_agents();
    if (activeId && !agents.some(a => a.id === activeId && a.running)) activeId = null;
    if (!activeId) pickFirstRunning();
    render();
  } catch (e) {
    toast('刷新失败：' + (e && (e.message || e)));
  }
}
async function stopAgent(id) {
  if (!apiReady()) return;
  setStatus('stopping...');
  try {
    agents = await window.pywebview.api.stop_agent(id);
    const frame = frames.get(id);
    if (frame) {
      frame.remove();
      frames.delete(id);
    }
    if (activeId === id) {
      activeId = null;
      pickFirstRunning();
    }
    render();
  } catch (e) {
    toast('关闭失败：' + (e && (e.message || e)));
  } finally {
    setStatus('ready');
  }
}
async function stopAll() {
  if (!apiReady()) return;
  if (!confirm('确定关闭本管理器启动的所有 Agent？')) return;
  setStatus('stopping all...');
  try {
    agents = await window.pywebview.api.stop_all();
    for (const frame of frames.values()) frame.remove();
    frames.clear();
    activeId = null;
    render();
  } catch (e) {
    toast('全部关闭失败：' + (e && (e.message || e)));
  } finally {
    setStatus('ready');
  }
}
async function removeStopped() {
  if (!apiReady()) return;
  try {
    agents = await window.pywebview.api.remove_stopped();
    render();
  } catch (e) {
    toast('清理失败：' + (e && (e.message || e)));
  }
}
async function openExternal(id) {
  if (!apiReady()) return;
  await window.pywebview.api.open_external(id);
}
function pickNewestRunning() {
  const running = agents.filter(a => a.running);
  if (running.length) activeId = running[running.length - 1].id;
}
function pickFirstRunning() {
  const first = agents.find(a => a.running);
  activeId = first ? first.id : null;
}
function selectAgent(id) {
  activeId = id;
  render();
}
function ensureFrame(agent) {
  if (!agent.running) return null;
  let frame = frames.get(agent.id);
  if (!frame) {
    frame = document.createElement('iframe');
    frame.dataset.id = agent.id;
    frame.src = agent.url;
    frame.allow = 'clipboard-read; clipboard-write; fullscreen';
    document.getElementById('frameWrap').appendChild(frame);
    frames.set(agent.id, frame);
  }
  return frame;
}
function render() {
  const list = document.getElementById('agentList');
  list.innerHTML = '';
  for (const a of agents) {
    const item = document.createElement('div');
    item.className = 'agent' + (a.id === activeId ? ' active' : '');
    item.onclick = () => a.running && selectAgent(a.id);
    const statusClass = a.running ? 'running' : 'stopped';
    item.innerHTML = `
      <div class="top">
        <div class="name">${esc(a.name)}</div>
        <span class="badge ${statusClass}">${esc(a.status)}</span>
      </div>
      <div class="meta">PID ${esc(a.pid)} · Port ${esc(a.port)}<br>${esc(a.created_at)}${a.last_error ? '<br>⚠ ' + esc(a.last_error) : ''}</div>
      <div class="rowBtns">
        <button class="ghost" onclick="event.stopPropagation(); selectAgent(${a.id})" ${a.running ? '' : 'disabled'}>切换</button>
        <button class="ghost" onclick="event.stopPropagation(); openExternal(${a.id})" ${a.running ? '' : 'disabled'}>浏览器</button>
        <button class="danger" onclick="event.stopPropagation(); stopAgent(${a.id})" ${a.running ? '' : 'disabled'}>关闭</button>
      </div>`;
    list.appendChild(item);
  }

  let activeAgent = agents.find(a => a.id === activeId && a.running);
  const empty = document.getElementById('empty');
  document.getElementById('title').textContent = activeAgent ? `${activeAgent.name} - ${activeAgent.url}` : '未选择 Agent';

  for (const [id, frame] of frames.entries()) {
    const stillRunning = agents.some(a => a.id === id && a.running);
    if (!stillRunning) {
      frame.remove();
      frames.delete(id);
    }
  }
  for (const a of agents) ensureFrame(a);
  for (const [id, frame] of frames.entries()) {
    frame.classList.toggle('active', !!activeAgent && id === activeAgent.id);
  }
  empty.style.display = activeAgent ? 'none' : 'flex';
}
window.refreshAgents = refreshAgents;
window.addEventListener('pywebviewready', refreshAgents);
setInterval(refreshAgents, 3000);
</script>
</body>
</html>
"""


api = AgentManagerApi()


def on_closed():
    try:
        api.shutdown()
    except Exception:
        pass


def main():
    window = webview.create_window(
        APP_TITLE,
        html=HTML,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        resizable=True,
        text_select=True,
        js_api=api,
    )
    api.set_window(window)
    try:
        window.events.closed += on_closed
    except Exception:
        pass
    atexit.register(on_closed)
    webview.start()


if __name__ == "__main__":
    main()