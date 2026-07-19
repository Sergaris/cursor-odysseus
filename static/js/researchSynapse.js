// static/js/researchSynapse.js
//
// Live SVG visualization of a deep-research run: central query node with
// sub-question branches and source leaves that pop in as rounds progress.
// Driven imperatively by chat.js when SSE research_progress events arrive.
//
// Density rule: never draw one circle per source. Cap visible leaves per
// round and fold the rest into a "+N" badge so 100+ sources stay readable.

const SVG_NS = 'http://www.w3.org/2000/svg';

const PHASE_LABEL = {
  probing:   'verifying model',
  planning:  'planning strategy',
  searching: 'searching',
  reading:   'reading sources',
  analyzing: 'analyzing findings',
  writing:   'writing report',
  error:     'error',
  done:      'complete',
};

/** Hard cap on drawn leaf circles per round node. */
const MAX_VISIBLE_LEAVES = 5;
/** Max round hubs on the ring. */
const MAX_SUBS = 8;

function rand(a, b) { return Math.random() * (b - a) + a; }

export default function createResearchSynapse(container, opts = {}) {
  const W = 520, H = 220;
  const cx = W / 2, cy = H / 2;

  const wrap = document.createElement('div');
  wrap.className = 'research-synapse' + (opts.compact ? ' research-synapse-compact' : '');
  wrap.innerHTML = `
    <div class="rs-stage">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        <g class="rs-edges"></g>
        <g class="rs-nodes"></g>
        <circle class="rs-pulse" cx="${cx}" cy="${cy}" r="6"></circle>
      </svg>
    </div>
    <div class="rs-meta">
      <span class="rs-status">starting…</span>
      <span class="rs-sep">·</span>
      <span class="rs-round">round <b>0</b></span>
      <span class="rs-sep">·</span>
      <span class="rs-sources"><b>0</b> sources</span>
      <span class="rs-sep">·</span>
      <span class="rs-timer">00:00</span>
    </div>
  `;
  container.appendChild(wrap);

  const edgesG  = wrap.querySelector('.rs-edges');
  const nodesG  = wrap.querySelector('.rs-nodes');
  const statusE = wrap.querySelector('.rs-status');
  const roundE  = wrap.querySelector('.rs-round b');
  const srcE    = wrap.querySelector('.rs-sources b');
  const timerE  = wrap.querySelector('.rs-timer');

  // ── root (query) ───────────────────────────────────────────────
  const root = document.createElementNS(SVG_NS, 'circle');
  root.setAttribute('cx', cx); root.setAttribute('cy', cy);
  root.setAttribute('r', 11);
  root.setAttribute('class', 'rs-node rs-node-root');
  nodesG.appendChild(root);
  const rootLabel = document.createElementNS(SVG_NS, 'text');
  rootLabel.setAttribute('x', cx);
  rootLabel.setAttribute('y', cy + 28);
  rootLabel.setAttribute('text-anchor', 'middle');
  rootLabel.setAttribute('class', 'rs-label');
  rootLabel.textContent = _trunc(opts.query || 'query', 28);
  nodesG.appendChild(rootLabel);

  // { x, y, count, labelEl, leafEls: [{edge, node}], badgeEdge, badgeNode, badgeText }
  const subs = [];
  let sourceCount = 0;
  let lastRound = 0;
  let completed = false;
  let leafAnimToken = 0;

  // ── timer ──────────────────────────────────────────────────────
  const startedAt = opts.startedAt || Date.now();
  let timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    timerE.textContent =
      String(Math.floor(elapsed / 60)).padStart(2, '0') + ':' +
      String(elapsed % 60).padStart(2, '0');
  }, 1000);

  // ── helpers ────────────────────────────────────────────────────
  function _trunc(s, n) {
    if (!s) return '';
    s = String(s).replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function _subLabelText(sub, baseLabel) {
    const base = baseLabel || sub.baseLabel || '';
    if (!sub.count) return base;
    return `${base} · ${sub.count}`;
  }

  function _updateSubLabel(sub) {
    if (!sub.labelEl) return;
    sub.labelEl.textContent = _trunc(_subLabelText(sub), 16);
  }

  function _badgePos(sub) {
    const baseAngle = Math.atan2(sub.y - cy, sub.x - cx);
    const r = 34;
    return {
      x: sub.x + Math.cos(baseAngle) * r,
      y: sub.y + Math.sin(baseAngle) * r,
    };
  }

  function _ensureBadge(sub) {
    if (sub.badgeNode) return;
    const pos = _badgePos(sub);
    const edge = document.createElementNS(SVG_NS, 'line');
    edge.setAttribute('x1', sub.x); edge.setAttribute('y1', sub.y);
    edge.setAttribute('x2', pos.x); edge.setAttribute('y2', pos.y);
    edge.setAttribute('class', 'rs-edge');
    edgesG.appendChild(edge);

    const node = document.createElementNS(SVG_NS, 'circle');
    node.setAttribute('cx', pos.x); node.setAttribute('cy', pos.y);
    node.setAttribute('r', 9);
    node.setAttribute('class', 'rs-node rs-node-badge');
    nodesG.appendChild(node);

    const text = document.createElementNS(SVG_NS, 'text');
    text.setAttribute('x', pos.x);
    text.setAttribute('y', pos.y + 3.5);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('class', 'rs-badge-label');
    nodesG.appendChild(text);

    sub.badgeEdge = edge;
    sub.badgeNode = node;
    sub.badgeText = text;
  }

  function _updateBadge(sub) {
    const overflow = Math.max(0, sub.count - (sub.collapsed ? 0 : Math.min(sub.leafEls.length, MAX_VISIBLE_LEAVES)));
    const showCount = sub.collapsed ? sub.count : overflow;
    if (showCount <= 0 && !sub.collapsed) {
      if (sub.badgeNode) {
        sub.badgeEdge?.remove();
        sub.badgeNode.remove();
        sub.badgeText?.remove();
        sub.badgeEdge = sub.badgeNode = sub.badgeText = null;
      }
      return;
    }
    _ensureBadge(sub);
    const label = sub.collapsed ? String(sub.count) : `+${showCount}`;
    sub.badgeText.textContent = label;
    sub.badgeNode.classList.add('rs-node-new');
    setTimeout(() => sub.badgeNode?.classList.remove('rs-node-new'), 600);
  }

  /** Older rounds: drop individual leaves, keep a single count badge. */
  function _collapseSub(sub) {
    if (sub.collapsed) {
      _updateBadge(sub);
      _updateSubLabel(sub);
      return;
    }
    sub.collapsed = true;
    for (const leaf of sub.leafEls) {
      leaf.edge?.remove();
      leaf.node?.remove();
    }
    sub.leafEls = [];
    _updateBadge(sub);
    _updateSubLabel(sub);
  }

  function _addSub(label) {
    if (subs.length >= MAX_SUBS) {
      // Ring is full — keep attributing activity to the last hub.
      return null;
    }
    // Collapse previous active round so only the newest keeps leaf fans.
    if (subs.length) _collapseSub(subs[subs.length - 1]);

    const slot = subs.length;
    const totalSlots = Math.max(6, Math.min(MAX_SUBS, subs.length + 1));
    const angle = (slot / totalSlots) * Math.PI * 2 - Math.PI / 2;
    // Pull hubs slightly inward when the ring is crowded.
    const r = subs.length >= 6 ? 68 : 78;
    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r;

    const edge = document.createElementNS(SVG_NS, 'line');
    edge.setAttribute('x1', cx); edge.setAttribute('y1', cy);
    edge.setAttribute('x2', x);  edge.setAttribute('y2', y);
    edge.setAttribute('class', 'rs-edge rs-edge-firing');
    edgesG.appendChild(edge);
    setTimeout(() => edge.classList.remove('rs-edge-firing'), 1100);

    const n = document.createElementNS(SVG_NS, 'circle');
    n.setAttribute('cx', x); n.setAttribute('cy', y); n.setAttribute('r', 7);
    n.setAttribute('class', 'rs-node rs-node-sub rs-node-new');
    nodesG.appendChild(n);

    const baseLabel = label || `R${slot + 1}`;
    const t = document.createElementNS(SVG_NS, 'text');
    const lx = cx + Math.cos(angle) * (r + 14);
    const ly = cy + Math.sin(angle) * (r + 14);
    t.setAttribute('x', lx); t.setAttribute('y', ly + 3);
    t.setAttribute('text-anchor', Math.cos(angle) > 0.15 ? 'start' :
                                  Math.cos(angle) < -0.15 ? 'end' : 'middle');
    t.setAttribute('class', 'rs-label rs-label-sub');
    t.textContent = _trunc(baseLabel, 14);
    nodesG.appendChild(t);

    const sub = {
      x, y, count: 0, collapsed: false,
      baseLabel, labelEl: t,
      leafEls: [],
      badgeEdge: null, badgeNode: null, badgeText: null,
    };
    subs.push(sub);
    return sub;
  }

  function _addVisibleLeaf(sub) {
    const baseAngle = Math.atan2(sub.y - cy, sub.x - cx);
    const idx = sub.leafEls.length;
    const perRing = MAX_VISIBLE_LEAVES;
    const slot = idx % perRing;
    const arcSpan = 1.8;
    const angle = baseAngle + (slot - (perRing - 1) / 2) * (arcSpan / perRing) + rand(-0.03, 0.03);
    const leafR = 22 + rand(-1, 1);
    const lx = sub.x + Math.cos(angle) * leafR;
    const ly = sub.y + Math.sin(angle) * leafR;

    const edge = document.createElementNS(SVG_NS, 'line');
    edge.setAttribute('x1', sub.x); edge.setAttribute('y1', sub.y);
    edge.setAttribute('x2', lx);    edge.setAttribute('y2', ly);
    edge.setAttribute('class', 'rs-edge rs-edge-firing');
    edgesG.appendChild(edge);
    setTimeout(() => edge.classList.remove('rs-edge-firing'), 1100);

    const leaf = document.createElementNS(SVG_NS, 'circle');
    leaf.setAttribute('cx', lx); leaf.setAttribute('cy', ly);
    leaf.setAttribute('r', 3.5);
    leaf.setAttribute('class', 'rs-node rs-node-leaf rs-node-new');
    nodesG.appendChild(leaf);

    sub.leafEls.push({ edge, node: leaf });
  }

  function _registerSources(sub, n) {
    if (!sub || n <= 0) return;
    sub.count += n;
    if (sub.collapsed) {
      _updateBadge(sub);
      _updateSubLabel(sub);
      return;
    }
    const room = Math.max(0, MAX_VISIBLE_LEAVES - sub.leafEls.length);
    const toDraw = Math.min(room, n);
    for (let i = 0; i < toDraw; i++) _addVisibleLeaf(sub);
    _updateBadge(sub);
    _updateSubLabel(sub);
  }

  function _activeSub() {
    if (!subs.length) return _addSub('R1');
    return subs[subs.length - 1];
  }

  // ── public API ─────────────────────────────────────────────────
  return {
    element: wrap,

    /** Reflect a phase change in the status text + side effects. */
    setPhase(phase, extra = {}) {
      if (completed) return;
      const label = PHASE_LABEL[phase] || phase || '';
      let txt = label;
      if (phase === 'searching' && extra.queries) {
        const q = Array.isArray(extra.queries) ? extra.queries.length : extra.queries;
        txt += ` · ${q} queries`;
      } else if (phase === 'reading' && extra.title) {
        txt = `reading: ${_trunc(extra.title, 32)}`;
      } else if (phase === 'reading' && extra.step_description) {
        txt = _trunc(extra.step_description, 40).toLowerCase();
      } else if (phase === 'analyzing' && extra.total_findings) {
        txt += ` · ${extra.total_findings} findings`;
      }
      statusE.textContent = txt;
      if (phase === 'error') wrap.classList.add('rs-error');
    },

    /** Bump the round counter — adds a sub-question node when round grows. */
    setRound(round, roundOpts = {}) {
      if (completed) return;
      if (typeof round !== 'number' || round < 1) return;
      if (round > lastRound) {
        for (let i = lastRound; i < round; i++) {
          if (subs.length >= MAX_SUBS) {
            // Attribute further rounds to the last hub (collapsed count).
            const last = subs[subs.length - 1];
            if (last) {
              last.baseLabel = roundOpts.label || `R${round}`;
              _updateSubLabel(last);
            }
            break;
          }
          _addSub(roundOpts.label || `R${i + 1}`);
        }
        lastRound = round;
        roundE.textContent = round;
      }
    },

    /** Update the total source count — capped leaf fan + "+N" overflow. */
    setSourceCount(total) {
      if (completed) return;
      if (typeof total !== 'number' || total <= sourceCount) return;
      const delta = total - sourceCount;
      sourceCount = total;
      srcE.textContent = total;

      const sub = _activeSub();
      // Animate at most a few new visible dots; the rest go straight to badge.
      const anim = Math.min(delta, Math.max(0, MAX_VISIBLE_LEAVES - (sub?.leafEls.length || 0)), 4);
      const token = ++leafAnimToken;
      for (let i = 0; i < anim; i++) {
        setTimeout(() => {
          if (token !== leafAnimToken || completed) return;
          _registerSources(_activeSub(), 1);
        }, i * 90);
      }
      const rest = delta - anim;
      if (rest > 0) {
        setTimeout(() => {
          if (token !== leafAnimToken || completed) return;
          _registerSources(_activeSub(), rest);
        }, anim * 90);
      }
    },

    /** Mark the run as done — freezes the pulse and tints the graph green. */
    complete() {
      if (completed) return;
      completed = true;
      wrap.classList.add('rs-complete');
      statusE.textContent = 'complete';
      // Collapse everything to round hubs + counts for a clean final frame.
      for (const sub of subs) _collapseSub(sub);
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    },

    destroy() {
      leafAnimToken++;
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    },
  };
}
