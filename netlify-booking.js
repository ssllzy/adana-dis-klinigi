(()=>{'use strict';
  const fields=document.querySelector('#bookingFields');
  const form=document.querySelector('#appointmentForm');
  const status=document.querySelector('#formStatus');
  document.querySelector('#retryBooking')?.setAttribute('hidden','');
  if(fields)fields.hidden=true;
  if(status){status.hidden=false;status.className='form-status static-deploy-notice';status.textContent='Online randevu formu geçici önizlemede kapalıdır. Güvenli randevu sunucusu bağlandığında yeniden açılacaktır.';}
  form?.addEventListener('submit',event=>event.preventDefault());
})();
