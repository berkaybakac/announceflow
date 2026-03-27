// ===== Timeline Rendering (shared) =====
const TOTAL_MINUTES = 24 * 60;
const TIMELINE_MIN_ZOOM = 100;
const TIMELINE_MAX_ZOOM = 280;
const TIMELINE_DEFAULT_ZOOM = 140;

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
    }
    return safeZoom;
}

function updateDensityBadge(barId, slotCount, calmMax, mediumMax) {
    const density = document.getElementById(barId + 'Density');
    if (!density) return;
    density.classList.remove('is-busy', 'is-calm');
    if (slotCount <= calmMax) {
        density.textContent = 'Yoğunluk: Sakin';
        density.classList.add('is-calm');
        return;
    }
    if (slotCount <= mediumMax) {
        density.textContent = 'Yoğunluk: Orta';
        return;
    }
    density.textContent = 'Yoğunluk: Yoğun';
    density.classList.add('is-busy');
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

function bindTimelineControls(containerId, barId, slotCount, calmMax, mediumMax) {
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
    updateDensityBadge(barId, slotCount, calmMax, mediumMax);
}

function addSlot(bar, startMin, endMin, type, label, meta) {
    meta = meta || {};
    const left = (startMin / TOTAL_MINUTES) * 100;
    const width = Math.max(((endMin - startMin) / TOTAL_MINUTES) * 100, 0.2);
    const div = document.createElement('div');
    div.className = 'timeline-slot type-' + type;
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
    bar.appendChild(div);
}

function renderTimelineBar(bar, data) {
    bar.innerHTML = '';

    // Hour markers
    for (let h = 0; h < 24; h += 3) {
        const pct = (h * 60 / TOTAL_MINUTES) * 100;
        const mark = document.createElement('div');
        mark.className = 'timeline-hour-mark';
        mark.style.left = pct + '%';
        bar.appendChild(mark);

        const lbl = document.createElement('div');
        lbl.className = 'timeline-hour-label';
        lbl.style.left = pct + '%';
        lbl.textContent = String(h).padStart(2, '0');
        bar.appendChild(lbl);
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

    // Occupied slots
    for (const slot of (data.slots || [])) {
        const start = timeToMinutes(slot.start);
        const end = timeToMinutes(slot.end);
        addSlot(bar, start, end, slot.type, slot.label, slot);
    }
}
