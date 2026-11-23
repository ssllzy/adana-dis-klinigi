(() => {
  'use strict';
  const header = document.querySelector('.site-header');
  const menu = document.querySelector('.menu-toggle');
  const nav = document.querySelector('.nav-links');
  const preference = matchMedia('(prefers-reduced-motion: reduce)');
  const updateHeader = () => header?.classList.toggle('scrolled', scrollY > 30);
  updateHeader();
  addEventListener('scroll', updateHeader, {passive:true});
  const menuLinks = [...(nav?.querySelectorAll('a') || [])];
  function syncMenuA11y(open = menu?.getAttribute('aria-expanded') === 'true') {
    menu?.setAttribute('aria-label', open ? 'Menüyü kapat' : 'Menüyü aç');
    if(innerWidth >= 900) nav?.removeAttribute('aria-hidden');
    else nav?.setAttribute('aria-hidden', open ? 'false' : 'true');
  }
  function closeMenu(restoreFocus = false) {
    menu?.classList.remove('open'); nav?.classList.remove('is-open');
    menu?.setAttribute('aria-expanded','false'); document.body.classList.remove('menu-open');
    syncMenuA11y(false);
    if(restoreFocus) menu?.focus();
  }
  menu?.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    closeMenu();
    if(open) { menu.classList.add('open'); nav.classList.add('is-open'); menu.setAttribute('aria-expanded','true'); document.body.classList.add('menu-open'); syncMenuA11y(true); menuLinks[0]?.focus(); }
  });
  nav?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => closeMenu()));
  document.addEventListener('keydown', event => {
    if(menu?.getAttribute('aria-expanded') !== 'true') return;
    if(event.key === 'Escape') { event.preventDefault(); closeMenu(true); return; }
    if(event.key !== 'Tab') return;
    const items = [menu, ...menuLinks];
    const first = items[0], last = items.at(-1);
    if(event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if(!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  addEventListener('resize', () => {if(innerWidth >= 900) closeMenu();else syncMenuA11y();});
  syncMenuA11y(false);
  const bookingSection = document.querySelector('#randevu');
  if(bookingSection) new IntersectionObserver(([entry]) => document.body.classList.toggle('booking-in-view', entry.isIntersecting), {threshold:.08}).observe(bookingSection);
  const controls = [];
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if(!entry.isIntersecting) return;
      const el = entry.target;
      el.classList.add('is-visible');
      if(!preference.matches && innerWidth >= 900 && window.Motion) {
        try { controls.push(Motion.animate(el,{opacity:[0,1]},{duration:.38,ease:'easeOut'})); } catch { /* Content remains visible without animation. */ }
      }
      observer.unobserve(el);
    });
  }, {threshold:.08});
  document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
  if(['localhost','127.0.0.1'].includes(location.hostname))fetch('/api/settings',{signal:AbortSignal.timeout(8000)}).then(response=>response.ok?response.json():Promise.reject()).then(settings=>{
    const address=document.querySelector('#publicClinicAddress'),phone=document.querySelector('#publicClinicPhone'),whatsapp=document.querySelector('#publicClinicWhatsapp'),hours=document.querySelector('#publicClinicHours');
    const shortName=settings.clinic_name.replace(/\s+diş\s+kliniği$/iu,'');
    document.querySelector('#publicBrandShort').textContent=shortName.toLocaleUpperCase('tr-TR');
    document.querySelector('#publicFooterBrand').textContent=settings.clinic_name.toLocaleUpperCase('tr-TR');
    document.querySelector('#publicCopyright').textContent='© 2026 '+settings.clinic_name.toLocaleUpperCase('tr-TR');
    document.querySelector('#publicCinemaBrand').textContent=(settings.address||'Yüreğir / Adana').toLocaleUpperCase('tr-TR')+' · '+settings.clinic_name.toLocaleUpperCase('tr-TR');
    document.title=settings.clinic_name+' | Randevu';
    if(settings.address)address.textContent=settings.address;
    const phoneDigits=(settings.phone||'').replace(/\D/g,'');
    if(phoneDigits){phone.textContent=settings.phone;phone.href='tel:+'+(phoneDigits.startsWith('0')?'90'+phoneDigits.slice(1):phoneDigits);phone.hidden=false;}
    const whatsappDigits=(settings.whatsapp||'').replace(/\D/g,'');
    if(whatsappDigits){whatsapp.href='https://wa.me/'+(whatsappDigits.startsWith('0')?'90'+whatsappDigits.slice(1):whatsappDigits);whatsapp.hidden=false;}
    if(settings.working_hours_note)hours.textContent=settings.working_hours_note;
  }).catch(()=>{});
  preference.addEventListener('change', () => {
    if(preference.matches) {controls.forEach(control => control.complete());document.querySelectorAll('.reveal').forEach(el => el.classList.add('is-visible'));}
  });
})();
