const APP_CONFIG = window.APP_CONFIG || {};
const ROUTES = APP_CONFIG.routes || {};
const SOCKET_EVENTS = APP_CONFIG.socketEvents || {};
const CHART_UPDATE_DELAY = 150;
const SETTINGS_TIMEOUT_MS = 5000;

const elements = {
    themeToggle: document.getElementById('themeToggle'),

    graphTabBtn: document.getElementById('graphTabBtn'),
    settingsTabBtn: document.getElementById('settingsTabBtn'),
    alertsTabBtn: document.getElementById('alertsTabBtn'),

    graphTab: document.getElementById('graphTab'),
    settingsTab: document.getElementById('settingsTab'),
    alertsTab: document.getElementById('alertsTab'),

    value: document.getElementById('value'),
    realtimeModeBtn: document.getElementById('realtimeModeBtn'),
    filterModeBtn: document.getElementById('filterModeBtn'),
    realtimeControls: document.getElementById('realtimeControls'),
    filterControls: document.getElementById('filterControls'),
    maxPoints: document.getElementById('maxPoints'),
    filterRenderLimit: document.getElementById('filterRenderLimit'),
    dateFrom: document.getElementById('dateFrom'),
    dateTo: document.getElementById('dateTo'),
    applyFilterBtn: document.getElementById('applyFilterBtn'),
    quickDayBtn: document.getElementById('quickDayBtn'),
    quickWeekBtn: document.getElementById('quickWeekBtn'),
    quickMonthBtn: document.getElementById('quickMonthBtn'),
    pauseBtn: document.getElementById('pauseBtn'),
    exportBtn: document.getElementById('exportBtn'),
    clearBtn: document.getElementById('clearBtn'),
    refreshBtn: document.getElementById('refreshBtn'),
    status: document.getElementById('status'),
    lastUpdate: document.getElementById('lastUpdate'),
    chartCanvas: document.getElementById('chart'),

    settingsStatus: document.getElementById('settingsStatus'),
    gain: document.getElementById('gain'),
    itMs: document.getElementById('it_ms'),
    interval: document.getElementById('interval'),
    samples: document.getElementById('samples'),
    loadSettingsBtn: document.getElementById('loadSettingsBtn'),
    saveSettingsBtn: document.getElementById('saveSettingsBtn'),

    alertsStatus: document.getElementById('alertsStatus'),
    alertEnabled: document.getElementById('alertEnabled'),
    alertEmail: document.getElementById('alertEmail'),
    alertThreshold: document.getElementById('alertThreshold'),
    alertDirection: document.getElementById('alertDirection'),
    loadAlertBtn: document.getElementById('loadAlertBtn'),
    saveAlertBtn: document.getElementById('saveAlertBtn'),
    testAlertBtn: document.getElementById('testAlertBtn')
};

let socket = io();
let chart = null;
let currentMode = 'realtime';
let isPaused = false;
let MAX_POINTS = APP_CONFIG.defaultRealtimePoints || 100;
let FILTER_RENDER_LIMIT = APP_CONFIG.defaultFilterRenderLimit || 1000;
let dateFrom = null;
let dateTo = null;
let lastUpdateTimestamp = null;
let filterApplied = false;
let lastUpdateTime = null;
let totalFilterPoints = 0;

function getThemeColors() {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    return {
        gridColor: isDark ? '#4a5568' : '#ddd',
        textColor: isDark ? '#e4e6eb' : '#212529'
    };
}

function createChart() {
    const ctx = elements.chartCanvas.getContext('2d');
    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Lux (lx)',
                data: [],
                fill: false,
                tension: 0.3,
                borderColor: '#007bff',
                backgroundColor: 'rgba(0,123,255,0.1)'
            }]
        },
        options: {
            animation: { duration: CHART_UPDATE_DELAY, easing: 'linear' },
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: getThemeColors().textColor }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: getThemeColors().textColor,
                        maxRotation: 45,
                        minRotation: 45,
                        autoSkip: true,
                        maxTicksLimit: 10
                    },
                    title: { display: true, text: 'Čas (lokálny)', color: getThemeColors().textColor },
                    grid: { color: getThemeColors().gridColor }
                },
                y: {
                    ticks: { color: getThemeColors().textColor },
                    title: { display: true, text: 'Intenzita osvetlenia (lx)', color: getThemeColors().textColor },
                    grid: { color: getThemeColors().gridColor },
                    beginAtZero: true
                }
            }
        }
    });
}

function formatLocalDateTimeForInput(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function localDateTimeToUTC(localDateTime) {
    const [datePart, timePart] = localDateTime.split('T');
    const [year, month, day] = datePart.split('-');
    const [hours, minutes] = timePart.split(':');
    const localDate = new Date(year, month - 1, day, hours, minutes, 0);
    return localDate.toISOString();
}

function pad2(value) {
    return String(value).padStart(2, '0');
}

function formatToLocalTime(value) {
    const date = value instanceof Date ? value : new Date(value);

    if (Number.isNaN(date.getTime())) {
        return '';
    }

    return `${pad2(date.getDate())}/${pad2(date.getMonth() + 1)}/${date.getFullYear()} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function updateLastUpdateTime() {
    if (lastUpdateTime) {
        elements.lastUpdate.innerHTML = `Posledná aktualizácia: ${formatToLocalTime(lastUpdateTime)}`;
    }
}

function switchTab(tabName) {
    const tabs = [
        { name: 'graph', btn: elements.graphTabBtn, panel: elements.graphTab },
        { name: 'settings', btn: elements.settingsTabBtn, panel: elements.settingsTab },
        { name: 'alerts', btn: elements.alertsTabBtn, panel: elements.alertsTab }
    ];

    tabs.forEach(tab => {
        if (tab.btn) {
            tab.btn.classList.toggle('active', tab.name === tabName);
        }
        if (tab.panel) {
            tab.panel.classList.toggle('active', tab.name === tabName);
        }
    });
}

function setDefaultDates() {
    const now = new Date();
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    elements.dateFrom.value = formatLocalDateTimeForInput(yesterday);
    elements.dateTo.value = formatLocalDateTimeForInput(now);
}

function setQuickFilter(period) {
    const now = new Date();
    let from = new Date();

    if (period === 'day') {
        from = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    } else if (period === 'week') {
        from = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    } else if (period === 'month') {
        from = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    }

    elements.dateFrom.value = formatLocalDateTimeForInput(from);
    elements.dateTo.value = formatLocalDateTimeForInput(now);
    applyDateFilter();
}

function setMode(mode) {
    currentMode = mode;
    const isRealtime = mode === 'realtime';

    if (elements.realtimeModeBtn) {
        elements.realtimeModeBtn.classList.toggle('active', isRealtime);
    }
    if (elements.filterModeBtn) {
        elements.filterModeBtn.classList.toggle('active', !isRealtime);
    }
    if (elements.realtimeControls) {
        elements.realtimeControls.classList.toggle('active', isRealtime);
    }
    if (elements.filterControls) {
        elements.filterControls.classList.toggle('active', !isRealtime);
    }

    if (isRealtime) {
        filterApplied = false;
        dateFrom = null;
        dateTo = null;
        totalFilterPoints = 0;
    }

    lastUpdateTimestamp = null;
    loadInitialData();
    updateStatusDisplay();
}

function applyDateFilter() {
    const fromValue = elements.dateFrom.value;
    const toValue = elements.dateTo.value;

    if (!fromValue || !toValue) {
        alert('Prosím vyberte oba dátumy');
        return;
    }

    dateFrom = localDateTimeToUTC(fromValue);
    dateTo = localDateTimeToUTC(toValue);
    filterApplied = true;
    lastUpdateTimestamp = null;
    loadInitialData();
    updateStatusDisplay();
}

function setChartData(data) {
    chart.data.labels = data.map(point => formatToLocalTime(point.time));
    chart.data.datasets[0].data = data.map(point => point.lux);
}

function clearChartData() {
    chart.data.labels = [];
    chart.data.datasets[0].data = [];
}

function getLatestPoint(data) {
    return data.length ? data[data.length - 1] : null;
}

function setLuxValue(point) {
    if (!point) {
        elements.value.innerHTML = 'Posledná hodnota lux: 0 lx';
        return;
    }

    elements.value.innerHTML = `Posledná hodnota lux: ${point.lux.toFixed(2)} lx`;
}

function refreshChart() {
    chart.stop();
    chart.update();
}

function updateStatusDisplay() {
    if (isPaused) {
        elements.status.innerHTML = '<strong>⏸️ Pozastavené:</strong> Aktualizácie sú zastavené. Kliknite na "Pokračovať" pre zobrazenie nových dát.';
        elements.status.className = 'status paused';
        return;
    }

    if (currentMode === 'filter' && filterApplied && dateFrom && dateTo) {
        const fromDate = new Date(dateFrom);
        const toDate = new Date(dateTo);
        const pointCount = totalFilterPoints;

        if (pointCount === 0) {
            elements.status.innerHTML = `<strong>🔍 Filter podľa dátumu:</strong> ${formatToLocalTime(fromDate)} - ${formatToLocalTime(toDate)} | ⚠️ Žiadne dáta v tomto časovom rozsahu`;
            elements.status.className = 'status empty';
        } else {
            elements.status.innerHTML = `<strong>🔍 Filter podľa dátumu:</strong> ${formatToLocalTime(fromDate)} - ${formatToLocalTime(toDate)} | Počet bodov: ${pointCount}`;
            elements.status.className = 'status filter-active';
        }
        return;
    }

    if (currentMode === 'realtime') {
        elements.status.innerHTML = `<strong>⌚ Realtime mód:</strong> Zobrazuje sa posledných ${MAX_POINTS} bodov | Čaká sa na nové dáta...`;
        elements.status.className = 'status realtime-active';
        return;
    }

    elements.status.innerHTML = '<strong>🔍 Filter mód:</strong> Vyberte dátumy a kliknite na "Aplikovať filter"';
    elements.status.className = 'status';
}

function buildDataUrl(options = {}) {
    const params = new URLSearchParams();
    params.set('mode', currentMode);

    if (currentMode === 'filter' && filterApplied && dateFrom && dateTo) {
        params.set('from', dateFrom);
        params.set('to', dateTo);
        params.set('filter_render_limit', FILTER_RENDER_LIMIT);
    } else if (currentMode === 'realtime') {
        params.set('max_points', MAX_POINTS);
        if (options.since) {
            params.set('since', options.since);
        }
    }

    return `${ROUTES.data}?${params.toString()}`;
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();

    let payload;
    try {
        payload = JSON.parse(text);
    } catch (e) {
        throw new Error(`Server nevrátil JSON. HTTP ${response.status}`);
    }

    if (!response.ok) {
        throw new Error(payload.message || 'Request failed');
    }

    return payload;
}

async function loadInitialData() {
    try {
        const payload = await fetchJson(buildDataUrl());
        const newData = payload.data || [];

        if (currentMode === 'filter') {
            totalFilterPoints = payload.total_points || 0;
        }

        if (newData.length === 0) {
            clearChartData();
            setLuxValue(null);
            lastUpdateTimestamp = null;
        } else {
            setChartData(newData);
            const latestPoint = getLatestPoint(newData);
            if (currentMode === 'realtime') {
                lastUpdateTimestamp = latestPoint.time;
            }
            setLuxValue(latestPoint);
        }

        refreshChart();
        updateStatusDisplay();
        lastUpdateTime = new Date();
        updateLastUpdateTime();
    } catch (error) {
        console.error('Error loading data:', error);
    }
}

function appendRealtimePoints(newData) {
    const filteredNewData = newData.filter(point => point.time > lastUpdateTimestamp);
    if (filteredNewData.length === 0) {
        return null;
    }

    filteredNewData.forEach(point => {
        chart.data.labels.push(formatToLocalTime(point.time));
        chart.data.datasets[0].data.push(point.lux);
        lastUpdateTimestamp = point.time;
    });

    if (chart.data.labels.length > MAX_POINTS) {
        const excess = chart.data.labels.length - MAX_POINTS;
        chart.data.labels.splice(0, excess);
        chart.data.datasets[0].data.splice(0, excess);
    }

    return getLatestPoint(filteredNewData);
}

async function updateNewData() {
    if (isPaused) {
        return;
    }

    if (currentMode === 'filter' && filterApplied) {
        await loadInitialData();
        return;
    }

    if (currentMode !== 'realtime') {
        return;
    }

    if (!lastUpdateTimestamp) {
        await loadInitialData();
        return;
    }

    try {
        const payload = await fetchJson(buildDataUrl({ since: lastUpdateTimestamp }));
        const newData = payload.data || [];

        if (newData.length > 0) {
            const latestPoint = appendRealtimePoints(newData);
            if (latestPoint) {
                setLuxValue(latestPoint);
                refreshChart();
            }
        }

        lastUpdateTime = new Date();
        updateLastUpdateTime();
        updateStatusDisplay();
    } catch (error) {
        console.error('Error updating data:', error);
    }
}

function updateSettingsDisplay(settings) {
    elements.gain.value = settings.gain;
    elements.itMs.value = settings.it_ms;
    elements.interval.value = settings.interval;
    elements.samples.value = settings.samples;

    const gainText = elements.gain.options[elements.gain.selectedIndex]?.text || settings.gain;
    const itText = elements.itMs.options[elements.itMs.selectedIndex]?.text || `${settings.it_ms} ms`;

    elements.settingsStatus.innerHTML = `<strong>Nastavenia načítané:</strong><br>Gain: ${gainText}<br>Integračný čas: ${itText}<br>Interval: ${settings.interval} s<br>Vzorky: ${settings.samples}`;
    elements.settingsStatus.className = 'status success';

    setTimeout(() => {
        elements.settingsStatus.innerHTML = '<strong>Info:</strong> Nastavenia boli úspešne načítané z ESP32. Môžete ich upraviť a uložiť.';
        elements.settingsStatus.className = 'status';
    }, SETTINGS_TIMEOUT_MS);
}

function getSettingsPayload() {
    return {
        gain: parseFloat(elements.gain.value),
        it_ms: parseInt(elements.itMs.value, 10),
        interval: parseFloat(elements.interval.value),
        samples: parseInt(elements.samples.value, 10)
    };
}

async function loadSettings() {
    elements.settingsStatus.innerHTML = '<strong>Načítavanie:</strong> Odosielam požiadavku na ESP32...';
    elements.settingsStatus.className = 'status';

    try {
        await fetchJson(ROUTES.requestSettings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conf: true })
        });

        elements.settingsStatus.innerHTML = '<strong>Požiadavka odoslaná:</strong> Čakám na odpoveď z ESP32...';
        elements.settingsStatus.className = 'status success';

        setTimeout(() => {
            if (elements.settingsStatus.innerHTML.includes('Čakám na odpoveď')) {
                elements.settingsStatus.innerHTML = '<strong>Upozornenie:</strong> ESP32 neodpovedalo do 5 sekúnd. Skontrolujte pripojenie.';
                elements.settingsStatus.className = 'status error';
            }
        }, SETTINGS_TIMEOUT_MS);
    } catch (error) {
        elements.settingsStatus.innerHTML = `<strong>Chyba:</strong> ${error.message}`;
        elements.settingsStatus.className = 'status error';
    }
}

async function saveSettings() {
    const settings = getSettingsPayload();

    elements.settingsStatus.innerHTML = '<strong>Ukladanie:</strong> Odosielam nastavenia do ESP32...';
    elements.settingsStatus.className = 'status';

    try {
        await fetchJson(ROUTES.saveSettings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        elements.settingsStatus.innerHTML = '<strong>Nastavenia odoslané:</strong> ESP32 by malo potvrdiť prijatie...';
        elements.settingsStatus.className = 'status success';

        setTimeout(() => {
            elements.settingsStatus.innerHTML = '<strong>ℹInfo:</strong> Nastavenia boli odoslané. ESP32 ich použije pri ďalších meraniach.';
            elements.settingsStatus.className = 'status';
        }, 3000);
    } catch (error) {
        elements.settingsStatus.innerHTML = `<strong>Chyba:</strong> ${error.message}`;
        elements.settingsStatus.className = 'status error';
    }
}

function updateAlertsStatus(message, className = 'status') {
    if (!elements.alertsStatus) {
        return;
    }
    elements.alertsStatus.innerHTML = message;
    elements.alertsStatus.className = className;
}

function updateAlertDisplay(settings) {
    if (!elements.alertEnabled || !elements.alertEmail || !elements.alertThreshold || !elements.alertDirection) {
        return;
    }

    elements.alertEnabled.checked = !!settings.enabled;
    elements.alertEmail.value = settings.email || '';
    elements.alertThreshold.value = settings.threshold ?? '';
    elements.alertDirection.value = settings.direction || 'above';

    updateAlertsStatus(
        `<strong>Upozornenie načítané:</strong><br>` +
        `Stav: ${settings.enabled ? 'Aktívne' : 'Neaktívne'}<br>` +
        `E-mail: ${settings.email || '-'}<br>` +
        `Limit: ${settings.threshold ?? '-'} lx<br>` +
        `Režim: ${settings.direction === 'below' ? 'Pod' : 'Nad'}`,
        'status success'
    );

    setTimeout(() => {
        updateAlertsStatus('<strong>Info:</strong> Môžete upraviť nastavenia upozornenia a uložiť ich.');
    }, SETTINGS_TIMEOUT_MS);
}

function getAlertPayload() {
    return {
        enabled: !!elements.alertEnabled?.checked,
        email: (elements.alertEmail?.value || '').trim(),
        threshold: parseFloat(elements.alertThreshold?.value),
        direction: elements.alertDirection?.value || 'above'
    };
}

function validateAlertPayload(payload, requireEmail = true) {
    if (!payload.direction || !['above', 'below'].includes(payload.direction)) {
        throw new Error('Neplatný režim upozornenia');
    }

    if (!payload.enabled) {
        return;
    }

    if (requireEmail) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!payload.email || !emailRegex.test(payload.email)) {
            throw new Error('Zadajte platnú e-mailovú adresu');
        }
    }

    if (!Number.isFinite(payload.threshold) || payload.threshold < 0) {
        throw new Error('Zadajte platnú hodnotu lux');
    }
}

async function loadAlertSettings() {
    if (!ROUTES.alertSettings) {
        updateAlertsStatus('<strong>Upozornenie:</strong> ROUTES.alertSettings nie je definované.', 'status error');
        return;
    }

    updateAlertsStatus('<strong>Načítavanie:</strong> Načítavam nastavenia upozornení...');

    try {
        const payload = await fetchJson(ROUTES.alertSettings);
        updateAlertDisplay(payload);
    } catch (error) {
        updateAlertsStatus(`<strong>Chyba:</strong> ${error.message}`, 'status error');
    }
}

async function saveAlertSettings() {
    if (!ROUTES.saveAlertSettings) {
        updateAlertsStatus('<strong>Upozornenie:</strong> ROUTES.saveAlertSettings nie je definované.', 'status error');
        return;
    }

    const payload = getAlertPayload();

    try {
        validateAlertPayload(payload, true);
        updateAlertsStatus('<strong>Ukladanie:</strong> Ukladám nastavenia upozornenia...');

        await fetchJson(ROUTES.saveAlertSettings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        updateAlertsStatus('<strong>Úspech:</strong> Nastavenie upozornenia bolo uložené.', 'status success');
    } catch (error) {
        updateAlertsStatus(`<strong>Chyba:</strong> ${error.message}`, 'status error');
    }
}

async function sendTestAlert() {
    if (!ROUTES.testAlert) {
        updateAlertsStatus('<strong>Upozornenie:</strong> ROUTES.testAlert nie je definované.', 'status error');
        return;
    }

    const payload = getAlertPayload();

    try {
        validateAlertPayload(payload, true);
        updateAlertsStatus('<strong>Test:</strong> Odosielam testovací e-mail...');

        await fetchJson(ROUTES.testAlert, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        updateAlertsStatus('<strong>Úspech:</strong> Testovací e-mail bol odoslaný.', 'status success');
    } catch (error) {
        updateAlertsStatus(`<strong>Chyba:</strong> ${error.message}`, 'status error');
    }
}

function togglePause() {
    isPaused = !isPaused;
    elements.pauseBtn.innerHTML = isPaused ? '▶️ Pokračovať' : '⏸️ Pozastaviť';

    if (!isPaused) {
        loadInitialData();
    }

    updateStatusDisplay();
}

function manualRefresh() {
    loadInitialData();
}

function downloadCSV() {
    window.location.href = ROUTES.export;
}

async function clearData() {
    if (!confirm('Naozaj chcete vymazať všetky dáta?')) {
        return;
    }

    const password = prompt('Zadajte heslo pre vymazanie dát:');
    if (password === null) {
        return;
    }

    try {
        await fetchJson(ROUTES.clear, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });

        lastUpdateTimestamp = null;
        setTimeout(() => loadInitialData(), 100);
    } catch (error) {
        alert(`Vymazanie zlyhalo: ${error.message}`);
    }
}

function applyChartTheme() {
    const colors = getThemeColors();

    if (!chart) {
        return;
    }

    chart.options.scales.x.ticks.color = colors.textColor;
    chart.options.scales.x.title.color = colors.textColor;
    chart.options.scales.x.grid.color = colors.gridColor;
    chart.options.scales.y.ticks.color = colors.textColor;
    chart.options.scales.y.title.color = colors.textColor;
    chart.options.scales.y.grid.color = colors.gridColor;
    chart.options.plugins.legend.labels.color = colors.textColor;
    chart.update();
}

function initTheme() {
    const savedTheme = localStorage.getItem('theme');

    if (savedTheme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
        elements.themeToggle.innerHTML = '☀️ Light mode';
    } else {
        document.documentElement.setAttribute('data-theme', 'light');
        elements.themeToggle.innerHTML = '🌙 Dark mode';
    }

    applyChartTheme();
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    elements.themeToggle.innerHTML = newTheme === 'dark' ? '☀️ Light mode' : '🌙 Dark mode';
    applyChartTheme();
}

function setupInputLimits() {
    elements.maxPoints.value = MAX_POINTS;
    elements.maxPoints.max = APP_CONFIG.maxRealtimePoints || 1000;
    elements.filterRenderLimit.value = FILTER_RENDER_LIMIT;
    elements.filterRenderLimit.max = APP_CONFIG.maxFilterRenderLimit || 10000;
}

function setupEventListeners() {
    elements.themeToggle?.addEventListener('click', toggleTheme);

    elements.graphTabBtn?.addEventListener('click', () => switchTab('graph'));
    elements.settingsTabBtn?.addEventListener('click', () => switchTab('settings'));
    elements.alertsTabBtn?.addEventListener('click', () => switchTab('alerts'));

    elements.realtimeModeBtn?.addEventListener('click', () => setMode('realtime'));
    elements.filterModeBtn?.addEventListener('click', () => setMode('filter'));
    elements.applyFilterBtn?.addEventListener('click', applyDateFilter);
    elements.quickDayBtn?.addEventListener('click', () => setQuickFilter('day'));
    elements.quickWeekBtn?.addEventListener('click', () => setQuickFilter('week'));
    elements.quickMonthBtn?.addEventListener('click', () => setQuickFilter('month'));
    elements.pauseBtn?.addEventListener('click', togglePause);
    elements.exportBtn?.addEventListener('click', downloadCSV);
    elements.clearBtn?.addEventListener('click', clearData);
    elements.refreshBtn?.addEventListener('click', manualRefresh);
    elements.loadSettingsBtn?.addEventListener('click', loadSettings);
    elements.saveSettingsBtn?.addEventListener('click', saveSettings);

    elements.loadAlertBtn?.addEventListener('click', loadAlertSettings);
    elements.saveAlertBtn?.addEventListener('click', saveAlertSettings);
    elements.testAlertBtn?.addEventListener('click', sendTestAlert);

    elements.maxPoints?.addEventListener('change', () => {
        MAX_POINTS = parseInt(elements.maxPoints.value, 10) || (APP_CONFIG.defaultRealtimePoints || 100);
        if (currentMode === 'realtime' && !isPaused) {
            loadInitialData();
        }
    });

    elements.filterRenderLimit?.addEventListener('change', () => {
        FILTER_RENDER_LIMIT = parseInt(elements.filterRenderLimit.value, 10) || (APP_CONFIG.defaultFilterRenderLimit || 1000);
        if (currentMode === 'filter' && filterApplied) {
            loadInitialData();
        }
    });
}

function setupSocketListeners() {
    socket.on(SOCKET_EVENTS.settingsResponse, data => {
        if (data.gain !== undefined && data.it_ms !== undefined && data.interval !== undefined && data.samples !== undefined) {
            updateSettingsDisplay(data);
        } else {
            console.error('Invalid settings format:', data);
            elements.settingsStatus.innerHTML = '<strong>Chyba:</strong> Prijaté neplatné nastavenia z ESP32';
            elements.settingsStatus.className = 'status error';
        }
    });

    socket.on(SOCKET_EVENTS.settingsSaved, () => {
        elements.settingsStatus.innerHTML = '<strong>Potvrdené:</strong> ESP32 potvrdilo prijatie nastavení!';
        elements.settingsStatus.className = 'status success';
        setTimeout(() => {
            elements.settingsStatus.innerHTML = '<strong>ℹInfo:</strong> Nové nastavenia sú aktívne na ESP32.';
            elements.settingsStatus.className = 'status';
        }, 3000);
    });

    socket.on(SOCKET_EVENTS.newData, () => {
        updateNewData();
    });

    socket.on(SOCKET_EVENTS.clearGraph, () => {
        lastUpdateTimestamp = null;
        loadInitialData();
    });
}

function initializeApp() {
    createChart();
    setupInputLimits();
    setupEventListeners();
    setupSocketListeners();
    setDefaultDates();
    initTheme();
    switchTab('graph');
    loadInitialData();

    if (elements.alertsTab && ROUTES.alertSettings) {
        loadAlertSettings();
    }
}

document.addEventListener('DOMContentLoaded', initializeApp);