/**
 * review.test.js —— SM-2 简化算法
 *
 * 只测 src/review.js 的纯函数：写库那一半在 src/db.js，由 npm run selftest 覆盖。
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { INITIAL_ROW, nextReview } from '../src/review.js';

/** 把一次打分的结果喂回下一次，模拟 paper_review 表里的行 */
function gradeTwice(row, quality) {
  const next = nextReview(row, quality);
  return { ...next, row: { ease: next.ease, interval_days: next.intervalDays, reps: next.reps } };
}

function chain(quality, times, start = INITIAL_ROW) {
  let row = start;
  const history = [];
  for (let i = 0; i < times; i += 1) {
    const stepped = gradeTwice(row, quality);
    history.push(stepped);
    row = stepped.row;
  }
  return history;
}

test('第一次记住（quality>=3）间隔 1 天', () => {
  assert.deepEqual(nextReview(INITIAL_ROW, 4), { ease: 2.5, reps: 1, intervalDays: 1 });
});

test('第二次记住间隔 3 天', () => {
  const row = { ease: 2.5, interval_days: 1, reps: 1 };
  assert.deepEqual(nextReview(row, 4), { ease: 2.5, reps: 2, intervalDays: 3 });
});

test('第三次起间隔按 ease 递增：1 → 3 → 8 → 20 → 50 → 125', () => {
  const history = chain(4, 6);
  assert.deepEqual(history.map((step) => step.intervalDays), [1, 3, 8, 20, 50, 125]);
  assert.deepEqual(history.map((step) => step.reps), [1, 2, 3, 4, 5, 6]);
});

test('quality < 3 视为忘了：复习次数与间隔都归零', () => {
  const row = { ease: 2.5, interval_days: 125, reps: 6 };
  for (const quality of [0, 1, 2]) {
    assert.deepEqual(nextReview(row, quality), { ease: 2.5, reps: 0, intervalDays: 0 }, `quality=${quality}`);
  }
});

test('重置后再答对，从头开始排间隔', () => {
  const reset = nextReview({ ease: 2.5, interval_days: 125, reps: 6 }, 1);
  const again = nextReview(
    { ease: reset.ease, interval_days: reset.intervalDays, reps: reset.reps },
    4,
  );
  assert.deepEqual(again, { ease: 2.5, reps: 1, intervalDays: 1 });
});

test('重置不惩罚难度系数，ease 保持原值', () => {
  assert.equal(nextReview({ ease: 1.8, interval_days: 30, reps: 4 }, 2).ease, 1.8);
});

test('quality=5 提高 ease，quality=3 压低 ease', () => {
  const row = { ease: 2.5, interval_days: 3, reps: 2 };
  const closeTo = (value, expected) => Math.abs(value - expected) < 1e-9;
  assert.ok(closeTo(nextReview(row, 5).ease, 2.6), String(nextReview(row, 5).ease));
  assert.ok(closeTo(nextReview(row, 3).ease, 2.36), String(nextReview(row, 3).ease));
  assert.ok(nextReview(row, 4).ease < nextReview(row, 5).ease);
});

test('ease 有 1.3 的下限，连续低分也不会跌破', () => {
  const history = chain(3, 40);
  for (const step of history) {
    assert.ok(step.ease >= 1.3, `ease 跌破下限：${step.ease}`);
    assert.ok(step.intervalDays >= 1, `间隔不能小于 1 天：${step.intervalDays}`);
  }
  assert.equal(Number(history[history.length - 1].ease.toFixed(6)), 1.3);
});

test('难度系数很低时间隔仍然至少 1 天', () => {
  const row = { ease: 1.3, interval_days: 1, reps: 3 };
  assert.equal(nextReview(row, 3).intervalDays, 1);
});

test('quality 超出 0-5 会被夹回区间内', () => {
  assert.deepEqual(nextReview(INITIAL_ROW, 99), nextReview(INITIAL_ROW, 5));
  assert.deepEqual(nextReview({ ease: 2.5, interval_days: 30, reps: 3 }, -3), {
    ease: 2.5,
    reps: 0,
    intervalDays: 0,
  });
  assert.deepEqual(nextReview(INITIAL_ROW, '4'), nextReview(INITIAL_ROW, 4), '字符串分数同样可用');
});

test('表里没有这一行时用初始状态起步', () => {
  assert.deepEqual(nextReview(null, 4), { ease: 2.5, reps: 1, intervalDays: 1 });
  assert.deepEqual(nextReview({}, 4), { ease: 2.5, reps: 1, intervalDays: 1 });
});

test('打分不会改写传进来的行（纯函数）', () => {
  const row = { ease: 2.5, interval_days: 3, reps: 2 };
  nextReview(row, 5);
  assert.deepEqual(row, { ease: 2.5, interval_days: 3, reps: 2 });
});
