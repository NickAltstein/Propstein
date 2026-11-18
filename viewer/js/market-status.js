/* One fail-closed presentation contract for the public market feed. */
((root) => {
  'use strict';

  const parseTime = (value) => {
    if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
    const time = Date.parse(value);
    return Number.isFinite(time) ? time : null;
  };
  const easternTime = (time) => new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric',
    minute: '2-digit', hour12: true
  }).format(new Date(time)) + ' ET';
  const ageSeconds = (time, now) => time === null ? null : Math.max(0, (now - time) / 1000);
  const quoteKey = (quote) => `${quote.player_id || quote.player_name || ''}::${quote.market || ''}`;

  function deriveMarketStatus(feedPayload, now, policy) {
    const at = now instanceof Date ? now.getTime() : Number(now);
    const p = policy || {};
    const windows = p.verificationWindows || {};
    const validWindows = windows.contract === 'verification.v1' && [
      windows.staleness_pregame_minutes, windows.staleness_near_kickoff_minutes,
      windows.staleness_in_game_minutes, windows.near_kickoff_window_minutes
    ].every((value) => Number.isFinite(Number(value)) && Number(value) > 0);
    const quotes = (Array.isArray(feedPayload?.quotes) ? feedPayload.quotes : [])
      .filter((quote) => !p.market || quote.market === p.market);
    const feed = p.feedUnavailable ? 'unavailable' : p.checking ? 'checking' : 'available';
    const capturedAt = parseTime(feedPayload?.updated_at || feedPayload?.generated_at);
    const states = p.gameStates || {};
    const qualifications = p.qualifications || {};
    const offers = quotes.map((quote) => {
      const game = states[quote.game_id] || {};
      const lifecycle = ['upcoming', 'started', 'final', 'postponed'].includes(game.lifecycle)
        ? game.lifecycle : 'unknown';
      const quotedAt = parseTime(quote.quoted_at);
      const gameStart = parseTime(game.startAt);
      let freshness = 'unknown';
      if (validWindows && quotedAt !== null && Number.isFinite(at) && quotedAt <= at && lifecycle !== 'unknown') {
        const untilStart = gameStart === null ? null : (gameStart - at) / 60000;
        const limit = lifecycle === 'started' || lifecycle === 'final'
          ? Number(windows.staleness_in_game_minutes)
          : untilStart !== null && untilStart <= Number(windows.near_kickoff_window_minutes)
            ? Number(windows.staleness_near_kickoff_minutes)
            : Number(windows.staleness_pregame_minutes);
        freshness = ageSeconds(quotedAt, at) <= limit * 60 ? 'current' : 'stale';
      }
      const receipt = quote.verification_receipt_id || quote.receipt_id;
      const verification = quote.verification_status === 'REJECTED' ? 'rejected'
        : quote.verified === true && quote.verification_status === 'VERIFIED' && receipt
          ? 'verified' : 'unverified';
      const verdict = qualifications[quoteKey(quote)];
      const qualification = verdict === true ? 'qualifies' : verdict === false
        ? 'does_not_qualify' : 'not_evaluable';
      const quoteStart = parseTime(quote.scheduled_start);
      const isPostStart = quote.pre_start === false ||
        (quotedAt !== null && quoteStart !== null && quotedAt >= quoteStart);
      const isPreStart = quotedAt !== null && quoteStart !== null
        && quotedAt < quoteStart && quote.pre_start !== false;
      const timingDiagnostics = [];
      if (quotedAt === null) timingDiagnostics.push('QUOTE_TIME_MISSING_OR_INVALID');
      if (quoteStart === null) timingDiagnostics.push('START_TIME_MISSING_OR_INVALID');
      if (isPostStart) timingDiagnostics.push('AFTER_START');
      const opportunity = feed === 'available' && lifecycle === 'upcoming'
        && freshness === 'current' && verification === 'verified'
        && qualification === 'qualifies' && isPreStart;
      return { quote, key: quoteKey(quote), lifecycle, freshness, verification,
        qualification, opportunity, timingDiagnostics, quotedAt: quote.quoted_at || null,
        ingestedAt: quote.observed_at || null,
        validatedAt: quote.verification_checked_at || null };
    });
    const count = new Set(offers.filter((offer) => offer.opportunity).map((offer) => offer.key)).size;
    const hasStarted = offers.some((offer) => ['started', 'final'].includes(offer.lifecycle));
    const staleSnapshot = capturedAt !== null && (offers.some((offer) => offer.freshness === 'stale')
      || (validWindows && ageSeconds(capturedAt, at) > Number(windows.staleness_pregame_minutes) * 60));
    const capturedAge = capturedAt === null || !Number.isFinite(at) ? null : ageSeconds(capturedAt, at);
    const relativeAge = capturedAge === null ? null : capturedAge >= 3600
      ? `${Math.floor(capturedAge / 3600)}h ago` : `${Math.floor(capturedAge / 60)}m ago`;
    let presentation;
    if (hasStarted) presentation = 'started';
    else if (feed === 'unavailable') presentation = 'unavailable';
    else if (feed === 'checking') presentation = 'checking';
    else if (staleSnapshot) presentation = 'snapshot';
    else if (count > 0) presentation = 'opportunities';
    else presentation = 'none';
    const labels = {
      started: 'Event started · pregame research archived',
      unavailable: 'Live prices unavailable · showing research',
      checking: 'Checking current prices',
      snapshot: `Snapshot only · ${capturedAt === null ? 'time unknown' : `${easternTime(capturedAt)} · ${relativeAge}`}`,
      opportunities: `${count} verified ${count === 1 ? 'opportunity' : 'opportunities'}`,
      none: 'No qualifying prices right now'
    };
    const allCount = Math.max(0, Number(p.allCount) || 0);
    const rankedCount = Number.isFinite(Number(p.topCount)) ? Math.max(0, Number(p.topCount)) : allCount;
    const topCount = Math.min(allCount, rankedCount, Math.max(0, Number(p.topLimit) || 25));
    return { feed, offers, verifiedCount: count, allCount, topCount, presentation,
      label: labels[presentation], capturedAt: capturedAt === null ? null : new Date(capturedAt).toISOString(),
      capturedLabel: capturedAt === null ? null : easternTime(capturedAt), relativeAge,
      lastRefreshAttemptAt: p.lastRefreshAttemptAt || null };
  }

  root.PropSteinMarketStatus = Object.freeze({ deriveMarketStatus });
  if (typeof module !== 'undefined' && module.exports) module.exports = { deriveMarketStatus };
})(typeof window !== 'undefined' ? window : globalThis);
