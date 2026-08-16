const toggle = document.querySelector('.nav-toggle');
const nav = document.querySelector('#primary-nav');
if (toggle && nav) {
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(open));
  });
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js'));
}

window.setTimeout(() => {
  document.querySelectorAll('.message').forEach((message) => message.remove());
}, 6000);
