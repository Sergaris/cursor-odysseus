/**
 * Open deep-research visual reports in the desktop WebView (not external browser).
 */

const _apiBase = () => {
  if (typeof window !== 'undefined' && window.API_BASE) return window.API_BASE;
  if (typeof window !== 'undefined' && window.location?.origin) return window.location.origin;
  return '';
};

export function researchReportPath(sessionId) {
  const id = encodeURIComponent(String(sessionId || '').trim());
  return `/api/research/report/${id}`;
}

export async function openResearchReport(sessionId) {
  const id = String(sessionId || '').trim();
  if (!id) return false;

  const base = _apiBase();
  try {
    const res = await fetch(`${base}/api/research/report-link/${encodeURIComponent(id)}`, {
      credentials: 'same-origin',
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = body.error || body.detail || detail;
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    const payload = await res.json();
    const path = payload.url || payload.path || researchReportPath(id);
    const url = path.startsWith('http') ? path : `${base}${path}`;
    window.open(url, '_blank', 'noopener');
    return true;
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (window.uiModule && typeof window.uiModule.showError === 'function') {
      window.uiModule.showError('Could not open research report: ' + msg);
    } else {
      alert('Could not open research report: ' + msg);
    }
    return false;
  }
}

if (typeof window !== 'undefined') {
  window.openResearchReport = openResearchReport;
}
