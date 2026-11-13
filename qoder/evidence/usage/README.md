# 使用效果原始材料（发布后往这里放）

展示页 `qoder/site/index.html` 的「使用效果」一节只填汇总数字，
可核对的原始材料统一放本目录，页面里的表格按文件名对应：

| 文件 | 内容 | 什么时候放 |
| --- | --- | --- |
| `analytics-<日期>.png` | Qoder Sites 站点「访问统计」截图（访问次数 / 独立访客） | 发布后第 3 天、第 7 天各一张 |
| `paper_study.csv` | 真实检索日志：`SELECT created_at, user_id, query_text, title, hit_by, score FROM paper_study ORDER BY created_at DESC;` | 有真实用户用过之后 |
| `paper_review.csv` | 复习情况：`SELECT user_id, title, reps, interval_days, due_at FROM paper_review;` | 同上 |
| `survey.csv` | 试用问卷导出（能不能找到 / 比手动翻快多少 / 会不会持续用） | 收齐 5–10 份之后 |

> 只填能核对的数字：数值旁边注明**统计截止时间**和**样本量**。
> 还没有数据就先留「待填」，不要拿估计值占位——评审会照着 SQL 复算。
