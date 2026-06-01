/**
 * Expense Analysis — Application Logic
 *
 * Alpine.js component that drives the dashboard: chart rendering,
 * expense list with pagination/filters, tag management, and modals.
 */

let chartInstance = null;

function app() {
    return {
        // ── State ──────────────────────────────────────────────────
        scrolled: false,
        settingsModalOpen: false,
        statementMonth: '',
        statementCutoffDay: parseInt(localStorage.getItem('cutoffDay')) || 28,
        loading: false,

        // Chart
        chartData: [],
        chartRange: localStorage.getItem('chartRange') || '1y',
        selectedPeriodData: null,
        totalSpent: 0,
        totalCount: 0,
        avgMonthly: 0,
        totalPayments: 0,

        // Expenses table
        expenses: [],
        page: 1,
        perPage: 50,
        totalExpenses: 0,
        totalPages: 1,
        filterTag: '',
        filterCard: '',
        searchQuery: '',

        // Tags
        tags: [],
        tagRules: [],
        cards: [],

        // Add rule form
        newRule: { pattern: '', tag_id: '', match_type: 'contains' },

        // Manage Tags / Quick Rule
        manageTagsModalOpen: false,
        detailsLoading: false,
        currentExpense: null,
        quickTagForm: { pattern: '', tag_id: '', match_type: 'contains' },

        // Edit tag
        editTagModalOpen: false,
        editTagForm: { id: '', name: '', color: '', icon: '', merge_target_id: '' },

        // ── Computed ───────────────────────────────────────────────
        get summaryLabel() {
            if (!this.statementMonth) return 'All time';
            return `Statement: ${this.actualDateFrom} → ${this.actualDateTo}`;
        },

        get actualDateFrom() {
            if (!this.statementMonth) return '';
            const [y, m] = this.statementMonth.split('-');
            let date = new Date(parseInt(y), parseInt(m) - 2, this.statementCutoffDay + 1);
            const pad = n => String(n).padStart(2, '0');
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
        },

        get actualDateTo() {
            if (!this.statementMonth) return '';
            const [y, m] = this.statementMonth.split('-');
            let date = new Date(parseInt(y), parseInt(m) - 1, this.statementCutoffDay);
            const pad = n => String(n).padStart(2, '0');
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
        },

        saveCutoffDay() {
            localStorage.setItem('cutoffDay', this.statementCutoffDay);
            this.fetchExpenses();
            this.fetchSummary();
        },

        // ── Expense Details Navigation ────────────────────────────
        get currentExpenseIndex() {
            if (!this.currentExpense || !this.expenses.length) return -1;
            return this.expenses.findIndex(e => e.id === this.currentExpense.id);
        },

        get hasPrevExpense() {
            return this.currentExpenseIndex > 0;
        },

        get hasNextExpense() {
            return this.currentExpenseIndex >= 0 && this.currentExpenseIndex < this.expenses.length - 1;
        },

        get expenseNavLabel() {
            const idx = this.currentExpenseIndex;
            if (idx < 0) return '';
            const globalIdx = (this.page - 1) * this.perPage + idx + 1;
            return `${globalIdx} of ${this.totalExpenses}`;
        },

        prevExpense() {
            if (!this.hasPrevExpense) return;
            this.openManageTags(this.expenses[this.currentExpenseIndex - 1]);
        },

        nextExpense() {
            if (!this.hasNextExpense) return;
            this.openManageTags(this.expenses[this.currentExpenseIndex + 1]);
        },

        // ── Init ──────────────────────────────────────────────────
        async init() {
            const now = new Date();
            this.statementMonth = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');

            await Promise.all([
                this.fetchTags(),
                this.fetchCards(),
            ]);
            await Promise.all([
                this.fetchSummary(),
                this.fetchExpenses(),
            ]);
        },

        // ── API calls ─────────────────────────────────────────────
        async fetchExpenses() {
            this.loading = true;
            const params = new URLSearchParams({
                page: this.page,
                per_page: this.perPage,
            });
            if (this.actualDateFrom) params.set('from', this.actualDateFrom);
            if (this.actualDateTo) params.set('to', this.actualDateTo);
            if (this.filterTag) params.set('tag', this.filterTag);
            if (this.filterCard) params.set('card', this.filterCard);
            if (this.searchQuery) params.set('search', this.searchQuery);

            try {
                const res = await fetch('/api/expenses?' + params);
                const data = await res.json();
                this.expenses = data.items;
                this.totalExpenses = data.total;
                this.totalPages = data.total_pages;
            } catch (e) {
                this.expenses = [];
            }
            this.loading = false;
        },

        async fetchSummary() {
            const params = new URLSearchParams();
            if (this.statementMonth) {
                const [y, m] = this.statementMonth.split('-');
                const pad = n => String(n).padStart(2, '0');

                // Determine how many periods to look back based on chartRange
                const rangeMonths = { '1m': 1, '1y': 12, '2y': 24, '5y': 60 };
                const lookback = rangeMonths[this.chartRange] || 12;

                let fromDate = new Date(parseInt(y), parseInt(m) - 1 - lookback, 1);
                let toDate = new Date(parseInt(y), parseInt(m) - 1, 1);

                params.set('from', `${fromDate.getFullYear()}-${pad(fromDate.getMonth() + 1)}`);
                params.set('to', `${toDate.getFullYear()}-${pad(toDate.getMonth() + 1)}`);
            }

            try {
                const res = await fetch('/api/summary/monthly?' + params);
                this.chartData = await res.json();
                this.computeStats();
                this.renderChart();
            } catch (e) {
                this.chartData = [];
            }
        },

        async fetchTags() {
            try {
                const res = await fetch('/api/tags');
                this.tags = await res.json();

                const res2 = await fetch('/api/tag-rules');
                this.tagRules = await res2.json();
            } catch (e) {
                this.tags = [];
                this.tagRules = [];
            }
        },

        async fetchCards() {
            try {
                const params = new URLSearchParams();
                if (this.actualDateFrom) params.set('from', this.actualDateFrom);
                if (this.actualDateTo) params.set('to', this.actualDateTo);
                const res = await fetch('/api/cards?' + params);
                this.cards = await res.json();
                if (this.filterCard && !this.cards.find(c => c.card_number === this.filterCard)) {
                    this.filterCard = '';
                }
            } catch (e) {
                this.cards = [];
            }
        },

        // ── Actions ───────────────────────────────────────────────
        shiftStatementMonth(offset) {
            if (!this.statementMonth) return;
            const [y, m] = this.statementMonth.split('-');
            let date = new Date(parseInt(y), parseInt(m) - 1 + offset, 1);
            const pad = n => String(n).padStart(2, '0');
            this.statementMonth = `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;
            this.applyDateRange();
        },

        applyDateRange() {
            this.page = 1;
            this.fetchCards().then(() => {
                this.fetchSummary();
                this.fetchExpenses();
            });
        },

        setChartRange(range) {
            this.chartRange = range;
            localStorage.setItem('chartRange', range);
            this.fetchSummary();
        },

        nextPage() {
            if (this.page < this.totalPages) {
                this.page++;
                this.fetchExpenses();
            }
        },

        prevPage() {
            if (this.page > 1) {
                this.page--;
                this.fetchExpenses();
            }
        },

        async openManageTags(exp) {
            this.currentExpense = exp;
            this.quickTagForm = {
                pattern: exp.description.substring(0, 20),
                tag_id: '',
                match_type: 'contains'
            };
            this.manageTagsModalOpen = true;
            this.detailsLoading = true;

            try {
                const res = await fetch(`/api/expenses/${exp.id}/details`);
                const data = await res.json();
                if (this.currentExpense && this.currentExpense.id === data.id) {
                    this.currentExpense = data;
                }
            } catch (e) {
                console.error("Failed to load details", e);
            } finally {
                this.detailsLoading = false;
            }
        },

        async submitQuickTag() {
            if (!this.quickTagForm.pattern || !this.quickTagForm.tag_id) return;

            await fetch('/api/tag-rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.quickTagForm),
            });

            await this.fetchTags();
            await this.retag();

            // Refresh details view if still open
            if (this.currentExpense && this.manageTagsModalOpen) {
                this.openManageTags(this.currentExpense);
            }
        },

        async removeTag(expenseId, tagId) {
            if (!expenseId || !tagId) return;
            await fetch(`/api/expenses/${expenseId}/tags/${tagId}`, { method: 'DELETE' });
            await Promise.all([
                this.fetchExpenses(),
                this.fetchSummary(),
            ]);
            if (this.currentExpense?.id === expenseId && this.manageTagsModalOpen) {
                this.openManageTags(this.currentExpense);
            }
        },

        async unsuppressTag(expenseId, tagId) {
            await fetch(`/api/expenses/${expenseId}/tags/${tagId}/restore`, { method: 'POST' });
            await this.fetchExpenses();
            if (this.currentExpense?.id === expenseId && this.manageTagsModalOpen) {
                this.openManageTags(this.currentExpense);
            }
        },

        async hardDeleteTag(expenseId, tagId) {
            if (!confirm("Delete tag from this expense? (It may return if an auto-rule still matches)")) return;
            await fetch(`/api/expenses/${expenseId}/tags/${tagId}/hard`, { method: 'DELETE' });
            await this.fetchExpenses();
            if (this.currentExpense?.id === expenseId && this.manageTagsModalOpen) {
                this.openManageTags(this.currentExpense);
            }
        },

        async addRule() {
            if (!this.newRule.pattern || !this.newRule.tag_id) return;

            await fetch('/api/tag-rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.newRule),
            });

            this.newRule = { pattern: '', tag_id: '', match_type: 'contains' };
            await this.fetchTags();
        },

        async deleteRule(id) {
            if (!confirm('Are you sure you want to delete this rule?')) return;
            await fetch(`/api/tag-rules/${id}`, { method: 'DELETE' });
            await this.fetchTags();
            if (this.currentExpense && this.manageTagsModalOpen) {
                await this.openManageTags(this.currentExpense);
            }
        },

        async retag() {
            this.loading = true;
            await fetch('/api/retag', { method: 'POST' });
            await Promise.all([
                this.fetchExpenses(),
                this.fetchSummary(),
            ]);
            this.loading = false;
        },

        openEditTag(tag) {
            this.editTagForm = {
                id: tag.id,
                name: tag.name,
                color: tag.color,
                icon: tag.icon,
                merge_target_id: ''
            };
            this.editTagModalOpen = true;
        },

        openCreateTag() {
            this.editTagForm = {
                id: '',
                name: '',
                color: '#6366f1',
                icon: '🏷️',
                merge_target_id: ''
            };
            this.editTagModalOpen = true;
        },

        async submitEditTag() {
            if (!this.editTagForm.name) return;
            this.loading = true;

            if (this.editTagForm.id) {
                // Update existing tag
                await fetch(`/api/tags/${this.editTagForm.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.editTagForm.name,
                        color: this.editTagForm.color,
                        icon: this.editTagForm.icon,
                    }),
                });
            } else {
                // Create new tag
                await fetch('/api/tags', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.editTagForm.name,
                        color: this.editTagForm.color,
                        icon: this.editTagForm.icon,
                    }),
                });
            }

            this.editTagModalOpen = false;
            await this.fetchTags();
            await Promise.all([this.fetchExpenses(), this.fetchSummary()]);
            this.loading = false;
        },

        async submitMergeTag() {
            if (!this.editTagForm.id || !this.editTagForm.merge_target_id) return;
            if (!confirm('Are you sure you want to merge these tags? This cannot be undone.')) return;

            this.loading = true;
            await fetch(`/api/tags/${this.editTagForm.id}/merge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_tag_id: parseInt(this.editTagForm.merge_target_id) }),
            });
            this.editTagModalOpen = false;
            await this.fetchTags();
            await this.retag();
        },

        async deleteTag(tagId) {
            if (!tagId) return;
            if (!confirm('Are you sure you want to delete this tag? All associated rules will also be deleted.')) return;

            this.loading = true;
            await fetch(`/api/tags/${tagId}`, { method: 'DELETE' });
            this.editTagModalOpen = false;
            await this.fetchTags();
            await this.retag();
        },

        // ── Computed stats ────────────────────────────────────────
        computeStats() {
            this.totalSpent = this.chartData.reduce((sum, m) => sum + m.total, 0);
            this.totalCount = this.chartData.reduce((sum, m) => sum + m.count, 0);
            this.avgMonthly = this.chartData.length > 0 ? this.totalSpent / this.chartData.length : 0;
            this.totalPayments = 0;

            // Default to the last (most recent) period
            if (this.chartData.length > 0) {
                this.selectedPeriodData = this.chartData[this.chartData.length - 1];
            }
        },

        // ── Formatters ────────────────────────────────────────────
        formatCurrency(val) {
            return new Intl.NumberFormat('tr-TR', {
                style: 'currency',
                currency: 'TRY',
                minimumFractionDigits: 0,
                maximumFractionDigits: 0,
            }).format(val);
        },

        formatAmount(amount, currency) {
            const cur = currency || 'TRY';
            return new Intl.NumberFormat('tr-TR', {
                style: 'currency',
                currency: cur === 'TRY' ? 'TRY' : cur,
                minimumFractionDigits: 2,
            }).format(amount);
        },

        // ── Chart ─────────────────────────────────────────────────
        renderChart() {
            const el = document.getElementById('monthlyChart');
            if (!el) return;

            if (!chartInstance) {
                chartInstance = echarts.init(el, 'dark');
                window.addEventListener('resize', () => chartInstance && chartInstance.resize());
            }

            if (!this.chartData || this.chartData.length === 0) {
                chartInstance.clear();
                return;
            }

            // Collect all unique tags across months
            const allTags = new Set();
            for (const m of this.chartData) {
                for (const tagName of Object.keys(m.by_tag || {})) {
                    allTags.add(tagName);
                }
            }
            const tagNames = [...allTags].sort();

            // Labels
            const labels = this.chartData.map(m => m.label);

            // Build one series per tag (stacked)
            const series = tagNames.map(tagName => {
                let color = '#64748b';
                for (const m of this.chartData) {
                    if (m.by_tag && m.by_tag[tagName]) {
                        color = m.by_tag[tagName].color;
                        break;
                    }
                }

                return {
                    name: tagName,
                    type: 'bar',
                    stack: 'total',
                    emphasis: { focus: 'series' },
                    itemStyle: { color: color, borderRadius: [0, 0, 0, 0] },
                    data: this.chartData.map(m => {
                        return m.by_tag && m.by_tag[tagName] ? m.by_tag[tagName].total : 0;
                    }),
                };
            });

            // Add "untagged" series (total minus sum of tags)
            series.push({
                name: 'untagged',
                type: 'bar',
                stack: 'total',
                emphasis: { focus: 'series' },
                itemStyle: { color: '#334155', borderRadius: [2, 2, 0, 0] },
                data: this.chartData.map(m => {
                    const taggedTotal = Object.values(m.by_tag || {}).reduce((s, t) => s + t.total, 0);
                    return Math.max(0, m.total - taggedTotal);
                }),
            });

            const option = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    borderColor: 'rgba(148, 163, 184, 0.2)',
                    textStyle: { color: '#e2e8f0', fontSize: 12 },
                    formatter: (params) => {
                        const idx = params[0].dataIndex;
                        const periodData = this.chartData[idx];
                        const fmtDec = (v) => new Intl.NumberFormat('tr-TR', {minimumFractionDigits:2, maximumFractionDigits:2}).format(v);

                        let total = 0;
                        let tagRows = '';
                        for (const p of params) {
                            if (p.value > 0) {
                                total += p.value;
                                tagRows += '<div style="display:flex;justify-content:space-between;gap:24px;">'
                                    + '<span>' + p.marker + ' ' + p.seriesName + '</span>'
                                    + '<span style="font-weight:600">' + fmtDec(p.value) + ' ₺</span></div>';
                            }
                        }

                        // Currency breakdown
                        let currencyRows = '';
                        if (periodData && periodData.by_currency) {
                            const currencies = [
                                { code: 'TRY', sym: '₺', color: '#34d399' },
                                { code: 'USD', sym: '$', color: '#60a5fa' },
                                { code: 'EUR', sym: '€', color: '#c084fc' },
                            ];
                            const activeCurrencies = currencies.filter(c => periodData.by_currency[c.code]);
                            if (activeCurrencies.length > 0) {
                                currencyRows = '<div style="border-top:1px solid rgba(148,163,184,0.15);margin-top:8px;padding-top:6px">'
                                    + '<div style="font-size:10px;color:#94a3b8;text-transform:uppercase;margin-bottom:4px">Currency</div>';
                                for (const c of activeCurrencies) {
                                    const data = periodData.by_currency[c.code];
                                    currencyRows += '<div style="display:flex;justify-content:space-between;gap:16px;font-size:11px">'
                                        + '<span style="color:#94a3b8">' + c.code + '</span>'
                                        + '<span style="font-weight:600;color:' + c.color + '">' + fmtDec(data.total) + ' ' + c.sym + '</span></div>';
                                }
                                currencyRows += '</div>';
                            }
                        }

                        // Card breakdown
                        let cardRows = '';
                        if (periodData && periodData.by_card) {
                            cardRows = '<div style="border-top:1px solid rgba(148,163,184,0.15);margin-top:8px;padding-top:6px">'
                                + '<div style="font-size:10px;color:#94a3b8;text-transform:uppercase;margin-bottom:4px">Cards</div>';
                            for (const card of periodData.by_card) {
                                const sym = card.currency === 'TRY' ? '₺' : (card.currency === 'USD' ? '$' : (card.currency === 'EUR' ? '€' : card.currency));
                                const color = card.currency === 'TRY' ? '#34d399' : '#60a5fa';
                                cardRows += '<div style="display:flex;justify-content:space-between;gap:16px;font-size:11px">'
                                    + '<span style="font-family:monospace;color:#94a3b8">' + card.card_number + '</span>'
                                    + '<span style="font-weight:600;color:' + color + '">' + fmtDec(card.total) + ' ' + sym + '</span></div>';
                            }
                            cardRows += '</div>';
                        }

                        return '<div style="font-weight:600;margin-bottom:6px">' + params[0].axisValue
                            + '</div>' + tagRows + currencyRows + cardRows;
                    },
                },
                legend: {
                    type: 'scroll',
                    bottom: 0,
                    textStyle: { color: '#94a3b8', fontSize: 11 },
                    pageTextStyle: { color: '#94a3b8' },
                },
                grid: {
                    left: 60,
                    right: 20,
                    top: 20,
                    bottom: 50,
                },
                xAxis: {
                    type: 'category',
                    data: labels,
                    axisLabel: { color: '#64748b', fontSize: 11 },
                    axisLine: { lineStyle: { color: '#334155' } },
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        color: '#64748b',
                        fontSize: 11,
                        formatter: function(v) {
                            if (v >= 1000) return (v / 1000).toFixed(0) + 'k';
                            return v;
                        },
                    },
                    splitLine: { lineStyle: { color: '#1e293b' } },
                },
                series: series,
            };

            chartInstance.setOption(option, true);
        },
    };
}
