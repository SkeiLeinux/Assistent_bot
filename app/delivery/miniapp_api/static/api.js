// api.js — слой обращения к backend мини-апы + интеграция с Telegram WebApp.
// Грузится как обычный <script> (без JSX), кладёт всё в window.
//
// Волна C: два клиента поверх одного fetch-ядра —
//   • window.apiV1(path, opts) — generic-клиент контекстов под /api/v1/* (Lists/Finance/
//     Reminders/Analytics/Nutrition). Бросает ApiError с .message (ru) из {code,message}.
//   • window.API — именованные методы дневника питания (перепроведены на /api/v1/nutrition/*).

(function () {
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  // initData — подписанная Telegram строка. Уходит в каждый запрос заголовком,
  // backend по ней аутентифицирует пользователя (kernel/auth).
  var INIT_DATA = tg ? tg.initData : '';

  function ApiError(message, code, status) {
    this.name = 'ApiError';
    this.message = message || 'Ошибка запроса';
    this.code = code || 'network';
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function qs(params) {
    if (!params) return '';
    var parts = [];
    Object.keys(params).forEach(function (k) {
      if (params[k] != null && params[k] !== '') {
        parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
      }
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  // Ядро: один fetch с initData-заголовком, разбором {code,message} и 204/blob/raw.
  // opts: { method, body, query, headers, raw }
  //   body: объект → JSON; FormData → как есть (boundary ставит браузер); строка → как есть.
  //   raw: true → вернуть Response (для xlsx/blob-выгрузок).
  async function coreFetch(fullPath, opts) {
    opts = opts || {};
    var headers = Object.assign({ 'X-Telegram-Init-Data': INIT_DATA }, opts.headers || {});
    var body = opts.body;
    var isForm = (typeof FormData !== 'undefined') && body instanceof FormData;
    if (body != null && !isForm && typeof body !== 'string') {
      body = JSON.stringify(body);
      headers['Content-Type'] = 'application/json';
    }
    var url = fullPath + qs(opts.query);
    var res;
    try {
      res = await fetch(url, { method: opts.method || 'GET', headers: headers, body: body });
    } catch (e) {
      throw new ApiError('Нет соединения с сервером', 'network', 0);
    }
    if (opts.raw) {
      if (!res.ok) throw await errorFrom(res);
      return res;
    }
    if (res.status === 204) return null;
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var msg = (data && (data.message || data.error)) || ('Ошибка ' + res.status);
      var code = (data && data.code) || 'server';
      throw new ApiError(msg, code, res.status);
    }
    return data;
  }

  async function errorFrom(res) {
    var data = null;
    try { data = await res.json(); } catch (e) {}
    var msg = (data && (data.message || data.error)) || ('Ошибка ' + res.status);
    var code = (data && data.code) || 'server';
    return new ApiError(msg, code, res.status);
  }

  // Generic-клиент контекстов: путь относительно /api/v1.
  function apiV1(path, opts) {
    return coreFetch('/api/v1' + path, opts);
  }

  // ─── Дневник питания (перепроведено на /api/v1/nutrition/*) ──────────────────
  function nutri(path, opts) { return apiV1('/nutrition' + path, opts); }

  window.API = {
    getDay: function (date) { return nutri('/day', { query: { date: date } }); },
    getGoals: function () { return nutri('/goals'); },
    setGoals: function (goals) { return nutri('/goals', { method: 'PUT', body: goals }); },
    analyze: function (payload) { return nutri('/analyze', { method: 'POST', body: payload }); },
    saveEntries: function (payload) { return nutri('/entries', { method: 'POST', body: payload }); },
    saveComposite: function (payload) { return nutri('/entries/composite', { method: 'POST', body: payload }); },
    updateEntry: function (id, fields) { return nutri('/entries/' + id, { method: 'PATCH', body: fields }); },
    deleteEntry: function (id) { return nutri('/entries/' + id, { method: 'DELETE' }); },
    getPeriod: function (from, to) { return nutri('/period', { query: { from: from, to: to } }); },
    getExport: function (from, to) { return nutri('/export', { query: { from: from, to: to } }); },
    getFoods: function (q) { return nutri('/foods', { query: { q: q || '' } }); },
    addEntryFromFood: function (payload) { return nutri('/entries/from-food', { method: 'POST', body: payload }); },
    analyzeDish: function (payload) { return nutri('/foods/analyze', { method: 'POST', body: payload }); },
    createFood: function (payload) { return nutri('/foods', { method: 'POST', body: payload }); },
  };

  // Сжатие фото на клиенте перед отправкой: длинная сторона ≤ maxSide, JPEG quality.
  function fileToCompressedDataUrl(file, maxSide, quality) {
    maxSide = maxSide || 1280;
    quality = quality || 0.82;
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onerror = function () { reject(new ApiError('Не удалось прочитать файл', 'bad_request', 0)); };
      reader.onload = function () {
        var img = new Image();
        img.onerror = function () { reject(new ApiError('Не удалось открыть изображение', 'bad_request', 0)); };
        img.onload = function () {
          var w = img.width, h = img.height;
          var scale = Math.min(1, maxSide / Math.max(w, h));
          var cw = Math.round(w * scale), ch = Math.round(h * scale);
          var canvas = document.createElement('canvas');
          canvas.width = cw; canvas.height = ch;
          canvas.getContext('2d').drawImage(img, 0, 0, cw, ch);
          resolve(canvas.toDataURL('image/jpeg', quality));
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    });
  }

  window.apiV1 = apiV1;
  window.TG = tg;
  window.ApiError = ApiError;
  window.fileToCompressedDataUrl = fileToCompressedDataUrl;
})();
