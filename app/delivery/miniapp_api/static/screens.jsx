// screens.jsx — Today, History, Export, AddSheet, Confirm, EditEntry.
// Данные приходят пропсами из app.jsx (реальный API), компоненты «тупые».

// ─── CTA «задайте цели» ────────────────────────────────────────
function GoalsCta({ onOpen }) {
  return (
    <div className="goals-cta" onClick={onOpen}>
      <div className="goals-cta__title">Задайте дневные цели</div>
      <div className="goals-cta__sub">Калории и Б/Ж/У — чтобы видеть прогресс по дню</div>
      <div className="goals-cta__btn">Настроить цели</div>
    </div>
  );
}

// ─── Today ─────────────────────────────────────────────────────
function TodayScreen({ day, loading, onOpenAdd, onEntryClick, onPrevDay, onNextDay, onOpenGoals }) {
  if (loading || !day) return <Loader label="Загружаем день…" />;
  const entries = day.entries || [];
  return (
    <React.Fragment>
      <DaySwitcher date={day.date} dow={dowOf(day.date)} isToday={day.is_today}
        onPrev={onPrevDay} onNext={onNextDay} />

      {!hasAnyGoal(day.goals) && <GoalsCta onOpen={onOpenGoals} />}

      <DayTotalCard totals={day.totals} goals={day.goals} />

      {entries.length === 0 ? (
        <div className="empty">
          <div className="empty__art">🍽</div>
          <div className="empty__title">Записей пока нет</div>
          <div className="empty__sub">Нажмите «Добавить», чтобы записать<br/>первый приём пищи</div>
        </div>
      ) : (
        MEAL_ORDER.map(meal => (
          <MealGroup key={meal} meal={meal}
            entries={entries.filter(e => e.meal === meal)}
            onEntryClick={onEntryClick} />
        ))
      )}
      {/* записи без meal_type / нераспознанный приём */}
      {entries.some(e => !MEAL_ORDER.includes(e.meal)) && (
        <MealGroup meal="other" entries={entries.filter(e => !MEAL_ORDER.includes(e.meal))}
          onEntryClick={onEntryClick} />
      )}
      <div style={{ height: 24 }} />
    </React.Fragment>
  );
}

// ─── History ──────────────────────────────────────────────────
function WeekChart({ days, goal }) {
  const hasGoal = goal != null && goal > 0;
  const max = Math.max(hasGoal ? goal : 0, ...days.map(d => d.kcal), 1) * 1.05;
  const goalPctFromBottom = hasGoal ? (goal / max) * 100 : 0;
  return (
    <div className="weekchart">
      <div className="weekchart__title">
        <span>Неделя</span>
        {hasGoal && <span>цель <b>{fmtNum(goal)}</b> ккал</span>}
      </div>
      <div className="weekchart__bars">
        {days.map((d, i) => {
          const h = Math.max((d.kcal / max) * 100, d.kcal > 0 ? 3 : 0);
          return (
            <div key={i} className="weekday">
              <div className="weekday__bar-wrap">
                <div
                  className={'weekday__bar ' + (d.today ? 'weekday__bar--active' : (d.over ? 'weekday__bar--over' : ''))}
                  style={{ height: h + '%' }}
                />
                {hasGoal && <div className="weekday__goalline" style={{ bottom: goalPctFromBottom + '%' }} />}
              </div>
              <div className={'weekday__lbl ' + (d.today ? 'weekday__lbl--today' : '')}>{d.dow}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HistoryScreen({ days, goalKcal, loading, onDayClick }) {
  if (loading || !days) return <Loader label="Загружаем историю…" />;
  const hasData = days.some(d => d.count > 0);
  if (!hasData) {
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
      <WeekChart days={days} goal={goalKcal} />
      <div className="card-title">Дни</div>
      <div style={{ margin: '0 12px', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
        {[...days].reverse().filter(d => d.count > 0).map((d) => {
          const dt = new Date(d.date);
          return (
            <div key={d.date} className="dayrow" onClick={() => onDayClick && onDayClick(d)}>
              <div className="dayrow__dot">
                <b>{dt.getDate()}</b>
                <small>{MONTHS[dt.getMonth()]}</small>
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
                {goalKcal ? <small>{Math.round(d.kcal / goalKcal * 100)}% цели</small> : null}
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
function ExportScreen({ period, setPeriod, customFrom, setCustomFrom, customTo, setCustomTo,
                        onGenerate, generating, output, onCopy, error }) {
  return (
    <React.Fragment>
      <div className="card-title">Период</div>
      <div className="export-period">
        <button className={'period-card ' + (period === '7d' ? 'period-card--active' : '')}
          onClick={() => setPeriod('7d')}>
          <span className="period-card__big">7 дней</span>
          <span className="period-card__sub">последняя неделя</span>
        </button>
        <button className={'period-card ' + (period === '30d' ? 'period-card--active' : '')}
          onClick={() => setPeriod('30d')}>
          <span className="period-card__big">Месяц</span>
          <span className="period-card__sub">последние 30 дней</span>
        </button>
        <button className={'period-card period-card--custom ' + (period === 'custom' ? 'period-card--active' : '')}
          onClick={() => setPeriod('custom')}>
          <IconCalendar size={20} stroke="var(--tg-text-2)" />
          <div>
            <div className="period-card__big">Свой период</div>
            <div className="period-card__sub">укажите даты вручную</div>
          </div>
        </button>
      </div>

      {period === 'custom' && (
        <div className="export-custom">
          <label>с <input type="date" value={customFrom} onChange={e => setCustomFrom(e.target.value)} /></label>
          <label>по <input type="date" value={customTo} onChange={e => setCustomTo(e.target.value)} /></label>
        </div>
      )}

      {error && (
        <div className="error" style={{ margin: '8px 12px' }}>
          <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
          <div className="error__body">{error}</div>
        </div>
      )}

      <div className="mainbutton-wrap" style={{ paddingTop: 0 }}>
        <button className="mainbutton" onClick={onGenerate} disabled={generating}>
          {generating ? (<React.Fragment><span className="mainbutton__spin" /> Формируем…</React.Fragment>) : 'Сформировать дамп'}
        </button>
      </div>

      {output && (
        <React.Fragment>
          <div className="card-title">Markdown-дамп</div>
          <div className="export-out">
            <button className="export-out__copy" onClick={() => onCopy(output)}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <IconCopy size={12} /> Копировать
              </span>
            </button>
            {output}
          </div>
        </React.Fragment>
      )}
      <div style={{ height: 24 }} />
    </React.Fragment>
  );
}

// ─── Create dish (text + photos combined) ────────────────────────
function CreateDishView({
  dishName, setDishName, dishWeight, setDishWeight,
  dishText, setDishText,
  dishPhotos, onAddPhoto, onRemovePhoto,
  dishAnalyzing, dishResult, setDishResult,
  dishSaving, dishError, onAnalyze, onSave, onBack,
}) {
  const fileRef = React.useRef(null);
  const [reading, setReading] = React.useState(false);

  async function onFile(e) {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!f) return;
    setReading(true);
    try { onAddPhoto(await fileToCompressedDataUrl(f)); }
    catch (_) {}
    finally { setReading(false); }
  }

  const baseReady = dishName.trim() && parseFloat(dishWeight) > 0;
  const canAnalyze = baseReady && (dishText.trim().length > 0 || dishPhotos.length > 0);
  const canSave = dishResult && baseReady;

  function updateResult(field, val) {
    setDishResult(r => ({ ...r, [field]: val }));
  }

  return (
    <React.Fragment>
      <button className="db-back" onClick={onBack}>← Назад к списку</button>

      <div className="field">
        <div className="field__label">Название блюда</div>
        <input className="input" placeholder="напр. Яичница с ветчиной"
          value={dishName} onChange={e => setDishName(e.target.value)} />
      </div>

      <div className="field">
        <div className="field__label">Стандартная порция, г</div>
        <input className="input" type="text" inputMode="decimal"
          placeholder="напр. 210"
          value={dishWeight} onChange={e => setDishWeight(e.target.value)} />
        <div className="dropzone__hint" style={{ marginTop: 4, padding: '0 4px' }}>
          Этот вес будет подставляться по умолчанию при добавлении
        </div>
      </div>

      <div className="field">
        <div className="field__label">
          Состав <span style={{ color: 'var(--tg-text-3)', fontWeight: 400 }}>(необязательно)</span>
        </div>
        <textarea className="textarea" style={{ minHeight: 80 }}
          placeholder="напр. 60г ветчины, 2.5 яйца, 50г яичного белка, 50г творожного сыра"
          value={dishText} onChange={e => setDishText(e.target.value)} />
      </div>

      <div className="field__label" style={{ padding: '0 16px 6px' }}>
        Фото этикеток <span style={{ color: 'var(--tg-text-3)', fontWeight: 400 }}>(необязательно)</span>
      </div>
      <div className="dish-photos">
        {dishPhotos.map((url, i) => (
          <div key={i} className="dish-photo">
            <img src={url} alt="" className="dish-photo__img" />
            <button className="dish-photo__remove" onClick={() => onRemovePhoto(i)}>×</button>
          </div>
        ))}
        {dishPhotos.length < 5 && (
          <div className={'dish-photo dish-photo--add ' + (reading ? 'dish-photo--loading' : '')}
            onClick={() => !reading && fileRef.current && fileRef.current.click()}>
            {reading
              ? <div className="loading-state__spinner" style={{ width: 20, height: 20, borderWidth: 2 }} />
              : <span style={{ fontSize: 24, color: 'var(--tg-text-3)' }}>+</span>}
          </div>
        )}
      </div>
      <div className="dropzone__hint" style={{ padding: '4px 16px 10px' }}>
        С фото этикеток точнее — модель читает реальные значения с упаковки
      </div>
      <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={onFile} />

      {dishError && (
        <div className="error" style={{ marginTop: 4 }}>
          <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
          <div className="error__body">{dishError}</div>
        </div>
      )}

      {dishResult && (
        <React.Fragment>
          <div className="card-title" style={{ paddingTop: 4 }}>
            КБЖУ на {dishWeight} г — уточните если нужно
          </div>
          <div className="confirm-item" style={{ marginTop: 0 }}>
            <div className="confirm-item__grid">
              <NumField label="ккал" value={dishResult.kcal} onChange={v => updateResult('kcal', v)} kcal />
              <NumField label="Б" value={dishResult.protein} onChange={v => updateResult('protein', v)} />
              <NumField label="Ж" value={dishResult.fat} onChange={v => updateResult('fat', v)} />
              <NumField label="У" value={dishResult.carbs} onChange={v => updateResult('carbs', v)} />
            </div>
          </div>
        </React.Fragment>
      )}

      {dishAnalyzing ? (
        <div className="loading-state" style={{ paddingTop: 20 }}>
          <div className="loading-state__spinner" />
          <div className="loading-state__title">Считаем КБЖУ…</div>
          <div className="loading-state__sub">обычно 3–10 секунд</div>
        </div>
      ) : dishResult ? (
        <div className="mainbutton-wrap">
          <button className="mainbutton" onClick={onSave} disabled={dishSaving || !canSave}>
            {dishSaving ? <span className="mainbutton__spin" /> : '💾 Сохранить в базу'}
          </button>
        </div>
      ) : (
        <div className="mainbutton-wrap">
          <button className="mainbutton mainbutton--warm" onClick={onAnalyze} disabled={!canAnalyze}>
            Проанализировать
          </button>
        </div>
      )}
    </React.Fragment>
  );
}

// ─── Food picker (mode = 'db' внутри AddSheet) ────────────────────
function FoodPickerMode({
  foodResults, foodLoading, pickedFood, pickWeight, setPickWeight,
  pickMeal, setPickMeal, pickSaving, onSearch, onPickFood, onPickBack, onPickConfirm,
  // create dish props
  createDishOpen, dishName, setDishName, dishWeight, setDishWeight,
  dishText, setDishText,
  dishPhotos, onAddDishPhoto, onRemoveDishPhoto,
  dishAnalyzing, dishResult, setDishResult, dishSaving, dishError,
  onOpenCreateDish, onDishAnalyze, onDishSave,
}) {
  const [q, setQ] = React.useState('');
  const searchRef = React.useRef(null);

  function handleQ(val) {
    setQ(val);
    onSearch(val);
  }

  // Вычисляем КБЖУ для текущего веса (масштабирование)
  function scaled(food, w) {
    const f = (parseFloat(w) || 0) / 100;
    return {
      kcal: Math.round(food.kcal_per_100g * f),
      p: Math.round(food.protein_per_100g * f * 10) / 10,
      f: Math.round(food.fat_per_100g * f * 10) / 10,
      c: Math.round(food.carbs_per_100g * f * 10) / 10,
    };
  }

  if (createDishOpen) {
    return (
      <CreateDishView
        dishName={dishName} setDishName={setDishName}
        dishWeight={dishWeight} setDishWeight={setDishWeight}
        dishText={dishText} setDishText={setDishText}
        dishPhotos={dishPhotos} onAddPhoto={onAddDishPhoto} onRemovePhoto={onRemoveDishPhoto}
        dishAnalyzing={dishAnalyzing} dishResult={dishResult} setDishResult={setDishResult}
        dishSaving={dishSaving} dishError={dishError}
        onAnalyze={onDishAnalyze} onSave={onDishSave}
        onBack={() => onOpenCreateDish(false)}
      />
    );
  }

  if (pickedFood) {
    const s = scaled(pickedFood, pickWeight);
    const wNum = parseFloat(pickWeight) || 0;
    return (
      <React.Fragment>
        <button className="db-back" onClick={onPickBack}>← Назад к списку</button>
        <div className="db-pick-card">
          <div className="db-pick-card__name">{pickedFood.name}</div>
          <div className="db-pick-card__std">стандартная порция: {pickedFood.weight_g} г</div>
          <div className="db-pick-weight">
            <label className="db-pick-weight__label">Ваша порция, г</label>
            <input
              className="input db-pick-weight__input"
              type="text"
              inputMode="decimal"
              value={pickWeight}
              onChange={e => setPickWeight(e.target.value)}
              autoFocus
            />
          </div>
          {wNum > 0 && (
            <div className="db-pick-preview">
              <span style={{ color: 'var(--kcal)', fontWeight: 700 }}>{s.kcal} ккал</span>
              <span style={{ color: 'var(--protein)' }}>Б {s.p}</span>
              <span style={{ color: 'var(--fat)' }}>Ж {s.f}</span>
              <span style={{ color: 'var(--carbs)' }}>У {s.c}</span>
            </div>
          )}
        </div>

        <div className="card-title">Приём пищи</div>
        <div className="pills">
          {MEAL_ORDER.map(m => (
            <button key={m} className={'pill ' + (pickMeal === m ? 'pill--active' : '')}
              onClick={() => setPickMeal(m)}>
              <span>{MEAL_META[m].icon}</span><span>{MEAL_META[m].label}</span>
            </button>
          ))}
        </div>

        <div className="mainbutton-wrap">
          <button className="mainbutton" onClick={onPickConfirm}
            disabled={pickSaving || wNum <= 0}>
            {pickSaving ? <span className="mainbutton__spin" /> : 'Добавить'}
          </button>
        </div>
      </React.Fragment>
    );
  }

  return (
    <React.Fragment>
      <div className="db-search">
        <input
          ref={searchRef}
          className="input db-search__input"
          placeholder="Поиск блюда…"
          value={q}
          onChange={e => handleQ(e.target.value)}
        />
      </div>

      {foodLoading ? (
        <div className="loading-state" style={{ paddingTop: 24 }}>
          <div className="loading-state__spinner" />
        </div>
      ) : !foodResults || foodResults.length === 0 ? (
        <div className="empty" style={{ paddingTop: 24 }}>
          <div className="empty__art">🍽</div>
          <div className="empty__title">{q ? 'Ничего не найдено' : 'База пуста'}</div>
          <div className="empty__sub">
            {q ? 'Попробуйте другой запрос.' : 'Сохраняйте блюда — они появятся здесь.'}
          </div>
        </div>
      ) : (
        <div className="db-list">
          {foodResults.map(f => {
            const kcal = Math.round(f.kcal_per_100g * f.weight_g / 100);
            return (
              <div key={f.id} className="db-item" onClick={() => onPickFood(f)}>
                <div className="db-item__name">{f.name}</div>
                <div className="db-item__meta">
                  {f.weight_g} г · <span style={{ color: 'var(--kcal)' }}>{kcal} ккал</span>
                  {f.by_me && <span className="db-item__mine">мои</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mainbutton-wrap" style={{ paddingTop: 12 }}>
        <button className="mainbutton mainbutton--ghost" onClick={() => onOpenCreateDish(true)}>
          ➕ Создать блюдо
        </button>
      </div>
    </React.Fragment>
  );
}

// ─── Add sheet ─────────────────────────────────────────────────
function AddSheet({ open, onClose, onAnalyze, mode, setMode, text, setText,
                    photo, setPhoto, caption, setCaption, analyzing, error,
                    foodResults, foodLoading, pickedFood, pickWeight, setPickWeight,
                    pickMeal, setPickMeal, pickSaving,
                    onFoodSearch, onPickFood, onPickBack, onPickConfirm,
                    createDishOpen, dishName, setDishName, dishWeight, setDishWeight,
                    dishText, setDishText,
                    dishPhotos, onAddDishPhoto, onRemoveDishPhoto,
                    dishAnalyzing, dishResult, setDishResult, dishSaving, dishError,
                    onOpenCreateDish, onDishAnalyze, onDishSave }) {
  const fileRef = React.useRef(null);
  const [reading, setReading] = React.useState(false);

  async function onFile(e) {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!f) return;
    setReading(true);
    try {
      const url = await fileToCompressedDataUrl(f);
      setPhoto(url);
    } catch (err) {
      setPhoto(null);
    } finally {
      setReading(false);
    }
  }

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
          <button className={'modeswitch__btn ' + (mode === 'text' ? 'modeswitch__btn--active' : '')}
            onClick={() => setMode('text')}>
            <IconText size={16} /> Текст
          </button>
          <button className={'modeswitch__btn ' + (mode === 'photo' ? 'modeswitch__btn--active' : '')}
            onClick={() => setMode('photo')}>
            <IconCamera size={16} /> Фото
          </button>
          <button className={'modeswitch__btn ' + (mode === 'db' ? 'modeswitch__btn--active' : '')}
            onClick={() => setMode('db')}>
            📖 База
          </button>
        </div>

        {mode === 'db' ? (
          <FoodPickerMode
            foodResults={foodResults} foodLoading={foodLoading}
            pickedFood={pickedFood} pickWeight={pickWeight} setPickWeight={setPickWeight}
            pickMeal={pickMeal} setPickMeal={setPickMeal} pickSaving={pickSaving}
            onSearch={onFoodSearch} onPickFood={onPickFood}
            onPickBack={onPickBack} onPickConfirm={onPickConfirm}
            createDishOpen={createDishOpen}
            dishName={dishName} setDishName={setDishName}
            dishWeight={dishWeight} setDishWeight={setDishWeight}
            dishText={dishText} setDishText={setDishText}
            dishPhotos={dishPhotos} onAddDishPhoto={onAddDishPhoto} onRemoveDishPhoto={onRemoveDishPhoto}
            dishAnalyzing={dishAnalyzing} dishResult={dishResult} setDishResult={setDishResult}
            dishSaving={dishSaving} dishError={dishError}
            onOpenCreateDish={onOpenCreateDish} onDishAnalyze={onDishAnalyze} onDishSave={onDishSave}
          />
        ) : mode === 'text' ? (
          <React.Fragment>
            <div className="field">
              <div className="field__label">Что вы съели</div>
              <textarea className="textarea"
                placeholder="напр. гречка 200, куриная грудка 150, салат 100"
                value={text} onChange={e => setText(e.target.value)} />
              <div className="dropzone__hint" style={{ marginTop: 6, padding: '0 4px' }}>
                Перечислите продукты с весом — модель посчитает КБЖУ.
              </div>
            </div>

            {error && (
              <div className="error" style={{ marginTop: 0 }}>
                <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
                <div className="error__body"><b>Не удалось распознать.</b> {error}</div>
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
                <button className="mainbutton mainbutton--warm" onClick={onAnalyze}
                  disabled={!text.trim()}>
                  Посчитать
                </button>
              </div>
            )}
          </React.Fragment>
        ) : (
          <React.Fragment>
            <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={onFile} />
            <div className={'dropzone ' + (photo ? 'dropzone--filled' : '')}
              onClick={() => { if (!photo && !reading) fileRef.current && fileRef.current.click(); }}>
              {reading ? (
                <div className="loading-state__spinner" />
              ) : photo ? (
                <React.Fragment>
                  <img className="dropzone__img" src={photo} alt="" />
                  <button className="dropzone__retake"
                    onClick={(e) => { e.stopPropagation(); fileRef.current && fileRef.current.click(); }}>
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
              <input className="input" placeholder="напр. порция ~250 г"
                value={caption} onChange={e => setCaption(e.target.value)} />
            </div>

            {error && (
              <div className="error" style={{ marginTop: 0 }}>
                <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
                <div className="error__body"><b>Не удалось распознать.</b> {error}</div>
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
                <button className="mainbutton mainbutton--warm" onClick={onAnalyze}
                  disabled={!photo}>
                  Посчитать
                </button>
              </div>
            )}
          </React.Fragment>
        )}
      </div>
    </React.Fragment>
  );
}

// ─── Confirm screen ────────────────────────────────────────────
function ConfirmScreen({ items, setItems, mealType, setMealType, onSave, onCancel, source, saving }) {
  const sum = sumEntries(items.map(e => ({ kcal: +e.kcal || 0, p: +e.p || 0, f: +e.f || 0, c: +e.c || 0 })));

  function update(id, field, value) {
    setItems(items.map(it => it.id === id ? { ...it, [field]: value } : it));
  }
  function remove(id) { setItems(items.filter(it => it.id !== id)); }
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

      {items.map(it => {
        const hasWeight = it.w && parseFloat(it.w) > 0;
        return (
          <div key={it.id} className="confirm-item">
            <div className="confirm-item__head">
              <input className="confirm-item__name" value={it.name}
                onChange={e => update(it.id, 'name', e.target.value)} placeholder="Название" />
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
            <div className="confirm-item__grid" style={{ marginTop: 6 }}>
              <div style={{ gridColumn: 'span 3' }} />
              <NumField label="У" value={it.c} onChange={v => update(it.id, 'c', v)} />
            </div>
            {hasWeight && (
              <button
                className={'save-toggle ' + (it.save ? 'save-toggle--on' : '')}
                onClick={() => update(it.id, 'save', !it.save)}
              >
                💾 {it.save ? 'Сохранено в базу' : 'Сохранить в базу'}
              </button>
            )}
          </div>
        );
      })}

      <button onClick={add} className="add-item-btn">
        <IconPlus size={16} /> Добавить позицию
      </button>

      <div className="card-title">Приём пищи</div>
      <div className="pills">
        {MEAL_ORDER.map(m => (
          <button key={m} className={'pill ' + (mealType === m ? 'pill--active' : '')}
            onClick={() => setMealType(m)}>
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
      <input type="text" inputMode="decimal" value={value == null ? '' : value}
        onChange={e => onChange(e.target.value)} />
    </div>
  );
}

// ─── Edit existing entry (bottom sheet) ────────────────────────
function EditEntrySheet({ open, entry, onClose, onSave, onDelete, saving }) {
  const [draft, setDraft] = React.useState(entry || {});
  React.useEffect(() => { setDraft(entry || {}); }, [entry && entry.id]);
  if (!entry) return null;

  function set(field, value) { setDraft(d => ({ ...d, [field]: value })); }

  return (
    <React.Fragment>
      <div className={'sheet-backdrop ' + (open ? 'sheet-backdrop--open' : '')} onClick={onClose} />
      <div className={'sheet ' + (open ? 'sheet--open' : '')}>
        <div className="sheet__grabber" />
        <div className="sheet__head">
          <div style={{ width: 50 }} />
          <div className="sheet__title">Запись</div>
          <button className="sheet__close" onClick={onClose}>Закрыть</button>
        </div>

        <div className="confirm-item" style={{ marginTop: 4 }}>
          <div className="confirm-item__head">
            <input className="confirm-item__name" value={draft.name || ''}
              onChange={e => set('name', e.target.value)} placeholder="Название" />
          </div>
          <div className="confirm-item__grid">
            <NumField label="вес, г" value={draft.w} onChange={v => set('w', v)} />
            <NumField label="ккал" value={draft.kcal} onChange={v => set('kcal', v)} kcal />
            <NumField label="Б" value={draft.p} onChange={v => set('p', v)} />
            <NumField label="Ж" value={draft.f} onChange={v => set('f', v)} />
          </div>
          <div className="confirm-item__grid" style={{ marginTop: 6 }}>
            <div style={{ gridColumn: 'span 3' }} />
            <NumField label="У" value={draft.c} onChange={v => set('c', v)} />
          </div>
        </div>

        <div className="card-title">Приём пищи</div>
        <div className="pills">
          {MEAL_ORDER.map(m => (
            <button key={m} className={'pill ' + (draft.meal === m ? 'pill--active' : '')}
              onClick={() => set('meal', m)}>
              <span>{MEAL_META[m].icon}</span><span>{MEAL_META[m].label}</span>
            </button>
          ))}
        </div>

        <div className="mainbutton-wrap" style={{ display: 'flex', gap: 8 }}>
          <button className="mainbutton" style={{ background: 'rgba(224,88,88,0.16)', color: 'var(--danger)', flex: '0 0 35%' }}
            onClick={() => onDelete(entry.id)} disabled={saving}>
            <IconTrash size={18} /> Удалить
          </button>
          <button className="mainbutton" style={{ flex: 1 }} onClick={() => onSave(entry.id, draft)} disabled={saving}>
            {saving ? <span className="mainbutton__spin" /> : 'Сохранить'}
          </button>
        </div>
      </div>
    </React.Fragment>
  );
}

// ─── Global error state (LLM down etc) ─────────────────────────
function ErrorBanner({ message, onRetry }) {
  return (
    <div className="error" style={{ margin: '12px' }}>
      <span className="error__icon"><IconWarn size={18} stroke="#F2A0A0" /></span>
      <div className="error__body" style={{ flex: 1 }}>
        <div>{message}</div>
        {onRetry && (
          <button onClick={onRetry} className="error__retry">
            <IconRetry size={14} /> Повторить
          </button>
        )}
      </div>
    </div>
  );
}

Object.assign(window, {
  GoalsCta, TodayScreen, WeekChart, HistoryScreen, ExportScreen,
  CreateDishView, FoodPickerMode, AddSheet, ConfirmScreen, NumField, EditEntrySheet, ErrorBanner,
});
