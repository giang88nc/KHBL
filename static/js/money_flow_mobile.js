(function(){
  function setNav(path){
    document.querySelectorAll('.mapp-nav a').forEach(function(a){
      var qr=a.getAttribute('href').endsWith('/mobile/qr/');
      var home=a.getAttribute('href').replace(/\/$/,'').endsWith('/mobile');
      a.classList.toggle('active',(qr&&path.indexOf('/mobile/qr/')!==-1)||(home&&/\/mobile(?:\/dashboard)?\/?$/.test(path)));
    });
  }
  document.body.addEventListener('htmx:afterSwap',function(e){if(e.detail.target.id==='mobile-stage')setNav(location.pathname);});
  document.body.addEventListener('htmx:pushedIntoHistory',function(){setNav(location.pathname);});
  document.body.addEventListener('htmx:historyRestore',function(){setNav(location.pathname);});
  document.body.addEventListener('htmx:beforeRequest',function(e){if(e.detail.target&&e.detail.target.id==='mobile-stage')e.detail.target.classList.add('is-loading');});
  document.body.addEventListener('htmx:afterRequest',function(){var s=document.getElementById('mobile-stage');if(s)s.classList.remove('is-loading');});
  document.addEventListener('money-flow-reconciled',function(){
    if(document.getElementById('mobile-stage') && (location.pathname.indexOf('/mobile/money-in/')!==-1 || location.pathname.indexOf('/mobile/qr/')!==-1 || location.pathname.indexOf('/mobile/online/')!==-1) && window.htmx)
      htmx.ajax('GET',location.pathname+location.search,{target:'#mobile-stage',swap:'innerHTML'});
  });
})();
