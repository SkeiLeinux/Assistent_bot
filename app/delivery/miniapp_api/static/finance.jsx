// finance.jsx — экран «Финансы» Mini App (волна C, spec-finance).
// React UMD, без сборки. Глобал window.FinanceScreen({ onExit }).
//
// Сценарии (spec-finance §9):
//   S1 Импорт xlsx (файл + год) → отчёт импорта (месяцы / новые категории / ошибки)
//   S2 Повторный импорт месяца (идемпотентно; бэк перезапишет)
//   S3 Аналитика план/факт/разница/% по дереву категорий + drill-down
//   S5 Каденс периода D/W/M/Q/Y + произвольный
//   S4 Доходы по источникам/партнёрам, переходящий остаток, Дельта
//   S6 Ревью категорий (accept новую / alias к канону)
//   + Выгрузка xlsx за период (A7)
//
// Видимость: весь Finance — FAMILY. Скрытого пер-элемента нет. plan_applicable=false ⇒
// план/разница неизвестны → рисуем «—», не ноль. Импорт fail-loud (422) — показываем
// список нераспознанного, ничего не записано.
//
// Сеть — window.apiV1 (пути без /api/v1). Свои методы — FinanceAPI ниже.

(function () {
  const { useState, useEffect, useRef, useCallback, useMemo } = React;

  // ─── API контекста Finance ────────────────────────────────────────────────
  const FinanceAPI = {
    months: () => window.apiV1('/finance/months'),
    categories: (status) =>
      window.apiV1('/finance/categories', { query: status ? { status } : null }),
    review: () => window.apiV1('/finance/categories/review'),
    resolve: (id, body) =>
      window.apiV1('/finance/categories/' + id + '/resolve', { method: 'POST', body }),
    analytics: (query) => window.apiV1('/finance/analytics', { query }),
    income: (query) => window.apiV1('/finance/income', { query }),
    // Импорт: и 200, и 422 несут один шейп (status/months/created_for_review/errors/message).
    // apiV1 на не-2xx бросает ApiError и теряет тело (errors[]), а spec требует показать
    // список нераспознанного — поэтому импорт идёт прямым fetch с тем же initData-заголовком
    // (см. api.js: 'X-Telegram-Init-Data'). Возврат: { ok:boolean, data, status }.
    importFile: async (file, year) => {
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('year', String(year));
      const tg = window.TG;
      const initData = tg ? tg.initData : '';
      let res;
      try {
        res = await fetch('/api/v1/finance/import', {
          method: 'POST',
          headers: { 'X-Telegram-Init-Data': initData },
          body: fd,
        });
      } catch (e) {
        throw new window.ApiError('Нет соединения с сервером', 'network', 0);
      }
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      // 401/403/прочие server-ошибки без import-шейпа — обычная ApiError.
      if (!res.ok && (res.status === 401 || res.status === 403 || !data)) {
        const msg = (data && (data.message || data.error)) || ('Ошибка ' + res.status);
        const code = (data && data.code) || 'server';
        throw new window.ApiError(msg, code, res.status);
      }
      return { ok: res.ok, status: res.status, data: data || {} };
    },
    exportRaw: (query) => window.apiV1('/finance/export', { query, raw: true }),
  };

  // ─── Период: модель + перевод в query бэка ────────────────────────────────
  // Бэк (analytics_service.resolve_period): M требует month=YYYY-MM-01; Q — quarter+year;
  // Y — year; D/W/custom — from/to (D/W бэк сам НЕ выводит, считаем диапазон тут).
  const TODAY = new Date();
  const pad2 = (n) => String(n).padStart(2, '0');
  const isoDate = (d) =>
    d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  const firstOfMonth = (year, month0) => year + '-' + pad2(month0 + 1) + '-01';

  function rangeForPeriod(p) {
    // → { period, query } для apiV1. query содержит только применимые ключи.
    if (p.kind === 'M') {
      return { query: { period: 'M', month: firstOfMonth(p.year, p.month0) } };
    }
    if (p.kind === 'Q') {
      return { query: { period: 'Q', quarter: p.quarter, year: p.year } };
    }
    if (p.kind === 'Y') {
      return { query: { period: 'Y', year: p.year } };
    }
    if (p.kind === 'D') {
      const t = isoDate(TODAY);
      return { query: { period: 'D', from: t, to: t } };
    }
    if (p.kind === 'W') {
      // Текущая неделя Пн–Вс относительно сегодня.
      const d = new Date(TODAY);
      const dow = (d.getDay() + 6) % 7; // 0=Пн
      const from = new Date(d); from.setDate(d.getDate() - dow);
      const to = new Date(from); to.setDate(from.getDate() + 6);
      return { query: { period: 'W', from: isoDate(from), to: isoDate(to) } };
    }
    // custom
    return { query: { period: 'custom', from: p.from, to: p.to } };
  }

  function periodLabel(p) {
    if (p.kind === 'M') return MONTHS[p.month0] + ' ' + p.year;
    if (p.kind === 'Q') return p.quarter + ' кв. ' + p.year;
    if (p.kind === 'Y') return p.year + ' год';
    if (p.kind === 'D') return 'Сегодня';
    if (p.kind === 'W') return 'Эта неделя';
    if (p.kind === 'custom') {
      if (!p.from || !p.to) return 'Свой период';
      return ddmmm(p.from) + ' – ' + ddmmm(p.to);
    }
    return '';
  }

  function defaultPeriod() {
    // По умолчанию — текущий месяц (целый календарный ⇒ план применим).
    return { kind: 'M', year: TODAY.getFullYear(), month0: TODAY.getMonth() };
  }

  // ─── Форматирование денег ─────────────────────────────────────────────────
  // value=null ⇒ «—» (план/разница неизвестны вне целого месяца, I9).
  function money(v) {
    if (v == null) return '—';
    const n = Math.round(Number(v));
    if (isNaN(n)) return '—';
    const sign = n < 0 ? '−' : '';
    const s = String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return sign + s + ' ₽';
  }
  function moneySigned(v) {
    if (v == null) return '—';
    const n = Math.round(Number(v));
    if (isNaN(n)) return '—';
    return (n > 0 ? '+' : n < 0 ? '−' : '') +
      String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₽';
  }
  function pctText(v) {
    if (v == null || isNaN(v)) return '—';
    return Math.round(Number(v)) + '%';
  }
  // Цвет разницы: для расходов перерасход (diff<0, факт>план) — тревожный.
  function diffColor(diff) {
    if (diff == null) return 'var(--tg-text-2)';
    if (diff < 0) return 'var(--danger)';
    if (diff > 0) return 'var(--success)';
    return 'var(--tg-text-2)';
  }

  // ─── Подэкран: Аналитика (план/факт/разница/% + drill-down) ───────────────
  function AnalyticsView({ period }) {
    const q = rangeForPeriod(period).query;
    const { data, loading, error, reload } = useAsyncData(
      () => FinanceAPI.analytics(q),
      [JSON.stringify(q)]
    );

    if (loading) return <Loader label="Загрузка аналитики…" />;
    if (error) return <ErrorState error={error} onRetry={reload} />;
    if (!data) return null;

    const planNA = !data.plan_applicable;
    const lines = data.lines || [];
    const total = data.total || {};

    return (
      <div>
        <Card className="fin-total">
          <div className="fin-total__row">
            <div className="fin-total__cell">
              <div className="fin-total__lbl">План</div>
              <div className="fin-total__val">{money(total.plan)}</div>
            </div>
            <div className="fin-total__cell">
              <div className="fin-total__lbl">Факт</div>
              <div className="fin-total__val">{money(total.fact)}</div>
            </div>
            <div className="fin-total__cell">
              <div className="fin-total__lbl">Разница</div>
              <div className="fin-total__val" style={{ color: diffColor(total.diff) }}>
                {moneySigned(total.diff)}
              </div>
            </div>
          </div>
        </Card>

        {planNA && (
          <div className="fin-note">
            <IconWarn size={15} stroke="#F5BE6A" /> План и разница доступны только за
            целые календарные месяцы — для этого периода показан только факт.
          </div>
        )}

        {lines.length === 0 ? (
          <EmptyState
            icon="📊"
            title="Нет данных за период"
            hint="Импортируйте бюджет или выберите другой период."
          />
        ) : (
          <div className="fin-lines">
            {lines.map((l) => (
              <AnalyticsLine key={l.category_id} line={l} depth={0} planNA={planNA} />
            ))}
          </div>
        )}
      </div>
    );
  }

  function AnalyticsLine({ line, depth, planNA }) {
    const [open, setOpen] = useState(false);
    const kids = line.children || [];
    const hasKids = kids.length > 0;
    return (
      <div className="fin-line-group">
        <div
          className={'fin-line ' + (hasKids ? 'fin-line--tap' : '')}
          style={{ paddingLeft: 16 + depth * 14 }}
          onClick={hasKids ? () => setOpen((v) => !v) : null}
        >
          <div className="fin-line__head">
            {hasKids ? (
              <span className={'fin-line__chev ' + (open ? 'fin-line__chev--open' : '')}>
                <IconChevR size={15} stroke="var(--tg-text-3)" />
              </span>
            ) : (
              <span className="fin-line__dot" />
            )}
            <span className="fin-line__name">{line.name}</span>
            <span className="fin-line__pct">{pctText(line.pct)}</span>
          </div>
          <div className="fin-line__nums">
            <span className="fin-line__plan">{planNA ? '' : 'план ' + money(line.plan)}</span>
            <span className="fin-line__fact">{money(line.fact)}</span>
            {!planNA && (
              <span className="fin-line__diff" style={{ color: diffColor(line.diff) }}>
                {moneySigned(line.diff)}
              </span>
            )}
          </div>
        </div>
        {hasKids && open && (
          <div className="fin-line__kids">
            {kids.map((c) => (
              <AnalyticsLine key={c.category_id} line={c} depth={depth + 1} planNA={planNA} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // ─── Подэкран: Доходы ──────────────────────────────────────────────────────
  const KIND_LABEL = {
    CARRYOVER: 'Переходящий остаток',
    PARTNER: 'Партнёр',
    CASHBACK: 'Кэшбек',
    OTHER: 'Прочее',
  };
  function kindNorm(k) {
    return (k || '').toString().toUpperCase();
  }

  function IncomeView({ period, members }) {
    const q = rangeForPeriod(period).query;
    const { data, loading, error, reload } = useAsyncData(
      () => FinanceAPI.income(q),
      [JSON.stringify(q)]
    );

    if (loading) return <Loader label="Загрузка доходов…" />;
    if (error) return <ErrorState error={error} onRetry={reload} />;
    if (!data) return null;

    const sources = data.by_source || [];
    // partner = user_id (int|null). Имя — из членов семьи, если совпало.
    const nameOf = (uid) => {
      if (uid == null) return null;
      const m = (members || []).find((x) => x.user_id === uid || x.id === uid);
      return m ? (m.first_name || m.name || ('ID ' + uid)) : ('ID ' + uid);
    };

    return (
      <div>
        <Card className="fin-total fin-total--income">
          <div className="fin-total__row">
            <div className="fin-total__cell">
              <div className="fin-total__lbl">Доход</div>
              <div className="fin-total__val">{money(data.total_income)}</div>
            </div>
            <div className="fin-total__cell">
              <div className="fin-total__lbl">Расход</div>
              <div className="fin-total__val">{money(data.total_expense)}</div>
            </div>
            <div className="fin-total__cell">
              <div className="fin-total__lbl">Дельта</div>
              <div className="fin-total__val" style={{ color: diffColor(data.delta) }}>
                {moneySigned(data.delta)}
              </div>
            </div>
          </div>
        </Card>

        {sources.length === 0 ? (
          <EmptyState
            icon="💸"
            title="Нет доходов за период"
            hint="Доходы считаются только за целые месяцы внутри периода."
          />
        ) : (
          <React.Fragment>
            <SectionTitle>Источники</SectionTitle>
            <div className="sk-group">
              {sources.map((s, i) => {
                const k = kindNorm(s.kind);
                const partnerName = k === 'PARTNER' ? nameOf(s.partner) : null;
                const sub = partnerName
                  ? KIND_LABEL[k] + ' · ' + partnerName
                  : (KIND_LABEL[k] || s.kind);
                return (
                  <Row
                    key={i}
                    title={s.source}
                    sub={sub}
                    right={
                      <span className="fin-src__nums">
                        <b>{money(s.fact)}</b>
                        {s.plan != null && <small>план {money(s.plan)}</small>}
                      </span>
                    }
                  />
                );
              })}
            </div>
            <div className="fin-carry">
              Переходящий остаток в периоде: <b>{money(data.carryover)}</b>
            </div>
          </React.Fragment>
        )}
      </div>
    );
  }

  // ─── Подэкран: Ревью категорий ─────────────────────────────────────────────
  function ReviewView({ onResolvedCount }) {
    const { data, loading, error, reload, setData } = useAsyncData(
      () => FinanceAPI.review(),
      []
    );
    const { toast, showToast } = useToast();
    const [busyId, setBusyId] = useState(null);
    const [aliasFor, setAliasFor] = useState(null); // категория, для которой выбираем канон
    const [tree, setTree] = useState(null);
    const [treeLoading, setTreeLoading] = useState(false);

    const openAlias = useCallback(async (cat) => {
      setAliasFor(cat);
      setTreeLoading(true);
      try {
        const t = await FinanceAPI.categories('active');
        setTree(t || []);
      } catch (e) {
        showToast(e.message || 'Не удалось загрузить категории');
        setAliasFor(null);
      } finally {
        setTreeLoading(false);
      }
    }, [showToast]);

    const removeLocal = useCallback((id) => {
      setData((prev) => (prev || []).filter((c) => c.id !== id));
      if (onResolvedCount) onResolvedCount();
    }, [setData, onResolvedCount]);

    const accept = useCallback(async (cat) => {
      setBusyId(cat.id);
      try {
        await FinanceAPI.resolve(cat.id, { action: 'accept' });
        removeLocal(cat.id);
        showToast('Категория «' + cat.name + '» подтверждена');
      } catch (e) {
        showToast(e.message || 'Не удалось подтвердить');
      } finally {
        setBusyId(null);
      }
    }, [removeLocal, showToast]);

    const doAlias = useCallback(async (targetId) => {
      if (!aliasFor) return;
      setBusyId(aliasFor.id);
      try {
        await FinanceAPI.resolve(aliasFor.id, { action: 'alias', target_id: targetId });
        const nm = aliasFor.name;
        removeLocal(aliasFor.id);
        setAliasFor(null);
        showToast('«' + nm + '» связана с каноном');
      } catch (e) {
        showToast(e.message || 'Не удалось связать');
      } finally {
        setBusyId(null);
      }
    }, [aliasFor, removeLocal, showToast]);

    if (loading) return <Loader label="Загрузка очереди…" />;
    if (error) return <ErrorState error={error} onRetry={reload} />;

    const queue = data || [];

    return (
      <div>
        {queue.length === 0 ? (
          <EmptyState
            icon="✅"
            title="Очередь пуста"
            hint="Новые категории из импорта появятся здесь для подтверждения."
          />
        ) : (
          <React.Fragment>
            <div className="fin-note">
              <IconWarn size={15} stroke="#F5BE6A" /> Эти категории встретились впервые при
              импорте. Подтвердите как новые или свяжите с уже существующими.
            </div>
            {queue.map((cat) => (
              <Card key={cat.id} className="fin-review">
                <div className="fin-review__name">{cat.name}</div>
                <div className="fin-review__actions">
                  <PrimaryButton
                    onClick={() => accept(cat)}
                    loading={busyId === cat.id}
                  >
                    Это новая категория
                  </PrimaryButton>
                  <GhostButton
                    onClick={() => openAlias(cat)}
                    disabled={busyId === cat.id}
                  >
                    Связать с существующей
                  </GhostButton>
                </div>
              </Card>
            ))}
          </React.Fragment>
        )}

        <Sheet
          open={!!aliasFor}
          title={aliasFor ? 'Канон для «' + aliasFor.name + '»' : ''}
          onClose={() => setAliasFor(null)}
        >
          {treeLoading ? (
            <Loader label="Загрузка категорий…" />
          ) : (tree && tree.length) ? (
            <div className="fin-pick">
              <CategoryPicker
                nodes={tree}
                disabled={busyId != null}
                onPick={doAlias}
              />
            </div>
          ) : (
            <EmptyState
              icon="📂"
              title="Нет активных категорий"
              hint="Сначала подтвердите хотя бы одну категорию как новую."
            />
          )}
        </Sheet>

        <Toast show={!!toast}>{toast}</Toast>
      </div>
    );
  }

  // Плоский выбор канона из дерева (родитель → подкатегории), для alias.
  function CategoryPicker({ nodes, onPick, disabled, depth }) {
    depth = depth || 0;
    return (
      <div>
        {nodes.map((n) => (
          <React.Fragment key={n.id}>
            <button
              className="fin-pick__item"
              style={{ paddingLeft: 14 + depth * 16 }}
              disabled={disabled}
              onClick={() => onPick(n.id)}
            >
              <span className="fin-pick__name">{n.name}</span>
              <IconChevR size={15} stroke="var(--tg-text-3)" />
            </button>
            {n.children && n.children.length > 0 && (
              <CategoryPicker
                nodes={n.children}
                onPick={onPick}
                disabled={disabled}
                depth={depth + 1}
              />
            )}
          </React.Fragment>
        ))}
      </div>
    );
  }

  // ─── Подэкран: Импорт ──────────────────────────────────────────────────────
  function ImportView({ onImported }) {
    const [year, setYear] = useState(String(TODAY.getFullYear()));
    const [file, setFile] = useState(null);
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null); // ImportResultDTO (ok)
    const [failure, setFailure] = useState(null); // 422-результат (errors+message)
    const { toast, showToast } = useToast();
    const fileRef = useRef(null);

    const yearOptions = useMemo(() => {
      const y = TODAY.getFullYear();
      const arr = [];
      for (let i = y + 1; i >= y - 4; i--) arr.push({ value: String(i), label: String(i) });
      return arr;
    }, []);

    const pickFile = (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) { setFile(f); setResult(null); setFailure(null); }
    };

    const doImport = useCallback(async () => {
      if (!file) { showToast('Сначала выберите файл .xlsx'); return; }
      setBusy(true);
      setResult(null);
      setFailure(null);
      try {
        const res = await FinanceAPI.importFile(file, Number(year));
        if (res.ok) {
          // 200 ok: показываем отчёт (месяцы / новые категории / пропущенные строки).
          setResult(res.data);
          if (onImported) onImported();
          showToast('Импорт завершён');
        } else {
          // 422 fail-loud: структура/инвариант не сошлись, ничего не записано.
          // Тело несёт message + errors[{row,reason,sheet}] — показываем список.
          setFailure({
            message: res.data.message || 'Файл не распознан',
            errors: res.data.errors || [],
            status: res.status,
          });
        }
      } catch (e) {
        // 401/403/сеть — человеческое сообщение из ApiError.
        setFailure({ message: e.message || 'Не удалось импортировать', errors: [], status: e.status });
      } finally {
        setBusy(false);
      }
    }, [file, year, onImported, showToast]);

    return (
      <div>
        <Card>
          <Field label="Год бюджета" hint="Применяется ко всем листам файла.">
            <SelectInput value={year} onChange={setYear} options={yearOptions} />
          </Field>

          <Field label="Файл выгрузки (.xlsx)">
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              style={{ display: 'none' }}
              onChange={pickFile}
            />
            <button className="fin-filebtn" onClick={() => fileRef.current && fileRef.current.click()}>
              <IconFile size={18} stroke="var(--tg-accent)" />
              <span className="fin-filebtn__name">
                {file ? file.name : 'Выбрать файл бюджета'}
              </span>
            </button>
          </Field>

          <div style={{ marginTop: 14 }}>
            <PrimaryButton onClick={doImport} loading={busy} disabled={!file}>
              Импортировать
            </PrimaryButton>
          </div>
        </Card>

        {failure && (
          <div className="fin-fail">
            <div className="fin-fail__head">
              <IconWarn size={18} stroke="var(--danger)" /> Импорт отклонён
            </div>
            <div className="fin-fail__msg">{failure.message}</div>
            <div className="fin-fail__hint">
              Ничего не записано — структура файла или контрольная сумма (родитель = Σ детей)
              не сошлись. Исправьте таблицу и загрузите снова.
            </div>
            {failure.errors && failure.errors.length > 0 && (
              <div className="fin-fail__rows">
                {failure.errors.map((er, i) => (
                  <div key={i} className="fin-fail__row">
                    <span className="fin-fail__rowloc">
                      {(er.sheet ? er.sheet + ' · ' : '') + (er.row != null ? 'стр. ' + er.row : '—')}
                    </span>
                    <span className="fin-fail__rowmsg">{er.reason}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {result && <ImportReport result={result} />}

        <Toast show={!!toast}>{toast}</Toast>
      </div>
    );
  }

  function ImportReport({ result }) {
    const months = result.months || [];
    const review = result.created_for_review || [];
    const errors = result.errors || [];
    return (
      <div className="fin-report">
        <div className="fin-report__head">
          {result.status === 'ok' ? (
            <React.Fragment>
              <IconCheck size={18} stroke="var(--success)" /> Импорт выполнен
            </React.Fragment>
          ) : (
            <React.Fragment>
              <IconWarn size={18} stroke="var(--danger)" /> Импорт не выполнен
            </React.Fragment>
          )}
        </div>

        {months.length > 0 && (
          <React.Fragment>
            <SectionTitle>Импортированные месяцы</SectionTitle>
            <div className="sk-group">
              {months.map((m, i) => (
                <Row
                  key={i}
                  title={monthTitle(m.month)}
                  sub={'категорий: ' + m.categories + ' · источников дохода: ' + m.income}
                />
              ))}
            </div>
          </React.Fragment>
        )}

        {review.length > 0 && (
          <React.Fragment>
            <SectionTitle right={review.length + ' на ревью'}>Новые категории</SectionTitle>
            <div className="sk-group">
              {review.map((c) => (
                <Row key={c.id} title={c.name} sub="требует подтверждения"
                  left={<span className="fin-badge fin-badge--review">new</span>} />
              ))}
            </div>
            <div className="fin-note">
              <IconWarn size={15} stroke="#F5BE6A" /> Подтвердите их во вкладке «Ревью».
            </div>
          </React.Fragment>
        )}

        {errors.length > 0 && (
          <React.Fragment>
            <SectionTitle right={errors.length}>Пропущенные строки</SectionTitle>
            <div className="sk-group">
              {errors.map((er, i) => (
                <Row
                  key={i}
                  title={er.reason}
                  sub={(er.sheet ? 'лист ' + er.sheet + ' · ' : '') + 'строка ' + er.row}
                  left={<span className="fin-badge fin-badge--warn">!</span>}
                />
              ))}
            </div>
          </React.Fragment>
        )}

        {months.length === 0 && review.length === 0 && errors.length === 0 && (
          <EmptyState icon="📄" title="Нечего импортировать" hint="В файле не найдено листов с данными." />
        )}
      </div>
    );
  }

  // month: 'YYYY-MM-DD' или 'YYYY-MM' → «Март 2026».
  function monthTitle(m) {
    if (!m) return '—';
    const parts = String(m).split('-');
    const y = parts[0];
    const mo = parseInt(parts[1], 10) - 1;
    const name = MONTHS[mo] || parts[1];
    return name.charAt(0).toUpperCase() + name.slice(1) + ' ' + y;
  }

  // ─── Селектор периода (для аналитики/доходов) ──────────────────────────────
  function PeriodSelector({ period, onChange }) {
    const [open, setOpen] = useState(false);
    const [kind, setKind] = useState(period.kind);
    const [year, setYear] = useState(period.year || TODAY.getFullYear());
    const [month0, setMonth0] = useState(period.month0 != null ? period.month0 : TODAY.getMonth());
    const [quarter, setQuarter] = useState(period.quarter || 1);
    const [from, setFrom] = useState(period.from || isoDate(TODAY));
    const [to, setTo] = useState(period.to || isoDate(TODAY));

    const years = useMemo(() => {
      const y = TODAY.getFullYear();
      const arr = [];
      for (let i = y + 1; i >= y - 5; i--) arr.push({ value: String(i), label: String(i) });
      return arr;
    }, []);
    const monthOpts = MONTHS.map((m, i) => ({
      value: String(i),
      label: m.charAt(0).toUpperCase() + m.slice(1),
    }));

    const apply = () => {
      let p;
      if (kind === 'M') p = { kind: 'M', year: Number(year), month0: Number(month0) };
      else if (kind === 'Q') p = { kind: 'Q', year: Number(year), quarter: Number(quarter) };
      else if (kind === 'Y') p = { kind: 'Y', year: Number(year) };
      else if (kind === 'D') p = { kind: 'D' };
      else if (kind === 'W') p = { kind: 'W' };
      else p = { kind: 'custom', from, to };
      onChange(p);
      setOpen(false);
    };

    const KINDS = [
      { value: 'D', label: 'День' },
      { value: 'W', label: 'Неделя' },
      { value: 'M', label: 'Месяц' },
      { value: 'Q', label: 'Квартал' },
      { value: 'Y', label: 'Год' },
      { value: 'custom', label: 'Период' },
    ];

    return (
      <React.Fragment>
        <button className="fin-period" onClick={() => setOpen(true)}>
          <IconCalendar size={16} stroke="var(--tg-accent)" />
          <span>{periodLabel(period)}</span>
          <IconChevDown size={16} stroke="var(--tg-text-2)" />
        </button>

        <Sheet
          open={open}
          title="Период"
          onClose={() => setOpen(false)}
          footer={<PrimaryButton onClick={apply}>Показать</PrimaryButton>}
        >
          <Field label="Каденс">
            <div className="fin-kinds">
              {KINDS.map((k) => (
                <Chip key={k.value} active={kind === k.value} onClick={() => setKind(k.value)}>
                  {k.label}
                </Chip>
              ))}
            </div>
          </Field>

          {kind === 'M' && (
            <div className="fin-row2">
              <Field label="Месяц">
                <SelectInput value={String(month0)} onChange={(v) => setMonth0(v)} options={monthOpts} />
              </Field>
              <Field label="Год">
                <SelectInput value={String(year)} onChange={(v) => setYear(v)} options={years} />
              </Field>
            </div>
          )}
          {kind === 'Q' && (
            <div className="fin-row2">
              <Field label="Квартал">
                <SelectInput
                  value={String(quarter)}
                  onChange={(v) => setQuarter(v)}
                  options={[1, 2, 3, 4].map((q) => ({ value: String(q), label: q + ' квартал' }))}
                />
              </Field>
              <Field label="Год">
                <SelectInput value={String(year)} onChange={(v) => setYear(v)} options={years} />
              </Field>
            </div>
          )}
          {kind === 'Y' && (
            <Field label="Год">
              <SelectInput value={String(year)} onChange={(v) => setYear(v)} options={years} />
            </Field>
          )}
          {kind === 'custom' && (
            <div className="fin-row2">
              <Field label="С">
                <input className="sk-input fin-date" type="date" value={from}
                  onChange={(e) => setFrom(e.target.value)} />
              </Field>
              <Field label="По">
                <input className="sk-input fin-date" type="date" value={to}
                  onChange={(e) => setTo(e.target.value)} />
              </Field>
            </div>
          )}
          {(kind === 'D' || kind === 'W') && (
            <div className="fin-note" style={{ margin: '8px 0 0' }}>
              <IconWarn size={15} stroke="#F5BE6A" /> За день/неделю показывается только факт —
              план задаётся помесячно.
            </div>
          )}
        </Sheet>
      </React.Fragment>
    );
  }

  // ─── Выгрузка xlsx за период ───────────────────────────────────────────────
  function ExportButton({ period, showToast }) {
    const [busy, setBusy] = useState(false);
    const onExport = useCallback(async () => {
      const q = rangeForPeriod(period).query;
      setBusy(true);
      try {
        const res = await FinanceAPI.exportRaw(q);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'finance_' + (q.from || q.month || q.year || 'period') + '.xlsx';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 1500);
        if (showToast) showToast('Файл выгружен');
      } catch (e) {
        if (showToast) showToast(e.message || 'Не удалось выгрузить');
      } finally {
        setBusy(false);
      }
    }, [period, showToast]);

    return (
      <button className="fin-export" onClick={onExport} disabled={busy} title="Выгрузить xlsx">
        {busy ? <Spinner size={16} /> : <IconShare size={18} stroke="var(--tg-accent)" />}
      </button>
    );
  }

  // ─── Корень экрана ─────────────────────────────────────────────────────────
  const TABS = [
    { value: 'analytics', label: 'План/факт' },
    { value: 'income', label: 'Доходы' },
    { value: 'review', label: 'Ревью' },
    { value: 'import', label: 'Импорт' },
  ];

  function FinanceScreen({ onExit }) {
    const [tab, setTab] = useState('analytics');
    const [period, setPeriod] = useState(defaultPeriod);
    const [reviewCount, setReviewCount] = useState(null);
    const [importNonce, setImportNonce] = useState(0); // дёргаем перезагрузку ревью после импорта
    const { toast, showToast } = useToast();

    // BackButton Telegram → выход в хаб.
    useTgBackButton(true, onExit);

    // /me — для имён партнёров в доходах и проверки наличия семьи.
    const { data: me } = useAsyncData(() => window.apiV1('/me'), []);
    const members = (me && me.family && me.family.members) || [];
    const noFamily = me && me.family === null;

    // Счётчик ревью для бейджа на вкладке.
    const { data: reviewQ } = useAsyncData(
      () => FinanceAPI.review().catch(() => []),
      [importNonce]
    );
    useEffect(() => {
      if (reviewQ) setReviewCount(reviewQ.length);
    }, [reviewQ]);

    const showPeriodBar = tab === 'analytics' || tab === 'income';

    return (
      <div className="app">
        <TgHeader
          title="Финансы"
          onBack={onExit}
          right={
            showPeriodBar ? <ExportButton period={period} showToast={showToast} /> : null
          }
        />

        <Segmented
          tabs={TABS.map((t) =>
            t.value === 'review' && reviewCount
              ? { value: t.value, label: t.label + ' · ' + reviewCount }
              : t
          )}
          value={tab}
          onChange={setTab}
        />

        {showPeriodBar && (
          <div className="fin-periodbar">
            <PeriodSelector period={period} onChange={setPeriod} />
          </div>
        )}

        <div className="scroll">
          <div className="page-enter">
            {noFamily ? (
              <EmptyState
                icon="👪"
                title="Нужна семья"
                hint="Финансы — общий бюджет семьи. Создайте семью в боте командой /newfamily, затем вернитесь сюда."
              />
            ) : tab === 'analytics' ? (
              <AnalyticsView period={period} />
            ) : tab === 'income' ? (
              <IncomeView period={period} members={members} />
            ) : tab === 'review' ? (
              <ReviewView onResolvedCount={() => setReviewCount((c) => (c ? c - 1 : c))} />
            ) : (
              <ImportView onImported={() => setImportNonce((n) => n + 1)} />
            )}
          </div>
        </div>

        <Toast show={!!toast}>{toast}</Toast>
      </div>
    );
  }

  // ─── Стили (инжект один раз, префикс fin-) ─────────────────────────────────
  const CSS = `
  .fin-periodbar { padding: 2px 16px 8px; }
  .fin-period { display:inline-flex; align-items:center; gap:8px; padding:8px 14px;
    background: var(--tg-surface); border:1px solid var(--tg-separator-strong);
    border-radius: 999px; color: var(--tg-text); font-size:14px; font-weight:500; }
  .fin-period:active { background: var(--tg-surface-2); }
  .fin-export { width:34px; height:34px; border-radius:17px; background:transparent;
    border:none; display:flex; align-items:center; justify-content:center; }
  .fin-export:active { background: var(--tg-surface); }
  .fin-export:disabled { opacity:.5; }

  .fin-total__row { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:8px; }
  .fin-total__cell { text-align:center; }
  .fin-total__lbl { font-size:11px; color: var(--tg-text-2); text-transform:uppercase;
    letter-spacing:.4px; margin-bottom:4px; }
  .fin-total__val { font-size:16px; font-weight:700; letter-spacing:-0.3px; }
  .fin-total--income { background: linear-gradient(150deg, var(--tg-surface), var(--tg-surface-2)); }

  .fin-note { margin: 10px 16px; padding:10px 12px; background: rgba(245,165,36,0.1);
    border:1px solid rgba(245,165,36,0.25); border-radius:12px; font-size:12.5px;
    color:#F5BE6A; display:flex; gap:8px; align-items:flex-start; line-height:1.4; }

  .fin-lines { margin: 6px 16px 16px; border-radius: var(--radius); overflow:hidden;
    background: var(--tg-surface); }
  .fin-line { padding:12px 16px; }
  .fin-lines > .fin-line-group + .fin-line-group { border-top:1px solid var(--tg-separator); }
  .fin-line--tap:active { background: var(--tg-surface-2); }
  .fin-line__head { display:flex; align-items:center; gap:8px; }
  .fin-line__chev { display:flex; transition: transform .15s; flex-shrink:0; }
  .fin-line__chev--open { transform: rotate(90deg); }
  .fin-line__dot { width:5px; height:5px; border-radius:50%; background: var(--tg-text-3);
    margin:0 5px; flex-shrink:0; }
  .fin-line__name { flex:1; font-size:14px; font-weight:500; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .fin-line__pct { font-size:12px; color: var(--tg-text-2); flex-shrink:0; }
  .fin-line__nums { display:flex; align-items:baseline; gap:10px; margin-top:4px;
    padding-left:21px; font-size:13px; }
  .fin-line__plan { color: var(--tg-text-3); font-size:12px; flex:1; }
  .fin-line__fact { font-weight:600; }
  .fin-line__diff { font-weight:600; font-size:12.5px; }
  .fin-line__kids { background: rgba(0,0,0,0.12); }

  .fin-src__nums { display:flex; flex-direction:column; align-items:flex-end; }
  .fin-src__nums b { font-size:14px; font-weight:600; }
  .fin-src__nums small { font-size:11px; color: var(--tg-text-3); margin-top:1px; }
  .fin-carry { margin:10px 16px; font-size:13px; color: var(--tg-text-2); }
  .fin-carry b { color: var(--tg-text); }

  .fin-review { }
  .fin-review__name { font-size:16px; font-weight:600; margin-bottom:12px; }
  .fin-review__actions { display:flex; flex-direction:column; gap:8px; }

  .fin-pick { padding-bottom:8px; }
  .fin-pick__item { display:flex; align-items:center; justify-content:space-between;
    width:100%; padding:13px 14px; background: var(--tg-surface); border:none;
    border-radius: var(--radius-sm); color: var(--tg-text); margin-bottom:6px; text-align:left; }
  .fin-pick__item:active { background: var(--tg-surface-2); }
  .fin-pick__item:disabled { opacity:.5; }
  .fin-pick__name { font-size:15px; font-weight:500; }

  .fin-filebtn { display:flex; align-items:center; gap:10px; width:100%; padding:14px;
    background: var(--tg-bg); border:1.5px dashed var(--tg-separator-strong);
    border-radius: var(--radius-sm); color: var(--tg-text); font-size:14px; text-align:left; }
  .fin-filebtn:active { background: var(--tg-surface-2); }
  .fin-filebtn__name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

  .fin-fail { margin:10px 16px; padding:14px; background: rgba(224,88,88,0.12);
    border:1px solid rgba(224,88,88,0.3); border-radius:14px; }
  .fin-fail__head { display:flex; align-items:center; gap:8px; font-size:15px;
    font-weight:600; color:#F2A0A0; }
  .fin-fail__msg { font-size:13.5px; color: var(--tg-text); margin-top:8px; line-height:1.4; }
  .fin-fail__hint { font-size:12.5px; color: var(--tg-text-2); margin-top:8px; line-height:1.4; }
  .fin-fail__rows { margin-top:10px; border-top:1px solid rgba(224,88,88,0.2); padding-top:8px; }
  .fin-fail__row { display:flex; gap:10px; padding:5px 0; font-size:12.5px; line-height:1.35; }
  .fin-fail__rowloc { flex-shrink:0; color:#F2A0A0; font-weight:600; min-width:84px; }
  .fin-fail__rowmsg { color: var(--tg-text-2); }

  .fin-report { padding-bottom:12px; }
  .fin-report__head { display:flex; align-items:center; gap:8px; margin:14px 16px 4px;
    font-size:15px; font-weight:600; }

  .fin-badge { width:24px; height:24px; border-radius:7px; display:flex; align-items:center;
    justify-content:center; font-size:10px; font-weight:700; text-transform:uppercase; }
  .fin-badge--review { background: rgba(90,180,255,0.18); color: var(--tg-accent); }
  .fin-badge--warn { background: rgba(245,165,36,0.18); color:#F5BE6A; font-size:14px; }

  .fin-kinds { display:flex; gap:6px; flex-wrap:wrap; }
  .fin-row2 { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
  .fin-date { color-scheme: dark; }
  `;

  if (!document.getElementById('fin-styles')) {
    const style = document.createElement('style');
    style.id = 'fin-styles';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  window.FinanceScreen = FinanceScreen;
})();
