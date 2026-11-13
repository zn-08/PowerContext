/**
 * review.js —— SM-2 简化算法（纯函数）
 *
 * 排期与打分只有这一处数学，读写数据库留在 src/db.js，
 * 这样 npm test 不需要 seekdb 实例也能验证记忆逻辑。
 */

/** paper_review 里还没有这个 (user, doc) 时的起始状态 */
export const INITIAL_ROW = { ease: 2.5, interval_days: 0, reps: 0 };

/**
 * 打一次分，算出新的难度系数、复习次数与间隔。
 * @param {{ease?: number, interval_days?: number, reps?: number} | null} row 数据库里的当前行
 * @param {number} quality 0-5，超范围会被夹回区间内
 * @returns {{ease: number, reps: number, intervalDays: number}}
 */
export function nextReview(row, quality) {
  const score = Math.max(0, Math.min(5, Number(quality)));
  const current = row || INITIAL_ROW;
  let ease = Number(current.ease ?? INITIAL_ROW.ease);
  let reps = Number(current.reps ?? INITIAL_ROW.reps);
  let interval = Number(current.interval_days ?? INITIAL_ROW.interval_days);

  if (score >= 3) {
    reps += 1;
    if (reps === 1) interval = 1;
    else if (reps === 2) interval = 3;
    else interval = Math.max(1, Math.round(interval * ease));
    ease = Math.max(1.3, ease + 0.1 - (5 - score) * (0.08 + (5 - score) * 0.02));
  } else {
    reps = 0;
    interval = 0;
  }
  return { ease, reps, intervalDays: interval };
}
