// KCU – kleine Helfer: Mehr-Menü, Lightbox, Alle-Auswahl, Bestätigungen, Schnellbeträge
(function () {
  // Mehr-Menü (Handy)
  const more = document.querySelector('[data-more]');
  const sheet = document.querySelector('.moresheet');
  if (more && sheet) {
    more.addEventListener('click', e => { e.preventDefault(); sheet.hidden = !sheet.hidden; });
    sheet.addEventListener('click', e => { if (e.target === sheet) sheet.hidden = true; });
  }

  // Bestätigung vor dem Löschen
  document.querySelectorAll('form[data-confirm]').forEach(f => {
    f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });
  document.querySelectorAll('[data-confirm-btn]').forEach(b => {
    b.addEventListener('click', e => { if (!confirm(b.dataset.confirmBtn)) e.preventDefault(); });
  });

  // "Alle" bei den Mitglieder-Chips
  document.querySelectorAll('[data-chips-all]').forEach(btn => {
    btn.addEventListener('click', () => {
      const boxes = btn.closest('[data-chips]').querySelectorAll('input[type=checkbox]');
      const all = [...boxes].every(b => b.checked);
      boxes.forEach(b => { b.checked = !all; });
      btn.textContent = all ? 'Alle' : 'Keine';
    });
  });

  // Schnellbeträge in der Kasse
  document.querySelectorAll('[data-set-amount]').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = btn.closest('form').querySelector('[data-amount]');
      const v = parseFloat(btn.dataset.setAmount || '0');
      if (input) input.value = v.toFixed(2).replace('.', ',');
    });
  });

  // Desktop-"Mehr"-Menü schließen bei Klick daneben
  document.addEventListener('click', e => {
    document.querySelectorAll('details.navmore[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
  });

  // Zu-/Absage: Grund-Feld bei "Kann nicht" hervorheben und Pflicht machen
  document.querySelectorAll('form[data-rsvp]').forEach(f => {
    const reason = f.querySelector('[name=reason]');
    const sync = () => {
      const st = (f.querySelector('[name=status]:checked') || {}).value;
      f.classList.toggle('picked', !!st);
      f.dataset.status = st || '';
      if (reason) {
        reason.required = st === 'nein';
        reason.placeholder = st === 'nein' ? 'Grund der Absage (Pflicht)' : 'Bemerkung (optional)';
      }
    };
    f.querySelectorAll('[name=status]').forEach(r => r.addEventListener('change', () => {
      sync();
      // Zusage ohne Bemerkung direkt absenden
      if (r.value !== 'nein' && reason && !reason.value) f.requestSubmit ? f.requestSubmit() : f.submit();
      else if (r.value === 'nein' && reason) reason.focus();
    }));
    sync();
  });

  // Abstimmungs-Formular: weitere Antworten, Datum -> Text
  const wd = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
  function bindDate(row) {
    const d = row.querySelector('.opt-date'), t = row.querySelector('[name=options]');
    if (!d || !t) return;
    d.addEventListener('change', () => {
      if (!d.value) return;
      const x = new Date(d.value + 'T12:00:00');
      t.value = wd[x.getDay()] + '., ' + String(x.getDate()).padStart(2, '0') + '.' + String(x.getMonth() + 1).padStart(2, '0') + '.' + x.getFullYear();
    });
  }
  document.querySelectorAll('[data-options]').forEach(fs => {
    fs.querySelectorAll('.opt-row').forEach(bindDate);
    const add = fs.querySelector('[data-add-option]');
    add && add.addEventListener('click', () => {
      const rows = fs.querySelectorAll('.opt-row');
      const row = rows[rows.length - 1].cloneNode(true);
      row.querySelectorAll('input').forEach(i => { i.value = ''; });
      row.querySelector('[name=options]').placeholder = 'Antwort ' + (rows.length + 1);
      fs.insertBefore(row, add);
      bindDate(row);
      row.querySelector('input').focus();
    });
  });

  // Kicker: Ergebnis-Eingabe mit +/- und "Sieg"-Knopf
  document.querySelectorAll('[data-score-form]').forEach(f => {
    const ziel = parseInt(f.dataset.ziel || '10', 10);
    const inputs = f.querySelectorAll('input[type=number]');
    f.querySelectorAll('[data-step]').forEach(b => b.addEventListener('click', () => {
      const i = b.parentElement.querySelector('input');
      i.value = Math.max(0, Math.min(99, (parseInt(i.value, 10) || 0) + parseInt(b.dataset.step, 10)));
    }));
    f.querySelectorAll('[data-win]').forEach((b, idx) => b.addEventListener('click', () => {
      inputs[idx].value = ziel;
      const other = inputs[1 - idx];
      if (other.value === '' || parseInt(other.value, 10) >= ziel) { other.value = ''; other.focus(); }
    }));
  });

  // Kicker: Felder je nach Modus ein-/ausblenden
  document.querySelectorAll('[data-tournament-form]').forEach(f => {
    const sync = () => {
      const mode = (f.querySelector('[name=mode]:checked') || {}).value;
      const entry = (f.querySelector('[name=entry]:checked') || {}).value || 'einzel';
      f.querySelectorAll('[data-only]').forEach(el => {
        const okMode = el.dataset.only.split(' ').includes(mode);
        const okEntry = !el.dataset.entry || el.dataset.entry === entry;
        el.hidden = !(okMode && okEntry);
        el.querySelectorAll('input, select, textarea').forEach(i => { i.disabled = el.hidden; });
      });
    };
    f.querySelectorAll('[name=mode], [name=entry]').forEach(r => r.addEventListener('change', sync));
    sync();
  });

  // Kicker: TV-Ansicht aktualisiert sich selbst, sobald ein Ergebnis eingetragen wurde
  const tv = document.getElementById('tv');
  if (tv) {
    let last = null;
    const poll = async () => {
      try {
        const r = await fetch(tv.dataset.status, { cache: 'no-store', credentials: 'same-origin' });
        if (r.ok) {
          const v = (await r.json()).v;
          if (last !== null && v !== last) {
            const h = await fetch(tv.dataset.src, { cache: 'no-store', credentials: 'same-origin' });
            if (h.ok) tv.innerHTML = await h.text();
          }
          last = v;
        }
      } catch (e) { /* Netz kurz weg – nächster Versuch */ }
      setTimeout(poll, 3000);
    };
    poll();
    const fs = document.querySelector('[data-fullscreen]');
    fs && fs.addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen();
    });
  }

  // Push-Benachrichtigungen
  const pushSupported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const swReady = pushSupported ? navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(() => navigator.serviceWorker.ready).catch(() => null) : Promise.resolve(null);
  const keyBytes = b64 => {
    const pad = '='.repeat((4 - b64.length % 4) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  };
  async function subscribe(card) {
    const reg = await swReady;
    if (!reg) throw new Error('Service Worker fehlt');
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') throw new Error('Mitteilungen wurden nicht erlaubt');
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(card.dataset.vapid) });
    const label = (isIOS ? 'iPhone/iPad' : /android/i.test(navigator.userAgent) ? 'Android' : 'Browser') + ' · ' + new Date().toLocaleDateString('de-DE');
    const r = await fetch(card.dataset.subUrl, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': card.dataset.csrf },
      body: JSON.stringify({ subscription: sub.toJSON(), label }) });
    if (!r.ok) throw new Error('Server hat das Abo abgelehnt');
    return sub;
  }
  const card = document.getElementById('push-card');
  if (card) {
    const state = card.querySelector('[data-push-state]');
    const on = card.querySelector('[data-push-on]'), off = card.querySelector('[data-push-off]');
    const render = async () => {
      if (!window.isSecureContext) { state.textContent = 'Nicht verfügbar (keine sichere Verbindung).'; card.querySelector('[data-insecure-hint]').hidden = false; return; }
      if (!pushSupported) {
        state.textContent = isIOS && !standalone ? 'Erst zum Home-Bildschirm hinzufügen.' : 'Dieser Browser unterstützt keine Push-Nachrichten.';
        card.querySelector('[data-ios-hint]').hidden = !isIOS; return;
      }
      const reg = await swReady;
      const sub = reg && await reg.pushManager.getSubscription();
      if (Notification.permission === 'denied') { state.textContent = '🚫 Mitteilungen sind für diese Seite im Browser blockiert – in den Browser-/Handy-Einstellungen erlauben.'; on.hidden = off.hidden = true; return; }
      state.textContent = sub ? '✅ Aktiv auf diesem Gerät.' : 'Auf diesem Gerät noch nicht aktiv.';
      on.hidden = !!sub; off.hidden = !sub;
      card.querySelector('[data-ios-hint]').hidden = !(isIOS && !standalone);
    };
    on.addEventListener('click', async () => {
      on.disabled = true; state.textContent = 'Aktiviere …';
      try { await subscribe(card); location.reload(); } catch (e) { state.textContent = '⚠ ' + e.message; on.disabled = false; }
    });
    off.addEventListener('click', async () => {
      const reg = await swReady; const sub = reg && await reg.pushManager.getSubscription();
      if (sub) {
        await fetch(card.dataset.unsubUrl, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': card.dataset.csrf }, body: JSON.stringify({ endpoint: sub.endpoint }) });
        await sub.unsubscribe();
      }
      location.reload();
    });
    render();
  }
  // Hinweis auf der Startseite, solange Push auf diesem Gerät nicht aktiv ist
  const prompt = document.querySelector('[data-push-prompt]');
  if (prompt && pushSupported) {
    let dismissed = false;
    try { dismissed = localStorage.getItem('kcu-push-dismissed') === '1'; } catch (e) { /* egal */ }
    swReady.then(reg => reg && reg.pushManager.getSubscription()).then(sub => {
      if (!sub && !dismissed && Notification.permission === 'default') prompt.hidden = false;
    });
    const x = prompt.querySelector('[data-dismiss]');
    x && x.addEventListener('click', e => { e.preventDefault(); prompt.hidden = true; try { localStorage.setItem('kcu-push-dismissed', '1'); } catch (er) { /* egal */ } });
  } else if (prompt && isIOS && !standalone && window.isSecureContext) {
    let dismissed = false;
    try { dismissed = localStorage.getItem('kcu-push-dismissed') === '1'; } catch (e) { /* egal */ }
    if (!dismissed) prompt.hidden = false;
  }

  // Jahresrückblick: Folien per Tippen/Wischen
  const wr = document.querySelector('[data-wrapped]');
  if (wr) {
    const slides = [...wr.querySelectorAll('.wr-slide')];
    const bar = document.querySelector('[data-wr-progress]');
    bar.innerHTML = slides.map(() => '<i></i>').join('');
    const dots = [...bar.children];
    const cur = () => Math.round(wr.scrollLeft / wr.clientWidth);
    const go = i => wr.scrollTo({ left: Math.max(0, Math.min(slides.length - 1, i)) * wr.clientWidth, behavior: 'smooth' });
    const mark = () => { const c = cur(); dots.forEach((d, i) => d.classList.toggle('on', i <= c)); };
    wr.addEventListener('scroll', () => requestAnimationFrame(mark));
    wr.addEventListener('click', e => {
      if (e.target.closest('a,button')) return;
      go(cur() + (e.clientX < window.innerWidth * 0.3 ? -1 : 1));
    });
    document.addEventListener('keydown', e => { if (e.key === 'ArrowRight' || e.key === ' ') go(cur() + 1); if (e.key === 'ArrowLeft') go(cur() - 1); });
    mark();
  }

  // Kopieren-Knöpfe
  document.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async () => {
    const el = document.querySelector(b.dataset.copy);
    try { await navigator.clipboard.writeText(el.value); } catch (e) { el.select(); document.execCommand('copy'); }
    const t = b.textContent; b.textContent = 'Kopiert ✓'; setTimeout(() => { b.textContent = t; }, 1600);
  }));

  // Lightbox für Galerie und Museum
  const links = [...document.querySelectorAll('[data-lightbox]')];
  if (!links.length) return;
  let idx = 0, box = null;

  function show(i) {
    idx = (i + links.length) % links.length;
    const a = links[idx];
    box.querySelector('img').src = a.getAttribute('href');
    box.querySelector('.lb-cap').textContent = (a.dataset.caption || '') + '  ' + (idx + 1) + '/' + links.length;
    box.querySelector('.lb-dl').href = a.dataset.original || a.getAttribute('href');
  }
  function close() { if (box) { box.remove(); box = null; document.removeEventListener('keydown', key); } }
  function key(e) {
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowRight') show(idx + 1);
    if (e.key === 'ArrowLeft') show(idx - 1);
  }
  function open(i) {
    box = document.createElement('div');
    box.className = 'lightbox';
    box.innerHTML = '<img alt=""><button class="lb-close" aria-label="Schließen">✕</button>' +
      (links.length > 1 ? '<button class="lb-btn lb-prev" aria-label="Zurück">‹</button><button class="lb-btn lb-next" aria-label="Weiter">›</button>' : '') +
      '<div class="lb-bar"><span class="lb-cap"></span><a class="lb-dl" href="#" download>Original laden</a></div>';
    document.body.appendChild(box);
    box.addEventListener('click', e => { if (e.target === box) close(); });
    box.querySelector('.lb-close').onclick = close;
    const p = box.querySelector('.lb-prev'), n = box.querySelector('.lb-next');
    if (p) p.onclick = () => show(idx - 1);
    if (n) n.onclick = () => show(idx + 1);
    let sx = null;
    box.addEventListener('touchstart', e => { sx = e.touches[0].clientX; }, { passive: true });
    box.addEventListener('touchend', e => {
      if (sx === null) return;
      const dx = e.changedTouches[0].clientX - sx;
      if (Math.abs(dx) > 50) show(idx + (dx < 0 ? 1 : -1));
      sx = null;
    });
    document.addEventListener('keydown', key);
    show(i);
  }
  links.forEach((a, i) => a.addEventListener('click', e => { e.preventDefault(); open(i); }));
})();
