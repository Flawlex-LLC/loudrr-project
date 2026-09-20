/**
 * Formatting helpers for the admin panel — the single source of truth for
 * dates and numbers.
 *
 * WHY THIS EXISTS (timestamps):
 * The backend serialises datetimes as NAIVE UTC ISO strings with no zone
 * suffix — e.g. "2026-09-18T06:46:14.517368". `new Date(that)` applies the
 * ECMA-262 rule for date-time forms without an offset and reads it as LOCAL
 * time, so every timestamp in the panel was wrong by the viewer's UTC offset
 * (a US admin at UTC-5 saw "in 5 hours" for a row written a second ago).
 * `serverDate()` re-attaches the implied `Z`; everything else here is built
 * on top of it. Never call `new Date(iso)` on a backend string directly.
 *
 * Numbers are always formatted with the `en-US` locale explicitly so the
 * panel reads identically for every admin regardless of their browser locale
 * (a German admin would otherwise see "1.234,56" next to a "1,234.56" written
 * by the API).
 */

const EM_DASH = '—';

/** Matches a trailing zone designator: `Z`, `+05:30`, `-0800`. */
const HAS_ZONE = /(?:Z|z|[+-]\d{2}:?\d{2})$/;

/**
 * Parse a timestamp coming from the Loudrr backend into a real `Date`.
 *
 * Naive strings (no zone suffix) are treated as UTC, which is what the
 * backend actually stores. Strings that already carry a zone are passed
 * through untouched, so this is safe to use on every API field.
 *
 * @returns `null` for null/empty input or an unparseable string — callers
 *          render a dash rather than "Invalid Date".
 */
export function serverDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const raw = String(iso).trim();
  if (!raw) return null;

  // Postgres/psycopg sometimes renders a space instead of the `T` separator.
  const normalised = raw.includes(' ') && !raw.includes('T') ? raw.replace(' ', 'T') : raw;
  // Date-only values ("2026-09-18") are already parsed as UTC by spec — don't
  // append a second Z to them.
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(normalised);
  const withZone = dateOnly || HAS_ZONE.test(normalised) ? normalised : `${normalised}Z`;

  const d = new Date(withZone);
  return Number.isNaN(d.getTime()) ? null : d;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * Compact relative time: "just now", "12m ago", "5h ago", "3d ago".
 * Beyond 30 days it falls back to an absolute date so admins aren't doing
 * arithmetic on "94d ago". Future instants read "in 12m".
 *
 * @param now Injectable clock (ms since epoch) — pass it to keep a table of
 *            rows consistent within one render, and for deterministic tests.
 */
export function timeAgo(iso: string | null | undefined, now?: number): string {
  const d = serverDate(iso);
  if (!d) return EM_DASH;

  const nowMs = now ?? Date.now();
  const diff = nowMs - d.getTime();
  const future = diff < 0;
  const abs = Math.abs(diff);

  if (abs < 45_000) return 'just now';

  let body: string;
  if (abs < HOUR) body = `${Math.round(abs / MINUTE)}m`;
  else if (abs < DAY) body = `${Math.round(abs / HOUR)}h`;
  else if (abs < 30 * DAY) body = `${Math.round(abs / DAY)}d`;
  else return absolute(iso);

  return future ? `in ${body}` : `${body} ago`;
}

/**
 * Absolute timestamp in the VIEWER's local zone (the instant is correct now
 * that `serverDate` anchors the string to UTC): "Sep 18, 2026, 6:46 AM".
 *
 * @param opts.seconds include seconds — for audit-log style precision.
 */
export function absolute(
  iso: string | null | undefined,
  opts?: { seconds?: boolean },
): string {
  const d = serverDate(iso);
  if (!d) return EM_DASH;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    ...(opts?.seconds ? { second: '2-digit' } : {}),
  }).format(d);
}

/**
 * Thousands-separated number, always en-US.
 *
 * With no `decimals`, integers render bare ("1,234") and fractions keep up to
 * two places ("1,234.56") — so a count never grows a ".00" tail and a float is
 * never silently rounded to the nearest whole.
 */
export function num(n: number, opts?: { decimals?: number }): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return EM_DASH;
  const decimals = opts?.decimals;
  if (decimals !== undefined) {
    return new Intl.NumberFormat('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(n);
  }
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: Number.isInteger(n) ? 0 : 2,
  }).format(n);
}

/**
 * Karma / credit amounts — always two decimals, always en-US. Use this
 * anywhere a balance, escrow or payout is shown so the panel never mixes
 * "12" and "12.00" for the same column.
 */
export function karma(n: number): string {
  return num(n, { decimals: 2 });
}
