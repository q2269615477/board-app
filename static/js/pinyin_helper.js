(function () {
  var FALLBACK_INITIALS = {
    '药': 'y',
    '明': 'm',
    '康': 'k',
    '德': 'd',
    '中': 'z',
    '国': 'g',
    '平': 'p',
    '安': 'a',
    '宁': 'n',
    '茅': 'm',
    '台': 't',
    '招': 'z',
    '商': 's',
    '银': 'y',
    '行': 'h',
    '上': 's',
    '证': 'z',
    '深': 's',
    '成': 'c',
    '创': 'c',
    '业': 'y',
    '板': 'b',
    '科': 'k',
    '技': 'j',
    '医': 'y',
    '疗': 'l',
    '新': 'x',
    '能': 'n',
    '源': 'y',
    '汽': 'q',
    '车': 'c',
    '半': 'b',
    '导': 'd',
    '体': 't'
  };

  function fromSearchIndex(str) {
    var list = window._sm || window.__searchIndex || [];
    if (!str || !list || !list.length) return '';
    var exact = list.find(function (item) { return item && item.name === str && item.initials; });
    return exact ? String(exact.initials).toLowerCase() : '';
  }

  function getStrPinyinInitials(str) {
    str = String(str || '').trim();
    if (!str) return '';

    var indexed = fromSearchIndex(str);
    if (indexed) return indexed;

    var initials = '';
    for (var i = 0; i < str.length; i++) {
      var ch = str[i];
      if (/[a-zA-Z0-9]/.test(ch)) initials += ch.toLowerCase();
      else if (FALLBACK_INITIALS[ch]) initials += FALLBACK_INITIALS[ch];
      else initials += ch;
    }
    return initials.toLowerCase();
  }

  window.getStrPinyinInitials = getStrPinyinInitials;
  if (document && document.documentElement) {
    document.documentElement.dataset.pinyinHelper = 'ready';
  }
})();
