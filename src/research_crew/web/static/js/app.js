(function () {
  function collectApprovedTopics(form) {
    var checks = form.querySelectorAll('.proposal-check:checked');
    var approved = [];
    checks.forEach(function (input) {
      var raw = input.getAttribute('data-proposal');
      if (!raw) {
        return;
      }
      try {
        approved.push(JSON.parse(raw));
      } catch (_err) {
        // Ignore malformed proposal rows.
      }
    });
    return approved;
  }

  function syncRunForm(form) {
    if (!form) {
      return;
    }
    var hidden = form.querySelector('#approved_topics_json');
    var submit = form.querySelector('button[type="submit"], .btn-primary');
    var approved = collectApprovedTopics(form);

    if (hidden) {
      hidden.value = JSON.stringify(approved);
    }
    if (submit) {
      submit.disabled = approved.length === 0;
    }

    var all = form.querySelectorAll('.proposal-check');
    all.forEach(function (check) {
      var card = check.closest('.proposal-card');
      if (!card) {
        return;
      }
      var badge = card.querySelector('.user-picked');
      if (!badge) {
        return;
      }
      badge.classList.toggle('hidden', !check.checked);
    });
  }

  document.body.addEventListener('change', function (event) {
    var target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    if (!target.classList.contains('proposal-check')) {
      return;
    }
    var form = target.closest('#run-form');
    syncRunForm(form);
  });

  document.body.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (form.id !== 'run-form') {
      return;
    }
    syncRunForm(form);
  });

  document.body.addEventListener('htmx:afterSwap', function () {
    var form = document.querySelector('#run-form');
    syncRunForm(form);
  });

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.querySelector('#run-form');
    syncRunForm(form);
  });
})();
