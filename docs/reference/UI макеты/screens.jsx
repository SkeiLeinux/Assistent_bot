// screens.jsx — Today, History, Export, AddSheet, Confirm

// ─── Today ─────────────────────────────────────────────────────
function TodayScreen({ entries, goal, onOpenAdd, onEntryClick, date, isToday, dow, onPrevDay, onNextDay, empty }) {
  const totals = sumEntries(entries);
  return (
    <React.Fragment>
      <DaySwitcher date={date} dow={dow} isToday={isToday} onPrev={onPrevDay} onNext={onNextDay} />
      <DayTotalCard totals={totals} goal={goal} />

      {empty ? (
        <div className="empty">
          <div className="empty__art">🍽</div>
          <div className="empty__title">Записей пока нет</div>
          <div className="empty__sub">Нажмите «Добавить», чтобы записать<br/>первый приём пищи</div>
        </div>
      ) : (
        <React.Fragment>
          {MEAL_ORDER.map(meal => {
            const list = entries.filter(e => e.meal === meal);
            // hide totally-empty groups except show all four after a meal exists in others
            return <MealGroup key={meal} meal={meal} entries={list} onEntryClick={onEntryClick} />;
          })}
        </React.Fragment>
      )}
      <div style={{ height: 24 }} />
    </React.Fragment>
  );
}

// ─── History ──────────────────────────────────────────────────
function WeekChart({ days, goal }) {
  const max = Math.max(goal, ...days.map(d => d.kcal)) * 1.05;
  const goalPctFromBottom = (goal / max) * 100;
  return (
    <div className="weekchart">
      <div className="weekchart__title">
        <span>Неделя</span>
        <span>цель <b>{fmtNum(goal)}</b> ккал</span>
      </div>
      <div className="weekchart__bars">
        {days.map((d, i) => {
          const h = Math.max((d.kcal / max) * 100, 3);
          return (
            <div key={i} className="weekday">
              <div className="weekday__bar-wrap">
                <div
                  className={'weekday__bar ' + (d.today ? 'weekday__bar--active' : (d.over ? 'weekday__bar--over' : ''))}
                  style={{ height: h + '%' }}
                />
                <div className="weekday__goalline" style={{ bottom: goalPctFromBottom + '%' }} />
              </div>
              <div className={'weekday__lbl ' + (d.today ? 'weekday__lbl--today' : '')}>{d.dow}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HistoryScreen({ days, goal, onDayClick, empty }) {
  if (empty) {
    return (
      <div className="empty">
        <div className="empty__art">📊</div>
        <div className="empty__title">История пуста</div>
        <div className="empty__sub">Начните записывать приёмы пищи —<br/>здесь появится статистика</div>
      </div>
    );
  }
  return (
    <React.Fragment>
      <WeekChart days={days} goal={goal} />
      <div className="card-title">Дни</div>
      <div style={{ margin: '0 12px', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
        {[...days].reverse().map((d, i) => {
          const dt = new Date(d.date);
          return (
            <div key={d.date} className="dayrow" onClick={() => onDayClick && onDayClick(d)}>
              <div className="dayrow__dot">
                <b>{dt.getDate()}</b>
                <small>{['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'][dt.getMonth()]}</small>
              </div>
              <div>
                <div className="dayrow__title">{d.today ? 'Сегодня' : (d.dow + ', ' + ddmmm(d.date))}</div>
                <div className="dayrow__sub">
                  <span>{d.count} {d.count === 1 ? 'запись' : (d.count < 5 ? 'записи' : 'записей')}</span>
                  <span>·</span>
                  <span style={{ color: 'var(--protein)' }}>Б{fmtNum(d.p)}</span>
                  <span style={{ color: 'var(--fat)' }}>Ж{fmtNum(d.f)}</span>
                  <span style={{ color: 'var(--carbs)' }}>У{fmtNum(d.c)}</span>
                </div>
              </div>
              <div className="dayrow__kcal">
                <b style={{ color: d.over ? 'var(--danger)' : 'var(--kcal)' }}>{fmtNum(d.kcal)}</b>
                <small>{Math.round(d.kcal / goal * 100)}% цели</small>
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ height: 24 }} />
    </React.Fragment>
  );
}

// ─── Export ────────────────────────────────────────────────────
const EXAMPLE_EXPORT = `# Дневник питания
**Период:** 19–25 мая 2026 (7 дней)
**Итого:** 14 622 ккал · Б 747 г · Ж 498 г · У 1 717 г
**В среднем за день:** 2 089 ккал · Б 107 г · Ж 71 г · У 245 г

---

## 19 мая (Пн) — 1 980 ккал
- 08:30  Овсянка на молоке, 250 г — 285 ккал (Б 11 / Ж 7 / У 44)
- 09:00  Кофе с молоком, 200 г — 80 ккал (Б 4 / Ж 4 / У 7)
- 13:15  Куриный суп, 350 г — 245 ккал (Б 22 / Ж 8 / У 18)
- 14:00  Хлеб, 60 г — 160 ккал (Б 5 / Ж 1 / У 32)
- 19:30  Стейк говяжий, 200 г — 520 ккал (Б 48 / Ж 32 / У 0)
- 19:35  Овощи гриль, 150 г — 110 ккал (Б 3 / Ж 6 / У 12)
*Итог:* 1 980 ккал · Б 118 · Ж 58 · У 220

## 20 мая (Вт) — 2 240 ккал
- 08:00  Омлет 3 яйца, 180 г — 280 ккал (Б 21 / Ж 22 / У 2)
…

---

## Шаблон промпта для анализа

Ты — нутрициолог. Проанализируй этот дневник:
1. Соответствие КБЖУ моим целям (2 200 / 110 / 70 / 280)
2. Что улучшить в рационе
3. На какие дефициты обратить внимание`;

function ExportScreen({ period, setPeriod, onGenerate, generating, output, onCopy }) {
  return (
    <React.Fragment>
      <div className="card-title">Период</div>
      <div className="export-period">
        <button
          className={'period-card ' + (period === '7d' ? 'period-card--active' : '')}
          onClick={() => setPeriod('7d')}
        >
          <span className="period-card__big">7 дней</span>
          <span className="period-card__sub">19–25 мая</span>
        </button>
        <button
          className={'period-card ' + (period === '30d' ? 'period-card--active' : '')}
          onClick={() => setPeriod('30d')}
        >
          <span className="period-card__big">Месяц</span>
          <span className="period-card__sub">26 апр – 25 мая</span>
        </button>
        <button
          className={'period-card period-card--custom ' + (period === 'custom' ? 'period-card--active' : '')}
          onClick={() => setPeriod('custom')}
        >
          <IconCalendar size={20} stroke="var(--tg-text-2)" />
          <div>
            <div className="period-card__big">Свой период</div>
            <div className="period-card__sub">укажите даты вручную</div>
          </div>
        </button>
      </div>

      {!output && (
        <div className="mainbutton-wrap" style={{ paddingTop: 0 }}>
          <button className="mainbutton" onClick={onGenerate} disabled={generating}>
            {generating ? (
              <React.Fragment>
                <span className="mainbutton__spin" /> Формируем…
              </React.Fragment>
            ) : 'Сформировать дамп'}
          </button>
        </div>
      )}

      {output && (
        <React.Fragment>
          <div className="card-title">Markdown-дамп</div>
          <div className="export-out">
            <button className="export-out__copy" onClick={onCopy}>
              <span style={{ display:'inline-flex', alignItems:'center', gap:4 }}>
                <IconCopy size={12} /> Копировать
              </span>
            </button>
            {output}
          </div>
          <div className="export-actions">
            <button className="mainbutton mainbutton--ghost" style={{ fontSize: 14, height: 44 }}>
              <IconFile size={16} /> Файлом в чат
            </button>
            <button className="mainbutton mainbutton--ghost" style={{ fontSize: 14, height: 44 }}>
              <IconShare size={16} /> Поделиться
            </button>
          </div>
        </React.Fragment>
      )}
      <div style={{ height: 24 }} />
    </React.Fragment>
  );
}

// ─── Add sheet ─────────────────────────────────────────────────
function AddSheet({ open, onClose, onAnalyze, mode, setMode, text, setText, photo, setPhoto, caption, setCaption, analyzing, error }) {
  return (
    <React.Fragment>
      <div className={'sheet-backdrop ' + (open ? 'sheet-backdrop--open' : '')} onClick={onClose} />
      <div className={'sheet ' + (open ? 'sheet--open' : '')}>
        <div className="sheet__grabber" />
        <div className="sheet__head">
          <div style={{ width: 50 }} />
          <div className="sheet__title">Добавить</div>
          <button className="sheet__close" onClick={onClose}>Отмена</button>
        </div>

        <div className="modeswitch">
          <button
            className={'modeswitch__btn ' + (mode === 'text' ? 'modeswitch__btn--active' : '')}
            onClick={() => setMode('text')}
          >
            <IconText size={16} /> Текст
          </button>
          <button
            className={'modeswitch__btn ' + (mode === 'photo' ? 'modeswitch__btn--active' : '')}
            onClick={() => setMode('photo')}
          >
            <IconCamera size={16} /> Фото
          </button>
        </div>

        {mode === 'text' ? (
          <div className="field">
            <div className="field__label">Что вы съели</div>
            <textarea
              className="textarea"
              placeholder="напр. гречка 200, куриная грудка 150, салат 100"
              value={text}
              onChange={e => setText(e.target.value)}
            />
            <div className="dropzone__hint" style={{ marginTop: 6, padding: '0 4px' }}>
              Перечислите продукты с весом — модель посчитает КБЖУ.
            </div>
          </div>
        ) : (
          <React.Fragment>
            <div
              className={'dropzone ' + (photo ? 'dropzone--filled' : '')}
              onClick={() => !photo && setPhoto('demo')}
            >
              {photo ? (
                <React.Fragment>
                  <div style={{
                    width: '100%', height: '100%',
                    background: 'linear-gradient(135deg, #5a4a2e 0%, #8a6b3a 50%, #c9a06a 100%)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 48,
                  }}>🍝</div>
                  <button className="dropzone__retake" onClick={(e) => { e.stopPropagation(); setPhoto(null); }}>
                    Заменить
                  </button>
                </React.Fragment>
              ) : (
                <React.Fragment>
                  <IconCamera size={32} stroke="var(--tg-text-3)" />
                  <div>Сделать фото или выбрать из галереи</div>
                  <div className="dropzone__hint">Этикетка → точно, домашнее блюдо → грубо</div>
                </React.Fragment>
              )}
            </div>
            <div className="field">
              <div className="field__label">Подпись (необязательно)</div>
              <input
                className="input"
                placeholder="напр. порция ~250 г"
                value={caption}
                onChange={e => setCaption(e.target.value)}
              />
            </div>
          </React.Fragment>
        )}

        {error && (
          <div className="error" style={{ marginTop: 0 }}>
            <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
            <div className="error__body">
              <b>Не удалось распознать.</b> {error}
            </div>
          </div>
        )}

        {analyzing ? (
          <div className="loading-state">
            <div className="loading-state__spinner" />
            <div className="loading-state__title">Модель считает КБЖУ…</div>
            <div className="loading-state__sub">обычно занимает 3–7 секунд</div>
          </div>
        ) : (
          <div className="mainbutton-wrap">
            <button
              className="mainbutton mainbutton--warm"
              onClick={onAnalyze}
              disabled={mode === 'text' ? !text.trim() : !photo}
            >
              Посчитать
            </button>
          </div>
        )}
      </div>
    </React.Fragment>
  );
}

// ─── Confirm screen ────────────────────────────────────────────
function ConfirmScreen({ items, setItems, mealType, setMealType, onSave, onCancel, source, saving }) {
  const sum = items.reduce((a, e) => ({
    kcal: a.kcal + (+e.kcal || 0),
    p: a.p + (+e.p || 0),
    f: a.f + (+e.f || 0),
    c: a.c + (+e.c || 0),
  }), { kcal: 0, p: 0, f: 0, c: 0 });

  function update(id, field, value) {
    setItems(items.map(it => it.id === id ? { ...it, [field]: value } : it));
  }
  function remove(id) {
    setItems(items.filter(it => it.id !== id));
  }
  function add() {
    const nid = Math.max(0, ...items.map(i => i.id)) + 1;
    setItems([...items, { id: nid, name: '', w: '', kcal: '', p: '', f: '', c: '' }]);
  }

  return (
    <React.Fragment>
      <div className="card-title" style={{ paddingTop: 8 }}>
        Распознано · {items.length} {items.length === 1 ? 'позиция' : (items.length < 5 ? 'позиции' : 'позиций')}
      </div>

      {source === 'photo' && (
        <div className="warn">
          <span className="warn__icon"><IconWarn size={16} stroke="#F5BE6A" /></span>
          <div>Для домашних блюд оценка КБЖУ грубая. Проверьте вес и калории, при необходимости поправьте.</div>
        </div>
      )}

      {items.map(it => (
        <div key={it.id} className="confirm-item">
          <div className="confirm-item__head">
            <input
              className="confirm-item__name"
              value={it.name}
              onChange={e => update(it.id, 'name', e.target.value)}
              placeholder="Название"
            />
            <button className="confirm-item__remove" onClick={() => remove(it.id)}>
              <IconTrash size={16} />
            </button>
          </div>
          <div className="confirm-item__grid">
            <NumField label="вес, г" value={it.w} onChange={v => update(it.id, 'w', v)} />
            <NumField label="ккал" value={it.kcal} onChange={v => update(it.id, 'kcal', v)} kcal />
            <NumField label="Б" value={it.p} onChange={v => update(it.id, 'p', v)} />
            <NumField label="Ж" value={it.f} onChange={v => update(it.id, 'f', v)} />
          </div>
          <div className="confirm-item__grid" style={{ marginTop: 6, gridTemplateColumns: '1fr 1fr 1fr 1fr' }}>
            <div style={{ gridColumn: 'span 3' }} />
            <NumField label="У" value={it.c} onChange={v => update(it.id, 'c', v)} />
          </div>
        </div>
      ))}

      <button
        onClick={add}
        style={{
          margin: '4px 12px 14px', width: 'calc(100% - 24px)',
          padding: '12px', background: 'transparent',
          border: '1.5px dashed var(--tg-separator-strong)',
          borderRadius: 14, color: 'var(--tg-accent)',
          fontSize: 14, fontWeight: 500,
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
        }}
      >
        <IconPlus size={16} /> Добавить позицию
      </button>

      <div className="card-title">Приём пищи</div>
      <div className="pills">
        {MEAL_ORDER.map(m => (
          <button
            key={m}
            className={'pill ' + (mealType === m ? 'pill--active' : '')}
            onClick={() => setMealType(m)}
          >
            <span>{MEAL_META[m].icon}</span>
            <span>{MEAL_META[m].label}</span>
          </button>
        ))}
      </div>

      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 12, color: 'var(--tg-text-2)', textTransform: 'uppercase', letterSpacing: 0.4 }}>Итого по записи</div>
          <div style={{ fontSize: 12, color: 'var(--tg-text-2)', marginTop: 4 }}>
            <span style={{ color: 'var(--protein)' }}>Б {fmtNum(sum.p)}</span> · 
            <span style={{ color: 'var(--fat)' }}> Ж {fmtNum(sum.f)}</span> · 
            <span style={{ color: 'var(--carbs)' }}> У {fmtNum(sum.c)}</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--kcal)', letterSpacing: -0.5 }}>{fmtNum(sum.kcal)}</div>
          <div style={{ fontSize: 11, color: 'var(--tg-text-3)', textTransform: 'uppercase' }}>ккал</div>
        </div>
      </div>

      <div style={{ height: 80 }} />

      <div className="fab-wrap" style={{ display: 'flex', gap: 8 }}>
        <button className="fab" style={{ background: 'var(--tg-surface-hi)', boxShadow: 'none', flex: '0 0 35%' }} onClick={onCancel}>
          Отмена
        </button>
        <button className="fab" style={{ flex: 1 }} onClick={onSave} disabled={saving || items.length === 0}>
          {saving ? <span className="mainbutton__spin" /> : <React.Fragment><IconCheck size={18} /> Сохранить</React.Fragment>}
        </button>
      </div>
    </React.Fragment>
  );
}

function NumField({ label, value, onChange, kcal }) {
  return (
    <div className={'numfield ' + (kcal ? 'numfield--kcal' : '')}>
      <div className="numfield__lbl">{label}</div>
      <input
        type="text"
        inputMode="decimal"
        value={value}
        onChange={e => onChange(e.target.value)}
      />
    </div>
  );
}

// ─── Global error state (LLM down etc) ─────────────────────────
function ErrorBanner({ kind, onRetry }) {
  const map = {
    no_llm:    { t: 'Модель недоступна',       s: 'Сервис распознавания временно не отвечает. Попробуйте позже.' },
    rate:      { t: 'Превышен лимит запросов', s: 'Слишком часто. Подождите минуту и попробуйте снова.' },
    no_key:    { t: 'Не настроен API-ключ',    s: 'Свяжитесь с владельцем бота — в конфиге нет ключа Groq.' },
    network:   { t: 'Нет соединения',          s: 'Проверьте интернет и попробуйте ещё раз.' },
  };
  const m = map[kind] || map.network;
  return (
    <div className="error" style={{ margin: '12px' }}>
      <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
      <div className="error__body" style={{ flex: 1 }}>
        <b>{m.t}</b>
        <div style={{ marginTop: 2 }}>{m.s}</div>
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              marginTop: 8, padding: '6px 12px', background: 'rgba(255,255,255,0.08)',
              border: 'none', borderRadius: 8, color: 'var(--tg-text)',
              fontSize: 12, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <IconRetry size={14} /> Повторить
          </button>
        )}
      </div>
    </div>
  );
}

Object.assign(window, {
  TodayScreen, HistoryScreen, ExportScreen, AddSheet, ConfirmScreen, ErrorBanner,
  EXAMPLE_EXPORT,
});
