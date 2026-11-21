/* CyberPulse Reader Static Portal Script - Hallmark Editorial Edition */
(function() {
  // Theme initialization before DOM paint (defaults to Editorial light paper)
  var saved = localStorage.getItem('cyberpulse-theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);

  // Check Google Material Symbols font loading
  if ('fonts' in document) {
    document.fonts.load('24px "Material Symbols Outlined"').catch(function() {
      document.documentElement.classList.add('fonts-failed');
    });
  }
})();

document.addEventListener('DOMContentLoaded', function() {
  // 1. Theme toggle listener
  var themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-theme') || 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('cyberpulse-theme', next);
    });
  }

  // 2. Reading progress bar on article pages
  var progressBar = document.getElementById('reading-progress');
  if (progressBar) {
    window.addEventListener('scroll', function() {
      var winScroll = document.documentElement.scrollTop || document.body.scrollTop;
      var height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
      var scrolled = height > 0 ? (winScroll / height) * 100 : 0;
      progressBar.style.width = scrolled + '%';
    });
  }

  // 3. Interactive Audio Player with Speed Toggle & Waveform
  var audio = document.getElementById('podcast-audio');
  var playBtn = document.getElementById('podcast-play-btn');
  var scrubber = document.getElementById('podcast-scrubber');
  var curTime = document.getElementById('podcast-time-cur');
  var durTime = document.getElementById('podcast-time-dur');
  var speedBtn = document.getElementById('podcast-speed-btn');
  var radioBox = document.getElementById('podcast-radio-box');

  if (audio && playBtn) {
    function formatTime(sec) {
      if (isNaN(sec) || sec < 0) return '00:00';
      var m = Math.floor(sec / 60);
      var s = Math.floor(sec % 60);
      return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    playBtn.addEventListener('click', function() {
      if (audio.paused) {
        audio.play().catch(function() {});
      } else {
        audio.pause();
      }
    });

    audio.addEventListener('play', function() {
      playBtn.classList.add('playing');
      if (radioBox) radioBox.classList.add('is-playing');
      playBtn.setAttribute('aria-label', 'Tạm dừng');
    });

    audio.addEventListener('pause', function() {
      playBtn.classList.remove('playing');
      if (radioBox) radioBox.classList.remove('is-playing');
      playBtn.setAttribute('aria-label', 'Phát');
    });

    audio.addEventListener('loadedmetadata', function() {
      if (durTime) durTime.textContent = formatTime(audio.duration);
      if (scrubber) scrubber.max = Math.floor(audio.duration || 100);
    });

    audio.addEventListener('timeupdate', function() {
      if (curTime) curTime.textContent = formatTime(audio.currentTime);
      if (scrubber && !scrubber.dataset.seeking) {
        scrubber.value = Math.floor(audio.currentTime);
      }
    });

    audio.addEventListener('ended', function() {
      playBtn.classList.remove('playing');
      if (radioBox) radioBox.classList.remove('is-playing');
      if (scrubber) scrubber.value = 0;
      if (curTime) curTime.textContent = '00:00';
    });

    if (scrubber) {
      scrubber.addEventListener('input', function() {
        scrubber.dataset.seeking = 'true';
        if (curTime) curTime.textContent = formatTime(scrubber.value);
      });
      scrubber.addEventListener('change', function() {
        audio.currentTime = scrubber.value;
        delete scrubber.dataset.seeking;
      });
    }

    if (speedBtn) {
      var speeds = [1.0, 1.25, 1.5];
      var speedIdx = 0;
      speedBtn.addEventListener('click', function() {
        speedIdx = (speedIdx + 1) % speeds.length;
        var newSpeed = speeds[speedIdx];
        audio.playbackRate = newSpeed;
        speedBtn.textContent = newSpeed.toFixed(2).replace(/\.00$/, '') + 'x';
      });
    }
  }

  // 4. Combined Filter and Search Function
  function applyCardFilters() {
    var activePill = document.querySelector('.filter-pill.active');
    var filter = activePill ? activePill.getAttribute('data-filter') : 'all';
    var searchInput = document.getElementById('search-input');
    var query = searchInput ? searchInput.value.toLowerCase().trim() : '';

    var cards = document.querySelectorAll('.threat-card, .story-card, .hero-card, .filterable-card');
    cards.forEach(function(card) {
      var sev = card.getAttribute('data-severity') || '';
      var matchFilter = false;
      if (filter === 'all') {
        matchFilter = true;
      } else if (filter === 'critical') {
        matchFilter = (sev === 'critical') || !!card.querySelector('.badge-critical');
      } else if (filter === 'warning') {
        matchFilter = (sev === 'warning' || sev === 'high') || !!card.querySelector('.badge-warning, .badge-high');
      } else if (filter === 'patch') {
        matchFilter = (sev === 'patch') || !!card.querySelector('.badge-patch');
      }

      var text = card.textContent.toLowerCase();
      var matchSearch = !query || text.indexOf(query) !== -1;

      if (matchFilter && matchSearch) {
        card.style.display = '';
      } else {
        card.style.display = 'none';
      }
    });
  }

  // 5. Search Filter
  var searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      applyCardFilters();
    });
  }

  // Quick keyboard focus on search with '/'
  window.addEventListener('keydown', function(e) {
    if (e.key === '/' && document.activeElement !== searchInput) {
      if (searchInput) {
        e.preventDefault();
        searchInput.focus();
      }
    }
  });

  // 6. Live Interactive Filter Pills
  var filterButtons = document.querySelectorAll('.filter-pill');
  if (filterButtons.length > 0) {
    filterButtons.forEach(function(btn) {
      btn.addEventListener('click', function() {
        filterButtons.forEach(function(b) { b.classList.remove('active'); });
        this.classList.add('active');
        applyCardFilters();
      });
    });
  }

  // 7. View Counters (Article & Edition)
  function getBaselineViews(str) {
    var hash = 0;
    for (var i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return 800 + (Math.abs(hash) % 1701);
  }

  var articleElem = document.querySelector('[data-article-id]') || document.querySelector('.single-article-main');
  var articleId = articleElem ? articleElem.getAttribute('data-article-id') : null;
  if (!articleId) {
    var match = window.location.pathname.match(/\/articles\/([^\/.]+)/);
    if (match) articleId = match[1];
  }
  if (articleId) {
    var key = 'cp_views_' + articleId;
    var count = parseInt(localStorage.getItem(key), 10);
    if (isNaN(count)) {
      count = getBaselineViews(articleId) + 1;
    } else {
      count += 1;
    }
    localStorage.setItem(key, count);
    var formatted = count.toLocaleString('vi-VN') || count.toLocaleString();
    var viewEls = document.querySelectorAll('#article-views-count, #article-views');
    viewEls.forEach(function(el) {
      el.textContent = formatted;
    });
  }

  var heroElem = document.querySelector('[data-run-id]');
  var runId = heroElem ? heroElem.getAttribute('data-run-id') : null;
  if (runId) {
    var runKey = 'cp_views_edition_' + runId;
    var edCount = parseInt(localStorage.getItem(runKey), 10);
    if (isNaN(edCount)) {
      edCount = (getBaselineViews(runId) * 4) + 1;
    } else {
      edCount += 1;
    }
    localStorage.setItem(runKey, edCount);
    var edFormatted = edCount.toLocaleString('vi-VN') || edCount.toLocaleString();
    var edViewEl = document.getElementById('edition-views-count');
    if (edViewEl) {
      edViewEl.textContent = edFormatted;
    }
  }

  // 8. Font Resizer
  var fontDec = document.getElementById('font-decrease');
  var fontInc = document.getElementById('font-increase');
  var bodyText = document.querySelector('.article-body-text');
  if (bodyText && (fontDec || fontInc)) {
    var savedSize = parseInt(localStorage.getItem('cp_font_size'), 10);
    var curSize = (!isNaN(savedSize) && savedSize >= 15 && savedSize <= 22) ? savedSize : 18;
    bodyText.style.fontSize = curSize + 'px';

    if (fontDec) {
      fontDec.addEventListener('click', function() {
        if (curSize > 15) {
          curSize -= 1;
          bodyText.style.fontSize = curSize + 'px';
          localStorage.setItem('cp_font_size', curSize);
        }
      });
    }

    if (fontInc) {
      fontInc.addEventListener('click', function() {
        if (curSize < 22) {
          curSize += 1;
          bodyText.style.fontSize = curSize + 'px';
          localStorage.setItem('cp_font_size', curSize);
        }
      });
    }
  }

  // 9. Podcast Quick Button Smooth Scroll
  var podcastQuickBtn = document.querySelector('.podcast-quick-btn');
  if (podcastQuickBtn) {
    podcastQuickBtn.addEventListener('click', function(e) {
      e.preventDefault();
      var target = document.getElementById('podcast-radio-box');
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        var playBtn = document.getElementById('podcast-play-btn');
        if (playBtn) playBtn.focus();
      }
    });
  }
});

// 5. Toast Notification & Copy Link
function copyCurrentUrl() {
  var url = window.location.href;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(showToast).catch(function() {
      fallbackCopy(url);
    });
  } else {
    fallbackCopy(url);
  }
}

function fallbackCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    showToast();
  } catch (err) {}
  document.body.removeChild(ta);
}

function showToast() {
  var toast = document.getElementById('toast-notify');
  if (!toast) return;
  toast.classList.add('show');
  setTimeout(function() {
    toast.classList.remove('show');
  }, 2500);
}
