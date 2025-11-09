export const DIFFICULTY_WEIGHTS = {
  "Very Easy": 1,
  Easy: 2,
  Medium: 3,
  Hard: 4,
  "Very Hard": 5,
} as const;

export type DifficultyLabel = keyof typeof DIFFICULTY_WEIGHTS;

export function getProblemWeight(label?: DifficultyLabel | string | null) {
  if (!label) return 1;
  return DIFFICULTY_WEIGHTS[label as DifficultyLabel] ?? 1;
}
