(() => {
  const Tail = window.PropsteinTail;
  const form = document.getElementById('slipForm');
  const input = document.getElementById('slipInput');
  const out = document.getElementById('slipResult');

  function fail(reason) {
    out.innerHTML = `<div class="r-fail" data-slip-status="PARSE_FAILED">Couldn’t check this slip. ${Tail.esc(reason)}</div>`;
  }

  async function check(raw) {
    const parsed = Tail.parseSlip(raw);
    if (!parsed.ok) {
      fail(parsed.reason);
      return;
    }
    out.innerHTML = '<p class="t-note" data-slip-status="LOOKING_UP">Looking up committed snapshot…</p>';
    let match = null;
    try {
      match = await Tail.findSnapshotForSlip(parsed);
    } catch {
      match = { ok: false, reason: 'Committed snapshots could not be loaded.' };
    }
    if (!match.ok) {
      out.innerHTML = `<div class="r-fail" data-slip-status="NO_SNAPSHOT">${Tail.esc(match.reason)}</div>`;
      return;
    }
    // Route to the same tail-check view on the committed receipt.
    window.location.assign(`/r/${match.row.content_hash}?slip=${Tail.encodeSlip(parsed)}`);
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    void check(input.value);
  });
})();
