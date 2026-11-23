'use strict';
if (!matchMedia('(prefers-reduced-motion: reduce)').matches && !location.hash) {
  document.documentElement.classList.add('intro-pending');
  window.introFailsafe = setTimeout(() => document.documentElement.classList.remove('intro-pending'), 6500);
}
