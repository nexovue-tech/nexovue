/** English plurals for the nouns these screens count. */
export function plural(count: number, singular: string, many = `${singular}s`): string {
  return count === 1 ? singular : many;
}
