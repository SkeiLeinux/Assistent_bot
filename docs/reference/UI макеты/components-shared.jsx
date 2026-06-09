// components-shared.jsx — small reused bits

function KcalRing({ value, goal, size = 92, stroke = 8 }) {
  const r = (size - stroke) / 2;
  const cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;
  const pct = Math.min(value / goal, 1.2);
  const offset = C * (1 - Math.min(pct, 1));
  const over = value > goal;
  const color = over ? 'var(--danger)' : 'var(--kcal)';
  return (
    <div className="kcal-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={cx} cy={cy} r={r} stroke="rgba(255,255,255,0.07)" strokeWidth={stroke} fill="none" />
        <circle
          cx={cx} cy={cy} r={r}
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          fill="none"
          strokeDasharray={C}
          strokeDashoffset={offset}
          style={{ transition: 'stroke-dashoffset 0.6s cubic-bezier(.2,.7,.2,1)' }}
        />
      </svg>
      <div className="kcal-ring__num">
        <div className="kcal-ring__val">{fmtNum(value)}</div>
        <div className="kcal-ring__sep">из</div>
        <div className="kcal-ring__goal">{fmtNum(goal)}</div>
      </div>
    </div>
  );
}

function MacroBar({ name, value, goal, color, unit = 'г' }) {
  const pct = Math.min((value / goal) * 100, 100);
  return (
    <div className="macro">
      <div className="macro__head">
        <span className="macro__name">{name}</span>
        <span className="macro__val">
          {fmtNum(value)}<small> / {fmtNum(goal)} {unit}</small>
        </span>
      </div>
      <div className="macro__bar">
        <span style={{ width: pct + '%', background: color }} />
      </div>
    </div>
  );
}

function DayTotalCard({ totals, goal }) {
  const remaining = Math.max(goal.kcal - totals.kcal, 0);
  const over = totals.kcal > goal.kcal;
  return (
    <div className="daytotal">
      <div className="daytotal__top">
        <KcalRing value={totals.kcal} goal={goal.kcal} />
        <div className="daytotal__right">
          <div className="daytotal__label">{over ? 'Превышение' : 'Осталось'}</div>
          <div className="daytotal__remaining" style={{ color: over ? 'var(--danger)' : 'var(--tg-text)' }}>
            {over ? '+' : ''}{fmtNum(over ? totals.kcal - goal.kcal : remaining)}
            <small>ккал</small>
          </div>
          <div className="daytotal__hint">
            Цель {fmtNum(goal.kcal)} ккал в день
          </div>
        </div>
      </div>
      <div className="macros">
        <MacroBar name="Белки"     value={totals.p} goal={goal.protein} color="var(--protein)" />
        <MacroBar name="Жиры"      value={totals.f} goal={goal.fat}     color="var(--fat)" />
        <MacroBar name="Углеводы"  value={totals.c} goal={goal.carbs}   color="var(--carbs)" />
      </div>
    </div>
  );
}

function MealGroup({ meal, entries, onEntryClick }) {
  const meta = MEAL_META[meal];
  const sum = sumEntries(entries);
  return (
    <div className="meal">
      <div className="meal__head">
        <div className="meal__title">
          <span className="meal__icon" style={{ background: meta.tint + '24' }}>{meta.icon}</span>
          <span>{meta.label}</span>
        </div>
        {entries.length > 0 && (
          <div className="meal__sum">
            <b>{fmtNum(sum.kcal)}</b> ккал · Б{fmtNum(sum.p)} · Ж{fmtNum(sum.f)} · У{fmtNum(sum.c)}
          </div>
        )}
      </div>
      <div className="meal__list">
        {entries.length === 0 ? (
          <div className="meal__empty">— ничего не добавлено —</div>
        ) : entries.map(e => (
          <div key={e.id} className="entry" onClick={() => onEntryClick && onEntryClick(e)}>
            <div className="entry__main">
              <div className="entry__name">{e.name}</div>
              <div className="entry__meta">
                <span>{e.time}</span>
                <span>·</span>
                <span>{e.w} г</span>
                <span>·</span>
                <span style={{ color: 'var(--protein)' }}>Б{fmtNum(e.p)}</span>
                <span style={{ color: 'var(--fat)' }}>Ж{fmtNum(e.f)}</span>
                <span style={{ color: 'var(--carbs)' }}>У{fmtNum(e.c)}</span>
              </div>
            </div>
            <div className="entry__kcal">
              <b>{fmtNum(e.kcal)}</b>
              <small>ккал</small>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DaySwitcher({ date, onPrev, onNext, isToday, dow }) {
  return (
    <div className="dayswitch">
      <button className="dayswitch__arrow" onClick={onPrev}>
        <IconChevL size={18} />
      </button>
      <div className="dayswitch__label">
        <div className="dayswitch__date">{isToday ? 'Сегодня' : ddmmm(date)}</div>
        <div className="dayswitch__sub">{dow} · {ddmmm(date)}</div>
      </div>
      <button className="dayswitch__arrow" onClick={onNext} disabled={isToday}>
        <IconChevR size={18} />
      </button>
    </div>
  );
}

function TgHeader({ title, sub, onBack, right }) {
  return (
    <div className="tg-header">
      {onBack ? (
        <button className="tg-header__back" onClick={onBack}>
          <IconBack size={20} />
        </button>
      ) : <div style={{ width: 32 }} />}
      <div className="tg-header__center">
        <div className="tg-header__title">{title}</div>
        {sub && <div className="tg-header__sub">{sub}</div>}
      </div>
      <div style={{ width: 32, display: 'flex', justifyContent: 'flex-end' }}>{right || null}</div>
    </div>
  );
}

function Toast({ show, children }) {
  return <div className={'toast ' + (show ? 'toast--show' : '')}>{children}</div>;
}

Object.assign(window, {
  KcalRing, MacroBar, DayTotalCard, MealGroup, DaySwitcher, TgHeader, Toast,
});
