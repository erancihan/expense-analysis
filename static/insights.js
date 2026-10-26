/**
 * /insights page — the `insights()` Alpine component.
 *
 * Independent of static/app.js: the panels share none of the dashboard's
 * state. Lens, basis and range are mirrored into the query string so a view
 * can be bookmarked. Every request here is a GET, so unlike the dashboard
 * this page needs no CSRF fetch wrapper.
 *
 * ECharts instances live in a module-level map, outside Alpine's reactive
 * state: Alpine wraps whatever it holds in proxies, which ECharts does not
 * survive.
 */

const RANGES = [
    { id: '12m', label: '12 months' },
    { id: '24m', label: '24 months' },
    { id: 'since-2022', label: 'Since 2022' },
    { id: 'all', label: 'All' },
];
const BASES = ['billed', 'purchased'];

const COLOR_BAR = '#6366f1';
const COLOR_INCOMPLETE = '#475569';
const COLOR_ROLL_3 = '#f59e0b';
const COLOR_ROLL_12 = '#34d399';
const COLOR_ANNUAL = '#818cf8';
const COLOR_NEUTRAL = '#64748b';
// For tags stored without a colour.
const FALLBACK_PALETTE = ['#f472b6', '#38bdf8', '#a3e635', '#fb923c', '#c084fc', '#2dd4bf', '#facc15', '#f87171'];

const AXIS_TEXT = { color: '#64748b', fontSize: 11 };
const AXIS_LINE = { lineStyle: { color: '#334155' } };
const SPLIT_LINE = { lineStyle: { color: '#1e293b' } };
const TOOLTIP = {
    backgroundColor: 'rgba(15, 23, 42, 0.95)',
    borderColor: 'rgba(148, 163, 184, 0.2)',
    textStyle: { color: '#e2e8f0', fontSize: 12 },
};

const charts = {};
let mixClickHandler = null;

function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (!charts[id]) {
        charts[id] = echarts.init(el, 'dark');
        window.addEventListener('resize', () => charts[id] && charts[id].resize());
    }
    return charts[id];
}

function shiftMonth(month, delta) {
    const [y, m] = month.split('-').map(Number);
    const index = y * 12 + (m - 1) + delta;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
}

function compactNumber(v) {
    const abs = Math.abs(v);
    if (abs >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (abs >= 1e3) return (v / 1e3).toFixed(0) + 'k';
    return String(Math.round(v));
}

function insights() {
    return {
        RANGES,
        BASES,
        scrolled: false,
        lens: '',
        basis: 'billed',
        range: 'since-2022',
        parent: '',
        meta: null,
        trend: null,
        loading: false,
        error: '',

        get hasData() {
            return !!(this.meta && this.meta.first_month);
        },

        get lensLabel() {
            const option = this.meta && this.meta.lenses.find(l => l.id === this.lens);
            return option ? option.label : '';
        },

        async init() {
            this.readQuery();
            await this.fetchMeta();
            if (!this.meta) return;
            const option = this.meta.lenses.find(l => l.id === this.lens);
            if (!option || !option.available) this.lens = this.meta.default_lens;
            this.syncQuery();
            await this.fetchTrend();
        },

        readQuery() {
            const q = new URLSearchParams(window.location.search);
            this.lens = q.get('lens') || '';
            if (BASES.includes(q.get('basis'))) this.basis = q.get('basis');
            if (RANGES.some(r => r.id === q.get('range'))) this.range = q.get('range');
            this.parent = q.get('parent') || '';
        },

        syncQuery() {
            const q = new URLSearchParams({ lens: this.lens, basis: this.basis, range: this.range });
            if (this.parent) q.set('parent', this.parent);
            history.replaceState(null, '', `${window.location.pathname}?${q}`);
        },

        rangeFrom() {
            if (!this.meta || !this.meta.first_month) return '';
            const first = this.meta.first_month;
            const last = this.meta.last_month;
            const from = {
                '12m': shiftMonth(last, -11),
                '24m': shiftMonth(last, -23),
                'since-2022': '2022-01',
                'all': first,
            }[this.range] || first;
            return from < first ? first : from;
        },

        async fetchMeta() {
            try {
                const response = await fetch('/api/insights/meta');
                const body = await response.json();
                if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
                this.meta = body;
            } catch (e) {
                this.error = `Could not load insights: ${e.message}`;
            }
        },

        async fetchTrend() {
            if (!this.hasData) return;
            this.loading = true;
            const q = new URLSearchParams({
                lens: this.lens, basis: this.basis, from: this.rangeFrom(), to: this.meta.last_month,
            });
            if (this.parent) q.set('parent', this.parent);
            try {
                const response = await fetch(`/api/insights/trend?${q}`);
                const body = await response.json();
                if (!response.ok) {
                    this.error = body.error || `HTTP ${response.status}`;
                    return;
                }
                this.error = '';
                this.trend = body;
                this.$nextTick(() => this.renderCharts());
            } catch (e) {
                this.error = `Could not load the trend: ${e.message}`;
            } finally {
                this.loading = false;
            }
        },

        setLens(id) { this.lens = id; this.syncQuery(); this.fetchTrend(); },
        setBasis(id) { this.basis = id; this.syncQuery(); this.fetchTrend(); },
        setRange(id) { this.range = id; this.syncQuery(); this.fetchTrend(); },
        drillInto(name) { this.parent = name; this.syncQuery(); this.fetchTrend(); },
        drillOut() { this.parent = ''; this.syncQuery(); this.fetchTrend(); },

        // ── Formatting ──────────────────────────────────────────────────

        fmt(value) {
            if (value === null || value === undefined) return '—';
            if (this.lens === 'usd') {
                return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD',
                    minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
            }
            return new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY',
                minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(value);
        },

        fmtPct(value) {
            if (value === null || value === undefined) return 'n/a';
            const sign = value > 0 ? '+' : value < 0 ? '−' : '';
            return `${sign}${Math.abs(value).toFixed(1)} %`;
        },

        // Spending up is the warning colour.
        pctClass(value) {
            if (value === null || value === undefined) return 'text-slate-500';
            return value > 0 ? 'text-rose-400' : 'text-emerald-400';
        },

        colorFor(name) {
            if (name === 'untagged' || name === 'other') return COLOR_NEUTRAL;
            const stored = this.meta && this.meta.tag_colors[name];
            if (stored) return stored;
            let hash = 0;
            for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
            return FALLBACK_PALETTE[hash % FALLBACK_PALETTE.length];
        },

        // ── Charts ──────────────────────────────────────────────────────

        renderCharts() {
            if (!this.trend) {
                Object.values(charts).forEach(c => c.clear());
                return;
            }
            this.renderMonthly();
            this.renderAnnual();
            this.renderMix();
        },

        renderMonthly() {
            const c = chart('monthlyChart');
            if (!c) return;
            const monthly = this.trend.monthly;
            const fmt = v => this.fmt(v);
            const bars = monthly.map(m => m.total === null ? null : {
                value: m.total,
                itemStyle: { color: m.coverage === 'incomplete' ? COLOR_INCOMPLETE : COLOR_BAR },
            });
            c.setOption({
                backgroundColor: 'transparent',
                tooltip: {
                    ...TOOLTIP,
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    formatter: (params) => {
                        const m = monthly[params[0].dataIndex];
                        let html = `<div style="font-weight:600;margin-bottom:4px">${m.month}</div>`;
                        if (m.coverage === 'none') {
                            html += '<div style="color:#94a3b8">No statement covers this month</div>';
                        } else {
                            for (const p of params) {
                                if (p.value === null || p.value === undefined) continue;
                                html += '<div style="display:flex;justify-content:space-between;gap:24px">'
                                    + `<span>${p.marker} ${p.seriesName}</span>`
                                    + `<span style="font-weight:600">${fmt(p.value)}</span></div>`;
                            }
                            if (m.coverage === 'incomplete') {
                                html += '<div style="color:#fbbf24;margin-top:4px">Incomplete: a statement is missing for this month</div>';
                            }
                        }
                        return html;
                    },
                },
                grid: { left: 60, right: 20, top: 20, bottom: 30 },
                xAxis: { type: 'category', data: monthly.map(m => m.month), axisLabel: AXIS_TEXT, axisLine: AXIS_LINE },
                yAxis: { type: 'value', axisLabel: { ...AXIS_TEXT, formatter: compactNumber }, splitLine: SPLIT_LINE },
                series: [
                    { name: 'Month', type: 'bar', data: bars, barMaxWidth: 28 },
                    { name: '3-month avg', type: 'line', data: this.trend.rolling['3'], connectNulls: false,
                      smooth: false, symbol: 'none', lineStyle: { color: COLOR_ROLL_3, width: 2 }, itemStyle: { color: COLOR_ROLL_3 } },
                    { name: '12-month avg', type: 'line', data: this.trend.rolling['12'], connectNulls: false,
                      smooth: false, symbol: 'none', lineStyle: { color: COLOR_ROLL_12, width: 2 }, itemStyle: { color: COLOR_ROLL_12 } },
                ],
            }, true);
        },

        renderAnnual() {
            const c = chart('annualChart');
            if (!c) return;
            const annual = this.trend.annual;
            const fmt = v => this.fmt(v);
            const labels = annual.map(a => a.is_ytd ? `${a.year} YTD · ${a.months_covered} mo` : String(a.year));
            c.setOption({
                backgroundColor: 'transparent',
                tooltip: {
                    ...TOOLTIP,
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    formatter: (params) => {
                        const a = annual[params[0].dataIndex];
                        return `<div style="font-weight:600">${labels[params[0].dataIndex]}</div>`
                            + `<div>${fmt(a.total)}</div>`
                            + `<div style="color:#94a3b8">${a.months_covered} complete month${a.months_covered === 1 ? '' : 's'}</div>`;
                    },
                },
                grid: { left: 60, right: 20, top: 20, bottom: 30 },
                xAxis: { type: 'category', data: labels, axisLabel: AXIS_TEXT, axisLine: AXIS_LINE },
                yAxis: { type: 'value', axisLabel: { ...AXIS_TEXT, formatter: compactNumber }, splitLine: SPLIT_LINE },
                series: [{
                    name: 'Year', type: 'bar', barMaxWidth: 48,
                    data: annual.map(a => ({ value: a.total, itemStyle: { color: COLOR_ANNUAL, opacity: a.is_ytd ? 0.6 : 1 } })),
                    label: { show: true, position: 'top', color: '#94a3b8', fontSize: 11, formatter: p => compactNumber(p.value) },
                }],
            }, true);
        },

        renderMix() {
            const c = chart('mixChart');
            if (!c) return;
            const mix = this.trend.mix;
            const key = this.parent ? 'by_child' : 'by_parent';
            // Series ordered by total share so the largest sits at the bottom of the stack.
            const weight = {};
            for (const q of mix) for (const [name, share] of Object.entries(q[key])) weight[name] = (weight[name] || 0) + share;
            const names = Object.keys(weight).sort((a, b) => weight[b] - weight[a]);
            const series = names.map(name => ({
                name, type: 'line', stack: 'mix', symbol: 'none', smooth: false,
                areaStyle: { opacity: 0.85 }, lineStyle: { width: 0 },
                color: this.colorFor(name),
                emphasis: { focus: 'series' },
                data: mix.map(q => Object.keys(q[key]).length ? +( (q[key][name] || 0) * 100 ).toFixed(2) : null),
            }));
            c.setOption({
                backgroundColor: 'transparent',
                tooltip: {
                    ...TOOLTIP,
                    trigger: 'axis',
                    formatter: (params) => {
                        const rows = params.filter(p => p.value).sort((a, b) => b.value - a.value);
                        let html = `<div style="font-weight:600;margin-bottom:4px">${params[0].axisValue}</div>`;
                        for (const p of rows) {
                            html += '<div style="display:flex;justify-content:space-between;gap:24px">'
                                + `<span>${p.marker} ${p.seriesName}</span><span style="font-weight:600">${p.value.toFixed(1)} %</span></div>`;
                        }
                        return html;
                    },
                },
                legend: { type: 'scroll', bottom: 0, textStyle: { color: '#94a3b8', fontSize: 11 }, pageTextStyle: { color: '#94a3b8' } },
                grid: { left: 50, right: 20, top: 20, bottom: 50 },
                xAxis: { type: 'category', boundaryGap: false, data: mix.map(q => q.quarter), axisLabel: AXIS_TEXT, axisLine: AXIS_LINE },
                yAxis: { type: 'value', max: 100, axisLabel: { ...AXIS_TEXT, formatter: '{value} %' }, splitLine: SPLIT_LINE },
                series,
            }, true);
            // Stacked-area polygons do not emit series click events, so hit-test
            // the click against the stack: pixel → (quarter, %) → the band it falls in.
            // Remove only our own previous handler: a bare off('click') would
            // also drop ECharts' internal one, which drives the legend.
            const zr = c.getZr();
            if (mixClickHandler) zr.off('click', mixClickHandler);
            mixClickHandler = (e) => {
                if (this.parent) return;
                const point = [e.offsetX, e.offsetY];
                if (!c.containPixel({ gridIndex: 0 }, point)) return;
                const [index, pct] = c.convertFromPixel({ gridIndex: 0 }, point);
                const quarter = mix[Math.round(index)];
                if (!quarter) return;
                const shown = (c.getOption().legend[0] || {}).selected || {};
                let top = 0;
                for (const s of series.filter(s => shown[s.name] !== false)) {
                    top += s.data[Math.round(index)] || 0;
                    if (pct <= top) {
                        if (s.name !== 'untagged') this.drillInto(s.name);
                        return;
                    }
                }
            };
            zr.on('click', mixClickHandler);
        },
    };
}
