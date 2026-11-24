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
    titleEl.textContent = `${ticket.n} · Anytime TD`;
    root.innerHTML = `<div data-ticket-status="OK">
      <div class="t-card" data-ticket-ask="1">
        <h2 class="t-title">Original ask</h2>
        <div class="t-row"><div class="t-row-head"><span class="t-label">Ask</span><span class="t-tag is-user">USER-ENTERED · NOT VERIFIED</span></div>
          <div class="t-row-body"><strong>${esc(ticket.n)} · Anytime TD · Yes</strong><br>Minimum odds <span class="t-odds">${esc(Tail.fmtOdds(ticket.o))}</span><br><span class="t-ts">Created ${esc(Tail.fmtTs(ticket.at))}</span></div></div>
        ${ticket.take ? `<div class="t-row"><div class="t-row-head"><span class="t-label">Sender's take</span><span class="t-tag is-user">USER-ENTERED</span></div><div class="t-row-body" data-ticket-take="1">${esc(ticket.take)}</div></div>` : ''}
        <p class="t-note">A ticket is someone's ask, not proof of a wager.</p>
      </div>
      <div id="ticketCompare"></div>
      <div class="c-ticket">
        <button type="button" class="c-btn" id="watchBtn" data-watch-btn="1"></button>
        <p class="t-note" id="watchNote" data-watch-note="1"></p>
      </div>
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
  }

  window.addEventListener('hashchange', () => { void main(); });
  void main();
})();
