# US Policy Calendar

用于 SPX / ES 交易的公开政策事件日历。不是新闻预警、交易信号或法律意见。

## 安装

创建公开 GitHub 仓库 `us-policy-calendar`，上传本项目根目录文件，再创建 `.github/workflows/update-calendar.yml`，内容来自压缩包内同名文件。Actions 中手动运行一次。工作流每小时 UTC :08、:23、:38、:53 尝试更新。GitHub Actions 和 Google Calendar 均可能延迟。

Google Calendar → 其他日历 → 通过网址添加：
https://raw.githubusercontent.com/rickeylau/us-policy-calendar/refs/heads/main/us-policy.ics

## 数据来源

- Roll Call / Factba.se 公开日程：https://rollcall.com/factbase/trump/calendar/ 。其帮助页 https://cqhelp.zendesk.com/hc/en-us/articles/25347226152987-President-s-Public-Schedule 链接到公开 Google 日历。程序尝试对应的 public/basic.ics 地址，尚未实际联网验证该下载端点；若不可用，保留旧日程缓存并报告错误。不会自行猜测讲话日期。只保留关键词匹配的公开活动，过滤闭门、池记者集合、私人活动等；只解析明确时区的独立 ICS 事件，不展开循环事件。部分事后新增事件可能保留。
- Federal Register API：https://www.federalregister.gov/developers/documentation/api/v1 。读取过去365天总统文件及 USTR 文件，按标题过滤。正式发布日期用全天事件，明确的 API effective_date 用全天事件；不会从正文任意提取日期作为生效日期，也不会自动判断文件是否被后续撤销。API 和机构筛选首次运行仍需联网验证。
- `manual_events.json`：人工审核的政策期限。修改或取消日期须编辑此文件（`active: false` 可取消）。2026-11-15 初始资料含3个未来政策日期；可能被后续公告改变。每个条目含来源链接及核对日期。政府停摆、债务上限等没有正式确认日期时不添加。若正式确认，可人工增加条目。

## 自动更新与故障处理

每个来源独立更新。抓取失败保留对应缓存，其他来源仍可生成并发布；最后 Actions 标红，`source_status.json` 写明状态。初始自动数据缓存为空，只有人工核对的三个事件。不能将首次抓取失败当成已实现实时更新。对成功抓取的日程，移除已取消或不再出现在来源的未来事件。历史保留45天，未来最多365天。词汇过滤可能漏掉重要事件或包含一般性讲话。人工事件如与自动事件日期和 URL 完全相同则优先保留人工版本；不同来源的同一政策可能仍出现多个事件。

## 本地测试

```bash
python -m unittest -v
python policy_calendar.py --offline --as-of 2026-11-15
```

仅需 Python 3.12；无 API key，无第三方 Python 依赖。
