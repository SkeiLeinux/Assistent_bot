// api.js — слой обращения к backend мини-апы + интеграция с Telegram WebApp.
// Грузится как обычный <script> (без JSX), кладёт всё в window.

(function () {
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  // initData — подписанная Telegram строка. Уходит в каждый запрос заголовком,
  // backend по ней аутентифицирует пользователя (web/auth.py).
  var INIT_DATA = tg ? tg.initData : '';

  function ApiError(message, code, status) {
    this.name = 'ApiError';
    this.message = message || 'Ошибка запроса';
    this.code = code || 'network';
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);

  async function apiFetch(path, opts) {
    opts = opts || {};
    var headers = Object.assign(
      { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': INIT_DATA },
      opts.headers || {}
    );
    var res;
    try {
      res = await fetch('/api' + path, {
        method: opts.method || 'GET',
        headers: headers,
        body: opts.body,
      });
    } catch (e) {
      // Сеть недоступна / запрос не ушёл.
      throw new ApiError('Нет соединения с сервером', 'network', 0);
    }
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var msg = (data && data.error) || ('Ошибка ' + res.status);
      var code = (data && data.code) || 'server';
      throw new ApiError(msg, code, res.status);
    }
    return data;
  }

  function qs(params) {
    var parts = [];
    Object.keys(params).forEach(function (k) {
      if (params[k] != null && params[k] !== '') {
        parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
      }
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  // Сжатие фото на клиенте перед отправкой: длинная сторона ≤ maxSide,
  // JPEG quality. Groq принимает ~4 МБ base64 — этого с запасом хватает.
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

  window.API = {
    getDay: function (date) { return apiFetch('/day' + qs({ date: date })); },
    getGoals: function () { return apiFetch('/goals'); },
    setGoals: function (goals) {
      return apiFetch('/goals', { method: 'PUT', body: JSON.stringify(goals) });
    },
    analyze: function (payload) {
      return apiFetch('/analyze', { method: 'POST', body: JSON.stringify(payload) });
    },
    saveEntries: function (payload) {
      return apiFetch('/entries', { method: 'POST', body: JSON.stringify(payload) });
    },
    updateEntry: function (id, fields) {
      return apiFetch('/entries/' + id, { method: 'PATCH', body: JSON.stringify(fields) });
    },
    deleteEntry: function (id) {
      return apiFetch('/entries/' + id, { method: 'DELETE' });
    },
    getPeriod: function (from, to) { return apiFetch('/period' + qs({ from: from, to: to })); },
    getExport: function (from, to) { return apiFetch('/export' + qs({ from: from, to: to })); },
    // Общая база блюд
    getFoods: function (q) { return apiFetch('/foods' + qs({ q: q || '' })); },
    addEntryFromFood: function (payload) {
      return apiFetch('/entries/from-food', { method: 'POST', body: JSON.stringify(payload) });
    },
    analyzeDish: function (payload) {
      return apiFetch('/foods/analyze', { method: 'POST', body: JSON.stringify(payload) });
    },
    createFood: function (payload) {
      return apiFetch('/foods', { method: 'POST', body: JSON.stringify(payload) });
    },
  };

  window.TG = tg;
  window.ApiError = ApiError;
  window.fileToCompressedDataUrl = fileToCompressedDataUrl;
})();
