// data.jsx — mock data + helpers
const GOAL = { kcal: 2200, protein: 110, fat: 70, carbs: 280 };

const MEAL_META = {
  breakfast: { label: 'Завтрак', icon: '☕', tint: '#5AB4FF' },
  lunch:     { label: 'Обед',    icon: '🍲', tint: '#F5A524' },
  dinner:    { label: 'Ужин',    icon: '🌙', tint: '#B79EFF' },
  snack:     { label: 'Перекус', icon: '🍎', tint: '#4FB286' },
};
const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack'];

// Today: filled state
const TODAY_ENTRIES = [
  { id: 1, time: '08:24', meal: 'breakfast', name: 'Овсянка на молоке',     w: 250, kcal: 285, p: 11, f: 7,  c: 44, src: 'manual' },
  { id: 2, time: '08:25', meal: 'breakfast', name: 'Кофе с молоком',         w: 200, kcal: 80,  p: 4,  f: 4,  c: 7,  src: 'manual' },
  { id: 3, time: '08:26', meal: 'breakfast', name: 'Банан',                  w: 120, kcal: 107, p: 1,  f: 0,  c: 28, src: 'manual' },

  { id: 4, time: '13:40', meal: 'lunch',     name: 'Куриная грудка гриль',  w: 180, kcal: 297, p: 56, f: 6,  c: 0,  src: 'photo' },
  { id: 5, time: '13:41', meal: 'lunch',     name: 'Рис басмати',            w: 200, kcal: 248, p: 5,  f: 1,  c: 54, src: 'photo' },
  { id: 6, time: '13:42', meal: 'lunch',     name: 'Салат греческий',       w: 150, kcal: 165, p: 4,  f: 13, c: 8,  src: 'photo' },

  { id: 7, time: '16:10', meal: 'snack',     name: 'Греческий йогурт',      w: 170, kcal: 102, p: 17, f: 0,  c: 7,  src: 'manual' },
  { id: 8, time: '16:11', meal: 'snack',     name: 'Орехи микс',            w: 25,  kcal: 158, p: 5,  f: 14, c: 4,  src: 'manual' },
];

// History days (last 7)
const HISTORY = [
  { date: '2026-05-19', dow: 'Пн', kcal: 1980, p: 118, f: 58, c: 220, count: 6 },
  { date: '2026-05-20', dow: 'Вт', kcal: 2240, p: 102, f: 78, c: 295, count: 8 },
  { date: '2026-05-21', dow: 'Ср', kcal: 1820, p: 95,  f: 62, c: 210, count: 5 },
  { date: '2026-05-22', dow: 'Чт', kcal: 2410, p: 124, f: 88, c: 280, count: 9 },
  { date: '2026-05-23', dow: 'Пт', kcal: 2050, p: 110, f: 65, c: 250, count: 7 },
  { date: '2026-05-24', dow: 'Сб', kcal: 2680, p: 95,  f: 102, c: 310, count: 10, over: true },
  { date: '2026-05-25', dow: 'Вс', kcal: 1442, p: 103, f: 45, c: 152, count: 8, today: true },
];

// Format helpers
function fmtNum(n, digits = 0) {
  if (n == null || isNaN(n)) return '—';
  return n.toFixed(digits).replace(/\.0$/, '');
}
function ddmmm(dateStr) {
  const d = new Date(dateStr);
  const m = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'][d.getMonth()];
  return `${d.getDate()} ${m}`;
}
function autoMeal(hour) {
  if (hour >= 5 && hour < 11) return 'breakfast';
  if (hour >= 11 && hour < 16) return 'lunch';
  if (hour >= 16 && hour < 22) return 'dinner';
  return 'snack';
}

// Sum
function sumEntries(entries) {
  return entries.reduce((a, e) => ({
    kcal: a.kcal + e.kcal,
    p: a.p + e.p,
    f: a.f + e.f,
    c: a.c + e.c,
  }), { kcal: 0, p: 0, f: 0, c: 0 });
}

Object.assign(window, {
  GOAL, MEAL_META, MEAL_ORDER,
  TODAY_ENTRIES, HISTORY,
  fmtNum, ddmmm, autoMeal, sumEntries,
});
