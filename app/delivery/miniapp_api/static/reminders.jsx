// reminders.jsx — экран волны C: Напоминания + Аналитика (Reminders + Analytics).
// Один экран с верхними вкладками: «Напоминания» | «Обзор» | «Дайджест».
// React UMD без сборки. Глобал window.RemindersScreen({ onExit }).
//
// Контракт: свой <TgHeader onBack=…>; во внутреннем корне back = onExit().
// Сеть — window.apiV1(path,{method,body,query}) (пути БЕЗ /api/v1). Строится против
// ФАКТИЧЕСКИХ шейпов routes.py (reminders/analytics) + digest.py (структурные секции).
//
// Источники истины по шейпам:
//   reminders: contexts/reminders/api/routes.py  — _dto(): id,text,next_fire_at,
//     recurrence('none|daily|weekly|interval'), interval_seconds, scope('personal|family'),
//     status('ACTIVE|DONE|MISSED|CANCELLED'), last_fired_at, owner_id, created_at.
//   analytics/overview: {range:{from,to}, lists:{by_type:[{type,added,done}]},
//     finance:{plan,fact,diff,plan_applicable,top_deviations:[{category,plan,fact,diff}]},
//     nutrition:{kcal,protein,fat,carbs,days_with_data,goal:{kcal,protein,fat,carbs}|null}}.
//   analytics/digest/preview: {period:{from,to}, sections:[…]} — секции СТРУКТУРНЫЕ
//     (lists:{by_type}, finance:{fact,plan,top_expenses}, nutrition:{kcal,…,goal}).
//   analytics/digest/schedules: [{enabled,cadence('DAILY|WEEKLY'),weekday(int|null,вс=6),
//     time_of_day('HH:MM'),last_sent_at}]; PUT /{cadence} {enabled?,weekday?,time_of_day}.

(function () {
  const { useState, useEffect, useRef, useCallback } = React;

  // ─── API-методы (пути относительно /api/v1) ─────────────────────────────────
  const RemAPI = {
    list:   (status)      => window.apiV1('/reminders', { query: status ? { status } : null }),
    create: (body)        => window.apiV1('/reminders', { method: 'POST', body }),
    patch:  (id, body)    => window.apiV1('/reminders/' + id, { method: 'PATCH', body }),
    remove: (id)          => window.apiV1('/reminders/' + id, { method: 'DELETE' }),
    ack:    (id)          => window.apiV1('/reminders/' + id + '/ack', { method: 'POST' }),
    snooze: (id, minutes) => window.apiV1('/reminders/' + id + '/snooze', { method: 'POST', body: { minutes } }),
  };
  const AnAPI = {
    overview:    (from, to) => window.apiV1('/analytics/overview', { query: { from, to } }),
    preview:     ()         => window.apiV1('/analytics/digest/preview'),
    schedules:   ()         => window.apiV1('/analytics/digest/schedules'),
    putSchedule: (cadence, body) => window.apiV1('/analytics/digest/schedules/' + cadence, { method: 'PUT', body }),
  };

  // ─── Константы / справочники ────────────────────────────────────────────────
  const STATUS_TABS = [
    { value: 'active',    label: 'Активные' },
    { value: 'done',      label: 'Выполнены' },
    { value: 'missed',    label: 'Пропущены' },
    { value: 'cancelled', label: 'Отменены' },
  ];
  const STATUS_RU = {
    active: 'Активно', done: 'Выполнено', missed: 'Пропущено', cancelled: 'Отменено',
  };
  const RECUR_OPTS = [
    { value: 'none',     label: 'Без повтора' },
    { value: 'daily',    label: 'Ежедневно' },
    { value: 'weekly',   label: 'Еженедельно' },
    { value: 'interval', label: 'Через интервал' },
  ];
  const RECUR_RU = {
    none: 'Разово', daily: 'Каждый день', weekly: 'Каждую неделю', interval: 'По интервалу',
  };
  const SCOPE_OPTS = [
    { value: 'personal', label: 'Личное' },
    { value: 'family',   label: 'Общее (семья)' },
  ];
  const SNOOZE_PRESETS = [
    { minutes: 15,  label: '15 мин' },
    { minutes: 60,  label: '1 час' },
    { minutes: 180, label: '3 часа' },
    { minutes: 1440, label: 'Завтра' },
  ];
  const LIST_TYPE_RU = {
    task: 'Дела', shopping: 'Покупки', idea: 'Идеи',
    movie: 'Фильмы', gift: 'Подарки', link: 'Ссылки',
  };
  const WEEKDAYS = [
    { value: 0, label: 'Пн' }, { value: 1, label: 'Вт' }, { value: 2, label: 'Ср' },
    { value: 3, label: 'Чт' }, { value: 4, label: 'Пт' }, { value: 5, label: 'Сб' },
    { value: 6, label: 'Вс' },
  ];
  const WEEKDAY_RU = { 0: 'понедельник', 1: 'вторник', 2: 'среда', 3: 'четверг', 4: 'пятница', 5: 'суббота', 6: 'воскресенье' };

  const INTERVAL_UNITS = [
    { value: 'min',  label: 'мин', factor: 60 },
    { value: 'hour', label: 'ч',   factor: 3600 },
    { value: 'day',  label: 'дн',  factor: 86400 },
  ];

  // ─── Хелперы дат/времени ────────────────────────────────────────────────────

  // 'YYYY-MM-DDTHH:MM' (локальное, для <input type=datetime-local>) из ISO-строки.
  function isoToLocalInput(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  // Значение <input type=datetime-local> → ISO без зоны (бэк парсит fromisoformat).
  function localInputToIso(v) {
    if (!v) return '';
    // v уже вида 'YYYY-MM-DDTHH:MM'; добавляем :00 для корректного fromisoformat.
    return v.length === 16 ? v + ':00' : v;
  }
  function defaultFireLocal() {
    const d = new Date(Date.now() + 60 * 60 * 1000); // +1 час
    d.setMinutes(0, 0, 0);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  // Человекочитаемая дата-время из ISO.
  function fmtDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return `${ddmmm(iso)}, ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  // Человекочитаемый интервал из секунд.
  function fmtInterval(secs) {
    if (!secs || secs <= 0) return '';
    if (secs % 86400 === 0) return `каждые ${secs / 86400} дн.`;
    if (secs % 3600 === 0) return `каждые ${secs / 3600} ч.`;
    if (secs % 60 === 0) return `каждые ${secs / 60} мин.`;
    return `каждые ${secs} сек.`;
  }
  // Описание recurrence человеку (для строки списка).
  function recurDesc(rem) {
    if (rem.recurrence === 'none') return RECUR_RU.none;
    if (rem.recurrence === 'interval') return fmtInterval(rem.interval_seconds) || RECUR_RU.interval;
    if (rem.recurrence === 'weekly') {
      const dt = rem.next_fire_at ? new Date(rem.next_fire_at) : null;
      // JS getDay(): вс=0..сб=6; наш weekday: пн=0..вс=6 → не критично, показываем по дате.
      const dow = dt && !isNaN(dt.getTime()) ? DOW[dt.getDay()] : '';
      return dow ? `Каждую неделю (${dow})` : RECUR_RU.weekly;
    }
    return RECUR_RU[rem.recurrence] || rem.recurrence;
  }
  function todayIso() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function fmtRub(n) {
    if (n == null || isNaN(n)) return '—';
    return Math.round(Number(n)).toLocaleString('ru-RU') + ' ₽';
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Вкладка 1 — НАПОМИНАНИЯ
  // ═══════════════════════════════════════════════════════════════════════════

  // Лист создания/редактирования напоминания.
  function ReminderEditor({ open, initial, onClose, onSubmit, busy }) {
    const isEdit = !!(initial && initial.id);
    const [text, setText] = useState('');
    const [fireLocal, setFireLocal] = useState('');
    const [recurrence, setRecurrence] = useState('none');
    const [scope, setScope] = useState('personal');
    const [intervalVal, setIntervalVal] = useState('1');
    const [intervalUnit, setIntervalUnit] = useState('hour');
    const [err, setErr] = useState(null);

    // Сброс полей при каждом открытии (initial задаёт стартовые значения).
    useEffect(() => {
      if (!open) return;
      setErr(null);
      if (initial && initial.id) {
        setText(initial.text || '');
        setFireLocal(isoToLocalInput(initial.next_fire_at) || defaultFireLocal());
        setRecurrence(initial.recurrence || 'none');
        setScope(initial.scope || 'personal');
        if (initial.recurrence === 'interval' && initial.interval_seconds) {
          // Подобрать удобную единицу.
          const s = initial.interval_seconds;
          if (s % 86400 === 0) { setIntervalVal(String(s / 86400)); setIntervalUnit('day'); }
          else if (s % 3600 === 0) { setIntervalVal(String(s / 3600)); setIntervalUnit('hour'); }
          else { setIntervalVal(String(Math.round(s / 60))); setIntervalUnit('min'); }
        } else {
          setIntervalVal('1'); setIntervalUnit('hour');
        }
      } else {
        setText('');
        setFireLocal(defaultFireLocal());
        setRecurrence('none');
        setScope('personal');
        setIntervalVal('1');
        setIntervalUnit('hour');
      }
    }, [open, initial]);

    function intervalSeconds() {
      const n = parseInt(intervalVal, 10);
      const unit = INTERVAL_UNITS.find((u) => u.value === intervalUnit);
      if (!n || n <= 0 || !unit) return 0;
      return n * unit.factor;
    }

    function submit() {
      setErr(null);
      const t = text.trim();
      if (!t) { setErr('Укажи текст напоминания'); return; }
      if (!fireLocal) { setErr('Укажи дату и время'); return; }
      const body = {
        text: t,
        fire_at: localInputToIso(fireLocal),
        recurrence,
        scope,
      };
      if (recurrence === 'interval') {
        const secs = intervalSeconds();
        if (!secs) { setErr('Укажи интервал повтора'); return; }
        body.interval_seconds = secs;
      }
      onSubmit(body);
    }

    const footer = (
      <PrimaryButton onClick={submit} loading={busy}>
        {isEdit ? 'Сохранить' : 'Создать'}
      </PrimaryButton>
    );

    return (
      <Sheet open={open} title={isEdit ? 'Редактировать' : 'Новое напоминание'} onClose={onClose} footer={footer}>
        <Field label="Текст">
          <TextArea value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Например: выпить витамин D" rows={2} />
        </Field>
        <Field label="Дата и время" hint={recurrence === 'weekly' ? 'День недели берётся из этой даты' : null}>
          <TextInput type="datetime-local" value={fireLocal}
            onChange={(e) => setFireLocal(e.target.value)} />
        </Field>
        <Field label="Повтор">
          <SelectInput value={recurrence} onChange={setRecurrence} options={RECUR_OPTS} />
        </Field>
        {recurrence === 'interval' && (
          <Field label="Интервал повтора">
            <div className="rem-interval">
              <TextInput type="number" min="1" value={intervalVal}
                onChange={(e) => setIntervalVal(e.target.value)} />
              <SelectInput value={intervalUnit} onChange={setIntervalUnit}
                options={INTERVAL_UNITS.map((u) => ({ value: u.value, label: u.label }))} />
            </div>
          </Field>
        )}
        <Field label="Видимость" hint={scope === 'family' ? 'Видно обоим в семье' : 'Только тебе'}>
          <SelectInput value={scope} onChange={setScope} options={SCOPE_OPTS} />
        </Field>
        {err && <div className="rem-formerr">{err}</div>}
      </Sheet>
    );
  }

  // Лист snooze — быстрый выбор минут.
  function SnoozeSheet({ open, reminder, onClose, onPick, busy }) {
    return (
      <Sheet open={open} title="Отложить" onClose={onClose}>
        {reminder && <div className="rem-snooze-text">{reminder.text}</div>}
        <div className="rem-snooze-grid">
          {SNOOZE_PRESETS.map((p) => (
            <button key={p.minutes} className="rem-snooze-btn"
              disabled={busy} onClick={() => onPick(p.minutes)}>
              {p.label}
            </button>
          ))}
        </div>
        {busy && <div className="rem-center"><Spinner size={20} /></div>}
      </Sheet>
    );
  }

  function ReminderRow({ rem, onAck, onSnooze, onEdit, onDelete, busyId }) {
    const isActive = rem.status === 'active';
    const busy = busyId === rem.id;
    const statusColor =
      rem.status === 'missed' ? 'var(--danger)' :
      rem.status === 'done' ? 'var(--success, var(--tg-text-2))' :
      'var(--tg-text-2)';
    const sub = (
      <span>
        <span className="rem-when">{fmtDateTime(rem.next_fire_at)}</span>
        <span className="rem-dot">·</span>
        <span>{recurDesc(rem)}</span>
        {rem.scope === 'family' && <span className="rem-badge">семья</span>}
      </span>
    );
    return (
      <div className="rem-item">
        <div className="rem-item__main">
          <div className="rem-item__title">{rem.text}</div>
          <div className="rem-item__sub">{sub}</div>
          {!isActive && (
            <div className="rem-item__status" style={{ color: statusColor }}>
              {STATUS_RU[rem.status] || rem.status}
            </div>
          )}
        </div>
        <div className="rem-item__actions">
          {busy ? (
            <Spinner size={18} />
          ) : isActive ? (
            <React.Fragment>
              <button className="rem-act rem-act--ok" title="Выполнено" onClick={() => onAck(rem)}>
                <IconCheck size={18} />
              </button>
              <button className="rem-act" title="Отложить" onClick={() => onSnooze(rem)}>
                <IconCalendar size={17} />
              </button>
              <button className="rem-act" title="Изменить" onClick={() => onEdit(rem)}>
                <IconEdit size={16} />
              </button>
              <button className="rem-act rem-act--del" title="Удалить" onClick={() => onDelete(rem)}>
                <IconTrash size={16} />
              </button>
            </React.Fragment>
          ) : (
            <button className="rem-act rem-act--del" title="Удалить" onClick={() => onDelete(rem)}>
              <IconTrash size={16} />
            </button>
          )}
        </div>
      </div>
    );
  }

  function RemindersTab({ showToast }) {
    const [status, setStatus] = useState('active');
    const { data, loading, error, reload, setData } = useAsyncData(
      () => RemAPI.list(status), [status]
    );
    const [editorOpen, setEditorOpen] = useState(false);
    const [editing, setEditing] = useState(null);   // null = создание
    const [saving, setSaving] = useState(false);
    const [snoozeFor, setSnoozeFor] = useState(null);
    const [snoozeBusy, setSnoozeBusy] = useState(false);
    const [confirmDel, setConfirmDel] = useState(null);
    const [delBusy, setDelBusy] = useState(false);
    const [busyId, setBusyId] = useState(null);

    const items = data || [];

    function openCreate() { setEditing(null); setEditorOpen(true); }
    function openEdit(rem) { setEditing(rem); setEditorOpen(true); }

    async function submitEditor(body) {
      setSaving(true);
      try {
        if (editing && editing.id) {
          await RemAPI.patch(editing.id, body);
          showToast('Сохранено');
        } else {
          await RemAPI.create(body);
          showToast('Напоминание создано');
        }
        setEditorOpen(false);
        setEditing(null);
        await reload();
      } catch (e) {
        showToast(e.message || 'Не удалось сохранить');
      } finally {
        setSaving(false);
      }
    }

    async function doAck(rem) {
      setBusyId(rem.id);
      try {
        await RemAPI.ack(rem.id);
        showToast('Отмечено выполненным');
        await reload();
      } catch (e) {
        showToast(e.message || 'Не удалось отметить');
        setBusyId(null);
      }
    }

    async function doSnooze(minutes) {
      if (!snoozeFor) return;
      setSnoozeBusy(true);
      try {
        await RemAPI.snooze(snoozeFor.id, minutes);
        showToast('Отложено');
        setSnoozeFor(null);
        await reload();
      } catch (e) {
        showToast(e.message || 'Не удалось отложить');
      } finally {
        setSnoozeBusy(false);
      }
    }

    async function doDelete() {
      if (!confirmDel) return;
      setDelBusy(true);
      try {
        await RemAPI.remove(confirmDel.id);
        showToast('Удалено');
        setConfirmDel(null);
        await reload();
      } catch (e) {
        showToast(e.message || 'Не удалось удалить');
      } finally {
        setDelBusy(false);
      }
    }

    let body;
    if (loading) {
      body = <div className="rem-center"><Spinner size={26} /></div>;
    } else if (error) {
      body = <ErrorState error={error} onRetry={reload} />;
    } else if (items.length === 0) {
      body = (
        <EmptyState icon="⏰"
          title={status === 'active' ? 'Пока нет напоминаний' : 'Здесь пусто'}
          hint={status === 'active' ? 'Создай первое — кнопкой «+» внизу.' : 'Нет напоминаний с таким статусом.'}
          action={status === 'active'
            ? <GhostButton onClick={openCreate}>Создать</GhostButton>
            : null} />
      );
    } else {
      body = (
        <div className="rem-group">
          {items.map((rem) => (
            <ReminderRow key={rem.id} rem={rem}
              onAck={doAck} onSnooze={setSnoozeFor} onEdit={openEdit} onDelete={setConfirmDel}
              busyId={busyId} />
          ))}
        </div>
      );
    }

    return (
      <React.Fragment>
        {/* .scroll — отдельный слой; Fab/шиты ниже = прямые сёстры, вне .page-enter
            (иначе transform .page-enter делает их containing block и Fab всплывает по центру). */}
        <div className="scroll">
          <div className="page-enter">
            <div className="rem-chips">
              {STATUS_TABS.map((t) => (
                <Chip key={t.value} active={status === t.value} onClick={() => setStatus(t.value)}>
                  {t.label}
                </Chip>
              ))}
            </div>
            {body}
          </div>
        </div>

        <Fab onClick={openCreate}><IconPlus size={20} /> Добавить</Fab>

        <ReminderEditor open={editorOpen} initial={editing}
          onClose={() => { setEditorOpen(false); setEditing(null); }}
          onSubmit={submitEditor} busy={saving} />

        <SnoozeSheet open={!!snoozeFor} reminder={snoozeFor}
          onClose={() => setSnoozeFor(null)} onPick={doSnooze} busy={snoozeBusy} />

        <ConfirmDialog open={!!confirmDel} title="Удалить напоминание?"
          message={confirmDel ? confirmDel.text : ''}
          confirmLabel="Удалить" danger busy={delBusy}
          onConfirm={doDelete} onCancel={() => setConfirmDel(null)} />
      </React.Fragment>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Вкладка 2 — ОБЗОР
  // ═══════════════════════════════════════════════════════════════════════════

  function OverviewTab({ showToast }) {
    const [from, setFrom] = useState(() => shiftDate(todayIso(), -29));
    const [to, setTo] = useState(() => todayIso());
    // applied — что реально отправлено в запрос (а не черновик в инпутах).
    const [applied, setApplied] = useState({ from, to });
    const { data, loading, error, reload } = useAsyncData(
      () => AnAPI.overview(applied.from, applied.to), [applied.from, applied.to]
    );

    function applyRange() {
      if (from > to) { showToast('Дата «с» позже даты «по»'); return; }
      setApplied({ from, to });
    }

    return (
      <div className="scroll">
        <div className="page-enter">
          <div className="rem-range">
            <Field label="С">
              <TextInput type="date" value={from} max={to}
                onChange={(e) => setFrom(e.target.value)} />
            </Field>
            <Field label="По">
              <TextInput type="date" value={to} max={todayIso()}
                onChange={(e) => setTo(e.target.value)} />
            </Field>
            <button className="rem-range__go" onClick={applyRange} title="Применить">
              <IconRetry size={18} />
            </button>
          </div>

          {loading ? (
            <div className="rem-center"><Spinner size={26} /></div>
          ) : error ? (
            <ErrorState error={error} onRetry={reload} />
          ) : data ? (
            <OverviewBody data={data} />
          ) : (
            <EmptyState icon="📊" title="Нет данных" hint="Попробуй другой период." />
          )}
        </div>
      </div>
    );
  }

  // Финансовая «разница» в обзоре = plan − fact (read_composition): diff>0 — экономия
  // (факт ниже плана, зелёный), diff<0 — перерасход (красный). Та же конвенция в
  // top_deviations[].diff. Не инвертировать.
  function OverviewBody({ data }) {
    const lists = data.lists || {};
    const fin = data.finance || {};
    const nut = data.nutrition || {};
    const byType = (lists.by_type || []).filter((t) => t.added > 0 || t.done > 0);
    const planApplicable = fin.plan_applicable && fin.plan != null;
    const hasFinance = fin.fact != null || fin.plan != null || (fin.top_deviations || []).length > 0;
    const hasNutrition = nut && (nut.kcal != null || (nut.days_with_data || 0) > 0);

    const empty = byType.length === 0 && !hasFinance && !hasNutrition;
    if (empty) {
      return <EmptyState icon="📊" title="За период пусто"
        hint="Нет изменений в видимых тебе сферах за этот период." />;
    }

    return (
      <div>
        {/* Списки */}
        <SectionTitle>Списки</SectionTitle>
        {byType.length === 0 ? (
          <Card><div className="rem-muted">Изменений нет</div></Card>
        ) : (
          <Card>
            {byType.map((t) => (
              <div key={t.type} className="rem-statline">
                <span className="rem-statline__name">{LIST_TYPE_RU[t.type] || t.type}</span>
                <span className="rem-statline__vals">
                  {t.added > 0 && <span className="rem-pill rem-pill--add">+{t.added} добавлено</span>}
                  {t.done > 0 && <span className="rem-pill rem-pill--done">{t.done} выполнено</span>}
                </span>
              </div>
            ))}
          </Card>
        )}

        {/* Финансы */}
        <SectionTitle>Финансы</SectionTitle>
        {!hasFinance ? (
          <Card><div className="rem-muted">Нет данных за период</div></Card>
        ) : (
          <Card>
            <div className="rem-fin-top">
              <div className="rem-fin-cell">
                <div className="rem-fin-cell__label">Факт</div>
                <div className="rem-fin-cell__val">{fmtRub(fin.fact)}</div>
              </div>
              <div className="rem-fin-cell">
                <div className="rem-fin-cell__label">План</div>
                <div className="rem-fin-cell__val">{planApplicable ? fmtRub(fin.plan) : '—'}</div>
              </div>
              <div className="rem-fin-cell">
                <div className="rem-fin-cell__label">Разница</div>
                <div className="rem-fin-cell__val" style={planApplicable && fin.diff != null
                  ? { color: fin.diff < 0 ? 'var(--danger)' : 'var(--success, var(--tg-text))' } : null}>
                  {planApplicable && fin.diff != null
                    ? (fin.diff > 0 ? '+' : '') + fmtRub(fin.diff)
                    : '—'}
                </div>
              </div>
            </div>
            {!planApplicable && (
              <div className="rem-hint-inline">План доступен только для полных месяцев.</div>
            )}
            {(fin.top_deviations || []).length > 0 && (
              <div className="rem-dev">
                <div className="rem-dev__head">Заметные отклонения</div>
                {fin.top_deviations.map((d, i) => (
                  <div key={i} className="rem-dev__row">
                    <span className="rem-dev__cat">{d.category}</span>
                    <span className="rem-dev__nums">
                      <span>{fmtRub(d.fact)}</span>
                      {d.plan != null && <span className="rem-muted"> / {fmtRub(d.plan)}</span>}
                      {d.diff != null && (
                        <span style={{ color: d.diff < 0 ? 'var(--danger)' : 'var(--success, var(--tg-text-2))' }}>
                          {' '}({d.diff > 0 ? '+' : ''}{fmtRub(d.diff)})
                        </span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        )}

        {/* Питание */}
        <SectionTitle>Питание</SectionTitle>
        {!hasNutrition ? (
          <Card><div className="rem-muted">Нет записей за период</div></Card>
        ) : (
          <Card>
            <div className="rem-nut-head">
              <div className="rem-nut-kcal">
                <b>{fmtNum(nut.kcal)}</b>
                {nut.goal && nut.goal.kcal != null && <span className="rem-muted"> / {fmtNum(nut.goal.kcal)}</span>}
                <span className="rem-nut-unit"> ккал</span>
              </div>
              <div className="rem-muted rem-nut-days">за {nut.days_with_data || 0} дн.</div>
            </div>
            <div className="rem-macros">
              <span style={{ color: 'var(--protein)' }}>Б {fmtNum(nut.protein)}{nut.goal && nut.goal.protein != null ? ' / ' + fmtNum(nut.goal.protein) : ''}</span>
              <span style={{ color: 'var(--fat)' }}>Ж {fmtNum(nut.fat)}{nut.goal && nut.goal.fat != null ? ' / ' + fmtNum(nut.goal.fat) : ''}</span>
              <span style={{ color: 'var(--carbs)' }}>У {fmtNum(nut.carbs)}{nut.goal && nut.goal.carbs != null ? ' / ' + fmtNum(nut.goal.carbs) : ''}</span>
            </div>
          </Card>
        )}
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Вкладка 3 — ДАЙДЖЕСТ
  // ═══════════════════════════════════════════════════════════════════════════

  function DigestTab({ showToast }) {
    const preview = useAsyncData(() => AnAPI.preview(), []);
    const sched = useAsyncData(() => AnAPI.schedules(), []);

    return (
      <div className="scroll">
        <div className="page-enter">
          <SectionTitle>Предпросмотр</SectionTitle>
          {preview.loading ? (
            <div className="rem-center"><Spinner size={24} /></div>
          ) : preview.error ? (
            <ErrorState error={preview.error} onRetry={preview.reload} />
          ) : (
            <DigestPreview dto={preview.data} />
          )}

          <SectionTitle>Расписание рассылки</SectionTitle>
          {sched.loading ? (
            <div className="rem-center"><Spinner size={24} /></div>
          ) : sched.error ? (
            <ErrorState error={sched.error} onRetry={sched.reload} />
          ) : (
            <SchedulesEditor schedules={sched.data || []} reload={sched.reload} showToast={showToast} />
          )}
        </div>
      </div>
    );
  }

  function DigestPreview({ dto }) {
    const sections = (dto && dto.sections) || [];
    const period = dto && dto.period;
    if (sections.length === 0) {
      return (
        <EmptyState icon="📨" title="Дайджест пуст"
          hint="За текущий период нет изменений — рассылать нечего." />
      );
    }
    return (
      <div>
        {period && (
          <div className="rem-period">{ddmmm(period.from)} — {ddmmm(period.to)}</div>
        )}
        {sections.map((s, i) => <DigestSection key={i} section={s} />)}
      </div>
    );
  }

  function DigestSection({ section }) {
    if (section.sphere === 'lists') {
      const byType = section.by_type || [];
      return (
        <Card>
          <div className="rem-dig-title">Списки</div>
          {byType.map((t) => (
            <div key={t.type} className="rem-statline">
              <span className="rem-statline__name">{LIST_TYPE_RU[t.type] || t.type}</span>
              <span className="rem-statline__vals">
                {t.added > 0 && <span className="rem-pill rem-pill--add">+{t.added}</span>}
                {t.done > 0 && <span className="rem-pill rem-pill--done">✓{t.done}</span>}
              </span>
            </div>
          ))}
        </Card>
      );
    }
    if (section.sphere === 'finance') {
      const top = section.top_expenses || [];
      return (
        <Card>
          <div className="rem-dig-title">Финансы</div>
          <div className="rem-dig-line">
            {section.plan != null
              ? <span>Факт <b>{fmtRub(section.fact)}</b> из плана {fmtRub(section.plan)}</span>
              : <span>Расходы <b>{fmtRub(section.fact)}</b></span>}
          </div>
          {top.length > 0 && (
            <div className="rem-dig-sub">
              <div className="rem-muted rem-dig-sublabel">Крупные траты</div>
              {top.map((e, i) => (
                <div key={i} className="rem-dev__row">
                  <span className="rem-dev__cat">{e.category}</span>
                  <span>{fmtRub(e.amount)}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      );
    }
    if (section.sphere === 'nutrition') {
      const goal = section.goal;
      const kcalGoal = goal && goal.kcal != null ? goal.kcal : null;
      return (
        <Card>
          <div className="rem-dig-title">Питание</div>
          <div className="rem-dig-line">
            <b>{fmtNum(section.kcal)}</b>
            {kcalGoal != null && <span className="rem-muted"> / {fmtNum(kcalGoal)}</span>}
            {' ккал за ' + (section.days_with_data || 0) + ' дн.'}
          </div>
          <div className="rem-macros">
            <span style={{ color: 'var(--protein)' }}>Б {fmtNum(section.protein)}</span>
            <span style={{ color: 'var(--fat)' }}>Ж {fmtNum(section.fat)}</span>
            <span style={{ color: 'var(--carbs)' }}>У {fmtNum(section.carbs)}</span>
          </div>
        </Card>
      );
    }
    return null;
  }

  function SchedulesEditor({ schedules, reload, showToast }) {
    // Бэк отдаёт расписания, которые существуют; если семьи нет — пустой массив.
    // Сводим к карте по cadence; даём редактор для DAILY и WEEKLY.
    const byCadence = {};
    schedules.forEach((s) => { byCadence[s.cadence] = s; });
    const noFamily = schedules.length === 0;

    return (
      <div>
        {noFamily && (
          <Card className="rem-softnote">
            <div className="rem-muted">
              Расписание дайджеста настраивается на семью. Создай или присоединись к семье в боте
              (команды /newfamily, /join), чтобы включить рассылку.
            </div>
          </Card>
        )}
        <ScheduleCard cadence="daily" label="Ежедневный"
          schedule={byCadence.daily} disabled={noFamily}
          reload={reload} showToast={showToast} />
        <ScheduleCard cadence="weekly" label="Еженедельный"
          schedule={byCadence.weekly} disabled={noFamily}
          reload={reload} showToast={showToast} />
      </div>
    );
  }

  function ScheduleCard({ cadence, label, schedule, disabled, reload, showToast }) {
    const isWeekly = cadence === 'weekly';
    const exists = !!schedule;
    const [enabled, setEnabled] = useState(exists ? schedule.enabled : true);
    const [time, setTime] = useState(exists ? schedule.time_of_day : (isWeekly ? '23:00' : '23:59'));
    const [weekday, setWeekday] = useState(exists && schedule.weekday != null ? schedule.weekday : 6);
    const [saving, setSaving] = useState(false);

    // Синхронизация при обновлении данных извне.
    useEffect(() => {
      if (schedule) {
        setEnabled(schedule.enabled);
        setTime(schedule.time_of_day);
        if (schedule.weekday != null) setWeekday(schedule.weekday);
      }
    }, [schedule]);

    async function save() {
      if (disabled) return;
      if (!/^\d{2}:\d{2}$/.test(time)) { showToast('Время в формате ЧЧ:ММ'); return; }
      setSaving(true);
      try {
        const body = { enabled, time_of_day: time };
        if (isWeekly) body.weekday = weekday;
        await AnAPI.putSchedule(cadence, body);
        showToast('Расписание сохранено');
        await reload();
      } catch (e) {
        showToast(e.message || 'Не удалось сохранить');
      } finally {
        setSaving(false);
      }
    }

    return (
      <Card>
        <div className="rem-sched-head">
          <div className="rem-sched-title">{label}</div>
          <label className="rem-switch">
            <input type="checkbox" checked={enabled} disabled={disabled}
              onChange={(e) => setEnabled(e.target.checked)} />
            <span className="rem-switch__track"><span className="rem-switch__knob" /></span>
          </label>
        </div>

        <div className="rem-sched-fields">
          <Field label="Время">
            <TextInput type="time" value={time} disabled={disabled}
              onChange={(e) => setTime(e.target.value)} />
          </Field>
          {isWeekly && (
            <Field label="День недели">
              <SelectInput value={String(weekday)} disabled={disabled}
                onChange={(v) => setWeekday(parseInt(v, 10))}
                options={WEEKDAYS.map((w) => ({ value: String(w.value), label: w.label + ' (' + WEEKDAY_RU[w.value] + ')' }))} />
            </Field>
          )}
        </div>

        {exists && schedule.last_sent_at && (
          <div className="rem-sched-last">Последняя рассылка: {fmtDateTime(schedule.last_sent_at)}</div>
        )}

        <PrimaryButton onClick={save} loading={saving} disabled={disabled}>
          Сохранить
        </PrimaryButton>
      </Card>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Корень экрана
  // ═══════════════════════════════════════════════════════════════════════════

  const TABS = [
    { value: 'reminders', label: 'Напоминания' },
    { value: 'overview',  label: 'Обзор' },
    { value: 'digest',    label: 'Дайджест' },
  ];

  function RemindersScreen({ onExit }) {
    const [tab, setTab] = useState('reminders');
    const { toast, showToast } = useToast();

    // Telegram BackButton — назад из экрана = выход в хаб.
    useTgBackButton(true, () => { if (onExit) onExit(); });

    const title =
      tab === 'reminders' ? 'Напоминания' :
      tab === 'overview' ? 'Обзор' : 'Дайджест';

    return (
      <div className="app">
        <TgHeader title={title} onBack={onExit} />

        <Segmented tabs={TABS} value={tab} onChange={setTab} />

        {/* Каждая вкладка владеет своим .scroll; Fab/шиты вкладки — её прямые сёстры,
            вне .page-enter, чтобы position:absolute якорился к .app, а не к контенту. */}
        {tab === 'reminders' && <RemindersTab key="reminders" showToast={showToast} />}
        {tab === 'overview' && <OverviewTab key="overview" showToast={showToast} />}
        {tab === 'digest' && <DigestTab key="digest" showToast={showToast} />}

        <Toast show={!!toast}>{toast}</Toast>
      </div>
    );
  }

  // ─── Стили (инжект один раз, префикс rem-) ──────────────────────────────────
  const CSS = `
  .rem-center { display:flex; justify-content:center; padding: 36px 0; }
  .rem-muted { color: var(--tg-text-2); font-size: 13px; }

  .rem-chips { display:flex; flex-wrap:wrap; gap: 8px; padding: 10px 16px 6px; }

  .rem-group { margin: 6px 16px; border-radius: var(--radius); overflow:hidden;
    background: var(--tg-surface); }
  .rem-item { display:flex; align-items:flex-start; gap: 10px; padding: 12px 14px; }
  .rem-item + .rem-item { border-top: 1px solid var(--tg-separator); }
  .rem-item__main { flex:1; min-width:0; }
  .rem-item__title { font-size: 15px; font-weight: 500; line-height: 1.3;
    overflow-wrap:anywhere; }
  .rem-item__sub { font-size: 12px; color: var(--tg-text-2); margin-top: 3px;
    display:flex; align-items:center; flex-wrap:wrap; gap: 4px; }
  .rem-when { color: var(--tg-text); }
  .rem-dot { opacity:.5; }
  .rem-badge { margin-left: 2px; padding: 1px 7px; border-radius: 999px;
    background: var(--tg-surface-hi); color: var(--tg-text-2); font-size: 11px; }
  .rem-item__status { font-size: 12px; margin-top: 4px; font-weight: 500; }
  .rem-item__actions { flex-shrink:0; display:flex; align-items:center; gap: 4px;
    min-height: 32px; }
  .rem-act { width: 32px; height: 32px; border-radius: 8px; border:none;
    background: var(--tg-surface-2, rgba(255,255,255,0.05)); color: var(--tg-text-2);
    display:flex; align-items:center; justify-content:center; }
  .rem-act:active { background: var(--tg-surface-hi); }
  .rem-act--ok { color: var(--success, #4FB286); }
  .rem-act--del { color: var(--danger); }

  .rem-formerr { color: var(--danger); font-size: 13px; margin-top: 10px; }

  .rem-interval { display:flex; gap: 10px; }
  .rem-interval .sk-input:first-child { flex: 1; }
  .rem-interval .sk-select { width: 96px; flex-shrink:0; }

  .rem-snooze-text { font-size: 14px; color: var(--tg-text-2); margin: 4px 0 14px; }
  .rem-snooze-grid { display:grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .rem-snooze-btn { padding: 14px; border-radius: var(--radius-sm); border:none;
    background: var(--tg-surface); color: var(--tg-text); font-size: 15px; font-weight: 500; }
  .rem-snooze-btn:active { background: var(--tg-surface-2); }
  .rem-snooze-btn:disabled { opacity:.55; }

  .rem-range { display:flex; align-items:flex-end; gap: 10px; padding: 8px 16px 4px; }
  .rem-range .sk-field { flex:1; margin: 4px 0; }
  .rem-range__go { flex-shrink:0; width: 44px; height: 44px; border-radius: var(--radius-sm);
    border:none; background: var(--tg-accent); color:#fff;
    display:flex; align-items:center; justify-content:center; }
  .rem-range__go:active { background: var(--tg-accent-press); }

  .rem-statline { display:flex; align-items:center; justify-content:space-between;
    gap: 10px; padding: 6px 0; }
  .rem-statline + .rem-statline { border-top: 1px solid var(--tg-separator); }
  .rem-statline__name { font-size: 14px; }
  .rem-statline__vals { display:flex; gap: 6px; flex-wrap:wrap; justify-content:flex-end; }
  .rem-pill { font-size: 12px; padding: 2px 8px; border-radius: 999px;
    background: var(--tg-surface-hi); color: var(--tg-text-2); }
  .rem-pill--add { color: var(--tg-accent); }
  .rem-pill--done { color: var(--success, #4FB286); }

  .rem-fin-top { display:flex; gap: 8px; }
  .rem-fin-cell { flex:1; text-align:center; }
  .rem-fin-cell__label { font-size: 11px; color: var(--tg-text-3); text-transform:uppercase;
    letter-spacing:.4px; }
  .rem-fin-cell__val { font-size: 16px; font-weight: 600; margin-top: 4px; }
  .rem-hint-inline { font-size: 12px; color: var(--tg-text-3); margin-top: 10px; }
  .rem-dev { margin-top: 12px; border-top: 1px solid var(--tg-separator); padding-top: 10px; }
  .rem-dev__head { font-size: 12px; color: var(--tg-text-3); text-transform:uppercase;
    letter-spacing:.4px; margin-bottom: 6px; }
  .rem-dev__row { display:flex; justify-content:space-between; gap: 10px;
    font-size: 13px; padding: 4px 0; }
  .rem-dev__cat { color: var(--tg-text); overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .rem-dev__nums { flex-shrink:0; text-align:right; }

  .rem-nut-head { display:flex; align-items:baseline; justify-content:space-between; }
  .rem-nut-kcal { font-size: 18px; }
  .rem-nut-kcal b { font-weight: 700; }
  .rem-nut-unit { font-size: 13px; color: var(--tg-text-2); }
  .rem-nut-days { font-size: 13px; }
  .rem-macros { display:flex; gap: 14px; margin-top: 10px; font-size: 13px; font-weight: 500; }

  .rem-period { text-align:center; font-size: 13px; color: var(--tg-text-2); margin: 4px 0 2px; }
  .rem-dig-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
  .rem-dig-line { font-size: 14px; }
  .rem-dig-line b { font-weight: 700; }
  .rem-dig-sub { margin-top: 10px; border-top: 1px solid var(--tg-separator); padding-top: 8px; }
  .rem-dig-sublabel { font-size: 11px; text-transform:uppercase; letter-spacing:.4px;
    margin-bottom: 4px; }

  .rem-softnote { border: 1px dashed var(--tg-separator-strong); background: transparent; }

  .rem-sched-head { display:flex; align-items:center; justify-content:space-between; }
  .rem-sched-title { font-size: 15px; font-weight: 600; }
  .rem-sched-fields { display:flex; gap: 12px; }
  .rem-sched-fields .sk-field { flex:1; }
  .rem-sched-last { font-size: 12px; color: var(--tg-text-3); margin: 2px 0 12px; }

  .rem-switch { position:relative; display:inline-flex; cursor:pointer; }
  .rem-switch input { position:absolute; opacity:0; width:0; height:0; }
  .rem-switch__track { width: 44px; height: 26px; border-radius: 13px;
    background: var(--tg-separator-strong); transition: background .2s; display:block; }
  .rem-switch__knob { position:absolute; top: 3px; left: 3px; width: 20px; height: 20px;
    border-radius: 50%; background: #fff; transition: transform .2s; }
  .rem-switch input:checked + .rem-switch__track { background: var(--tg-accent); }
  .rem-switch input:checked + .rem-switch__track .rem-switch__knob { transform: translateX(18px); }
  .rem-switch input:disabled + .rem-switch__track { opacity:.5; }
  `;

  if (!document.getElementById('rem-styles')) {
    const style = document.createElement('style');
    style.id = 'rem-styles';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  window.RemindersScreen = RemindersScreen;
})();
