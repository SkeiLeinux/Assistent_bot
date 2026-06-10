// shell.jsx — корень Mini App ассистента: хаб контекстов + роутинг (волна C).
// Определяет глобальный <App/>, который рендерит index.html. Каждый контекст —
// самостоятельный экран-глобал window.<X>Screen({ onExit }); хаб = домашний экран.
//
// Экраны (строят frontend-агенты, по одному файлу):
//   window.ListsScreen      — lists.jsx
//   window.FinanceScreen    — finance.jsx
//   window.RemindersScreen  — reminders.jsx  (напоминания + аналитика/дайджест)
//   window.NutritionApp     — app.jsx        (дневник питания, переиспользован)
//
// ВАЖНО: каждый <script type="text/babel"> исполняется в ГЛОБАЛЬНОЙ области; чтобы не
// конфликтовать с `const { useState } = React` из app.jsx, шелл зовёт React.useState/
// React.useEffect напрямую (как goals.jsx/screens.jsx), без своих глобальных биндингов.

// Плитки хаба: какой экран, как подписан. order — порядок в сетке.
const HUB_TILES = [
  { key: 'lists',     glob: 'ListsScreen',     icon: '📝', label: 'Списки',     sub: 'Дела, покупки, идеи, подарки' },
  { key: 'finance',   glob: 'FinanceScreen',   icon: '💰', label: 'Финансы',    sub: 'Бюджет: план и факт' },
  { key: 'nutrition', glob: 'NutritionApp',    icon: '🍎', label: 'Питание',    sub: 'Дневник КБЖУ' },
  { key: 'reminders', glob: 'RemindersScreen', icon: '⏰', label: 'Напоминания', sub: 'Напоминания и обзор' },
];

function HubHeader({ me }) {
  const name = me && me.user ? me.user.first_name : null;
  const fam = me ? me.family : undefined;
  let sub;
  if (fam === undefined) sub = '';
  else if (fam) sub = 'Семья «' + (fam.name || '—') + '» · ' + ((fam.members || []).length) + '/2';
  else sub = 'Семьи пока нет — создай в боте: /newfamily';
  return (
    <div className="hub-head">
      <div className="hub-head__hi">{name ? ('Привет, ' + name) : 'Семейный ассистент'}</div>
      {sub && <div className="hub-head__sub">{sub}</div>}
    </div>
  );
}

function Hub({ me, meError, onOpen }) {
  return (
    <div className="app">
      <HubHeader me={me} />
      <div className="scroll">
        <div className="page-enter">
          {meError && meError.status === 401 && (
            <div className="hub-note">Открой приложение из Telegram — нужна авторизация.</div>
          )}
          {meError && meError.status === 403 && (
            <div className="hub-note">Нет доступа: ты не в списке разрешённых пользователей.</div>
          )}
          <div className="hub-grid">
            {HUB_TILES.map((t) => {
              const ready = typeof window[t.glob] === 'function';
              return (
                <button key={t.key} className={'hub-tile ' + (ready ? '' : 'hub-tile--soon')}
                  onClick={() => ready && onOpen(t.key)}>
                  <span className="hub-tile__icon">{t.icon}</span>
                  <span className="hub-tile__label">{t.label}</span>
                  <span className="hub-tile__sub">{ready ? t.sub : 'скоро'}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function App() {
  const [screen, setScreen] = React.useState('home');
  const [me, setMe] = React.useState(null);
  const [meError, setMeError] = React.useState(null);

  // Инициализация Telegram + первичный /me.
  React.useEffect(() => {
    const tg = window.TG;
    if (tg) {
      try { tg.ready(); tg.expand(); } catch (e) {}
      try { tg.setBackgroundColor('#17212B'); tg.setHeaderColor('#17212B'); } catch (e) {}
    }
    let alive = true;
    window.apiV1('/me').then(
      (d) => { if (alive) setMe(d); },
      (e) => { if (alive) setMeError(e); }
    );
    return () => { alive = false; };
  }, []);

  // На хабе BackButton не нужен (корень); экраны управляют им сами.
  React.useEffect(() => {
    const tg = window.TG;
    if (screen === 'home' && tg && tg.BackButton) { try { tg.BackButton.hide(); } catch (e) {} }
  }, [screen]);

  if (screen === 'home') {
    return <Hub me={me} meError={meError} onOpen={setScreen} />;
  }

  const tile = HUB_TILES.find((t) => t.key === screen);
  const Screen = tile ? window[tile.glob] : null;
  const onExit = () => setScreen('home');

  if (typeof Screen !== 'function') {
    // Подстраховка boot: экран ещё не подключён.
    return (
      <div className="app">
        <TgHeader title={tile ? tile.label : 'Экран'} onBack={onExit} />
        <div className="scroll"><div className="page-enter">
          <EmptyState icon="🚧" title="Экран ещё не подключён" hint="Этот раздел появится в сборке волны C." />
        </div></div>
      </div>
    );
  }
  return <Screen onExit={onExit} />;
}

// Стили хаба (инжект один раз).
(function () {
  if (document.getElementById('shell-styles')) return;
  const css = `
  .hub-head { flex-shrink:0; padding: calc(env(safe-area-inset-top,0px) + 18px) 20px 10px; }
  .hub-head__hi { font-size: 22px; font-weight: 700; letter-spacing: -0.3px; }
  .hub-head__sub { font-size: 13px; color: var(--tg-text-2); margin-top: 4px; }
  .hub-note { margin: 8px 16px; padding: 12px 14px; background: var(--tg-surface);
    border-radius: var(--radius); font-size: 13px; color: var(--tg-text-2); }
  .hub-grid { display:grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px 16px; }
  .hub-tile { display:flex; flex-direction:column; align-items:flex-start; gap: 4px;
    background: var(--tg-surface); border:none; border-radius: var(--radius-lg);
    padding: 18px 16px; text-align:left; min-height: 116px; color: var(--tg-text); }
  .hub-tile:active { background: var(--tg-surface-2); }
  .hub-tile--soon { opacity: .5; }
  .hub-tile__icon { font-size: 28px; margin-bottom: 6px; }
  .hub-tile__label { font-size: 16px; font-weight: 600; }
  .hub-tile__sub { font-size: 12px; color: var(--tg-text-2); line-height: 1.3; }
  `;
  const style = document.createElement('style');
  style.id = 'shell-styles';
  style.textContent = css;
  document.head.appendChild(style);
})();

window.App = App;
