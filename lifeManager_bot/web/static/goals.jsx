// goals.jsx — экран настройки дневных целей КБЖУ.
// Пустое поле = снять цель (на бэке станет NULL).

function GoalField({ label, hint, value, onChange, accent }) {
  return (
    <div className="goalfield">
      <div className="goalfield__main">
        <div className="goalfield__label" style={accent ? { color: accent } : null}>{label}</div>
        {hint && <div className="goalfield__hint">{hint}</div>}
      </div>
      <div className="goalfield__input">
        <input type="text" inputMode="decimal" placeholder="—"
          value={value == null ? '' : value}
          onChange={e => onChange(e.target.value)} />
      </div>
    </div>
  );
}

function GoalsScreen({ goals, onSave, saving }) {
  const init = goals || {};
  const [kcal, setKcal] = React.useState(init.kcal == null ? '' : init.kcal);
  const [protein, setProtein] = React.useState(init.protein == null ? '' : init.protein);
  const [fat, setFat] = React.useState(init.fat == null ? '' : init.fat);
  const [carbs, setCarbs] = React.useState(init.carbs == null ? '' : init.carbs);

  function applyPreset() {
    setKcal(2000); setProtein(100); setFat(67); setCarbs(250);
  }

  return (
    <React.Fragment>
      <div className="card-title" style={{ paddingTop: 8 }}>Дневные цели</div>

      <div className="goals-card">
        <GoalField label="Калории" hint="ккал в день" value={kcal} onChange={setKcal} accent="var(--kcal)" />
        <GoalField label="Белки" hint="граммы" value={protein} onChange={setProtein} accent="var(--protein)" />
        <GoalField label="Жиры" hint="граммы" value={fat} onChange={setFat} accent="var(--fat)" />
        <GoalField label="Углеводы" hint="граммы" value={carbs} onChange={setCarbs} accent="var(--carbs)" />
      </div>

      <div className="goals-hint">
        Пустое поле — цель не задана: прогресс-бар покажет только съеденное.
        <button className="goals-preset" onClick={applyPreset}>Подставить пример (2000/100/67/250)</button>
      </div>

      <div className="mainbutton-wrap">
        <button className="mainbutton" disabled={saving}
          onClick={() => onSave({ kcal, protein, fat, carbs })}>
          {saving ? <span className="mainbutton__spin" /> : 'Сохранить цели'}
        </button>
      </div>
      <div style={{ height: 24 }} />
    </React.Fragment>
  );
}

Object.assign(window, { GoalsScreen, GoalField });
