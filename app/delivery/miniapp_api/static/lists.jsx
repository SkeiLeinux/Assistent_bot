// lists.jsx — экран «Списки» (Lists) Mini App, волна C. Глобал window.ListsScreen.
// React UMD без сборки. Сеть — window.apiV1 (бросает ApiError с .message ru, .status).
// Стиль/палитра/примитивы переиспользуют screen-kit.jsx + styles.css (1:1 с едой).
//
// Контракт бэка (lowercase wire-значения enum'ов — см. contexts/lists/models/enums.py):
//   type:   task|shopping|idea|movie|gift|link
//   status: open|done
//   scope:  personal|family
//   action: created|edited|status_changed|moved
//
// Видимость двухуровневая: scope на списке; hidden_from (подарки) — поэлементно,
// осмыслен только в family-списке. Скрытое бэк не отдаёт — фронт «N скрытых» не рисует.

(function () {
  const { useState, useEffect, useRef, useMemo, useCallback } = React;

  // ─── API контекста Lists (пути без /api/v1; хелпер добавит) ──────────────────
  const ListsAPI = {
    listLists: (type) => window.apiV1('/lists', { query: type ? { type } : null }),
    createList: (body) => window.apiV1('/lists', { method: 'POST', body }),
    getList: (id) => window.apiV1('/lists/' + id),
    patchList: (id, body) => window.apiV1('/lists/' + id, { method: 'PATCH', body }),
    deleteList: (id) => window.apiV1('/lists/' + id, { method: 'DELETE' }),
    addItem: (listId, body) => window.apiV1('/lists/' + listId + '/items', { method: 'POST', body }),
    patchItem: (id, body) => window.apiV1('/items/' + id, { method: 'PATCH', body }),
    deleteItem: (id) => window.apiV1('/items/' + id, { method: 'DELETE' }),
    clearDone: (listId) => window.apiV1('/lists/' + listId + '/clear-done', { method: 'POST' }),
    search: (q) => window.apiV1('/lists/search', { query: { q } }),
    analytics: (type, from, to) => window.apiV1('/lists/analytics', { query: { type, from, to } }),
    itemHistory: (id) => window.apiV1('/items/' + id + '/history'),
    family: () => window.apiV1('/family'),
  };

  // ─── Справочники типов (lowercase ключи = wire-значения) ─────────────────────
  const TYPES = [
    { v: 'task',     emoji: '✅', label: 'Дела',    one: 'дело' },
    { v: 'shopping', emoji: '🛒', label: 'Покупки', one: 'покупку' },
    { v: 'idea',     emoji: '💡', label: 'Идеи',    one: 'идею' },
    { v: 'movie',    emoji: '🎬', label: 'Фильмы',  one: 'фильм' },
    { v: 'gift',     emoji: '🎁', label: 'Подарки', one: 'подарок' },
    { v: 'link',     emoji: '🔗', label: 'Ссылки',  one: 'ссылку' },
  ];
  const TYPE_BY_V = TYPES.reduce((a, t) => { a[t.v] = t; return a; }, {});
  const typeMeta = (v) => TYPE_BY_V[v] || { v, emoji: '📄', label: v, one: 'элемент' };

  // Схема type-полей: только ключи своего типа (чужой → 422 на бэке). kind: 'text'|'num'|'date'.
  const ATTR_SCHEMA = {
    task:     [{ k: 'due_date', label: 'Срок', kind: 'date' }, { k: 'priority', label: 'Приоритет', kind: 'text' }],
    shopping: [{ k: 'quantity', label: 'Кол-во', kind: 'text' }, { k: 'unit', label: 'Ед.', kind: 'text' }, { k: 'price', label: 'Цена', kind: 'num' }],
    idea:     [{ k: 'url', label: 'Ссылка', kind: 'text' }, { k: 'note', label: 'Заметка', kind: 'text' }],
    movie:    [{ k: 'year', label: 'Год', kind: 'num' }, { k: 'rating', label: 'Оценка', kind: 'num' }, { k: 'genre', label: 'Жанр', kind: 'text' }, { k: 'watched_on', label: 'Просмотрено', kind: 'date' }],
    gift:     [{ k: 'price', label: 'Цена', kind: 'num' }, { k: 'url', label: 'Ссылка', kind: 'text' }, { k: 'for_whom', label: 'Кому', kind: 'text' }],
    link:     [{ k: 'url', label: 'Ссылка', kind: 'text' }, { k: 'title', label: 'Заголовок', kind: 'text' }],
  };

  const HISTORY_LABEL = {
    created: 'Создан',
    edited: 'Изменён',
    status_changed: 'Статус',
    moved: 'Перемещён',
  };

  const EMOJI_CHOICES = ['📝', '✅', '🛒', '💡', '🎬', '🎁', '🔗', '⭐', '🏠', '✈️', '📚', '🍿', '🎵', '🧩'];

  // ─── Утилиты ─────────────────────────────────────────────────────────────────

  // Плоский список items → дерево по parent_id, отсортировано по position.
  function buildTree(items) {
    const byParent = new Map();
    for (const it of items) {
      const key = it.parent_id == null ? 'root' : it.parent_id;
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(it);
    }
    for (const arr of byParent.values()) {
      arr.sort((a, b) => (a.position || 0) - (b.position || 0) || a.id - b.id);
    }
    return byParent;
  }

  // Привести значение поля к нужному типу для отправки; пустое → ключ опускаем.
  function coerceAttr(kind, raw) {
    const s = (raw == null ? '' : String(raw)).trim();
    if (s === '') return undefined;
    if (kind === 'num') {
      const n = Number(s.replace(',', '.'));
      return isNaN(n) ? undefined : n;
    }
    return s; // date и text уходят строкой
  }

  // Собрать specific_attrs из формы по схеме типа; пустые ключи опускаются.
  function buildAttrs(type, form) {
    const schema = ATTR_SCHEMA[type] || [];
    const out = {};
    for (const f of schema) {
      const v = coerceAttr(f.kind, form[f.k]);
      if (v !== undefined) out[f.k] = v;
    }
    return Object.keys(out).length ? out : null;
  }

  // Краткое превью attrs под текстом элемента.
  function attrPreview(type, attrs) {
    if (!attrs) return null;
    const schema = ATTR_SCHEMA[type] || [];
    const parts = [];
    for (const f of schema) {
      const v = attrs[f.k];
      if (v != null && v !== '') parts.push(f.label + ': ' + v);
    }
    return parts.length ? parts.join(' · ') : null;
  }

  function isoToday() {
    const d = new Date();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + mm + '-' + dd;
  }
  function isoDaysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + mm + '-' + dd;
  }
  function fmtDateTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const dd = d.getDate() + ' ' + (window.MONTHS ? window.MONTHS[d.getMonth()] : (d.getMonth() + 1));
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return dd + ', ' + hh + ':' + mi;
  }

  // ─── Стили экрана (один инжект, префикс lists-) ──────────────────────────────
  const CSS = `
  .lists-search { margin: 4px 16px 6px; }
  .lists-search__in { padding: 10px 14px; font-size: 15px; }
  .lists-typegrid { display:grid; grid-template-columns: repeat(auto-fill, minmax(96px,1fr));
    gap: 8px; padding: 8px 16px 2px; }
  .lists-typetile { display:flex; flex-direction:column; align-items:center; gap:4px;
    background: var(--tg-surface); border:1px solid transparent; border-radius: var(--radius);
    padding: 12px 6px; color: var(--tg-text); }
  .lists-typetile:active { background: var(--tg-surface-2); }
  .lists-typetile--on { border-color: var(--tg-accent); background: var(--tg-surface-hi); }
  .lists-typetile__e { font-size: 22px; }
  .lists-typetile__l { font-size: 12px; color: var(--tg-text-2); }

  .lists-listrow__emoji { font-size: 22px; width: 30px; text-align:center; }
  .lists-counts { display:flex; gap:8px; align-items:center; font-size:12px; }
  .lists-counts__open { color: var(--tg-text-2); }
  .lists-counts__done { color: var(--success); }
  .lists-scope { font-size: 11px; padding: 2px 7px; border-radius: 6px;
    background: var(--tg-surface-hi); color: var(--tg-text-2); }
  .lists-scope--personal { background: rgba(183,158,255,0.16); color: var(--carbs); }

  .lists-tree { margin: 6px 16px 0; border-radius: var(--radius); overflow:hidden; }
  .lists-item { display:flex; align-items:flex-start; gap:10px; padding: 11px 14px;
    background: var(--tg-surface); position:relative; }
  .lists-item + .lists-item { border-top: 1px solid var(--tg-separator); }
  .lists-item:active { background: var(--tg-surface-2); }
  .lists-check { flex-shrink:0; width:24px; height:24px; border-radius:50%;
    border: 2px solid var(--tg-separator-strong); background:transparent;
    display:flex; align-items:center; justify-content:center; padding:0; margin-top:1px; color:#fff; }
  .lists-check--done { background: var(--success); border-color: var(--success); }
  .lists-item__body { flex:1; min-width:0; }
  .lists-item__text { font-size: 15px; line-height:1.3; word-break: break-word; }
  .lists-item__text--done { color: var(--tg-text-3); text-decoration: line-through; }
  .lists-item__meta { font-size:12px; color: var(--tg-text-2); margin-top:3px;
    display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .lists-gift-badge { font-size:11px; padding:1px 6px; border-radius:5px;
    background: rgba(245,165,36,0.16); color: var(--kcal); }
  .lists-item__kids-btn { background:none; border:none; color: var(--tg-text-2);
    font-size:12px; padding:2px 0; display:inline-flex; align-items:center; gap:3px; }
  .lists-item__add { background:none; border:none; color: var(--tg-accent);
    font-size:12px; padding:2px 0; display:inline-flex; align-items:center; gap:3px; }
  .lists-children { padding-left: 22px; }

  .lists-toggle-row { display:flex; align-items:center; justify-content:space-between;
    padding: 12px 14px; background: var(--tg-surface); border-radius: var(--radius-sm); margin: 12px 0; }
  .lists-toggle-row__t { font-size:14px; font-weight:500; }
  .lists-toggle-row__h { font-size:12px; color: var(--tg-text-2); margin-top:2px; }
  .lists-switch { width:46px; height:28px; border-radius:14px; border:none; position:relative;
    background: var(--tg-separator-strong); flex-shrink:0; transition: background .15s; }
  .lists-switch--on { background: var(--kcal); }
  .lists-switch__knob { position:absolute; top:3px; left:3px; width:22px; height:22px;
    border-radius:50%; background:#fff; transition: left .15s; }
  .lists-switch--on .lists-switch__knob { left:21px; }

  .lists-attrgrid { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
  .lists-attrgrid .sk-field { margin: 6px 0; }
  .lists-attrgrid--full { grid-column: 1 / -1; }

  .lists-emoji-row { display:flex; flex-wrap:wrap; gap:8px; }
  .lists-emoji-pick { width:40px; height:40px; border-radius:10px; border:1px solid var(--tg-separator-strong);
    background: var(--tg-surface); font-size:20px; display:flex; align-items:center; justify-content:center; }
  .lists-emoji-pick--on { border-color: var(--tg-accent); background: var(--tg-surface-hi); }

  .lists-an-card { background: var(--tg-surface); border-radius: var(--radius); margin: 8px 16px; padding: 16px; }
  .lists-an-big { font-size: 30px; font-weight: 700; letter-spacing:-0.5px; }
  .lists-an-sub { font-size: 13px; color: var(--tg-text-2); margin-top:2px; }
  .lists-an-bars { margin-top:14px; display:flex; flex-direction:column; gap:8px; }
  .lists-an-bar { display:grid; grid-template-columns: 64px 1fr 30px; gap:8px; align-items:center; font-size:12px; }
  .lists-an-bar__track { height:8px; background: rgba(255,255,255,0.08); border-radius:4px; overflow:hidden; }
  .lists-an-bar__fill { height:100%; background: var(--tg-accent); border-radius:4px; }
  .lists-an-bar__lbl { color: var(--tg-text-2); }
  .lists-an-bar__n { text-align:right; font-weight:600; }

  .lists-hist-row { display:flex; gap:10px; padding: 11px 14px; background: var(--tg-surface); }
  .lists-hist-row + .lists-hist-row { border-top:1px solid var(--tg-separator); }
  .lists-hist-row__a { font-size:14px; font-weight:500; }
  .lists-hist-row__d { font-size:12px; color: var(--tg-text-2); }
  .lists-hist-row__t { font-size:12px; color: var(--tg-text-3); margin-left:auto; white-space:nowrap; }

  .lists-srch-kind { font-size:11px; padding:1px 6px; border-radius:5px; background: var(--tg-surface-hi);
    color: var(--tg-text-2); margin-left:6px; }

  /* Фильтр по типу — горизонтально-скроллящийся ряд чипов (категорий больше, чем влезает
     в равноширокий Segmented; .tabbar их обрезал). */
  .lists-typebar { display:flex; gap:8px; overflow-x:auto; padding: 2px 16px 8px;
    scrollbar-width:none; -webkit-overflow-scrolling:touch; }
  .lists-typebar::-webkit-scrollbar { display:none; }
  .lists-typechip { flex:0 0 auto; display:inline-flex; align-items:center; gap:6px;
    padding: 7px 13px; border-radius:999px; font-size:13px; font-weight:500;
    white-space:nowrap; background: var(--tg-surface); color: var(--tg-text-2);
    border:1px solid var(--tg-separator-strong); }
  .lists-typechip--on { background: var(--tg-surface-hi); color: var(--tg-text);
    border-color: var(--tg-accent); }
  .lists-typechip__e { font-size:15px; line-height:1; }
  `;
  if (!document.getElementById('lists-styles')) {
    const st = document.createElement('style');
    st.id = 'lists-styles';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ─── Переключатель (тумблер «скрыть от партнёра») ────────────────────────────
  function Switch({ on, onClick }) {
    return (
      <button className={'lists-switch ' + (on ? 'lists-switch--on' : '')} onClick={onClick} type="button">
        <span className="lists-switch__knob" />
      </button>
    );
  }

  // ─── Шит создания/редактирования списка ──────────────────────────────────────
  function ListEditSheet({ open, initial, defaultType, onClose, onSubmit, onDelete }) {
    const editing = !!(initial && initial.id);
    const [type, setType] = useState('task');
    const [name, setName] = useState('');
    const [emoji, setEmoji] = useState('');
    const [scope, setScope] = useState('family');
    const [busy, setBusy] = useState(false);

    useEffect(() => {
      if (!open) return;
      // При создании предвыбираем тип активного фильтра категории (defaultType);
      // при редактировании — тип самого списка.
      setType(initial && initial.type ? initial.type : (defaultType || 'task'));
      setName(initial && initial.name ? initial.name : '');
      setEmoji(initial && initial.emoji ? initial.emoji : '');
      setScope(initial && initial.scope ? initial.scope : 'family');
      setBusy(false);
    }, [open]);

    const submit = async () => {
      if (!name.trim() || busy) return;
      setBusy(true);
      try {
        if (editing) {
          await onSubmit({ name: name.trim(), emoji: emoji || null, scope });
        } else {
          await onSubmit({ type, name: name.trim(), emoji: emoji || null, scope });
        }
      } catch (e) {
        setBusy(false); // ошибку покажет родитель тостом; не теряем ввод
      }
    };

    return (
      <Sheet open={open} title={editing ? 'Изменить список' : 'Новый список'} onClose={onClose}
        footer={<PrimaryButton onClick={submit} loading={busy} disabled={!name.trim()}>
          {editing ? 'Сохранить' : 'Создать'}
        </PrimaryButton>}>
        {!editing && (
          <Field label="Тип">
            <div className="lists-typegrid">
              {TYPES.map((t) => (
                <button key={t.v} type="button"
                  className={'lists-typetile ' + (type === t.v ? 'lists-typetile--on' : '')}
                  onClick={() => setType(t.v)}>
                  <span className="lists-typetile__e">{t.emoji}</span>
                  <span className="lists-typetile__l">{t.label}</span>
                </button>
              ))}
            </div>
          </Field>
        )}
        <Field label="Название">
          <TextInput value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Например, «Продукты»" maxLength={120} autoFocus />
        </Field>
        <Field label="Иконка" hint="Необязательно">
          <div className="lists-emoji-row">
            <button type="button"
              className={'lists-emoji-pick ' + (!emoji ? 'lists-emoji-pick--on' : '')}
              onClick={() => setEmoji('')}>—</button>
            {EMOJI_CHOICES.map((e) => (
              <button key={e} type="button"
                className={'lists-emoji-pick ' + (emoji === e ? 'lists-emoji-pick--on' : '')}
                onClick={() => setEmoji(e)}>{e}</button>
            ))}
          </div>
        </Field>
        <Field label="Видимость"
          hint={scope === 'personal' ? 'Личный список — партнёр его не видит.' : 'Семейный — виден обоим. Отдельные элементы можно скрыть (подарки).'}>
          <Segmented value={scope} onChange={setScope}
            tabs={[{ value: 'family', label: 'Семейный' }, { value: 'personal', label: 'Личный' }]} />
        </Field>
        {editing && onDelete && (
          <div style={{ marginTop: 18 }}>
            <GhostButton danger onClick={onDelete}><IconTrash size={16} /> Удалить список</GhostButton>
          </div>
        )}
      </Sheet>
    );
  }

  // ─── Шит создания/редактирования элемента ────────────────────────────────────
  function ItemEditSheet({ open, listType, listScope, partner, parentId, initial, onClose, onSubmit }) {
    const editing = !!(initial && initial.id);
    const schema = ATTR_SCHEMA[listType] || [];
    const [text, setText] = useState('');
    const [form, setForm] = useState({});
    const [hide, setHide] = useState(false);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
      if (!open) return;
      setText(initial && initial.text ? initial.text : '');
      const f = {};
      const a = initial && initial.specific_attrs;
      if (a) for (const fld of schema) if (a[fld.k] != null) f[fld.k] = String(a[fld.k]);
      setForm(f);
      // hidden_from содержит партнёра → режим подарка (только family-список)
      const hf = (initial && initial.hidden_from) || [];
      setHide(!!partner && hf.indexOf(partner.user_id) !== -1);
      setBusy(false);
    }, [open]);

    const giftAllowed = listScope === 'family' && !!partner;

    const submit = async () => {
      if (!text.trim() || busy) return;
      setBusy(true);
      const body = { text: text.trim(), specific_attrs: buildAttrs(listType, form) };
      if (giftAllowed) body.hidden_from = hide ? [partner.user_id] : [];
      if (!editing && parentId != null) body.parent_id = parentId;
      try {
        await onSubmit(body);
      } catch (e) {
        setBusy(false);
      }
    };

    const setField = (k, v) => setForm((p) => Object.assign({}, p, { [k]: v }));

    return (
      <Sheet open={open}
        title={editing ? 'Изменить элемент' : (parentId != null ? 'Подэлемент' : 'Новый элемент')}
        onClose={onClose}
        footer={<PrimaryButton onClick={submit} loading={busy} disabled={!text.trim()}>
          {editing ? 'Сохранить' : 'Добавить'}
        </PrimaryButton>}>
        <Field label="Текст">
          <TextArea value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Что добавить?" autoFocus />
        </Field>
        {schema.length > 0 && (
          <div className="lists-attrgrid">
            {schema.map((f) => (
              <div key={f.k} className={schema.length % 2 === 1 && f === schema[schema.length - 1] ? 'lists-attrgrid--full' : ''}>
                <Field label={f.label}>
                  {f.kind === 'date' ? (
                    <TextInput type="date" value={form[f.k] || ''} onChange={(e) => setField(f.k, e.target.value)} />
                  ) : f.kind === 'num' ? (
                    <TextInput type="number" inputMode="decimal" value={form[f.k] || ''} onChange={(e) => setField(f.k, e.target.value)} />
                  ) : (
                    <TextInput value={form[f.k] || ''} onChange={(e) => setField(f.k, e.target.value)} />
                  )}
                </Field>
              </div>
            ))}
          </div>
        )}
        {giftAllowed && (
          <div className="lists-toggle-row">
            <div>
              <div className="lists-toggle-row__t">Скрыть от {partner.first_name || 'партнёра'}</div>
              <div className="lists-toggle-row__h">Режим подарка — партнёр элемент не увидит</div>
            </div>
            <Switch on={hide} onClick={() => setHide((v) => !v)} />
          </div>
        )}
      </Sheet>
    );
  }

  // ─── Узел дерева элементов (рекурсивно) ──────────────────────────────────────
  function ItemNode({ item, listType, byParent, busyIds, expanded, onToggleExpand,
    onToggleStatus, onTap, onAddChild }) {
    const kids = byParent.get(item.id) || [];
    const hasKids = kids.length > 0;
    const isOpenNode = expanded.has(item.id);
    const done = item.status === 'done';
    const preview = attrPreview(listType, item.specific_attrs);
    const isGift = (item.hidden_from || []).length > 0;
    const busy = busyIds.has(item.id);

    return (
      <React.Fragment>
        <div className="lists-item">
          <button className={'lists-check ' + (done ? 'lists-check--done' : '')}
            disabled={busy} onClick={() => onToggleStatus(item)} type="button" aria-label="Отметить">
            {busy ? <Spinner size={14} /> : (done ? <IconCheck size={15} /> : null)}
          </button>
          <div className="lists-item__body" onClick={() => onTap(item)}>
            <div className={'lists-item__text ' + (done ? 'lists-item__text--done' : '')}>{item.text}</div>
            <div className="lists-item__meta">
              {isGift && <span className="lists-gift-badge">🎁 скрыт от партнёра</span>}
              {preview && <span>{preview}</span>}
            </div>
            <div className="lists-item__meta" onClick={(e) => e.stopPropagation()}>
              {hasKids && (
                <button className="lists-item__kids-btn" type="button" onClick={() => onToggleExpand(item.id)}>
                  {isOpenNode ? <IconChevDown size={14} /> : <IconChevR size={14} />}
                  {kids.length} {isOpenNode ? 'скрыть' : 'показать'}
                </button>
              )}
              <button className="lists-item__add" type="button" onClick={() => onAddChild(item)}>
                <IconPlus size={13} /> подпункт
              </button>
            </div>
          </div>
        </div>
        {hasKids && isOpenNode && (
          <div className="lists-children">
            {kids.map((k) => (
              <ItemNode key={k.id} item={k} listType={listType} byParent={byParent}
                busyIds={busyIds} expanded={expanded} onToggleExpand={onToggleExpand}
                onToggleStatus={onToggleStatus} onTap={onTap} onAddChild={onAddChild} />
            ))}
          </div>
        )}
      </React.Fragment>
    );
  }

  // ─── Экран истории элемента ──────────────────────────────────────────────────
  function HistoryView({ itemId, onBack }) {
    const { data, loading, error, reload } = useAsyncData(() => ListsAPI.itemHistory(itemId), [itemId]);
    useTgBackButton(true, onBack);
    return (
      <div className="app">
        <TgHeader title="История" onBack={onBack} />
        <div className="scroll"><div className="page-enter">
          {loading ? <Loader label="Загрузка истории…" />
            : error ? <ErrorState error={error} onRetry={reload} />
            : !data || data.length === 0
              ? <EmptyState icon="🕓" title="Истории пока нет" hint="Изменения элемента появятся здесь." />
              : (
                <div className="lists-tree">
                  {data.map((h, i) => (
                    <div className="lists-hist-row" key={i}>
                      <div>
                        <div className="lists-hist-row__a">{HISTORY_LABEL[h.action] || h.action}</div>
                        {h.detail && <div className="lists-hist-row__d">{h.detail}</div>}
                      </div>
                      <div className="lists-hist-row__t">{fmtDateTime(h.at)}</div>
                    </div>
                  ))}
                </div>
              )}
        </div></div>
      </div>
    );
  }

  // ─── Экран аналитики по типу ─────────────────────────────────────────────────
  function AnalyticsView({ type, onBack }) {
    const [range, setRange] = useState('30');
    const meta = typeMeta(type);
    const { from, to } = useMemo(() => {
      if (range === 'all') return { from: undefined, to: undefined };
      return { from: isoDaysAgo(range === '7' ? 7 : range === '30' ? 30 : 365), to: isoToday() };
    }, [range]);
    const { data, loading, error, reload } = useAsyncData(
      () => ListsAPI.analytics(type, from, to), [type, from, to]);
    useTgBackButton(true, onBack);

    const byPeriod = (data && data.by_period) || [];
    const maxN = byPeriod.reduce((m, p) => Math.max(m, p.done || 0), 0) || 1;

    return (
      <div className="app">
        <TgHeader title={'Аналитика · ' + meta.label} onBack={onBack} />
        <Segmented value={range} onChange={setRange} tabs={[
          { value: '7', label: 'Неделя' }, { value: '30', label: 'Месяц' },
          { value: '365', label: 'Год' }, { value: 'all', label: 'Всё' },
        ]} />
        <div className="scroll"><div className="page-enter">
          {loading ? <Loader label="Считаем…" />
            : error ? <ErrorState error={error} onRetry={reload} />
            : (
              <React.Fragment>
                <div className="lists-an-card">
                  <div className="lists-an-big">{data.done} / {data.total}</div>
                  <div className="lists-an-sub">Выполнено из всех видимых · {meta.label.toLowerCase()}</div>
                  {byPeriod.length > 0 && (
                    <div className="lists-an-bars">
                      {byPeriod.map((p) => (
                        <div className="lists-an-bar" key={p.period}>
                          <span className="lists-an-bar__lbl">{p.period}</span>
                          <span className="lists-an-bar__track">
                            <span className="lists-an-bar__fill" style={{ width: Math.round((p.done / maxN) * 100) + '%' }} />
                          </span>
                          <span className="lists-an-bar__n">{p.done}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {data.total === 0 && (
                  <EmptyState icon="📊" title="Нет данных за период" hint="Отметь элементы выполненными — они появятся в аналитике." />
                )}
              </React.Fragment>
            )}
        </div></div>
      </div>
    );
  }

  // ─── Экран детали списка ─────────────────────────────────────────────────────
  function ListDetailView({ listId, partner, onBack, onAfterDelete }) {
    const { data, loading, error, reload, setData } = useAsyncData(() => ListsAPI.getList(listId), [listId]);
    const { toast, showToast } = useToast();
    const [expanded, setExpanded] = useState(new Set());
    const [busyIds, setBusyIds] = useState(new Set());
    const [sub, setSub] = useState(null); // 'history' | 'analytics'
    const [histItemId, setHistItemId] = useState(null);
    const [itemSheet, setItemSheet] = useState(null); // {parentId?} | {item}
    const [listSheet, setListSheet] = useState(false);
    const [confirm, setConfirm] = useState(null); // {kind, ...}
    const [confirmBusy, setConfirmBusy] = useState(false);

    const lst = data;
    const items = (data && data.items) || [];
    const byParent = useMemo(() => buildTree(items), [items]);
    const roots = byParent.get('root') || [];
    const meta = lst ? typeMeta(lst.type) : null;
    const doneCount = lst ? lst.counts.done : 0;

    // BackButton: подэкраны управляют сами; здесь — назад к обзору.
    const noSub = !sub && histItemId == null;
    useTgBackButton(noSub, onBack);

    // Тихий рефетч после мутаций: обновляет data без полноэкранного лоадера
    // (screen-level reload включает loading=true и мигал бы Loader'ом).
    const refresh = useCallback(async () => {
      try {
        const fresh = await ListsAPI.getList(listId);
        setData(fresh);
      } catch (e) { /* ошибку мутации уже показали тостом; список не трогаем */ }
    }, [listId, setData]);

    const mark = (id, on) => setBusyIds((s) => {
      const n = new Set(s); if (on) n.add(id); else n.delete(id); return n;
    });

    const toggleExpand = useCallback((id) => setExpanded((s) => {
      const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n;
    }), []);

    // Обновить один элемент в локальном дереве (оптимистично/после ответа).
    const replaceItem = (updated) => setData((d) => {
      if (!d) return d;
      return Object.assign({}, d, {
        items: d.items.map((it) => (it.id === updated.id ? updated : it)),
      });
    });

    const toggleStatus = async (item) => {
      const next = item.status === 'done' ? 'open' : 'done';
      mark(item.id, true);
      try {
        const upd = await ListsAPI.patchItem(item.id, { status: next });
        replaceItem(upd);
        await refresh(); // counts на списке пересчитываются на бэке
      } catch (e) {
        showToast(e.message || 'Не удалось обновить');
      } finally {
        mark(item.id, false);
      }
    };

    const submitNewItem = async (body) => {
      try {
        await ListsAPI.addItem(listId, body);
        setItemSheet(null);
        await refresh();
        showToast('Добавлено');
      } catch (e) { showToast(e.message || 'Не удалось добавить'); throw e; }
    };
    const submitEditItem = async (body) => {
      try {
        await ListsAPI.patchItem(itemSheet.item.id, body);
        setItemSheet(null);
        await refresh();
        showToast('Сохранено');
      } catch (e) { showToast(e.message || 'Не удалось сохранить'); throw e; }
    };
    const submitEditList = async (body) => {
      try {
        const upd = await ListsAPI.patchList(listId, body);
        setData((d) => d ? Object.assign({}, d, upd) : d);
        setListSheet(false);
        showToast('Сохранено');
      } catch (e) { showToast(e.message || 'Не удалось сохранить'); throw e; }
    };

    const doConfirm = async () => {
      setConfirmBusy(true);
      try {
        if (confirm.kind === 'delItem') {
          await ListsAPI.deleteItem(confirm.item.id);
          await refresh();
          showToast('Удалено');
        } else if (confirm.kind === 'clearDone') {
          const r = await ListsAPI.clearDone(listId);
          await refresh();
          showToast('Удалено отмеченных: ' + (r && r.deleted != null ? r.deleted : 0));
        } else if (confirm.kind === 'delList') {
          await ListsAPI.deleteList(listId);
          onAfterDelete();
          return;
        }
        setConfirm(null);
      } catch (e) {
        showToast(e.message || 'Не удалось выполнить');
      } finally {
        setConfirmBusy(false);
      }
    };

    // Тап по элементу → меню действий (через простой шит-выбор).
    const [actionFor, setActionFor] = useState(null);

    if (histItemId != null) {
      return <HistoryView itemId={histItemId} onBack={() => setHistItemId(null)} />;
    }
    if (sub === 'analytics' && lst) {
      return <AnalyticsView type={lst.type} onBack={() => setSub(null)} />;
    }

    if (loading) {
      return (
        <div className="app">
          <TgHeader title="Список" onBack={onBack} />
          <div className="scroll"><Loader label="Загрузка списка…" /></div>
        </div>
      );
    }
    if (error) {
      return (
        <div className="app">
          <TgHeader title="Список" onBack={onBack} />
          <div className="scroll"><div className="page-enter"><ErrorState error={error} onRetry={reload} /></div></div>
        </div>
      );
    }

    return (
      <div className="app">
        <TgHeader
          title={(lst.emoji ? lst.emoji + ' ' : (meta ? meta.emoji + ' ' : '')) + lst.name}
          sub={meta ? meta.label : null}
          onBack={onBack}
          right={<button className="tg-header__action" onClick={() => setListSheet(true)} aria-label="Настройки списка"><IconGear size={20} /></button>}
        />
        <div className="scroll">
          <div className="page-enter">
            <SectionTitle right={
              <span onClick={() => setSub('analytics')} style={{ cursor: 'pointer' }}>Аналитика</span>
            }>
              {lst.counts.open} открыто · {lst.counts.done} готово
            </SectionTitle>

            {roots.length === 0 ? (
              <EmptyState icon={meta ? meta.emoji : '📝'} title="Список пуст"
                hint={'Добавь первый элемент кнопкой ниже.'} />
            ) : (
              <div className="lists-tree">
                {roots.map((it) => (
                  <ItemNode key={it.id} item={it} listType={lst.type} byParent={byParent}
                    busyIds={busyIds} expanded={expanded} onToggleExpand={toggleExpand}
                    onToggleStatus={toggleStatus} onTap={(item) => setActionFor(item)}
                    onAddChild={(item) => setItemSheet({ parentId: item.id })} />
                ))}
              </div>
            )}

            {doneCount > 0 && (
              <div style={{ padding: '14px 16px' }}>
                <GhostButton danger onClick={() => setConfirm({ kind: 'clearDone' })}>
                  <IconTrash size={16} /> Очистить отмеченные ({doneCount})
                </GhostButton>
              </div>
            )}
          </div>
        </div>

        <Fab onClick={() => setItemSheet({ parentId: null })}><IconPlus size={20} /> Добавить</Fab>

        {/* Меню действий по элементу */}
        <Sheet open={!!actionFor} title={actionFor ? actionFor.text : ''} onClose={() => setActionFor(null)}>
          <div className="lists-tree" style={{ margin: '4px 0' }}>
            <Row left={<IconEdit size={18} />} title="Изменить"
              onClick={() => { const it = actionFor; setActionFor(null); setItemSheet({ item: it }); }} />
            <Row left={<IconPlus size={18} />} title="Добавить подпункт"
              onClick={() => { const it = actionFor; setActionFor(null); setItemSheet({ parentId: it.id }); }} />
            <Row left={<IconCalendar size={18} />} title="История"
              onClick={() => { const it = actionFor; setActionFor(null); setHistItemId(it.id); }} />
            <Row left={<IconTrash size={18} />} title="Удалить" accent="var(--danger)"
              onClick={() => { const it = actionFor; setActionFor(null); setConfirm({ kind: 'delItem', item: it }); }} />
          </div>
        </Sheet>

        <ItemEditSheet
          open={!!itemSheet}
          listType={lst.type}
          listScope={lst.scope}
          partner={partner}
          parentId={itemSheet ? itemSheet.parentId : null}
          initial={itemSheet ? itemSheet.item : null}
          onClose={() => setItemSheet(null)}
          onSubmit={itemSheet && itemSheet.item ? submitEditItem : submitNewItem}
        />

        <ListEditSheet
          open={listSheet}
          initial={lst}
          onClose={() => setListSheet(false)}
          onSubmit={submitEditList}
          onDelete={() => { setListSheet(false); setConfirm({ kind: 'delList' }); }}
        />

        <ConfirmDialog
          open={!!confirm}
          danger
          busy={confirmBusy}
          title={confirm && confirm.kind === 'delItem' ? 'Удалить элемент?'
            : confirm && confirm.kind === 'clearDone' ? 'Очистить отмеченные?'
            : 'Удалить список?'}
          message={confirm && confirm.kind === 'delItem' ? 'Элемент и его подпункты будут удалены.'
            : confirm && confirm.kind === 'clearDone' ? 'Будут удалены все отмеченные элементы этого списка.'
            : 'Список и все его элементы будут удалены безвозвратно.'}
          confirmLabel="Удалить"
          onConfirm={doConfirm}
          onCancel={() => setConfirm(null)}
        />

        <Toast show={!!toast}>{toast}</Toast>
      </div>
    );
  }

  // ─── Экран поиска ────────────────────────────────────────────────────────────
  function SearchView({ onBack, onOpenList }) {
    const [q, setQ] = useState('');
    const [hits, setHits] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const timer = useRef(null);
    useTgBackButton(true, onBack);

    const runSearch = useCallback(async (query) => {
      setLoading(true);
      try {
        const r = await ListsAPI.search(query);
        setHits(r); setError(null);
      } catch (e) { setError(e); setHits(null); }
      finally { setLoading(false); }
    }, []);

    useEffect(() => {
      clearTimeout(timer.current);
      const query = q.trim();
      if (!query) { setHits(null); setError(null); setLoading(false); return; }
      timer.current = setTimeout(() => runSearch(query), 300);
      return () => clearTimeout(timer.current);
    }, [q, runSearch]);

    return (
      <div className="app">
        <TgHeader title="Поиск" onBack={onBack} />
        <div className="lists-search">
          <TextInput className="sk-input lists-search__in" value={q} autoFocus
            onChange={(e) => setQ(e.target.value)} placeholder="Поиск по спискам и элементам" />
        </div>
        <div className="scroll"><div className="page-enter">
          {loading ? <Loader label="Ищем…" />
            : error ? <ErrorState error={error} onRetry={() => runSearch(q.trim())} />
            : hits == null ? <EmptyState icon="🔍" title="Что ищем?" hint="Введите запрос — найдём по названиям списков и тексту элементов." />
            : hits.length === 0 ? <EmptyState icon="🔍" title="Ничего не найдено" hint="Попробуйте другой запрос." />
            : (
              <div className="lists-tree">
                {hits.map((h, i) => {
                  const m = typeMeta(h.list.type);
                  if (h.kind === 'list') {
                    return (
                      <Row key={'l' + i} left={<span style={{ fontSize: 20 }}>{m.emoji}</span>}
                        title={<span>{h.list.name}<span className="lists-srch-kind">список</span></span>}
                        sub={m.label} right={<IconChevR size={16} />}
                        onClick={() => onOpenList(h.list.id)} />
                    );
                  }
                  const it = h.item || {};
                  const done = it.status === 'done';
                  return (
                    <Row key={'i' + i} left={<span style={{ fontSize: 18 }}>{done ? '✅' : '○'}</span>}
                      title={<span>{it.text}<span className="lists-srch-kind">{m.label}</span></span>}
                      sub={'в списке «' + h.list.name + '»'} right={<IconChevR size={16} />}
                      onClick={() => onOpenList(h.list.id)} />
                  );
                })}
              </div>
            )}
        </div></div>
      </div>
    );
  }

  // ─── Экран обзора (корень) ───────────────────────────────────────────────────
  function ListsScreen({ onExit }) {
    const [typeFilter, setTypeFilter] = useState(''); // '' = все
    const { data, loading, error, reload } = useAsyncData(
      () => ListsAPI.listLists(typeFilter || null), [typeFilter]);
    const { toast, showToast } = useToast();
    const [partner, setPartner] = useState(null);
    const [openListId, setOpenListId] = useState(null);
    const [searching, setSearching] = useState(false);
    const [createOpen, setCreateOpen] = useState(false);

    // Партнёр (для тумблера подарков). Нет семьи → null (тумблер просто не появится).
    useEffect(() => {
      let alive = true;
      ListsAPI.family().then(
        (fam) => {
          if (!alive) return;
          const p = (fam.members || []).find((m) => !m.is_self);
          setPartner(p || null);
        },
        () => { if (alive) setPartner(null); }
      );
      return () => { alive = false; };
    }, []);

    // Корневой back = выход в хаб; подэкраны управляют BackButton сами.
    const atRoot = openListId == null && !searching;
    useTgBackButton(atRoot, onExit);

    const submitCreate = async (body) => {
      try {
        const created = await ListsAPI.createList(body);
        setCreateOpen(false);
        await reload();
        showToast('Список создан');
        if (created && created.id != null) setOpenListId(created.id);
      } catch (e) { showToast(e.message || 'Не удалось создать'); throw e; }
    };

    if (searching) {
      return <SearchView onBack={() => setSearching(false)}
        onOpenList={(id) => { setSearching(false); setOpenListId(id); }} />;
    }
    if (openListId != null) {
      return <ListDetailView listId={openListId} partner={partner}
        onBack={() => { setOpenListId(null); reload(); }}
        onAfterDelete={() => { setOpenListId(null); reload(); showToast('Список удалён'); }} />;
    }

    const lists = data || [];

    return (
      <div className="app">
        <TgHeader title="Списки" onBack={onExit}
          right={<button className="tg-header__action" onClick={() => setSearching(true)} aria-label="Поиск">
            <SearchIcon /></button>} />
        <div className="lists-typebar">
          <button type="button"
            className={'lists-typechip ' + (typeFilter === '' ? 'lists-typechip--on' : '')}
            onClick={() => setTypeFilter('')}>Все</button>
          {TYPES.map((t) => (
            <button key={t.v} type="button"
              className={'lists-typechip ' + (typeFilter === t.v ? 'lists-typechip--on' : '')}
              onClick={() => setTypeFilter(t.v)}>
              <span className="lists-typechip__e">{t.emoji}</span>{t.label}
            </button>
          ))}
        </div>
        <div className="scroll">
          <div className="page-enter">
            {loading ? <Loader label="Загрузка списков…" />
              : error ? <ErrorState error={error} onRetry={reload} />
              : lists.length === 0 ? (
                <EmptyState icon="📝" title={typeFilter ? 'Здесь пока пусто' : 'Списков пока нет'}
                  hint="Создай первый список кнопкой ниже."
                  action={<PrimaryButton onClick={() => setCreateOpen(true)}>Новый список</PrimaryButton>} />
              ) : (
                <div className="lists-tree">
                  {lists.map((l) => {
                    const m = typeMeta(l.type);
                    return (
                      <Row key={l.id}
                        left={<span className="lists-listrow__emoji">{l.emoji || m.emoji}</span>}
                        title={l.name}
                        sub={
                          <span className="lists-counts">
                            <span className="lists-counts__open">{l.counts.open} открыто</span>
                            {l.counts.done > 0 && <span className="lists-counts__done">{l.counts.done} готово</span>}
                          </span>
                        }
                        right={
                          <React.Fragment>
                            {l.scope === 'personal' && <span className="lists-scope lists-scope--personal">личный</span>}
                            <IconChevR size={16} />
                          </React.Fragment>
                        }
                        onClick={() => setOpenListId(l.id)} />
                    );
                  })}
                </div>
              )}
          </div>
        </div>

        {lists.length > 0 && (
          <Fab onClick={() => setCreateOpen(true)}><IconPlus size={20} /> Новый список</Fab>
        )}

        <ListEditSheet open={createOpen} initial={null} defaultType={typeFilter || null}
          onClose={() => setCreateOpen(false)} onSubmit={submitCreate} />

        <Toast show={!!toast}>{toast}</Toast>
      </div>
    );
  }

  // Иконка поиска (нет в общем наборе icons.jsx — локальный inline SVG).
  function SearchIcon({ size = 20 }) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
      </svg>
    );
  }

  window.ListsScreen = ListsScreen;
})();
