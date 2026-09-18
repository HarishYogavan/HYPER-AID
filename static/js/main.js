/* main.js - shared behaviour across every page */

document.addEventListener('DOMContentLoaded', () => {
  initLoader();
  initTheme();
  initSidebar();
  initHighContrast();
  initBigIconMode();
  initScrollTop();
  initShakeDetection();
  registerServiceWorker();
  initFirstTimeTour();
  initOfflineSupport();
});

/* ---------- Sidebar (mobile off-canvas drawer) ---------- */
function initSidebar() {
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const toggleBtn = document.getElementById('sidebarToggle');
  if (!sidebar) return;

  function openSidebar() {
    sidebar.classList.add('open');
    if (backdrop) backdrop.classList.add('show');
    document.body.style.overflow = 'hidden';
  }
  function closeSidebar() {
    sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('show');
    document.body.style.overflow = '';
  }
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });
  }
  if (backdrop) backdrop.addEventListener('click', closeSidebar);
  sidebar.querySelectorAll('.nav-item, .sidebar-footer a').forEach(link => {
    link.addEventListener('click', () => { if (window.innerWidth <= 900) closeSidebar(); });
  });
  window.addEventListener('resize', () => { if (window.innerWidth > 900) closeSidebar(); });
}

/* ---------- First-time walkthrough ---------- */
const TOUR_STEPS = [
  { icon: 'fa-heart-pulse', title: 'Welcome to HyperAid', body: 'A quick 20-second tour of what this app can do for you in an emergency.' },
  { icon: 'fa-tower-broadcast', title: 'The SOS button', body: 'Tap it any time (top-right, or the floating button) to share your live location and alert your saved emergency contacts instantly.' },
  { icon: 'fa-robot', title: 'HYPER AI Assistant', body: 'Ask it about any emergency or everyday health issue - it gives first-aid guidance and home remedies, by voice or text.' },
  { icon: 'fa-gauge-high', title: 'Emergency Dashboard', body: 'See the nearest hospitals, ambulances, police and more, ranked by real distance from you.' },
  { icon: 'fa-thumbs-up', title: "You're all set", body: 'Create a free account to save emergency contacts and unlock the Emergency Card, location tracker, and more.' },
];

function initFirstTimeTour() {
  if (localStorage.getItem('rescueai-tour-seen') === 'yes') return;
  const modalEl = document.getElementById('tourModal');
  if (!modalEl || typeof bootstrap === 'undefined') return;
  const modal = new bootstrap.Modal(modalEl, { backdrop: 'static' });
  let step = 0;

  function render() {
    const s = TOUR_STEPS[step];
    document.getElementById('tourStepBody').innerHTML = `
      <div class="feature-icon accent mx-auto mb-3" style="width:60px;height:60px;font-size:1.5rem"><i class="fa-solid ${s.icon}"></i></div>
      <h5>${s.title}</h5>
      <p class="text-muted-custom mb-0">${s.body}</p>`;
    document.getElementById('tourStepLabel').textContent = `${step + 1} / ${TOUR_STEPS.length}`;
    document.getElementById('tourNextBtn').textContent = step === TOUR_STEPS.length - 1 ? 'Get started' : 'Next';
  }

  function finish() {
    localStorage.setItem('rescueai-tour-seen', 'yes');
    modal.hide();
  }

  document.getElementById('tourNextBtn').addEventListener('click', () => {
    if (step === TOUR_STEPS.length - 1) { finish(); return; }
    step++; render();
  });
  document.getElementById('tourSkipBtn').addEventListener('click', finish);
  modalEl.addEventListener('hidden.bs.modal', () => localStorage.setItem('rescueai-tour-seen', 'yes'));

  render();
  setTimeout(() => modal.show(), 600);
}

/* ---------- Fake incoming call decoy ---------- */
function showFakeCall() {
  const overlay = document.getElementById('fakeCallOverlay');
  if (overlay) overlay.classList.remove('d-none');
  if (navigator.vibrate) navigator.vibrate([300, 200, 300]);
}
function hideFakeCall() {
  const overlay = document.getElementById('fakeCallOverlay');
  if (overlay) overlay.classList.add('d-none');
}

/* ---------- Shake-to-SOS (DeviceMotion API) ---------- */
function initShakeDetection() {
  if (typeof DeviceMotionEvent === 'undefined') return;
  let lastX, lastY, lastZ, lastTime = 0;
  const THRESHOLD = 22; // acceleration delta to count as a "shake"
  let shakeCount = 0, shakeWindowStart = 0;

  function handleMotion(event) {
    const acc = event.accelerationIncludingGravity;
    if (!acc) return;
    const now = Date.now();
    if (now - lastTime < 100) return;
    const dt = now - lastTime;
    lastTime = now;

    const dx = Math.abs((acc.x || 0) - (lastX || 0));
    const dy = Math.abs((acc.y || 0) - (lastY || 0));
    const dz = Math.abs((acc.z || 0) - (lastZ || 0));
    lastX = acc.x; lastY = acc.y; lastZ = acc.z;

    const speed = (dx + dy + dz) / dt * 1000;
    if (speed > THRESHOLD) {
      if (now - shakeWindowStart > 1500) { shakeWindowStart = now; shakeCount = 0; }
      shakeCount++;
      if (shakeCount >= 3) {
        shakeCount = 0;
        if (localStorage.getItem('rescueai-shake-sos') === 'on') {
          showToast('Shake detected - triggering SOS in 3s. Shake again to cancel.', 'danger');
          window._shakeSosCancelled = false;
          const cancel = () => { window._shakeSosCancelled = true; };
          window.addEventListener('devicemotion', cancel, { once: true });
          setTimeout(() => { if (!window._shakeSosCancelled) triggerSOS(); }, 3000);
        }
      }
    }
  }

  // Only attach after explicit opt-in, since iOS requires a user gesture to
  // grant motion-sensor permission - see enableShakeSos() below.
  window._attachShakeListener = () => window.addEventListener('devicemotion', handleMotion);
  if (localStorage.getItem('rescueai-shake-sos') === 'on') window._attachShakeListener();
}

function enableShakeSos() {
  const finish = () => {
    localStorage.setItem('rescueai-shake-sos', 'on');
    window._attachShakeListener && window._attachShakeListener();
    showToast('Shake-to-SOS enabled - shake your phone firmly 3 times to trigger SOS.', 'success');
  };
  if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
    DeviceMotionEvent.requestPermission().then(state => {
      if (state === 'granted') finish();
      else showToast('Motion permission denied - shake-to-SOS needs it to work.', 'warning');
    }).catch(() => showToast('Could not enable shake-to-SOS on this device.', 'warning'));
  } else if (typeof DeviceMotionEvent !== 'undefined') {
    finish();
  } else {
    showToast('This device/browser does not support motion detection.', 'warning');
  }
}

function disableShakeSos() {
  localStorage.setItem('rescueai-shake-sos', 'off');
  showToast('Shake-to-SOS turned off.', 'primary');
}

/* ---------- PWA install support ---------- */
function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  }
}

/* ---------- Offline support: banner + write-queue ---------- */
const OFFLINE_QUEUE_KEY = 'rescueai-offline-queue';

function getOfflineQueue() {
  try { return JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || '[]'); }
  catch (e) { return []; }
}
function setOfflineQueue(q) { localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(q)); }

function queueOfflineRequest(url, body) {
  const q = getOfflineQueue();
  q.push({ url, body: body || null, ts: Date.now() });
  setOfflineQueue(q);
  updateOfflineBanner();
}

/* Offline-aware POST helper. Used by SOS, I'm-safe, anomaly-dismiss, and
   accessibility-preference sync so those actions still "succeed" locally
   when there's no signal, then flush automatically once reconnected. */
function postJSON(url, body) {
  if (!navigator.onLine) {
    queueOfflineRequest(url, body);
    return Promise.resolve({ queued: true, message: 'Saved offline - will send once you\'re back online.' });
  }
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
    .then(r => r.json().catch(() => ({})))
    .catch(() => {
      queueOfflineRequest(url, body);
      return { queued: true, message: 'Saved offline - will send once you\'re back online.' };
    });
}

async function processOfflineQueue() {
  if (!navigator.onLine) return;
  const q = getOfflineQueue();
  if (q.length === 0) return;
  showToast(`Sending ${q.length} queued action${q.length > 1 ? 's' : ''}...`, 'primary');
  const remaining = [];
  for (const item of q) {
    try {
      const res = await fetch(item.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: item.body ? JSON.stringify(item.body) : undefined,
      });
      if (!res.ok) remaining.push(item);
    } catch (e) {
      remaining.push(item);
      break;
    }
  }
  setOfflineQueue(remaining);
  updateOfflineBanner();
  if (remaining.length === 0 && q.length > 0) showToast('All queued actions were sent.', 'success');
}

function updateOfflineBanner() {
  const banner = document.getElementById('offlineBanner');
  if (!banner) return;
  const textEl = document.getElementById('offlineBannerText');
  const pending = getOfflineQueue().length;
  if (!navigator.onLine) {
    banner.classList.add('show');
    if (textEl) textEl.textContent = "You're offline - showing cached data. Actions like SOS will send automatically once you're back online.";
  } else if (pending > 0) {
    banner.classList.add('show');
    if (textEl) textEl.textContent = `Back online - sending ${pending} queued action${pending > 1 ? 's' : ''}...`;
    setTimeout(() => { if (getOfflineQueue().length === 0) banner.classList.remove('show'); }, 4000);
  } else {
    banner.classList.remove('show');
  }
}

function initOfflineSupport() {
  updateOfflineBanner();
  if (navigator.onLine) processOfflineQueue();
  window.addEventListener('online', () => { updateOfflineBanner(); processOfflineQueue(); });
  window.addEventListener('offline', updateOfflineBanner);
}

/* ---------- Loading animation ---------- */
function initLoader() {
  const loader = document.getElementById('loader');
  if (!loader) return;
  window.addEventListener('load', () => {
    setTimeout(() => loader.classList.add('hide'), 250);
  });
  // Fallback in case 'load' already fired
  setTimeout(() => loader.classList.add('hide'), 1200);
}

/* ---------- Theme: Light / Dark / Black & White ---------- */
const THEME_CYCLE = ['light', 'dark', 'bw'];
const THEME_ICONS = { light: 'fa-moon', dark: 'fa-circle-half-stroke', bw: 'fa-sun' };
const THEME_LABELS = { light: 'Light', dark: 'Dark', bw: 'Black & White' };

function initTheme() {
  const root = document.documentElement;
  const btn = document.getElementById('themeToggle');
  let saved = localStorage.getItem('rescueai-theme');
  if (!saved) {
    // First visit: respect the OS/browser's colour-scheme preference
    saved = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  root.setAttribute('data-theme', saved);
  updateThemeIcon(saved);

  if (btn) {
    btn.addEventListener('click', () => {
      const current = root.getAttribute('data-theme') || 'light';
      const idx = THEME_CYCLE.indexOf(current);
      const next = THEME_CYCLE[(idx + 1) % THEME_CYCLE.length];
      root.setAttribute('data-theme', next);
      localStorage.setItem('rescueai-theme', next);
      updateThemeIcon(next);
      showToast(`${THEME_LABELS[next]} theme`, 'primary');
    });
  }
}

function updateThemeIcon(theme) {
  const btn = document.getElementById('themeToggle');
  if (!btn) return;
  const icon = THEME_ICONS[theme] || 'fa-moon';
  btn.innerHTML = `<i class="fa-solid ${icon}"></i>`;
}

/* ---------- High-contrast accessibility mode ---------- */
function initHighContrast() {
  const root = document.documentElement;
  const btn = document.getElementById('contrastToggle');
  const saved = localStorage.getItem('rescueai-contrast') === 'on';
  if (saved) root.classList.add('high-contrast');

  if (btn) {
    btn.addEventListener('click', () => {
      const on = root.classList.toggle('high-contrast');
      localStorage.setItem('rescueai-contrast', on ? 'on' : 'off');
      // Best-effort sync to the account if logged in - queued if offline.
      postJSON('/api/accessibility/high-contrast', { enabled: on });
      showToast(on ? 'High-contrast mode on.' : 'High-contrast mode off.', 'primary');
    });
  }
}

/* ---------- Big-icon simplified mode ---------- */
function initBigIconMode() {
  const root = document.documentElement;
  const btn = document.getElementById('bigIconToggle');
  const saved = localStorage.getItem('rescueai-bigicon') === 'on';
  if (saved) root.classList.add('big-icon-mode');

  if (btn) {
    btn.addEventListener('click', () => {
      const on = root.classList.toggle('big-icon-mode');
      localStorage.setItem('rescueai-bigicon', on ? 'on' : 'off');
      showToast(on ? 'Simplified big-icon mode on.' : 'Simplified big-icon mode off.', 'primary');
    });
  }
}

/* ---------- Scroll-to-top ---------- */
function initScrollTop() {
  const btn = document.getElementById('scrollTopBtn');
  if (!btn) return;
  window.addEventListener('scroll', () => {
    btn.classList.toggle('show', window.scrollY > 400);
  });
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
}

/* ---------- Toasts ---------- */
function showToast(message, type = 'primary') {
  const region = document.getElementById('toastRegion');
  if (!region) { alert(message); return; }
  const el = document.createElement('div');
  el.className = `toast align-items-center text-bg-${type} border-0 show`;
  el.innerHTML = `<div class="d-flex">
      <div class="toast-body">${message}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="this.closest('.toast').remove()"></button>
    </div>`;
  region.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/* ---------- SOS ---------- */
function triggerSOS() {
  showToast('Getting your location to dispatch the SOS alert...', 'warning');

  const send = (lat, lng) => {
    if (!navigator.onLine) {
      queueOfflineRequest('/api/sos', { lat, lng });
      showToast("You're offline - SOS is queued and will send the instant you reconnect. Call 112 directly if this is urgent.", 'danger');
      return;
    }
    fetch('/api/sos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lng }),
    })
      .then(r => r.json())
      .then(data => {
        showToast(data.message || 'SOS dispatched.', 'danger');
        if (data.notified_contacts && data.notified_contacts.length) {
          const names = data.notified_contacts.map(c => c.name).join(', ');
          showToast(`Notified: ${names}`, 'success');
        }
        if (data.chat_url) {
          showToast(`Coordination room ready: ${data.chat_url}`, 'primary');
        }
      })
      .catch(() => {
        queueOfflineRequest('/api/sos', { lat, lng });
        showToast('Could not reach the server - SOS queued to send once reconnected. Call 112 directly.', 'danger');
      });
  };

  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      pos => send(pos.coords.latitude, pos.coords.longitude),
      () => send(null, null),
      { timeout: 5000 }
    );
  } else {
    send(null, null);
  }
}

/* ---------- Anomaly check-in banner ---------- */
function dismissAnomalyBanner() {
  postJSON('/api/anomaly-checkin/dismiss').then(() => {
    const banner = document.getElementById('anomalyBanner');
    if (banner) banner.remove();
    showToast('Glad you\'re okay!', 'success');
  });
}

/* ---------- I'm Safe broadcast ---------- */
function triggerImSafe() {
  postJSON('/api/im-safe').then(data => {
    showToast(data.message || "'I'm safe' sent.", 'success');
  });
}
