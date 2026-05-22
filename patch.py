import re

with open('static/index.html', 'r') as f:
    content = f.read()

# 1. State changes
content = content.replace("dateFrom: '',\n        dateTo: '',", "statementMonth: '',\n        statementCutoffDay: parseInt(localStorage.getItem('cutoffDay')) || 28,")
content = content.replace("if (!this.dateFrom && !this.dateTo) return 'All time';\n            const from = this.dateFrom || '...';\n            const to = this.dateTo || '...';\n            return from + ' → ' + to;", "if (!this.statementMonth) return 'All time';\n            return `Statement: ${this.actualDateFrom} → ${this.actualDateTo}`;\n        },\n\n        get actualDateFrom() {\n            if (!this.statementMonth) return '';\n            const [y, m] = this.statementMonth.split('-');\n            let date = new Date(parseInt(y), parseInt(m) - 2, this.statementCutoffDay + 1);\n            const pad = n => String(n).padStart(2, '0');\n            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;\n        },\n\n        get actualDateTo() {\n            if (!this.statementMonth) return '';\n            const [y, m] = this.statementMonth.split('-');\n            let date = new Date(parseInt(y), parseInt(m) - 1, this.statementCutoffDay);\n            const pad = n => String(n).padStart(2, '0');\n            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;\n        },\n\n        saveCutoffDay() {\n            localStorage.setItem('cutoffDay', this.statementCutoffDay);\n            this.fetchExpenses();\n            this.fetchSummary();")
content = content.replace("const yearAgo = new Date(now.getFullYear() - 1, now.getMonth(), 1);\n            this.dateFrom = yearAgo.getFullYear() + '-' + String(yearAgo.getMonth() + 1).padStart(2, '0');\n            this.dateTo = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');", "this.statementMonth = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');")
content = content.replace("if (this.dateFrom) params.set('from', this.dateFrom);\n            if (this.dateTo) params.set('to', this.dateTo);", "if (this.actualDateFrom) params.set('from', this.actualDateFrom);\n            if (this.actualDateTo) params.set('to', this.actualDateTo);")
content = content.replace("if (this.actualDateFrom) params.set('from', this.actualDateFrom);\n            if (this.actualDateTo) params.set('to', this.actualDateTo);\n\n            try {\n                const res = await fetch('/api/summary/monthly?' + params);", "if (this.statementMonth) {\n                const [y, m] = this.statementMonth.split('-');\n                let toDate = new Date(parseInt(y), parseInt(m) - 1, 1);\n                let fromDate = new Date(parseInt(y) - 1, parseInt(m) - 1, 1);\n                const pad = n => String(n).padStart(2, '0');\n                params.set('from', `${fromDate.getFullYear()}-${pad(fromDate.getMonth() + 1)}`);\n                params.set('to', `${toDate.getFullYear()}-${pad(toDate.getMonth() + 1)}`);\n            }\n\n            try {\n                const res = await fetch('/api/summary/monthly?' + params);")

# 2. HTML Picker
old_picker = """<!-- Date range picker -->
            <div class="flex items-center gap-3 glass-card rounded-xl px-4 py-2.5">
                <div class="flex items-center gap-2">
                    <label class="text-xs text-slate-400 uppercase tracking-wider font-medium">From</label>
                    <input type="month" x-model="dateFrom"
                           class="bg-slate-800/60 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none">
                </div>
                <span class="text-slate-600">–</span>
                <div class="flex items-center gap-2">
                    <label class="text-xs text-slate-400 uppercase tracking-wider font-medium">To</label>
                    <input type="month" x-model="dateTo"
                           class="bg-slate-800/60 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none">
                </div>
                <button @click="applyDateRange()"
                        class="btn-press bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition-colors">
                    Apply
                </button>
            </div>"""
new_picker = """<!-- Statement Month & Cutoff -->
            <div class="flex items-center gap-3 glass-card rounded-xl px-4 py-2.5">
                <div class="flex items-center gap-2" title="The day of the month when your statement ends">
                    <label class="text-xs text-slate-400 uppercase tracking-wider font-medium">Cutoff Day</label>
                    <input type="number" min="1" max="31" x-model.number="statementCutoffDay" @change="saveCutoffDay()"
                           class="w-16 bg-slate-800/60 border border-slate-700 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none">
                </div>
                <div class="w-px h-6 bg-slate-700/50"></div>
                <div class="flex items-center gap-1">
                    <label class="text-xs text-slate-400 uppercase tracking-wider font-medium mr-1">Statement</label>
                    <button @click="shiftStatementMonth(-1)" class="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors focus:outline-none" title="Previous Month">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                    </button>
                    <input type="month" x-model="statementMonth" @change="applyDateRange()"
                           class="bg-slate-800/60 border border-slate-700 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none">
                    <button @click="shiftStatementMonth(1)" class="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors focus:outline-none" title="Next Month">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
                    </button>
                </div>
            </div>"""
content = content.replace(old_picker, new_picker)

# 3. Card Tabs
old_select = """<select x-model="filterCard" @change="fetchExpenses()"
                        class="bg-slate-800/60 border border-slate-700 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none">
                    <option value="">All Cards</option>
                    <template x-for="card in cards" :key="card.card_number">
                        <option :value="card.card_number" x-text="card.masked + ' (' + card.count + ')'"></option>
                    </template>
                </select>
            </div>
        </div>"""
new_tabs = """</div>
        </div>
        <!-- Card Tabs -->
        <div class="flex items-center gap-1 mb-4 overflow-x-auto border-b border-slate-700/50">
            <button @click="filterCard = ''; fetchExpenses()"
                    :class="filterCard === '' ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300 hover:border-slate-600'"
                    class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap">
                All Cards
            </button>
            <template x-for="card in cards" :key="card.card_number">
                <button @click="filterCard = card.card_number; fetchExpenses()"
                        :class="filterCard === card.card_number ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300 hover:border-slate-600'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap"
                        x-text="card.masked">
                </button>
            </template>
        </div>"""
content = content.replace(old_select, new_tabs)

# 4. Fetch Cards params
old_fetch_cards = """        async fetchCards() {
            try {
                const res = await fetch('/api/cards');
                this.cards = await res.json();
            } catch (e) {
                this.cards = [];
            }
        },"""
new_fetch_cards = """        async fetchCards() {
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
        },"""
content = content.replace(old_fetch_cards, new_fetch_cards)

# 5. ApplyDateRange
old_apply = """        applyDateRange() {
            this.page = 1;
            this.fetchSummary();
            this.fetchExpenses();
        },"""
new_apply = """        shiftStatementMonth(offset) {
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
        },"""
content = content.replace(old_apply, new_apply)

with open('static/index.html', 'w') as f:
    f.write(content)
