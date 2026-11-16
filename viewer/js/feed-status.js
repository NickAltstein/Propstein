/* Fail-closed board state published by scripts/feed_age.py. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.PropsteinFeedStatus = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function sportRow(statusDoc, sport) {
    return statusDoc && statusDoc.sports && statusDoc.sports[sport] ? statusDoc.sports[sport] : null;
  }

  function isFailClosed(statusDoc, sport) {
    var row = sportRow(statusDoc, sport);
    return !!(row && row.fail_closed);
  }

  function staleLabel(statusDoc, sport) {
    var row = sportRow(statusDoc, sport);
    if (!row || !row.fail_closed) return '';
    return String(sport || '').toUpperCase() + ' prices stale since ' + row.stale_since;
  }

  function stripEdges(quotes) {
    return (quotes || []).map(function (quote) {
      if (!quote || typeof quote !== 'object') return quote;
      var copy = Object.assign({}, quote);
      delete copy.evaluation;
      return copy;
    });
  }

  function apply(doc, statusDoc, sport) {
    var closed = isFailClosed(statusDoc, sport);
    if (doc && doc.body && doc.body.classList && doc.body.classList.toggle) {
      doc.body.classList.toggle('feed-fail-closed', closed);
    }
    var banner = doc && doc.querySelector ? doc.querySelector('#boardPageStatus') : null;
    if (banner) banner.textContent = closed ? staleLabel(statusDoc, sport) : '';
    return {
      failClosed: closed,
      showEdges: !closed,
      staleSince: closed ? sportRow(statusDoc, sport).stale_since : null,
      label: closed ? staleLabel(statusDoc, sport) : ''
    };
  }

  return { sportRow: sportRow, isFailClosed: isFailClosed, staleLabel: staleLabel, stripEdges: stripEdges, apply: apply };
});
