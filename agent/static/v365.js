(() => {
  'use strict';
  const key='axiorhub-scroll-v365:'+location.pathname;
  const sameOrigin=document.referrer && new URL(document.referrer).origin===location.origin;
  if(sameOrigin){
    try{
      const saved=JSON.parse(sessionStorage.getItem(key)||'null');
      if(saved && Date.now()-saved.at<120000){
        requestAnimationFrame(()=>requestAnimationFrame(()=>window.scrollTo({top:saved.y,behavior:'instant'})));
      }
      sessionStorage.removeItem(key);
    }catch(_error){/* storage may be disabled */}
  }
  document.addEventListener('submit',event=>{
    const form=event.target;
    if(!(form instanceof HTMLFormElement) || form.method.toLowerCase()!=='post')return;
    try{sessionStorage.setItem(key,JSON.stringify({y:window.scrollY,at:Date.now()}));}catch(_error){}
    const button=event.submitter;
    if(button){button.disabled=true;button.dataset.originalLabel=button.textContent;button.textContent='Traitement…';}
  });
})();
