// ===== Timeline Rendering (shared) =====
const TOTAL_MINUTES = 24 * 60;
const TIMELINE_MIN_ZOOM = 100;
const TIMELINE_MAX_ZOOM = 500;
const TIMELINE_DEFAULT_ZOOM = 100;

function timeToMinutes(timeStr) {
    const [h, m] = timeStr.split(':').map(Number);
    return h * 60 + m;
}

function applyTimelineZoom(container, zoomPercent) {
    const safeZoom = Math.max(TIMELINE_MIN_ZOOM, Math.min(TIMELINE_MAX_ZOOM, Number(zoomPercent) || TIMELINE_DEFAULT_ZOOM));
    const scroll = container.querySelector('.timeline-scroll');
    if (scroll && scroll.clientWidth > 0) {
        const availableWidth = Math.max(scroll.clientWidth - 40, 1);
        const barWidth = Math.round(availableWidth * safeZoom / 100);
        container.style.setProperty('--timeline-bar-width', barWidth + 'px');
        console.debug('[timeline] zoom=%d barWidth=%dpx scrollWidth=%d', safeZoom, barWidth, scroll.clientWidth);
    } else if (scroll) {
        requestAnimationFrame(() => applyTimelineZoom(container, safeZoom));
    }
    container.dataset.zoomLevel = safeZoom >= 380 ? 'high' : safeZoom >= 220 ? 'medium' : 'low';
    return safeZoom;
}

// Returns { anonsCount, conflictCount } for a single bar's slots
function _getTimelineBadgeInfo(slots) {
    const anons = (slots || []).filter(s => s.type !== 'prayer' && s.type !== 'info');
    const conflicted = new Set();
    for (let i = 0; i < anons.length; i++) {
        for (let j = i + 1; j < anons.length; j++) {
            const aS = timeToMinutes(anons[i].start), aE = timeToMinutes(anons[i].end);
            const bS = timeToMinutes(anons[j].start), bE = timeToMinutes(anons[j].end);
            if (aS < bE && aE > bS) {
                conflicted.add(i);
                conflicted.add(j);
            }
        }
    }
    return { anonsCount: anons.length, conflictCount: conflicted.size };
}

function updateTimelineBadges(barId, anonsCount, conflictCount) {
    const anonsBadge = document.getElementById(barId + 'Density');
    const conflictBadge = document.getElementById(barId + 'Conflict');
    if (anonsBadge) {
        anonsBadge.textContent = anonsCount ? anonsCount + ' anons' : '';
    }
    if (conflictBadge) {
        if (conflictCount > 0) {
            conflictBadge.textContent = conflictCount + ' sıralı';
            conflictBadge.style.display = '';
        } else {
            conflictBadge.style.display = 'none';
        }
    }
}

function highlightSlotGroup(container, groupKey, locked) {
    const slots = container.querySelectorAll('.timeline-slot[data-group-key]');
    slots.forEach(slot => {
        const isMatch = slot.dataset.groupKey === groupKey;
        slot.classList.toggle('timeline-dim', !isMatch);
        slot.classList.toggle('timeline-highlight', isMatch);
        slot.classList.toggle('timeline-highlight-locked', locked && isMatch);
    });
}

function clearSlotGroupHighlight(container) {
    const slots = container.querySelectorAll('.timeline-slot[data-group-key]');
    slots.forEach(slot => {
        slot.classList.remove('timeline-dim', 'timeline-highlight', 'timeline-highlight-locked');
    });
}

function bindTimelineControls(containerId, barId) {
    const container = document.getElementById(containerId);
    const slider = document.getElementById(barId + 'Zoom');
    const zoomValue = document.getElementById(barId + 'ZoomValue');
    if (!container || !slider || !zoomValue) return;

    if (!slider.dataset.bound) {
        slider.dataset.bound = '1';
        slider.addEventListener('input', (event) => {
            const safeZoom = applyTimelineZoom(container, event.target.value);
            zoomValue.textContent = safeZoom + '%';
        });
        container.addEventListener('click', (event) => {
            if (!event.target.classList.contains('timeline-slot')) {
                container.dataset.lockedGroupKey = '';
                clearSlotGroupHighlight(container);
            }
        });
        const ro = new ResizeObserver(() => {
            const safeZoom = applyTimelineZoom(container, slider.value || TIMELINE_DEFAULT_ZOOM);
            zoomValue.textContent = safeZoom + '%';
        });
        ro.observe(container);
    }

    const safeZoom = applyTimelineZoom(container, slider.value || TIMELINE_DEFAULT_ZOOM);
    zoomValue.textContent = safeZoom + '%';
}

function addSlot(bar, startMin, endMin, type, label, meta, isConflict, queueLabels) {
    meta = meta || {};
    const left = (startMin / TOTAL_MINUTES) * 100;
    const width = Math.max(((endMin - startMin) / TOTAL_MINUTES) * 100, 0.2);
    const div = document.createElement('div');
    div.className = 'timeline-slot type-' + type + (isConflict ? ' is-conflict' : '');
    div.style.left = left + '%';
    div.style.width = width + '%';
    div.title = label;
    if (meta.group_key) {
        const container = bar.closest('.timeline-container');
        div.dataset.groupKey = meta.group_key;
        div.dataset.sourceType = meta.source_type || '';
        div.dataset.sourceId = meta.source_id != null ? String(meta.source_id) : '';
        div.dataset.mediaId = meta.media_id != null ? String(meta.media_id) : '';
        div.addEventListener('mouseenter', () => {
            if (!container) return;
            if (container.dataset.lockedGroupKey) return;
            highlightSlotGroup(container, meta.group_key, false);
        });
        div.addEventListener('mouseleave', () => {
            if (!container) return;
            if (container.dataset.lockedGroupKey) return;
            clearSlotGroupHighlight(container);
        });
        div.addEventListener('click', (event) => {
            event.stopPropagation();
            if (!container) return;
            const locked = container.dataset.lockedGroupKey || '';
            if (locked === meta.group_key) {
                container.dataset.lockedGroupKey = '';
                clearSlotGroupHighlight(container);
                return;
            }
            container.dataset.lockedGroupKey = meta.group_key;
            highlightSlotGroup(container, meta.group_key, true);
        });
    }
    if (isConflict && queueLabels && queueLabels.length > 1) {
        const ORDINALS = ['①','②','③','④','⑤','⑥','⑦','⑧','⑨','⑩'];
        div.addEventListener('mouseenter', () => {
            const tooltip = document.getElementById('tlTooltip');
            if (!tooltip) return;
            const header = `Sıralı çalacak (${queueLabels.length} anons):`;
            const list = queueLabels.map((l, idx) => `${ORDINALS[idx] || (idx + 1 + '.')} ${l}`).join('  ');
            tooltip.textContent = header + '\n' + list;
            tooltip.style.whiteSpace = 'pre';
            const rect = div.getBoundingClientRect();
            tooltip.style.left = (rect.left + rect.width / 2) + 'px';
            tooltip.style.top = (rect.top + window.scrollY) + 'px';
            tooltip.style.display = 'block';
        });
        div.addEventListener('mouseleave', () => {
            const tooltip = document.getElementById('tlTooltip');
            if (tooltip) tooltip.style.display = 'none';
        });
    }
    bar.appendChild(div);
}

function renderTimelineBar(bar, data) {
    bar.innerHTML = '';

    // Hour markers — every hour
    for (let h = 0; h < 24; h++) {
        const pct = (h * 60 / TOTAL_MINUTES) * 100;
        const mark = document.createElement('div');
        mark.className = 'timeline-hour-mark' + (h % 3 === 0 ? ' timeline-hour-mark-major' : '');
        mark.style.left = pct + '%';
        bar.appendChild(mark);

        const lbl = document.createElement('div');
        lbl.className = 'timeline-hour-label';
        lbl.style.left = pct + '%';
        lbl.textContent = String(h).padStart(2, '0');
        bar.appendChild(lbl);

        // Sub-hour marks (hidden at low zoom, shown via CSS class on container)
        const m30 = document.createElement('div');
        m30.className = 'timeline-submark timeline-submark-30';
        m30.style.left = ((h * 60 + 30) / TOTAL_MINUTES) * 100 + '%';
        bar.appendChild(m30);

        for (const min of [15, 45]) {
            const m15 = document.createElement('div');
            m15.className = 'timeline-submark timeline-submark-15';
            m15.style.left = ((h * 60 + min) / TOTAL_MINUTES) * 100 + '%';
            bar.appendChild(m15);
        }
    }

    // Working hours info
    if (data.working_hours && data.working_hours.enabled) {
        const wStart = timeToMinutes(data.working_hours.start);
        const wEnd = timeToMinutes(data.working_hours.end);
        const infoTip = 'Mesai dışı saatler (bu saatlerde plan çalıştırılmaz)';
        if (wStart <= wEnd) {
            if (wStart > 0) addSlot(bar, 0, wStart, 'info', infoTip);
            if (wEnd < TOTAL_MINUTES) addSlot(bar, wEnd, TOTAL_MINUTES, 'info', infoTip);
        } else {
            addSlot(bar, wEnd, wStart, 'info', infoTip);
        }
    }

    // Detect conflicts among non-prayer/non-info slots
    const slots = data.slots || [];
    const anons = slots.filter(s => s.type !== 'prayer' && s.type !== 'info');
    const conflictedIdx = new Set();
    for (let i = 0; i < anons.length; i++) {
        for (let j = i + 1; j < anons.length; j++) {
            const aS = timeToMinutes(anons[i].start), aE = timeToMinutes(anons[i].end);
            const bS = timeToMinutes(anons[j].start), bE = timeToMinutes(anons[j].end);
            if (aS < bE && aE > bS) {
                conflictedIdx.add(i);
                conflictedIdx.add(j);
            }
        }
    }
    const anonIndexMap = new Map(anons.map((s, i) => [s, i]));

    // Build per-slot conflict queue (ordered list of overlapping slot labels)
    const conflictGroupMap = new Map();
    for (let i = 0; i < anons.length; i++) {
        if (!conflictedIdx.has(i)) continue;
        const group = [];
        for (let j = 0; j < anons.length; j++) {
            const aS = timeToMinutes(anons[i].start), aE = timeToMinutes(anons[i].end);
            const bS = timeToMinutes(anons[j].start), bE = timeToMinutes(anons[j].end);
            if (aS < bE && aE > bS) group.push(anons[j]);
        }
        group.sort((a, b) => timeToMinutes(a.start) - timeToMinutes(b.start));
        conflictGroupMap.set(anons[i], group.map(s => s.label));
    }

    // Render slots
    for (const slot of slots) {
        const start = timeToMinutes(slot.start);
        const end = timeToMinutes(slot.end);
        const isConflict = anonIndexMap.has(slot) && conflictedIdx.has(anonIndexMap.get(slot));
        const queueLabels = anonIndexMap.has(slot) ? (conflictGroupMap.get(slot) || null) : null;
        addSlot(bar, start, end, slot.type, slot.label, slot, isConflict, queueLabels);
    }
}
