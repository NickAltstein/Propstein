(() => {
  const Tail = window.PropsteinTail;
  const Ticket = window.PropsteinTicket;
  const esc = Tail.esc;
  const root = document.getElementById('ticketBody');
  const titleEl = document.getElementById('ticketTitle');

  function notFound() {
    titleEl.textContent = 'Ticket not found';
    root.innerHTML = '<div class="r-fail" data-ticket-status="NOT_FOUND">This ticket link is missing, malformed, or for an unsupported market.</div>';
  }

  async function main() {
    const ticket = Ticket.decodeTicket(window.location.hash);
    if (!ticket) {
      notFound();
      return;
    }
    titleEl.textContent = `${ticket.n} · ${Ticket.marketTitle(ticket)}`;
    root.innerHTML = `<div data-ticket-status="OK">
      <div class="t-card" data-ticket-ask="1">
        <h2 class="t-title">Original ask</h2>
        <div class="t-row"><div class="t-row-head"><span class="t-label">Ask</span><span class="t-tag is-user">USER-ENTERED · NOT VERIFIED</span></div>
          <div class="t-row-body"><strong>${esc(ticket.n)} · ${esc(Ticket.selectionLabel(ticket))}</strong><br>Minimum odds <span class="t-odds">${esc(Tail.fmtOdds(ticket.o))}</span><br><span class="t-ts">Created ${esc(Tail.fmtTs(ticket.at))}</span></div></div>
        ${ticket.f ? `<div class="t-row" data-ticket-parent="1"><div class="t-row-head"><span class="t-label">Forked from an earlier ask</span><span class="t-tag is-user">USER-ENTERED</span></div><div class="t-row-body">Original minimum <span class="t-odds">${esc(Tail.fmtOdds(ticket.f.o))}</span><br><span class="t-ts">Created ${esc(Tail.fmtTs(ticket.f.at))}</span>${ticket.f.take ? `<br>Original take: ${esc(ticket.f.take)}` : ''}</div></div>` : ''}
        ${ticket.take ? `<div class="t-row"><div class="t-row-head"><span class="t-label">Sender's take</span><span class="t-tag is-user">USER-ENTERED</span></div><div class="t-row-body" data-ticket-take="1">${esc(ticket.take)}</div></div>` : ''}
        <p class="t-note">A ticket is someone's ask, not proof of a wager.</p>
      </div>
      <div id="ticketCompare"></div>
      <div class="c-ticket">
        <button type="button" class="c-btn" id="watchBtn" data-watch-btn="1"></button>
        <p class="t-note" id="watchNote" data-watch-note="1"></p>
      </div>
      <form class="c-ticket" id="forkForm" data-fork-form="1" novalidate>
        <h2 class="t-title">Make your own ticket</h2>
        <p class="t-note">Same selection, your own minimum odds. This ticket stays as it is.</p>
        <label for="forkOdds">Your minimum odds (signed, e.g. +250)</label>
        <input class="c-line" id="forkOdds" maxlength="8" autocomplete="off" inputmode="text">
        <label for="forkTake">One-line take (optional)</label>
        <input class="c-line" id="forkTake" maxlength="100" autocomplete="off">
        <button type="submit" class="c-btn" id="forkBtn">Create my ticket link</button>
        <div id="forkOut" aria-live="polite"></div>
      </form>
    </div>`;
    let summary = { books: {} };
    try {
      summary = await Ticket.renderTicketComparison(document.getElementById('ticketCompare'), ticket, {
        title: 'Can you still get it?',
        scopeNote: 'Live comparison · read-only',
        postedLabel: 'Posted ask',
        postedTag: 'USER-ENTERED',
        postedTagClass: 'is-user',
        postedAtText: `Created ${Tail.fmtTs(ticket.at)}`,
      });
    } catch {
      document.getElementById('ticketCompare').innerHTML = '<p class="t-note" data-source-status="source_failed"><span data-no-current-quote="1">No current quote</span>. Quote source could not be loaded.</p>';
    }
    const btn = document.getElementById('watchBtn');
    const note = document.getElementById('watchNote');
    const paint = (savedOk = true) => {
      const watching = Ticket.isWatching(ticket);
      btn.textContent = watching ? 'Stop watching' : 'Watch this price';
      btn.setAttribute('aria-pressed', watching ? 'true' : 'false');
      note.textContent = savedOk
        ? `${watching ? `Watching ${Tail.fmtOdds(ticket.o)} on this device only. No alerts yet. ` : ''}${Ticket.watchLine(ticket, summary.books)}`
        : 'This browser blocked local storage, so the watch was not saved.';
    };
    btn.addEventListener('click', () => {
      const r = Ticket.toggleWatch(ticket);
      paint(r.saved);
    });
    paint();

    document.getElementById('forkForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const out = document.getElementById('forkOut');
      const parsed = Tail.parseOddsToken(document.getElementById('forkOdds').value);
      const r = parsed.ok
        ? Ticket.forkTicket(ticket, parsed.odds, document.getElementById('forkTake').value, new Date(Date.now()).toISOString())
        : { ok: false, reason: 'Odds need an explicit sign, such as +250 or -110 (or “even”).' };
      if (!r.ok) {
        out.innerHTML = `<div class="r-fail" data-fork-status="REJECTED">${esc(r.reason)}</div>`;
        return;
      }
      const url = `${window.location.origin}/t/#${r.encoded}`;
      out.innerHTML = `<div data-fork-status="OK"><p class="t-note">Your ticket link (no account needed):</p><a class="r-link c-ticket-link" data-fork-link="1" href="/t/#${esc(r.encoded)}">${esc(url)}</a><p class="t-note">It carries your cutoff and the original ask as context. Not a verified receipt.</p></div>`;
    });
  }

  window.addEventListener('hashchange', () => { void main(); });
  void main();
})();
