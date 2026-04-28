import sys, os, json, re, time, subprocess
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'memory'))

_IS_WIN = os.name == 'nt'
_ORIG_POPEN = getattr(subprocess, '_ga_orig_popen', subprocess.Popen)
_ORIG_RUN = getattr(subprocess, '_ga_orig_run', subprocess.run)
_ORIG_CALL = getattr(subprocess, '_ga_orig_call', subprocess.call)
_ORIG_CHECK_CALL = getattr(subprocess, '_ga_orig_check_call', subprocess.check_call)
_ORIG_CHECK_OUTPUT = getattr(subprocess, '_ga_orig_check_output', subprocess.check_output)
subprocess._ga_orig_popen = _ORIG_POPEN
subprocess._ga_orig_run = _ORIG_RUN
subprocess._ga_orig_call = _ORIG_CALL
subprocess._ga_orig_check_call = _ORIG_CHECK_CALL
subprocess._ga_orig_check_output = _ORIG_CHECK_OUTPUT

def _hide_window_kwargs(k):
    """Default child processes spawned inside code_run to no-window on Windows."""
    if not _IS_WIN:
        return k
    k = dict(k)
    if 'creationflags' not in k:
        k['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if 'startupinfo' not in k:
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            k['startupinfo'] = si
        except Exception:
            pass
    return k

def _d(b):
    if not b: return ''
    if isinstance(b, str): return b
    try: return b.decode()
    except: return b.decode('gbk', 'replace')

def _popen(*a, **k):
    return _ORIG_POPEN(*a, **_hide_window_kwargs(k))

def _run(*a, **k):
    t = k.pop('text', 0) | k.pop('universal_newlines', 0)
    enc = k.pop('encoding', None)
    k.pop('errors', None)
    if enc: t = 1
    if t and isinstance(k.get('input'), str):
        k['input'] = k['input'].encode()
    r = _ORIG_RUN(*a, **_hide_window_kwargs(k))
    if t:
        if r.stdout is not None: r.stdout = _d(r.stdout)
        if r.stderr is not None: r.stderr = _d(r.stderr)
    return r

def _call(*a, **k):
    return _ORIG_CALL(*a, **_hide_window_kwargs(k))

def _check_call(*a, **k):
    return _ORIG_CHECK_CALL(*a, **_hide_window_kwargs(k))

def _check_output(*a, **k):
    return _ORIG_CHECK_OUTPUT(*a, **_hide_window_kwargs(k))

subprocess.Popen = _popen
subprocess.run = _run
subprocess.call = _call
subprocess.check_call = _check_call
subprocess.check_output = _check_output
_Pi = subprocess.Popen.__init__
def _pinit(self, *a, **k):
    if os.name == 'nt': k['creationflags'] = (k.get('creationflags') or 0) | 0x08000000
    _Pi(self, *a, **k)
subprocess.Popen.__init__ = _pinit
sys.excepthook = lambda t, v, tb: (sys.__excepthook__(t, v, tb), print(f"\n[Agent Hint]: NO GUESSING! You MUST probe first. If missing common package, pip.")) if issubclass(t, (ImportError, AttributeError)) else sys.__excepthook__(t, v, tb)
