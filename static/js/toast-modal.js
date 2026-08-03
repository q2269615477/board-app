function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2500);}
function showToastBar(html){let el=document.getElementById('toast-bar');if(!el){el=document.createElement('div');el.id='toast-bar';el.className='toast-bar';document.body.appendChild(el);}el.innerHTML=html;el.classList.add('show');clearTimeout(el._bt);el._bt=setTimeout(()=>el.classList.remove('show'),15000);}

function showModal(){document.getElementById('modal-overlay').classList.add('show');}
function closeModal(){document.getElementById('modal-overlay').classList.remove('show');}
document.getElementById('modal-overlay').addEventListener('click',function(e){if(e.target===this)closeModal();});
function escHtml(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
function escAttr(s){return (s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;');}
