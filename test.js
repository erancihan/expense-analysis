const y = 2026;
const m = 4;
const statementCutoffDay = 28;
let dateFrom = new Date(parseInt(y), parseInt(m) - 2, statementCutoffDay + 1);
let dateTo = new Date(parseInt(y), parseInt(m) - 1, statementCutoffDay);

const pad = n => String(n).padStart(2, '0');
const localIso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

console.log('DateFrom (toISOString):', dateFrom.toISOString().split('T')[0]);
console.log('DateFrom (Local):', localIso(dateFrom));

console.log('DateTo (toISOString):', dateTo.toISOString().split('T')[0]);
console.log('DateTo (Local):', localIso(dateTo));
