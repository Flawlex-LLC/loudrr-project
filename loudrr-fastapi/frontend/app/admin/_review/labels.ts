/**
 * Human labels and small formatters for the two review queues.
 *
 * The panel was rendering database values straight into the table — a
 * reviewer read "cis_eastern_europe" and "ai_tech" in a column headed
 * "Region / Niche". These are the CHECK-constrained enum values from
 * models/waitlist_entry.py; the backend serves the raw value (it's what the
 * filters take) and the label belongs here, in the UI.
 *
 * `_review` is underscore-prefixed, so Next.js treats it as a private folder
 * and never routes it — these are modules, not pages.
 */

import { num } from '@/lib/admin/format';

export const EM_DASH = '—';

/** models/waitlist_entry.py :: Region */
export const REGION_LABELS: Record<string, string> = {
  north_america: 'North America',
  europe: 'Europe',
  middle_east: 'Middle East',
  south_asia: 'South Asia',
  southeast_asia: 'Southeast Asia',
  east_asia: 'East Asia',
  africa: 'Africa',
  latin_america: 'Latin America',
  oceania: 'Oceania',
  cis_eastern_europe: 'CIS / Eastern Europe',
};

/** models/waitlist_entry.py :: Niche */
export const NICHE_LABELS: Record<string, string> = {
  memecoins: 'Memecoins',
  gamefi: 'GameFi',
  trading: 'Trading',
  nfts: 'NFTs',
  defi: 'DeFi',
  ai_tech: 'AI & Tech',
  daos: 'DAOs',
};

/**
 * Label a raw enum value. Unknown values (a new enum member shipped before
 * this map is updated) degrade to Title Case rather than disappearing.
 */
export function labelFor(map: Record<string, string>, value: string): string {
  if (!value) return '';
  return map[value] ?? value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export const regionLabel = (v: string) => labelFor(REGION_LABELS, v);
export const nicheLabel = (v: string) => labelFor(NICHE_LABELS, v);

/**
 * Compact follower/tweet counts — "12.4k", "1.3M". The second line of a row
 * is a glance, not a ledger; `num()` stays for anywhere the exact figure
 * matters (the detail drawer, tooltips).
 */
export function compact(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return EM_DASH;
  const abs = Math.abs(n);
  if (abs < 1000) return num(n);
  const [value, suffix] = abs < 1_000_000 ? [n / 1000, 'k'] : [n / 1_000_000, 'M'];
  // one decimal, but never "12.0k"
  return `${value.toFixed(1).replace(/\.0$/, '')}${suffix}`;
}

/**
 * How old the X account is, from the provider's `register_date`
 * ("2012-07-25"). The single best bot tell in the payload, and it was sitting
 * in the JSON unread.
 */
export function accountAge(registerDate: string | null | undefined, now = Date.now()): string {
  if (!registerDate) return '';
  const started = Date.parse(`${registerDate}T00:00:00Z`);
  if (Number.isNaN(started)) return '';
  const days = Math.floor((now - started) / 86_400_000);
  if (days < 0) return '';
  if (days < 31) return `${days}d old`;
  const months = Math.floor(days / 30.44);
  if (months < 24) return `${months}mo old`;
  return `${Math.floor(days / 365.25)}y old`;
}

/**
 * What to call this applicant. `telegram_username` is empty on a meaningful
 * share of rows and the table used to render them as a bare dash — with
 * nothing to read, click or search on.
 */
export function identityLabel(row: {
  telegram_username: string;
  telegram_display_name: string;
  telegram_id: number | null;
  x_username?: string;
}): string {
  if (row.telegram_username) return `@${row.telegram_username}`;
  if (row.telegram_display_name) return row.telegram_display_name;
  if (row.x_username) return `@${row.x_username}`;
  if (row.telegram_id !== null) return `#${row.telegram_id}`;
  return EM_DASH;
}

/** Strip a leading "@" so links and comparisons see the bare handle. */
export const bareHandle = (h: string | null | undefined) => (h || '').replace(/^@/, '');

/**
 * Split two handles into the shared prefix and the part that differs, for the
 * side-by-side diff that replaced the always-on "MISMATCH" badge. Every row
 * in this queue is a mismatch by definition — the badge said nothing, while
 * one seeded pair differs only by a trailing underscore.
 */
export function handleDiff(a: string, b: string): { shared: string; rest: string } {
  const left = bareHandle(a);
  const right = bareHandle(b);
  let i = 0;
  while (i < right.length && i < left.length && right[i].toLowerCase() === left[i].toLowerCase()) {
    i += 1;
  }
  return { shared: right.slice(0, i), rest: right.slice(i) };
}
