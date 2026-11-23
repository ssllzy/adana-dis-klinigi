(() => {
  'use strict';
  const root = document.documentElement;
  const hero = document.querySelector('.cinema-hero');
  const title = document.querySelector('.cinema-title');
  const photo = document.querySelector('.cinema-photo');
  const details = [...document.querySelectorAll('.hero-detail')];
  const preference = matchMedia('(prefers-reduced-motion: reduce)');
  const controls = [];
  let finished = false;
  let watchdog;
  function finish() {
    if (finished) return;
    finished = true;
    controls.forEach(control => control.stop());
    [title, photo, ...details].forEach(el => {
      if (!el) return;
      el.style.removeProperty('transform');
      el.style.removeProperty('opacity');
      el.style.removeProperty('filter');
    });
    root.classList.remove('intro-pending');
    clearTimeout(window.introFailsafe);
    clearTimeout(watchdog);
    window.removeEventListener('scroll', onScroll);
    window.removeEventListener('resize', finish);
    document.removeEventListener('keydown', onKey);
    preference.removeEventListener('change', finish);
  }
  function onScroll() { if (scrollY > 40) finish(); }
  function onKey(event) { if (event.key === 'Escape' || event.key === 'Tab') finish(); }
  if (!hero || !root.classList.contains('intro-pending') || !window.Motion || preference.matches) {
    finish();
    return;
  }
  window.addEventListener('scroll', onScroll, {passive:true});
  window.addEventListener('resize', finish);
  document.addEventListener('keydown', onKey);
  preference.addEventListener('change', finish);
  watchdog = setTimeout(finish, 5500);
  async function play() {
    try {
      // Wait briefly for typography, never block entry on a remote font or image.
      await Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 350))]);
      if (finished) return;
      const rect = title.getBoundingClientRect();
      const x = (innerWidth - rect.width) / 2 - rect.left;
      const y = (innerHeight - rect.height) / 2 - rect.top;
      const centered = `translate(${x}px, ${y}px)`;
      const animate = (el, frames, options) => {
        const control = Motion.animate(el, frames, options);
        controls.push(control);
        return control;
      };
      title.style.transform = centered;
      await animate(title, {opacity:[0,1],filter:['blur(9px)','blur(0px)']}, {duration:.9,ease:'easeOut'});
      if (finished) return;
      await animate(title, {transform:[centered,'translate(0px, 0px)']}, {delay:.25,duration:.9,ease:[.22,1,.36,1]});
      if (finished) return;
      animate(photo, {opacity:[0,1],transform:['scale(1.01)','scale(1.035)']}, {duration:1.1,ease:[.22,1,.36,1]});
      await animate(details, {opacity:[0,1]},{delay:.2,duration:.6,ease:'easeOut'});
      // Leave the final zoom in place until its last frame before releasing styles.
      await controls[controls.length-2];
      finish();
    } catch { finish(); }
  }
  play();
})();
