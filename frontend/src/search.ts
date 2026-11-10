/** Search only supplied display values; never turn hidden rows into deleted records. */
function searchableText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(searchableText).join(' ');
  if (typeof value === 'object') return Object.values(value).map(searchableText).join(' ');
  return String(value);
}

function normalize(value: string): string {
  return value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replaceAll('_', ' ').replace(/\s+/g, ' ').trim();
}

/** Every typed term must occur, case/accent insensitively, in the row's values. */
export function matchesSearch(query: string, ...values: unknown[]): boolean {
  const normalizedQuery = normalize(query);
  if (!normalizedQuery) return true;
  const text = normalize(values.map(searchableText).join(' '));
  return normalizedQuery.split(' ').every(term => text.includes(term));
}

/** Retain source indexes so editing/removing a filtered row targets the right item. */
export function indexedMatches<T>(items: T[], query: string, values: (item: T, index: number) => unknown = item => item): { item: T; index: number }[] {
  return items.map((item, index) => ({ item, index })).filter(({ item, index }) => matchesSearch(query, values(item, index)));
}
