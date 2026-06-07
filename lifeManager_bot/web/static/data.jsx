// data.jsx — мета приёмов пищи + хелперы форматирования.
// Реальные данные приходят из API (api.js), здесь только константы и утилиты.

const MEAL_META = {
  breakfast: { label: 'Завтрак', icon: '☕', tint: '#5AB4FF' },
  lunch:     { label: 'Обед',    icon: '🍲', tint: '#F5A524' },
  dinner:    { label: 'Ужин',    icon: '🌙', tint: '#B79EFF' },
  snack:     { label: 'Перекус', icon: '🍎', tint: '#4FB286' },
  other:     { label: 'Другое',  icon: '🍽', tint: '#8A9AAE' },
};
const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];

const DOW = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const MONTHS = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];

// Format helpers
function fmtNum(n, digits = 0) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(digits).replace(/\.0$/, '');
}
function ddmmm(dateStr) {
  const d = new Date(dateStr);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}
function dowOf(dateStr) {
  return DOW[new Date(dateStr).getDay()];
}
// Локальная дата 'YYYY-MM-DD' со сдвигом на n дней от base ('YYYY-MM-DD').
function shiftDate(dateStr, n) {
  const d = new Date(dateStr + 'T00:00:00');
  d.setDate(d.getDate() + n);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}
function autoMeal(hour) {
  if (hour >= 5 && hour < 11) return 'breakfast';
  if (hour >= 11 && hour < 16) return 'lunch';
  if (hour >= 16 && hour < 22) return 'dinner';
  return 'snack';
}

// Сумма по списку записей (поля p/f/c как во фронте).
function sumEntries(entries) {
  return entries.reduce((a, e) => ({
    kcal: a.kcal + (+e.kcal || 0),
    p: a.p + (+e.p || 0),
    f: a.f + (+e.f || 0),
    c: a.c + (+e.c || 0),
  }), { kcal: 0, p: 0, f: 0, c: 0 });
}

// Есть ли хоть одна заданная цель.
function hasAnyGoal(goals) {
  return !!goals && [goals.kcal, goals.protein, goals.fat, goals.carbs].some(v => v != null);
}

Object.assign(window, {
  MEAL_META, MEAL_ORDER, DOW, MONTHS,
  fmtNum, ddmmm, dowOf, shiftDate, autoMeal, sumEntries, hasAnyGoal,
});
