(() => {
  const Tail = window.PropsteinTail;
  const Ticket = window.PropsteinTicket;
  const form = document.getElementById('slipForm');
  const input = document.getElementById('slipInput');
  const out = document.getElementById('slipResult');
  const esc = Tail.esc;

  function readLine(read) {
    const parts = [];
    if (read.player) parts.push(`player “${read.player}”`);
    if (read.team) parts.push(`team ${read.team}`);
    if (read.market) parts.push(read.market);
    if (read.odds) parts.push(`odds ${read.odds}`);
    return parts.length ? `<p class="t-note" data-slip-read="1">Read: ${esc(parts.join(' · '))}</p>` : '';
  }

  function stop(status, reason, read = {}) {
    out.innerHTML = `<div class="r-fail" data-slip-status="${esc(status)}">${esc(reason)}</div>${readLine(read)}`;
  }

  async function check(raw) {
    const parsed = Tail.parseSlip(raw);
    if (!parsed.ok) {
      stop('NEEDS_DETAIL', parsed.reason, parsed.read);
      return;
    }
    out.innerHTML = '<p class="t-note" data-slip-status="LOOKING_UP">Looking up the current slate…</p>';
    let match;
    try {
      match = await Tail.resolveSlipPlayer(parsed);
    } catch {
      match = { ok: false, reason: 'The current slate could not be loaded.' };
    }
    if (!match.ok) {
      stop(match.ambiguous ? 'AMBIGUOUS' : 'NO_MATCH', match.reason, parsed.read);
      return;
    }
    const ticket = {
      v: 1,
      m: 'anytime_td',
      p: match.row.player_id,
      g: match.row.game_id,
      n: match.row.player_display_name || match.row.player_name || parsed.player,
      o: parsed.odds,
      at: new Date(Date.now()).toISOString(),
    };
    out.innerHTML = `<div id="previewWrap" data-slip-status="LOOKING_UP">
      <p class="t-scope">Unverified preview · user-entered odds · not a receipt</p>
      <div id="slipCompare"></div>
      <div class="c-ticket">
        <label for="takeInput">One-line take (optional, shown on the ticket as yours)</label>
        <input class="c-line" id="takeInput" maxlength="100" autocomplete="off">
        <button type="button" class="c-btn" id="makeTicket">Create ticket link</button>
        <div id="ticketOut"></div>
      </div>
    </div>`;
    await Ticket.renderTicketComparison(document.getElementById('slipCompare'), ticket, {
      title: 'Would you still tail?',
      scopeNote: '',
      postedLabel: 'Your slip',
      postedTag: 'USER-ENTERED ODDS',
      postedTagClass: 'is-user',
      postedAtText: 'Entered by you · not proof of a wager',
    }).catch(() => {
      document.getElementById('slipCompare').innerHTML = '<p class="t-note" data-source-status="source_failed"><span data-no-current-quote="1">No current quote</span>. Quote source could not be loaded.</p>';
    });
    document.getElementById('previewWrap').setAttribute('data-slip-status', 'PREVIEW');
    document.getElementById('makeTicket').addEventListener('click', () => {
      const take = document.getElementById('takeInput').value.replace(/\s+/g, ' ').trim().slice(0, 100);
      const encoded = Ticket.encodeTicket({ ...ticket, take: take || undefined });
      const target = document.getElementById('ticketOut');
      if (!encoded) {
        target.innerHTML = '<div class="r-fail">Could not create a ticket from this slip.</div>';
        return;
      }
      const url = `${window.location.origin}/t/#${encoded}`;
      target.innerHTML = `<p class="t-note">Ticket link (anyone can open it, no account):</p><a class="r-link c-ticket-link" data-ticket-link="1" href="/t/#${esc(encoded)}">${esc(url)}</a><p class="t-note">The link carries your ask only. It is not a verified receipt.</p>`;
    });
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    void check(input.value);
  });
})();
