# competitive-intel

Read competitor presence on 5+ platforms in parallel and summarize.

## When to use
User asks about competitor activity, e.g. 'What's Stripe shipping this week?'

## Tools
- `agent_reach_read`
- `fetch_url`

## Example
```
agent_reach_read platform=twitter target=stripe; agent_reach_read platform=github target=stripe; ...
```
