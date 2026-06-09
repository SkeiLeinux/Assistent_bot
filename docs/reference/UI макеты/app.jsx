// app.jsx — main shell tying screens together
const { useState, useEffect, useMemo, useRef } = React;

const ANALYZED_TEXT_ITEMS = [
  { id: 1, name: 'Гречка варёная',      w: 200, kcal: 224, p: 8.4, f: 2.2, c: 45 },
  { id: 2, name: 'Куриная грудка гриль', w: 150, kcal: 248, p: 47,  f: 5.4, c: 0 },
];
const ANALYZED_PHOTO_ITEMS = [
  { id: 1, name: 'Паста с томатным соусом', w: 350, kcal: 525, p: 18, f: 12, c: 88 },
  { id: 2, name: 'Пармезан тёртый',         w: 15,  kcal: 60,  p: 5,  f: 4.4, c: 0.5 },
];

function App() {
  const [t, setTweak] = useTweaks(/*EDITMODE-BEGIN*/{
    "demo_today": "filled",
    "demo_history": "filled",
    "demo_error": "none",
    "start_screen": "today"
  }/*EDITMODE-END*/);

  // Tab routing
  const [tab, setTab] = useState(t.start_screen || 'today');
  useEffect(() => { setTab(t.start_screen); }, [t.start_screen]);

  // Day navigation on Today
  const [dayIdx, setDayIdx] = useState(0); // 0 = today, -1 = yesterday, etc.
  const dates = useMemo(() => {
    const arr = [];
    for (let i = -6; i <= 0; i++) {
      const d = new Date('2026-05-25');
      d.setDate(d.getDate() + i);
      arr.push(d);
    }
    return arr;
  }, []);
  const curDate = useMemo(() => {
    const d = new Date('2026-05-25');
    d.setDate(d.getDate() + dayIdx);
    return d;
  }, [dayIdx]);
  const curDateStr = curDate.toISOString().slice(0, 10);
  const dow = ['Вс','Пн','Вт','Ср','Чт','Пт','Сб'][curDate.getDay()];

  // Add sheet state
  const [addOpen, setAddOpen] = useState(false);
  const [mode, setMode] = useState('text');
  const [text, setText] = useState('гречка 200, куриная грудка 150');
  const [photo, setPhoto] = useState(null);
  const [caption, setCaption] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);

  // Confirm screen state
  const [confirmItems, setConfirmItems] = useState(null);
  const [confirmSource, setConfirmSource] = useState('text');
  const [mealType, setMealType] = useState('lunch');
  const [saving, setSaving] = useState(false);

  // Export
  const [period, setPeriod] = useState('7d');
  const [generating, setGenerating] = useState(false);
  const [output, setOutput] = useState(null);

  // Toast
  const [toast, setToast] = useState(null);
  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(null), 2200);
  }

  // Today entries (with demo state)
  const todayEntries = t.demo_today === 'empty' ? [] : (dayIdx === 0 ? TODAY_ENTRIES : TODAY_ENTRIES.slice(0, 3));

  function handleAnalyze() {
    setAnalyzing(true);
    setAnalyzeError(null);
    setTimeout(() => {
      // Simulate error toggle
      if (t.demo_error === 'rate' && Math.random() < 0.5) {
        setAnalyzing(false);
        setAnalyzeError('Превышен лимит модели (429). Попробуйте через минуту.');
        return;
      }
      setAnalyzing(false);
      const items = mode === 'text' ? ANALYZED_TEXT_ITEMS : ANALYZED_PHOTO_ITEMS;
      setConfirmItems(items);
      setConfirmSource(mode);
      // Auto meal-type by hour
      setMealType(autoMeal(new Date().getHours()));
      setAddOpen(false);
    }, 1400);
  }

  function handleSave() {
    setSaving(true);
    setTimeout(() => {
      setSaving(false);
      setConfirmItems(null);
      setText('');
      setPhoto(null);
      showToast('Сохранено · ' + confirmItems.length + ' позиций');
    }, 700);
  }

  function handleGenerate() {
    setGenerating(true);
    setTimeout(() => {
      setGenerating(false);
      setOutput(EXAMPLE_EXPORT);
    }, 900);
  }

  // ─── Screen rendering ────────────────────────────────────────
  let header, content;
  const showFab = !confirmItems && !addOpen;

  if (confirmItems) {
    header = <TgHeader
      title="Подтверждение"
      sub={confirmItems.length + ' ' + (confirmItems.length === 1 ? 'позиция' : 'позиций')}
      onBack={() => setConfirmItems(null)}
    />;
    content = (
      <ConfirmScreen
        items={confirmItems}
        setItems={setConfirmItems}
        mealType={mealType}
        setMealType={setMealType}
        source={confirmSource}
        saving={saving}
        onSave={handleSave}
        onCancel={() => setConfirmItems(null)}
      />
    );
  } else if (tab === 'today') {
    header = (
      <TgHeader
        title="Дневник питания"
        sub={dayIdx === 0 ? 'сегодня' : ddmmm(curDateStr)}
        right={
          <button className="tg-header__action" onClick={() => setTab('history')} title="История">
            <IconCalendar size={20} />
          </button>
        }
      />
    );
    content = (
      <TodayScreen
        entries={todayEntries}
        goal={GOAL}
        empty={t.demo_today === 'empty'}
        date={curDateStr}
        dow={dow}
        isToday={dayIdx === 0}
        onPrevDay={() => setDayIdx(Math.max(dayIdx - 1, -6))}
        onNextDay={() => setDayIdx(Math.min(dayIdx + 1, 0))}
        onOpenAdd={() => setAddOpen(true)}
        onEntryClick={() => showToast('Тап → редактировать запись')}
      />
    );
  } else if (tab === 'history') {
    header = <TgHeader title="История" onBack={() => setTab('today')} />;
    content = <HistoryScreen
      days={HISTORY}
      goal={GOAL.kcal}
      empty={t.demo_history === 'empty'}
      onDayClick={(d) => { setDayIdx(-(HISTORY.length - 1 - HISTORY.findIndex(x => x.date === d.date))); setTab('today'); }}
    />;
  } else if (tab === 'export') {
    header = <TgHeader title="Выгрузка за период" onBack={() => setTab('today')} />;
    content = <ExportScreen
      period={period}
      setPeriod={setPeriod}
      output={output}
      generating={generating}
      onGenerate={handleGenerate}
      onCopy={() => showToast('Скопировано в буфер')}
    />;
  }

  return (
    <div className="app">
      {header}

      {/* Sub-tabs (only on top-level screens) */}
      {!confirmItems && (
        <div className="tabbar">
          <button className={'tabbar__btn ' + (tab==='today' ? 'tabbar__btn--active' : '')} onClick={() => setTab('today')}>Сегодня</button>
          <button className={'tabbar__btn ' + (tab==='history' ? 'tabbar__btn--active' : '')} onClick={() => setTab('history')}>История</button>
          <button className={'tabbar__btn ' + (tab==='export' ? 'tabbar__btn--active' : '')} onClick={() => setTab('export')}>Выгрузка</button>
        </div>
      )}

      <div className="scroll" key={tab + (confirmItems ? '-c' : '')}>
        <div className="page-enter">
          {t.demo_error !== 'none' && !confirmItems && tab === 'today' && (
            <ErrorBanner
              kind={t.demo_error}
              onRetry={() => setTweak('demo_error', 'none')}
            />
          )}
          {content}
        </div>
      </div>

      {/* Floating action button — only on Today (not Confirm, not when sheet open) */}
      {showFab && tab === 'today' && (
        <div className="fab-wrap">
          <button className="fab fab--warm" onClick={() => setAddOpen(true)}>
            <IconPlus size={20} /> Добавить
          </button>
        </div>
      )}

      <AddSheet
        open={addOpen}
        onClose={() => { setAddOpen(false); setAnalyzeError(null); }}
        mode={mode}
        setMode={setMode}
        text={text}
        setText={setText}
        photo={photo}
        setPhoto={setPhoto}
        caption={caption}
        setCaption={setCaption}
        analyzing={analyzing}
        error={analyzeError}
        onAnalyze={handleAnalyze}
      />

      <Toast show={!!toast}>
        <IconCheck size={14} stroke="var(--success)" /> {toast}
      </Toast>

      {/* Tweaks panel */}
      <TweaksPanel title="Tweaks" width={300}>
        <TweakSection label="Демо-данные">
          <TweakRadio
            label="Сегодня"
            value={t.demo_today}
            options={[{ label: 'Заполнено', value: 'filled' }, { label: 'Пусто', value: 'empty' }]}
            onChange={v => setTweak('demo_today', v)}
          />
          <TweakRadio
            label="История"
            value={t.demo_history}
            options={[{ label: 'Заполнено', value: 'filled' }, { label: 'Пусто', value: 'empty' }]}
            onChange={v => setTweak('demo_history', v)}
          />
        </TweakSection>
        <TweakSection label="Сценарии">
          <TweakSelect
            label="Состояние ошибки"
            value={t.demo_error}
            options={[
              { label: 'Нет ошибки', value: 'none' },
              { label: 'LLM недоступен', value: 'no_llm' },
              { label: 'Превышен лимит (429)', value: 'rate' },
              { label: 'Нет API-ключа', value: 'no_key' },
              { label: 'Нет соединения', value: 'network' },
            ]}
            onChange={v => setTweak('demo_error', v)}
          />
        </TweakSection>
        <TweakSection label="Переход к экрану">
          <div className="scenenav">
            {[
              ['Сегодня', () => { setTab('today'); setConfirmItems(null); setAddOpen(false); }],
              ['Добавить (Текст)', () => { setMode('text'); setTab('today'); setConfirmItems(null); setAddOpen(true); }],
              ['Добавить (Фото)', () => { setMode('photo'); setPhoto('demo'); setTab('today'); setConfirmItems(null); setAddOpen(true); }],
              ['Подтверждение', () => { setConfirmItems(ANALYZED_TEXT_ITEMS); setConfirmSource('text'); setAddOpen(false); }],
              ['Подтверждение (фото)', () => { setConfirmItems(ANALYZED_PHOTO_ITEMS); setConfirmSource('photo'); setAddOpen(false); }],
              ['История', () => { setTab('history'); setConfirmItems(null); setAddOpen(false); }],
              ['Выгрузка', () => { setTab('export'); setConfirmItems(null); setAddOpen(false); setOutput(null); }],
              ['Выгрузка → результат', () => { setTab('export'); setConfirmItems(null); setAddOpen(false); setOutput(EXAMPLE_EXPORT); }],
            ].map(([label, fn]) => (
              <button key={label} onClick={fn}>{label}</button>
            ))}
          </div>
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

window.App = App;
