/**
 * PropStein Live Sniper — Market Normalization
 * ===========================================
 * Canonicalizes raw observed markets: player names, teams, selections,
 * market labels, odds, and drive identifiers.
 *
 * Non-negotiable rules (spec §5):
 *  - "reception this drive" is NEVER confused with "reception next play"
 *    or full-game receptions. Each maps to a distinct MarketTypeCode, and
 *    ambiguous labels classify as `unknown` → UNVERIFIED → no value alerts.
 *  - A player name alone never identifies an event. Event resolution is the
 *    matcher's job; the normalizer only produces canonical keys.
 */

import type { NormalizedMarket, ObservedMarket, MarketTypeCode } from "./market-types.ts";
import { americanToDecimal, parseAmericanOddsText } from "./odds-math.ts";

// ---------------------------------------------------------------------------
// Team canonicalization — NFL 32 clubs (aliases cover common book displays)
// ---------------------------------------------------------------------------

export const NFL_TEAM_ALIASES: Readonly<Record<string, string>> = {
  ARI: "ARI", ARIZONA: "ARI", CARDINALS: "ARI",
  ATL: "ATL", "ATLANTA": "ATL", FALCONS: "ATL",
  BAL: "BAL", BALTIMORE: "BAL", RAVENS: "BAL",
  BUF: "BUF", BUFFALO: "BUF", BILLS: "BUF",
  CAR: "CAR", CAROLINA: "CAR", PANTHERS: "CAR",
  CHI: "CHI", CHICAGO: "CHI", BEARS: "CHI",
  CIN: "CIN", CINCINNATI: "CIN", BENGALS: "CIN",
  CLE: "CLE", CLEVELAND: "CLE", BROWNS: "CLE",
  DAL: "DAL", DALLAS: "DAL", COWBOYS: "DAL",
  DEN: "DEN", DENVER: "DEN", BRONCOS: "DEN",
  DET: "DET", DETROIT: "DET", LIONS: "DET",
  GB: "GB", "GREEN BAY": "GB", PACKERS: "GB",
  HOU: "HOU", HOUSTON: "HOU", TEXANS: "HOU",
  IND: "IND", INDIANAPOLIS: "IND", COLTS: "IND",
  JAX: "JAX", JACKSONVILLE: "JAX", JAGUARS: "JAX", "JAC": "JAX",
  KC: "KC", "KANSAS CITY": "KC", CHIEFS: "KC",
  LV: "LV", "LAS VEGAS": "LV", RAIDERS: "LV", "OAK": "LV",
  LAC: "LAC", "LOS ANGELES CHARGERS": "LAC", CHARGERS: "LAC", "SD": "LAC",
  LAR: "LAR", "LOS ANGELES RAMS": "LAR", RAMS: "LAR", "LA": "LAR", "STL": "LAR",
  MIA: "MIA", MIAMI: "MIA", DOLPHINS: "MIA",
  MIN: "MIN", MINNESOTA: "MIN", VIKINGS: "MIN",
  NE: "NE", "NEW ENGLAND": "NE", PATRIOTS: "NE",
  NO: "NO", "NEW ORLEANS": "NO", SAINTS: "NO",
  NYG: "NYG", "NEW YORK GIANTS": "NYG", GIANTS: "NYG",
  NYJ: "NYJ", "NEW YORK JETS": "NYJ", JETS: "NYJ",
  PHI: "PHI", PHILADELPHIA: "PHI", EAGLES: "PHI",
  PIT: "PIT", PITTSBURGH: "PIT", STEELERS: "PIT",
  SF: "SF", "SAN FRANCISCO": "SF", NINERS: "SF", "49ERS": "SF",
  SEA: "SEA", SEATTLE: "SEA", SEAHAWKS: "SEA",
  TB: "TB", "TAMPA BAY": "TB", BUCCANEERS: "TB", "TAMPA": "TB",
  TEN: "TEN", TENNESSEE: "TEN", TITANS: "TEN",
  WAS: "WAS", WASHINGTON: "WAS", COMMANDERS: "WAS", "WSH": "WAS",
};

export const NBA_TEAM_ALIASES: Readonly<Record<string, string>> = {
  ATL: "ATL", HAWKS: "ATL",
  BOS: "BOS", BOSTON: "BOS", CELTICS: "BOS",
  BKN: "BKN", BRK: "BKN", BROOKLYN: "BKN", NETS: "BKN",
  CHA: "CHA", CHO: "CHA", CHARLOTTE: "CHA", HORNETS: "CHA",
  CHI: "CHI", BULLS: "CHI",
  CLE: "CLE", CAVALIERS: "CLE", CAVS: "CLE",
  DAL: "DAL", MAVERICKS: "DAL", MAVS: "DAL",
  DEN: "DEN", NUGGETS: "DEN",
  DET: "DET", PISTONS: "DET",
  GSW: "GSW", GS: "GSW", "GOLDEN STATE": "GSW", WARRIORS: "GSW",
  HOU: "HOU", ROCKETS: "HOU",
  IND: "IND", PACERS: "IND",
  LAC: "LAC", CLIPPERS: "LAC", "LA CLIPPERS": "LAC",
  LAL: "LAL", LAKERS: "LAL", "LA LAKERS": "LAL",
  MEM: "MEM", MEMPHIS: "MEM", GRIZZLIES: "MEM",
  MIA: "MIA", HEAT: "MIA",
  MIL: "MIL", MILWAUKEE: "MIL", BUCKS: "MIL",
  MIN: "MIN", TIMBERWOLVES: "MIN", WOLVES: "MIN",
  NOP: "NOP", NO: "NOP", PELICANS: "NOP",
  NYK: "NYK", NY: "NYK", "NEW YORK KNICKS": "NYK", KNICKS: "NYK",
  OKC: "OKC", "OKLAHOMA CITY": "OKC", THUNDER: "OKC",
  ORL: "ORL", ORLANDO: "ORL", MAGIC: "ORL",
  PHI: "PHI", "76ERS": "PHI", SIXERS: "PHI",
  PHX: "PHX", PHO: "PHX", PHOENIX: "PHX", SUNS: "PHX",
  POR: "POR", PORTLAND: "POR", "TRAIL BLAZERS": "POR", BLAZERS: "POR",
  SAC: "SAC", SACRAMENTO: "SAC", KINGS: "SAC",
  SAS: "SAS", SA: "SAS", "SAN ANTONIO": "SAS", SPURS: "SAS",
  TOR: "TOR", TORONTO: "TOR", RAPTORS: "TOR",
  UTA: "UTA", UTAH: "UTA", JAZZ: "UTA",
  WAS: "WAS", WIZARDS: "WAS",
};

const NAME_SUFFIXES = ["JR", "SR", "II", "III", "IV", "V"];

/** Special Latin letters that Unicode NFKD does not decompose to ASCII. */
const UNICODE_FOLD_MAP: Readonly<Record<string, string>> = {
  "\u0111": "d", // đ (Đorđe, Đurišić)
  "\u0142": "l", // ł (Łukasz)
  "\u00f8": "o", // ø
  "\u00df": "ss", // ß
  "\u00e6": "ae", // æ
  "\u0153": "oe", // œ
  "\u0131": "i", // ı (Turkish dotless i)
};

/** Canonical, comparison-safe player name key with NBA/NFL Unicode folding. */
export function playerNameKey(rawName: string): string {
  let n = (rawName ?? "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\u00A0/g, " ")
    .toLowerCase()
    .normalize("NFKD");
  // Strip combining marks left by NFKD for accented characters (ć, č, š, ž, ņ, ģ, ū, ö, ü, etc.).
  n = n.replace(/[\u0300-\u036f]/g, "");
  // Fold non-decomposing Latin characters (đ, ł, ø, ß, æ, œ, ı).
  n = n.replace(/[\u0111\u0142\u00f8\u00df\u00e6\u0153\u0131]/g, (ch) => UNICODE_FOLD_MAP[ch] ?? ch);
  // Apostrophes and periods are REMOVED (Ja'Marr → jamarr) so books that
  // render the same name with or without punctuation still match.
  n = n.replace(/[.'`’]/g, "");
  // Hyphens/dashes become spaces (Amon-Ra → amon ra, Gilgeous-Alexander → gilgeous alexander).
  n = n.replace(/[-–—]/g, " ");
  // Collapse whitespace.
  n = n.replace(/\s+/g, " ").trim();
  if (!n) return "";
  const parts = n.split(" ");
  // Remove trailing generational suffixes ("chase jr" → "chase").
  const meaningful = parts.filter((p) => !NAME_SUFFIXES.includes(p.toUpperCase()));
  return meaningful.join(" ").trim();
}

/**
 * True when a display name is an initials form ("J. Allen", "J.Allen").
 * Initials forms are inherently ambiguous across rosters and are never
 * auto-resolved to a single player.
 */
export function isInitialsForm(rawName: string): boolean {
  const n = (rawName ?? "").trim();
  return /^[A-Za-z]\.?\s*[A-Za-z][A-Za-z'.\-]*$/.test(n);
}

/** Canonical team abbreviation or undefined when unrecognized. */
export function canonicalTeam(rawTeam: string | undefined, league?: string): string | undefined {
  if (!rawTeam) return undefined;
  const key = rawTeam.trim().toUpperCase().replace(/\./g, "");
  if (league?.toUpperCase() === "NBA") {
    return NBA_TEAM_ALIASES[key] ?? NFL_TEAM_ALIASES[key];
  }
  return NFL_TEAM_ALIASES[key] ?? NBA_TEAM_ALIASES[key];
}

// ---------------------------------------------------------------------------
// Market label classification — the anti-false-match core
// ---------------------------------------------------------------------------

/** Words that separate drive-scope from next-play-scope from game-scope. */
const DRIVE_MARKERS = ["this drive", "current drive", "during the drive", "on the drive", "rest of drive", "remainder of drive", "rest of this drive"];
const NEXT_PLAY_MARKERS = ["next play", "this play", "current play", "on the play"];
const GAME_MARKERS = ["this game", "full game", "game total", "total receptions", "total receiving", "anytime touchdown", "anytime td", "to score a td"];

export interface MarketClassification {
  code: MarketTypeCode;
  reasons: string[];
}

/**
 * Classify a market label into a canonical MarketTypeCode.
 * Fails closed: overlapping or unrecognized scope → "unknown".
 */
export function classifyMarketLabel(label: string): MarketClassification {
  const t = ` ${(label ?? "").toLowerCase().replace(/\s+/g, " ").trim()} `;
  const reasons: string[] = [];
  if (!t.trim()) return { code: "unknown", reasons: ["empty market label"] };

  const mentionsReception = /reception|catch|record a reception|receptions/.test(t);
  const mentionsYards = /receiving yards|rec yards/.test(t);
  const mentionsFirstTd = /\b(first|1st)\s+(touchdown|td)\b|\bfirst_td\b/.test(t);
  const mentionsTwoPlusTd = /\b(2\+|2 or more|two\+|two or more)\s+(touchdowns?|tds?)\b|\btwo_plus_td\b/.test(t);
  const mentionsAnytimeTd = /anytime (touchdown|td)|to score a (touchdown|td)|\banytime_td\b/.test(t);
  const mentionsPra = /\b(pts?\s*(?:\+|\s)\s*rebs?\s*(?:\+|\s)\s*asts?|points?\s*(?:\+|\s)\s*rebounds?\s*(?:\+|\s)\s*assists?|points?,?\s*rebounds?\s*(?:&|and|\+)\s*assists?|pra|nba_pra)\b/.test(t);
  const mentionsThrees = /\b(3[\s-]*(?:pointers?|pt|pts)\s*(?:made)?|three[\s-]*pointers?\s*(?:made)?|3pm|threes|nba_threes)\b/.test(t);
  const mentionsRebounds = /\b(rebounds?|rebs?|nba_rebounds)\b/.test(t);
  const mentionsAssists = /\b(assists?|asts?|nba_assists)\b/.test(t);
  const mentionsPoints = /\b(player\s+points?|points?|pts|nba_points)\b/.test(t) && !/fantasy|three|3[\s-]*pt/.test(t);

  const hasDrive = DRIVE_MARKERS.some((m) => t.includes(m));
  const hasNextPlay = NEXT_PLAY_MARKERS.some((m) => t.includes(m));
  const hasGame = GAME_MARKERS.some((m) => t.includes(m));

  // Overlapping scope markers → unclassifiable. Fail closed.
  const scopeHits = [hasDrive, hasNextPlay, hasGame].filter(Boolean).length;
  if (scopeHits > 1) {
    return {
      code: "unknown",
      reasons: [`overlapping scope markers in label "${label}" (drive=${hasDrive}, nextPlay=${hasNextPlay}, game=${hasGame})`],
    };
  }

  if (mentionsReception && hasDrive && !hasNextPlay) {
    return { code: "drive_reception_1plus", reasons: [] };
  }
  if (mentionsReception && hasNextPlay && !hasDrive) {
    return { code: "next_play_reception_1plus", reasons: [] };
  }
  if (mentionsReception && hasGame) {
    // Full-game reception totals: selection over/under resolves later.
    return { code: "game_reception_over", reasons: [] };
  }
  if (mentionsReception && !scopeHits) {
    // "Player to record a reception" with no scope word at all is ambiguous
    // between drive/next-play/game on live boards — fail closed.
    return { code: "unknown", reasons: [`reception market without explicit scope in "${label}"`] };
  }
  if (mentionsYards) {
    return { code: "game_receiving_yards_over", reasons: [] };
  }
  if (mentionsFirstTd) {
    return { code: "first_td", reasons: [] };
  }
  if (mentionsTwoPlusTd) {
    return { code: "two_plus_td", reasons: [] };
  }
  if (mentionsAnytimeTd) {
    return { code: "anytime_td", reasons: [] };
  }
  if (mentionsPra) {
    return { code: "nba_pra", reasons: [] };
  }
  if (mentionsThrees) {
    return { code: "nba_threes", reasons: [] };
  }
  if (mentionsRebounds && !mentionsAssists && !mentionsPoints) {
    return { code: "nba_rebounds", reasons: [] };
  }
  if (mentionsAssists && !mentionsRebounds && !mentionsPoints) {
    return { code: "nba_assists", reasons: [] };
  }
  if (mentionsPoints && !mentionsRebounds && !mentionsAssists) {
    return { code: "nba_points", reasons: [] };
  }
  return { code: "unknown", reasons: [`unrecognized market label "${label}"`] };
}

/** Normalize a selection label. */
export function normalizeSelection(raw: string): "YES" | "NO" | "OVER" | "UNDER" | "OTHER" {
  const s = (raw ?? "").trim().toUpperCase();
  if (s === "YES" || s === "Y") return "YES";
  if (s === "NO" || s === "N") return "NO";
  if (s === "OVER" || s === "O") return "OVER";
  if (s === "UNDER" || s === "U") return "UNDER";
  return "OTHER";
}

/** Canonical drive key: `${eventId}::${driveId}`. */
export function driveKey(eventId: string | undefined, driveId: string | undefined): string | undefined {
  if (!eventId || !driveId) return undefined;
  const e = eventId.trim();
  const d = driveId.trim();
  if (!e || !d) return undefined;
  return `${e}::${d}`;
}

// ---------------------------------------------------------------------------
// Full normalization
// ---------------------------------------------------------------------------

/**
 * Normalize one observed market. Never throws for content reasons —
 * ambiguity is reported, not hidden.
 */
export function normalizeObservedMarket(observed: ObservedMarket): NormalizedMarket {
  const ambiguityReasons: string[] = [];
  const notes: string[] = [];

  // --- Player name ---
  const nameKey = playerNameKey(observed.playerName);
  if (!nameKey) {
    ambiguityReasons.push("player name is empty after normalization");
  }
  if (isInitialsForm(observed.playerName)) {
    ambiguityReasons.push(`player name "${observed.playerName}" is an initials form — ambiguous without roster context`);
  }

  // --- Teams ---
  const homeKey = canonicalTeam(observed.homeTeam, observed.league);
  const awayKey = canonicalTeam(observed.awayTeam, observed.league);
  if ((observed.homeTeam && !homeKey) || (observed.awayTeam && !awayKey)) {
    ambiguityReasons.push("one or more team names unrecognized");
    notes.push(`teams as displayed: ${observed.awayTeam ?? "?"} @ ${observed.homeTeam ?? "?"}`);
  }

  // --- Market classification (the anti-false-match core) ---
  const classification = classifyMarketLabel(`${observed.marketType} ${observed.marketLabel}`);
  if (classification.code === "unknown") {
    ambiguityReasons.push(...classification.reasons);
  }

  // --- Selection ---
  const selectionKey = normalizeSelection(observed.selection);
  if (selectionKey === "OTHER") {
    ambiguityReasons.push(`unrecognized selection "${observed.selection}"`);
  }
  if (selectionKey === "NO" && classification.code !== "drive_reception_1plus" && classification.code !== "next_play_reception_1plus") {
    notes.push("NO selection on a non-binary market treated as unclassifiable");
  }

  // --- Odds ---
  let americanOdds = observed.americanOdds;
  let decimalOdds = observed.decimalOdds;
  if (americanOdds === undefined && decimalOdds !== undefined) {
    notes.push("only decimal odds observed");
  }
  if (americanOdds !== undefined) {
    try {
      decimalOdds = americanToDecimal(americanOdds);
    } catch {
      ambiguityReasons.push(`invalid American odds ${String(americanOdds)}`);
      americanOdds = undefined;
    }
  } else if (observed.marketLabel && decimalOdds === undefined) {
    // Adapters may leave raw odds text in the label; try to recover.
    const recovered = parseAmericanOddsText(observed.marketLabel);
    if (recovered !== undefined) {
      americanOdds = recovered;
      decimalOdds = americanToDecimal(recovered);
      notes.push("odds recovered from label text");
    }
  }
  if (decimalOdds === undefined && americanOdds === undefined) {
    ambiguityReasons.push("no usable odds on this observation");
  }

  // --- Drive context ---
  const dKey = driveKey(observed.eventId, observed.driveId);
  if ((classification.code === "drive_reception_1plus" || classification.code === "next_play_reception_1plus") && !dKey) {
    ambiguityReasons.push("drive-scoped market observed without a drive identifier — cannot bind to a possession");
  }

  // --- Event binding ---
  if (!observed.eventId && !(homeKey && awayKey)) {
    ambiguityReasons.push("no event identifier and incomplete team pair — cannot bind to a single game");
  }

  return {
    observed,
    marketTypeCode: classification.code,
    playerNameKey: nameKey,
    teamKey: awayKey ?? homeKey,
    selectionKey,
    americanOdds,
    decimalOdds,
    driveKey: dKey,
    ambiguityReasons,
    normalizationNotes: notes,
  };
}
