// screen-kit.jsx — общий набор для экранов волны C (Lists / Finance / Reminders+Analytics).
// React UMD, без сборки. Всё кладётся в window. Стили инжектятся одним <style> (ниже),
// классы с префиксом sk-; переиспользуют CSS-переменные из styles.css (--tg-*, --radius*).
//
// Что даёт:
//   Хуки:   useTgBackButton(active, handler), useToast(), useAsyncData(loader, deps)
//   UI:     Spinner, EmptyState, ErrorState, Segmented, Card, Sheet, Field, TextInput,
//           TextArea, SelectInput, PrimaryButton, GhostButton, Fab, Chip, ConfirmDialog,
//           SectionTitle, Row
//
// Контракт экрана: компонент-глобал window.<X>Screen({ onExit }); back во внутреннем
// корне зовёт onExit(); сетевые вызовы — через window.apiV1 (api.js).

(function () {
  const { useState, useEffect, useRef, useCallback } = React;

  // ─── Хуки ──────────────────────────────────────────────────────────────────

  // Telegram BackButton: показать на время active, повесить handler, снять при размонтаже.
  function useTgBackButton(active, handler) {
    const cb = useRef(handler);
    cb.current = handler;
    useEffect(() => {
      const tg = window.TG;
      if (!tg || !tg.BackButton) return;
      const onClick = () => { if (cb.current) cb.current(); };
      if (active) tg.BackButton.show(); else tg.BackButton.hide();
      tg.BackButton.onClick(onClick);
      return () => { try { tg.BackButton.offClick(onClick); } catch (e) {} };
    }, [active]);
  }

  // Тост: { toast, showToast }. Рендерить <Toast show={!!toast}>{toast}</Toast> (глобал из components-shared).
  function useToast() {
    const [toast, setToast] = useState(null);
    const timer = useRef(null);
    const showToast = useCallback((msg) => {
      setToast(msg);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setToast(null), 2400);
    }, []);
    useEffect(() => () => clearTimeout(timer.current), []);
    return { toast, showToast };
  }

  // Загрузка данных с loading/error/reload. loader: async () => data. deps — как в useEffect.
  function useAsyncData(loader, deps) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const loaderRef = useRef(loader);
    loaderRef.current = loader;
    const reload = useCallback(async () => {
      setLoading(true);
      setError(null);
      try {
        const d = await loaderRef.current();
        setData(d);
      } catch (e) {
        setError(e);
      } finally {
        setLoading(false);
      }
    }, []);
    useEffect(() => { reload(); /* eslint-disable-next-line */ }, deps || []);
    return { data, loading, error, reload, setData };
  }

  // ─── Примитивы ──────────────────────────────────────────────────────────────

  function Spinner({ size = 22 }) {
    return <span className="sk-spinner" style={{ width: size, height: size }} />;
  }

  function EmptyState({ icon, title, hint, action }) {
    return (
      <div className="sk-empty">
        {icon && <div className="sk-empty__icon">{icon}</div>}
        <div className="sk-empty__title">{title}</div>
        {hint && <div className="sk-empty__hint">{hint}</div>}
        {action && <div className="sk-empty__action">{action}</div>}
      </div>
    );
  }

  // ErrorState — человеческое сообщение из ApiError.message (ru) + retry.
  function ErrorState({ error, onRetry }) {
    const msg = (error && (error.message || String(error))) || 'Что-то пошло не так';
    const code = error && error.status;
    return (
      <div className="sk-error">
        <div className="sk-error__icon"><IconWarn size={26} stroke="var(--danger)" /></div>
        <div className="sk-error__title">{msg}</div>
        {code === 401 && <div className="sk-error__hint">Открой приложение из Telegram — нужна авторизация.</div>}
        {code === 403 && <div className="sk-error__hint">Нет доступа: ты не в списке разрешённых пользователей.</div>}
        {onRetry && <button className="sk-btn sk-btn--ghost" onClick={onRetry}><IconRetry size={16} /> Повторить</button>}
      </div>
    );
  }

  function Segmented({ tabs, value, onChange }) {
    // tabs: [{ value, label }]. Переиспользует .tabbar из styles.css.
    return (
      <div className="tabbar">
        {tabs.map((t) => (
          <button key={t.value}
            className={'tabbar__btn ' + (t.value === value ? 'tabbar__btn--active' : '')}
            onClick={() => onChange(t.value)}>
            {t.label}
          </button>
        ))}
      </div>
    );
  }

  function SectionTitle({ children, right }) {
    return (
      <div className="sk-section">
        <span className="sk-section__t">{children}</span>
        {right && <span className="sk-section__r">{right}</span>}
      </div>
    );
  }

  function Card({ children, onClick, className }) {
    return (
      <div className={'sk-card ' + (onClick ? 'sk-card--tap ' : '') + (className || '')}
        onClick={onClick}>
        {children}
      </div>
    );
  }

  function Row({ left, title, sub, right, onClick, accent }) {
    return (
      <div className={'sk-row ' + (onClick ? 'sk-row--tap' : '')} onClick={onClick}>
        {left != null && <div className="sk-row__left">{left}</div>}
        <div className="sk-row__main">
          <div className="sk-row__title" style={accent ? { color: accent } : null}>{title}</div>
          {sub != null && <div className="sk-row__sub">{sub}</div>}
        </div>
        {right != null && <div className="sk-row__right">{right}</div>}
      </div>
    );
  }

  function Chip({ children, active, onClick, color }) {
    return (
      <button className={'sk-chip ' + (active ? 'sk-chip--on' : '')}
        style={active && color ? { background: color, borderColor: color, color: '#fff' } : null}
        onClick={onClick}>
        {children}
      </button>
    );
  }

  // Bottom sheet / модалка. open управляется снаружи; onClose по фону и крестику.
  function Sheet({ open, title, onClose, children, footer }) {
    if (!open) return null;
    return (
      <div className="sk-sheet-overlay" onClick={onClose}>
        <div className="sk-sheet" onClick={(e) => e.stopPropagation()}>
          <div className="sk-sheet__head">
            <div className="sk-sheet__title">{title}</div>
            <button className="sk-sheet__close" onClick={onClose}><IconClose size={20} /></button>
          </div>
          <div className="sk-sheet__body">{children}</div>
          {footer && <div className="sk-sheet__footer">{footer}</div>}
        </div>
      </div>
    );
  }

  function Field({ label, hint, children, accent }) {
    return (
      <label className="sk-field">
        {label && <div className="sk-field__label" style={accent ? { color: accent } : null}>{label}</div>}
        {children}
        {hint && <div className="sk-field__hint">{hint}</div>}
      </label>
    );
  }

  function TextInput(props) {
    return <input className="sk-input" {...props} />;
  }
  function TextArea(props) {
    return <textarea className="sk-input sk-input--area" {...props} />;
  }
  function SelectInput({ value, onChange, options, ...rest }) {
    // options: [{ value, label }]
    return (
      <select className="sk-input sk-select" value={value}
        onChange={(e) => onChange(e.target.value)} {...rest}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  }

  function PrimaryButton({ onClick, loading, disabled, children, danger }) {
    return (
      <button className={'sk-btn sk-btn--primary ' + (danger ? 'sk-btn--danger' : '')}
        onClick={onClick} disabled={disabled || loading}>
        {loading ? <Spinner size={18} /> : children}
      </button>
    );
  }
  function GhostButton({ onClick, children, disabled, danger }) {
    return (
      <button className={'sk-btn sk-btn--ghost ' + (danger ? 'sk-btn--ghost-danger' : '')}
        onClick={onClick} disabled={disabled}>
        {children}
      </button>
    );
  }

  function Fab({ onClick, children }) {
    return (
      <div className="fab-wrap">
        <button className="fab fab--warm" onClick={onClick}>{children}</button>
      </div>
    );
  }

  // Подтверждение разрушительного действия.
  function ConfirmDialog({ open, title, message, confirmLabel, onConfirm, onCancel, danger, busy }) {
    if (!open) return null;
    return (
      <div className="sk-sheet-overlay" onClick={onCancel}>
        <div className="sk-confirm" onClick={(e) => e.stopPropagation()}>
          <div className="sk-confirm__title">{title}</div>
          {message && <div className="sk-confirm__msg">{message}</div>}
          <div className="sk-confirm__actions">
            <button className="sk-btn sk-btn--ghost" onClick={onCancel} disabled={busy}>Отмена</button>
            <button className={'sk-btn sk-btn--primary ' + (danger ? 'sk-btn--danger' : '')}
              onClick={onConfirm} disabled={busy}>
              {busy ? <Spinner size={18} /> : (confirmLabel || 'Подтвердить')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ─── Стили (инжект один раз) ─────────────────────────────────────────────────
  const CSS = `
  .sk-spinner { display:inline-block; border:2.5px solid rgba(255,255,255,0.18);
    border-top-color: var(--tg-accent); border-radius:50%; animation: sk-spin .7s linear infinite; vertical-align:middle; }
  @keyframes sk-spin { to { transform: rotate(360deg); } }

  .sk-empty { padding: 48px 28px; text-align:center; color: var(--tg-text-2); }
  .sk-empty__icon { font-size: 34px; margin-bottom: 10px; opacity:.9; }
  .sk-empty__title { font-size: 15px; font-weight: 600; color: var(--tg-text); }
  .sk-empty__hint { font-size: 13px; margin-top: 6px; line-height:1.4; }
  .sk-empty__action { margin-top: 16px; }

  .sk-error { padding: 40px 28px; text-align:center; }
  .sk-error__icon { margin-bottom: 10px; }
  .sk-error__title { font-size: 15px; font-weight: 600; color: var(--tg-text); }
  .sk-error__hint { font-size: 13px; color: var(--tg-text-2); margin-top: 6px; }
  .sk-error .sk-btn { margin-top: 16px; }

  .sk-section { display:flex; align-items:center; justify-content:space-between;
    padding: 14px 18px 6px; font-size: 12px; text-transform: uppercase;
    letter-spacing: .5px; color: var(--tg-text-3); }
  .sk-section__r { text-transform:none; letter-spacing:0; color: var(--tg-accent); font-size:13px; }

  .sk-card { background: var(--tg-surface); border-radius: var(--radius);
    margin: 8px 16px; padding: 14px 16px; }
  .sk-card--tap:active { background: var(--tg-surface-2); }

  .sk-row { display:flex; align-items:center; gap: 12px; padding: 12px 16px;
    background: var(--tg-surface); }
  .sk-row + .sk-row { border-top: 1px solid var(--tg-separator); }
  .sk-row--tap:active { background: var(--tg-surface-2); }
  .sk-row__left { flex-shrink:0; display:flex; align-items:center; }
  .sk-row__main { flex:1; min-width:0; }
  .sk-row__title { font-size: 15px; font-weight: 500; overflow:hidden; text-overflow:ellipsis; }
  .sk-row__sub { font-size: 12px; color: var(--tg-text-2); margin-top: 2px; }
  .sk-row__right { flex-shrink:0; display:flex; align-items:center; gap:8px; color: var(--tg-text-2); }
  .sk-group { margin: 8px 16px; border-radius: var(--radius); overflow:hidden; }
  .sk-group .sk-row { background: var(--tg-surface); }

  .sk-chip { padding: 7px 13px; border-radius: 999px; font-size: 13px; font-weight: 500;
    background: var(--tg-surface); color: var(--tg-text-2); border: 1px solid var(--tg-separator-strong); }
  .sk-chip--on { background: var(--tg-surface-hi); color: var(--tg-text); border-color: var(--tg-accent); }

  .sk-sheet-overlay { position:absolute; inset:0; background: rgba(0,0,0,0.5);
    display:flex; align-items:flex-end; justify-content:center; z-index: 40; animation: sk-fade .15s ease; }
  @keyframes sk-fade { from { opacity:0; } to { opacity:1; } }
  .sk-sheet { width:100%; max-height: 88%; background: var(--tg-bg); border-radius: 18px 18px 0 0;
    display:flex; flex-direction:column; animation: sk-up .2s cubic-bezier(.2,.7,.2,1); }
  @keyframes sk-up { from { transform: translateY(30px); } to { transform: translateY(0); } }
  .sk-sheet__head { display:flex; align-items:center; justify-content:space-between;
    padding: 16px 16px 8px; }
  .sk-sheet__title { font-size: 17px; font-weight: 600; }
  .sk-sheet__close { width:32px; height:32px; border-radius:16px; background: var(--tg-surface);
    border:none; color: var(--tg-text-2); display:flex; align-items:center; justify-content:center; }
  .sk-sheet__body { padding: 4px 16px 16px; overflow-y:auto; }
  .sk-sheet__footer { padding: 12px 16px calc(env(safe-area-inset-bottom,0px) + 16px);
    border-top: 1px solid var(--tg-separator); }

  .sk-confirm { width: calc(100% - 48px); max-width: 360px; margin: auto; align-self:center;
    background: var(--tg-surface); border-radius: var(--radius-lg); padding: 20px; }
  .sk-confirm__title { font-size: 16px; font-weight: 600; }
  .sk-confirm__msg { font-size: 14px; color: var(--tg-text-2); margin-top: 8px; line-height:1.4; }
  .sk-confirm__actions { display:flex; gap: 10px; margin-top: 18px; }
  .sk-confirm__actions .sk-btn { flex:1; }

  .sk-field { display:block; margin: 12px 0; }
  .sk-field__label { font-size: 13px; font-weight: 500; color: var(--tg-text-2); margin-bottom: 6px; }
  .sk-field__hint { font-size: 12px; color: var(--tg-text-3); margin-top: 5px; }
  .sk-input { width:100%; padding: 12px 14px; font-size: 15px; background: var(--tg-surface);
    border: 1px solid var(--tg-separator-strong); border-radius: var(--radius-sm);
    color: var(--tg-text); outline:none; }
  .sk-input:focus { border-color: var(--tg-accent); }
  .sk-input--area { min-height: 80px; resize: vertical; }
  .sk-select { appearance:none; -webkit-appearance:none; }

  .sk-btn { display:inline-flex; align-items:center; justify-content:center; gap:6px;
    border:none; border-radius: var(--radius-sm); font-size: 15px; font-weight: 600;
    padding: 12px 16px; }
  .sk-btn:disabled { opacity:.55; }
  .sk-btn--primary { background: var(--tg-accent); color:#fff; width:100%; }
  .sk-btn--primary:active { background: var(--tg-accent-press); }
  .sk-btn--danger { background: var(--danger); }
  .sk-btn--ghost { background: var(--tg-surface); color: var(--tg-text); }
  .sk-btn--ghost-danger { color: var(--danger); }
  `;

  if (!document.getElementById('sk-styles')) {
    const style = document.createElement('style');
    style.id = 'sk-styles';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  Object.assign(window, {
    useTgBackButton, useToast, useAsyncData,
    Spinner, EmptyState, ErrorState, Segmented, SectionTitle, Card, Row, Chip,
    Sheet, Field, TextInput, TextArea, SelectInput, PrimaryButton, GhostButton,
    Fab, ConfirmDialog,
  });
})();
