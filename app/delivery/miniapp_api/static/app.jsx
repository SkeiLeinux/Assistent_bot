// app.jsx — оркестратор мини-апы: маршрутизация экранов, загрузка данных из
// API, интеграция с Telegram WebApp (тема, BackButton).
const { useState, useEffect, useRef, useCallback } = React;

function clientHour() { return new Date().getHours(); }

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      resolve();
    } catch (e) { reject(e); }
  });
}

// items из /api/analyze → формат confirm-экрана (со своими id и save=false по умолчанию)
function withIds(items) {
  return items.map((it, i) => ({ id: i + 1, ...it, save: false }));
}

function NutritionApp({ onExit }) {
  // Навигация
  const [view, setView] = useState('today');     // today | history | export
  const [goalsOpen, setGoalsOpen] = useState(false);

  // Today
  const [date, setDate] = useState(null);
  const [today, setToday] = useState(null);
  const [day, setDay] = useState(null);
  const [dayLoading, setDayLoading] = useState(true);
  const [dayError, setDayError] = useState(null);

  // Goals
  const [goals, setGoals] = useState(null);
  const [goalsSaving, setGoalsSaving] = useState(false);

  // History
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Add sheet
  const [addOpen, setAddOpen] = useState(false);
  const [mode, setMode] = useState('text');
  const [text, setText] = useState('');
  const [photo, setPhoto] = useState(null);       // dataURL | null
  const [caption, setCaption] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);

  // Confirm
  const [confirmItems, setConfirmItems] = useState(null);
  const [confirmSource, setConfirmSource] = useState('text');
  const [mealType, setMealType] = useState('lunch');
  const [saving, setSaving] = useState(false);

  // Edit existing entry
  const [editEntry, setEditEntry] = useState(null);
  const [entrySaving, setEntrySaving] = useState(false);

  // Food DB picker
  const [foodResults, setFoodResults] = useState(null);
  const [foodLoading, setFoodLoading] = useState(false);
  const [pickedFood, setPickedFood] = useState(null);
  const [pickWeight, setPickWeight] = useState('');
  const [pickMeal, setPickMeal] = useState('lunch');
  const [pickSaving, setPickSaving] = useState(false);
  const foodSearchTimer = useRef(null);

  // Create dish
  const [createDishOpen, setCreateDishOpen] = useState(false);
  const [dishName, setDishName] = useState('');
  const [dishWeight, setDishWeight] = useState('');
  const [dishText, setDishText] = useState('');
  const [dishPhotos, setDishPhotos] = useState([]);
  const [dishAnalyzing, setDishAnalyzing] = useState(false);
  const [dishResult, setDishResult] = useState(null);
  const [dishSaving, setDishSaving] = useState(false);
  const [dishError, setDishError] = useState(null);

  // Export
  const [period, setPeriod] = useState('7d');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [generating, setGenerating] = useState(false);
  const [output, setOutput] = useState(null);
  const [exportError, setExportError] = useState(null);

  // Toast
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const showToast = useCallback((msg) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2400);
  }, []);

  // ─── Загрузка дня ──────────────────────────────────────────────
  const loadDay = useCallback(async (d) => {
    setDayLoading(true);
    setDayError(null);
    try {
      const resp = await API.getDay(d);
      setDay(resp);
      setDate(resp.date);
      setGoals(resp.goals);
      if (resp.is_today) setToday(resp.date);
    } catch (e) {
      setDayError(e.message || 'Не удалось загрузить день');
    } finally {
      setDayLoading(false);
    }
  }, []);

  // Первичная загрузка + инициализация Telegram
  useEffect(() => {
    const tg = window.TG;
    if (tg) {
      try { tg.ready(); tg.expand(); } catch (e) {}
      try { tg.setBackgroundColor('#17212B'); tg.setHeaderColor('#17212B'); } catch (e) {}
    }
    loadDay(undefined);
  }, [loadDay]);

  // ─── История ──────────────────────────────────────────────────
  const loadHistory = useCallback(async (anchor) => {
    if (!anchor) return;
    setHistoryLoading(true);
    try {
      const from = shiftDate(anchor, -6);
      const resp = await API.getPeriod(from, anchor);
      const byDate = {};
      resp.days.forEach(d => { byDate[d.date] = d; });
      const goalKcal = resp.goal_kcal;
      const days = [];
      for (let i = -6; i <= 0; i++) {
        const ds = shiftDate(anchor, i);
        const row = byDate[ds];
        const t = row ? row.totals : { kcal: 0, p: 0, f: 0, c: 0 };
        days.push({
          date: ds, dow: dowOf(ds),
          kcal: t.kcal, p: t.p, f: t.f, c: t.c,
          count: row ? row.count : 0,
          today: ds === resp.today,
          over: goalKcal != null && t.kcal > goalKcal,
        });
      }
      setHistory({ days, goalKcal });
    } catch (e) {
      showToast(e.message || 'Не удалось загрузить историю');
    } finally {
      setHistoryLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (view === 'history' && today) loadHistory(today);
  }, [view, today, loadHistory]);

  // ─── Навигация по дням ─────────────────────────────────────────
  function prevDay() { if (date) loadDay(shiftDate(date, -1)); }
  function nextDay() { if (date && day && !day.is_today) loadDay(shiftDate(date, 1)); }

  // ─── Анализ еды ────────────────────────────────────────────────
  async function handleAnalyze() {
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      let payload;
      if (mode === 'text') payload = { type: 'text', text };
      else payload = { type: 'photo', image: photo, caption };
      const resp = await API.analyze(payload);
      if (!resp.items || resp.items.length === 0) {
        setAnalyzeError('Продукты не распознаны, попробуйте уточнить.');
        return;
      }
      setConfirmItems(withIds(resp.items));
      setConfirmSource(mode);
      setMealType(autoMeal(clientHour()));
      setAddOpen(false);
    } catch (e) {
      setAnalyzeError(e.message || 'Ошибка анализа');
    } finally {
      setAnalyzing(false);
    }
  }

  // ─── Сохранение распознанных позиций ───────────────────────────
  async function handleSave() {
    setSaving(true);
    try {
      const items = confirmItems.map(it => ({
        name: it.name, w: it.w, kcal: it.kcal, p: it.p, f: it.f, c: it.c,
        save: it.save || false,
      }));
      const resp = await API.saveEntries({
        items, meal_type: mealType, source: confirmSource, date,
      });
      setConfirmItems(null);
      setText(''); setPhoto(null); setCaption('');
      showToast('Сохранено · ' + resp.count + ' поз.');
      await loadDay(date);
    } catch (e) {
      showToast(e.message || 'Не удалось сохранить');
    } finally {
      setSaving(false);
    }
  }

  // ─── Редактирование / удаление записи ──────────────────────────
  async function handleEntrySave(id, draft) {
    setEntrySaving(true);
    try {
      await API.updateEntry(id, {
        name: draft.name, w: draft.w, kcal: draft.kcal,
        p: draft.p, f: draft.f, c: draft.c, meal_type: draft.meal,
      });
      setEditEntry(null);
      showToast('Изменено');
      await loadDay(date);
    } catch (e) {
      showToast(e.message || 'Не удалось изменить');
    } finally {
      setEntrySaving(false);
    }
  }
  async function handleEntryDelete(id) {
    setEntrySaving(true);
    try {
      await API.deleteEntry(id);
      setEditEntry(null);
      showToast('Удалено');
      await loadDay(date);
    } catch (e) {
      showToast(e.message || 'Не удалось удалить');
    } finally {
      setEntrySaving(false);
    }
  }

  // ─── Цели ──────────────────────────────────────────────────────
  async function handleGoalsSave(vals) {
    setGoalsSaving(true);
    try {
      const resp = await API.setGoals(vals);
      setGoals(resp);
      setGoalsOpen(false);
      showToast('Цели сохранены');
      await loadDay(date);
    } catch (e) {
      showToast(e.message || 'Не удалось сохранить цели');
    } finally {
      setGoalsSaving(false);
    }
  }

  // ─── Выгрузка ──────────────────────────────────────────────────
  async function handleGenerate() {
    setExportError(null);
    let from, to;
    if (period === '7d') { to = today; from = shiftDate(today, -6); }
    else if (period === '30d') { to = today; from = shiftDate(today, -29); }
    else {
      if (!customFrom || !customTo) { setExportError('Укажите обе даты.'); return; }
      from = customFrom; to = customTo;
      if (from > to) { setExportError('Дата «с» позже даты «по».'); return; }
    }
    setGenerating(true);
    setOutput(null);
    try {
      const resp = await API.getExport(from, to);
      setOutput(resp.markdown);
    } catch (e) {
      setExportError(e.message || 'Не удалось сформировать');
    } finally {
      setGenerating(false);
    }
  }

  function handleCopy(textToCopy) {
    copyText(textToCopy).then(() => showToast('Скопировано')).catch(() => showToast('Не удалось скопировать'));
  }

  // ─── База блюд ─────────────────────────────────────────────────
  const handleFoodSearch = useCallback((q) => {
    clearTimeout(foodSearchTimer.current);
    setFoodLoading(true);
    foodSearchTimer.current = setTimeout(async () => {
      try {
        const items = await API.getFoods(q);
        setFoodResults(items);
      } catch (_) {}
      finally { setFoodLoading(false); }
    }, 300);
  }, []);

  // Загружаем список при открытии вкладки "База"
  useEffect(() => {
    if (addOpen && mode === 'db') handleFoodSearch('');
  }, [addOpen, mode, handleFoodSearch]);

  function handlePickFood(food) {
    setPickedFood(food);
    setPickWeight(String(food.weight_g));
    setPickMeal(autoMeal(clientHour()));
  }

  function handleOpenCreateDish(open) {
    setCreateDishOpen(open);
    if (!open) {
      setDishName(''); setDishWeight(''); setDishText(''); setDishPhotos([]);
      setDishResult(null); setDishError(null);
    }
  }

  async function handleDishAnalyze() {
    setDishAnalyzing(true);
    setDishError(null);
    setDishResult(null);
    try {
      const payload = { name: dishName.trim(), weight_g: parseFloat(dishWeight) };
      if (dishText.trim()) payload.text = dishText.trim();
      if (dishPhotos.length > 0) payload.images = dishPhotos;
      const resp = await API.analyzeDish(payload);
      setDishResult({ kcal: resp.kcal, protein: resp.protein, fat: resp.fat, carbs: resp.carbs });
    } catch (e) {
      setDishError(e.message || 'Не удалось проанализировать');
    } finally {
      setDishAnalyzing(false);
    }
  }

  async function handleDishSave() {
    if (!dishResult) return;
    setDishSaving(true);
    try {
      await API.createFood({
        name: dishName.trim(),
        weight_g: parseFloat(dishWeight),
        kcal: parseFloat(dishResult.kcal) || 0,
        protein: parseFloat(dishResult.protein) || 0,
        fat: parseFloat(dishResult.fat) || 0,
        carbs: parseFloat(dishResult.carbs) || 0,
      });
      handleOpenCreateDish(false);
      showToast('Блюдо сохранено в базу');
      handleFoodSearch('');  // обновить список
    } catch (e) {
      setDishError(e.message || 'Не удалось сохранить');
    } finally {
      setDishSaving(false);
    }
  }

  async function handlePickConfirm() {
    const w = parseFloat(pickWeight) || 0;
    if (!w || w <= 0) return;
    setPickSaving(true);
    try {
      await API.addEntryFromFood({ food_id: pickedFood.id, weight_g: w, meal_type: pickMeal, date });
      setPickedFood(null);
      setAddOpen(false);
      showToast('Добавлено');
      await loadDay(date);
    } catch (e) {
      showToast(e.message || 'Не удалось добавить');
    } finally {
      setPickSaving(false);
    }
  }

  // ─── Назад (Telegram BackButton + in-app) ──────────────────────
  const goBack = useCallback(() => {
    if (editEntry) { setEditEntry(null); return; }
    if (createDishOpen) { handleOpenCreateDish(false); return; }
    if (pickedFood) { setPickedFood(null); return; }
    if (addOpen) { setAddOpen(false); setAnalyzeError(null); setPickedFood(null); return; }
    if (confirmItems) { setConfirmItems(null); return; }
    if (goalsOpen) { setGoalsOpen(false); return; }
    if (view !== 'today') { setView('today'); return; }
    if (onExit) onExit();                              // корень дневника → хаб ассистента
  }, [editEntry, createDishOpen, pickedFood, addOpen, confirmItems, goalsOpen, view, onExit]);

  const canGoBack = !!(editEntry || createDishOpen || pickedFood || addOpen || confirmItems || goalsOpen || view !== 'today' || onExit);
  useEffect(() => {
    const tg = window.TG;
    if (!tg || !tg.BackButton) return;
    if (canGoBack) { tg.BackButton.show(); } else { tg.BackButton.hide(); }
    tg.BackButton.onClick(goBack);
    return () => { try { tg.BackButton.offClick(goBack); } catch (e) {} };
  }, [canGoBack, goBack]);

  // ─── Рендер ────────────────────────────────────────────────────
  let header, content;
  const overlay = goalsOpen || confirmItems;       // экраны поверх вкладок
  const showFab = view === 'today' && !overlay && !addOpen && !editEntry;
  const showTabs = !overlay;

  if (confirmItems) {
    header = <TgHeader title="Подтверждение"
      sub={confirmItems.length + ' ' + (confirmItems.length === 1 ? 'позиция' : 'позиций')}
      onBack={() => setConfirmItems(null)} />;
    content = <ConfirmScreen items={confirmItems} setItems={setConfirmItems}
      mealType={mealType} setMealType={setMealType} source={confirmSource}
      saving={saving} onSave={handleSave} onCancel={() => setConfirmItems(null)} />;
  } else if (goalsOpen) {
    header = <TgHeader title="Цели КБЖУ" onBack={() => setGoalsOpen(false)} />;
    content = <GoalsScreen goals={goals} saving={goalsSaving} onSave={handleGoalsSave} />;
  } else if (view === 'today') {
    header = <TgHeader title="Дневник питания" sub={day && day.is_today ? 'сегодня' : (date ? ddmmm(date) : '')}
      onBack={onExit}
      right={
        <button className="tg-header__action" onClick={() => setGoalsOpen(true)} title="Цели">
          <IconGear size={20} />
        </button>
      } />;
    content = dayError ? (
      <ErrorBanner message={dayError} onRetry={() => loadDay(date)} />
    ) : (
      <TodayScreen day={day} loading={dayLoading}
        onOpenAdd={() => setAddOpen(true)} onEntryClick={setEditEntry}
        onPrevDay={prevDay} onNextDay={nextDay} onOpenGoals={() => setGoalsOpen(true)} />
    );
  } else if (view === 'history') {
    header = <TgHeader title="История" onBack={() => setView('today')} />;
    content = <HistoryScreen days={history ? history.days : null}
      goalKcal={history ? history.goalKcal : null} loading={historyLoading || !history}
      onDayClick={(d) => { setView('today'); loadDay(d.date); }} />;
  } else if (view === 'export') {
    header = <TgHeader title="Выгрузка за период" onBack={() => setView('today')} />;
    content = <ExportScreen period={period} setPeriod={setPeriod}
      customFrom={customFrom} setCustomFrom={setCustomFrom}
      customTo={customTo} setCustomTo={setCustomTo}
      output={output} generating={generating} error={exportError}
      onGenerate={handleGenerate} onCopy={handleCopy} />;
  }

  return (
    <div className="app">
      {header}

      {showTabs && (
        <div className="tabbar">
          <button className={'tabbar__btn ' + (view === 'today' ? 'tabbar__btn--active' : '')} onClick={() => setView('today')}>Сегодня</button>
          <button className={'tabbar__btn ' + (view === 'history' ? 'tabbar__btn--active' : '')} onClick={() => setView('history')}>История</button>
          <button className={'tabbar__btn ' + (view === 'export' ? 'tabbar__btn--active' : '')} onClick={() => setView('export')}>Выгрузка</button>
        </div>
      )}

      <div className="scroll" key={view + (confirmItems ? '-c' : '') + (goalsOpen ? '-g' : '')}>
        <div className="page-enter">{content}</div>
      </div>

      {showFab && (
        <div className="fab-wrap">
          <button className="fab fab--warm" onClick={() => setAddOpen(true)}>
            <IconPlus size={20} /> Добавить
          </button>
        </div>
      )}

      <AddSheet open={addOpen}
        onClose={() => { setAddOpen(false); setAnalyzeError(null); setPickedFood(null); handleOpenCreateDish(false); }}
        mode={mode} setMode={setMode} text={text} setText={setText}
        photo={photo} setPhoto={setPhoto} caption={caption} setCaption={setCaption}
        analyzing={analyzing} error={analyzeError} onAnalyze={handleAnalyze}
        foodResults={foodResults} foodLoading={foodLoading}
        pickedFood={pickedFood} pickWeight={pickWeight} setPickWeight={setPickWeight}
        pickMeal={pickMeal} setPickMeal={setPickMeal} pickSaving={pickSaving}
        onFoodSearch={handleFoodSearch} onPickFood={handlePickFood}
        onPickBack={() => setPickedFood(null)} onPickConfirm={handlePickConfirm}
        createDishOpen={createDishOpen}
        dishName={dishName} setDishName={setDishName}
        dishWeight={dishWeight} setDishWeight={setDishWeight}
        dishText={dishText} setDishText={setDishText}
        dishPhotos={dishPhotos}
        onAddDishPhoto={url => setDishPhotos(p => [...p, url])}
        onRemoveDishPhoto={idx => setDishPhotos(p => p.filter((_, i) => i !== idx))}
        dishAnalyzing={dishAnalyzing} dishResult={dishResult} setDishResult={setDishResult}
        dishSaving={dishSaving} dishError={dishError}
        onOpenCreateDish={handleOpenCreateDish}
        onDishAnalyze={handleDishAnalyze} onDishSave={handleDishSave} />

      <EditEntrySheet open={!!editEntry} entry={editEntry}
        onClose={() => setEditEntry(null)} onSave={handleEntrySave}
        onDelete={handleEntryDelete} saving={entrySaving} />

      <Toast show={!!toast}>
        <IconCheck size={14} stroke="var(--success)" /> {toast}
      </Toast>
    </div>
  );
}

window.NutritionApp = NutritionApp;
